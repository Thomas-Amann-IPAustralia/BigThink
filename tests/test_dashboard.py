"""Tests for the interactive point-cloud dashboard (src/dashboard.py).

Offline like the rest of the suite. The fixture builds a small but faithful
run in a temporary DuckDB — the same shape test_notebook.py uses — so
build_dashboard() is exercised against real topic membership rather than
data invented to agree with it.
"""

from __future__ import annotations

import json
import sys

import numpy as np
import pytest

from src import dashboard, db
from src.config import load_config


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def test_pca_2d_returns_two_columns():
    rng = np.random.default_rng(0)
    vectors = rng.normal(size=(40, 16))
    coords = dashboard._pca_2d(vectors)
    assert coords.shape == (40, 2)


def test_pca_2d_is_deterministic():
    rng = np.random.default_rng(1)
    vectors = rng.normal(size=(30, 8))
    a = dashboard._pca_2d(vectors)
    b = dashboard._pca_2d(vectors)
    np.testing.assert_array_equal(a, b)


def test_project_2d_honours_explicit_pca_config():
    config = load_config()
    config["dashboard"]["projection"]["method"] = "pca"
    rng = np.random.default_rng(2)
    vectors = rng.normal(size=(20, 12))
    coords, method, params = dashboard.project_2d(vectors, config)
    assert method == "pca"
    assert coords.shape == (20, 2)
    assert params["method"] == "pca"


def test_project_2d_falls_back_to_pca_without_umap(monkeypatch):
    """umap-learn is an optional dependency (requirements.txt, but not
    guaranteed present) — a missing import must degrade to PCA, not crash
    the build the way a missing bge model would if unhandled."""
    monkeypatch.setitem(sys.modules, "umap", None)
    config = load_config()
    rng = np.random.default_rng(3)
    vectors = rng.normal(size=(20, 12))
    coords, method, _params = dashboard.project_2d(vectors, config)
    assert method == "pca"
    assert coords.shape == (20, 2)


def test_project_2d_uses_umap_when_available():
    umap = pytest.importorskip("umap")
    del umap
    config = load_config()
    rng = np.random.default_rng(4)
    vectors = rng.normal(size=(30, 12))
    coords, method, _params = dashboard.project_2d(vectors, config)
    assert method == "umap"
    assert coords.shape == (30, 2)


# ---------------------------------------------------------------------------
# Projection parameters
#
# The map is only worth reading as evidence about the clustering if it was
# projected in the space the clustering happened in. These guard that it is.
# ---------------------------------------------------------------------------


def test_projection_follows_the_bertopic_configuration():
    config = load_config()
    config["emergence"]["topics"]["method"] = "bertopic"
    config["emergence"]["topics"]["bertopic"]["n_neighbors"] = 31
    config["emergence"]["topics"]["bertopic"]["random_state"] = 7
    config["dashboard"]["projection"]["follow_clustering"] = True
    config["dashboard"]["projection"]["n_neighbors"] = 15
    config["dashboard"]["projection"]["random_state"] = 42

    params = dashboard.projection_params(config)
    assert params["n_neighbors"] == 31
    assert params["random_state"] == 7
    assert set(params["followed"]) == {"n_neighbors", "random_state"}


def test_projection_never_follows_min_dist():
    """The clustering packs at min_dist 0.0, which on a screen draws every
    topic as one indistinguishable dot. Following it would be visually fatal."""
    config = load_config()
    config["emergence"]["topics"]["method"] = "bertopic"
    config["emergence"]["topics"]["bertopic"]["min_dist"] = 0.0
    config["dashboard"]["projection"]["min_dist"] = 0.25
    assert dashboard.projection_params(config)["min_dist"] == 0.25


