"""
src/dashboard.py — build the interactive point-cloud explorer.

Renders docs/dashboard.html: every collected document laid out as a 2D map,
coloured by topic, horizon, signal class, source or STEEPV category, with
filters, search and a details panel wired to the same evidence this run's
report and notebook already carry. Self-contained, like src/report.py — one
HTML file with the data and every line of JS inlined, no CDN, no build step,
because it is served from GitHub Pages and a page that silently fails to
render behind a corporate proxy is worse than a plain one.

WHERE THE POINTS COME FROM. Stage 2 embeds every document to cluster it, but
only persists the vectors when the backend is cacheable (`bge`; `hashing`,
the default, is cheap enough that persisting it would cost more in database
writes than it saves — see src/embeddings.py). So this module re-embeds the
corpus with the same embedder and the same cache the stages use, rather than
reading stored vectors that usually are not there. For `hashing` this is a
tokenise-and-hash pass, milliseconds for a whole corpus; for `bge` it reads
back the vectors Stage 2 already cached. Topic membership itself — which
this module does not recompute — comes straight from `topic_documents`.

WHAT THE MAP DOES NOT MEAN. UMAP and PCA both produce axes with no inherent
meaning: proximity is evidence of similar language, the axes themselves are
not a dimension of anything. The same caution this project applies to the
opportunity index applies here, and the page says so.

Run:
    python -m src.dashboard --run-id 2026-08-31
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np

from src import db
from src.collectors.base import document_text
from src.config import REPO_ROOT, get, load_config, resolve_path
from src.embeddings import build_embedder, encode_with_cache

logger = logging.getLogger(__name__)

_GITHUB_URL_RE = re.compile(r"https://github\.com/[^\s)]+")
_DEFAULT_REPO_URL = "https://github.com/Thomas-Amann-IPAustralia/BigThink"


def _repo_url(config: dict[str, Any]) -> str:
    """Pull the repo URL out of the configured user-agent rather than adding
    a second place to name it."""
    user_agent = str(get(config, "pipeline", "user_agent", default="") or "")
    match = _GITHUB_URL_RE.search(user_agent)
    return match.group(0).rstrip(")") if match else _DEFAULT_REPO_URL


def _round(value: Any, places: int = 4) -> float | None:
    try:
        return round(float(value), places)
    except (TypeError, ValueError):
        return None


# ---------------------------------------------------------------------------
# Projection
# ---------------------------------------------------------------------------


def _pca_2d(vectors: np.ndarray) -> np.ndarray:
    """Top-2 principal components via SVD. Numpy-only, no extra dependency.

    Used when umap-learn is not installed. UMAP separates topic clusters far
    better on this kind of high-dimensional sparse text vector; PCA still
    produces a real, honest map, just a flatter one — the same
    graceful-degradation shape src/embeddings.py uses for the bge backend.
    """
    centered = vectors - vectors.mean(axis=0, keepdims=True)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    return centered @ vt[:2].T


def project_2d(vectors: np.ndarray, config: dict[str, Any]) -> tuple[np.ndarray, str]:
    """Reduce *vectors* to 2D. Returns (coords, method actually used)."""
    projection = get(config, "dashboard", "projection", default={}) or {}
    method = str(projection.get("method", "umap"))
    n = vectors.shape[0]

    if method == "umap" and n >= 4:
        try:
            import umap  # noqa: PLC0415 - optional dependency, imported lazily

            reducer = umap.UMAP(
                n_neighbors=max(2, min(int(projection.get("n_neighbors", 15)), n - 1)),
                min_dist=float(projection.get("min_dist", 0.1)),
                metric=str(projection.get("metric", "cosine")),
                random_state=int(projection.get("random_state", 42)),
                n_components=2,
            )
            coords = reducer.fit_transform(np.asarray(vectors, dtype=np.float64))
            return np.asarray(coords, dtype=np.float64), "umap"
        except ImportError:
            logger.warning(
                "umap-learn not installed; falling back to a PCA projection. "
                "pip install umap-learn (or requirements.txt) for the intended point cloud."
            )

    return _pca_2d(np.asarray(vectors, dtype=np.float64)), "pca"


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------


def _select_points(
    documents: list[dict[str, Any]],
    assigned: set[str],
    max_points: int,
    run_id: str,
) -> list[int]:
    """Indices of documents to plot, downsampled deterministically if needed.

    Every topic-assigned document is kept first — that is the signal worth
    exploring — and the budget left over, if any, is filled with a random
    sample of the rest (mostly GDELT, which forms no topics; see
    emergence.topics.forming_sources). Seeded from run_id so a re-render of
    the same run reproduces the same sample rather than jittering pointlessly.
    """
    n = len(documents)
    if n <= max_points:
        return list(range(n))

    seed = int(hashlib.sha256(run_id.encode("utf-8")).hexdigest()[:8], 16)
    rng = np.random.default_rng(seed)

    assigned_idx = [i for i, d in enumerate(documents) if d["doc_id"] in assigned]
    other_idx = [i for i, d in enumerate(documents) if d["doc_id"] not in assigned]

    if len(assigned_idx) >= max_points:
        keep = rng.choice(np.array(assigned_idx), size=max_points, replace=False)
    else:
        budget = max_points - len(assigned_idx)
        extra = (
            rng.choice(np.array(other_idx), size=min(budget, len(other_idx)), replace=False)
            if other_idx else np.array([], dtype=int)
        )
        keep = np.concatenate([np.array(assigned_idx, dtype=int), extra])
    return sorted(int(i) for i in keep)


def build_dashboard(config: dict[str, Any], run_id: str) -> dict[str, Any]:
    """Assemble the JSON payload the dashboard page embeds."""
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    try:
        documents = db.fetch_documents(conn)
        if not documents:
            raise SystemExit(f"No documents for run_id={run_id!r}. Run the pipeline first.")
        total_documents = len(documents)

        ranked_topics = db.fetch_ranked_topics(conn, run_id)
        if not ranked_topics:
            raise SystemExit(f"No ranked topics for run_id={run_id!r}. Run Stage 5 first.")

        membership = db.fetch_run_topic_documents(conn, run_id)

        # Same embedding recipe as Stage 2 (src/stage2_emergence.py): fit IDF
        # over the whole corpus, then encode with the cache. Deterministic —
        # a re-render of the same corpus under the same backend reproduces
        # the same vectors.
        texts = [document_text(d) for d in documents]
        embedder = build_embedder(config)
        embedder.fit(texts)
        vectors = encode_with_cache(
            embedder, texts, conn,
            enabled=bool(get(config, "embeddings", "cache_vectors", default=True)),
        )
    finally:
        conn.close()

    doc_topic: dict[str, tuple[str, float | None]] = {
        m["doc_id"]: (m["topic_id"], m.get("similarity")) for m in membership
    }

    max_points = int(get(config, "dashboard", "max_points", default=25000))
    keep = _select_points(documents, set(doc_topic), max_points, run_id)
    if len(keep) < len(documents):
        logger.info(
            "Dashboard: plotting %d of %d documents (dashboard.max_points=%d)",
            len(keep), len(documents), max_points,
        )
    documents = [documents[i] for i in keep]
    vectors = vectors[keep]

    coords, projection_method = project_2d(vectors, config)

    repo_url = _repo_url(config)
    shortlist_size = int(get(config, "synthesis", "shortlist_size", default=15))
    topic_index = {t["topic_id"]: i for i, t in enumerate(ranked_topics)}

    topics_out = []
    for t in ranked_topics:
        terms = [str(term) for term, _weight in (t.get("terms") or [])][:5]
        evidence_url = None
        rank = t.get("rank")
        if rank and int(rank) <= shortlist_size:
            evidence_url = (
                f"{repo_url}/blob/main/data/outputs/{run_id}/evidence/"
                f"{int(rank):02d}_{t['topic_id']}.md"
            )
        topics_out.append({
            "id": t["topic_id"],
            "label": t.get("label") or t["topic_id"],
            "terms": terms,
            "horizon": t.get("horizon"),
            "signal_class": t.get("signal_class"),
            "rank": rank,
            "document_count": t.get("document_count"),
            "emergence_score": _round(t.get("emergence_score")),
            "strategic_fit": _round(t.get("strategic_fit")),
            "asset_leverage": _round(t.get("asset_leverage")),
            "opportunity_index": (
                None if t.get("index_suppressed") else _round(t.get("opportunity_index"))
            ),
            "index_suppressed": bool(t.get("index_suppressed")),
            "composite_rank_score": _round(t.get("composite_rank_score")),
            "best_objective": t.get("best_objective"),
            "best_asset": t.get("best_asset"),
            "critical_tech": t.get("critical_tech"),
            "fit_quadrant": t.get("fit_quadrant") or "watch",
            "cagr": _round(t.get("cagr"), 4),
            "maturity": _round(t.get("maturity"), 3),
            "first_slice": t.get("first_slice"),
            "last_slice": t.get("last_slice"),
            "evidence_url": evidence_url,
        })

    sources = sorted({d["source"] for d in documents if d.get("source")})
    source_index = {s: i for i, s in enumerate(sources)}
    steepv_cats = sorted({d["steepv"] for d in documents if d.get("steepv")})
    steepv_index = {s: i for i, s in enumerate(steepv_cats)}

    xs, ys = [], []
    topic_col, sim_col, source_col, steepv_col = [], [], [], []
    year_col, citation_col, title_col, url_col, venue_col = [], [], [], [], []

    for doc, (x, y) in zip(documents, coords):
        xs.append(round(float(x), 4))
        ys.append(round(float(y), 4))
        tid, sim = doc_topic.get(doc["doc_id"], (None, None))
        topic_col.append(topic_index.get(tid, -1))
        sim_col.append(round(float(sim), 3) if sim is not None else None)
        source_col.append(source_index.get(doc.get("source"), -1))
        steepv_col.append(steepv_index.get(doc.get("steepv"), -1))
        year_col.append(doc.get("year"))
        citation_col.append(int(doc.get("citation_count") or 0))
        title_col.append((doc.get("title") or "(untitled)").strip()[:220])
        raw_url = str(doc.get("url") or "")
        url_col.append(raw_url if raw_url.startswith(("http://", "https://")) else "")
        venue_col.append((doc.get("venue") or "").strip()[:120])

    years = [y for y in year_col if y]

    return {
        "run_id": run_id,
        "generated_at": datetime.now(timezone.utc).isoformat(timespec="minutes"),
        "repo_url": repo_url,
        "backend": embedder.name,
        "projection_method": projection_method,
        "shortlist_size": shortlist_size,
        "documents_total": total_documents,
        "documents_plotted": len(documents),
        "topics_total": len(ranked_topics),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "sources": sources,
        "steepv": steepv_cats,
        "topics": topics_out,
        "points": {
            "x": xs, "y": ys,
            "topic": topic_col, "similarity": sim_col,
            "source": source_col, "steepv": steepv_col,
            "year": year_col, "citation": citation_col,
            "title": title_col, "url": url_col, "venue": venue_col,
        },
    }


# ---------------------------------------------------------------------------
# HTML rendering
# ---------------------------------------------------------------------------

_CSS = """
:root {
  --bg: #fbfbfa; --fg: #16150f; --muted: #56544c; --line: #e2e0d8;
  --card: #ffffff; --accent: #1c4f8f; --canvas-bg: #f2f1ea;
  --h1: #7a5c00; --h2: #1c6b52; --h3: #7a2d6b;
  --weak: #7a2d6b; --strong: #1c4f8f; --latent: #56544c; --noise: #8a8880;
  --unassigned: #a3a196;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #14150f; --fg: #f2f1ea; --muted: #a3a196; --line: #2f3128;
    --card: #1c1e17; --accent: #86b3ec; --canvas-bg: #10110c;
    --h1: #e0bc55; --h2: #6fc9a6; --h3: #d78fc4;
    --weak: #d78fc4; --strong: #86b3ec; --latent: #a3a196; --noise: #75736b;
    --unassigned: #55544c;
  }
}
* { box-sizing: border-box; }
html, body { height: 100%; }
body {
  margin: 0; background: var(--bg); color: var(--fg);
  font: 14px/1.5 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
  display: flex; flex-direction: column;
}
a { color: var(--accent); }
code { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.92em; }
button, select, input { font: inherit; color: inherit; }
header.top {
  padding: 14px 20px; border-bottom: 1px solid var(--line);
  display: flex; align-items: baseline; gap: 16px; flex-wrap: wrap;
}
header.top h1 { font-size: 18px; margin: 0; letter-spacing: -0.01em; }
header.top .meta { color: var(--muted); font-size: 12.5px; }
header.top .nav { margin-left: auto; font-size: 13px; display: flex; gap: 14px; }
.callout {
  margin: 10px 20px 0; padding: 10px 14px; font-size: 12.5px; color: var(--muted);
  background: var(--card); border: 1px solid var(--line); border-left: 3px solid var(--accent);
  border-radius: 6px;
}
.callout strong { color: var(--fg); }
.app { flex: 1; display: flex; min-height: 0; }
aside.controls {
  width: 250px; flex: 0 0 250px; overflow-y: auto; padding: 14px 16px;
  border-right: 1px solid var(--line); font-size: 13px;
}
aside.controls h2 {
  font-size: 11px; text-transform: uppercase; letter-spacing: 0.06em;
  color: var(--muted); margin: 18px 0 8px;
}
aside.controls h2:first-child { margin-top: 0; }
.field { margin-bottom: 8px; }
.field label { display: block; margin-bottom: 3px; color: var(--muted); font-size: 12px; }
select, input[type="text"], input[type="number"] {
  width: 100%; padding: 5px 7px; background: var(--card); color: var(--fg);
  border: 1px solid var(--line); border-radius: 5px;
}
input[type="range"] { width: 100%; }
.chk { display: flex; align-items: center; gap: 6px; margin: 3px 0; cursor: pointer; }
.chk input { accent-color: var(--accent); }
.chk .swatch { width: 10px; height: 10px; border-radius: 3px; flex: 0 0 auto; }
.chk .count { margin-left: auto; color: var(--muted); font-size: 11px; font-variant-numeric: tabular-nums; }
.legend-list { max-height: 220px; overflow-y: auto; }
button.btn {
  width: 100%; padding: 6px 8px; margin-top: 4px; background: var(--card);
  border: 1px solid var(--line); border-radius: 5px; cursor: pointer; font-size: 12.5px;
}
button.btn:hover { border-color: var(--accent); color: var(--accent); }
.years { display: flex; align-items: center; gap: 6px; font-size: 12px; color: var(--muted); }
.years input[type="number"] { width: 64px; }
main.canvas-wrap { flex: 1; position: relative; min-width: 0; background: var(--canvas-bg); }
canvas { display: block; width: 100%; height: 100%; cursor: grab; touch-action: none; }
canvas.dragging { cursor: grabbing; }
.stats-badge {
  position: absolute; left: 12px; bottom: 12px; background: var(--card);
  border: 1px solid var(--line); border-radius: 6px; padding: 6px 10px;
  font-size: 11.5px; color: var(--muted); pointer-events: none;
}
.stats-badge strong { color: var(--fg); }
.axis-note {
  position: absolute; right: 12px; bottom: 12px; max-width: 260px; text-align: right;
  font-size: 11px; color: var(--muted); background: var(--card); border: 1px solid var(--line);
  border-radius: 6px; padding: 6px 10px; pointer-events: none;
}
.tooltip {
  position: absolute; pointer-events: none; max-width: 280px; background: var(--card);
  border: 1px solid var(--line); border-radius: 6px; padding: 8px 10px; font-size: 12px;
  box-shadow: 0 4px 14px rgba(0,0,0,0.18); display: none; z-index: 5;
}
.tooltip .t-title { font-weight: 600; margin-bottom: 3px; }
.tooltip .t-meta { color: var(--muted); font-size: 11px; }
aside.details {
  width: 320px; flex: 0 0 320px; overflow-y: auto; padding: 16px 18px;
  border-left: 1px solid var(--line); font-size: 13px;
}
aside.details.empty { display: flex; align-items: center; justify-content: center; color: var(--muted); }
aside.details h3 { font-size: 15px; margin: 0 0 4px; }
aside.details .close { float: right; background: none; border: none; color: var(--muted);
  cursor: pointer; font-size: 16px; line-height: 1; }
