"""Tests for the Jupyter notebook exporter.

Offline like the rest of the suite: the fixture builds a small but *faithful*
run in a temporary DuckDB — faithful meaning its stored scores are produced by
the same functions the real stages use, so the exporter's re-derivation checks
are exercised against real arithmetic rather than against numbers invented to
agree with them.

The test that matters most is `test_verification_survives_a_config_edit`. The
notebook's whole claim is that it re-derives a run under the config that
produced it; if it silently fell back to whatever is on disk today, every
verification cell would become a tautology the first time somebody touched a
weight.
"""

from __future__ import annotations

import copy
import json
import re

import pytest

from src import db, notebook
from src.config import load_config, snapshot_config
from src.normalise import percentile_rank
from src.stage2_emergence import assign_horizon
from src.stage4_opportunity_index import redistribute_weights
from src.stage5_synthesis import composite_scores

ATTRS = ("novelty", "growth", "coherence", "impact", "uncertainty")

# Six topics: enough that percentile ranks separate, few enough to read.
_TOPIC_SEEDS = [
    # label, docs, novelty, growth, coherence, impact, uncertainty, maturity
    ("geographical indication / trade / protection", 40, 0.61, 0.44, 0.55, 0.30, 0.42, 0.55),
    ("automated decision-making / administrative law", 32, 0.48, 0.71, 0.61, 0.22, 0.38, 0.81),
    ("prior art / patent / search / retrieval", 25, 0.35, 0.22, 0.72, 0.51, 0.29, 0.90),
    ("indigenous knowledge / treaty / protocol", 18, 0.77, 0.83, 0.44, 0.11, 0.66, 0.19),
    ("trust / institution / public / citizen", 22, 0.29, 0.35, 0.50, 0.44, 0.51, 0.62),
    ("quantum / sensing / metrology", 9, 0.82, 0.66, 0.38, 0.18, 0.72, 0.33),  # thin: suppressed
]


def _fixture_config(tmp_path) -> dict:
    """The shipped config, pointed at a temporary corpus and output directory."""
    config = load_config()
    config["storage"]["duckdb_path"] = str(tmp_path / "fixture.duckdb")
    config["storage"]["outputs_dir"] = str(tmp_path / "outputs")
    config["notebook"] = {
        "enabled": True,
        "topics_detailed": 3,
        "evidence_documents_per_topic": 2,
        "include_verification": True,
    }
    return config


