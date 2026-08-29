"""
src/report.py — build the GitHub Pages site from a run's outputs.

Renders docs/index.html: the ranked shortlist, both 2x2 views, and links to
the evidence cards. Self-contained (no external CSS or JS) because the site is
served from GitHub Pages with no build step, and because a horizon-scan report
that silently fails to render behind a corporate proxy is worse than a plain one.

Run:
    python -m src.report --run-id 2026-08-29
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from src import db
from src.config import REPO_ROOT, get, load_config, resolve_path

logger = logging.getLogger(__name__)

HORIZON_BLURB = {
    "H1": "Established (0–3 yrs)",
    "H2": "Transitional",
    "H3": "Paradigm shift (10–30 yrs)",
}

_CSS = """
:root {
  --bg: #fbfbfa; --fg: #16150f; --muted: #56544c; --line: #e2e0d8;
  --card: #ffffff; --accent: #1c4f8f;
  --h1: #7a5c00; --h2: #1c6b52; --h3: #7a2d6b;
  --weak: #7a2d6b; --strong: #1c4f8f; --latent: #56544c; --noise: #8a8880;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14150f; --fg: #f2f1ea; --muted: #a3a196; --line: #2f3128;
    --card: #1c1e17; --accent: #86b3ec;
    --h1: #e0bc55; --h2: #6fc9a6; --h3: #d78fc4;
    --weak: #d78fc4; --strong: #86b3ec; --latent: #a3a196; --noise: #75736b;
  }
}
* { box-sizing: border-box; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 15px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
}
.wrap { max-width: 1120px; margin: 0 auto; padding: 40px 24px 80px; }
h1 { font-size: 30px; margin: 0 0 6px; letter-spacing: -0.02em; }
h2 { font-size: 20px; margin: 44px 0 6px; letter-spacing: -0.01em; }
h2 + .sub { margin-top: 0; }
.sub { color: var(--muted); margin: 0 0 18px; font-size: 14px; }
.meta { color: var(--muted); font-size: 13px; margin-bottom: 22px; }
.callout {
  background: var(--card); border: 1px solid var(--line);
  border-left: 3px solid var(--accent); border-radius: 6px;
  padding: 14px 18px; margin: 22px 0; font-size: 14px; color: var(--muted);
}
.callout strong { color: var(--fg); }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: 14px; min-width: 720px; }
th, td { text-align: left; padding: 9px 10px; border-bottom: 1px solid var(--line); }
th { font-weight: 600; font-size: 12px; text-transform: uppercase;
     letter-spacing: 0.04em; color: var(--muted); }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
tbody tr:hover { background: var(--card); }
.tag { font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 10px;
       border: 1px solid currentColor; white-space: nowrap; }
.H1 { color: var(--h1); } .H2 { color: var(--h2); } .H3 { color: var(--h3); }
.weak { color: var(--weak); } .strong { color: var(--strong); }
.latent { color: var(--latent); } .noise { color: var(--noise); }
.grid { display: grid; grid-template-columns: 1fr 1fr; gap: 12px; margin: 8px 0 4px; }
@media (max-width: 720px) { .grid { grid-template-columns: 1fr; } }
.quad { background: var(--card); border: 1px solid var(--line);
        border-radius: 6px; padding: 14px 16px; }
.quad h3 { margin: 0 0 8px; font-size: 13px; text-transform: uppercase;
           letter-spacing: 0.04em; color: var(--muted); }
.quad.act { border-color: var(--accent); }
.quad ul { margin: 0; padding-left: 18px; }
.quad li { margin: 3px 0; font-size: 13.5px; }
.quad .empty { color: var(--muted); font-style: italic; font-size: 13.5px; }
a { color: var(--accent); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.9em; }
footer { margin-top: 56px; padding-top: 18px; border-top: 1px solid var(--line);
         color: var(--muted); font-size: 13px; }
