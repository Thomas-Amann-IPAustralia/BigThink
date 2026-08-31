/* ==========================================================================
   core.js — the shared namespace every view attaches to.

   Loaded first (see _JS_ASSETS in src/dashboard.py); boot.js is loaded last.
   Everything is wrapped in one IIFE at render time, so `BT` never reaches the
   global scope and two copies of the page on one document cannot collide.

   No framework, no build step. The whole page is one file served from GitHub
   Pages, and every dependency it does not have is a dependency that cannot
   fail to load behind a proxy.
   ========================================================================== */

var BT = {
  D: window.__DASHBOARD_DATA__,
  views: {},
  bus: {}
};

BT.P = BT.D.points;
BT.N = BT.P.x.length;

/* ------------------------------------------------------------------ events */

BT.on = function (name, fn) { (BT.bus[name] || (BT.bus[name] = [])).push(fn); };
BT.emit = function (name, payload) {
  (BT.bus[name] || []).forEach(function (fn) { fn(payload); });
};

/* ---------------------------------------------------------------- elements */

function el(tag, cls, text) {
  var n = document.createElement(tag);
  if (cls) n.className = cls;
  if (text != null) n.textContent = text;
  return n;
}
BT.el = el;

/* Terse tree builder. Children may be nodes, strings, or null (skipped), so a
   conditional row is `cond ? h("tr", …) : null` rather than an if-block. */
function h(tag, attrs, children) {
  var n = document.createElement(tag);
  if (attrs) {
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k === "class") n.className = v;
      else if (k === "text") n.textContent = v;
      else if (k === "html") n.innerHTML = v;
      else if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else if (k === "style") n.setAttribute("style", v);
      else n.setAttribute(k, v === true ? "" : v);
    });
  }
  (Array.isArray(children) ? children : [children]).forEach(function (c) {
    if (c == null || c === false) return;
    n.appendChild(typeof c === "string" || typeof c === "number"
      ? document.createTextNode(String(c)) : c);
  });
  return n;
}
BT.h = h;

BT.clear = function (node) { while (node.firstChild) node.removeChild(node.firstChild); return node; };

BT.svg = function (tag, attrs, children) {
  var n = document.createElementNS("http://www.w3.org/2000/svg", tag);
  if (attrs) {
    Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v == null || v === false) return;
      if (k.slice(0, 2) === "on") n.addEventListener(k.slice(2), v);
      else if (k === "text") n.textContent = v;
      else n.setAttribute(k, v);
    });
  }
  (Array.isArray(children) ? children : [children]).forEach(function (c) {
    if (c != null && c !== false) n.appendChild(c);
  });
  return n;
};

/* -------------------------------------------------------------- formatting */

var fmt = {
  int: function (v) { return v == null ? "—" : Number(v).toLocaleString(); },
  score: function (v, dp) { return v == null ? "—" : Number(v).toFixed(dp == null ? 2 : dp); },
  pct: function (v, dp) { return v == null ? "—" : (Number(v) * 100).toFixed(dp == null ? 0 : dp) + "%"; },
  signed: function (v, dp) {
    if (v == null) return "—";
    var s = Number(v).toFixed(dp == null ? 2 : dp);
    return Number(v) > 0 ? "+" + s : s;
  },
  /* Sentence-cased for prose; the stored values are lowercase slugs. */
  title: function (s) { return s ? s.charAt(0).toUpperCase() + s.slice(1) : ""; }
};
BT.fmt = fmt;

/* ------------------------------------------------------------------ colour */

BT.theme = function () { return document.documentElement.getAttribute("data-theme") === "dark" ? "dark" : "light"; };

BT.cssVar = function (name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
};

/* Twenty well-separated hues, then three lightness tiers on top of them.
   Beyond about sixty categories no palette is distinguishable and pretending
   otherwise is a lie the eye has to unpick — so past that, colour is for
   grouping and the highlight is for identifying. */
var HUES = [212, 28, 146, 320, 258, 45, 188, 350, 100, 276,
            14, 168, 300, 62, 232, 128, 338, 200, 82, 248];

BT.catColor = function (i, alpha) {
  var dark = BT.theme() === "dark";
  var hue = HUES[i % HUES.length];
  var tier = Math.floor(i / HUES.length) % 3;
  var sat = [64, 44, 78][tier];
  var light = (dark ? 62 : 45) + [0, 9, -7][tier];
  return "hsla(" + hue + "," + sat + "%," + light + "%," + (alpha == null ? 1 : alpha) + ")";
};