def test_projection_ignores_the_clustering_when_told_to():
    config = load_config()
    config["emergence"]["topics"]["method"] = "bertopic"
    config["emergence"]["topics"]["bertopic"]["n_neighbors"] = 31
    config["dashboard"]["projection"]["follow_clustering"] = False
    config["dashboard"]["projection"]["n_neighbors"] = 15
    params = dashboard.projection_params(config)
    assert params["n_neighbors"] == 15
    assert params["followed"] == []


def test_projection_does_not_follow_a_non_bertopic_clustering():
    """agglomerative has no UMAP stage, so there is nothing to follow and
    borrowing BERTopic's numbers would be cargo-culting them."""
    config = load_config()
    config["emergence"]["topics"]["method"] = "agglomerative"
    config["emergence"]["topics"]["bertopic"]["n_neighbors"] = 31
    config["dashboard"]["projection"]["n_neighbors"] = 15
    assert dashboard.projection_params(config)["n_neighbors"] == 15


# ---------------------------------------------------------------------------
# Projection fidelity
# ---------------------------------------------------------------------------


def test_knn_indices_puts_each_point_first_and_finds_its_own_cluster():
    rng = np.random.default_rng(11)
    a = rng.normal(size=(30, 8)) + 10.0
    b = rng.normal(size=(30, 8)) - 10.0
    vectors = np.vstack([a, b])
    nn = dashboard.knn_indices(vectors, k=5)
    assert nn.shape == (60, 6)
    assert list(nn[:, 0]) == list(range(60))
    # Two well-separated blobs: every neighbour should come from the same one.
    labels = np.array([0] * 30 + [1] * 30)
    assert (labels[nn[:, 1:]] == labels[:, None]).all()


def test_knn_indices_is_correct_under_the_euclidean_metric():
    """The map's own k-NN pass runs euclidean over 2D coordinates.

    Guards a specific arithmetic slip: expanding ||a-b||^2 and dropping the
    ||b||^2 term ranks a distant large vector as a near neighbour, because that
    term varies per candidate. Points of very different magnitude make the
    error visible; equal-magnitude fixtures hide it."""
    points = np.array([
        [0.0, 0.0], [0.1, 0.0], [0.0, 0.1],      # a tight cluster at the origin
        [40.0, 40.0], [40.1, 40.0], [40.0, 40.1],  # and one far away
    ])
    nn = dashboard.knn_indices(points, k=2, metric="euclidean")
    assert list(nn[:, 0]) == list(range(6)), "each point should be its own first neighbour"
    for i in range(6):
        near_side = set(range(3)) if i < 3 else set(range(3, 6))
        assert set(nn[i, 1:]) == near_side - {i}, f"point {i} matched across the gap"


def test_trustworthiness_is_perfect_for_a_lossless_projection():
    """A projection that loses nothing must read 1.0 on both measures. If this
    drifts, the numbers printed on the map are wrong.

    Note what "lossless" has to mean here. The high-dimensional side is scored
    by cosine — the metric the clustering itself used — and the 2D side by
    euclidean, because euclidean is what a reader's eye does with the picture.
    The two only rank neighbours identically for unit-norm vectors, where
    ||a-b||^2 = 2 - 2cos, so the fixture puts its points on the unit circle.
    Feeding unnormalised vectors here would fail at about 0.95, and that would
    be the metric pairing showing up, not a defect. The angles are jittered for
    the same kind of reason: on a perfectly regular circle every neighbour has
    an exact twin at the same distance, and the two matrices break that tie on
    different floating-point noise."""
    rng = np.random.default_rng(12)
    angles = np.sort(rng.uniform(0, 2 * np.pi, size=60))
    coords = np.column_stack([np.cos(angles), np.sin(angles)])
    trust, cont = dashboard.trustworthiness_continuity(coords, coords, k=5)
    assert trust == pytest.approx(1.0, abs=1e-9)
    assert cont == pytest.approx(1.0, abs=1e-9)


