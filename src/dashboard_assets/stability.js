/* ==========================================================================
   stability.js — how much of the ranking is the corpus, and how much is the
   three numbers in synthesis.rank_weights?

   The shortlist is ordered by a weighted sum of three percentile-ranked axes.
   The weights are a judgement call that nobody has validated, and on the
   2026-09-14 run the composite scores are very nearly tied — a median gap of
   0.005 between neighbours. Both facts are in PROJECT_STATE.md; neither is
   visible in a ranked table, which shows an order and says nothing about how
   much of it survives a defensible change of mind.

   This view sweeps every weight triple on a lattice over their simplex and
   draws which topic wins where. A large single-coloured region means the
   ordering is a property of the corpus. A mosaic means rank 1 is a property
   of the config.

   WHAT IS COMPUTED WHERE, AND WHY. Everything quantitative — the winner at
   each lattice point, each topic's share of the simplex, its best and worst
   rank anywhere in it — comes from src/dashboard.py, computed once through
   the production ranking functions. This file only *draws* those arrays. The
   one thing it computes itself is the ordering at a single arbitrary weight
   triple, for the probe the reader drags; that is the same weighted sum and
   it is checked against the shipped ordering at the configured weights on
   mount, so the two implementations cannot quietly disagree.
   ========================================================================== */