/* Viridis, nine stops. Sequential, perceptually ordered and colour-vision
   safe, and it reads on both the light and the dark canvas — which a
   theme-flipped single-hue ramp does not. */
var VIRIDIS = [[68, 1, 84], [71, 45, 123], [59, 82, 139], [44, 114, 142], [33, 145, 140],
               [40, 174, 128], [94, 201, 98], [173, 220, 48], [253, 231, 37]];

BT.ramp = function (t, alpha) {
  if (t == null || isNaN(t)) return BT.cssVar("--unassigned");
  var x = Math.max(0, Math.min(1, t)) * (VIRIDIS.length - 1);
  var i = Math.floor(x), f = x - i;
  var a = VIRIDIS[i], b = VIRIDIS[Math.min(i + 1, VIRIDIS.length - 1)];
  return "rgba(" + Math.round(a[0] + (b[0] - a[0]) * f) + "," +
    Math.round(a[1] + (b[1] - a[1]) * f) + "," +
    Math.round(a[2] + (b[2] - a[2]) * f) + "," + (alpha == null ? 1 : alpha) + ")";
};

BT.horizonColor = function (hz) {
  return BT.cssVar(hz === "H1" ? "--h1" : hz === "H2" ? "--h2" : hz === "H3" ? "--h3" : "--unassigned");
};
BT.signalColor = function (sc) {
  return BT.cssVar(sc === "weak" ? "--weak" : sc === "strong" ? "--strong"
    : sc === "latent" ? "--latent" : sc === "noise" ? "--noise" : "--unassigned");
};

BT.HORIZON_TEXT = {
  H1: "H1 — the current paradigm, declining in relevance",
  H2: "H2 — the transition zone, where today's business is disrupted",
  H3: "H3 — the emerging paradigm, marginal today"
};
BT.SIGNAL_TEXT = {
  weak: "Low volume, high growth — the horizon-scanning target",
  strong: "High volume, high growth — already visible to everyone",
  latent: "High volume, low growth — established, not moving",
  noise: "Low volume, low growth"
};
BT.QUADRANT_TEXT = {
  "act": "High strategic fit and high asset leverage",
  "on-strategy, no right-to-play": "On strategy, but IP Australia brings little to it",
  "capability looking for a problem": "Plays to an asset, but does not serve an objective",
  "watch": "Below the median on both axes"
};

/* ------------------------------------------------------------------ topics */

BT.topic = function (i) { return i == null || i < 0 ? null : BT.D.topics[i]; };
BT.topicOf = function (pointIdx) { return BT.topic(BT.P.topic[pointIdx]); };

/* Members of a topic, cached: the map redraws on every filter change and a
   linear scan of 25,000 points per redraw is the difference between a
   responsive page and a janky one. */
var memberCache = {};
BT.topicMembers = function (ti) {
  if (memberCache[ti]) return memberCache[ti];
  var out = [];
  for (var i = 0; i < BT.N; i++) if (BT.P.topic[i] === ti) out.push(i);
  return (memberCache[ti] = out);
};

/* High-dimensional neighbours of a plotted point, or null when the fidelity
   pass was skipped. Stored flat; reshaped here. */
BT.neighbours = function (i) {
  var nb = BT.D.neighbours;
  if (!nb || !nb.k || !nb.idx.length) return null;
  return nb.idx.slice(i * nb.k, (i + 1) * nb.k);
};

/* ------------------------------------------------------- numeric accessors

   One table drives the Topics columns, the Scores axes and the Map's
   continuous colouring, so a field added here appears in all three and cannot
   be described differently in each. `why` is the one-line explanation shown
   under a score bar; it is the shortest honest version of docs/method.md. */