def test_trustworthiness_scores_the_high_side_by_cosine():
    """Pins the metric pairing above rather than leaving it to be rediscovered.

    Scaling a vector changes its euclidean distance to everything and its
    cosine distance to nothing, so a corpus and the same corpus with varied
    magnitudes must produce the same high-dimensional neighbourhoods — which
    is the property that makes the measure comparable across backends whose
    vectors are not normalised the same way."""
    rng = np.random.default_rng(21)
    vectors = rng.normal(size=(50, 6))
    coords = rng.normal(size=(50, 2))
    scaled = vectors * rng.uniform(0.2, 5.0, size=(50, 1))
    assert dashboard.trustworthiness_continuity(vectors, coords, k=6) == pytest.approx(
        dashboard.trustworthiness_continuity(scaled, coords, k=6), abs=1e-9
    )


def test_trustworthiness_falls_for_a_scrambled_projection():
    rng = np.random.default_rng(13)
    vectors = rng.normal(size=(80, 12))
    scrambled = rng.normal(size=(80, 2))
    trust, cont = dashboard.trustworthiness_continuity(vectors, scrambled, k=8)
    assert trust < 0.9
    assert cont < 0.9


def test_neighbour_purity_separates_a_clean_split_from_a_mixed_one():
    clean = np.array([[0, 1, 2], [1, 0, 2], [2, 0, 1], [3, 4, 5], [4, 3, 5], [5, 3, 4]])
    labels = np.array([0, 0, 0, 1, 1, 1])
    assert dashboard.neighbour_purity(clean, labels).mean() == pytest.approx(1.0)

    mixed = np.array([[0, 3, 4], [1, 3, 5], [2, 4, 5], [3, 0, 1], [4, 0, 2], [5, 1, 2]])
    assert dashboard.neighbour_purity(mixed, labels).mean() == pytest.approx(0.0)


def test_topic_hull_ignores_a_single_stray_member():
    """Untrimmed, one outlier stretches the hull across the whole map and the
    shape stops meaning anything."""
    square = np.array([[0.0, 0.0], [0.0, 1.0], [1.0, 1.0], [1.0, 0.0]] * 6)
    with_stray = np.vstack([square, [[80.0, 80.0]]])
    hull = dashboard.topic_hull(with_stray)
    assert max(abs(x) for x, _y in hull) < 5.0


def test_convex_hull_of_a_square_keeps_only_its_corners():
    points = np.array([[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0], [0.5, 0.5]])
    hull = dashboard._convex_hull(points)
    assert len(hull) == 4
    assert [0.5, 0.5] not in hull


# ---------------------------------------------------------------------------
# Downsampling
# ---------------------------------------------------------------------------


def _fake_documents(n: int) -> list[dict]:
    return [{"doc_id": f"d{i}"} for i in range(n)]


def test_select_points_keeps_everything_under_the_cap():
    docs = _fake_documents(10)
    keep = dashboard._select_points(docs, {"d0", "d1"}, max_points=100, run_id="r1")
    assert keep == list(range(10))


def test_select_points_keeps_every_assigned_document():
    docs = _fake_documents(50)
    assigned = {f"d{i}" for i in range(0, 50, 2)}  # every even doc
    keep = dashboard._select_points(docs, assigned, max_points=30, run_id="r1")
    kept_ids = {docs[i]["doc_id"] for i in keep}
    assert assigned <= kept_ids
    assert len(keep) == 30


def test_select_points_is_deterministic_for_the_same_run_id():
    docs = _fake_documents(200)
    assigned = {f"d{i}" for i in range(20)}
    a = dashboard._select_points(docs, assigned, max_points=80, run_id="2026-08-31")
    b = dashboard._select_points(docs, assigned, max_points=80, run_id="2026-08-31")
    assert a == b


def test_select_points_differs_for_a_different_run_id():
    docs = _fake_documents(300)
    assigned = {f"d{i}" for i in range(10)}
    a = dashboard._select_points(docs, assigned, max_points=100, run_id="run-a")
    b = dashboard._select_points(docs, assigned, max_points=100, run_id="run-b")
    assert a != b


