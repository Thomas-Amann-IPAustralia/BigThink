/* ==========================================================================
   scores.js — any score against any other.

   The Topics table shows every number; this shows the relationships between
   them, which is where the interesting questions live. The presets are not
   decoration: each is a figure the method actually reasons about, and each
   draws its own guide lines so the reader sees the cut that produced a label
   rather than being told about it.

     Fit × leverage     the Stage 3 2×2, with the medians that split it
     Volume × growth    the weak-signal quadrants, same idea
     Maturity × emerg.  the Three Horizons cut-points as bands
     Emergence × index  the number that ranks against the number that does not
   ========================================================================== */

BT.views.scores = (function () {
  var h = BT.h, fmt = BT.fmt, D = BT.D;
  var cv, api, toolbar, inspector, tooltip, mounted = false, index = null;
  var pts = [], selected = -1;

  var state = {
    x: "strategic_fit",
    y: "asset_leverage",
    color: "horizon",
    size: "document_count",
    preset: "fit",
    labels: true
  };

  var PRESETS = {
    fit: { label: "Fit × leverage", x: "strategic_fit", y: "asset_leverage",
      color: "quadrant", guides: "median",
      note: "The Stage 3 2×2. Split at the median of each axis within this run, so the "
        + "quadrants divide the topics into rough quarters — they rank topics against each "
        + "other, not against an absolute standard of good fit." },
    weak: { label: "Volume × growth", x: "avg_proportion", y: "growth",
      color: "signal", guides: "median",
      note: "The weak-signal split. Low volume with high growth is the horizon-scanning "
        + "target: the thing that is moving before it is obvious." },
    horizons: { label: "Maturity × emergence", x: "maturity", y: "emergence_score",
      color: "horizon", guides: "horizons",
      note: "Fitted position on the logistic curve against the emergence score. The vertical "
        + "bands are the Three Horizons cut-points — that fitted position, not the topic's "
        + "age, is what assigns the band." },
    index: { label: "Emergence × opportunity index", x: "emergence_score", y: "opportunity_index",
      color: "signal", guides: null,
      note: "The number the ranking uses against the number it deliberately excludes. "
        + "Topics with a suppressed index are absent from this plot entirely, because a "
        + "suppressed index is missing, not zero." }
  };

  /* -------------------------------------------------------------- layout */

  var PAD = { l: 62, r: 22, t: 20, b: 46 };

  function scales() {
    var xExt = BT.fieldExtent(state.x), yExt = BT.fieldExtent(state.y);
    var w = api.w - PAD.l - PAD.r, hh = api.h - PAD.t - PAD.b;
    return {
      xExt: xExt, yExt: yExt,
      sx: function (v) { return PAD.l + ((v - xExt.lo) / (xExt.hi - xExt.lo)) * w; },
      sy: function (v) { return PAD.t + hh - ((v - yExt.lo) / (yExt.hi - yExt.lo)) * hh; },
      w: w, h: hh
    };
  }

  function rebuild() {
    pts = [];
    D.topics.forEach(function (t, i) {
      var x = BT.fieldValue(t, state.x), y = BT.fieldValue(t, state.y);
      if (x == null || y == null || isNaN(x) || isNaN(y)) return;
      pts.push({ i: i, t: t, x: x, y: y });
    });
  }

  function colorOf(t) {
    if (state.color === "horizon") return BT.horizonColor(t.horizon);
    if (state.color === "signal") return BT.signalColor(t.signal_class);
    if (state.color === "quadrant") {
      return t.fit_quadrant === "act" ? BT.cssVar("--h2")
        : t.fit_quadrant === "watch" ? BT.cssVar("--noise")
        : t.fit_quadrant === "on-strategy, no right-to-play" ? BT.cssVar("--accent")
        : BT.cssVar("--h1");
    }
    if (state.color === "shortlist") {
      return t.rank && t.rank <= D.shortlist_size ? BT.cssVar("--accent") : BT.cssVar("--unassigned");
    }
    var v = BT.fieldValue(t, state.color);
    if (v == null) return BT.cssVar("--unassigned");
    var ext = BT.fieldExtent(state.color);
    return BT.ramp((v - ext.lo) / (ext.hi - ext.lo));
  }

  function radiusOf(t) {
    if (state.size === "none") return 6;
    var v = BT.fieldValue(t, state.size);
    if (v == null) return 4;
    var ext = BT.fieldExtent(state.size);
    /* Area, not radius, tracks the value: a circle whose radius doubles looks
       four times bigger, and reading it as twice the value is the classic
       bubble-chart lie. */
    var frac = Math.max(0, Math.min(1, (v - ext.lo) / (ext.hi - ext.lo)));
    return Math.sqrt(16 + frac * 300);
  }

  /* ---------------------------------------------------------------- draw */

  var raf = null;
  function draw() {
    if (raf) return;
    raf = requestAnimationFrame(function () { raf = null; render(); });
  }

  function render() {
    if (!api) return;
    var ctx = api.ctx, s = scales();
    ctx.clearRect(0, 0, api.w, api.h);
    drawGuides(ctx, s);
    drawAxes(ctx, s);

    pts.forEach(function (p) {
      var cx = s.sx(p.x), cy = s.sy(p.y), r = radiusOf(p.t);
      ctx.beginPath();
      ctx.arc(cx, cy, r, 0, Math.PI * 2);
      ctx.fillStyle = colorOf(p.t);
      ctx.globalAlpha = selected >= 0 && selected !== p.i ? 0.28 : 0.72;
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.lineWidth = selected === p.i ? 2.5 : 1;
      ctx.strokeStyle = selected === p.i ? BT.cssVar("--fg") : BT.cssVar("--surface");
      ctx.stroke();
    });

    if (state.labels) drawLabels(ctx, s);
  }

  function drawAxes(ctx, s) {
    var fg = BT.cssVar("--faint"), line = BT.cssVar("--line");
    ctx.strokeStyle = line;
    ctx.lineWidth = 1;
    ctx.font = "10px " + BT.cssVar("--sans").replace(/"/g, "");
    ctx.fillStyle = fg;

    for (var i = 0; i <= 4; i++) {
      var yv = s.yExt.lo + (s.yExt.hi - s.yExt.lo) * (i / 4);
      var yy = Math.round(s.sy(yv)) + 0.5;
      ctx.beginPath();
      ctx.moveTo(PAD.l, yy);
      ctx.lineTo(api.w - PAD.r, yy);
      ctx.stroke();
      ctx.textAlign = "right";
      ctx.textBaseline = "middle";
      ctx.fillText(BT.fieldFormat(state.y, yv), PAD.l - 8, yy);

      var xv = s.xExt.lo + (s.xExt.hi - s.xExt.lo) * (i / 4);
      var xx = Math.round(s.sx(xv)) + 0.5;
      ctx.textAlign = "center";
      ctx.textBaseline = "top";
      ctx.fillText(BT.fieldFormat(state.x, xv), xx, api.h - PAD.b + 8);
    }

    ctx.fillStyle = BT.cssVar("--muted");
    ctx.font = "600 11.5px " + BT.cssVar("--sans").replace(/"/g, "");
    ctx.textAlign = "center";
    ctx.fillText((BT.field(state.x) || {}).label || state.x, PAD.l + s.w / 2, api.h - 12);
    ctx.save();
    ctx.translate(15, PAD.t + s.h / 2);
    ctx.rotate(-Math.PI / 2);
    ctx.fillText((BT.field(state.y) || {}).label || state.y, 0, 0);
    ctx.restore();
    ctx.textAlign = "start";
  }

  function median(values) {
    var v = values.slice().sort(function (a, b) { return a - b; });
    if (!v.length) return null;
    var m = Math.floor(v.length / 2);
    return v.length % 2 ? v[m] : (v[m - 1] + v[m]) / 2;
  }

  function drawGuides(ctx, s) {
    var preset = PRESETS[state.preset];
    if (!preset || !preset.guides) return;

    if (preset.guides === "median") {
      var mx = median(pts.map(function (p) { return p.x; }));
      var my = median(pts.map(function (p) { return p.y; }));
      if (mx == null || my == null) return;
      ctx.save();
      ctx.strokeStyle = BT.cssVar("--line-strong");
      ctx.setLineDash([4, 4]);
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(s.sx(mx), PAD.t);
      ctx.lineTo(s.sx(mx), api.h - PAD.b);
      ctx.moveTo(PAD.l, s.sy(my));
      ctx.lineTo(api.w - PAD.r, s.sy(my));
      ctx.stroke();
      ctx.restore();
      ctx.fillStyle = BT.cssVar("--faint");
      ctx.font = "10px " + BT.cssVar("--sans").replace(/"/g, "");
      ctx.fillText("median", s.sx(mx) + 5, PAD.t + 11);

      /* Anchored in each quadrant's outer corner, not its middle: the middle
         is where the topics are, and a caption printed over the data is a
         caption competing with it. */
      if (state.preset === "fit") {
        quadLabel(ctx, api.w - PAD.r, PAD.t, "right", "top", "act");
        quadLabel(ctx, PAD.l, PAD.t, "left", "top", "on-strategy, no right-to-play");
        quadLabel(ctx, api.w - PAD.r, api.h - PAD.b, "right", "bottom", "capability looking for a problem");
        quadLabel(ctx, PAD.l, api.h - PAD.b, "left", "bottom", "watch");
      } else if (state.preset === "weak") {
        quadLabel(ctx, PAD.l, PAD.t, "left", "top", "weak — the target");
        quadLabel(ctx, api.w - PAD.r, PAD.t, "right", "top", "strong");
        quadLabel(ctx, PAD.l, api.h - PAD.b, "left", "bottom", "noise");
        quadLabel(ctx, api.w - PAD.r, api.h - PAD.b, "right", "bottom", "latent");
      }
      return;
    }

    if (preset.guides === "horizons") {
      var th = D.method.three_horizons || {};
      var bands = [
        [s.xExt.lo, Number(th.h2_max_maturity), "H3"],
        [Number(th.h2_max_maturity), Number(th.h1_max_maturity), "H2"],
        [Number(th.h1_max_maturity), s.xExt.hi, "H1"]
      ];
      bands.forEach(function (b) {
        var x0 = s.sx(Math.max(b[0], s.xExt.lo)), x1 = s.sx(Math.min(b[1], s.xExt.hi));
        if (x1 <= x0) return;
        ctx.fillStyle = BT.horizonColor(b[2]);
        ctx.globalAlpha = 0.09;
        ctx.fillRect(x0, PAD.t, x1 - x0, s.h);
        ctx.globalAlpha = 1;
        ctx.fillStyle = BT.horizonColor(b[2]);
        ctx.font = "600 11px " + BT.cssVar("--sans").replace(/"/g, "");
        ctx.textAlign = "center";
        ctx.fillText(b[2], (x0 + x1) / 2, PAD.t + 13);
        ctx.textAlign = "start";
      });
    }
  }

  function quadLabel(ctx, x, y, hAlign, vAlign, text) {
    ctx.save();
    ctx.fillStyle = BT.cssVar("--faint");
    ctx.font = "600 10.5px " + BT.cssVar("--sans").replace(/"/g, "");
    ctx.textAlign = hAlign === "right" ? "right" : "left";
    ctx.textBaseline = "alphabetic";
    ctx.globalAlpha = 0.75;
    ctx.fillText(text, x + (hAlign === "right" ? -6 : 6), y + (vAlign === "top" ? 13 : -6));
    ctx.restore();
    ctx.textAlign = "start";
  }

  /* Only the topics with room, largest circle first — the same rule the map
     uses, for the same reason. */
  function drawLabels(ctx, s) {
    var placed = [];
    ctx.font = "600 10.5px " + BT.cssVar("--sans").replace(/"/g, "");
    ctx.textBaseline = "middle";
    var ordered = pts.slice().sort(function (a, b) { return radiusOf(b.t) - radiusOf(a.t); });
    for (var k = 0; k < ordered.length && placed.length < 26; k++) {
      var p = ordered[k];
      var r = radiusOf(p.t);
      var cx = s.sx(p.x) + r + 5, cy = s.sy(p.y);
      var text = p.t.short;
      var w = ctx.measureText(text).width;
      if (cx + w > api.w - PAD.r) { cx = s.sx(p.x) - r - 5 - w; }
      if (cx < PAD.l) continue;
      var box = [cx - 2, cy - 7, w + 4, 14];
      var clash = false;
      for (var j = 0; j < placed.length; j++) {
        var q = placed[j];
        if (box[0] < q[0] + q[2] && box[0] + box[2] > q[0] &&
            box[1] < q[1] + q[3] && box[1] + box[3] > q[1]) { clash = true; break; }
      }
      if (clash) continue;
      placed.push(box);
      ctx.fillStyle = BT.cssVar("--muted");
      ctx.globalAlpha = selected >= 0 && selected !== p.i ? 0.4 : 1;
      ctx.fillText(text, cx, cy);
      ctx.globalAlpha = 1;
    }
  }

  /* ------------------------------------------------------------ picking */

  function pick(sx, sy) {
    var s = scales(), best = -1, bestD = Infinity;
    pts.forEach(function (p) {
      var dx = s.sx(p.x) - sx, dy = s.sy(p.y) - sy;
      var d = dx * dx + dy * dy;
      var r = radiusOf(p.t) + 4;
      if (d < r * r && d < bestD) { bestD = d; best = p.i; }
    });
    return best;
  }

  /* ------------------------------------------------------------ toolbar */

  function buildToolbar() {
    BT.clear(toolbar);

    toolbar.appendChild(h("div", { class: "field" }, [
      h("label", { text: "Preset" }),
      h("div", { class: "chips radio" }, Object.keys(PRESETS).map(function (k) {
        return h("button", {
          class: "chip", type: "button", "aria-pressed": String(state.preset === k),
          onclick: function () { applyPreset(k); },
          text: PRESETS[k].label
        });
      }))
    ]));

    toolbar.appendChild(axisSelect("X axis", "x"));
    toolbar.appendChild(axisSelect("Y axis", "y"));

    var colorOpts = [["horizon", "Three Horizons"], ["signal", "Signal class"],
      ["quadrant", "2×2 placement"], ["shortlist", "Shortlisted"]]
      .concat(["emergence_score", "strategic_fit", "asset_leverage", "opportunity_index"]
        .map(function (k) { return [k, (BT.field(k) || {}).label || k]; }));
    var csel = h("select", { "aria-label": "Colour by", onchange: function () {
      state.color = csel.value; draw(); buildLegend();
    } }, colorOpts.map(function (o) {
      return h("option", { value: o[0], selected: o[0] === state.color, text: o[1] });
    }));
    toolbar.appendChild(h("div", { class: "field" }, [h("label", { text: "Colour" }), csel]));

    var sizeOpts = [["none", "Uniform"]].concat(
      ["document_count", "emergence_score", "burst_weight", "opportunity_index"]
        .map(function (k) { return [k, (BT.field(k) || {}).label || k]; }));
    var ssel = h("select", { "aria-label": "Size by", onchange: function () {
      state.size = ssel.value; draw();
    } }, sizeOpts.map(function (o) {
      return h("option", { value: o[0], selected: o[0] === state.size, text: o[1] });
    }));
    toolbar.appendChild(h("div", { class: "field" }, [h("label", { text: "Size" }), ssel]));

    toolbar.appendChild(h("div", { class: "field" }, [
      h("label", { html: "&nbsp;" }),
      h("button", { class: "btn", type: "button", "aria-pressed": String(state.labels),
        onclick: function () { state.labels = !state.labels; buildToolbar(); draw(); },
        text: "Labels" })
    ]));
    buildLegend();
  }

  function axisSelect(label, which) {
    var sel = h("select", { "aria-label": label, onchange: function () {
      state[which] = sel.value;
      state.preset = matchPreset();
      rebuild(); buildToolbar(); draw(); updateNote();
    } }, BT.FIELDS.map(function (f) {
      return h("option", { value: f.key, selected: f.key === state[which], text: f.label });
    }));
    return h("div", { class: "field" }, [h("label", { text: label }), sel]);
  }

  function matchPreset() {
    var found = null;
    Object.keys(PRESETS).forEach(function (k) {
      if (PRESETS[k].x === state.x && PRESETS[k].y === state.y) found = k;
    });
    return found;
  }

  function applyPreset(k) {
    var p = PRESETS[k];
    state.preset = k;
    state.x = p.x; state.y = p.y;
    state.color = p.color;
    rebuild(); buildToolbar(); updateNote(); draw();
  }

  function buildLegend() {
    var box = document.getElementById("scoreStats");
    if (!box) return;
    BT.clear(box);
    box.appendChild(h("div", null, [
      h("b", { text: fmt.int(pts.length) }), " of " + fmt.int(D.topics.length) + " topics plotted"
    ]));
    if (pts.length < D.topics.length) {
      box.appendChild(h("div", { class: "note", text:
        (D.topics.length - pts.length) + " have no value on one of these axes and are omitted "
        + "rather than drawn at zero." }));
    }

    var entries = null;
    if (state.color === "horizon") {
      entries = ["H1", "H2", "H3"].map(function (k) {
        return { c: BT.horizonColor(k), l: k };
      });
    } else if (state.color === "signal") {
      entries = ["weak", "strong", "latent", "noise"].map(function (k) {
        return { c: BT.signalColor(k), l: k };
      });
    } else if (state.color === "quadrant") {
      entries = [{ c: BT.cssVar("--h2"), l: "act" },
        { c: BT.cssVar("--accent"), l: "on-strategy" },
        { c: BT.cssVar("--h1"), l: "capability" },
        { c: BT.cssVar("--noise"), l: "watch" }];
    } else if (state.color === "shortlist") {
      entries = [{ c: BT.cssVar("--accent"), l: "shortlisted" },
        { c: BT.cssVar("--unassigned"), l: "not shortlisted" }];
    }
    if (entries) {
      box.appendChild(h("div", { class: "legendrow" }, entries.map(function (e) {
        return h("span", null, [h("span", { class: "swatch", style: "background:" + e.c }), e.l]);
      })));
    }
  }

  function updateNote() {
    var box = document.getElementById("scoreNote");
    if (!box) return;
    var preset = PRESETS[state.preset];
    var fx = BT.field(state.x), fy = BT.field(state.y);
    BT.clear(box);
    if (preset) {
      box.appendChild(h("p", { style: "margin:0 0 8px" }, [
        h("strong", { text: preset.label + ". " }), preset.note
      ]));
    }
    box.appendChild(h("p", { class: "note", style: "margin:0" }, [
      h("b", { text: (fx || {}).label || state.x }), " — " + ((fx || {}).why || "")
    ]));
    box.appendChild(h("p", { class: "note", style: "margin:6px 0 0" }, [
      h("b", { text: (fy || {}).label || state.y }), " — " + ((fy || {}).why || "")
    ]));
  }

  /* ---------------------------------------------------------- inspector */

  function showPlaceholder() {
    BT.clear(inspector);
    var box = h("div", { class: "pad" });
    box.appendChild(h("h4", { text: "What you are looking at" }));
    box.appendChild(h("div", { id: "scoreNote", style: "margin-top:8px;font-size:13px" }));
    box.appendChild(h("p", { class: "hint", style: "margin-top:16px", text:
      "Click any circle for the topic behind it. Both axes and the colour and size channels "
      + "can be set to any stored score — the presets are the four the method itself reasons "
      + "about." }));
    inspector.appendChild(box);
    updateNote();
  }

  function showTopic(i) {
    var t = D.topics[i];
    BT.clear(inspector);
    var box = h("div", { class: "pad" });
    box.appendChild(h("div", { style: "display:flex;gap:6px;flex-wrap:wrap;margin-bottom:6px" }, [
      t.rank ? h("span", { class: "tag t-muted", text: "#" + t.rank }) : null,
      BT.horizonTag(t.horizon), BT.signalTag(t.signal_class)
    ]));
    box.appendChild(h("h3", { text: t.label }));
    box.appendChild(h("p", { class: "note", style: "margin-top:4px",
      text: fmt.int(t.document_count) + " documents · " + t.first_slice + "–" + t.last_slice }));

    box.appendChild(h("h4", { style: "margin-top:14px", text: "On these axes" }));
    box.appendChild(BT.scoreBar(state.x, BT.fieldValue(t, state.x)));
    box.appendChild(BT.scoreBar(state.y, BT.fieldValue(t, state.y)));

    box.appendChild(h("h4", { style: "margin-top:16px", text: "Everything else" }));
    ["composite_rank_score", "emergence_score", "strategic_fit", "asset_leverage",
     "opportunity_index"].forEach(function (k) {
      if (k === state.x || k === state.y) return;
      box.appendChild(BT.scoreBar(k, BT.fieldValue(t, k), { why: false }));
    });

    box.appendChild(h("dl", { class: "kv" }, [
      h("dt", { text: "2×2 placement" }),
      h("dd", null, [t.fit_quadrant,
        h("div", { class: "note", text: BT.QUADRANT_TEXT[t.fit_quadrant] || "" })]),
      h("dt", { text: "Closest objective" }), h("dd", { text: t.best_objective || "—" }),
      h("dt", { text: "Closest asset" }), h("dd", { text: t.best_asset || "—" })
    ]));

    box.appendChild(h("div", { class: "rowactions", style: "margin-top:14px" }, [
      h("button", { class: "btn sm", type: "button",
        onclick: function () { BT.go("topics", { topic: t.id }); }, text: "Full detail →" }),
      h("button", { class: "btn sm", type: "button",
        onclick: function () { BT.go("map", { topic: t.id }); }, text: "On the map →" }),
      t.evidence_url ? h("a", { class: "btn sm", href: t.evidence_url, target: "_blank",
        rel: "noopener noreferrer", text: "Evidence card →" }) : null
    ]));
    inspector.appendChild(box);
  }

  function moveTooltip(i, e) {
    if (i < 0) { tooltip.style.display = "none"; return; }
    var t = D.topics[i];
    BT.clear(tooltip);
    tooltip.appendChild(h("div", { class: "tt", text: t.short }));
    tooltip.appendChild(h("div", { class: "tm", text:
      (BT.field(state.x) || {}).label + ": " + BT.fieldFormat(state.x, BT.fieldValue(t, state.x)) }));
    tooltip.appendChild(h("div", { class: "tm", text:
      (BT.field(state.y) || {}).label + ": " + BT.fieldFormat(state.y, BT.fieldValue(t, state.y)) }));
    tooltip.style.display = "block";
    var wrap = cv.parentElement.getBoundingClientRect();
    var left = e.clientX - wrap.left + 15, top = e.clientY - wrap.top + 15;
    if (left + tooltip.offsetWidth > wrap.width - 6) left = e.clientX - wrap.left - tooltip.offsetWidth - 13;
    if (top + tooltip.offsetHeight > wrap.height - 6) top = e.clientY - wrap.top - tooltip.offsetHeight - 13;
    tooltip.style.left = Math.max(4, left) + "px";
    tooltip.style.top = Math.max(4, top) + "px";
  }

  /* ------------------------------------------------------------------ api */

  return {
    mount: function () {
      toolbar = document.getElementById("scoresToolbar");
      inspector = document.getElementById("scoreInspector");
      if (!mounted) {
        mounted = true;
        cv = document.getElementById("scoreCv");
        tooltip = document.getElementById("scoreTooltip");
        api = BT.makeCanvas(cv);
        cv.style.cursor = "default";

        cv.addEventListener("pointermove", function (e) {
          var rect = cv.getBoundingClientRect();
          moveTooltip(pick(e.clientX - rect.left, e.clientY - rect.top), e);
        });
        cv.addEventListener("pointerleave", function () { tooltip.style.display = "none"; });
        cv.addEventListener("click", function (e) {
          var rect = cv.getBoundingClientRect();
          selected = pick(e.clientX - rect.left, e.clientY - rect.top);
          if (selected < 0) showPlaceholder(); else showTopic(selected);
          draw();
        });
        window.addEventListener("resize", function () {
          if (document.getElementById("view-scores").classList.contains("active")) {
            api.resize(); draw();
          }
        });
      }
      /* rebuild() first: buildToolbar() draws the legend, and the legend
         counts the plotted points. */
      rebuild();
      buildToolbar();
      showPlaceholder();
      api.resize();
      draw();
    },

    resize: function () { if (mounted) { api.resize(); draw(); } },
    refresh: function () { if (mounted) { buildToolbar(); draw(); } },

    selectTopic: function (topicId) {
      D.topics.forEach(function (t, i) { if (t.id === topicId) selected = i; });
      if (selected >= 0) showTopic(selected);
      draw();
    }
  };
})();