BT.FIELDS = [
  { key: "composite_rank_score", label: "Composite rank score", dp: 3,
    why: "Emergence, strategic fit and asset leverage combined on the configured rank weights. The headline ordering." },
  { key: "emergence_score", label: "Emergence", dp: 2,
    why: "The five Rotolo attributes, each percentile-ranked within the run, on the configured weights." },
  { key: "strategic_fit", label: "Strategic fit", dp: 2,
    why: "Similarity to the single best-matching Corporate Plan objective or initiative — not the average across all of them." },
  { key: "asset_leverage", label: "Asset leverage", dp: 2,
    why: "Similarity to the single best-matching asset IP Australia already holds." },
  { key: "opportunity_index", label: "Opportunity index", dp: 2,
    why: "A relative within-run ordering of the signals that usually accompany a large opportunity. Not a market size, and not comparable to another run." },
  { key: "novelty", label: "Novelty", dp: 2, group: "rotolo",
    why: "Cosine distance of the topic from the early-corpus centroid." },
  { key: "growth", label: "Growth", dp: 2, group: "rotolo",
    why: "Compound annual growth blended with Kleinberg burst intensity." },
  { key: "coherence", label: "Coherence", dp: 2, group: "rotolo",
    why: "Mean cosine of the topic's documents to their own centroid. Low means the cluster may be an artefact." },
  { key: "impact", label: "Impact", dp: 2, group: "rotolo",
    why: "Citation percentile computed within each source, so arXiv preprints are not penalised for reporting no citations." },
  { key: "uncertainty", label: "Uncertainty", dp: 2, group: "rotolo",
    why: "Normalised entropy over the actors and source types behind the topic." },
  { key: "maturity", label: "Maturity", dp: 2,
    why: "Fitted position on the logistic growth curve at the last time slice. This, not the topic's age, sets the horizon band." },
  { key: "cagr", label: "CAGR", dp: 3, signed: true,
    why: "Compound annual growth rate of the topic's document count." },
  { key: "burst_weight", label: "Burst intensity", dp: 2,
    why: "Peak Kleinberg burst intensity. A topic growing only as fast as the corpus does not burst." },
  { key: "avg_proportion", label: "Avg. proportion", dp: 4,
    why: "Mean share of the corpus per time slice. The volume axis of the weak-signal split." },
  { key: "document_count", label: "Documents", dp: 0, int: true,
    why: "Documents assigned to the topic." },
  { key: "best_objective_sim", label: "Objective similarity", dp: 2,
    why: "Similarity to the best-matching objective, before the reference's own priority weight is applied." }
];

BT.field = function (key) {
  for (var i = 0; i < BT.FIELDS.length; i++) if (BT.FIELDS[i].key === key) return BT.FIELDS[i];
  return null;
};

BT.fieldValue = function (topic, key) {
  var v = topic[key];
  return v == null || v === "" ? null : Number(v);
};

BT.fieldFormat = function (key, value) {
  var f = BT.field(key);
  if (value == null) return "—";
  if (!f) return String(value);
  if (f.int) return fmt.int(value);
  if (f.signed) return fmt.signed(value, f.dp);
  return fmt.score(value, f.dp);
};

/* Min/max of a field across topics, for normalising bars and colour ramps.
   Cached because the map recolours on every pan. */
var extentCache = {};
BT.fieldExtent = function (key) {
  if (extentCache[key]) return extentCache[key];
  var lo = Infinity, hi = -Infinity;
  BT.D.topics.forEach(function (t) {
    var v = BT.fieldValue(t, key);
    if (v == null || isNaN(v)) return;
    if (v < lo) lo = v;
    if (v > hi) hi = v;
  });
  if (!isFinite(lo)) { lo = 0; hi = 1; }
  if (hi === lo) hi = lo + 1e-9;
  return (extentCache[key] = { lo: lo, hi: hi });
};

/* -------------------------------------------------------- shared fragments */

BT.tag = function (text, cls) { return h("span", { class: "tag " + (cls || ""), text: text }); };

BT.horizonTag = function (hz) {
  return hz ? h("span", { class: "tag t-" + hz, title: BT.HORIZON_TEXT[hz] || "", text: hz }) : null;
};
BT.signalTag = function (sc) {
  return sc ? h("span", { class: "tag t-" + sc, title: BT.SIGNAL_TEXT[sc] || "", text: sc }) : null;
};

/* A labelled bar with the number on it and the one-line reason underneath.
   The reason is the point: a score with no explanation beside it is a number
   the reader has to take on trust, which is what this whole page exists to
   avoid. */
BT.scoreBar = function (key, value, opts) {
  opts = opts || {};
  var f = BT.field(key) || { label: key, dp: 2 };
  var ext = opts.extent || BT.fieldExtent(key);
  var frac = value == null ? 0 : (value - ext.lo) / (ext.hi - ext.lo);
  var wrap = h("div", { class: "scorebar" }, [
    h("div", { class: "head" }, [
      h("span", { class: "name", text: opts.label || f.label }),
      h("span", { class: "val", text: BT.fieldFormat(key, value) })
    ]),
    h("div", { class: "track" }, [
      h("div", {
        class: "fill",
        style: "width:" + (value == null ? 0 : Math.max(1.5, frac * 100)).toFixed(1) + "%" +
          (opts.color ? ";background:" + opts.color : "")
      })
    ])
  ]);
  if (opts.why !== false && f.why) wrap.appendChild(h("div", { class: "why", text: f.why }));
  if (opts.note) wrap.appendChild(h("div", { class: "why", text: opts.note }));
  return wrap;
};

