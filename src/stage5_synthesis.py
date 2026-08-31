"""
src/stage5_synthesis.py — Stage 5: Opportunity synthesis and ranking.

The output stage. Combines Stage 2 emergence, Stage 3 fit and leverage, and
Stage 4's relative index into a ranked shortlist, and writes the artefacts a
human actually reads:

  data/outputs/<run_id>/shortlist.md      ranked shortlist with the 2x2 views
  data/outputs/<run_id>/evidence/*.md     one evidence card per shortlisted topic
  data/outputs/<run_id>/topics.csv        every topic, every score, for analysis
  data/outputs/<run_id>/summary.json      machine-readable run summary

RANKING. A weighted combination of emergence, strategic fit and asset leverage
(`synthesis.rank_weights`). The opportunity index is deliberately NOT in the
ranking formula: it is the weakest-founded number in the pipeline, and folding
it into the rank would launder that weakness into the headline ordering. It is
reported alongside, where a reader can weigh it themselves.

WHAT THIS STAGE IS NOT. It is not the answer. Everything here is a candidate
list produced by a method whose weights have not yet been validated against a
known outcome. The proposal is explicit that the pipeline surfaces candidates
and a short human sense-making session does the rest — Seven Questions or a
lightweight scenario stress-test over the top ten, with Doblin Ten Types as a
diagnostic to widen the framing beyond "new product". Every evidence card ends
with those prompts for that reason.

Run:
    python -m src.stage5_synthesis --run-id 2026-08-29
"""

from __future__ import annotations

import argparse
import csv
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np

from src import db
from src.config import get, load_config, resolve_path, snapshot_config
from src.errors import insufficient_data_error
from src.normalise import percentile_rank

logger = logging.getLogger(__name__)

STAGE = "stage5_synthesis"

HORIZON_MEANING = {
    "H1": "H1 — established (0-3 yrs): sustains the current system",
    "H2": "H2 — transitional: emerging innovation, the contested middle",
    "H3": "H3 — paradigm shift (10-30 yrs): weak now, structural if it lands",
}

SIGNAL_MEANING = {
    "weak": "weak signal — low volume, high growth (the horizon-scanning target)",
    "strong": "strong signal — high volume, high growth (already visible to everyone)",
    "latent": "latent — high volume, low growth (established, not moving)",
    "noise": "noise — low volume, low growth",
}

# The Doblin Ten Types, used as a synthesis diagnostic rather than a score.
# Reproduced so the prompt travels with the evidence card.
DOBLIN_TEN_TYPES = [
    ("Configuration", ["Profit model", "Network", "Structure", "Process"]),
    ("Offering", ["Product performance", "Product system"]),
    ("Experience", ["Service", "Channel", "Brand", "Customer engagement"]),
]

SEVEN_QUESTIONS = [
    "What are the sources of confidence in this reading, and of doubt?",
    "If this is true, what would have to change about how IP Australia works?",
    "Who wins and who loses if this trend continues?",
    "What would have to happen for this to become urgent within 12 months?",
    "What is already being done, by us or by others?",
    "What would we need to know to decide, and how could we find it out cheaply?",
    "If we do nothing, what is the cost in three years?",
]


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------


RANK_AXES = (
    ("emergence", "emergence_score"),
    ("strategic_fit", "strategic_fit"),
    ("asset_leverage", "asset_leverage"),
)


def composite_scores(
    rows: Sequence[dict[str, Any]], weights: dict[str, float]
) -> list[float]:
    """Weighted combination of emergence, strategic fit and asset leverage.

    The three axes are rank-normalised across the population before weighting,
    for the same reason the Rotolo attributes are (see stage2_emergence): a
    weighted sum of raw values is dominated by whichever axis happens to have
    the widest spread, so the configured weights end up describing something
    other than what the code does. Measured on the 2026-08-29 run, asset
    leverage spanned 0.03-0.10 against emergence's 0.33-0.79, and a configured
    25% weight bought it 6% of the actual influence.

    Rank-normalising makes the weights mean what they say, and lets a
    compressed axis still express its ordering — which is the part of it that
    is meaningful. It also makes the composite relative to this run's
    population, like every other combined score here.

    Deliberately excludes the opportunity index — see the module docstring.
    """
    if not rows:
        return []
    ranked = {
        name: percentile_rank([float(r.get(column) or 0.0) for r in rows])
        for name, column in RANK_AXES
    }
    return [
        float(sum(ranked[name][i] * float(weights.get(name, 0.0)) for name, _ in RANK_AXES))
        for i in range(len(rows))
    ]