# ---------------------------------------------------------------------------
# End-to-end build against a small faithful corpus
# ---------------------------------------------------------------------------

_TOPICS = [
    # label, doc count, horizon, signal_class, rank
    ("geographical indication / protection / trade", 12, "H2", "strong", 1),
    ("automated decision-making / administrative law", 9, "H1", "weak", 2),
    ("prior art / patent / search / retrieval", 6, "H2", "latent", 3),
]


def _fixture_config(tmp_path):
    config = load_config()
    config["storage"]["duckdb_path"] = str(tmp_path / "fixture.duckdb")
    config["dashboard"]["max_points"] = 1000
    config["synthesis"]["shortlist_size"] = 2  # only the first two topics are "shortlisted"
    # Pinned, not inherited. The shipped config runs `bge` + `bertopic`, which
    # would download a ~400 MB model and need torch — and the suite is offline
    # by design, so CI never depends on a third-party service being up. Pinning
    # also keeps this test testing what it says it tests: a default that
    # changes underneath it would silently change what is being exercised.
    config["embeddings"]["backend"] = "hashing"
    config["emergence"]["topics"]["method"] = "agglomerative"
    return config


def _build_run(config, run_id: str) -> None:
    conn = db.init_db(config["storage"]["duckdb_path"])
    try:
        documents = []
        doc_n = 0
        for t, (label, count, *_rest) in enumerate(_TOPICS):
            for i in range(count):
                year = 2020 + (i % 5)
                documents.append({
                    "doc_id": f"doc-{doc_n}",
                    "source": ("crossref", "arxiv", "gdelt")[doc_n % 3],
                    "native_id": f"10.0000/{doc_n}",
                    "title": f"{label.split(' / ')[0].title()} study {doc_n}",
                    "abstract": f"An abstract about {label}.",
                    "published_date": f"{year}-06-01",
                    "year": year,
                    "time_slice": str(year),
                    "url": f"https://example.invalid/{doc_n}",
                    "venue": "Journal of Test Fixtures",
                    "authors": ["A. Author"],
                    "institutions": ["Test University"],
                    "concepts": [label],
                    "citation_count": doc_n % 10,
                    "steepv": ("Technological", "Legal", "Social")[t % 3],
                    "scan_frame_key": f"frame_{t}",
                    "tone": None,
                    "raw_path": None,
                    "collected_at": db.now(),
                    "run_id": run_id,
                })
                doc_n += 1
        # A handful of documents that never join any topic — GDELT-shaped
        # attention signal, exercised as the "unassigned" case.
        for i in range(4):
            documents.append({
                "doc_id": f"doc-unassigned-{i}",
                "source": "gdelt",
                "native_id": f"url-{i}",
                "title": f"News item {i}",
                "abstract": "Unrelated coverage.",
                "published_date": "2023-01-01",
                "year": 2023,
                "time_slice": "2023",
                "url": f"https://example.invalid/news/{i}",
                "venue": None,
                "authors": [],
                "institutions": [],
                "concepts": [],
                "citation_count": 0,
                "steepv": "Social",
                "scan_frame_key": "frame_0",
                "tone": -1.2,
                "raw_path": None,
                "collected_at": db.now(),
                "run_id": run_id,
            })
        db.upsert_documents(conn, documents)

        topics = []
        doc_cursor = 0
        for t, (label, count, horizon, signal_class, _rank) in enumerate(_TOPICS):
            member_ids = [f"doc-{doc_cursor + i}" for i in range(count)]
            doc_cursor += count
            topics.append({
                "topic_id": f"T{t:04d}",
                "label": label,
                "terms": [[w, 1.0 - 0.1 * i] for i, w in enumerate(label.split(" / "))],
                "document_count": count,
                "first_slice": "2020",
                "last_slice": "2024",
                "novelty": 0.5, "growth": 0.3, "coherence": 0.4,
                "impact": 0.3, "uncertainty": 0.5,
                "emergence_score": 0.9 - 0.2 * t,
                "burst_weight": 1.0,
                "burst_slices": ["2024"],
                "cagr": 0.05,
                "maturity": 0.4,
                "horizon": horizon,
                "signal_class": signal_class,
                "avg_proportion": 0.02,
                "documents": [{"doc_id": d, "similarity": 0.9 - 0.01 * i} for i, d in enumerate(member_ids)],
                "timeseries": [{"time_slice": "2024", "doc_count": count, "proportion": 0.1, "in_burst": True}],
            })
        db.replace_topics(conn, run_id, topics)

        stored = db.fetch_topics(conn, run_id)
        rows = []
        for i, topic in enumerate(stored):
            rows.append({
                "topic_id": topic["topic_id"],
                "strategic_fit": 0.1 + 0.05 * i,
                "best_objective": f"Objective {i}",
                "best_objective_sim": 0.4,
                "critical_tech": None,
                "asset_leverage": 0.05 + 0.01 * i,
                "best_asset": f"Asset {i}",
                "opportunity_index": 0.5 + 0.05 * i,
                "index_components": {},
                "index_suppressed": False,
                "composite_rank_score": 1.0 - 0.1 * i,
                "rank": i + 1,
                "fit_quadrant": ("act", "watch", "on-strategy, no right-to-play")[i % 3],
            })
        db.replace_topic_scores(conn, run_id, rows)
    finally:
        conn.close()