aside.details dl { margin: 10px 0; }
aside.details dt { color: var(--muted); font-size: 11px; text-transform: uppercase;
  letter-spacing: 0.04em; margin-top: 8px; }
aside.details dd { margin: 2px 0 0; }
.tag { font-size: 11px; font-weight: 600; padding: 2px 7px; border-radius: 10px;
       border: 1px solid currentColor; white-space: nowrap; }
.H1 { color: var(--h1); } .H2 { color: var(--h2); } .H3 { color: var(--h3); }
.weak { color: var(--weak); } .strong { color: var(--strong); }
.latent { color: var(--latent); } .noise { color: var(--noise); }
@media (max-width: 900px) {
  .app { flex-direction: column; }
  aside.controls, aside.details { width: auto; flex: none; max-height: 40vh; border: none;
    border-bottom: 1px solid var(--line); }
  main.canvas-wrap { min-height: 50vh; }
}
"""

_JS = r"""
(function () {
  "use strict";
  var D = window.__DASHBOARD_DATA__;
  var P = D.points;
  var N = P.x.length;

  var HORIZON_COLOR = { H1: "#c9971a", H2: "#1c9a72", H3: "#b04fa0", "": "#8a8880" };
  var SIGNAL_COLOR = { weak: "#b04fa0", strong: "#3d7fc9", latent: "#8a8880", noise: "#5f5d54" };
  var UNASSIGNED = "#8a888066";

  function goldenColor(i, alpha) {
    var hue = (i * 137.508) % 360;
    return "hsla(" + hue.toFixed(1) + ",65%,52%," + (alpha == null ? 1 : alpha) + ")";
  }

  var topicColors = D.topics.map(function (_, i) { return goldenColor(i); });
  var sourceColors = D.sources.map(function (_, i) { return goldenColor(i * 2.3); });
  var steepvColors = D.steepv.map(function (_, i) { return goldenColor(i * 3.7); });

  // --- DOM ------------------------------------------------------------
  var canvas = document.getElementById("cv");
  var ctx = canvas.getContext("2d");
  var tooltip = document.getElementById("tooltip");
  var details = document.getElementById("details");
  var statsBadge = document.getElementById("statsBadge");
  var legendList = document.getElementById("legendList");
  var colorBySel = document.getElementById("colorBy");
  var topicSearch = document.getElementById("topicSearch");
  var topicOptions = document.getElementById("topicOptions");
  var searchInput = document.getElementById("searchInput");
  var sourceFilters = document.getElementById("sourceFilters");
  var horizonFilters = document.getElementById("horizonFilters");
  var yearMinInput = document.getElementById("yearMin");
  var yearMaxInput = document.getElementById("yearMax");
  var shortlistOnly = document.getElementById("shortlistOnly");
  var showUnassigned = document.getElementById("showUnassigned");
  var pointSizeInput = document.getElementById("pointSize");
  var resetFiltersBtn = document.getElementById("resetFilters");
  var resetViewBtn = document.getElementById("resetView");

  // --- state ------------------------------------------------------------
  var state = {
    colorBy: "topic",
    sources: new Set(D.sources),
    horizons: new Set(["H1", "H2", "H3", ""]),
    yearMin: D.year_min, yearMax: D.year_max,
    search: "",
    shortlistOnly: false,
    showUnassigned: true,
    topicFocus: -1,
    pointSize: 2.2,
  };

  var visible = new Uint8Array(N);
  var visibleIdx = [];
  var colorGroups = null; // color string -> Int32Array

  // --- camera -------------------------------------------------------
  var cam = { scale: 1, tx: 0, ty: 0 };
  var extent = computeExtent();

  function computeExtent() {
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    for (var i = 0; i < N; i++) {
      var x = P.x[i], y = P.y[i];
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
    if (!isFinite(minX)) { minX = -1; maxX = 1; minY = -1; maxY = 1; }
    return { minX: minX, maxX: maxX, minY: minY, maxY: maxY };
  }

  function fitCamera(idxList) {
    var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
    var list = idxList && idxList.length ? idxList : null;
    var count = list ? list.length : N;
    for (var k = 0; k < count; k++) {
      var i = list ? list[k] : k;
      var x = P.x[i], y = P.y[i];
      if (x < minX) minX = x; if (x > maxX) maxX = x;
      if (y < minY) minY = y; if (y > maxY) maxY = y;
    }
    if (!isFinite(minX)) { minX = extent.minX; maxX = extent.maxX; minY = extent.minY; maxY = extent.maxY; }
    var w = Math.max(maxX - minX, 1e-6), h = Math.max(maxY - minY, 1e-6);
    var pad = 1.15;
    var rect = canvas.getBoundingClientRect();
    var sx = rect.width / (w * pad), sy = rect.height / (h * pad);
    cam.scale = Math.max(0.02, Math.min(sx, sy));
    var cx = (minX + maxX) / 2, cy = (minY + maxY) / 2;
    cam.tx = rect.width / 2 - cx * cam.scale;
    cam.ty = rect.height / 2 + cy * cam.scale; // y flipped on screen
  }

  function screenToData(sx, sy) {
    return [(sx - cam.tx) / cam.scale, -(sy - cam.ty) / cam.scale];
  }

  // --- sizing ---------------------------------------------------------
  function resize() {
    var rect = canvas.parentElement.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    canvas.width = Math.max(1, Math.round(rect.width * dpr));
    canvas.height = Math.max(1, Math.round(rect.height * dpr));
    canvas.style.width = rect.width + "px";
    canvas.style.height = rect.height + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    draw();
  }

  // --- filtering --------------------------------------------------------
  function recomputeVisible() {
    var q = state.search.trim().toLowerCase();
    var shortlistTopicIdx = null;
    if (state.shortlistOnly) {
      shortlistTopicIdx = new Set();
      D.topics.forEach(function (t, i) { if (t.rank && t.rank <= D.shortlist_size) shortlistTopicIdx.add(i); });
    }
    visibleIdx = [];
    for (var i = 0; i < N; i++) {
      var ti = P.topic[i];
      if (ti === -1) {
        if (!state.showUnassigned || state.shortlistOnly) { visible[i] = 0; continue; }
      } else if (shortlistTopicIdx && !shortlistTopicIdx.has(ti)) {
        visible[i] = 0; continue;
      }
      var src = D.sources[P.source[i]];
      if (!state.sources.has(src)) { visible[i] = 0; continue; }
      var hz = ti === -1 ? "" : (D.topics[ti].horizon || "");
      if (!state.horizons.has(hz)) { visible[i] = 0; continue; }
      var yr = P.year[i];
      if (state.yearMin != null && yr != null && yr < state.yearMin) { visible[i] = 0; continue; }
      if (state.yearMax != null && yr != null && yr > state.yearMax) { visible[i] = 0; continue; }
      if (q && P.title[i].toLowerCase().indexOf(q) === -1) { visible[i] = 0; continue; }
      visible[i] = 1;
      visibleIdx.push(i);
    }
    regroupColors();
    updateStats();
    draw();
  }

  function colorFor(i) {
    var ti = P.topic[i];
    switch (state.colorBy) {
      case "topic": return ti === -1 ? UNASSIGNED : topicColors[ti];
      case "horizon": return HORIZON_COLOR[ti === -1 ? "" : (D.topics[ti].horizon || "")] || UNASSIGNED;
      case "signal": return SIGNAL_COLOR[ti === -1 ? "" : (D.topics[ti].signal_class || "")] || UNASSIGNED;
      case "source": return sourceColors[P.source[i]] || UNASSIGNED;
      case "steepv": return P.steepv[i] === -1 ? UNASSIGNED : steepvColors[P.steepv[i]];
    }
    return UNASSIGNED;
  }

  function regroupColors() {
    var groups = {};
    for (var k = 0; k < visibleIdx.length; k++) {
      var i = visibleIdx[k];
      var c = colorFor(i);
      (groups[c] || (groups[c] = [])).push(i);
    }
    colorGroups = groups;
    buildLegend();
  }

  function legendCategories() {
    if (state.colorBy === "topic") {
      return D.topics.map(function (t, i) { return { key: i, label: t.label, color: topicColors[i] }; })
        .concat([{ key: -1, label: "(unassigned)", color: UNASSIGNED }]);
    }
    if (state.colorBy === "horizon") {
      return [
        { key: "H1", label: "H1 — established", color: HORIZON_COLOR.H1 },
        { key: "H2", label: "H2 — transitional", color: HORIZON_COLOR.H2 },
        { key: "H3", label: "H3 — paradigm shift", color: HORIZON_COLOR.H3 },
        { key: "", label: "(unassigned)", color: HORIZON_COLOR[""] },
      ];
    }
    if (state.colorBy === "signal") {
      return [
        { key: "weak", label: "weak — the horizon-scanning target", color: SIGNAL_COLOR.weak },
        { key: "strong", label: "strong — already visible", color: SIGNAL_COLOR.strong },
        { key: "latent", label: "latent — established, static", color: SIGNAL_COLOR.latent },
        { key: "noise", label: "noise", color: SIGNAL_COLOR.noise },
        { key: "", label: "(unassigned)", color: UNASSIGNED },
      ];
    }
    if (state.colorBy === "source") {
      return D.sources.map(function (s, i) { return { key: s, label: s, color: sourceColors[i] }; });
    }
    return D.steepv.map(function (s, i) { return { key: s, label: s, color: steepvColors[i] }; });
  }

  function buildLegend() {
    var cats = legendCategories();
    var counts = {};
    for (var k = 0; k < visibleIdx.length; k++) {
      var i = visibleIdx[k];
      var key;
      if (state.colorBy === "topic") key = P.topic[i];
      else if (state.colorBy === "horizon") key = P.topic[i] === -1 ? "" : (D.topics[P.topic[i]].horizon || "");
      else if (state.colorBy === "signal") key = P.topic[i] === -1 ? "" : (D.topics[P.topic[i]].signal_class || "");
      else if (state.colorBy === "source") key = D.sources[P.source[i]];
      else key = P.steepv[i] === -1 ? null : D.steepv[P.steepv[i]];
      counts[key] = (counts[key] || 0) + 1;
    }
    legendList.innerHTML = "";
    cats.sort(function (a, b) { return (counts[b.key] || 0) - (counts[a.key] || 0); });
    cats.slice(0, 60).forEach(function (c) {
      var clickable = state.colorBy === "topic" && c.key !== -1;
      var row = document.createElement("div");
      row.className = "chk";
      row.style.cursor = clickable ? "pointer" : "default";
      var sw = document.createElement("span");
      sw.className = "swatch";
      sw.style.background = c.color;
      var label = document.createElement("span");
      label.textContent = c.label;
      label.style.overflow = "hidden";
      label.style.textOverflow = "ellipsis";
      label.style.whiteSpace = "nowrap";
      var count = document.createElement("span");
      count.className = "count";
      count.textContent = (counts[c.key] || 0).toLocaleString();
      row.appendChild(sw); row.appendChild(label); row.appendChild(count);
      if (clickable) {
        row.addEventListener("click", function () { focusTopic(c.key); });
      }
      legendList.appendChild(row);
    });
  }

  function updateStats() {
    statsBadge.innerHTML = "<strong>" + visibleIdx.length.toLocaleString() + "</strong> of " +
      N.toLocaleString() + " documents shown &middot; " + D.topics_total + " topics &middot; run " +
      D.run_id;
  }

  // --- drawing ------------------------------------------------------
  var raf = null;
  function draw() {
    if (raf) return;
    raf = requestAnimationFrame(function () {
      raf = null;
      var rect = canvas.getBoundingClientRect();
      ctx.clearRect(0, 0, rect.width, rect.height);
      var r = state.pointSize;
      var focused = state.topicFocus !== -1;
      for (var color in colorGroups) {
        ctx.fillStyle = color;
        var idxs = colorGroups[color];
        for (var k = 0; k < idxs.length; k++) {
          var i = idxs[k];
          var sx = P.x[i] * cam.scale + cam.tx;
          var sy = -P.y[i] * cam.scale + cam.ty;
          if (sx < -4 || sy < -4 || sx > rect.width + 4 || sy > rect.height + 4) continue;
          ctx.globalAlpha = focused && P.topic[i] !== state.topicFocus ? 0.12 : 0.85;
          ctx.fillRect(sx - r, sy - r, r * 2, r * 2);
        }
      }
      ctx.globalAlpha = 1;
      if (selectedIdx !== -1 && visible[selectedIdx]) {
        var hx = P.x[selectedIdx] * cam.scale + cam.tx;
        var hy = -P.y[selectedIdx] * cam.scale + cam.ty;
        ctx.strokeStyle = "#1c4f8f"; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(hx, hy, r + 4, 0, Math.PI * 2); ctx.stroke();
      }
    });
  }

  // --- spatial index for hover/click ------------------------------------
  var GRID = 48;
  var grid = null;
  function buildGrid() {
    grid = {};
    var cw = (extent.maxX - extent.minX) / GRID || 1;
    var ch = (extent.maxY - extent.minY) / GRID || 1;
    for (var k = 0; k < visibleIdx.length; k++) {
      var i = visibleIdx[k];
      var cx = Math.floor((P.x[i] - extent.minX) / cw);
      var cy = Math.floor((P.y[i] - extent.minY) / ch);
      var key = cx + "," + cy;
      (grid[key] || (grid[key] = [])).push(i);
    }
    grid._cw = cw; grid._ch = ch;
  }

  function nearestPoint(sx, sy) {
    if (!grid) return -1;
    var d = screenToData(sx, sy);
    var cx = Math.floor((d[0] - extent.minX) / grid._cw);
    var cy = Math.floor((d[1] - extent.minY) / grid._ch);
    var best = -1, bestDist = Infinity;
    var pxThreshold = 8 / cam.scale;
    for (var dx = -1; dx <= 1; dx++) {
      for (var dy = -1; dy <= 1; dy++) {
        var cell = grid[(cx + dx) + "," + (cy + dy)];
        if (!cell) continue;
        for (var k = 0; k < cell.length; k++) {
          var i = cell[k];
          var ddx = P.x[i] - d[0], ddy = P.y[i] - d[1];
          var dist = ddx * ddx + ddy * ddy;
          if (dist < bestDist) { bestDist = dist; best = i; }
        }
      }
    }
    return bestDist <= pxThreshold * pxThreshold ? best : -1;
  }

  // --- interaction: pan / zoom -------------------------------------------
  var dragging = false, lastX = 0, lastY = 0, moved = false;
  canvas.addEventListener("pointerdown", function (e) {
    dragging = true; moved = false; lastX = e.clientX; lastY = e.clientY;
    canvas.setPointerCapture(e.pointerId);
    canvas.classList.add("dragging");
  });
  canvas.addEventListener("pointermove", function (e) {
    var rect = canvas.getBoundingClientRect();
    var sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    if (dragging) {
      var dx = e.clientX - lastX, dy = e.clientY - lastY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
      cam.tx += dx; cam.ty += dy;
      lastX = e.clientX; lastY = e.clientY;
      hideTooltip();
      draw();
      return;
    }
    var hit = nearestPoint(sx, sy);
    if (hit === -1) { hideTooltip(); return; }
    showTooltip(hit, e.clientX, e.clientY);
  });
  window.addEventListener("pointerup", function (e) {
    if (!dragging) return;
    dragging = false;
    canvas.classList.remove("dragging");
    if (!moved) {
      var rect = canvas.getBoundingClientRect();
      var hit = nearestPoint(e.clientX - rect.left, e.clientY - rect.top);
      selectPoint(hit);
    }
  });
  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    var rect = canvas.getBoundingClientRect();
    var sx = e.clientX - rect.left, sy = e.clientY - rect.top;
    var before = screenToData(sx, sy);
    var factor = Math.exp(-e.deltaY * 0.0015);
    cam.scale = Math.max(0.005, Math.min(cam.scale * factor, 400));
    var afterSx = before[0] * cam.scale + cam.tx;
    var afterSy = -before[1] * cam.scale + cam.ty;
    cam.tx += sx - afterSx; cam.ty += sy - afterSy;
    hideTooltip();
    draw();
  }, { passive: false });
  canvas.addEventListener("dblclick", function (e) {
    var rect = canvas.getBoundingClientRect();
    var hit = nearestPoint(e.clientX - rect.left, e.clientY - rect.top);
    if (hit !== -1 && P.topic[hit] !== -1) focusTopic(P.topic[hit]);
  });

  function showTooltip(i, clientX, clientY) {
    var ti = P.topic[i];
    var topicLabel = ti === -1 ? "(unassigned — no topic near enough)" : D.topics[ti].label;
    tooltip.innerHTML = "";
    var title = document.createElement("div");
    title.className = "t-title"; title.textContent = P.title[i];
    var meta1 = document.createElement("div");
    meta1.className = "t-meta";
    meta1.textContent = (D.sources[P.source[i]] || "") + (P.year[i] ? " · " + P.year[i] : "") +
      (P.venue[i] ? " · " + P.venue[i] : "");
    var meta2 = document.createElement("div");
    meta2.className = "t-meta"; meta2.textContent = "Topic: " + topicLabel;
    tooltip.appendChild(title); tooltip.appendChild(meta1); tooltip.appendChild(meta2);
    tooltip.style.display = "block";
    var wrapRect = canvas.parentElement.getBoundingClientRect();
    var left = clientX - wrapRect.left + 14, top = clientY - wrapRect.top + 14;
    if (left + 280 > wrapRect.width) left = clientX - wrapRect.left - 294;
    tooltip.style.left = left + "px"; tooltip.style.top = top + "px";
  }
  function hideTooltip() { tooltip.style.display = "none"; }

  var selectedIdx = -1;
  function selectPoint(i) {
    selectedIdx = i;
    if (i === -1) { showDetailsEmpty(); draw(); return; }
    showDetailsForPoint(i);
    draw();
  }

  function fmtScore(v) { return v == null ? "—" : v.toFixed(2); }

  function showDetailsEmpty() {
    details.classList.add("empty");
    details.innerHTML = "Click a point to see its details, or select a topic to zoom in.";
  }

  function el(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text != null) e.textContent = text;
    return e;
  }

  function showDetailsForPoint(i) {
    details.classList.remove("empty");
    details.innerHTML = "";
    var closeBtn = el("button", "close", "×");
    closeBtn.addEventListener("click", function () { selectPoint(-1); });
    details.appendChild(closeBtn);
    details.appendChild(el("h3", null, P.title[i]));
    var meta = el("div", "t-meta");
    meta.style.color = "var(--muted)";
    meta.textContent = (D.sources[P.source[i]] || "") + (P.year[i] ? " · " + P.year[i] : "") +
      (P.citation[i] ? " · " + P.citation[i] + " citations" : "");
    details.appendChild(meta);
    if (P.venue[i]) details.appendChild(el("div", "t-meta", P.venue[i]));
    if (P.url[i]) {
      var link = el("a", null, "Open source →");
      link.href = P.url[i]; link.target = "_blank"; link.rel = "noopener noreferrer";
      link.style.display = "inline-block"; link.style.marginTop = "8px";
      details.appendChild(link);
    }
    var ti = P.topic[i];
    if (ti === -1) {
      var note = el("dl");
      note.appendChild(el("dt", null, "Topic"));
      note.appendChild(el("dd", null, "Unassigned — too dissimilar from every detected topic " +
        "(mostly GDELT attention signal, which does not form topics). Still plotted, since it " +
        "is real corpus content."));
      details.appendChild(note);
      return;
    }
    if (P.similarity[i] != null) {
      var simP = el("div", "t-meta", "Similarity to topic: " + P.similarity[i].toFixed(2));
      details.appendChild(simP);
    }
    appendTopicSummary(D.topics[ti]);
  }

  function appendTopicSummary(t) {
    var dl = el("dl");
    function row(label, value) {
      dl.appendChild(el("dt", null, label));
      var dd = el("dd", null, value);
      dl.appendChild(dd);
    }
    var head = el("h3", null, t.label);
    details.appendChild(head);
    var tags = el("div");
    if (t.horizon) { var h = el("span", "tag " + t.horizon, t.horizon); tags.appendChild(h); tags.appendChild(document.createTextNode(" ")); }
    if (t.signal_class) { var s = el("span", "tag " + t.signal_class, t.signal_class); tags.appendChild(s); }
    details.appendChild(tags);
    row("Rank", t.rank ? ("#" + t.rank + " of " + D.topics_total) : "unranked");
    row("Documents", t.document_count);
    row("Emergence score", fmtScore(t.emergence_score));
    row("Strategic fit", fmtScore(t.strategic_fit));
    row("Asset leverage", fmtScore(t.asset_leverage));
    row("Opportunity index", t.index_suppressed ? "suppressed (too few documents)" : fmtScore(t.opportunity_index));
    row("2×2 placement", t.fit_quadrant);
    row("Closest objective", t.best_objective || "—");
    row("Closest asset", t.best_asset || "—");
    if (t.critical_tech) row("DISR critical technology", t.critical_tech);
    row("Span", (t.first_slice || "?") + "–" + (t.last_slice || "?"));
    row("Top terms", t.terms.join(", "));
    details.appendChild(dl);
    var btn = el("button", "btn", "Zoom to this topic");
    btn.addEventListener("click", function () { focusTopic(D.topics.indexOf(t)); });
    details.appendChild(btn);
    if (t.evidence_url) {
      var ev = el("a", null, "Read the evidence card on GitHub →");
      ev.href = t.evidence_url; ev.target = "_blank"; ev.rel = "noopener noreferrer";
      ev.style.display = "inline-block"; ev.style.marginTop = "8px";
      details.appendChild(ev);
    }
  }

  function idxsForTopic(topicIdx) {
    var out = [];
    for (var i = 0; i < N; i++) if (P.topic[i] === topicIdx) out.push(i);
    return out;
  }

  function focusTopic(topicIdx) {
    state.topicFocus = topicIdx;
    topicSearch.value = topicIdx === -1 ? "" : D.topics[topicIdx].label;
    fitCamera(topicIdx === -1 ? null : idxsForTopic(topicIdx));
    draw();
    if (topicIdx !== -1) {
      details.classList.remove("empty");
      details.innerHTML = "";
      appendTopicSummary(D.topics[topicIdx]);
    }
  }

  // --- controls -----------------------------------------------------
  colorBySel.addEventListener("change", function () { state.colorBy = colorBySel.value; regroupColors(); draw(); });

  D.sources.forEach(function (s) {
    var row = el("label", "chk");
    var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", function () {
      if (cb.checked) state.sources.add(s); else state.sources.delete(s);
      recomputeVisible(); buildGrid();
    });
    var sw = el("span", "swatch"); sw.style.background = sourceColors[D.sources.indexOf(s)];
    row.appendChild(cb); row.appendChild(sw); row.appendChild(document.createTextNode(s));
    sourceFilters.appendChild(row);
  });

  [["H1", "H1"], ["H2", "H2"], ["H3", "H3"], ["", "unassigned"]].forEach(function (pair) {
    var row = el("label", "chk");
    var cb = document.createElement("input"); cb.type = "checkbox"; cb.checked = true;
    cb.addEventListener("change", function () {
      if (cb.checked) state.horizons.add(pair[0]); else state.horizons.delete(pair[0]);
      recomputeVisible(); buildGrid();
    });
    row.appendChild(cb); row.appendChild(document.createTextNode(pair[1]));
    horizonFilters.appendChild(row);
  });

  D.topics.forEach(function (t) {
    var opt = document.createElement("option");
    opt.value = t.label;
    topicOptions.appendChild(opt);
  });
  topicSearch.addEventListener("change", function () {
    var val = topicSearch.value;
    var idx = D.topics.findIndex(function (t) { return t.label === val; });
    focusTopic(idx === -1 ? -1 : idx);
  });

  var searchDebounce = null;
  searchInput.addEventListener("input", function () {
    clearTimeout(searchDebounce);
    searchDebounce = setTimeout(function () {
      state.search = searchInput.value;
      recomputeVisible(); buildGrid();
    }, 150);
  });

  yearMinInput.value = D.year_min || ""; yearMaxInput.value = D.year_max || "";
  yearMinInput.min = D.year_min || ""; yearMinInput.max = D.year_max || "";
  yearMaxInput.min = D.year_min || ""; yearMaxInput.max = D.year_max || "";
  [yearMinInput, yearMaxInput].forEach(function (inp) {
    inp.addEventListener("change", function () {
      state.yearMin = yearMinInput.value ? parseInt(yearMinInput.value, 10) : null;
      state.yearMax = yearMaxInput.value ? parseInt(yearMaxInput.value, 10) : null;
      recomputeVisible(); buildGrid();
    });
  });

  shortlistOnly.addEventListener("change", function () {
    state.shortlistOnly = shortlistOnly.checked;
    recomputeVisible(); buildGrid();
  });
  showUnassigned.addEventListener("change", function () {
    state.showUnassigned = showUnassigned.checked;
    recomputeVisible(); buildGrid();
  });
  pointSizeInput.addEventListener("input", function () {
    state.pointSize = parseFloat(pointSizeInput.value);
    draw();
  });

  resetFiltersBtn.addEventListener("click", function () {
    state.sources = new Set(D.sources);
    state.horizons = new Set(["H1", "H2", "H3", ""]);
    state.yearMin = D.year_min; state.yearMax = D.year_max;
    state.search = ""; state.shortlistOnly = false; state.showUnassigned = true;
    state.topicFocus = -1;
    searchInput.value = ""; topicSearch.value = "";
    shortlistOnly.checked = false; showUnassigned.checked = true;
    yearMinInput.value = D.year_min || ""; yearMaxInput.value = D.year_max || "";
    sourceFilters.querySelectorAll("input").forEach(function (c) { c.checked = true; });
    horizonFilters.querySelectorAll("input").forEach(function (c) { c.checked = true; });
    details.classList.add("empty"); showDetailsEmpty();
    selectedIdx = -1;
    recomputeVisible(); buildGrid();
    fitCamera(null);
    draw();
  });
  resetViewBtn.addEventListener("click", function () {
    fitCamera(state.topicFocus !== -1 ? idxsForTopic(state.topicFocus) : null);
    draw();
  });

  // --- boot -----------------------------------------------------------
  showDetailsEmpty();
  window.addEventListener("resize", resize);
  recomputeVisible();
  buildGrid();
  resize();
  fitCamera(null);
  draw();
})();
"""


def _escape_for_script(payload: dict[str, Any]) -> str:
    """JSON-encode *payload* for embedding inside a <script> tag.

    Document titles and venues come from external APIs (Crossref, GDELT,
    arXiv) and are not trusted. A title containing a literal '</script>'
    would otherwise close the tag early and let arbitrary markup follow it
    on a page this pipeline publishes to the public internet. Escaping the
    one sequence that can do that is the standard, sufficient defence for
    JSON embedded this way.
    """
    return json.dumps(payload, separators=(",", ":")).replace("</", "<\\/")


def render_html(data: dict[str, Any]) -> str:
    stats = (
        f'{data["documents_plotted"]:,} of {data["documents_total"]:,} documents · '
        f'{data["topics_total"]} topics · projection <code>{data["projection_method"]}</code> · '
        f'embedding backend <code>{data["backend"]}</code>'
    )
    repo_url = data["repo_url"]
    run_id = data["run_id"]
    parts: list[str] = []
    add = parts.append

    add("<title>IPAVentures horizon scan — explorer</title>")
    add(f"<style>{_CSS}</style>")
    add('<header class="top">')
    add("<h1>IPAVentures horizon scan — explorer</h1>")
    add(f'<span class="meta">run <code>{run_id}</code> · {stats}</span>')
    add('<span class="nav">')
    add('<a href="index.html">&larr; ranked shortlist</a>')
    add(f'<a href="{repo_url}/blob/main/docs/method.md" target="_blank" rel="noopener noreferrer">what the numbers mean</a>')
    add(
        f'<a href="{repo_url}/tree/main/data/outputs/{run_id}" target="_blank" '
        'rel="noopener noreferrer">this run\'s outputs</a>'
    )
    add("</span></header>")
    add(
        '<div class="callout"><strong>This is a map of language, not of importance.</strong> '
        "Position comes from a 2D projection of each document's text — nearby points used "
        "similar words, distant ones did not. The axes themselves carry no unit and no "
        "direction means anything. Colour and the details panel carry the real scores; this "
        "view exists to help you find and zoom into clusters, not to read distance as a "
        "number. Candidates, not conclusions — see the ranked report for the caveats on every "
        "score.</div>"
    )
    add('<div class="app">')

    add('<aside class="controls">')
    add("<h2>Colour by</h2>")
    add(
        '<select id="colorBy">'
        '<option value="topic">Topic</option>'
        '<option value="horizon">Horizon</option>'
        '<option value="signal">Signal class</option>'
        '<option value="source">Source</option>'
        '<option value="steepv">STEEPV category</option>'
        "</select>"
    )
    add('<div id="legendList" class="legend-list"></div>')

    add("<h2>Find a topic</h2>")
    add('<div class="field"><input id="topicSearch" type="text" list="topicOptions" '
        'placeholder="Type a topic label…"><datalist id="topicOptions"></datalist></div>')

    add("<h2>Search titles</h2>")
    add('<div class="field"><input id="searchInput" type="text" placeholder="e.g. geographical indication"></div>')

    add("<h2>Year</h2>")
    add(
        '<div class="years"><input id="yearMin" type="number"> to '
        '<input id="yearMax" type="number"></div>'
    )

    add("<h2>Source</h2>")
    add('<div id="sourceFilters"></div>')

    add("<h2>Horizon</h2>")
    add('<div id="horizonFilters"></div>')

    add("<h2>Other filters</h2>")
    add('<label class="chk"><input id="shortlistOnly" type="checkbox"> Shortlisted topics only</label>')
    add('<label class="chk"><input id="showUnassigned" type="checkbox" checked> Show unassigned documents</label>')

    add("<h2>Display</h2>")
    add('<div class="field"><label>Point size</label>'
        '<input id="pointSize" type="range" min="0.8" max="6" step="0.2" value="2.2"></div>')

    add('<button id="resetFilters" class="btn">Reset filters</button>')
    add('<button id="resetView" class="btn">Reset view</button>')
    add("</aside>")

    add('<main class="canvas-wrap">')
    add('<canvas id="cv"></canvas>')
    add('<div id="tooltip" class="tooltip"></div>')
    add('<div id="statsBadge" class="stats-badge"></div>')
    add('<div class="axis-note">Drag to pan · scroll to zoom · double-click a point to zoom to '
        "its topic. Axes have no unit.</div>")
    add("</main>")

    add('<aside id="details" class="details"></aside>')
    add("</div>")

    add(f"<script>window.__DASHBOARD_DATA__ = {_escape_for_script(data)};</script>")
    add(f"<script>{_JS}</script>")
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], run_id: str) -> Path:
    docs_dir = REPO_ROOT / "docs"
    out = docs_dir / "dashboard.html"
    if not bool(get(config, "dashboard", "enabled", default=True)):
        logger.info("dashboard.enabled is false; leaving %s untouched.", out)
        return out

    data = build_dashboard(config, run_id)
    docs_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(render_html(data), encoding="utf-8")
    # GitHub Pages runs Jekyll by default, which skips files and directories
    # beginning with an underscore. Nothing here needs Jekyll.
    (docs_dir / ".nojekyll").write_text("", encoding="utf-8")
    logger.info(
        "Wrote %s (%d of %d documents, %d topics, projection=%s)",
        out, data["documents_plotted"], data["documents_total"],
        data["topics_total"], data["projection_method"],
    )
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build the interactive point-cloud dashboard.")
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
