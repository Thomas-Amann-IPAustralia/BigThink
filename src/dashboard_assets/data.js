/* ==========================================================================
   data.js — the run's tables, browsable and exportable.

   WHY THIS IS NOT DuckDB-WASM. The obvious way to let a reader query the
   database is to ship duckdb-wasm and the real .duckdb file. Two things rule
   it out here and both are load-bearing rather than fussy: the WASM bundle
   only exists on a CDN, and this page is deliberately CDN-free because it is
   read from GitHub Pages behind corporate proxies where a silent
   script-load failure would take the whole page down; and the database is
   gitignored precisely because it is a multi-hundred-megabyte binary that
   does not diff, so Pages could not serve it anyway.

   What is achievable, and is what this does: ship the run's rows in the
   payload the page already carries, and give them a real browser — sort,
   filter, paginate, export. Then say plainly how to query the actual
   database, for the reader who wants SQL rather than a table.

   Every table here is a projection of what the pipeline stored, not a
   re-derivation. If a number disagrees with the Topics view, that is a bug in
   this page and not two opinions about the run.
   ========================================================================== */

BT.views.data = (function () {
  var h = BT.h, fmt = BT.fmt, D = BT.D, P = BT.P;
  var page, toolbar, mounted = false, body;
  var state = { table: "topics", search: "", sort: null, dir: 1, page: 0, perPage: 100 };

  /* ---------------------------------------------------------- table specs */

  function topicRows() {
    return D.topics.map(function (t) {
      return {
        rank: t.rank, topic_id: t.id, label: t.label,
        document_count: t.document_count, horizon: t.horizon, signal_class: t.signal_class,
        emergence_score: t.emergence_score, novelty: t.novelty, growth: t.growth,
        coherence: t.coherence, impact: t.impact, uncertainty: t.uncertainty,
        burst_weight: t.burst_weight, cagr: t.cagr, maturity: t.maturity,
        avg_proportion: t.avg_proportion,
        strategic_fit: t.strategic_fit, best_objective: t.best_objective,
        asset_leverage: t.asset_leverage, best_asset: t.best_asset,
        critical_tech: t.critical_tech,
        opportunity_index: t.index_suppressed ? null : t.opportunity_index,
        index_suppressed: t.index_suppressed,
        composite_rank_score: t.composite_rank_score, fit_quadrant: t.fit_quadrant,
        first_slice: t.first_slice, last_slice: t.last_slice
      };
    });
  }

  function documentRows() {
    var out = [];
    for (var i = 0; i < BT.N; i++) {
      var ti = P.topic[i];
      out.push({
        title: P.title[i],
        source: D.sources[P.source[i]] || null,
        year: P.year[i],
        venue: P.venue[i] || null,
        citation_count: P.citation[i],
        steepv: D.steepv[P.steepv[i]] || null,
        topic_id: ti < 0 ? null : D.topics[ti].id,
        topic: ti < 0 ? null : D.topics[ti].short,
        similarity: P.similarity[i],
        url: P.url[i] || null
      });
    }
    return out;
  }

  function membershipRows() {
    var out = [];
    D.topics.forEach(function (t) {
      t.docs.forEach(function (d) {
        out.push({
          topic_id: t.id, topic: t.short, similarity: d.sim,
          title: P.title[d.i], source: D.sources[P.source[d.i]] || null,
          year: P.year[d.i], url: P.url[d.i] || null
        });
      });
    });
    return out;
  }

  function timeseriesRows() {
    var out = [];
    D.topics.forEach(function (t) {
      t.timeseries.forEach(function (s) {
        out.push({
          topic_id: t.id, topic: t.short, time_slice: s.slice,
          doc_count: s.n, proportion: s.p, in_burst: s.burst
        });
      });
    });
    return out;
  }

  function collectionRows() {
    return (D.collection || []).map(function (c) {
      return { source: c.source, outcome: c.worst, queries: c.queries, records: c.records };
    });
  }

  function strategyRows() {
    var out = [];
    Object.keys(D.strategy || {}).forEach(function (type) {
      D.strategy[type].forEach(function (r) {
        out.push({ ref_type: type, code: r.code, label: r.label, weight: r.weight,
          lexicon: (r.lexicon || []).join("; "), text: r.text });
      });
    });
    return out;
  }

  function fidelityRows() {
    return D.topics.map(function (t) {
      return {
        topic_id: t.id, topic: t.short, plotted: t.plotted_count,
        space_purity: t.space_purity, map_purity: t.map_purity,
        distortion: t.space_purity == null || t.map_purity == null
          ? null : Number((t.space_purity - t.map_purity).toFixed(3))
      };
    });
  }

  var TABLES = {
    topics: {
      label: "topics + topic_scores",
      note: "One row per detected topic, joined to its Stage 3–5 scores. The same join "
        + "src/db.py:fetch_ranked_topics performs, which is what the report and the notebook read.",
      rows: topicRows
    },
    documents: {
      label: "documents",
      note: "Every plotted document, with the topic it joined and how similar it was. "
        + "Titles and venues come from external APIs and are shown as collected.",
      rows: documentRows
    },
    topic_documents: {
      label: "topic_documents",
      note: "Topic membership. Capped at the closest 40 documents per topic so the page stays a "
        + "reasonable download — the full table is in the database.",
      rows: membershipRows
    },
    topic_timeseries: {
      label: "topic_timeseries",
      note: "Per-slice counts: the input to Kleinberg burst detection and to the growth fit. "
        + "in_burst is the automaton's own state assignment.",
      rows: timeseriesRows
    },
    collection_log: {
      label: "collection_log",
      note: "Per-source outcome for this run, aggregated across scan-frame queries. A dead "
        + "collector shows up here as a skipped or failed row rather than as fewer results.",
      rows: collectionRows
    },
    strategy_refs: {
      label: "strategy_refs",
      note: "The Stage 0 reference set every fit and leverage score is measured against.",
      rows: strategyRows
    },
    projection_fidelity: {
      label: "projection_fidelity",
      note: "Derived, not stored: per-topic neighbour purity in the full embedding space against "
        + "the same measure on the 2D map. A large positive distortion means the projection tore "
        + "that topic apart and the map understates its coherence.",
      rows: fidelityRows
    }
  };

  /* ------------------------------------------------------------ rendering */

  var cache = {};
  function currentRows() {
    if (!cache[state.table]) cache[state.table] = TABLES[state.table].rows();
    var rows = cache[state.table];
    var q = state.search.trim().toLowerCase();
    if (q) {
      rows = rows.filter(function (r) {
        for (var k in r) {
          if (r[k] != null && String(r[k]).toLowerCase().indexOf(q) >= 0) return true;
        }
        return false;
      });
    }
    if (state.sort) {
      var key = state.sort, dir = state.dir;
      rows = rows.slice().sort(function (a, b) {
        var av = a[key], bv = b[key];
        if (av == null && bv == null) return 0;
        if (av == null) return 1;   /* nulls last, both directions */
        if (bv == null) return -1;
        if (typeof av === "number" && typeof bv === "number") return (av - bv) * dir;
        return String(av).localeCompare(String(bv)) * dir;
      });
    }
    return rows;
  }

  function columnsOf(rows) {
    return rows.length ? Object.keys(rows[0]) : [];
  }

  function render() {
    BT.clear(body);
    var spec = TABLES[state.table];
    var rows = currentRows();
    var cols = columnsOf(cache[state.table] || []);

    body.appendChild(h("p", { class: "note", style: "margin-bottom:10px" }, [
      h("code", { text: state.table }), " — " + spec.note
    ]));

    if (!rows.length) {
      body.appendChild(h("div", { class: "empty", text: "No row matches that filter." }));
      return;
    }

    var pages = Math.max(1, Math.ceil(rows.length / state.perPage));
    if (state.page >= pages) state.page = pages - 1;
    var slice = rows.slice(state.page * state.perPage, (state.page + 1) * state.perPage);

    var thead = h("thead", null, [h("tr", null, cols.map(function (c) {
      var numeric = typeof (rows[0] || {})[c] === "number";
      var active = state.sort === c;
      var th = h("th", {
        class: "sortable" + (numeric ? " n" : ""), scope: "col", tabindex: "0",
        onclick: function () { sortBy(c); },
        onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); sortBy(c); } }
      }, [c, h("span", { class: "arrow", text: active ? (state.dir > 0 ? "▲" : "▼") : "↕" })]);
      if (active) th.setAttribute("aria-sort", state.dir > 0 ? "ascending" : "descending");
      return th;
    }))]);

    var tb = h("tbody", null, slice.map(function (r) {
      return h("tr", null, cols.map(function (c) {
        var v = r[c];
        var numeric = typeof v === "number";
        if (c === "url" && v) {
          return h("td", null, [h("a", { href: v, target: "_blank", rel: "noopener noreferrer",
            text: "open ↗" })]);
        }
        var text = v == null ? "—" : typeof v === "boolean" ? (v ? "true" : "false") : String(v);
        /* Every text cell truncates on one line. A raw table dump is for
           scanning many rows at once, and a cell that wraps to four lines
           turns forty rows into a page of scrolling. The full value is in the
           tooltip and in the CSV. */
        return h("td", {
          class: numeric ? "n" : "truncate",
          style: numeric ? null : "max-width:" + (text.length > 40 ? 300 : 190) + "px",
          title: text.length > 24 ? text : null,
          text: text
        });
      }));
    }));

    body.appendChild(h("div", { class: "tablewrap", style: "max-height:calc(100vh - 320px)" },
      [h("table", null, [thead, tb])]));

    body.appendChild(h("div", { class: "rowactions", style: "margin-top:11px" }, [
      h("span", { class: "note", text:
        "Rows " + fmt.int(state.page * state.perPage + 1) + "–" +
        fmt.int(state.page * state.perPage + slice.length) + " of " + fmt.int(rows.length) +
        (rows.length !== (cache[state.table] || []).length
          ? " (filtered from " + fmt.int(cache[state.table].length) + ")" : "") }),
      h("span", { class: "spacer" }),
      h("button", { class: "btn sm", type: "button", disabled: state.page === 0,
        onclick: function () { state.page--; render(); }, text: "← Previous" }),
      h("span", { class: "note", text: "page " + (state.page + 1) + " of " + pages }),
      h("button", { class: "btn sm", type: "button", disabled: state.page >= pages - 1,
        onclick: function () { state.page++; render(); }, text: "Next →" })
    ]));
  }

  function sortBy(col) {
    if (state.sort === col) state.dir = -state.dir;
    else { state.sort = col; state.dir = 1; }
    state.page = 0;
    render();
  }

  function exportCSV() {
    var rows = currentRows();
    var cols = columnsOf(cache[state.table] || []);
    var out = [cols];
    rows.forEach(function (r) { out.push(cols.map(function (c) { return r[c]; })); });
    BT.downloadCSV("bigthink-" + state.table + "-" + D.run_id + ".csv", out);
  }

  /* -------------------------------------------------------------- toolbar */

  function buildToolbar() {
    BT.clear(toolbar);

    var sel = h("select", { "aria-label": "Table", onchange: function () {
      state.table = sel.value; state.sort = null; state.page = 0; render();
    } }, Object.keys(TABLES).map(function (k) {
      return h("option", { value: k, selected: k === state.table, text: TABLES[k].label });
    }));
    toolbar.appendChild(h("div", { class: "field" }, [h("label", { text: "Table" }), sel]));

    var search = h("input", { type: "search", value: state.search,
      placeholder: "Filter across every column…", "aria-label": "Filter rows",
      oninput: (function () {
        var t = null;
        return function () {
          clearTimeout(t);
          t = setTimeout(function () { state.search = search.value; state.page = 0; render(); }, 160);
        };
      })() });
    toolbar.appendChild(h("div", { class: "field grow" }, [h("label", { text: "Filter" }), search]));

    var per = h("select", { "aria-label": "Rows per page", onchange: function () {
      state.perPage = Number(per.value); state.page = 0; render();
    } }, [50, 100, 250, 1000].map(function (n) {
      return h("option", { value: n, selected: n === state.perPage, text: n + " rows" });
    }));
    toolbar.appendChild(h("div", { class: "field" }, [h("label", { text: "Page size" }), per]));

    toolbar.appendChild(h("div", { class: "spacer" }));
    toolbar.appendChild(h("div", { class: "field" }, [
      h("label", { html: "&nbsp;" }),
      h("button", { class: "btn", type: "button", onclick: exportCSV, text: "Download CSV" })
    ]));
  }

  /* --------------------------------------------------------- SQL guidance */

  function sqlCard() {
    var repo = D.repo_url;
    return h("details", { class: "more", style: "border:1px solid var(--line);border-radius:7px;padding:14px 16px;background:var(--surface);margin-top:20px" }, [
      h("summary", { text: "Querying the real database with SQL" }),
      h("div", null, [
        h("p", { class: "note", style: "margin-top:10px" },
          "The tables above are the rows this page carries. The database itself is not published: "
          + "it is a multi-hundred-megabyte binary that does not diff, so it is deliberately "
          + "gitignored and GitHub Pages could not serve it. Rebuild it from the repository and "
          + "query it directly:"),
        h("pre", { class: "mono",
          style: "background:var(--sunken);border:1px solid var(--line);border-radius:6px;"
            + "padding:11px 13px;overflow-x:auto;font-size:12px;line-height:1.55",
          text:
            "git clone " + repo + ".git && cd BigThink\n" +
            "pip install -r requirements.txt\n\n" +
            "# collect and analyse (or restore a corpus from R2 if you have keys)\n" +
            "python -m src.pipeline --run-id " + D.run_id + "\n\n" +
            "# then query it with the DuckDB CLI or from Python\n" +
            "duckdb data/bigthink.duckdb\n" +
            "  SELECT t.label, s.strategic_fit, s.asset_leverage, s.rank\n" +
            "  FROM topics t JOIN topic_scores s USING (topic_id, run_id)\n" +
            "  WHERE t.run_id = '" + D.run_id + "' ORDER BY s.rank LIMIT 20;" }),
        h("p", { class: "note", style: "margin-top:10px" }, [
          "The schema — every table and every column, with the reason each exists — is in ",
          h("a", { href: repo + "/blob/main/src/db.py", target: "_blank",
            rel: "noopener noreferrer", text: "src/db.py" }),
          ". This run also published a peer-review notebook that recomputes four stored numbers "
          + "from their stored inputs, so the arithmetic can be checked rather than trusted: ",
          h("a", { href: repo + "/tree/main/data/outputs/" + D.run_id, target: "_blank",
            rel: "noopener noreferrer", text: "this run's outputs" }), "."
        ])
      ])
    ]);
  }

  /* ------------------------------------------------------------------ api */

  return {
    mount: function () {
      page = document.getElementById("dataPage");
      toolbar = document.getElementById("dataToolbar");
      mounted = true;
      BT.clear(page);

      page.appendChild(h("div", { style: "display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;margin-bottom:12px" }, [
        h("h1", { style: "font-size:20px", text: "The data behind the run" }),
        h("span", { class: "note", text:
          "Everything the pipeline stored for run " + D.run_id + ", as it was stored." })
      ]));

      body = h("div");
      page.appendChild(body);
      page.appendChild(sqlCard());

      buildToolbar();
      render();
    },

    refresh: function () { if (mounted && page) BT.views.data.mount(); }
  };
})();