@pytest.fixture()
def run_fixture(tmp_path):
    config = _fixture_config(tmp_path)
    run_id = "test-run"
    _build_run(config, run_id)
    return config, run_id


def test_build_dashboard_covers_every_document(run_fixture):
    config, run_id = run_fixture
    data = dashboard.build_dashboard(config, run_id)
    total = sum(count for _l, count, *_r in _TOPICS) + 4  # + unassigned
    assert data["documents_total"] == total
    assert data["documents_plotted"] == total
    points = data["points"]
    assert len(points["x"]) == len(points["y"]) == total
    assert all(len(points[k]) == total for k in ("topic", "source", "year", "title", "url"))


def test_build_dashboard_assigns_topics_correctly(run_fixture):
    config, run_id = run_fixture
    data = dashboard.build_dashboard(config, run_id)
    topic_ids = [t["id"] for t in data["topics"]]
    points = data["points"]
    assigned_count = sum(1 for ti in points["topic"] if ti != -1)
    unassigned_count = sum(1 for ti in points["topic"] if ti == -1)
    assert assigned_count == sum(count for _l, count, *_r in _TOPICS)
    assert unassigned_count == 4
    for ti in points["topic"]:
        assert -1 <= ti < len(topic_ids)


def test_build_dashboard_evidence_url_only_for_shortlisted_topics(run_fixture):
    config, run_id = run_fixture  # shortlist_size = 2, three topics
    data = dashboard.build_dashboard(config, run_id)
    by_rank = {t["rank"]: t for t in data["topics"]}
    assert by_rank[1]["evidence_url"] is not None
    assert by_rank[2]["evidence_url"] is not None
    assert by_rank[3]["evidence_url"] is None
    assert "evidence/01_" in by_rank[1]["evidence_url"]


def test_build_dashboard_carries_fit_quadrant_and_best_asset(run_fixture):
    """Regression guard for the topic_scores round-trip fix: both fields
    used to be computed and silently dropped before reaching the database
    (PROJECT_STATE.md issue 9)."""
    config, run_id = run_fixture
    data = dashboard.build_dashboard(config, run_id)
    for t in data["topics"]:
        assert t["fit_quadrant"] in ("act", "watch", "on-strategy, no right-to-play")
        assert t["best_asset"] and t["best_asset"].startswith("Asset")