/* --------------------------------------------------------- small SVG charts

   Deliberately minimal: these exist to make a number legible next to its
   explanation, not to be a charting library. Everything is drawn in a
   0..w × 0..h viewBox with `preserveAspectRatio` left at the default, so a
   chart scales with its column. */

BT.chart = {};

BT.chart.bars = function (values, labels, opts) {
  opts = opts || {};
  var w = opts.width || 340, h0 = opts.height || 90, pad = { l: 4, r: 4, t: 6, b: 15 };
  var max = opts.max != null ? opts.max : Math.max.apply(null, values.concat([1]));
  var bw = (w - pad.l - pad.r) / Math.max(values.length, 1);
  var kids = [];
  values.forEach(function (v, i) {
    var bh = max <= 0 ? 0 : (v / max) * (h0 - pad.t - pad.b);
    kids.push(BT.svg("rect", {
      x: (pad.l + i * bw + bw * 0.14).toFixed(1), width: (bw * 0.72).toFixed(1),
      y: (h0 - pad.b - bh).toFixed(1), height: Math.max(0, bh).toFixed(1),
      rx: 1.5, class: "barfill",
      fill: opts.colors ? opts.colors[i] : null,
      opacity: opts.opacity ? opts.opacity[i] : null
    }, [BT.svg("title", { text: (labels[i] || "") + ": " + v })]));
    if (labels && labels.length <= 14) {
      kids.push(BT.svg("text", {
        x: (pad.l + i * bw + bw / 2).toFixed(1), y: h0 - 3,
        class: "lab mid", text: String(labels[i])
      }));
    }
  });
  kids.push(BT.svg("line", { x1: pad.l, x2: w - pad.r, y1: h0 - pad.b, y2: h0 - pad.b, class: "axis" }));
  return BT.svg("svg", { class: "chart", viewBox: "0 0 " + w + " " + h0, role: "img",
    "aria-label": opts.ariaLabel || "bar chart" }, kids);
};

/* Stacked columns — the corpus by year and source in one picture, which is
   how you see at a glance that one source dominates the recent slices. */
BT.chart.stacked = function (rows, rowLabels, seriesColors, opts) {
  opts = opts || {};
  var w = opts.width || 640, h0 = opts.height || 130, pad = { l: 4, r: 4, t: 6, b: 16 };
  var totals = rows.map(function (r) { return r.reduce(function (a, b) { return a + b; }, 0); });
  var max = Math.max.apply(null, totals.concat([1]));
  var bw = (w - pad.l - pad.r) / Math.max(rows.length, 1);
  var kids = [];
  rows.forEach(function (row, i) {
    var y = h0 - pad.b;
    row.forEach(function (v, s) {
      if (!v) return;
      var bh = (v / max) * (h0 - pad.t - pad.b);
      y -= bh;
      kids.push(BT.svg("rect", {
        x: (pad.l + i * bw + bw * 0.12).toFixed(1), width: (bw * 0.76).toFixed(1),
        y: y.toFixed(1), height: bh.toFixed(1), fill: seriesColors[s]
      }, [BT.svg("title", {
        text: rowLabels[i] + " · " + (opts.seriesLabels ? opts.seriesLabels[s] : s) + ": " + v
      })]));
    });
    kids.push(BT.svg("text", { x: (pad.l + i * bw + bw / 2).toFixed(1), y: h0 - 4,
      class: "lab mid", text: String(rowLabels[i]) }));
  });
  kids.push(BT.svg("line", { x1: pad.l, x2: w - pad.r, y1: h0 - pad.b, y2: h0 - pad.b, class: "axis" }));
  return BT.svg("svg", { class: "chart", viewBox: "0 0 " + w + " " + h0, role: "img",
    "aria-label": opts.ariaLabel || "stacked bar chart" }, kids);
};

/* A topic's per-slice counts with its burst slices marked — the Kleinberg
   output made visible against the series it was computed from. */