def _build_run(config: dict, run_id: str) -> None:
    """Populate a temporary DuckDB with one internally consistent run.

    Mirrors what Stages 1-5 persist, computing every derived score with the
    production helpers so the exporter has something real to reproduce.
    """
    conn = db.init_db(config["storage"]["duckdb_path"])
    try:
        # --- Stage 1 ------------------------------------------------------
        documents = []
        for t, (label, count, *_rest) in enumerate(_TOPIC_SEEDS):
            for i in range(count):
                year = 2019 + (i % 7)
                source = ("crossref", "arxiv", "gdelt", "datagovau")[i % 4]
                documents.append(
                    {
                        "doc_id": f"doc-{t}-{i}",
                        "source": source,
                        "native_id": f"10.0000/{t}.{i}",
                        "title": f"{label.split(' / ')[0].title()} study {i}",
                        "abstract": f"An abstract about {label}.",
                        "published_date": f"{year}-06-01",
                        "year": year,
                        "time_slice": str(year),
                        "url": f"https://example.invalid/{t}/{i}",
                        "venue": "Journal of Test Fixtures",
                        "authors": ["A. Author"],
                        "institutions": ["Test University"],
                        "concepts": [label],
                        "citation_count": (i * 3) % 40,
                        "steepv": ("Technological", "Legal", "Social")[t % 3],
                        "scan_frame_key": f"frame_{t}",
                        "tone": -1.5 + (i % 5) if source == "gdelt" else None,
                        "raw_path": None,
                        "collected_at": db.now(),
                        "run_id": run_id,
                    }
                )
        db.upsert_documents(conn, documents)

        for source in ("crossref", "arxiv", "gdelt", "datagovau"):
            db.log_collection(conn, run_id, source, "frame_0", "success", records=40)
        db.log_collection(conn, run_id, "gdelt", "frame_1", "failed", message="connection reset")

        # --- Stage 0 ------------------------------------------------------
        db.replace_strategy_refs(
            conn,
            [
                {"ref_id": "obj-1.1", "ref_type": "objective", "code": "1.1",
                 "label": "Trust and confidence", "text": "Trust in the IP system.",
                 "lexicon": ["trust", "confidence"], "weight": 1.0},
                {"ref_id": "obj-4.2", "ref_type": "objective", "code": "4.2",
                 "label": "Digital and data-driven services",
                 "text": "Digital service delivery.",
                 "lexicon": ["digital", "automation"], "weight": 1.0},
                {"ref_id": "asset-1", "ref_type": "asset", "code": None,
                 "label": "First Nations partnerships",
                 "text": "Relationships under the First Nations Strategy.",
                 "lexicon": ["indigenous knowledge"], "weight": 1.0},
            ],
        )

        # --- Stage 2 ------------------------------------------------------
        topics = []
        for t, (label, count, nov, grw, coh, imp, unc, maturity) in enumerate(_TOPIC_SEEDS):
            years = [str(y) for y in range(2019, 2026)]
            topics.append(
                {
                    "topic_id": f"t{t:04d}",
                    "label": label,
                    "terms": [[w, 1.0 - 0.1 * i] for i, w in enumerate(label.split(" / "))],
                    "document_count": count,
                    "first_slice": years[0],
                    "last_slice": years[-1],
                    "novelty": nov, "growth": grw, "coherence": coh,
                    "impact": imp, "uncertainty": unc,
                    "emergence_score": 0.0,          # filled below, as Stage 2 does
                    "burst_weight": 0.4 + 0.1 * t,
                    "burst_slices": [years[3]],
                    "cagr": 0.05 * (t + 1),
                    "maturity": maturity,
                    "horizon": assign_horizon(maturity, config),
                    "signal_class": ("weak", "strong", "latent", "noise")[t % 4],
                    "avg_proportion": 0.02 * (t + 1),
                    "documents": [
                        {"doc_id": f"doc-{t}-{i}", "similarity": 0.9 - 0.01 * i}
                        for i in range(count)
                    ],
                    "timeseries": [
                        {"time_slice": y, "doc_count": count // len(years) + (j % 3),
                         "proportion": 0.01 * (j + 1), "in_burst": j == 3}
                        for j, y in enumerate(years)
                    ],
                }
            )

        weights = config["emergence"]["rotolo_weights"]
        ranked = {a: percentile_rank([t[a] for t in topics]) for a in ATTRS}
        for i, topic in enumerate(topics):
            topic["emergence_score"] = sum(ranked[a][i] * float(weights[a]) for a in ATTRS)
        db.replace_topics(conn, run_id, topics)

        # --- Stages 3-5 ---------------------------------------------------
        # Read back in Stage 5's own order (emergence_score DESC): the stable
        # sort that produces `rank` depends on it, so the fixture has to use it
        # too or the ordering it stores is not the one the code would produce.
        stored = db.fetch_topics(conn, run_id)
        fit = {t["topic_id"]: 0.05 + 0.04 * i for i, t in enumerate(stored)}
        lev = {t["topic_id"]: 0.10 - 0.01 * i for i, t in enumerate(stored)}

        component_weights = config["opportunity_index"]["components"]
        available = {"research_growth", "attention", "attention_tone", "policy_salience"}
        effective = redistribute_weights(component_weights, available)
        raw = {k: [hash((k, t["topic_id"])) % 100 / 100.0 for t in stored] for k in effective}
        component_ranks = {k: percentile_rank(v) for k, v in raw.items()}
        min_documents = int(config["opportunity_index"]["min_documents"])

        rows = []
        for i, topic in enumerate(stored):
            components = {k: round(component_ranks[k][i], 4) for k in effective}
            suppressed = int(topic["document_count"]) < min_documents
            rows.append(
                {
                    **topic,
                    "strategic_fit": fit[topic["topic_id"]],
                    "asset_leverage": lev[topic["topic_id"]],
                    "best_objective": "4.2 Digital and data-driven services",
                    "best_objective_sim": 0.4,
                    "critical_tech": None,
                    # Stage 4 combines the ROUNDED components with the
                    # UNROUNDED weights, and stores the weights rounded. That
                    # asymmetry is the exporter's index tolerance; reproduce it.
                    "opportunity_index": (
                        None if suppressed
                        else sum(components[k] * w for k, w in effective.items())
                    ),
                    "index_components": {
                        **components,
                        "_effective_weights": {k: round(v, 4) for k, v in effective.items()},
                        "_omitted_components": sorted(set(component_weights) - available),
                    },
                    "index_suppressed": suppressed,
                }
            )

        for row, score in zip(rows, composite_scores(rows, config["synthesis"]["rank_weights"])):
            row["composite_rank_score"] = score
        rows.sort(key=lambda r: -r["composite_rank_score"])
        for position, row in enumerate(rows, 1):
            row["rank"] = position
        db.replace_topic_scores(conn, run_id, rows)

        # --- run log ------------------------------------------------------
        for stage in ("stage0_strategy", "stage1_collect", "stage2_emergence",
                      "stage5_synthesis"):
            entry = db.log_stage_start(conn, run_id, stage, snapshot_config(config))
            db.log_stage_finish(conn, entry, "success", records_in=len(documents),
                                records_out=len(topics), message=f"{stage} ok")
    finally:
        conn.close()