def test_build_dashboard_downsamples_under_a_small_cap(run_fixture):
    config, run_id = run_fixture
    config["dashboard"]["max_points"] = 10
    data = dashboard.build_dashboard(config, run_id)
    assert data["documents_plotted"] == 10
    assert data["documents_total"] > 10
    # Every plotted point should still carry a valid, in-range topic index.
    for ti in data["points"]["topic"]:
        assert -1 <= ti < len(data["topics"])


def test_build_dashboard_carries_every_score_and_its_inputs(run_fixture):
    """The page explains each score by showing what went into it. A field
    dropped from the payload turns a breakdown into an unexplained number."""
    config, run_id = run_fixture
    data = dashboard.build_dashboard(config, run_id)
    for t in data["topics"]:
        for attribute in ("novelty", "growth", "coherence", "impact", "uncertainty"):
            assert t[attribute] is not None, f"{t['id']} is missing {attribute}"
        assert t["timeseries"], f"{t['id']} has no time series to draw"
        assert t["docs"], f"{t['id']} carries no member documents"
        assert t["terms"]
        for member in t["docs"]:
            assert 0 <= member["i"] < data["documents_plotted"]


def test_build_dashboard_reports_the_configured_weights(run_fixture):
    """The Method view states the weights it explains. Reading them from the
    live config is what stops the page describing a pipeline that has moved."""
    config, run_id = run_fixture
    data = dashboard.build_dashboard(config, run_id)
    method = data["method"]
    assert method["rotolo_weights"] == config["emergence"]["rotolo_weights"]
    assert method["rank_weights"] == config["synthesis"]["rank_weights"]
    assert method["index_components"] == config["opportunity_index"]["components"]
    assert method["three_horizons"] == config["emergence"]["three_horizons"]


def test_build_dashboard_says_when_no_critical_tech_cutoff_was_swept(run_fixture):
    """A blank cut-off means "match nothing", not "value missing". The page has
    to be able to tell a reader which of those an empty column means."""
    config, run_id = run_fixture
    config["scoring"]["strategic_fit"]["critical_tech_match"]["thresholds"]["hashing"] = None
    data = dashboard.build_dashboard(config, run_id)
    assert data["method"]["critical_tech_matching"] is False
    assert data["method"]["critical_tech_threshold"] is None

    config["scoring"]["strategic_fit"]["critical_tech_match"]["thresholds"]["hashing"] = 0.25
    data = dashboard.build_dashboard(config, run_id)
    assert data["method"]["critical_tech_matching"] is True
    assert data["method"]["critical_tech_threshold"] == 0.25


def test_build_dashboard_measures_projection_fidelity(run_fixture):
    config, run_id = run_fixture
    data = dashboard.build_dashboard(config, run_id)
    fidelity = data["fidelity"]
    assert fidelity["computed"] is True
    assert 0.0 <= fidelity["trustworthiness"] <= 1.0
    assert 0.0 <= fidelity["continuity"] <= 1.0

    # One neighbour row per plotted point, and no index out of range.
    neighbours = data["neighbours"]
    assert len(neighbours["idx"]) == data["documents_plotted"] * neighbours["k"]
    assert max(neighbours["idx"]) < data["documents_plotted"]

    for topic in data["topics"]:
        assert topic["map_purity"] is not None
        assert topic["space_purity"] is not None


def test_build_dashboard_skips_fidelity_over_the_budget(run_fixture):
    """The k-NN pass is exact and quadratic. Past the budget it must decline
    and say why, rather than putting a free Actions runner into a long
    matrix multiply."""
    config, run_id = run_fixture
    config["dashboard"]["fidelity"]["max_points"] = 5
    data = dashboard.build_dashboard(config, run_id)
    assert data["fidelity"]["computed"] is False
    assert data["fidelity"]["reason"]
    assert data["neighbours"]["idx"] == []
    # The rest of the payload still has to be complete and drawable.
    assert data["topics"] and data["points"]["x"]