BT.chart.timeseries = function (series, opts) {
  opts = opts || {};
  var w = opts.width || 320, h0 = opts.height || 66, pad = { l: 3, r: 3, t: 5, b: 13 };
  var max = Math.max.apply(null, series.map(function (s) { return s.n; }).concat([1]));
  var bw = (w - pad.l - pad.r) / Math.max(series.length, 1);
  var accent = BT.cssVar("--accent"), burst = BT.cssVar("--h3"), faint = BT.cssVar("--line-strong");
  var kids = [];
  series.forEach(function (s, i) {
    var bh = (s.n / max) * (h0 - pad.t - pad.b);
    kids.push(BT.svg("rect", {
      x: (pad.l + i * bw + bw * 0.15).toFixed(1), width: (bw * 0.7).toFixed(1),
      y: (h0 - pad.b - bh).toFixed(1), height: Math.max(0.6, bh).toFixed(1), rx: 1,
      fill: s.burst ? burst : accent, opacity: s.burst ? 1 : 0.5
    }, [BT.svg("title", { text: s.slice + ": " + s.n + " documents" + (s.burst ? " (in burst)" : "") })]));
    if (series.length <= 12) {
      kids.push(BT.svg("text", { x: (pad.l + i * bw + bw / 2).toFixed(1), y: h0 - 3,
        class: "lab mid", text: String(s.slice).slice(-4) }));
    }
  });
  kids.push(BT.svg("line", { x1: pad.l, x2: w - pad.r, y1: h0 - pad.b, y2: h0 - pad.b,
    stroke: faint, "stroke-width": 1 }));
  return BT.svg("svg", { class: "chart", viewBox: "0 0 " + w + " " + h0, role: "img",
    "aria-label": opts.ariaLabel || "documents per time slice" }, kids);
};

/* --------------------------------------------------------------- downloads

   The viewer sandbox on some hosts blocks a page-initiated download, so this
   is best-effort and the Data view also renders the rows on screen. */