@pytest.fixture()
def run_fixture(tmp_path):
    config = _fixture_config(tmp_path)
    run_id = "test-run"
    _build_run(config, run_id)
    return config, run_id


def _outputs(document: dict) -> str:
    """All stdout the executed cells produced, concatenated."""
    return "\n".join(
        "".join(out.get("text", []))
        for cell in document["cells"]
        for out in cell.get("outputs", [])
        if out["output_type"] == "stream"
    )


# --- structure ------------------------------------------------------------


def test_notebook_is_structurally_valid(run_fixture):
    """A file Jupyter refuses to open is worth nothing to a reviewer."""
    config, run_id = run_fixture
    path = notebook.run(config, run_id)
    document = json.loads(path.read_text(encoding="utf-8"))

    assert document["nbformat"] == 4
    assert document["nbformat_minor"] == 5
    assert document["metadata"]["kernelspec"]["name"] == "python3"

    ids = [cell["id"] for cell in document["cells"]]
    assert len(ids) == len(set(ids)), "cell ids must be unique"
    assert all(re.fullmatch(r"[a-zA-Z0-9_-]{1,64}", i) for i in ids)

    for cell in document["cells"]:
        assert cell["cell_type"] in {"markdown", "code"}
        assert isinstance(cell["source"], list)
        if cell["cell_type"] == "code":
            assert isinstance(cell["outputs"], list)
            assert cell["execution_count"] is not None


def test_cells_are_executed_in_one_namespace(run_fixture):
    """Later cells depend on names bound by earlier ones, as in a real kernel."""
    config, run_id = run_fixture
    conn_config = copy.deepcopy(config)
    document, failures = notebook.build(
        __import__("duckdb").connect(conn_config["storage"]["duckdb_path"], read_only=True),
        conn_config, run_id,
    )
    assert failures == [], f"cells failed: {failures}"
    errors = [
        out for cell in document["cells"] for out in cell.get("outputs", [])
        if out["output_type"] == "error"
    ]
    assert errors == [], f"error outputs embedded: {errors}"


# --- the verification cells ------------------------------------------------


