/* ==========================================================================
   topics.js — every topic and every score, in one sortable table.

   The map answers "what is near what". This answers the question an analyst
   actually arrives with: show me all of them, ordered by the thing I care
   about, and let me open the one that looks interesting.

   Two decisions worth stating. Numeric cells carry a micro-bar under the
   figure, so a column can be scanned for shape without becoming a chart. And
   an expanded row shows the arithmetic — the five Rotolo attributes with their
   weights, the index components, the time series, the member documents —
   because a score with no visible inputs is a score a reader can only take on
   trust, which is the opposite of what this project is for.
   ========================================================================== */

BT.views.topics = (function () {
  var h = BT.h, fmt = BT.fmt, D = BT.D;
  var page, toolbar, mounted = false, tbody;
  var openId = null;

  var state = {
    sort: "rank",
    dir: 1,
    search: "",
    horizons: {},
    signals: {},
    quadrants: {},
    shortlistOnly: false
  };

  var COLUMNS = [
    { key: "rank", label: "#", n: true, width: "42px",
      get: function (t) { return t.rank; },
      cell: function (t) { return h("span", { class: "num", text: t.rank || "—" }); } },
    { key: "label", label: "Topic",
      get: function (t) { return (t.label || "").toLowerCase(); },
      cell: function (t) {
        return h("div", null, [
          h("div", { style: "font-weight:600" }, [
            t.short,
            t.rank && t.rank <= D.shortlist_size
              ? h("span", { class: "tag t-good", style: "margin-left:7px", text: "shortlist" }) : null
          ]),
          h("div", { class: "note truncate", style: "max-width:340px", text: t.terms.slice(0, 8).join(", ") })
        ]);
      } },
    { key: "horizon", label: "Horizon", width: "78px",
      get: function (t) { return t.horizon || "zz"; },
      cell: function (t) { return BT.horizonTag(t.horizon) || h("span", { class: "note", text: "—" }); } },
    { key: "signal_class", label: "Signal", width: "78px",
      get: function (t) { return t.signal_class || "zz"; },
      cell: function (t) { return BT.signalTag(t.signal_class) || h("span", { class: "note", text: "—" }); } },
    { key: "document_count", label: "Docs", n: true, num: true },
    { key: "emergence_score", label: "Emergence", n: true, num: true },
    { key: "strategic_fit", label: "Fit", n: true, num: true },
    { key: "asset_leverage", label: "Leverage", n: true, num: true },
    { key: "opportunity_index", label: "Opp. index", n: true, num: true },
    { key: "composite_rank_score", label: "Rank score", n: true, num: true }
  ];

  /* ------------------------------------------------------------ filtering */

  function rows() {
    var q = state.search.trim().toLowerCase();
    var out = D.topics.filter(function (t) {
      if (state.shortlistOnly && !(t.rank && t.rank <= D.shortlist_size)) return false;
      if (!state.horizons[t.horizon || ""]) return false;
      if (!state.signals[t.signal_class || ""]) return false;
      if (!state.quadrants[t.fit_quadrant || ""]) return false;
      if (q) {
        var hay = (t.label + " " + t.terms.join(" ") + " " +
          (t.best_objective || "") + " " + (t.best_asset || "") + " " +
          (t.critical_tech || "")).toLowerCase();
        if (hay.indexOf(q) < 0) return false;
      }
      return true;
    });

    var col = null;
    COLUMNS.forEach(function (c) { if (c.key === state.sort) col = c; });
    var get = col && col.get ? col.get : function (t) {
      var v = t[state.sort];
      return v == null ? -Infinity : v;
    };
    out.sort(function (a, b) {
      var av = get(a), bv = get(b);
      if (av === bv) return (a.rank || 0) - (b.rank || 0);
      /* Nulls sort last whichever way the column is pointing: a topic with no
         opportunity index has not "scored zero", and floating it to the top of
         an ascending sort would say it had. */
      if (av === -Infinity) return 1;
      if (bv === -Infinity) return -1;
      return (av < bv ? -1 : 1) * state.dir;
    });
    return out;
  }

  /* -------------------------------------------------------------- toolbar */

  function buildToolbar() {
    BT.clear(toolbar);

    var search = h("input", { type: "search", placeholder: "Filter by term, objective or asset…",
      "aria-label": "Filter topics", value: state.search,
      oninput: debounce(function () { state.search = search.value; render(); }, 150) });
    toolbar.appendChild(h("div", { class: "field grow" }, [
      h("label", { text: "Search" }), search
    ]));

    toolbar.appendChild(chipGroup("Horizon", ["H1", "H2", "H3", ""], state.horizons, function (k) {
      return k || "none";
    }));
    toolbar.appendChild(chipGroup("Signal", ["weak", "strong", "latent", "noise", ""],
      state.signals, function (k) { return k || "none"; }));
    toolbar.appendChild(chipGroup("2×2", ["act", "on-strategy, no right-to-play",
      "capability looking for a problem", "watch"], state.quadrants, function (k) {
      return k === "on-strategy, no right-to-play" ? "on-strategy"
        : k === "capability looking for a problem" ? "capability" : k;
    }));

    toolbar.appendChild(h("div", { class: "field" }, [
      h("label", { text: "&nbsp;", html: "&nbsp;" }),
      h("button", {
        class: "btn", type: "button", "aria-pressed": String(state.shortlistOnly),
        onclick: function () { state.shortlistOnly = !state.shortlistOnly; buildToolbar(); render(); },
        text: "Shortlist only"
      })
    ]));

    toolbar.appendChild(h("div", { class: "spacer" }));
    toolbar.appendChild(h("div", { class: "field" }, [
      h("label", { html: "&nbsp;" }),
      h("button", { class: "btn", type: "button", onclick: exportCSV, text: "Download CSV" })
    ]));
  }

  function chipGroup(label, keys, set, name) {
    return h("div", { class: "field" }, [
      h("label", { text: label }),
      h("div", { class: "chips" }, keys.map(function (k) {
        var btn = h("button", {
          class: "chip", type: "button", "aria-pressed": String(!!set[k]),
          onclick: function () {
            set[k] = !set[k];
            btn.setAttribute("aria-pressed", String(!!set[k]));
            render();
          },
          text: name(k)
        });
        return btn;
      }))
    ]);
  }

  function debounce(fn, ms) {
    var t = null;
    return function () { clearTimeout(t); t = setTimeout(fn, ms); };
  }

  /* ---------------------------------------------------------------- table */

  function header() {
    return h("thead", null, [h("tr", null, COLUMNS.map(function (c) {
      var active = state.sort === c.key;
      var th = h("th", {
        class: "sortable" + (c.n ? " n" : ""),
        style: c.width ? "width:" + c.width : null,
        scope: "col",
        tabindex: "0",
        title: (BT.field(c.key) || {}).why || ("Sort by " + c.label),
        onclick: function () { sortBy(c.key); },
        onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(c.key); } }
      }, [c.label, h("span", { class: "arrow", text: active ? (state.dir > 0 ? "▲" : "▼") : "↕" })]);
      if (active) th.setAttribute("aria-sort", state.dir > 0 ? "ascending" : "descending");
      return th;
    }))]);
  }

  function sortBy(key) {
    if (state.sort === key) state.dir = -state.dir;
    else {
      state.sort = key;
      /* Rank reads best ascending (#1 first); every score reads best
         descending (the biggest first). Guessing right saves a click on
         almost every column. */
      state.dir = key === "rank" || key === "label" ? 1 : -1;
    }
    render();
  }

  function numericCell(t, key) {
    var v = BT.fieldValue(t, key);
    var text = BT.fieldFormat(key, v);
    if (v == null) {
      return h("span", { class: "note",
        title: key === "opportunity_index" && t.index_suppressed
          ? "Suppressed: below the minimum document count for a composite index." : null,
        text: t.index_suppressed && key === "opportunity_index" ? "suppressed" : "—" });
    }
    var ext = BT.fieldExtent(key);
    var frac = Math.max(0, Math.min(1, (v - ext.lo) / (ext.hi - ext.lo)));
    return h("span", { class: "bar num", title: text + " — " + (frac * 100).toFixed(0) +
      "% of this column's range across the run" }, [
      h("span", { text: text }),
      h("u", null, [h("i", { style: "width:" + Math.max(2, frac * 100).toFixed(1) + "%" })])
    ]);
  }

  function render() {
    if (!tbody) return;
    BT.clear(tbody);
    var list = rows();
    if (!list.length) {
      tbody.appendChild(h("tr", null, [h("td", { colspan: COLUMNS.length },
        [h("div", { class: "empty", text: "No topic matches these filters." })])]));
      updateCount(0);
      return;
    }
    list.forEach(function (t) {
      var tr = h("tr", { class: "clickable" + (openId === t.id ? " open" : ""),
        onclick: function () { toggle(t); } },
        COLUMNS.map(function (c) {
          return h("td", { class: c.n ? "n" : null },
            [c.cell ? c.cell(t) : c.num ? numericCell(t, c.key) : String(t[c.key] == null ? "—" : t[c.key])]);
        }));
      tbody.appendChild(tr);
      if (openId === t.id) {
        tbody.appendChild(h("tr", { class: "detail" }, [
          h("td", { colspan: COLUMNS.length }, [detail(t)])
        ]));
      }
    });
    updateCount(list.length);
  }

  function updateCount(n) {
    var box = document.getElementById("topicsCount");
    if (box) box.textContent = n === D.topics.length
      ? fmt.int(n) + " topics"
      : fmt.int(n) + " of " + fmt.int(D.topics.length) + " topics";
  }

  function toggle(t) {
    openId = openId === t.id ? null : t.id;
    render();
    if (openId) {
      var row = page.querySelector("tr.open");
      if (row && row.nextSibling && row.nextSibling.scrollIntoView) {
        row.scrollIntoView({ block: "nearest" });
      }
    }
  }

  /* --------------------------------------------------------------- detail */

  function detail(t) {
    var M = D.method;
    var box = h("div", { style: "padding:16px 18px 20px" });

    box.appendChild(h("div", { style: "display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px" }, [
      h("h3", { style: "margin-right:6px", text: t.label }),
      BT.horizonTag(t.horizon), BT.signalTag(t.signal_class),
      t.critical_tech ? h("span", { class: "tag t-muted", text: "DISR: " + t.critical_tech }) : null,
      h("span", { class: "note", text: t.id + " · " + fmt.int(t.document_count) + " documents · "
        + t.first_slice + "–" + t.last_slice })
    ]));

    var cols = h("div", { class: "grid g3" });
    box.appendChild(cols);

    /* --- how the emergence score was built --- */
    var rw = M.rotolo_weights || {};
    var left = h("div", null, [h("h4", { text: "Emergence — the five Rotolo attributes" })]);
    Object.keys(rw).forEach(function (k, i) {
      left.appendChild(BT.scoreBar(k, t[k], {
        extent: { lo: 0, hi: 1 }, why: false,
        label: fmt.title(k) + " ×" + Number(rw[k]).toFixed(2),
        color: BT.catColor(i * 3 + 1, 0.85)
      }));
    });
    left.appendChild(h("p", { class: "hint", text:
      "Each attribute is percentile-ranked within the run, multiplied by its weight and summed. "
      + "Emergence score: " + fmt.score(t.emergence_score) + "." }));
    left.appendChild(h("div", { class: "note", style: "margin-top:6px", text:
      "Burst intensity " + fmt.score(t.burst_weight) +
      (t.burst_slices.length ? " · in burst " + t.burst_slices.join(", ") : " · never in burst") +
      " · CAGR " + fmt.signed(t.cagr, 3) + " · maturity " + fmt.score(t.maturity) }));
    cols.appendChild(left);

    /* --- fit, leverage, index --- */
    var mid = h("div", null, [h("h4", { text: "Fit, leverage and the index" })]);
    mid.appendChild(BT.scoreBar("strategic_fit", t.strategic_fit, { why: false }));
    mid.appendChild(h("p", { class: "hint", style: "margin:-4px 0 8px",
      text: "Best match: " + (t.best_objective || "—") +
        (t.best_objective_sim != null ? " (similarity " + fmt.score(t.best_objective_sim) + ")" : "") }));
    mid.appendChild(BT.scoreBar("asset_leverage", t.asset_leverage, { why: false }));
    mid.appendChild(h("p", { class: "hint", style: "margin:-4px 0 8px",
      text: "Best match: " + (t.best_asset || "—") }));

    var comps = t.index_components || {};
    var compKeys = Object.keys(comps);
    if (t.index_suppressed) {
      mid.appendChild(h("div", { class: "callout warn", style: "margin-top:8px" }, [
        h("strong", { text: "Opportunity index suppressed. " }),
        "Below " + M.index_min_documents + " documents, a composite looks identical whether it " +
        "rests on eight documents or eight hundred, so it is not reported."
      ]));
    } else {
      mid.appendChild(BT.scoreBar("opportunity_index", t.opportunity_index, { why: false }));
      if (compKeys.length) {
        compKeys.forEach(function (k) {
          mid.appendChild(BT.scoreBar(k, comps[k], {
            extent: { lo: 0, hi: 1 }, why: false,
            label: k.replace(/_/g, " ") + " ×" + Number((M.index_components || {})[k] || 0).toFixed(2),
            color: BT.cssVar("--muted")
          }));
        });
      }
      mid.appendChild(h("p", { class: "hint", text:
        "A relative within-run ordering, never a market size — and not comparable to the same "
        + "number in another run." }));
    }
    mid.appendChild(h("dl", { class: "kv" }, [
      h("dt", { text: "2×2 placement" }),
      h("dd", null, [t.fit_quadrant,
        h("div", { class: "note", text: BT.QUADRANT_TEXT[t.fit_quadrant] || "" })])
    ]));
    cols.appendChild(mid);

    /* --- evidence --- */
    var right = h("div", null, [h("h4", { text: "Documents over time" })]);
    if (t.timeseries.length) right.appendChild(BT.chart.timeseries(t.timeseries, { width: 300, height: 66 }));
    right.appendChild(h("h4", { style: "margin-top:14px",
      text: "Closest documents (" + Math.min(t.docs.length, 8) + " of " + fmt.int(t.document_count) + ")" }));
    t.docs.slice(0, 8).forEach(function (d) {
      var P = BT.P;
      right.appendChild(h("div", { style: "margin:6px 0;font-size:12.5px;line-height:1.4" }, [
        P.url[d.i]
          ? h("a", { href: P.url[d.i], target: "_blank", rel: "noopener noreferrer", text: P.title[d.i] })
          : h("span", { text: P.title[d.i] }),
        h("div", { class: "note", text:
          [BT.D.sources[P.source[d.i]], P.year[d.i], d.sim == null ? null : "sim " + fmt.score(d.sim)]
            .filter(Boolean).join(" · ") })
      ]));
    });

    var f = D.fidelity || {};
    if (f.computed && t.map_purity != null) {
      right.appendChild(h("h4", { style: "margin-top:14px", text: "How well the map draws it" }));
      right.appendChild(h("p", { class: "note" }, [
        h("b", { text: fmt.pct(t.space_purity) }), " of a member's nearest neighbours share this "
        + "topic in " + D.dimensions + " dimensions; ",
        h("b", { text: fmt.pct(t.map_purity) }), " do in the 2D projection."
      ]));
    }

    right.appendChild(h("div", { class: "rowactions", style: "margin-top:14px" }, [
      h("button", { class: "btn sm", type: "button",
        onclick: function () { BT.go("map", { topic: t.id }); }, text: "Show on the map →" }),
      h("button", { class: "btn sm", type: "button",
        onclick: function () { BT.go("scores", { topic: t.id }); }, text: "Plot it →" }),
      t.evidence_url ? h("a", { class: "btn sm", href: t.evidence_url, target: "_blank",
        rel: "noopener noreferrer", text: "Evidence card →" }) : null
    ]));
    right.appendChild(h("p", { class: "hint", style: "margin-top:10px", text:
      "If these documents do not look like a coherent theme, this topic is a clustering artefact "
      + "and should be discarded. That check is the cheapest quality control in the method." }));
    cols.appendChild(right);

    return box;
  }

  /* --------------------------------------------------------------- export */

  function exportCSV() {
    var keys = ["rank", "id", "label", "horizon", "signal_class", "document_count",
      "emergence_score", "novelty", "growth", "coherence", "impact", "uncertainty",
      "burst_weight", "cagr", "maturity", "avg_proportion",
      "strategic_fit", "best_objective", "asset_leverage", "best_asset",
      "opportunity_index", "index_suppressed", "composite_rank_score",
      "fit_quadrant", "critical_tech", "first_slice", "last_slice",
      "map_purity", "space_purity"];
    var out = [keys];
    rows().forEach(function (t) {
      out.push(keys.map(function (k) {
        var v = t[k];
        return Array.isArray(v) ? v.join(";") : v == null ? "" : v;
      }));
    });
    BT.downloadCSV("bigthink-topics-" + D.run_id + ".csv", out);
  }

  /* ------------------------------------------------------------------ api */

  return {
    mount: function () {
      page = document.getElementById("topicsPage");
      toolbar = document.getElementById("topicsToolbar");
      if (!mounted) {
        mounted = true;
        ["H1", "H2", "H3", ""].forEach(function (k) { state.horizons[k] = true; });
        ["weak", "strong", "latent", "noise", ""].forEach(function (k) { state.signals[k] = true; });
        ["act", "on-strategy, no right-to-play", "capability looking for a problem", "watch", ""]
          .forEach(function (k) { state.quadrants[k] = true; });
      }
      BT.clear(page);
      buildToolbar();

      page.appendChild(h("div", { style: "display:flex;align-items:baseline;gap:12px;margin-bottom:12px;flex-wrap:wrap" }, [
        h("h1", { style: "font-size:20px", text: "Every topic, every score" }),
        h("span", { class: "note", id: "topicsCount" }),
        h("span", { class: "spacer" }),
        h("span", { class: "note", text: "Click a row to open its full breakdown. Click a column heading to sort." })
      ]));

      var wrap = h("div", { class: "tablewrap" });
      tbody = h("tbody");
      wrap.appendChild(h("table", null, [header(), tbody]));
      page.appendChild(wrap);

      page.appendChild(h("p", { class: "hint", style: "margin-top:12px", text:
        "Ranked on emergence, strategic fit and asset leverage only. The opportunity index is "
        + "reported here but deliberately kept out of the ordering: it is the weakest-founded "
        + "number in the pipeline, and folding it in would launder that weakness." }));

      render();
    },

    openTopic: function (topicId) {
      openId = topicId;
      state.shortlistOnly = false;
      state.search = "";
      render();
      var row = page && page.querySelector("tr.open");
      if (row) row.scrollIntoView({ behavior: "smooth", block: "center" });
    },

    refresh: function () { if (mounted && page) BT.views.topics.mount(); }
  };
})();