def test_build_dashboard_hulls_stay_inside_the_plotted_extent(run_fixture):
    config, run_id = run_fixture
    data = dashboard.build_dashboard(config, run_id)
    xs, ys = data["points"]["x"], data["points"]["y"]
    for topic in data["topics"]:
        for x, y in topic["hull"]:
            assert min(xs) <= x <= max(xs)
            assert min(ys) <= y <= max(ys)


def test_build_dashboard_summarises_collection_by_source(run_fixture):
    """A source that half failed is one story, and the badge must show the
    worst outcome rather than the most common one."""
    config, run_id = run_fixture
    conn = db.init_db(config["storage"]["duckdb_path"])
    try:
        db.log_collection(conn, run_id, "crossref", "f1", "success", 100, "")
        db.log_collection(conn, run_id, "crossref", "f2", "partial", 3, "rate limited")
        db.log_collection(conn, run_id, "patentsview", "f1", "skipped", 0, "no key")
    finally:
        conn.close()

    data = dashboard.build_dashboard(config, run_id)
    by_source = {c["source"]: c for c in data["collection"]}
    assert by_source["crossref"]["worst"] == "partial"
    assert by_source["crossref"]["records"] == 103
    assert by_source["crossref"]["queries"] == 2
    assert by_source["patentsview"]["worst"] == "skipped"


# ---------------------------------------------------------------------------
# HTML rendering / script-injection safety
# ---------------------------------------------------------------------------


def _minimal_data(title: str) -> dict:
    return {
        "run_id": "r", "generated_at": "now", "repo_url": "https://github.com/x/y",
        "backend": "hashing", "projection_method": "pca", "shortlist_size": 1,
        "documents_total": 1, "documents_plotted": 1, "topics_total": 0,
        "year_min": 2020, "year_max": 2020, "sources": ["gdelt"], "steepv": ["Social"],
        "topics": [],
        "points": {
            "x": [0.0], "y": [0.0], "topic": [-1], "similarity": [None],
            "source": [0], "steepv": [0], "year": [2020], "citation": [0],
            "title": [title], "url": [""], "venue": [""],
        },
    }


def test_render_html_escapes_script_closing_sequence():
    malicious = "Normal title</script><script>window.pwned=true</script>"
    html = dashboard.render_html(_minimal_data(malicious))
    assert "</script><script>window.pwned" not in html
    assert "<\\/script><script>window.pwned" in html
    # The document survives as valid JSON once unescaped back.
    blob = html.split("window.__DASHBOARD_DATA__ = ", 1)[1].split(";</script>", 1)[0]
    parsed = json.loads(blob.replace("<\\/", "</"))
    assert parsed["points"]["title"][0] == malicious


def test_render_html_contains_title_and_data():
    html = dashboard.render_html(_minimal_data("A normal title"))
    assert "<title>" in html
    assert "__DASHBOARD_DATA__" in html
    assert "A normal title" in html


# --- self-containment -----------------------------------------------------
#
# The page is served from GitHub Pages and read behind corporate proxies, so
# it inlines everything. A stylesheet or a script that silently fails to load
# from a CDN takes the whole dashboard with it, and the failure is invisible
# to whoever published it.


def test_every_asset_is_present_and_inlined():
    html = dashboard.render_html(_minimal_data("t"))
    for name in dashboard._JS_ASSETS + ("dashboard.css", "shell.html"):
        path = dashboard.ASSET_DIR / name
        assert path.is_file(), f"missing asset {name}"
        # A distinctive line from each file, to catch an asset that exists but
        # was dropped from the render.
        marker = next(
            line.strip() for line in path.read_text(encoding="utf-8").splitlines()
            if len(line.strip()) > 30 and "*" not in line and "<!--" not in line
        )
        assert marker in html, f"{name} was not inlined into the page"


