/* ==========================================================================
   map.js — the point cloud.

   Every collected document, laid out by a 2D projection of its embedding.
   The hard part is not drawing it; it is stopping a reader believing it.

   Three things do that work, and none of them is a disclaimer:

     1. The fidelity readout, top left. Trustworthiness and continuity are
        computed from this run's own vectors and coordinates, so the page
        states how much it is lying rather than warning that it might be.
     2. The per-topic purity pair in the inspector. A topic drawn scattered
        gets to say whether it is incoherent or merely badly projected.
     3. "Show true neighbours". Select a document and the page draws lines to
        its actual nearest neighbours in the full embedding space. When those
        lines shoot across the map, the reader has just watched the projection
        distort, on their own data, rather than been told it does.
   ========================================================================== */

BT.views.map = (function () {
  var h = BT.h, fmt = BT.fmt, D = BT.D, P = BT.P, N = BT.N;
  var cv, api, index, tooltip, inspector, rail;
  var visible = [], colorGroups = null, mounted = false;
  var selected = -1, focusTopic = -1, hoverTopic = -1;

  var state = {
    colorBy: "topic",
    sources: null,
    horizons: null,
    signals: null,
    yearMin: D.year_min, yearMax: D.year_max,
    search: "",
    shortlistOnly: false,
    showUnassigned: true,
    pointSize: 3.2,
    hulls: true,
    labels: true,
    neighbours: false
  };

  var HORIZONS = ["H1", "H2", "H3", ""];
  var SIGNALS = ["weak", "strong", "latent", "noise", ""];

  var CONTINUOUS = [
    "emergence_score", "strategic_fit", "asset_leverage",
    "opportunity_index", "composite_rank_score", "maturity"
  ];

  /* ------------------------------------------------------------- colours */

  function colorOf(i) {
    var ti = P.topic[i];
    switch (state.colorBy) {
      case "topic":
        return ti < 0 ? BT.cssVar("--unassigned") : BT.catColor(ti);
      case "horizon":
        return ti < 0 ? BT.cssVar("--unassigned") : BT.horizonColor(D.topics[ti].horizon);
      case "signal":
        return ti < 0 ? BT.cssVar("--unassigned") : BT.signalColor(D.topics[ti].signal_class);
      case "source":
        return P.source[i] < 0 ? BT.cssVar("--unassigned") : BT.catColor(P.source[i] * 3 + 1);
      case "steepv":
        return P.steepv[i] < 0 ? BT.cssVar("--unassigned") : BT.catColor(P.steepv[i] * 5 + 2);
      case "year":
        var y = P.year[i];
        if (y == null || D.year_max === D.year_min) return BT.cssVar("--unassigned");
        return BT.ramp((y - D.year_min) / (D.year_max - D.year_min));
      default:
        if (ti < 0) return BT.cssVar("--unassigned");
        var v = BT.fieldValue(D.topics[ti], state.colorBy);
        if (v == null) return BT.cssVar("--unassigned");
        var ext = BT.fieldExtent(state.colorBy);
        return BT.ramp((v - ext.lo) / (ext.hi - ext.lo));
    }
  }

  /* -------------------------------------------------------------- filter */

  function recompute() {
    var q = state.search.trim().toLowerCase();
    var shortlist = null;
    if (state.shortlistOnly) {
      shortlist = {};
      D.topics.forEach(function (t, i) {
        if (t.rank && t.rank <= D.shortlist_size) shortlist[i] = 1;
      });
    }
    visible = [];
    for (var i = 0; i < N; i++) {
      var ti = P.topic[i];
      if (ti < 0) {
        if (!state.showUnassigned || state.shortlistOnly) continue;
      } else {
        if (shortlist && !shortlist[ti]) continue;
        var t = D.topics[ti];
        if (!state.horizons[t.horizon || ""]) continue;
        if (!state.signals[t.signal_class || ""]) continue;
      }
      if (ti < 0 && (!state.horizons[""] || !state.signals[""])) continue;
      if (!state.sources[D.sources[P.source[i]]]) continue;
      var yr = P.year[i];
      if (yr != null) {
        if (state.yearMin != null && yr < state.yearMin) continue;
        if (state.yearMax != null && yr > state.yearMax) continue;
      }
      if (q && P.title[i].toLowerCase().indexOf(q) < 0) continue;
      visible.push(i);
    }
    regroup();
    index = BT.spatialIndex(visible, P.x, P.y);
    updateStats();
    buildLegend();
    draw();
  }

  function regroup() {
    var groups = Object.create(null);
    for (var k = 0; k < visible.length; k++) {
      var c = colorOf(visible[k]);
      (groups[c] || (groups[c] = [])).push(visible[k]);
    }
    colorGroups = groups;
  }

  /* --------------------------------------------------------------- draw */

  var raf = null;
  function draw() {
    if (raf) return;
    raf = requestAnimationFrame(function () {
      raf = null;
      render();
    });
  }

  function render() {
    if (!api) return;
    var ctx = api.ctx, TAU = Math.PI * 2;
    ctx.clearRect(0, 0, api.w, api.h);

    var dim = focusTopic >= 0 || hoverTopic >= 0;
    var lit = focusTopic >= 0 ? focusTopic : hoverTopic;

    if (state.hulls) drawHulls(ctx, lit);

    var r = state.pointSize;
    for (var color in colorGroups) {
      var idxs = colorGroups[color];
      /* Two passes per colour when something is highlighted: the dimmed rest
         first, then the lit topic on top, so highlighted points are never
         buried under the crowd they are meant to stand out from. */
      for (var pass = 0; pass < (dim ? 2 : 1); pass++) {
        ctx.beginPath();
        ctx.fillStyle = color;
        ctx.globalAlpha = !dim ? 0.78 : (pass === 0 ? 0.09 : 0.95);
        var any = false;
        for (var k = 0; k < idxs.length; k++) {
          var i = idxs[k];
          if (dim && (P.topic[i] === lit) !== (pass === 1)) continue;
          var sx = P.x[i] * api.cam.scale + api.cam.tx;
          var sy = -P.y[i] * api.cam.scale + api.cam.ty;
          if (sx < -6 || sy < -6 || sx > api.w + 6 || sy > api.h + 6) continue;
          ctx.moveTo(sx + r, sy);
          ctx.arc(sx, sy, r, 0, TAU);
          any = true;
        }
        if (any) ctx.fill();
      }
    }
    ctx.globalAlpha = 1;

    if (state.neighbours && selected >= 0) drawNeighbourLinks(ctx);
    if (selected >= 0) drawSelection(ctx);
    if (state.labels) drawLabels(ctx, lit);
  }

  function drawHulls(ctx, lit) {
    ctx.lineWidth = 1;
    D.topics.forEach(function (t, ti) {
      if (!t.hull || t.hull.length < 3) return;
      if (focusTopic >= 0 && ti !== focusTopic) return;
      ctx.beginPath();
      for (var k = 0; k < t.hull.length; k++) {
        var s = api.toScreen(t.hull[k][0], t.hull[k][1]);
        if (k === 0) ctx.moveTo(s[0], s[1]); else ctx.lineTo(s[0], s[1]);
      }
      ctx.closePath();
      var on = ti === lit;
      ctx.strokeStyle = state.colorBy === "topic" ? BT.catColor(ti) : BT.cssVar("--line-strong");
      ctx.globalAlpha = on ? 0.85 : (lit >= 0 ? 0.08 : 0.3);
      ctx.stroke();
      if (on) {
        ctx.fillStyle = ctx.strokeStyle;
        ctx.globalAlpha = 0.08;
        ctx.fill();
      }
    });
    ctx.globalAlpha = 1;
  }

  /* Labels for the topics that have room, sat just above their own points
     rather than over them, and drawn largest-first so that when two collide
     the bigger topic keeps its name. An unreadable pile of overlapping labels
     is worse than showing fewer; a reader who wants the rest has the legend,
     the search and the Topics table. Zooming in spreads the clusters apart,
     so more names appear — which is the behaviour that makes the map worth
     exploring rather than just looking at. */
  function drawLabels(ctx, lit) {
    var placed = [];
    var ordered = D.topics.map(function (t, i) { return { t: t, i: i }; })
      .filter(function (o) { return o.t.cx != null && o.t.plotted_count > 0; })
      .sort(function (a, b) {
        if (lit >= 0) {
          if (a.i === lit) return -1;
          if (b.i === lit) return 1;
        }
        return b.t.plotted_count - a.t.plotted_count;
      });

    ctx.font = "600 11px " + BT.cssVar("--sans").replace(/"/g, "");
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    var bg = BT.cssVar("--sunken");

    for (var k = 0; k < ordered.length; k++) {
      var t = ordered[k].t, ti = ordered[k].i;
      if (focusTopic >= 0 && ti !== focusTopic) continue;
      var s = api.toScreen(t.cx, t.cy);
      var lift = Math.min(60, (t.spread || 0) * api.cam.scale) + state.pointSize + 9;
      var lx = s[0], ly = s[1] - lift;
      if (ly < 14) ly = s[1] + lift;      /* flip under the cluster near the top edge */
      if (ly < 12 || ly > api.h - 12) continue;

      var text = t.short;
      var w = ctx.measureText(text).width;
      /* Clamp the box into the canvas rather than testing the centre point: a
         centre test lets a wide label hang off the edge half-drawn, which is
         how a name becomes "neuromorphic · computing · spi". A label wider than
         the whole canvas is dropped. */
      if (w + 12 > api.w) continue;
      lx = Math.max(w / 2 + 6, Math.min(lx, api.w - w / 2 - 6));
      var box = [lx - w / 2 - 4, ly - 8, w + 8, 16];
      var clash = false;
      for (var j = 0; j < placed.length; j++) {
        var p = placed[j];
        if (box[0] < p[0] + p[2] && box[0] + box[2] > p[0] &&
            box[1] < p[1] + p[3] && box[1] + box[3] > p[1]) { clash = true; break; }
      }
      if (clash) continue;
      placed.push(box);

      var on = ti === lit;
      /* A soft backdrop in the canvas colour, not a bordered chip: the label
         has to stay readable over points without becoming the thing you see
         first. The points are the data; the label is the caption. */
      ctx.globalAlpha = on ? 0.92 : (lit >= 0 ? 0.1 : 0.78);
      ctx.fillStyle = bg;
      roundRect(ctx, box[0], box[1], box[2], box[3], 3);
      ctx.fill();
      ctx.globalAlpha = on ? 1 : (lit >= 0 ? 0.2 : 0.92);
      ctx.fillStyle = on ? BT.cssVar("--fg") : BT.cssVar("--muted");
      ctx.fillText(text, lx, ly);
    }
    ctx.globalAlpha = 1;
    ctx.textAlign = "start";
  }

  function roundRect(ctx, x, y, w, hh, r) {
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + hh, r);
    ctx.arcTo(x + w, y + hh, x, y + hh, r);
    ctx.arcTo(x, y + hh, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
  }

  function drawNeighbourLinks(ctx) {
    var nb = BT.neighbours(selected);
    if (!nb) return;
    var from = api.toScreen(P.x[selected], P.y[selected]);
    ctx.strokeStyle = BT.cssVar("--h3");
    ctx.lineWidth = 1.4;
    ctx.globalAlpha = 0.8;
    ctx.beginPath();
    nb.forEach(function (j) {
      var to = api.toScreen(P.x[j], P.y[j]);
      ctx.moveTo(from[0], from[1]);
      ctx.lineTo(to[0], to[1]);
    });
    ctx.stroke();
    ctx.fillStyle = BT.cssVar("--h3");
    nb.forEach(function (j) {
      var to = api.toScreen(P.x[j], P.y[j]);
      ctx.beginPath();
      ctx.arc(to[0], to[1], state.pointSize + 1.6, 0, Math.PI * 2);
      ctx.fill();
    });
    ctx.globalAlpha = 1;
  }

  function drawSelection(ctx) {
    var s = api.toScreen(P.x[selected], P.y[selected]);
    ctx.strokeStyle = BT.cssVar("--fg");
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(s[0], s[1], state.pointSize + 5, 0, Math.PI * 2);
    ctx.stroke();
  }

  /* ----------------------------------------------------------- overlays */

  function updateStats() {
    var box = document.getElementById("mapStats");
    BT.clear(box);
    box.appendChild(h("span", null, [
      h("b", { text: fmt.int(visible.length) }), " of " + fmt.int(N) + " documents shown"
    ]));
    if (focusTopic >= 0) {
      box.appendChild(h("span", null, [
        " · focused on ", h("b", { text: D.topics[focusTopic].short }),
        " ",
        h("button", { class: "btn sm", type: "button", style: "pointer-events:auto;margin-left:6px",
          onclick: function () { setFocus(-1); }, text: "clear" })
      ]));
      box.classList.add("interactive");
    } else {
      box.classList.remove("interactive");
    }
  }

  function fidelityOverlay() {
    var box = document.getElementById("mapFidelity");
    BT.clear(box);
    var f = D.fidelity || {};
    if (!f.computed) {
      box.appendChild(h("span", null, [
        h("b", { text: "Projection fidelity not measured" }), " — ", f.reason || "unavailable"
      ]));
      return;
    }
    function metric(label, value, good, title) {
      var cls = value == null ? "t-muted" : value >= good ? "t-good" : value >= good - 0.15 ? "t-warn" : "t-bad";
      return h("span", { title: title,
        style: "display:inline-flex;gap:5px;align-items:baseline;margin-right:13px" }, [
        label, h("b", { class: cls, text: value == null ? "—" : value.toFixed(2) })
      ]);
    }
    box.appendChild(h("div", { style: "display:flex;flex-wrap:wrap;align-items:baseline" }, [
      h("span", { style: "font-weight:650;color:var(--fg);margin-right:10px",
        text: "Projection fidelity" }),
      metric("trustworthiness", f.trustworthiness, 0.9,
        "Are the neighbours you can see real? 1.00 means nothing was drawn close that is not."),
      metric("continuity", f.continuity, 0.9,
        "Are the real neighbours visible? 1.00 means nothing real was pushed apart.")
    ]));
    box.appendChild(h("div", { class: "note", style: "margin-top:2px",
      title: "The share of a document's nearest neighbours that share its topic, measured both "
        + "ways. The gap between them is the distortion this picture adds." },
      "same-topic neighbours: " +
      (f.map_purity == null ? "—" : (f.map_purity * 100).toFixed(0) + "%") + " here, " +
      (f.space_purity == null ? "—" : (f.space_purity * 100).toFixed(0) + "%") +
      " in the real " + D.dimensions + "-D space · k=" + f.k +
      " on " + fmt.int(f.sample) + " sampled points"));
  }

  /* One line, not a paragraph. The caveat has to be present on the map itself
     — it is the thing most likely to be misread — but a four-line block in the
     corner covers the data it is warning about. The detail lives in the title
     and, at length, in the inspector. */
  function axisNote() {
    var box = document.getElementById("mapAxisNote");
    BT.clear(box);
    var pj = D.projection || {};
    box.title = pj.resolved === "umap"
      ? "UMAP · n_neighbors=" + pj.n_neighbors + " · min_dist=" + pj.min_dist +
        " · metric " + pj.metric + " · seed " + pj.random_state +
        (pj.followed && pj.followed.length
          ? " — following the clustering configuration for: " + pj.followed.join(", ")
          : "")
      : "PCA fallback: umap-learn is not installed, so this is a flatter map than intended.";
    box.appendChild(h("div", null, [
      h("b", { text: "The axes have no unit." }),
      " Nearness means similar language. ",
      h("span", { class: "note", text: pj.resolved === "umap"
        ? "UMAP k=" + pj.n_neighbors + ", seed " + pj.random_state
        : "PCA fallback" })
    ]));
  }

  /* ------------------------------------------------------------- legend */

  function legendEntries() {
    var counts = Object.create(null);
    for (var k = 0; k < visible.length; k++) {
      var i = visible[k], key;
      var ti = P.topic[i];
      if (state.colorBy === "topic") key = ti;
      else if (state.colorBy === "horizon") key = ti < 0 ? "" : (D.topics[ti].horizon || "");
      else if (state.colorBy === "signal") key = ti < 0 ? "" : (D.topics[ti].signal_class || "");
      else if (state.colorBy === "source") key = D.sources[P.source[i]] || "";
      else if (state.colorBy === "steepv") key = D.steepv[P.steepv[i]] || "";
      else continue;
      counts[key] = (counts[key] || 0) + 1;
    }

    if (state.colorBy === "topic") {
      return D.topics.map(function (t, i) {
        return { key: i, label: t.short, color: BT.catColor(i), n: counts[i] || 0, topic: i };
      }).concat([{ key: -1, label: "(unassigned)", color: BT.cssVar("--unassigned"), n: counts[-1] || 0 }])
        .sort(function (a, b) { return b.n - a.n; });
    }
    if (state.colorBy === "horizon") {
      return HORIZONS.map(function (hz) {
        return { key: hz, label: hz ? BT.HORIZON_TEXT[hz] : "(unassigned)",
          color: BT.horizonColor(hz), n: counts[hz] || 0 };
      });
    }
    if (state.colorBy === "signal") {
      return SIGNALS.map(function (sc) {
        return { key: sc, label: sc ? sc + " — " + BT.SIGNAL_TEXT[sc] : "(unassigned)",
          color: BT.signalColor(sc), n: counts[sc] || 0 };
      });
    }
    if (state.colorBy === "source") {
      return D.sources.map(function (s, i) {
        return { key: s, label: s, color: BT.catColor(i * 3 + 1), n: counts[s] || 0 };
      });
    }
    if (state.colorBy === "steepv") {
      return D.steepv.map(function (s, i) {
        return { key: s, label: s, color: BT.catColor(i * 5 + 2), n: counts[s] || 0 };
      });
    }
    return null;
  }

  function buildLegend() {
    var box = document.getElementById("mapLegend");
    if (!box) return;
    BT.clear(box);

    var entries = legendEntries();
    if (!entries) {
      /* Continuous scale — a gradient with its two ends labelled. */
      var f = BT.field(state.colorBy);
      var ext = state.colorBy === "year"
        ? { lo: D.year_min, hi: D.year_max } : BT.fieldExtent(state.colorBy);
      var stops = [];
      for (var s = 0; s <= 10; s++) stops.push(BT.ramp(s / 10) + " " + (s * 10) + "%");
      box.appendChild(h("div", {
        style: "height:12px;border-radius:3px;border:1px solid var(--line);" +
          "background:linear-gradient(90deg," + stops.join(",") + ")"
      }));
      box.appendChild(h("div", { class: "legendrow", style: "justify-content:space-between" }, [
        h("span", { text: state.colorBy === "year" ? String(ext.lo) : fmt.score(ext.lo, 2) }),
        h("span", { text: state.colorBy === "year" ? String(ext.hi) : fmt.score(ext.hi, 2) })
      ]));
      if (f && f.why) box.appendChild(h("div", { class: "hint", text: f.why }));
      box.appendChild(h("div", { class: "hint", text:
        "Unassigned documents are grey: they belong to no topic, so they have no topic score." }));
      return;
    }

    entries.slice(0, 80).forEach(function (e) {
      var row = h("div", {
        class: "chk",
        style: e.topic != null ? "cursor:pointer" : "",
        title: e.label,
        onclick: e.topic != null ? function () { setFocus(focusTopic === e.topic ? -1 : e.topic); } : null,
        onmouseenter: e.topic != null ? function () { hoverTopic = e.topic; draw(); } : null,
        onmouseleave: e.topic != null ? function () { hoverTopic = -1; draw(); } : null
      }, [
        h("span", { class: "swatch", style: "background:" + e.color }),
        h("span", { class: "lab", text: e.label }),
        h("span", { class: "count", text: fmt.int(e.n) })
      ]);
      box.appendChild(row);
    });
    if (entries.length > 80) {
      box.appendChild(h("div", { class: "hint",
        text: (entries.length - 80) + " more — use the topic search above." }));
    }
  }

  /* ---------------------------------------------------------------- rail */

  function buildRail() {
    BT.clear(rail);

    rail.appendChild(h("h4", { text: "Colour by" }));
    var options = [
      ["topic", "Topic"], ["horizon", "Three Horizons band"], ["signal", "Signal class"],
      ["source", "Source"], ["steepv", "STEEPV category"], ["year", "Year"]
    ].concat(CONTINUOUS.map(function (k) {
      var f = BT.field(k);
      return [k, f ? f.label : k];
    }));
    var sel = h("select", { "aria-label": "Colour the points by", onchange: function () {
      state.colorBy = sel.value; regroup(); buildLegend(); draw();
    } }, options.map(function (o) {
      return h("option", { value: o[0], selected: o[0] === state.colorBy, text: o[1] });
    }));
    rail.appendChild(sel);
    rail.appendChild(h("div", { id: "mapLegend", class: "scrolllist", style: "margin-top:8px" }));

    rail.appendChild(h("h4", { text: "Find" }));
    var topicSearch = h("input", {
      type: "search", placeholder: "Jump to a topic…", list: "mapTopicList",
      "aria-label": "Jump to a topic",
      onchange: function () {
        var idx = -1;
        D.topics.forEach(function (t, i) { if (t.label === topicSearch.value) idx = i; });
        setFocus(idx);
      }
    });
    rail.appendChild(topicSearch);
    rail.appendChild(h("datalist", { id: "mapTopicList" }, D.topics.map(function (t) {
      return h("option", { value: t.label });
    })));

    var titleSearch = h("input", {
      type: "search", placeholder: "Filter document titles…", style: "margin-top:6px",
      "aria-label": "Filter document titles",
      oninput: debounce(function () { state.search = titleSearch.value; recompute(); }, 160)
    });
    rail.appendChild(titleSearch);

    rail.appendChild(h("h4", { text: "Year" }));
    var yMin = h("input", { type: "number", value: D.year_min, min: D.year_min, max: D.year_max,
      "aria-label": "Earliest year" });
    var yMax = h("input", { type: "number", value: D.year_max, min: D.year_min, max: D.year_max,
      "aria-label": "Latest year" });
    [yMin, yMax].forEach(function (inp) {
      inp.addEventListener("change", function () {
        state.yearMin = yMin.value ? Number(yMin.value) : null;
        state.yearMax = yMax.value ? Number(yMax.value) : null;
        recompute();
      });
    });
    rail.appendChild(h("div", { style: "display:flex;gap:6px;align-items:center" },
      [yMin, h("span", { class: "note", text: "to" }), yMax]));

    rail.appendChild(h("h4", { text: "Source" }));
    rail.appendChild(checkList(D.sources, state.sources, function (s, i) {
      return { label: s, color: BT.catColor(i * 3 + 1) };
    }));

    rail.appendChild(h("h4", { text: "Horizon" }));
    rail.appendChild(checkList(HORIZONS, state.horizons, function (hz) {
      return { label: hz || "(unassigned)", color: BT.horizonColor(hz) };
    }));

    rail.appendChild(h("h4", { text: "Signal class" }));
    rail.appendChild(checkList(SIGNALS, state.signals, function (sc) {
      return { label: sc || "(unassigned)", color: BT.signalColor(sc) };
    }));

    rail.appendChild(h("h4", { text: "Show" }));
    rail.appendChild(toggle("Shortlisted topics only", state.shortlistOnly, function (v) {
      state.shortlistOnly = v; recompute();
    }));
    rail.appendChild(toggle("Documents with no topic", state.showUnassigned, function (v) {
      state.showUnassigned = v; recompute();
    }));
    rail.appendChild(toggle("Topic outlines", state.hulls, function (v) { state.hulls = v; draw(); }));
    rail.appendChild(toggle("Topic labels", state.labels, function (v) { state.labels = v; draw(); }));
    rail.appendChild(toggle("True neighbours of the selected point",
      state.neighbours, function (v) { state.neighbours = v; draw(); },
      BT.D.neighbours && BT.D.neighbours.k
        ? "Draws lines to the point's nearest neighbours in the full " + D.dimensions +
          "-dimensional space. Lines that shoot across the map are the projection distorting."
        : null,
      !(BT.D.neighbours && BT.D.neighbours.k)));

    rail.appendChild(h("h4", { text: "Point size" }));
    var size = h("input", { type: "range", min: 1, max: 7, step: 0.2, value: state.pointSize,
      "aria-label": "Point size",
      oninput: function () { state.pointSize = Number(size.value); draw(); } });
    rail.appendChild(size);

    rail.appendChild(h("div", { class: "rowactions", style: "margin-top:14px" }, [
      h("button", { class: "btn", type: "button", style: "flex:1", onclick: reset, text: "Reset" }),
      h("button", { class: "btn", type: "button", style: "flex:1", onclick: function () {
        api.fit(BT.bounds(visible.length ? visible : allIdx(), P.x, P.y));
        draw();
      }, text: "Fit view" })
    ]));

    buildLegend();
  }

  function checkList(keys, set, meta) {
    var box = h("div", { class: "scrolllist" });
    keys.forEach(function (key, i) {
      var m = meta(key, i);
      var cb = h("input", { type: "checkbox", checked: !!set[key], onchange: function () {
        set[key] = cb.checked; recompute();
      } });
      box.appendChild(h("label", { class: "chk" }, [
        cb, h("span", { class: "swatch", style: "background:" + m.color }),
        h("span", { class: "lab", text: m.label })
      ]));
    });
    return box;
  }

  function toggle(label, value, onchange, title, disabled) {
    var cb = h("input", { type: "checkbox", checked: value, disabled: disabled,
      onchange: function () { onchange(cb.checked); } });
    return h("label", { class: "chk", title: title || label,
      style: disabled ? "opacity:.45" : null }, [cb, h("span", { class: "lab", text: label })]);
  }

  function debounce(fn, ms) {
    var t = null;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  function allIdx() {
    var out = [];
    for (var i = 0; i < N; i++) out.push(i);
    return out;
  }

  function reset() {
    state.sources = {};
    D.sources.forEach(function (s) { state.sources[s] = true; });
    state.horizons = {}; HORIZONS.forEach(function (k) { state.horizons[k] = true; });
    state.signals = {}; SIGNALS.forEach(function (k) { state.signals[k] = true; });
    state.yearMin = D.year_min; state.yearMax = D.year_max;
    state.search = ""; state.shortlistOnly = false; state.showUnassigned = true;
    focusTopic = -1; selected = -1;
    buildRail();
    recompute();
    api.fit(BT.bounds(visible.length ? visible : allIdx(), P.x, P.y));
    showPlaceholder();
    draw();
  }

  /* ---------------------------------------------------------- inspector */

  /* The panel is 330px wide and would otherwise sit empty until the first
     click. Filling it with how to read the map, and with the largest topics
     as a jump list, means the space earns itself on arrival rather than
     rewarding a reader who has already worked out what to do. */
  function showPlaceholder() {
    BT.clear(inspector);
    var box = h("div", { class: "pad" });
    var f = D.fidelity || {};

    box.appendChild(h("h4", { text: "How to read this map" }));
    box.appendChild(h("p", { style: "margin-top:7px;font-size:13px" },
      "Each dot is one collected document, placed by a 2D projection of its " +
      D.dimensions + "-dimensional embedding. Points that sit together used similar language. " +
      "The axes themselves carry no unit and no direction means anything."));

    if (f.computed) {
      box.appendChild(h("p", { style: "font-size:13px" }, [
        "Flattening " + D.dimensions + " dimensions to two is lossy, so the page measures the " +
        "loss rather than warning about it. On this run trustworthiness is ",
        h("b", { text: f.trustworthiness == null ? "—" : f.trustworthiness.toFixed(2) }),
        " — of the neighbours you can see, that share are genuinely close — and continuity is ",
        h("b", { text: f.continuity == null ? "—" : f.continuity.toFixed(2) }),
        ", the share of genuinely close pairs the picture keeps together."
      ]));
      if (BT.D.neighbours && BT.D.neighbours.k) {
        box.appendChild(h("p", { class: "note" },
          "Select a point and switch on “True neighbours” in the left rail to draw lines to its " +
          "actual nearest neighbours in the full space. Lines that shoot across the map are the " +
          "projection distorting, on your own data."));
      }
    } else {
      box.appendChild(h("p", { class: "note" },
        "Projection fidelity was not measured on this run" +
        (f.reason ? " (" + f.reason + ")" : "") + ", so treat every grouping here as a lead " +
        "to check rather than a result."));
    }

    box.appendChild(h("h4", { style: "margin-top:18px", text: "Largest topics" }));
    var ordered = D.topics.map(function (t, i) { return { t: t, i: i }; })
      .sort(function (a, b) { return b.t.document_count - a.t.document_count; })
      .slice(0, 12);
    ordered.forEach(function (o) {
      box.appendChild(h("div", {
        class: "chk", style: "cursor:pointer", title: o.t.label,
        onclick: function () { setFocus(o.i); },
        onmouseenter: function () { hoverTopic = o.i; draw(); },
        onmouseleave: function () { hoverTopic = -1; draw(); }
      }, [
        h("span", { class: "swatch", style: "background:" + BT.catColor(o.i) }),
        h("span", { class: "lab", text: o.t.short }),
        h("span", { class: "count", text: fmt.int(o.t.document_count) })
      ]));
    });

    box.appendChild(h("p", { class: "hint", style: "margin-top:14px" },
      "Click a point for the document behind it. Double-click to focus its topic. Every score " +
      "shown here is also in the Topics table, sortable and exportable."));
    inspector.appendChild(box);
  }

  function topicDetail(t, ti) {
    var box = h("div", { class: "pad" });
    box.appendChild(h("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px" }, [
      t.rank ? h("span", { class: "tag t-muted", text: "#" + t.rank + " of " + D.topics_total }) : null,
      BT.horizonTag(t.horizon), BT.signalTag(t.signal_class),
      t.rank && t.rank <= D.shortlist_size ? h("span", { class: "tag t-good", text: "shortlisted" }) : null
    ]));
    box.appendChild(h("h3", { text: t.label }));
    box.appendChild(h("p", { class: "note", style: "margin-top:4px" },
      fmt.int(t.document_count) + " documents · " + t.first_slice + "–" + t.last_slice +
      (t.critical_tech ? " · DISR: " + t.critical_tech : "")));

    ["composite_rank_score", "emergence_score", "strategic_fit", "asset_leverage"].forEach(function (k) {
      box.appendChild(BT.scoreBar(k, t[k]));
    });
    box.appendChild(BT.scoreBar("opportunity_index", t.opportunity_index, {
      note: t.index_suppressed
        ? "Suppressed: below the minimum document count, so the composite is not reported."
        : null
    }));

    box.appendChild(h("h4", { style: "margin:16px 0 4px", text: "Documents over time" }));
    if (t.timeseries.length) box.appendChild(BT.chart.timeseries(t.timeseries, { width: 292, height: 62 }));

    var f = D.fidelity || {};
    if (f.computed && t.map_purity != null) {
      box.appendChild(h("h4", { style: "margin:16px 0 4px", text: "Is this topic drawn faithfully?" }));
      var gap = t.space_purity - t.map_purity;
      box.appendChild(h("p", { class: "note" }, [
        "Of a member document's " + f.k + " nearest neighbours, ",
        h("b", { text: fmt.pct(t.space_purity) }), " share this topic in the full " +
        D.dimensions + "-dimensional space and ",
        h("b", { text: fmt.pct(t.map_purity) }), " do here on the map."
      ]));
      box.appendChild(h("p", { class: "note", style: "margin-top:4px", text:
        gap > 0.25
          ? "The projection has pulled this topic apart: it is more coherent than it looks here."
          : gap < -0.1
            ? "The map draws this topic tighter than the real space does — read its evidence card before trusting the grouping."
            : "The map represents this topic about as well as the real space does." }));
    }

    box.appendChild(h("dl", { class: "kv" }, [
      h("dt", { text: "Top terms" }), h("dd", { text: t.terms.join(", ") }),
      h("dt", { text: "Closest objective" }),
      h("dd", { text: t.best_objective || "—" }),
      h("dt", { text: "Closest asset" }), h("dd", { text: t.best_asset || "—" }),
      h("dt", { text: "2×2 placement" }),
      h("dd", null, [t.fit_quadrant, h("div", { class: "note", text: BT.QUADRANT_TEXT[t.fit_quadrant] || "" })])
    ]));

    box.appendChild(h("div", { class: "rowactions", style: "margin-top:14px" }, [
      h("button", { class: "btn", type: "button", onclick: function () { setFocus(ti); },
        text: focusTopic === ti ? "Clear focus" : "Focus this topic" }),
      h("button", { class: "btn", type: "button",
        onclick: function () { BT.go("topics", { topic: t.id }); }, text: "Full detail →" }),
      t.evidence_url ? h("a", { class: "btn", href: t.evidence_url, target: "_blank",
        rel: "noopener noreferrer", text: "Evidence card →" }) : null
    ]));
    return box;
  }

  function showTopic(ti) {
    var t = D.topics[ti];
    if (!t) return showPlaceholder();
    BT.clear(inspector);
    inspector.appendChild(topicDetail(t, ti));
  }

  function showPoint(i) {
    BT.clear(inspector);
    var box = h("div", { class: "pad" });
    box.appendChild(h("h4", { text: "Document" }));
    box.appendChild(h("h3", { style: "margin-top:4px", text: P.title[i] }));
    box.appendChild(h("p", { class: "note", style: "margin-top:4px" },
      [D.sources[P.source[i]] || "—", P.year[i] ? " · " + P.year[i] : "",
       P.citation[i] ? " · " + fmt.int(P.citation[i]) + " citations" : "",
       P.venue[i] ? " · " + P.venue[i] : ""].join("")));
    if (P.url[i]) {
      box.appendChild(h("p", { style: "margin-top:8px" }, [
        h("a", { href: P.url[i], target: "_blank", rel: "noopener noreferrer",
          text: "Open the source →" })
      ]));
    }

    var ti = P.topic[i];
    if (ti < 0) {
      box.appendChild(h("div", { class: "callout", style: "margin-top:12px" }, [
        h("strong", { text: "No topic. " }),
        "This document was too dissimilar from every detected topic to join one — mostly GDELT " +
        "attention signal, which is deliberately excluded from topic formation. It is still " +
        "plotted, because it is real corpus content and it still feeds the attention components " +
        "of the opportunity index."
      ]));
    } else if (P.similarity[i] != null) {
      box.appendChild(h("p", { class: "note", style: "margin-top:8px",
        text: "Similarity to its topic centroid: " + fmt.score(P.similarity[i]) }));
    }

    var nb = BT.neighbours(i);
    if (nb) {
      box.appendChild(h("h4", { style: "margin:16px 0 5px",
        text: "Its true nearest neighbours (" + D.dimensions + "-D)" }));
      box.appendChild(h("div", { class: "note", style: "margin-bottom:6px", text:
        "Computed in the full embedding space, not on the map. Turn on “True neighbours” in the "
        + "left rail to see where they landed." }));
      nb.slice(0, 6).forEach(function (j) {
        var jt = P.topic[j];
        box.appendChild(h("div", { class: "chk", style: "cursor:pointer;align-items:flex-start",
          onclick: function () { select(j); } }, [
          h("span", { class: "swatch", style: "margin-top:5px;background:" +
            (jt < 0 ? BT.cssVar("--unassigned") : BT.catColor(jt)) }),
          h("span", { style: "flex:1;min-width:0" }, [
            h("div", { style: "line-height:1.3", text: P.title[j] }),
            h("div", { class: "note", text: jt < 0 ? "no topic" : D.topics[jt].short })
          ])
        ]));
      });
    }

    if (ti >= 0) {
      box.appendChild(h("h4", { style: "margin:18px 0 0", text: "Its topic" }));
      inspector.appendChild(box);
      inspector.appendChild(topicDetail(D.topics[ti], ti));
      return;
    }
    inspector.appendChild(box);
  }

  function select(i) {
    selected = i;
    if (i < 0) showPlaceholder(); else showPoint(i);
    draw();
  }

  function setFocus(ti) {
    focusTopic = ti;
    if (ti >= 0) {
      var members = BT.topicMembers(ti).filter(function (i) { return visible.indexOf(i) >= 0 || true; });
      if (members.length) api.fit(BT.bounds(members, P.x, P.y), 1.5);
      showTopic(ti);
    }
    updateStats();
    draw();
  }

  /* ------------------------------------------------------------ tooltip */

  function moveTooltip(i, e) {
    if (i < 0) { tooltip.style.display = "none"; return; }
    BT.clear(tooltip);
    var ti = P.topic[i];
    tooltip.appendChild(h("div", { class: "tt", text: P.title[i] }));
    tooltip.appendChild(h("div", { class: "tm", text:
      [D.sources[P.source[i]] || "", P.year[i] || "", P.venue[i] || ""]
        .filter(Boolean).join(" · ") }));
    tooltip.appendChild(h("div", { class: "tm", style: "margin-top:3px" }, [
      h("span", { class: "swatch", style: "margin-right:5px;background:" +
        (ti < 0 ? BT.cssVar("--unassigned") : BT.catColor(ti)) }),
      ti < 0 ? "no topic" : D.topics[ti].short
    ]));
    tooltip.style.display = "block";

    var wrap = cv.parentElement.getBoundingClientRect();
    var w = tooltip.offsetWidth, hh = tooltip.offsetHeight;
    var left = e.clientX - wrap.left + 16, top = e.clientY - wrap.top + 16;
    if (left + w > wrap.width - 6) left = e.clientX - wrap.left - w - 14;
    if (top + hh > wrap.height - 6) top = e.clientY - wrap.top - hh - 14;
    tooltip.style.left = Math.max(4, left) + "px";
    tooltip.style.top = Math.max(4, top) + "px";
  }

  /* ---------------------------------------------------------------- api */

  return {
    mount: function () {
      if (mounted) { api.resize(); draw(); return; }
      mounted = true;
      cv = document.getElementById("cv");
      rail = document.getElementById("mapRail");
      tooltip = document.getElementById("tooltip");
      inspector = document.getElementById("inspector");
      api = BT.makeCanvas(cv);

      state.sources = {};
      D.sources.forEach(function (s) { state.sources[s] = true; });
      state.horizons = {}; HORIZONS.forEach(function (k) { state.horizons[k] = true; });
      state.signals = {}; SIGNALS.forEach(function (k) { state.signals[k] = true; });

      BT.attachPanZoom(api, {
        pick: function (sx, sy) {
          if (!index) return -1;
          var d = api.toData(sx, sy);
          return index.nearest(d[0], d[1], Math.max(8, state.pointSize * 2.5) / api.cam.scale);
        },
        onHover: moveTooltip,
        onClick: function (i) { select(i); },
        onDoubleClick: function (i) { if (i >= 0 && P.topic[i] >= 0) setFocus(P.topic[i]); },
        onChange: draw
      });

      document.getElementById("zoomIn").onclick = function () { api.zoomBy(1.4); draw(); };
      document.getElementById("zoomOut").onclick = function () { api.zoomBy(1 / 1.4); draw(); };
      document.getElementById("zoomFit").onclick = function () {
        api.fit(BT.bounds(visible.length ? visible : allIdx(), P.x, P.y));
        draw();
      };

      buildRail();
      fidelityOverlay();
      axisNote();
      showPlaceholder();
      api.resize();
      recompute();
      api.fit(BT.bounds(visible, P.x, P.y));
      draw();
    },

    resize: function () { if (mounted) { api.resize(); draw(); } },

    refresh: function () {
      if (!mounted) return;
      regroup(); buildLegend(); fidelityOverlay(); axisNote(); draw();
    },

    focusTopicId: function (topicId) {
      var idx = -1;
      D.topics.forEach(function (t, i) { if (t.id === topicId) idx = i; });
      if (idx >= 0) setFocus(idx);
    }
  };
})();