def quadrant(x: float, y: float, x_cut: float, y_cut: float, labels: Sequence[str]) -> str:
    """Place a point on a 2x2. labels = (low-low, high-low, low-high, high-high)."""
    return labels[(1 if x >= x_cut else 0) + (2 if y >= y_cut else 0)]


# ---------------------------------------------------------------------------
# Output writers
# ---------------------------------------------------------------------------


def write_topics_csv(path: Path, rows: Sequence[dict[str, Any]]) -> None:
    """Every topic with every score — the analysis-ready table."""
    columns = [
        "rank", "topic_id", "label", "document_count", "horizon", "signal_class",
        "emergence_score", "novelty", "growth", "coherence", "impact", "uncertainty",
        "burst_weight", "burst_slices", "cagr", "maturity", "avg_proportion",
        "strategic_fit", "best_objective", "critical_tech", "asset_leverage",
        "best_asset", "opportunity_index", "index_suppressed",
        "composite_rank_score", "first_slice", "last_slice", "fit_quadrant",
    ]
    with path.open("w", encoding="utf-8", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            out = dict(row)
            out["burst_slices"] = ";".join(row.get("burst_slices") or [])
            writer.writerow(out)


def write_evidence_card(
    path: Path,
    topic: dict[str, Any],
    documents: Sequence[dict[str, Any]],
    timeseries: Sequence[dict[str, Any]],
    config: dict[str, Any],
) -> None:
    """One card per shortlisted topic: every score, and the text behind it."""
    lines: list[str] = []
    add = lines.append

    add(f"# {topic['rank']}. {topic['label'] or topic['topic_id']}")
    add("")
    add(f"`{topic['topic_id']}` · {topic['document_count']} documents · "
        f"{topic.get('first_slice')}–{topic.get('last_slice')}")
    add("")

    # --- verdict line -----------------------------------------------------
    add(f"**{HORIZON_MEANING.get(topic.get('horizon', ''), topic.get('horizon', '?'))}**  ")
    add(f"**{SIGNAL_MEANING.get(topic.get('signal_class', ''), topic.get('signal_class', '?'))}**")
    add("")

    # --- scores -----------------------------------------------------------
    add("## Scores")
    add("")
    add("| Measure | Score | What it means |")
    add("|---|---:|---|")
    add(f"| Composite rank score | {topic['composite_rank_score']:.3f} | "
        f"Weighted emergence + fit + leverage |")
    add(f"| Emergence (Rotolo) | {float(topic['emergence_score']):.3f} | "
        f"Weighted five-attribute score |")
    add(f"| — novelty | {float(topic['novelty']):.3f} | Distance from where the field already was |")
    add(f"| — growth | {float(topic['growth']):.3f} | CAGR blended with burst intensity |")
    add(f"| — coherence | {float(topic['coherence']):.3f} | How tightly the documents cluster |")
    add(f"| — impact | {float(topic['impact']):.3f} | Citation percentile within source |")
    add(f"| — uncertainty | {float(topic['uncertainty']):.3f} | Dispersion of actors and evidence types |")
    add(f"| Strategic fit | {float(topic['strategic_fit']):.3f} | Closeness to published strategy |")
    add(f"| Asset leverage | {float(topic['asset_leverage']):.3f} | What IP Australia would bring |")
    index = topic.get("opportunity_index")
    if topic.get("index_suppressed"):
        add("| Opportunity index | *suppressed* | Too few documents to compute honestly |")
    else:
        add(f"| Opportunity index | {float(index):.3f} | **Relative only — not a market size** |")
    add("")

    add(f"- **Closest strategic objective:** {topic.get('best_objective') or '—'}")
    add(f"- **Closest agency asset:** {topic.get('best_asset') or '—'}")
    add(f"- **DISR critical technology:** {topic.get('critical_tech') or '— (no match)'}")
    cagr = float(topic.get("cagr") or 0.0)
    add(f"- **Growth:** {cagr:+.1%} per {get(config, 'emergence', 'time_slice', default='year')}"
        f" · maturity {float(topic.get('maturity') or 0):.2f}")
    bursts = topic.get("burst_slices") or []
    add(f"- **Burst periods:** {', '.join(bursts) if bursts else 'none detected'}")
    add("")

    # --- terms and trajectory --------------------------------------------
    add("## Defining terms")
    add("")
    add(", ".join(f"`{term}`" for term, _ in (topic.get("terms") or [])[:10]) or "—")
    add("")

    if timeseries:
        add("## Trajectory")
        add("")
        peak = max((int(p["doc_count"]) for p in timeseries), default=0) or 1
        add("```")
        for point in timeseries:
            count = int(point["doc_count"])
            bar = "█" * int(round(20 * count / peak))
            flag = "  ← burst" if point.get("in_burst") else ""
            add(f"{point['time_slice']:>7s} {count:5d} {bar}{flag}")
        add("```")
        add("")

    # --- evidence ---------------------------------------------------------
    add("## Evidence")
    add("")
    add("The documents nearest this topic's centre. Every score above derives "
        "from this set — if the documents do not look like a coherent theme, "
        "the topic is an artefact and should be discarded.")
    add("")
    for doc in documents:
        title = (doc.get("title") or "").strip() or "(untitled)"
        url = doc.get("url") or ""
        meta = " · ".join(
            filter(None, [
                str(doc.get("source", "")),
                str(doc.get("year") or ""),
                str(doc.get("venue") or "")[:60],
                f"{int(doc.get('citation_count') or 0)} citations"
                if doc.get("citation_count") else "",
            ])
        )
        add(f"- [{title}]({url})  " if url else f"- {title}  ")
        add(f"  <sub>{meta}</sub>")
    add("")

    # --- human synthesis prompts -----------------------------------------
    if bool(get(config, "synthesis", "doblin_prompt_in_cards", default=True)):
        add("## Synthesis prompts")
        add("")
        add("This is where the pipeline stops and judgement starts. The scores above "
            "rank candidates; they do not decide anything.")
        add("")
        add("**Doblin Ten Types** — where could the innovation actually sit? Doblin's "
            "research found breakthroughs usually combine several types, and that "
            "product-only innovation returns the least. A regulator can innovate in "
            "process, channel and engagement, not just in what it offers.")
        add("")
        for cluster, types in DOBLIN_TEN_TYPES:
            add(f"- *{cluster}*: {', '.join(types)}")
        add("")
        add("**Seven Questions** — for the sense-making session:")
        add("")
        for i, question in enumerate(SEVEN_QUESTIONS, 1):
            add(f"{i}. {question}")
        add("")

    path.write_text("\n".join(lines), encoding="utf-8")


def write_shortlist(
    path: Path,
    rows: Sequence[dict[str, Any]],
    shortlist: Sequence[dict[str, Any]],
    config: dict[str, Any],
    run_id: str,
    stats: dict[str, Any],
) -> None:
    """The document a human reads first."""
    lines: list[str] = []
    add = lines.append

    add("# IPAVentures horizon scan — shortlist")
    add("")
    add(f"Run `{run_id}` · generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC")
    add("")
    add(f"{stats['documents']:,} documents · {stats['topics']} topics · "
        f"{stats['slices']} time slices ({stats['span']}) · "
        f"embedding backend `{stats['backend']}`")
    add("")

    add("> **How to read this.** These are *candidates*, not conclusions. The ranking "
        "combines an emergence score, strategic fit and asset leverage; the weights "
        "behind it are a considered judgement that has not yet been validated against "
        "a known past opportunity. The opportunity index is relative and is not a "
        "market size. Read the evidence cards before you believe any row.")
    add("")

    # --- the shortlist ----------------------------------------------------
    add("## Ranked shortlist")
    add("")
    add("| # | Topic | H | Signal | Emrg | Fit | Lev | Index | Closest objective |")
    add("|---:|---|:-:|---|---:|---:|---:|---:|---|")
    for row in shortlist:
        index = (
            "—" if row.get("index_suppressed")
            else f"{float(row['opportunity_index']):.2f}"
        )
        label = (row.get("label") or row["topic_id"])[:44]
        objective = (row.get("best_objective") or "—")[:34]
        add(
            f"| {row['rank']} | **{label}** | {row.get('horizon','')} | "
            f"{row.get('signal_class','')} | {float(row['emergence_score']):.2f} | "
            f"{float(row['strategic_fit']):.2f} | {float(row['asset_leverage']):.2f} | "
            f"{index} | {objective} |"
        )
    add("")
    add(f"Evidence cards: `data/outputs/{run_id}/evidence/`")
    add("")

    # --- 2x2 views --------------------------------------------------------
    add("## View 1 — strategic fit × emergence")
    add("")
    add("*Which emerging trends are on strategy?*")
    add("")
    _add_quadrant_table(add, rows, "strategic_fit", "emergence_score",
                        "strategic fit", "emergence")
    add("## View 2 — strategic fit × asset leverage")
    add("")
    add("*Of those, which could IP Australia credibly act on?* This is the view that "
        "separates an interesting trend from a viable venture.")
    add("")
    _add_quadrant_table(add, rows, "strategic_fit", "asset_leverage",
                        "strategic fit", "asset leverage")

    # --- distributions ----------------------------------------------------
    add("## Distribution")
    add("")
    add("| Three Horizons | Topics | | Signal class | Topics |")
    add("|---|---:|---|---|---:|")
    horizons = ["H1", "H2", "H3"]
    signals = ["weak", "strong", "latent", "noise"]
    for i in range(max(len(horizons), len(signals))):
        h = horizons[i] if i < len(horizons) else ""
        hc = sum(1 for r in rows if r.get("horizon") == h) if h else ""
        s = signals[i] if i < len(signals) else ""
        sc = sum(1 for r in rows if r.get("signal_class") == s) if s else ""
        add(f"| {h} | {hc} | | {s} | {sc} |")
    add("")

    # --- next step --------------------------------------------------------
    add("## What happens next")
    add("")
    add("1. **Discard the artefacts.** Open the top evidence cards. Any topic whose "
        "documents are not a coherent theme is a clustering artefact — note it and "
        "drop it. Expect some.")
    add("2. **Run the sense-making session** over the survivors. Seven Questions on "
        "each, with Doblin Ten Types to widen the framing past 'new product'. The "
        "prompts are on every card.")
    add("3. **Validate the weights.** Pick an opportunity IP Australia already "
        "pursued and check whether this pipeline would have surfaced it. Until that "
        "is done, the ranking is a hypothesis about ranking.")
    add("")
    add("---")
    add("")
    add("<sub>Generated by the BigThink pipeline. Method: `docs/method.md`. "
        "Scan frame: `data/strategy/scan_frame.yaml` — the scan cannot find what the "
        "frame does not ask for.</sub>")

    path.write_text("\n".join(lines), encoding="utf-8")


def _add_quadrant_table(
    add: Any, rows: Sequence[dict[str, Any]], x_key: str, y_key: str,
    x_name: str, y_name: str,
) -> None:
    """Render a 2x2 as a markdown table, split at the median of each axis."""
    xs = [float(r.get(x_key) or 0.0) for r in rows]
    ys = [float(r.get(y_key) or 0.0) for r in rows]
    if not xs:
        return
    x_cut, y_cut = float(np.median(xs)), float(np.median(ys))

    buckets: dict[str, list[str]] = {k: [] for k in ("ll", "hl", "lh", "hh")}
    for row in rows:
        key = quadrant(
            float(row.get(x_key) or 0.0), float(row.get(y_key) or 0.0),
            x_cut, y_cut, ("ll", "hl", "lh", "hh"),
        )
        buckets[key].append((row.get("label") or row["topic_id"])[:38])

    add(f"Split at the median of each axis ({x_name} {x_cut:.2f}, {y_name} {y_cut:.2f}).")
    add("")
    add(f"| | low {x_name} | high {x_name} |")
    add("|---|---|---|")
    add(f"| **high {y_name}** | {_cell(buckets['lh'])} | {_cell(buckets['hh'])} |")
    add(f"| **low {y_name}** | {_cell(buckets['ll'])} | {_cell(buckets['hl'])} |")
    add("")


def _cell(items: Sequence[str], limit: int = 6) -> str:
    if not items:
        return "*(none)*"
    shown = "<br>".join(items[:limit])
    extra = f"<br>*+{len(items) - limit} more*" if len(items) > limit else ""
    return shown + extra


# ---------------------------------------------------------------------------
# Stage entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    entry_id = db.log_stage_start(conn, run_id, STAGE, snapshot_config(config))
    try:
        rows = _run_inner(conn, config, run_id)
        db.log_stage_finish(
            conn, entry_id, "success",
            records_in=len(rows), records_out=len(rows),
            message=f"ranked {len(rows)} topics; outputs written",
        )
        return rows
    except Exception as exc:
        db.log_stage_finish(conn, entry_id, "failed", message=str(exc))
        raise
    finally:
        conn.close()


def _run_inner(conn: Any, config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    from src.stage3_scoring import run as run_stage3
    from src.stage4_opportunity_index import run as run_stage4

    topics = db.fetch_topics(conn, run_id)
    if not topics:
        raise insufficient_data_error(
            STAGE, f"no topics for run_id={run_id!r}. Run Stage 2 first."
        )

    # Stages 3 and 4 are computed here and written together, so `topic_scores`
    # is never left half-populated by a crash between them.
    fit_scores = {s["topic_id"]: s for s in run_stage3(config, run_id)}
    index_scores = {s["topic_id"]: s for s in run_stage4(config, run_id)}

    weights = get(config, "synthesis", "rank_weights", default={}) or {}
    rows: list[dict[str, Any]] = [
        {**topic, **fit_scores.get(topic["topic_id"], {}),
         **index_scores.get(topic["topic_id"], {})}
        for topic in topics
    ]
    for row, score in zip(rows, composite_scores(rows, weights)):
        row["composite_rank_score"] = score

    rows.sort(key=lambda r: -r["composite_rank_score"])
    for position, row in enumerate(rows, 1):
        row["rank"] = position

    # 2x2 quadrant label, recorded per topic so it survives into the CSV.
    fits = [float(r.get("strategic_fit") or 0.0) for r in rows]
    levs = [float(r.get("asset_leverage") or 0.0) for r in rows]
    fit_cut, lev_cut = float(np.median(fits)), float(np.median(levs))
    for row in rows:
        row["fit_quadrant"] = quadrant(
            float(row.get("strategic_fit") or 0.0),
            float(row.get("asset_leverage") or 0.0),
            fit_cut, lev_cut,
            ("watch", "on-strategy, no right-to-play",
             "capability looking for a problem", "act"),
        )

    db.replace_topic_scores(conn, run_id, [
        {
            "topic_id": r["topic_id"],
            "strategic_fit": r.get("strategic_fit"),
            "best_objective": r.get("best_objective"),
            "best_objective_sim": r.get("best_objective_sim"),
            "critical_tech": r.get("critical_tech"),
            "asset_leverage": r.get("asset_leverage"),
            "best_asset": r.get("best_asset"),
            "opportunity_index": r.get("opportunity_index"),
            "index_components": r.get("index_components"),
            "index_suppressed": r.get("index_suppressed", False),
            "composite_rank_score": r["composite_rank_score"],
            "rank": r["rank"],
            "fit_quadrant": r.get("fit_quadrant"),
        }
        for r in rows
    ])

    _write_outputs(conn, config, run_id, rows)
    return rows


def _write_outputs(
    conn: Any, config: dict[str, Any], run_id: str, rows: list[dict[str, Any]]
) -> None:
    out_dir = resolve_path(config, "storage", "outputs_dir") / run_id
    evidence_dir = out_dir / "evidence"
    # Cards are named <rank>_<topic_id>.md, and both parts move between runs of
    # the same run_id. Without clearing, a re-run leaves last run's cards beside
    # this run's — two files claiming rank 1, and no way to tell which is live.
    if evidence_dir.exists():
        for stale in evidence_dir.glob("*.md"):
            stale.unlink()
    evidence_dir.mkdir(parents=True, exist_ok=True)

    shortlist_size = int(get(config, "synthesis", "shortlist_size", default=15))
    evidence_n = int(get(config, "synthesis", "evidence_documents_per_topic", default=8))
    shortlist = rows[:shortlist_size]

    documents = db.fetch_documents(conn)
    slices = sorted({d["time_slice"] for d in documents if d.get("time_slice")})
    stats = {
        "documents": len(documents),
        "topics": len(rows),
        "slices": len(slices),
        "span": f"{slices[0]}–{slices[-1]}" if slices else "—",
        "backend": str(get(config, "embeddings", "backend", default="hashing")),
    }

    write_topics_csv(out_dir / "topics.csv", rows)
    write_shortlist(out_dir / "shortlist.md", rows, shortlist, config, run_id, stats)

    for row in shortlist:
        write_evidence_card(
            evidence_dir / f"{row['rank']:02d}_{row['topic_id']}.md",
            row,
            db.fetch_topic_documents(conn, row["topic_id"], limit=evidence_n),
            db.fetch_topic_timeseries(conn, row["topic_id"]),
            config,
        )

    (out_dir / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "generated_at": datetime.now(timezone.utc).isoformat(),
                **stats,
                "config_snapshot": json.loads(snapshot_config(config)),
                "shortlist": [
                    {
                        "rank": r["rank"],
                        "topic_id": r["topic_id"],
                        "label": r["label"],
                        "horizon": r.get("horizon"),
                        "signal_class": r.get("signal_class"),
                        "emergence_score": r.get("emergence_score"),
                        "strategic_fit": r.get("strategic_fit"),
                        "asset_leverage": r.get("asset_leverage"),
                        "opportunity_index": r.get("opportunity_index"),
                        "index_suppressed": r.get("index_suppressed"),
                        "composite_rank_score": r.get("composite_rank_score"),
                        "best_objective": r.get("best_objective"),
                        "best_asset": r.get("best_asset"),
                        "critical_tech": r.get("critical_tech"),
                        "fit_quadrant": r.get("fit_quadrant"),
                    }
                    for r in shortlist
                ],
            },
            indent=2, default=str,
        ),
        encoding="utf-8",
    )
    logger.info("Stage 5 wrote outputs to %s", out_dir)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 5 — synthesis and ranking.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    config = load_config(args.config)
    rows = run(config, args.run_id)

    size = int(get(config, "synthesis", "shortlist_size", default=15))
    print(f"\n{'#':>3s} {'topic':7s} {'emrg':>5s} {'fit':>5s} {'lev':>5s} {'idx':>5s} "
          f"{'H':>2s} {'signal':7s} label")
    for row in rows[:size]:
        index = "  —  " if row.get("index_suppressed") else f"{float(row['opportunity_index']):.3f}"
        print(
            f"{row['rank']:3d} {row['topic_id']:7s} {float(row['emergence_score']):5.3f} "
            f"{float(row['strategic_fit']):5.3f} {float(row['asset_leverage']):5.3f} {index} "
            f"{row.get('horizon',''):>2s} {row.get('signal_class',''):7s} {row['label'][:46]}"
        )
    print(f"\nOutputs: data/outputs/{args.run_id}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