def test_every_verification_passes_on_a_consistent_run(run_fixture):
    config, run_id = run_fixture
    path = notebook.run(config, run_id)
    text = _outputs(json.loads(path.read_text(encoding="utf-8")))

    assert "FAIL" not in text, text
    for check in ("emergence_score", "Three Horizons band", "opportunity_index",
                  "composite_rank_score", "shortlist ordering"):
        assert f"PASS  {check}" in text, f"{check} was not verified:\n{text}"


def test_verification_survives_a_config_edit(run_fixture):
    """Weights come from the run's snapshot, never from today's config.

    Without this the checks are tautological: edit a weight, and a notebook
    that verified against the edited file would still report PASS while
    describing arithmetic that never happened.
    """
    config, run_id = run_fixture
    edited = copy.deepcopy(config)
    edited["emergence"]["rotolo_weights"] = {
        "novelty": 0.5, "growth": 0.2, "coherence": 0.1,
        "impact": 0.1, "uncertainty": 0.1,
    }
    edited["synthesis"]["rank_weights"] = {
        "emergence": 0.1, "strategic_fit": 0.1, "asset_leverage": 0.8,
    }

    path = notebook.run(edited, run_id)
    text = _outputs(json.loads(path.read_text(encoding="utf-8")))
    assert "FAIL" not in text, (
        "verification used the edited config instead of the run's snapshot:\n" + text
    )
    assert "PASS  emergence_score" in text


def test_verify_close_detects_a_mismatch(capsys):
    """A check that cannot fail is not a check."""
    assert notebook.verify_close("x", [0.1, 0.2], [0.1, 0.2]) is True
    assert notebook.verify_close("x", [0.1, 0.2], [0.1, 0.9]) is False
    assert "FAIL" in capsys.readouterr().out


def test_verify_close_rejects_a_different_population_size(capsys):
    assert notebook.verify_close("x", [0.1, 0.2], [0.1]) is False
    assert "do not describe the same topics" in capsys.readouterr().out


def test_verify_close_tolerance_is_absolute_not_relative(capsys):
    """The index check runs at 1e-3 because Stage 4 stores rounded weights."""
    assert notebook.verify_close("x", [0.5], [0.5004], tolerance=notebook.TOLERANCE_INDEX)
    assert not notebook.verify_close("x", [0.5], [0.5004])
    capsys.readouterr()


def test_verify_identical_detects_reordering(capsys):
    assert notebook.verify_identical("order", ["a", "b", "c"], ["a", "b", "c"]) is True
    assert notebook.verify_identical("order", ["a", "b", "c"], ["a", "c", "b"]) is False
    out = capsys.readouterr().out
    assert "first divergence at position 1" in out


# --- config drift ----------------------------------------------------------


def test_diff_config_reports_changed_leaves_by_path():
    changes = notebook.diff_config(
        {"emergence": {"topics": {"min_topic_size": 8}}, "kept": 1},
        {"emergence": {"topics": {"min_topic_size": 12}}, "kept": 1},
    )
    assert changes == [("emergence.topics.min_topic_size", 8, 12)]


def test_diff_config_reports_added_and_removed_keys():
    changes = dict((path, (was, now)) for path, was, now in
                   notebook.diff_config({"a": 1}, {"b": 2}))
    assert changes == {"a": (1, None), "b": (None, 2)}


# --- observations ----------------------------------------------------------


def test_analyst_observations_are_inserted(run_fixture, tmp_path):
    config, run_id = run_fixture
    conn = db.init_db(config["storage"]["duckdb_path"])
    try:
        top_topic = db.fetch_ranked_topics(conn, run_id, limit=1)[0]["topic_id"]
    finally:
        conn.close()

    out_dir = tmp_path / "outputs" / run_id
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "observations.yaml").write_text(
        "stage1: |\n"
        "  GDELT dropped a third of its queries this week.\n"
        f"topic:{top_topic}: |\n"
        "  Reads as a real theme; the 2023 burst is a single conference.\n",
        encoding="utf-8",
    )

    path = notebook.run(config, run_id)
    document = json.loads(path.read_text(encoding="utf-8"))
    markdown = "\n".join(
        "".join(cell["source"]) for cell in document["cells"]
        if cell["cell_type"] == "markdown"
    )
    assert "GDELT dropped a third of its queries" in markdown
    assert "the 2023 burst is a single conference" in markdown
    assert markdown.count("**Analyst observation**") == 2