BT.views.stability = (function () {
  var h = BT.h, fmt = BT.fmt, D = BT.D;
  var S = D.stability || { available: false };

  var mounted = false, api = null, cv = null, tooltip = null;
  var page = null, live = null, statrow = null;
  var cellAt = null, regions = [], agreed = true;
  var field = null, fieldKey = null;

  /* The probe: the weight triple the live panel is ranked at. null means "the
     configured weights", which is the state the view opens in — a reader
     should see the run as published before they see it perturbed. */
  var state = { probe: null, hover: null };

  var AXES = (S.axes || []).map(function (a) { return a.name; });
  var FIELDS = (S.axes || []).map(function (a) { return a.field; });

  function axisLabel(i) {
    var f = BT.field(FIELDS[i]);
    return f ? f.label : AXES[i];
  }

  function configured() {
    return AXES.map(function (name) { return Number((S.weights || {})[name] || 0); });
  }

  function current() { return state.hover || state.probe || configured(); }

  function isConfigured(w) {
    var c = configured();
    return Math.abs(w[0] - c[0]) < 1e-9 && Math.abs(w[1] - c[1]) < 1e-9
      && Math.abs(w[2] - c[2]) < 1e-9;
  }

  /* ------------------------------------------------------------- ordering */

  /* The composite at one weight triple, from the same percentile-ranked axes
     Stage 5 used. Returns topic indices, best first. */
  function orderAt(w) {
    var inputs = S.inputs, n = inputs.length, scored = new Array(n);
    for (var t = 0; t < n; t++) {
      var row = inputs[t], s = 0;
      for (var a = 0; a < AXES.length; a++) s += w[a] * (row[AXES[a]] || 0);
      scored[t] = { t: t, score: s };
    }
    scored.sort(function (x, y) { return y.score - x.score || x.t - y.t; });
    return scored;
  }

  /* --------------------------------------------------------------- lattice */

  function buildLookup() {
    var R = S.resolution;
    cellAt = new Int32Array((R + 1) * (R + 1));
    for (var g = 0; g < cellAt.length; g++) cellAt[g] = -1;
    for (var c = 0; c < S.winner.length; c++) {
      cellAt[S.i[c] * (R + 1) + S.j[c]] = S.winner[c];
    }

    /* Region centroids, so each winner can be labelled inside its own
       territory rather than only in a legend. Barycentric means are exact:
       the mean of a set of weight triples is itself a weight triple. */
    var acc = {};
    for (var k = 0; k < S.winner.length; k++) {
      var t = S.winner[k], e = acc[t] || (acc[t] = { t: t, n: 0, i: 0, j: 0 });
      e.n++; e.i += S.i[k]; e.j += S.j[k];
    }
    regions = Object.keys(acc).map(function (key) {
      var e = acc[key];
      return { t: e.t, share: e.n / S.cells, i: e.i / e.n, j: e.j / e.n };
    }).sort(function (a, b) { return b.share - a.share; });
  }

  function winnerAtWeights(w) {
    var R = S.resolution;
    var i = Math.round(w[0] * R), j = Math.round(w[1] * R);
    i = Math.max(0, Math.min(R, i));
    j = Math.max(0, Math.min(R - i, j));
    var v = cellAt[i * (R + 1) + j];
    return v == null ? -1 : v;
  }

  /* -------------------------------------------------------------- geometry

     Barycentric (a, b, c) over (emergence, strategic fit, asset leverage),
     drawn as an equilateral triangle with the first axis at the apex. */

  var PAD = { t: 40, r: 80, b: 48, l: 80 };

  function frame() {
    var w = Math.max(60, api.w - PAD.l - PAD.r);
    var hh = Math.max(50, api.h - PAD.t - PAD.b);
    var side = Math.min(w, hh / 0.8660254);
    var height = side * 0.8660254;
    var left = PAD.l + (w - side) / 2;
    var top = PAD.t + (hh - height) / 2;
    return { side: side, height: height, left: left, top: top };
  }

  function toScreen(a, b, c, f) {
    /* x runs left (b = 1) to right (c = 1); y runs bottom (a = 0) to apex. */
    var x = 0.5 * a + c;
    return [f.left + x * f.side, f.top + (1 - a) * f.height];
  }

  function latticeToScreen(i, j, f) {
    var R = S.resolution;
    return toScreen(i / R, j / R, (R - i - j) / R, f);
  }

  function fromScreen(sx, sy, f) {
    var a = 1 - (sy - f.top) / f.height;
    var c = (sx - f.left) / f.side - 0.5 * a;
    var b = 1 - a - c;
    /* Clamp into the simplex rather than refusing a pointer just outside it:
       a reader dragging along an edge should slide along it, not lose grip. */
    var w = [Math.max(0, a), Math.max(0, b), Math.max(0, c)];
    var sum = w[0] + w[1] + w[2];
    return sum > 0 ? [w[0] / sum, w[1] / sum, w[2] / sum] : configured();
  }

  /* ---------------------------------------------------------------- colour

     Topic colour, not region colour: the same BT.catColor a topic wears on
     the Map and in the Topics table, so a reader recognises it across views.
     Two winners could in principle draw the same hue — the ramp has twenty —
     which is why every region above a visible size is also labelled in place
     and listed in the legend. Identity never rests on the fill alone. */
  function colorOf(t) {
    return t < 0 ? BT.cssVar("--unassigned") : BT.catColor(t);
  }

  /* ----------------------------------------------------------------- draw */

  function draw() {
    if (!api) return;
    var ctx = api.ctx, f = frame();
    ctx.clearRect(0, 0, api.w, api.h);

    paintField(ctx, f);

    drawGuides(ctx, f);
    drawRegionLabels(ctx, f);
    drawCorners(ctx, f);

    var c = configured();
    drawMarker(ctx, f, c, false);
    var probe = state.hover || state.probe;
    if (probe && !isConfigured(probe)) drawMarker(ctx, f, probe, true);
  }

  /* Every pixel of the triangle takes the colour of the lattice point nearest
     to it, which draws each weight triple's true territory — the region of
     weightings closer to it than to any other sampled one. Tiling the lattice
     with little triangles instead leaves a serrated edge along every boundary
     that reads as noise in the data, and is noise in the rendering.

     Drawn once into an offscreen buffer and blitted afterwards: the field does
     not change when the probe moves, and re-deriving a million pixels on every
     pointermove would make the drag stutter for no gain. The buffer is keyed
     on size and theme, which are the only two things that invalidate it. */
  function paintField(ctx, f) {
    var dpr = api.canvas.width / Math.max(1, api.w);
    var key = [f.left, f.top, f.side, f.height, dpr, BT.theme()].join(":");
    if (key !== fieldKey) {
      fieldKey = key;
      field = buildField(f, dpr);
    }
    if (!field) return;
    ctx.drawImage(field.canvas, field.x / dpr, field.y / dpr,
      field.canvas.width / dpr, field.canvas.height / dpr);
  }

  function buildField(f, dpr) {
    var R = S.resolution;
    var left = f.left * dpr, top = f.top * dpr;
    var side = f.side * dpr, height = f.height * dpr;
    var x0 = Math.floor(left), y0 = Math.floor(top);
    var w = Math.ceil(left + side) - x0 + 1, hh = Math.ceil(top + height) - y0 + 1;
    if (w < 1 || hh < 1) return null;

    var off = document.createElement("canvas");
    off.width = w; off.height = hh;
    var octx = off.getContext("2d");
    var img = octx.createImageData(w, hh);
    var data = img.data;

    /* One RGB triple per topic that owns any territory, resolved once. */
    var palette = {};
    (S.winner || []).forEach(function (t) {
      if (palette[t] === undefined) palette[t] = t < 0 ? [128, 128, 128] : BT.catRGB(t);
    });

    for (var py = 0; py < hh; py++) {
      var a = 1 - (y0 + py + 0.5 - top) / height;
      for (var px = 0; px < w; px++) {
        var c = (x0 + px + 0.5 - left) / side - 0.5 * a;
        var b = 1 - a - c;
        if (a < 0 || b < 0 || c < 0) continue;   // outside the simplex

        /* Nearest lattice point. Rounding the three coordinates separately can
           leave the triple summing to R ± 1, so the coordinate that was
           rounded furthest is the one corrected — the standard barycentric
           rounding, and the only one that cannot move a pixel across a
           boundary it does not actually cross. */
        var fa = a * R, fb = b * R, fc = c * R;
        var ri = Math.round(fa), rj = Math.round(fb), rk = Math.round(fc);
        var d = ri + rj + rk - R;
        if (d !== 0) {
          var ea = Math.abs(ri - fa), eb = Math.abs(rj - fb), ec = Math.abs(rk - fc);
          if (ea >= eb && ea >= ec) ri -= d;
          else if (eb >= ec) rj -= d;
          else rk -= d;
        }
        if (ri < 0) ri = 0;
        if (rj < 0) rj = 0;
        if (ri + rj > R) rj = R - ri;

        var t = cellAt[ri * (R + 1) + rj];
        var rgb = palette[t] || (palette[t] = t < 0 ? [128, 128, 128] : BT.catRGB(t));
        var o = (py * w + px) * 4;
        data[o] = rgb[0]; data[o + 1] = rgb[1]; data[o + 2] = rgb[2]; data[o + 3] = 255;
      }
    }
    octx.putImageData(img, 0, 0);
    return { canvas: off, x: x0, y: y0 };
  }

  /* Decile guides, so a reader can read a weight off the picture instead of
     only off the probe. Recessive by design — the regions are the data. */
  function drawGuides(ctx, f) {
    ctx.save();
    ctx.strokeStyle = BT.cssVar("--bg");
    ctx.globalAlpha = 0.22;
    ctx.lineWidth = 1;
    for (var s = 1; s < 10; s++) {
      var v = s / 10;
      line(ctx, f, [v, 1 - v, 0], [v, 0, 1 - v]);   // constant emergence
      line(ctx, f, [1 - v, v, 0], [0, v, 1 - v]);   // constant strategic fit
      line(ctx, f, [1 - v, 0, v], [0, 1 - v, v]);   // constant asset leverage
    }
    ctx.restore();

    ctx.save();
    ctx.strokeStyle = BT.cssVar("--line-strong");
    ctx.lineWidth = 1.25;
    ctx.beginPath();
    var apex = toScreen(1, 0, 0, f), bl = toScreen(0, 1, 0, f), br = toScreen(0, 0, 1, f);
    ctx.moveTo(apex[0], apex[1]);
    ctx.lineTo(bl[0], bl[1]);
    ctx.lineTo(br[0], br[1]);
    ctx.closePath();
    ctx.stroke();
    ctx.restore();
  }

  function line(ctx, f, w1, w2) {
    var p = toScreen(w1[0], w1[1], w1[2], f), q = toScreen(w2[0], w2[1], w2[2], f);
    ctx.beginPath();
    ctx.moveTo(p[0], p[1]);
    ctx.lineTo(q[0], q[1]);
    ctx.stroke();
  }

  function drawRegionLabels(ctx, f) {
    ctx.save();
    ctx.font = "600 11.5px " + BT.cssVar("--sans");
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    regions.forEach(function (r) {
      if (r.share < 0.035 || r.t < 0) return;
      var topic = D.topics[r.t];
      if (!topic) return;
      var s = latticeToScreen(r.i, r.j, f);
      var text = topic.short;
      var w = ctx.measureText(text).width;
      ctx.fillStyle = BT.cssVar("--surface");
      ctx.globalAlpha = 0.82;
      roundRect(ctx, s[0] - w / 2 - 6, s[1] - 9, w + 12, 18, 4);
      ctx.fill();
      ctx.globalAlpha = 1;
      ctx.fillStyle = BT.cssVar("--fg");
      ctx.fillText(text, s[0], s[1]);
    });
    ctx.restore();
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

  /* A corner label names its axis and shows the weight the probe currently
     gives it, so the picture and the panel never disagree about where the
     reader is. Each is nudged back inside the canvas if the triangle sits
     close enough to an edge to push it out — a clipped axis name is the one
     thing on this view that would leave a reader guessing. */
  function drawCorners(ctx, f) {
    var w = current();
    var corners = [
      { p: toScreen(1, 0, 0, f), label: axisLabel(0), v: w[0], align: "center", dy: -30 },
      { p: toScreen(0, 1, 0, f), label: axisLabel(1), v: w[1], align: "right", dy: 24 },
      { p: toScreen(0, 0, 1, f), label: axisLabel(2), v: w[2], align: "left", dy: 24 }
    ];
    ctx.save();
    ctx.textBaseline = "middle";
    corners.forEach(function (c) {
      var value = "weight " + c.v.toFixed(2);
      ctx.font = "600 12px " + BT.cssVar("--sans");
      var wide = ctx.measureText(c.label).width;
      ctx.font = "11.5px " + BT.cssVar("--mono");
      wide = Math.max(wide, ctx.measureText(value).width);

      var x = c.p[0] + (c.align === "right" ? -10 : c.align === "left" ? 10 : 0);
      if (c.align === "right") x = Math.max(x, wide + 4);
      else if (c.align === "left") x = Math.min(x, api.w - wide - 4);
      else x = Math.min(Math.max(x, wide / 2 + 4), api.w - wide / 2 - 4);

      ctx.textAlign = c.align;
      ctx.fillStyle = BT.cssVar("--fg");
      ctx.font = "600 12px " + BT.cssVar("--sans");
      ctx.fillText(c.label, x, c.p[1] + c.dy);
      ctx.fillStyle = BT.cssVar("--muted");
      ctx.font = "11.5px " + BT.cssVar("--mono");
      ctx.fillText(value, x, c.p[1] + c.dy + 15);
    });
    ctx.restore();
  }

  function drawMarker(ctx, f, w, isProbe) {
    var s = toScreen(w[0], w[1], w[2], f);
    ctx.save();
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.arc(s[0], s[1], isProbe ? 7 : 6, 0, Math.PI * 2);
    ctx.strokeStyle = BT.cssVar("--surface");
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(s[0], s[1], isProbe ? 6 : 5, 0, Math.PI * 2);
    ctx.strokeStyle = BT.cssVar("--fg");
    ctx.stroke();
    if (!isProbe) {
      ctx.fillStyle = BT.cssVar("--fg");
      ctx.beginPath();
      ctx.arc(s[0], s[1], 2.2, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.restore();
  }

  /* ------------------------------------------------------------ live panel */

  function renderLive() {
    if (!live) return;
    BT.clear(live);
    var w = current(), order = orderAt(w), top = S.top_n;
    var atConfig = isConfigured(w);

    var held = 0;
    order.slice(0, top).forEach(function (row) {
      if (D.topics[row.t] && D.topics[row.t].rank_at_config <= top) held++;
    });

    live.appendChild(h("div", { class: "livehead" }, [
      h("div", { class: "lbl", text: atConfig ? "At the configured weights" : "At the probe" }),
      h("div", { class: "wts" }, AXES.map(function (name, a) {
        return h("span", { class: "wt" }, [
          h("b", { text: w[a].toFixed(2) }),
          h("span", { text: axisLabel(a) })
        ]);
      })),
      atConfig
        ? h("div", { class: "note", text: "This is the ranking the run published." })
        : h("div", { class: "note" }, [
          held + " of the published top " + top + " are still in the top " + top + " here. ",
          h("button", {
            class: "btn sm", type: "button", text: "Reset to configured",
            onclick: function () { state.probe = null; state.hover = null; sync(); }
          })
        ])
    ]));

    var rows = order.slice(0, top).map(function (row, i) {
      var t = D.topics[row.t];
      if (!t) return null;
      var was = t.rank_at_config;
      var delta = was - (i + 1);
      return h("tr", { class: "clickable", onclick: function () { BT.go("topics", { topic: t.id }); } }, [
        h("td", { class: "n", text: String(i + 1) }),
        h("td", { class: "truncate", title: t.label }, [
          h("span", { class: "swatch", style: "background:" + colorOf(row.t) }),
          h("span", { text: t.short })
        ]),
        h("td", { class: "n", text: fmt.score(row.score, 3) }),
        h("td", { class: "n" }, [
          delta === 0
            ? h("span", { class: "t-muted", text: "—" })
            : h("span", {
              class: delta > 0 ? "t-good" : "t-bad",
              title: "Published rank " + was,
              text: (delta > 0 ? "▲" : "▼") + Math.abs(delta)
            })
        ])
      ]);
    });

    live.appendChild(h("div", { class: "tablewrap" }, [
      h("table", null, [
        h("thead", {}, h("tr", {}, [
          h("th", { class: "n", style: "width:34px", text: "#" }),
          h("th", { style: "width:56%", text: "Topic" }),
          h("th", { class: "n", style: "width:84px", text: "Composite" }),
          h("th", {
            class: "n", style: "width:62px", text: "Move",
            title: "Places gained or lost against the rank this topic was published at."
          })
        ])),
        h("tbody", {}, rows)
      ])
    ]));
  }

  /* ------------------------------------------------------------ statistics */

  function renderStats() {
    if (!statrow) return;
    BT.clear(statrow);
    var top1 = regions.length ? regions[0] : null;
    var winners = (S.winners || []).length;
    var shortlist = D.topics.filter(function (t) { return t.rank_at_config <= S.top_n; });
    var localHeld = shortlist.filter(function (t) { return t.rank_stability_local >= 1; }).length;

    [
      {
        k: "Rank 1 holds", v: top1 ? fmt.pct(top1.share, 0) : "—",
        d: "of the weight simplex",
        title: "The share of all weight triples at which the topic ranked first "
          + "here is still first. Anything short of the whole simplex means rank 1 "
          + "was partly a choice of weights."
      },
      {
        k: "Topics that win somewhere", v: String(winners),
        d: "reach rank 1 at some weighting",
        title: "How many different topics take first place somewhere in the simplex. "
          + "One means the ordering is robust; several means first place is contested."
      },
      {
        k: "Shortlist holds locally", v: localHeld + " of " + shortlist.length,
        d: "within ±" + (S.neighbourhood / 2).toFixed(3) + " of the weights",
        title: "Topics in the published top " + S.top_n + " that stay in the top "
          + S.top_n + " across every weighting within an L1 distance of "
          + S.neighbourhood + " of the configured one (" + S.neighbourhood_cells
          + " lattice points). Membership can be robust while the order inside it is not."
      },
      {
        k: "Median rank gap", v: S.gap_median == null ? "—" : fmt.score(S.gap_median, 4),
        d: "between neighbouring topics",
        title: "The median difference in composite score between consecutively ranked "
          + "topics. The composite is a weighted sum of percentile ranks, so a gap of "
          + "0.005 is well under one rank position on one axis — the order is close to a tie."
      }
    ].forEach(function (s) {
      statrow.appendChild(h("div", { class: "stat", title: s.title }, [
        h("div", { class: "k", text: s.k }),
        h("div", { class: "v", text: s.v }),
        h("div", { class: "d", text: s.d })
      ]));
    });
  }

  /* ---------------------------------------------------------------- table */

  /* The micro-gauge from the Topics table: the figure stays the thing you read
     and the track makes the column scannable. A full scoreBar here would print
     the field name on every row of a column that already has a heading. */
  function shareBar(v) {
    var frac = v == null ? 0 : Math.max(0, Math.min(1, v));
    return h("span", {
      class: "bar n",
      title: v == null ? "—" : fmt.pct(v, 1) + " of the weight simplex keeps this topic "
        + "in the top " + S.top_n
    }, [
      h("span", { text: v == null ? "—" : fmt.pct(v, 0) }),
      h("u", null, [h("i", { style: "width:" + Math.max(2, frac * 100).toFixed(1) + "%" })])
    ]);
  }

  function rangeBar(t) {
    var n = D.topics.length;
    var lo = (t.rank_best - 1) / Math.max(1, n - 1);
    var hi = (t.rank_worst - 1) / Math.max(1, n - 1);
    var at = (t.rank_at_config - 1) / Math.max(1, n - 1);
    return h("div", {
      class: "rangebar",
      title: "Ranks " + t.rank_best + " to " + t.rank_worst + " across the simplex; "
        + t.rank_at_config + " at the configured weights"
    }, [
      h("u", {}, [
        h("i", { style: "left:" + (lo * 100).toFixed(1) + "%;right:" + ((1 - hi) * 100).toFixed(1) + "%" }),
        h("b", { style: "left:" + (at * 100).toFixed(1) + "%" })
      ]),
      h("span", { class: "ends", text: t.rank_best + "–" + t.rank_worst })
    ]);
  }

  function renderTable(into) {
    var sorted = D.topics.slice().sort(function (a, b) {
      return (a.rank_at_config || 1e9) - (b.rank_at_config || 1e9);
    });
    var scores = sorted.map(function (t) { return t.composite_rank_score; });

    var body = sorted.map(function (t, i) {
      var next = scores[i + 1];
      var gap = next == null || t.composite_rank_score == null
        ? null : t.composite_rank_score - next;
      var shortlisted = t.rank_at_config <= S.top_n;
      return h("tr", {
        class: "clickable" + (shortlisted ? " hi" : ""),
        onclick: function () { BT.go("topics", { topic: t.id }); }
      }, [
        h("td", { class: "n", text: String(t.rank_at_config) }),
        h("td", { class: "truncate", title: t.label }, [
          h("span", { class: "swatch", style: "background:" + BT.catColor(D.topics.indexOf(t)) }),
          h("span", { text: t.short })
        ]),
        h("td", {}, BT.horizonTag(t.horizon)),
        h("td", { class: "n", text: fmt.score(t.composite_rank_score, 3) }),
        h("td", {
          class: "n " + (gap != null && gap < 0.01 ? "t-warn" : "t-muted"),
          title: gap != null && gap < 0.01
            ? "Under 0.01 from the next topic — these two are effectively tied"
            : ""
        }, gap == null ? "—" : fmt.score(gap, 3)),
        h("td", { class: "n" }, shareBar(t.rank_stability)),
        h("td", {}, rangeBar(t))
      ]);
    });

    into.appendChild(h("div", { class: "tablewrap" }, [
      h("table", null, [
        h("thead", {}, h("tr", {}, [
          h("th", { class: "n", style: "width:58px", text: "Rank" }),
          h("th", { style: "width:34%", text: "Topic" }),
          h("th", { style: "width:52px", text: "H" }),
          h("th", { class: "n", style: "width:96px", text: "Composite" }),
          h("th", {
            class: "n", style: "width:96px", text: "Gap to next",
            title: "Difference in composite score from the topic ranked immediately below. "
              + "Amber marks a gap under 0.01 — closer than one rank position on one axis."
          }),
          h("th", { class: "n", style: "width:130px", text: "In top " + S.top_n }),
          h("th", { style: "width:210px", text: "Rank range across the simplex" })
        ])),
        h("tbody", {}, body)
      ])
    ]));
  }

  /* ---------------------------------------------------------------- mount */

  function sync() { draw(); renderLive(); }

  function pointerWeights(ev) {
    var rect = cv.getBoundingClientRect();
    return fromScreen(ev.clientX - rect.left, ev.clientY - rect.top, frame());
  }

  function showTooltip(ev, w) {
    var t = winnerAtWeights(w);
    var topic = t >= 0 ? D.topics[t] : null;
    BT.clear(tooltip);
    tooltip.appendChild(h("div", { class: "tt", text: topic ? topic.short : "—" }));
    tooltip.appendChild(h("div", { class: "tm", text: AXES.map(function (name, a) {
      return axisLabel(a).toLowerCase() + " " + w[a].toFixed(2);
    }).join(" · ") }));
    if (topic) {
      tooltip.appendChild(h("div", { class: "tm", style: "margin-top:3px",
        text: "ranks first here — published rank " + topic.rank_at_config }));
    }
    tooltip.style.display = "block";
    var wrap = cv.parentElement.getBoundingClientRect();
    var left = ev.clientX - wrap.left + 15, top = ev.clientY - wrap.top + 15;
    if (left + tooltip.offsetWidth > wrap.width - 6) left = ev.clientX - wrap.left - tooltip.offsetWidth - 13;
    if (top + tooltip.offsetHeight > wrap.height - 6) top = ev.clientY - wrap.top - tooltip.offsetHeight - 13;
    tooltip.style.left = Math.max(4, left) + "px";
    tooltip.style.top = Math.max(4, top) + "px";
  }

  function bind() {
    var dragging = false;

    cv.addEventListener("pointermove", function (ev) {
      var w = pointerWeights(ev);
      if (dragging) { state.probe = w; state.hover = null; sync(); }
      else { state.hover = w; sync(); }
      showTooltip(ev, w);
    });
    cv.addEventListener("pointerleave", function () {
      state.hover = null;
      tooltip.style.display = "none";
      sync();
    });
    cv.addEventListener("pointerdown", function (ev) {
      dragging = true;
      cv.setPointerCapture(ev.pointerId);
      state.probe = pointerWeights(ev);
      state.hover = null;
      sync();
    });
    cv.addEventListener("pointerup", function (ev) {
      dragging = false;
      try { cv.releasePointerCapture(ev.pointerId); } catch (e) { /* already gone */ }
    });
    cv.addEventListener("dblclick", function () { state.probe = null; sync(); });

    /* Keyboard equivalent: the probe moves one lattice step per key, which is
       the only way this view is usable without a pointer. */
    cv.addEventListener("keydown", function (ev) {
      var step = 1 / S.resolution;
      var w = (state.probe || configured()).slice();
      var moves = {
        ArrowUp: [0, -0.5, -0.5], ArrowDown: [0, 0.5, 0.5],
        ArrowLeft: [-0.5, 0.5, 0], ArrowRight: [-0.5, 0, 0.5]
      };
      var m = moves[ev.key];
      if (!m) {
        if (ev.key === "Escape") { state.probe = null; sync(); }
        return;
      }
      ev.preventDefault();
      for (var a = 0; a < 3; a++) w[a] = Math.max(0, w[a] + m[a] * 2 * step);
      var sum = w[0] + w[1] + w[2];
      state.probe = w.map(function (v) { return v / sum; });
      state.hover = null;
      sync();
    });
  }

  function unavailable() {
    page.appendChild(h("div", { class: "card" }, [
      h("h2", { text: "Rank stability" }),
      h("p", { class: "lede", text:
        "This run has fewer than two ranked topics, so there is no ordering to "
        + "perturb. The view appears when a run ranks more than one topic." })
    ]));
  }

  function mount() {
    if (mounted) return;
    mounted = true;
    page = document.getElementById("stabilityPage");
    BT.clear(page);

    if (!S.available) { unavailable(); return; }

    buildLookup();

    /* The self-check. The panel below recomputes the ordering in JavaScript;
       the arrays it is drawn on were computed in Python. If those two ever
       disagree at the configured weights, everything on this page is suspect
       and the page has to say so rather than look confident. */
    var jsOrder = orderAt(configured());
    agreed = jsOrder.every(function (row, i) {
      var t = D.topics[row.t];
      return t && t.rank_at_config === i + 1;
    });

    page.appendChild(h("h2", { text: "How much of the ranking is the weights?" }));
    page.appendChild(h("p", { class: "lede", text:
      "The shortlist is ordered by a weighted sum of three percentile-ranked axes, on "
      + "weights nobody has validated against a known past opportunity. Every weighting "
      + "that could have been chosen is somewhere in this triangle, coloured by the topic "
      + "that would rank first there. Drag anywhere to re-rank the run at those weights." }));

    statrow = h("div", { class: "statrow", style: "margin:16px 0" });
    page.appendChild(statrow);

    if (!agreed || !S.verified) {
      page.appendChild(h("div", { class: "callout warn" }, [
        h("strong", { text: "This page did not reproduce the run's own ranking. " }),
        !S.verified
          ? "The composite recomputed from the stored inputs differs from the stored "
            + "composite_rank_score by " + S.max_drift + ", which means the run was ranked "
            + "under weights other than the ones in the config this page was built with. "
          : "",
        !agreed
          ? "The ordering drawn here disagrees with the ordering computed alongside it. "
          : "",
        "Treat everything below as unverified and check pipeline_runs.config_snapshot."
      ]));
    }

    var box = h("div", { class: "simplexbox" }, [
      h("canvas", {
        id: "simplexCv", tabindex: "0", role: "img",
        "aria-label": "The simplex of rank weights, coloured by which topic ranks first "
          + "at each weighting. The same figures are in the table below."
      }),
      h("div", { class: "tooltip", id: "simplexTooltip", role: "status" })
    ]);

    live = h("div", { class: "livepanel" });

    var legend = h("div", { class: "legendrow" },
      (S.winners || []).map(function (wn) {
        var t = D.topics[wn.topic];
        return h("span", { title: t ? t.label : "" }, [
          h("span", { class: "swatch", style: "background:" + colorOf(wn.topic) }),
          h("span", { text: t ? t.short : "—" }),
          h("b", { class: "n", text: fmt.pct(wn.share, 1) })
        ]);
      }));

    page.appendChild(h("div", { class: "card" }, [
      h("div", { class: "simplexgrid" }, [
        h("div", {}, [
          box,
          legend,
          h("p", { class: "note", style: "margin-top:8px", text:
            "Each region is the set of weightings at which that topic ranks first. "
            + "Shares are over " + fmt.int(S.cells) + " weight triples; the ringed dot "
            + "is the configured weighting. Drag to move the probe, or focus the "
            + "triangle and use the arrow keys." })
        ]),
        live
      ])
    ]));

    page.appendChild(h("p", { class: "note", style: "margin-top:18px", text:
      "Rank range below is the best and worst position a topic reaches anywhere in the "
      + "triangle; the tick is where the published weights put it. A wide range on a "
      + "high-ranked topic means its place in the shortlist was bought by the weights." }));

    var tableCard = h("div", { class: "stack", style: "margin-top:12px" });
    renderTable(tableCard);
    page.appendChild(tableCard);

    cv = document.getElementById("simplexCv");
    tooltip = document.getElementById("simplexTooltip");
    api = BT.makeCanvas(cv);
    bind();
    resize();
    renderStats();
  }

  function resize() {
    if (!api) return;
    api.resize();
    sync();
  }

  function refresh() {
    if (!mounted || !api) return;
    resize();
  }

  return { mount: mount, resize: resize, refresh: refresh };
})();