"""


def _e(value: Any) -> str:
    return html.escape(str(value if value is not None else ""))


def _num(value: Any, places: int = 2, dash: str = "—") -> str:
    try:
        return f"{float(value):.{places}f}"
    except (TypeError, ValueError):
        return dash


def build_html(
    rows: Sequence[dict[str, Any]],
    shortlist: Sequence[dict[str, Any]],
    run_id: str,
    stats: dict[str, Any],
) -> str:
    parts: list[str] = []
    add = parts.append

    add(f"<title>IPAVentures Horizon Scan</title>")
    add(f"<style>{_CSS}</style>")
    add('<div class="wrap">')
    add("<h1>IPAVentures horizon scan</h1>")
    add(
        f'<p class="meta">Run <code>{_e(run_id)}</code> · '
        f'{stats["documents"]:,} documents · {stats["topics"]} topics · '
        f'{stats["slices"]} time slices ({_e(stats["span"])}) · '
        f'embedding backend <code>{_e(stats["backend"])}</code> · '
        f'generated {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC</p>'
    )
    add(
        '<div class="callout"><strong>These are candidates, not conclusions.</strong> '
        "The ranking combines an emergence score, strategic fit and asset leverage. "
        "Those weights are a considered judgement that has not yet been validated "
        "against a known past opportunity. The opportunity index is relative and "
        "unitless — it is <strong>not</strong> a market size and cannot be converted "
        "into one. Read the evidence cards before believing any row.</div>"
    )

    # --- shortlist --------------------------------------------------------
    add("<h2>Ranked shortlist</h2>")
    add('<p class="sub">Ordered by the composite of emergence, strategic fit and '
        "asset leverage.</p>")
    add('<div class="scroll"><table><thead><tr>'
        "<th>#</th><th>Topic</th><th>Horizon</th><th>Signal</th>"
        '<th class="num">Emergence</th><th class="num">Fit</th>'
        '<th class="num">Leverage</th><th class="num">Index</th>'
        "<th>Closest objective</th></tr></thead><tbody>")
    for row in shortlist:
        horizon = str(row.get("horizon") or "")
        signal = str(row.get("signal_class") or "")
        index = "—" if row.get("index_suppressed") else _num(row.get("opportunity_index"))
        add(
            f'<tr><td class="num">{row["rank"]}</td>'
            f'<td><strong>{_e(row.get("label") or row["topic_id"])}</strong></td>'
            f'<td><span class="tag {_e(horizon)}" title="{_e(HORIZON_BLURB.get(horizon, ""))}">'
            f'{_e(horizon)}</span></td>'
            f'<td><span class="tag {_e(signal)}">{_e(signal)}</span></td>'
            f'<td class="num">{_num(row.get("emergence_score"))}</td>'
            f'<td class="num">{_num(row.get("strategic_fit"))}</td>'
            f'<td class="num">{_num(row.get("asset_leverage"))}</td>'
            f'<td class="num">{index}</td>'
            f'<td>{_e((row.get("best_objective") or "—")[:44])}</td></tr>'
        )
    add("</tbody></table></div>")

    # --- 2x2 --------------------------------------------------------------
    add("<h2>Strategic fit × asset leverage</h2>")
    add('<p class="sub">The view that separates an interesting trend from a viable '
        "venture. Split at the median of each axis.</p>")
    buckets: dict[str, list[str]] = {}
    for row in rows:
        buckets.setdefault(str(row.get("fit_quadrant") or "watch"), []).append(
            str(row.get("label") or row["topic_id"])
        )
    order = [
        ("act", "Act — high fit, high leverage", True),
        ("on-strategy, no right-to-play", "On strategy, no right to play", False),
        ("capability looking for a problem", "Capability looking for a problem", False),
        ("watch", "Watch — low fit, low leverage", False),
    ]
    add('<div class="grid">')
    for key, heading, highlight in order:
        items = buckets.get(key, [])
        add(f'<div class="quad{" act" if highlight else ""}"><h3>{_e(heading)}</h3>')
        if items:
            add("<ul>" + "".join(f"<li>{_e(i[:60])}</li>" for i in items[:10]) + "</ul>")
            if len(items) > 10:
                add(f'<p class="empty">+{len(items) - 10} more</p>')
        else:
            add('<p class="empty">(none)</p>')
        add("</div>")
    add("</div>")

    # --- distribution -----------------------------------------------------
    add("<h2>Distribution</h2>")
    add('<div class="scroll"><table><thead><tr><th>Band</th>'
        '<th class="num">Topics</th><th>Meaning</th></tr></thead><tbody>')
    for horizon in ("H1", "H2", "H3"):
        count = sum(1 for r in rows if r.get("horizon") == horizon)
        add(f'<tr><td><span class="tag {horizon}">{horizon}</span></td>'
            f'<td class="num">{count}</td><td>{_e(HORIZON_BLURB[horizon])}</td></tr>')
    for signal, meaning in (
        ("weak", "Low volume, high growth — the horizon-scanning target"),
        ("strong", "High volume, high growth — already visible to everyone"),
        ("latent", "High volume, low growth — established, not moving"),
        ("noise", "Low volume, low growth"),
    ):
        count = sum(1 for r in rows if r.get("signal_class") == signal)
        add(f'<tr><td><span class="tag {signal}">{signal}</span></td>'
            f'<td class="num">{count}</td><td>{_e(meaning)}</td></tr>')
    add("</tbody></table></div>")

    add("<h2>What happens next</h2>")
    add(
        '<p class="sub">The pipeline surfaces candidates; judgement does the rest.</p>'
        "<ol>"
        "<li><strong>Discard the artefacts.</strong> Open the evidence cards. Any topic "
        "whose documents are not a coherent theme is a clustering artefact. Expect some.</li>"
        "<li><strong>Run the sense-making session</strong> over the survivors — Seven "
        "Questions each, with Doblin Ten Types to widen the framing past “new product”. "
        "Both prompts are on every card.</li>"
        "<li><strong>Validate the weights.</strong> Pick an opportunity IP Australia "
        "already pursued and check whether this pipeline would have surfaced it. Until "
        "then the ranking is a hypothesis about ranking.</li>"
        "</ol>"
    )

    add(
        "<footer>Evidence cards and the full topic table are in "
        f"<code>data/outputs/{_e(run_id)}/</code>. Method: <code>docs/method.md</code>. "
        "Scan frame: <code>data/strategy/scan_frame.yaml</code> — the scan cannot find "
        "what the frame does not ask for.</footer>"
    )
    add("</div>")
    return "\n".join(parts)


def run(config: dict[str, Any], run_id: str) -> Path:
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    try:
        rows = db.fetch_ranked_topics(conn, run_id)
        if not rows:
            raise SystemExit(
                f"No ranked topics for run_id={run_id!r}. Run Stage 5 first."
            )
        documents = db.fetch_documents(conn)
        slices = sorted({d["time_slice"] for d in documents if d.get("time_slice")})
        stats = {
            "documents": len(documents),
            "topics": len(rows),
            "slices": len(slices),
            "span": f"{slices[0]}–{slices[-1]}" if slices else "—",
            "backend": str(get(config, "embeddings", "backend", default="hashing")),
        }
    finally:
        conn.close()

    shortlist_size = int(get(config, "synthesis", "shortlist_size", default=15))
    docs_dir = REPO_ROOT / "docs"
    docs_dir.mkdir(parents=True, exist_ok=True)

    index = docs_dir / "index.html"
    index.write_text(
        build_html(rows, rows[:shortlist_size], run_id, stats), encoding="utf-8"
    )
    # GitHub Pages runs Jekyll by default, which skips files and directories
    # beginning with an underscore. Nothing here needs Jekyll.
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")

    (docs_dir / "latest.json").write_text(
        json.dumps({"run_id": run_id, **stats}, indent=2), encoding="utf-8"
    )
    logger.info("Wrote %s", index)
    return index


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the GitHub Pages report.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(message)s")
    path = run(load_config(args.config), args.run_id)
    print(f"Wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