def test_unknown_observation_anchor_is_rejected(tmp_path):
    """A typo'd anchor must not silently drop the analyst's writing."""
    path = tmp_path / "observations.yaml"
    path.write_text("stage9: something\n", encoding="utf-8")
    with pytest.raises(ValueError, match="unknown observation anchor"):
        notebook.load_observations(path)


def test_missing_or_empty_observations_file_is_fine(tmp_path):
    assert notebook.load_observations(tmp_path / "absent.yaml") == {}
    empty = tmp_path / "empty.yaml"
    empty.write_text("", encoding="utf-8")
    assert notebook.load_observations(empty) == {}


def test_auto_observations_flag_a_suppressed_index_and_a_thin_axis(run_fixture):
    config, run_id = run_fixture
    path = notebook.run(config, run_id)
    markdown = "\n".join(
        "".join(cell["source"]) for cell in json.loads(path.read_text(encoding="utf-8"))["cells"]
        if cell["cell_type"] == "markdown"
    )
    # The 9-document topic is below opportunity_index.min_documents.
    assert "suppressed rather than scored zero" in markdown
    # Fit spans 0.05-0.25 and leverage 0.05-0.10 in the fixture; both compressed.
    assert "Asset leverage spans only" in markdown
    # patent_activity has no data in the fixture, so its weight is redistributed.
    assert "patent_activity" in markdown


# --- rendering -------------------------------------------------------------


def test_table_handles_empty_rows_and_nulls():
    assert "(no rows)" in notebook.table([], ["a", "b"])
    rendered = notebook.table([(1, None, "x"), (2, 0.5, None)], ["n", "v", "s"])
    assert "—" in rendered
    assert "0.500" in rendered


def test_table_right_aligns_numeric_columns():
    rendered = notebook.table([(1, "a"), (200, "b")], ["n", "s"]).splitlines()
    assert rendered[-2].startswith("  1")   # 1 padded to the width of 200
    assert rendered[-1].startswith("200")


# --- guard rails -----------------------------------------------------------


def test_export_refuses_a_run_that_has_no_ranking(run_fixture):
    config, _ = run_fixture
    with pytest.raises(SystemExit, match="Run Stage 5 first"):
        notebook.run(config, "a-run-that-was-never-scored")


def test_include_verification_false_removes_every_check(run_fixture):
    """The flag has to govern all four checks or it governs none of them.

    Stage 4's check originally ran regardless, so a notebook generated with
    verification off still claimed to have verified something.
    """
    config, run_id = run_fixture
    config["notebook"]["include_verification"] = False
    path = notebook.run(config, run_id)
    document = json.loads(path.read_text(encoding="utf-8"))

    text = _outputs(document)
    assert "PASS" not in text, text
    assert "FAIL" not in text
    # No call sites left. The setup cell still imports the helpers, which is
    # why this looks for the call and not the name.
    code = "".join(
        "".join(cell["source"]) for cell in document["cells"]
        if cell["cell_type"] == "code"
    )
    assert "verify_close(" not in code
    assert "verify_identical(" not in code
    # The descriptive content is unaffected — only the checks are dropped.
    assert "The published shortlist" in "".join(
        "".join(cell["source"]) for cell in document["cells"]
        if cell["cell_type"] == "markdown"
    )


def test_no_execute_emits_cells_without_outputs(run_fixture):
    config, run_id = run_fixture
    path = notebook.run(config, run_id, execute=False)
    document = json.loads(path.read_text(encoding="utf-8"))
    code_cells = [c for c in document["cells"] if c["cell_type"] == "code"]
    assert code_cells
    assert all(c["outputs"] == [] and c["execution_count"] is None for c in code_cells)