BT.downloadCSV = function (filename, rows) {
  var body = rows.map(function (r) {
    return r.map(function (c) {
      var s = c == null ? "" : String(c);
      return /[",\n]/.test(s) ? '"' + s.replace(/"/g, '""') + '"' : s;
    }).join(",");
  }).join("\n");
  var blob = new Blob([body], { type: "text/csv;charset=utf-8" });
  var a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  setTimeout(function () { URL.revokeObjectURL(a.href); a.remove(); }, 0);
};

/* ------------------------------------------------------------------ canvas

   Shared by the map and the scores scatter: both need a DPR-correct backing
   store and a camera that survives a resize. */

BT.makeCanvas = function (canvas) {
  var ctx = canvas.getContext("2d");
  var api = { canvas: canvas, ctx: ctx, w: 1, h: 1, cam: { scale: 1, tx: 0, ty: 0 } };

  api.resize = function () {
    var rect = canvas.parentElement.getBoundingClientRect();
    var dpr = window.devicePixelRatio || 1;
    api.w = Math.max(1, rect.width);
    api.h = Math.max(1, rect.height);
    canvas.width = Math.round(api.w * dpr);
    canvas.height = Math.round(api.h * dpr);
    canvas.style.width = api.w + "px";
    canvas.style.height = api.h + "px";
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  };

  api.toScreen = function (x, y) {
    return [x * api.cam.scale + api.cam.tx, -y * api.cam.scale + api.cam.ty];
  };
  api.toData = function (sx, sy) {
    return [(sx - api.cam.tx) / api.cam.scale, -(sy - api.cam.ty) / api.cam.scale];
  };

  api.fit = function (bounds, padFactor) {
    var pad = padFactor || 1.12;
    var w = Math.max(bounds.maxX - bounds.minX, 1e-6);
    var hh = Math.max(bounds.maxY - bounds.minY, 1e-6);
    api.cam.scale = Math.max(1e-6, Math.min(api.w / (w * pad), api.h / (hh * pad)));
    api.cam.tx = api.w / 2 - ((bounds.minX + bounds.maxX) / 2) * api.cam.scale;
    api.cam.ty = api.h / 2 + ((bounds.minY + bounds.maxY) / 2) * api.cam.scale;
  };

  api.zoomBy = function (factor, sx, sy) {
    var cx = sx == null ? api.w / 2 : sx, cy = sy == null ? api.h / 2 : sy;
    var before = api.toData(cx, cy);
    api.cam.scale = Math.max(1e-5, Math.min(api.cam.scale * factor, 1e6));
    var after = api.toScreen(before[0], before[1]);
    api.cam.tx += cx - after[0];
    api.cam.ty += cy - after[1];
  };

  return api;
};

BT.bounds = function (indices, xs, ys) {
  var minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (var k = 0; k < indices.length; k++) {
    var i = indices[k], x = xs[i], y = ys[i];
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (y < minY) minY = y;
    if (y > maxY) maxY = y;
  }
  if (!isFinite(minX)) return { minX: -1, maxX: 1, minY: -1, maxY: 1 };
  return { minX: minX, maxX: maxX, minY: minY, maxY: maxY };
};

/* Pan/zoom/hover/click wiring shared by both canvases. The host supplies
   `pick` (screen point → index or -1) and the callbacks. */
BT.attachPanZoom = function (api, opts) {
  var canvas = api.canvas;
  var dragging = false, moved = false, lastX = 0, lastY = 0;

  canvas.addEventListener("pointerdown", function (e) {
    dragging = true; moved = false; lastX = e.clientX; lastY = e.clientY;
    /* Capture is an optimisation — it keeps a drag alive when the pointer
       leaves the canvas. It throws for a pointer id the browser does not
       consider active, and letting that propagate would abort the handler and
       leave the map undraggable. Panning still works via the window-level
       pointerup below. */
    try { canvas.setPointerCapture(e.pointerId); } catch (err) { /* not capturable */ }
    canvas.classList.add("dragging");
  });

  canvas.addEventListener("pointermove", function (e) {
    var rect = canvas.getBoundingClientRect();
    if (dragging) {
      var dx = e.clientX - lastX, dy = e.clientY - lastY;
      if (Math.abs(dx) > 2 || Math.abs(dy) > 2) moved = true;
      api.cam.tx += dx; api.cam.ty += dy;
      lastX = e.clientX; lastY = e.clientY;
      if (opts.onHover) opts.onHover(-1, e);
      opts.onChange();
      return;
    }
    if (opts.onHover) opts.onHover(opts.pick(e.clientX - rect.left, e.clientY - rect.top), e);
  });

  canvas.addEventListener("pointerleave", function (e) { if (opts.onHover) opts.onHover(-1, e); });

  window.addEventListener("pointerup", function (e) {
    if (!dragging) return;
    dragging = false;
    canvas.classList.remove("dragging");
    if (!moved && opts.onClick) {
      var rect = canvas.getBoundingClientRect();
      opts.onClick(opts.pick(e.clientX - rect.left, e.clientY - rect.top), e);
    }
  });

  canvas.addEventListener("wheel", function (e) {
    e.preventDefault();
    var rect = canvas.getBoundingClientRect();
    api.zoomBy(Math.exp(-e.deltaY * 0.0015), e.clientX - rect.left, e.clientY - rect.top);
    if (opts.onHover) opts.onHover(-1, e);
    opts.onChange();
  }, { passive: false });

  canvas.addEventListener("dblclick", function (e) {
    if (!opts.onDoubleClick) return;
    var rect = canvas.getBoundingClientRect();
    opts.onDoubleClick(opts.pick(e.clientX - rect.left, e.clientY - rect.top), e);
  });
};

/* A uniform grid over the plotted points, so hover is O(1) rather than a scan
   of every point on every mouse move. Rebuilt when the visible set changes. */
BT.spatialIndex = function (indices, xs, ys, cells) {
  var b = BT.bounds(indices, xs, ys);
  var n = cells || 60;
  var cw = (b.maxX - b.minX) / n || 1, ch = (b.maxY - b.minY) / n || 1;
  var grid = Object.create(null);
  for (var k = 0; k < indices.length; k++) {
    var i = indices[k];
    var key = Math.floor((xs[i] - b.minX) / cw) + "," + Math.floor((ys[i] - b.minY) / ch);
    (grid[key] || (grid[key] = [])).push(i);
  }
  return {
    nearest: function (dx, dy, radius) {
      var cx = Math.floor((dx - b.minX) / cw), cy = Math.floor((dy - b.minY) / ch);
      var best = -1, bestDist = radius * radius;
      var reach = Math.max(1, Math.ceil(radius / Math.min(cw, ch)));
      reach = Math.min(reach, 4);
      for (var ox = -reach; ox <= reach; ox++) {
        for (var oy = -reach; oy <= reach; oy++) {
          var cell = grid[(cx + ox) + "," + (cy + oy)];
          if (!cell) continue;
          for (var k2 = 0; k2 < cell.length; k2++) {
            var i2 = cell[k2];
            var ddx = xs[i2] - dx, ddy = ys[i2] - dy;
            var d = ddx * ddx + ddy * ddy;
            if (d < bestDist) { bestDist = d; best = i2; }
          }
        }
      }
      return best;
    }
  };
};