def test_the_published_page_fetches_nothing_from_the_network():
    """Guards the no-CDN decision. Anything with a scheme in a src/href that is
    not a link a reader clicks would be a runtime dependency on a third party."""
    import re

    html = dashboard.render_html(_minimal_data("t"))
    for attr in ("src", "href"):
        for match in re.finditer(rf'{attr}\s*=\s*"([^"]+)"', html):
            url = match.group(1)
            if url.startswith(("http://", "https://", "//")):
                # Anchors to the repository and to source documents are fine —
                # those are navigation, not resources the page needs to render.
                context = html[max(0, match.start() - 120):match.start()]
                assert "<a " in context.rsplit(">", 1)[-1] or "<a" in context[-40:], (
                    f"page loads {url} from the network"
                )


def test_the_stylesheet_defines_the_dark_palette_for_an_explicit_choice():
    """The in-page toggle stamps data-theme, and a blocking head snippet stamps
    it before first paint. If the stylesheet ever went back to keying dark mode
    off prefers-color-scheme alone, the toggle would stop working one way."""
    css = (dashboard.ASSET_DIR / "dashboard.css").read_text(encoding="utf-8")
    assert ':root[data-theme="dark"]' in css
    html = dashboard.render_html(_minimal_data("t"))
    assert "data-theme" in html.split("</head>")[0], "theme is not set before first paint"


# --- published page structure ---------------------------------------------
#
# Every page this project publishes began with a bare <title> until 2026-08-31:
# no doctype (quirks mode), no charset (mojibake for the en-dashes both pages
# are full of, whenever the file is opened rather than served), and no viewport
# meta — which meant the `@media (max-width: 720px)` rules both stylesheets
# carry could never fire on a phone. PROJECT_STATE.md issue 28.


def _published_pages():
    """Both generated pages, rendered from the same fixture."""
    from src.dashboard import render_html
    from src.report import build_html

    data = {
        "run_id": "test-run", "generated_at": "2026-08-31T00:00", "repo_url": "https://x/y",
        "backend": "hashing", "projection_method": "pca", "shortlist_size": 1,
        "documents_total": 1, "documents_plotted": 1, "topics_total": 1,
        "year_min": 2024, "year_max": 2026, "sources": ["crossref"], "steepv": ["Technological"],
        "topics": [], "points": {k: [] for k in (
            "x", "y", "topic", "similarity", "source", "steepv",
            "year", "citation", "title", "url", "venue")},
    }
    rows = [{
        "rank": 1, "topic_id": "T0000", "label": "a topic — with an en-dash",
        "horizon": "H2", "signal_class": "weak", "emergence_score": 0.5,
        "strategic_fit": 0.5, "asset_leverage": 0.5, "opportunity_index": 0.5,
        "index_suppressed": False, "best_objective": "4.2", "fit_quadrant": "act",
    }]
    stats = {"documents": 1, "topics": 1, "slices": 1, "span": "2024–2026",
             "backend": "hashing"}
    return {
        "dashboard.html": render_html(data),
        "index.html": build_html(rows, rows, "test-run", stats, "https://x/y"),
    }


@pytest.mark.parametrize("required", [
    "<!doctype html>",
    '<meta charset="utf-8">',
    '<meta name="viewport" content="width=device-width, initial-scale=1">',
])
def test_every_published_page_declares_its_document_head(required):
    for name, html in _published_pages().items():
        assert required in html, f"{name} is missing {required}"


def test_every_published_page_is_a_closed_document():
    for name, html in _published_pages().items():
        assert html.rstrip().endswith("</html>"), f"{name} is not closed"
        for tag in ("<html", "<head>", "</head>", "<body>", "</body>"):
            assert tag in html, f"{name} is missing {tag}"


def test_the_shortlist_links_each_topic_to_its_evidence_card():
    """Reading the evidence cards is the one check this project says finds
    clustering artefacts. The page should not make a reader go and find them."""
    html = _published_pages()["index.html"]
    assert "data/outputs/test-run/evidence/01_T0000.md" in html
