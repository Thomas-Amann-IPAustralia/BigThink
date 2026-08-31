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
    coords, method = dashboard.project_2d(vectors, config)
    assert method == "pca"
    assert coords.shape == (20, 2)


def test_project_2d_falls_back_to_pca_without_umap(monkeypatch):
    """umap-learn is an optional dependency (requirements.txt, but not
    guaranteed present) — a missing import must degrade to PCA, not crash
    the build the way a missing bge model would if unhandled."""
    monkeypatch.setitem(sys.modules, "umap", None)
    config = load_config()
    rng = np.random.default_rng(3)
    vectors = rng.normal(size=(20, 12))
    coords, method = dashboard.project_2d(vectors, config)
    assert method == "pca"
    assert coords.shape == (20, 2)


def test_project_2d_uses_umap_when_available():
    umap = pytest.importorskip("umap")
    del umap
    config = load_config()
    rng = np.random.default_rng(4)
    vectors = rng.normal(size=(30, 12))
    coords, method = dashboard.project_2d(vectors, config)
    assert method == "umap"
    assert coords.shape == (30, 2)


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
