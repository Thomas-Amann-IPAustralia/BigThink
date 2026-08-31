/* ==========================================================================
   method.js — docs/method.md, made interactive and grounded in this run.

   Every number on this page is read from the payload, never hardcoded. That
   is the whole point: a method page that describes weights the pipeline no
   longer uses is worse than no method page, because it is confidently wrong
   in the way this project is most anxious about (see CLAUDE.md — a bad
   threshold produces a confident, wrong shortlist nobody can tell is wrong).

   The device running through it is the worked example: pick one topic at the
   top of Stage 2 and the burst chart, the horizon band and the Rotolo
   breakdown all re-explain themselves on that topic. An abstract description
   of Kleinberg's automaton is a thing to skim; the same description with a
   real topic's counts under it is a thing to check.
   ========================================================================== */

BT.views.method = (function () {
  var h = BT.h, el = BT.el, fmt = BT.fmt;
  var root, exampleIdx = 0, exampleHooks = [];

  /* ------------------------------------------------------------- helpers */

  function section(id, num, title, lede) {
    var s = h("section", { class: "card stagecard", id: "m-" + id, style: "margin-top:18px" }, [
      h("div", { class: "stagehead" }, [
        num == null ? null : h("span", { class: "stagenum", text: num }),
        h("h2", { text: title })
      ]),
      lede ? h("p", { class: "lede", text: lede }) : null
    ]);
    return s;
  }

  function more(summary, build) {
    var box = h("details", { class: "more" }, [h("summary", { text: summary })]);
    var body = h("div");
    box.appendChild(body);
    var built = false;
    box.addEventListener("toggle", function () {
      if (box.open && !built) { built = true; build(body); }
    });
    return box;
  }

  /* A weight set drawn as one proportional bar. Reading "0.30" in a table and
     seeing that growth is nearly a third of the score are different acts. */
  function weightBar(weights, colorFn) {
    var keys = Object.keys(weights);
    var total = keys.reduce(function (a, k) { return a + Number(weights[k] || 0); }, 0) || 1;
    return h("div", { class: "weights" }, keys.map(function (k, i) {
      var pct = (Number(weights[k]) / total) * 100;
      return h("span", {
        style: "width:" + pct.toFixed(2) + "%;background:" + (colorFn ? colorFn(k, i) : BT.catColor(i)),
        title: k + " — " + Number(weights[k]).toFixed(2),
        text: pct > 11 ? k : ""
      });
    }));
  }

  function statCard(k, v, d, unit) {
    return h("div", { class: "stat" }, [
      h("div", { class: "k", text: k }),
      h("div", { class: "v" }, [String(v), unit ? h("small", { text: " " + unit }) : null]),
      d ? h("div", { class: "d", text: d }) : null
    ]);
  }

  function table(headers, rows, opts) {
    opts = opts || {};
    return h("div", { class: "tablewrap", style: opts.maxHeight ? "max-height:" + opts.maxHeight : null }, [
      h("table", null, [
        h("thead", null, [h("tr", null, headers.map(function (col) {
          return h("th", { class: col.n ? "n" : null, text: col.label || col });
        }))]),
        h("tbody", null, rows.map(function (r) {
          return h("tr", null, r.map(function (c, i) {
            var col = headers[i] || {};
            return h("td", { class: col.n ? "n" : null }, [c == null ? "—" : c]);
          }));
        }))
      ])
    ]);
  }

  /* --------------------------------------------------- the worked example */

  function onExample(fn) { exampleHooks.push(fn); fn(BT.D.topics[exampleIdx]); }

  function examplePicker() {
    var sel = h("select", {
      "aria-label": "Topic used for the worked examples below",
      onchange: function () {
        exampleIdx = Number(sel.value);
        var t = BT.D.topics[exampleIdx];
        exampleHooks.forEach(function (fn) { fn(t); });
      }
    }, BT.D.topics.map(function (t, i) {
      return h("option", { value: i, text: "#" + (t.rank || "–") + "  " + t.label });
    }));
    return h("div", { class: "callout", style: "border-left-color:var(--h2)" }, [
      h("div", { style: "display:flex;gap:12px;align-items:center;flex-wrap:wrap" }, [
        h("strong", { text: "Worked example" }),
        h("span", { class: "note", style: "flex:1 1 240px",
          text: "Everything in this stage is explained twice: once in general, and once on the topic you pick here." }),
        h("span", { style: "flex:1 1 260px;min-width:200px" }, [sel])
      ])
    ]);
  }

  /* ------------------------------------------------------------- diagram */

  function pipelineDiagram() {
    var stages = [
      ["0", "Strategy", "objectives, assets"],
      ["1", "Signals", "six sources"],
      ["2", "Emergence", "topics, bursts"],
      ["3", "Fit", "strategy, assets"],
      ["4", "Index", "relative only"],
      ["5", "Shortlist", "ranked, evidenced"]
    ];
    var w = 940, hgt = 96, bw = 132, gap = (w - stages.length * bw) / (stages.length - 1);
    var kids = [];
    stages.forEach(function (s, i) {
      var x = i * (bw + gap);
      if (i < stages.length - 1) {
        kids.push(BT.svg("path", {
          class: "flow", "stroke-width": 1.2,
          d: "M" + (x + bw) + " 40 H" + (x + bw + gap)
        }));
        kids.push(BT.svg("path", {
          class: "flow", "stroke-width": 1.2, fill: "var(--line-strong)",
          d: "M" + (x + bw + gap - 5) + " 37 l5 3 -5 3 z", stroke: "none"
        }));
      }
      kids.push(BT.svg("g", {
        class: "node", role: "button", tabindex: "0",
        onclick: function () { jump("m-stage" + s[0]); },
        onkeydown: function (e) { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); jump("m-stage" + s[0]); } }
      }, [
        BT.svg("rect", { x: x, y: 18, width: bw, height: 44, rx: 7, "stroke-width": 1 }),
        BT.svg("text", { x: x + 11, y: 37, text: s[0] + " · " + s[1] }),
        BT.svg("text", { x: x + 11, y: 51, class: "sub", text: s[2] }),
        BT.svg("title", { text: "Jump to Stage " + s[0] })
      ]));
    });
    kids.push(BT.svg("text", { x: w / 2, y: 86, class: "flowlab",
      "text-anchor": "middle",
      text: "every stage reads its inputs from DuckDB and writes its outputs back — no stage passes objects to another" }));
    return BT.svg("svg", { class: "pipeline", viewBox: "0 0 " + w + " " + hgt,
      role: "img", "aria-label": "The six pipeline stages in order" }, kids);
  }

  function jump(id) {
    var node = document.getElementById(id);
    if (!node) return;
    node.scrollIntoView({ behavior: "smooth", block: "start" });
  }

  /* --------------------------------------------------------------- build */

  function buildIntro(page) {
    var D = BT.D;
    page.appendChild(h("h1", { text: "How this shortlist was produced" }));
    page.appendChild(h("p", { class: "lede", style: "margin-top:8px" },
      "What every number means and — more usefully — what it does not mean. " +
      "Every figure below is read from run " + D.run_id + " itself, so this page cannot " +
      "describe a pipeline that no longer exists."));

    page.appendChild(h("div", { class: "statrow", style: "margin:18px 0" }, [
      statCard("Documents", fmt.int(D.documents_total), "collected and deduplicated"),
      statCard("Topics", fmt.int(D.topics_total), "detected by " + D.method.clustering_method),
      statCard("Shortlisted", fmt.int(D.shortlist_size), "with an evidence card each"),
      statCard("Time slices", fmt.int(D.corpus.years.length),
        (D.year_min || "?") + "–" + (D.year_max || "?") + ", by " + D.method.time_slice),
      statCard("Embedding", D.backend, D.dimensions + " dimensions")
    ]));

    page.appendChild(h("div", { class: "card" }, [
      h("h4", { text: "The chain of reasoning" }),
      h("div", { style: "margin:10px 0 2px" }, [pipelineDiagram()])
    ]));

    page.appendChild(h("div", { class: "callout warn", style: "margin-top:14px" }, [
      h("strong", { text: "Three cautions that apply to every number on this page. " }),
      "The opportunity index is a relative within-run ordering and never a market size. " +
      "Scores from two runs are not comparable unless the corpus and the config snapshot match. " +
      "And no weight here has yet been validated against a known past opportunity — until that " +
      "test is run, the ranking is a hypothesis, not a finding."
    ]));
  }

  function buildStage0(page) {
    var s = section("stage0", "0", "Strategy encoding",
      "What the scan is scoring against: the Corporate Plan, the asset inventory and the " +
      "DISR critical-technology list, each turned into a reference vector and a hand-written lexicon.");
    var strategy = BT.D.strategy || {};
    var groups = [
      ["objective", "Objectives"], ["initiative", "Initiatives"],
      ["asset", "Assets"], ["critical_tech", "Critical technologies"]
    ];
    s.appendChild(h("div", { class: "statrow", style: "margin:14px 0" },
      groups.map(function (g) {
        return statCard(g[1], (strategy[g[0]] || []).length, "reference vectors");
      })));

    s.appendChild(h("div", { class: "callout" }, [
      h("strong", { text: "Why both a text block and a lexicon. " }),
      "Embeddings catch a trend that means the same thing in different words. The lexicon " +
      "catches a trend that names the thing exactly — “geographical indications” must score " +
      "against its initiative on the strength of the phrase, and embedding a whole paragraph " +
      "can dilute that to nothing."
    ]));

    var all = [];
    groups.forEach(function (g) {
      (strategy[g[0]] || []).forEach(function (r) {
        all.push([g[1], r.code || "—", r.label, r.weight == null ? "—" : r.weight.toFixed(2),
          r.lexicon.length ? r.lexicon.slice(0, 6).join(", ") : "—"]);
      });
    });
    if (all.length) {
      s.appendChild(more("Show the full reference set (" + all.length + " rows)", function (body) {
        body.appendChild(table(
          [{ label: "Type" }, { label: "Code" }, { label: "Label" }, { label: "Weight", n: true },
           { label: "Lexicon (first six)" }],
          all, { maxHeight: "340px" }));
        body.appendChild(h("p", { class: "hint",
          text: "Hand-curated on purpose. There are only a few dozen of these, a parsing error in "
            + "any one would corrupt every downstream score, and no automation saves enough work "
            + "to be worth that risk." }));
      }));
    } else {
      s.appendChild(h("p", { class: "hint", text:
        "No strategy references are stored in this database — Stage 0 has not been run against it." }));
    }
    page.appendChild(s);
  }

  function buildStage1(page) {
    var D = BT.D;
    var s = section("stage1", "1", "Signal collection",
      "Six sources, each good for a different kind of signal and each with a different lag.");

    s.appendChild(h("div", { class: "callout", style: "border-left-color:var(--h3)" }, [
      h("strong", { text: "The scan frame is the method. " }),
      "A horizon scan cannot surface a trend it never collected, so the frame — not the scoring " +
      "weights — is the single biggest determinant of what you are looking at. ",
      h("a", { href: D.repo_url + "/blob/main/data/strategy/scan_frame.yaml",
        target: "_blank", rel: "noopener noreferrer", text: "Read the frame" }),
      " before reading any result."
    ]));

    var STATUS = {
      success: ["t-good", "every query returned"],
      partial: ["t-warn", "some queries failed; the rest were kept"],
      skipped: ["t-muted", "not run — usually a missing key"],
      failed: ["t-bad", "the source returned nothing"]
    };
    var rows = (D.collection || []).map(function (c) {
      var meta = STATUS[c.worst] || ["t-muted", ""];
      return [
        h("span", { class: "mono", text: c.source }),
        h("span", { class: "tag " + meta[0], text: c.worst }),
        fmt.int(c.queries),
        fmt.int(c.records),
        h("span", { class: "note", text: meta[1] })
      ];
    });
    if (rows.length) {
      s.appendChild(h("h4", { style: "margin:16px 0 7px", text: "What each source returned on this run" }));
      s.appendChild(table([{ label: "Source" }, { label: "Outcome" }, { label: "Queries", n: true },
        { label: "Records", n: true }, { label: "Meaning" }], rows));
      s.appendChild(h("p", { class: "hint", text:
        "One source failing must not end a scan, so every outcome is recorded rather than "
        + "retried into the ground. That is what makes a silently dead collector show up as a "
        + "skipped row instead of as “fewer results this week”." }));
    }

    var corpus = D.corpus;
    if (corpus.years.length) {
      var colors = corpus.sources.map(function (_, i) { return BT.catColor(i, 0.92); });
      s.appendChild(h("h4", { style: "margin:20px 0 7px", text: "The corpus, by year and source" }));
      s.appendChild(BT.chart.stacked(corpus.by_year_source, corpus.years, colors, {
        seriesLabels: corpus.sources, height: 140,
        ariaLabel: "Documents collected per year, split by source"
      }));
      s.appendChild(h("div", { class: "legendrow" }, corpus.sources.map(function (src, i) {
        return h("span", null, [
          h("span", { class: "swatch", style: "background:" + colors[i] }),
          src + " · " + fmt.int(corpus.by_source[src])
        ]);
      })));
      s.appendChild(h("p", { class: "hint", text:
        "The recent slices are thicker because indexing lags: a 2019 paper has been in Crossref "
        + "for years and this month's preprint has not. Growth is measured against the corpus, "
        + "not against a flat expectation, precisely so that this shape does not read as a trend." }));
    }

    var steepv = Object.keys(corpus.by_steepv || {});
    if (steepv.length) {
      s.appendChild(h("h4", { style: "margin:20px 0 7px", text: "STEEPV coverage" }));
      s.appendChild(BT.chart.bars(
        steepv.map(function (k) { return corpus.by_steepv[k]; }), steepv,
        { height: 100, colors: steepv.map(function (_, i) { return BT.catColor(i + 7, 0.9); }),
          ariaLabel: "Documents per STEEPV category" }));
      s.appendChild(h("p", { class: "hint", text:
        "The frame is strongest on Technological and weakest on Social, Values and Environmental. "
        + "That mirrors where free structured data exists, not where the opportunities are — it is "
        + "a known limitation of this scan, not a finding about the world." }));
    }
    page.appendChild(s);
  }

  function buildStage2(page) {
    var D = BT.D, M = D.method;
    var s = section("stage2", "2", "Emergence detection",
      "Documents are embedded and clustered into topics; each topic is then tested for a burst, " +
      "fitted to a growth curve, and scored on the five attributes Rotolo et al. use to define " +
      "an emerging technology.");

    s.appendChild(examplePicker());

    /* --- topic formation ------------------------------------------------ */
    s.appendChild(h("h3", { style: "margin:20px 0 6px", text: "Topic formation" }));
    var bp = M.bertopic || {};
    var formRows = [
      ["Clustering method", M.clustering_method],
      ["Embedding backend", D.backend + " (" + D.dimensions + " dimensions)"],
      ["Similarity threshold",
        M.similarity_threshold == null ? "not set for this pairing" : String(M.similarity_threshold)],
      ["Minimum topic size", M.min_topic_size == null ? "—" : String(M.min_topic_size)],
      ["Maximum topics", M.max_topics == null ? "—" : String(M.max_topics)]
    ];
    if (M.clustering_method === "bertopic") {
      formRows.push(["UMAP neighbours / components / min_dist",
        bp.n_neighbors + " / " + bp.n_components + " / " + bp.min_dist]);
      formRows.push(["UMAP seed", String(bp.random_state)]);
      formRows.push(["HDBSCAN selection", String(bp.cluster_selection_method)]);
    }
    s.appendChild(table([{ label: "Setting" }, { label: "This run" }], formRows));

    s.appendChild(h("div", { class: "callout", style: "margin-top:12px" }, [
      h("strong", { text: "A cosine's scale belongs to the backend, not to the pipeline. " }),
      "Under a hashed lexical backend an unrelated pair of documents scores near zero; under " +
      "BGE it still scores 0.35–0.5. So one threshold is a real filter under one backend and " +
      "no filter at all under another, and every similarity cut-off here is stored per method " +
      "and per backend rather than as a single number."
    ]));

    if (M.clustering_method === "bertopic") {
      s.appendChild(h("div", { class: "callout" }, [
        h("strong", { text: "What the seed buys, and what it cannot. " }),
        "UMAP's initialisation is stochastic, so the seed is not bookkeeping: without it two runs " +
        "over an identical corpus disagree about what the topics are. With it they agree exactly. " +
        "What it cannot fix is that UMAP fits a manifold to the whole corpus — next week's " +
        "documents move this week's topics rather than merely adding to them. That cost was " +
        "accepted deliberately, and it is the reason scores are not comparable across runs."
      ]));
    }

    /* --- Kleinberg ------------------------------------------------------ */
    s.appendChild(h("h3", { style: "margin:22px 0 6px", text: "Kleinberg burst detection" }));
    s.appendChild(h("p", { class: "lede" },
      "The two-state case of Kleinberg's automaton. Per time slice, the topic's document count " +
      "is tested against the corpus total under a base rate and an elevated rate, and the " +
      "minimum-cost state sequence is found exactly by Viterbi."));
    s.appendChild(h("div", { class: "callout" }, [
      h("strong", { text: "The critical property: " }),
      "a topic growing at the same rate as the corpus does ",
      h("em", { text: "not" }),
      " burst. Without that, every topic in a growing corpus looks like it is taking off. " +
      "Burst intensity is then combined with recency, so a topic that peaked five years ago " +
      "scores below one peaking now."
    ]));

    var burstBox = h("div", { class: "card tight", style: "margin-top:12px" });
    s.appendChild(burstBox);
    onExample(function (t) {
      BT.clear(burstBox);
      burstBox.appendChild(h("div", { style: "display:flex;justify-content:space-between;gap:10px;flex-wrap:wrap;align-items:baseline" }, [
        h("strong", { text: t.short }),
        h("span", { class: "note", text:
          "peak intensity " + fmt.score(t.burst_weight) +
          (t.burst_slices.length ? " · in burst: " + t.burst_slices.join(", ") : " · never entered the burst state") })
      ]));
      burstBox.appendChild(t.timeseries.length
        ? BT.chart.timeseries(t.timeseries, { width: 700, height: 92 })
        : h("p", { class: "hint", text: "No stored time series for this topic." }));
      burstBox.appendChild(h("p", { class: "hint", text:
        "Each bar is one time slice. Slices the automaton placed in the elevated state are drawn "
        + "solid; the rest are faded." }));
    });

    /* --- growth and horizons -------------------------------------------- */
    s.appendChild(h("h3", { style: "margin:22px 0 6px", text: "Growth curve and Three Horizons" }));
    s.appendChild(h("p", { class: "lede" },
      "A logistic curve is fitted to cumulative counts by direct least squares. Maturity is the " +
      "fitted position on that curve at the last slice — and that position, not the topic's age, " +
      "decides its horizon band."));

    var th = M.three_horizons || {};
    var bandBox = h("div", { class: "card tight", style: "margin-top:10px" });
    s.appendChild(bandBox);
    (function () {
      var counts = { H1: 0, H2: 0, H3: 0 };
      D.topics.forEach(function (t) { if (counts[t.horizon] != null) counts[t.horizon]++; });
      bandBox.appendChild(h("h4", { text: "Where this run's topics fall" }));
      bandBox.appendChild(h("div", { class: "grid g3", style: "margin-top:9px" }, [
        band("H3", "maturity below " + th.h2_max_maturity, counts.H3,
          "The emerging paradigm — marginal today, and what a horizon scan exists to find."),
        band("H2", "maturity " + th.h2_max_maturity + " to " + th.h1_max_maturity, counts.H2,
          "The transition zone, where today's business gets disrupted."),
        band("H1", "maturity above " + th.h1_max_maturity, counts.H1,
          "The current paradigm, declining in relevance.")
      ]));
    })();

    function band(hz, range, n, text) {
      return h("div", { class: "quad", style: "border-left:3px solid " + BT.horizonColor(hz) }, [
        h("div", { class: "qn" }, [h("span", { class: "tag t-" + hz, text: hz }), " " + range]),
        h("div", { style: "font-size:20px;font-weight:650;margin-top:4px" },
          [String(n), h("small", { class: "note", style: "font-weight:500", text: " topics" })]),
        h("div", { class: "qd", text: text })
      ]);
    }

    var horizonBox = h("div", { class: "card tight", style: "margin-top:10px" });
    s.appendChild(horizonBox);
    onExample(function (t) {
      BT.clear(horizonBox);
      horizonBox.appendChild(h("div", { style: "display:flex;gap:9px;align-items:baseline;flex-wrap:wrap" }, [
        h("strong", { text: t.short }), BT.horizonTag(t.horizon),
        h("span", { class: "note", text:
          "fitted maturity " + fmt.score(t.maturity) + " · CAGR " + fmt.signed(t.cagr, 3) +
          " · " + t.first_slice + "–" + t.last_slice })
      ]));
      horizonBox.appendChild(maturityScale(t.maturity, th));
    });

    s.appendChild(h("div", { class: "callout trap", style: "margin-top:12px" }, [
      h("strong", { text: "A trap worth naming. " }),
      "The obvious way to fit a logistic is to linearise it. It fails here: for a series still in " +
      "its exponential phase, ln(y/(K−y)) is near-linear for any sufficiently large K, so " +
      "maximising linearity picks the smallest K and reports a young technology as saturated — " +
      "precisely backwards, and precisely the case Three Horizons exists to identify. Fitting the " +
      "curve itself has neither problem, and a regression test guards it."
    ]));

    /* --- Rotolo --------------------------------------------------------- */
    s.appendChild(h("h3", { style: "margin:22px 0 6px", text: "The Rotolo emergence score" }));
    s.appendChild(h("p", { class: "lede" },
      "Rotolo, Hicks & Martin (2015) define emergence through five attributes. Each is measured, " +
      "percentile-ranked within the run, and combined on these weights:"));

    var rw = M.rotolo_weights || {};
    s.appendChild(weightBar(rw, function (k, i) { return BT.catColor(i * 3 + 1, 0.92); }));
    s.appendChild(h("div", { class: "legendrow" }, Object.keys(rw).map(function (k, i) {
      return h("span", null, [
        h("span", { class: "swatch", style: "background:" + BT.catColor(i * 3 + 1, 0.92) }),
        k + " " + Number(rw[k]).toFixed(2)
      ]);
    })));

    var rotoloBox = h("div", { class: "card tight", style: "margin-top:12px" });
    s.appendChild(rotoloBox);
    onExample(function (t) {
      BT.clear(rotoloBox);
      rotoloBox.appendChild(h("div", { style: "display:flex;justify-content:space-between;align-items:baseline;gap:10px;flex-wrap:wrap" }, [
        h("strong", { text: t.short }),
        h("span", { class: "note", text: "emergence score " + fmt.score(t.emergence_score) })
      ]));
      Object.keys(rw).forEach(function (k) {
        rotoloBox.appendChild(BT.scoreBar(k, t[k], {
          extent: { lo: 0, hi: 1 },
          label: fmt.title(k) + "  ×" + Number(rw[k]).toFixed(2),
          color: BT.catColor(Object.keys(rw).indexOf(k) * 3 + 1, 0.85)
        }));
      });
      rotoloBox.appendChild(h("p", { class: "hint", text:
        "Multiply each bar by its weight and add: that is the emergence score. The Topics view "
        + "does the same arithmetic for every topic, and the peer-review notebook recomputes it "
        + "from the stored inputs so a reader can check it rather than trust it." }));
    });

    s.appendChild(h("div", { class: "callout warn", style: "margin-top:10px" }, [
      h("strong", { text: "The weights are a judgement, not a finding. " }),
      "They were set by reading Rotolo, not by fitting to a known outcome. Impact in particular " +
      "is ranked within each source on purpose: arXiv reports no citations at all, so a global " +
      "ranking would put every preprint at the bottom and systematically penalise the " +
      "fastest-moving evidence in the corpus."
    ]));

    /* --- weak signals --------------------------------------------------- */
    s.appendChild(h("h3", { style: "margin:22px 0 6px", text: "Weak-signal classification" }));
    s.appendChild(h("p", { class: "lede" },
      "Topics are placed on average proportion × growth, split at the median of each."));
    var counts = { weak: 0, strong: 0, latent: 0, noise: 0 };
    D.topics.forEach(function (t) { if (counts[t.signal_class] != null) counts[t.signal_class]++; });
    s.appendChild(h("div", { class: "quadgrid", style: "margin-top:10px" }, [
      signalQuad("weak", "Low volume · high growth", counts.weak, true),
      signalQuad("strong", "High volume · high growth", counts.strong),
      signalQuad("noise", "Low volume · low growth", counts.noise),
      signalQuad("latent", "High volume · low growth", counts.latent)
    ]));
    page.appendChild(s);

    function signalQuad(key, axes, n, hi) {
      return h("div", { class: "quad" + (hi ? " hi" : "") }, [
        h("div", { class: "qn" }, [
          h("span", { class: "tag t-" + key, text: key }),
          h("span", { style: "float:right;font-weight:650", text: n + " topics" })
        ]),
        h("div", { class: "qd", text: axes }),
        h("div", { class: "qd", style: "margin-top:3px", text: BT.SIGNAL_TEXT[key] })
      ]);
    }
  }

  function maturityScale(maturity, th) {
    var w = 660, hh = 52;
    var h2 = Number(th.h2_max_maturity), h1 = Number(th.h1_max_maturity);
    var x = function (v) { return 6 + v * (w - 12); };
    var bands = [
      ["H3", 0, h2, BT.horizonColor("H3")],
      ["H2", h2, h1, BT.horizonColor("H2")],
      ["H1", h1, 1, BT.horizonColor("H1")]
    ];
    var kids = bands.map(function (b) {
      return BT.svg("g", null, [
        BT.svg("rect", { x: x(b[1]), y: 14, width: Math.max(1, x(b[2]) - x(b[1])), height: 15,
          fill: b[3], opacity: 0.22, rx: 2 }),
        BT.svg("text", { x: (x(b[1]) + x(b[2])) / 2, y: 44, class: "lab mid",
          text: b[0] + "  " + b[1].toFixed(2) + "–" + b[2].toFixed(2) })
      ]);
    });
    if (maturity != null) {
      kids.push(BT.svg("line", { x1: x(maturity), x2: x(maturity), y1: 8, y2: 33,
        stroke: BT.cssVar("--fg"), "stroke-width": 2 }));
      kids.push(BT.svg("text", { x: x(maturity), y: 6, class: "lab mid",
        fill: BT.cssVar("--fg"), text: fmt.score(maturity) }));
    }
    return BT.svg("svg", { class: "chart", viewBox: "0 0 " + w + " " + hh, role: "img",
      "aria-label": "Fitted maturity against the Three Horizons cut-points" }, kids);
  }

  function buildStage3(page) {
    var D = BT.D, M = D.method;
    var s = section("stage3", "3", "Strategic fit and asset leverage",
      "Two axes, both built the same way: cosine similarity of the topic to a reference vector, " +
      "plus lexical overlap, times the reference's own priority weight.");

    var sf = M.strategic_fit || {}, al = M.asset_leverage || {};
    s.appendChild(h("div", { class: "grid g2", style: "margin:14px 0" }, [
      h("div", { class: "card tight" }, [
        h("h4", { text: "Strategic fit blend" }),
        weightBar({ embedding: sf.embedding_weight, lexicon: sf.lexicon_weight },
          function (k) { return k === "embedding" ? BT.cssVar("--accent") : BT.cssVar("--h2"); }),
        h("p", { class: "hint", text: "Against the Corporate Plan objectives and initiatives." })
      ]),
      h("div", { class: "card tight" }, [
        h("h4", { text: "Asset leverage blend" }),
        weightBar({ embedding: al.embedding_weight, lexicon: al.lexicon_weight },
          function (k) { return k === "embedding" ? BT.cssVar("--accent") : BT.cssVar("--h2"); }),
        h("p", { class: "hint", text: "Against the inventory of what IP Australia already holds." })
      ])
    ]));

    s.appendChild(h("div", { class: "callout" }, [
      h("strong", { text: "Best match, not the mean. " }),
      "A topic takes the score of its single best-matching reference. A trend speaking directly " +
      "to one objective is a strong fit; averaging that against eight unrelated objectives would " +
      "bury it and push every topic toward the same middling score. Topics are also represented " +
      "by their label terms rather than their member documents, because document text carries a " +
      "great deal of shared academic boilerplate that pulls every topic vector toward the same region."
    ]));

    var matching = M.critical_tech_matching;
    s.appendChild(h("div", { class: "callout " + (matching ? "" : "warn"), style: "margin-top:10px" }, [
      h("strong", { text: "DISR critical-technology match: " }),
      matching
        ? "a cut-off of " + M.critical_tech_threshold + " has been swept for the " + D.backend +
          " backend, and a matching topic receives a fixed bonus of " +
          (sf.critical_tech_bonus == null ? "—" : sf.critical_tech_bonus) +
          ". The match is binary because the DISR list is a policy designation: a topic either " +
          "falls in a national-interest field or it does not."
        : "no cut-off has been swept for the " + D.backend + " backend, so no topic is matched " +
          "and no topic receives the bonus. An empty critical-technology column on this run means " +
          "“not measured”, not “not in a DISR field”. Borrowing a threshold swept in a " +
          "different vector space is how a national-interest designation ended up printed on 114 " +
          "of 114 topics, meaning nothing."
    ]));

    var quads = { "act": 0, "on-strategy, no right-to-play": 0,
      "capability looking for a problem": 0, "watch": 0 };
    D.topics.forEach(function (t) { if (quads[t.fit_quadrant] != null) quads[t.fit_quadrant]++; });
    s.appendChild(h("h4", { style: "margin:18px 0 8px", text: "The 2×2, on this run" }));
    s.appendChild(h("div", { class: "quadgrid" }, [
      fitQuad("act", "High fit · high leverage", quads["act"], true),
      fitQuad("on-strategy, no right-to-play", "High fit · low leverage", quads["on-strategy, no right-to-play"]),
      fitQuad("capability looking for a problem", "Low fit · high leverage", quads["capability looking for a problem"]),
      fitQuad("watch", "Low fit · low leverage", quads["watch"])
    ]));
    s.appendChild(h("p", { class: "hint", text:
      "Split at the median of each axis within this run, so the quadrants always divide the "
      + "topics roughly into quarters. They rank topics against each other, not against an "
      + "absolute standard of “good fit”." }));
    s.appendChild(h("p", { style: "margin-top:12px" }, [
      h("button", { class: "btn", type: "button",
        onclick: function () { BT.go("scores", { preset: "fit" }); },
        text: "Open this 2×2 as a plot →" })
    ]));
    page.appendChild(s);

    function fitQuad(key, axes, n, hi) {
      return h("div", { class: "quad" + (hi ? " hi" : "") }, [
        h("div", { class: "qn" }, [key, h("span", { style: "float:right;font-weight:650", text: n + " topics" })]),
        h("div", { class: "qd", text: axes })
      ]);
    }
  }

  function buildStage4(page) {
    var D = BT.D, M = D.method;
    var s = section("stage4", "4", "Opportunity index",
      "A relative ordering of the signals that usually accompany a large opportunity.");

    s.appendChild(h("div", { class: "callout warn" }, [
      h("strong", { text: "Read this before using any number from this stage. " }),
      "It is not a market size. It is not a dollar figure. It cannot be converted into one. " +
      "McKinsey-style value pools are bottom-up gross-margin models built from segment-level " +
      "expert assumptions, and there is no free feed for them. What this produces is an ordering: " +
      "given two topics ",
      h("em", { text: "in the same run" }),
      ", which has more of those signals. Components are percentile-ranked within the run before " +
      "combining, which is what makes them addable at all — and which also means an index of 0.8 " +
      "last month and 0.8 this month say nothing about each other."
    ]));

    var comp = M.index_components || {};
    s.appendChild(h("h4", { style: "margin:16px 0 7px", text: "Component weights" }));
    s.appendChild(weightBar(comp, function (k, i) { return BT.catColor(i * 4 + 2, 0.92); }));
    s.appendChild(h("div", { class: "legendrow" }, Object.keys(comp).map(function (k, i) {
      return h("span", null, [
        h("span", { class: "swatch", style: "background:" + BT.catColor(i * 4 + 2, 0.92) }),
        k.replace(/_/g, " ") + " " + Number(comp[k]).toFixed(2)
      ]);
    })));

    var suppressed = D.topics.filter(function (t) { return t.index_suppressed; }).length;
    var present = {};
    D.topics.forEach(function (t) {
      Object.keys(t.index_components || {}).forEach(function (k) { present[k] = true; });
    });
    var missing = Object.keys(comp).filter(function (k) { return !present[k]; });

    s.appendChild(h("div", { class: "grid g2", style: "margin-top:14px" }, [
      h("div", { class: "card tight" }, [
        h("h4", { text: "Thin topics are suppressed, not scored" }),
        h("div", { style: "font-size:20px;font-weight:650;margin:4px 0" },
          [String(suppressed), h("small", { class: "note", style: "font-weight:500",
            text: " of " + D.topics_total + " topics" })]),
        h("p", { class: "qd", text:
          "Below " + M.index_min_documents + " documents the index is null and flagged. A "
          + "composite built on eight documents looks identical to one built on eight hundred, "
          + "and that is how a horizon scan misleads people." })
      ]),
      h("div", { class: "card tight" }, [
        h("h4", { text: "Missing components have their weight redistributed" }),
        h("div", { style: "font-size:20px;font-weight:650;margin:4px 0" },
          [String(missing.length), h("small", { class: "note", style: "font-weight:500",
            text: " components with no data" })]),
        h("p", { class: "qd", text: missing.length
          ? missing.join(", ").replace(/_/g, " ") + " contributed nothing, so the remaining "
            + "weights were rescaled to sum to 1."
          : "Every component had data on this run, so no redistribution was needed." }),
        h("p", { class: "hint", text:
          "Without redistribution, disabling one source would silently shrink every index and "
          + "the ranking would look unchanged while measuring something different." })
      ])
    ]));
    page.appendChild(s);
  }

  function buildStage5(page) {
    var D = BT.D, M = D.method;
    var s = section("stage5", "5", "Synthesis and ranking",
      "The headline ordering: emergence, strategic fit and asset leverage, combined on these weights.");

    var rw = M.rank_weights || {};
    s.appendChild(weightBar(rw, function (k) {
      return k === "emergence" ? BT.cssVar("--h3")
        : k === "strategic_fit" ? BT.cssVar("--accent") : BT.cssVar("--h2");
    }));
    s.appendChild(h("div", { class: "legendrow" }, Object.keys(rw).map(function (k) {
      return h("span", null, [
        h("span", { class: "swatch", style: "background:" + (k === "emergence" ? BT.cssVar("--h3")
          : k === "strategic_fit" ? BT.cssVar("--accent") : BT.cssVar("--h2")) }),
        k.replace(/_/g, " ") + " " + Number(rw[k]).toFixed(2)
      ]);
    })));

    s.appendChild(h("div", { class: "callout", style: "margin-top:12px" }, [
      h("strong", { text: "The opportunity index is deliberately excluded from the ranking. " }),
      "It is the weakest-founded number in the pipeline, and folding it into the headline ordering " +
      "would launder that weakness. It is reported alongside, where a reader can weigh it themselves."
    ]));

    var top = D.topics.slice(0, Math.min(10, D.topics.length));
    s.appendChild(h("h4", { style: "margin:18px 0 7px", text: "The top of this run's shortlist" }));
    s.appendChild(table(
      [{ label: "#", n: true }, { label: "Topic" }, { label: "Horizon" },
       { label: "Emergence", n: true }, { label: "Fit", n: true }, { label: "Leverage", n: true },
       { label: "Rank score", n: true }],
      top.map(function (t) {
        return [
          String(t.rank || "—"),
          h("a", { href: "#", class: "nowrap", onclick: function (e) {
            e.preventDefault(); BT.go("topics", { topic: t.id });
          }, text: t.short }),
          BT.horizonTag(t.horizon),
          fmt.score(t.emergence_score), fmt.score(t.strategic_fit),
          fmt.score(t.asset_leverage), fmt.score(t.composite_rank_score, 3)
        ];
      })));

    s.appendChild(h("div", { class: "callout", style: "margin-top:14px" }, [
      h("strong", { text: "Do not skip the evidence cards. " }),
      "Every shortlisted topic carries the primary documents behind its scores. If those documents " +
      "do not look like a coherent theme, the topic is a clustering artefact and should be " +
      "discarded. Reading them is the cheapest and most reliable quality control in the whole " +
      "method, and nothing on this page replaces it."
    ]));

    s.appendChild(h("p", { class: "rowactions", style: "margin-top:14px" }, [
      h("a", { class: "btn", href: "index.html", text: "The ranked report →" }),
      h("a", { class: "btn", href: D.repo_url + "/tree/main/data/outputs/" + D.run_id,
        target: "_blank", rel: "noopener noreferrer", text: "Evidence cards and the notebook →" }),
      h("button", { class: "btn", type: "button", onclick: function () { BT.go("topics"); },
        text: "Every topic and every score →" })
    ]));
    page.appendChild(s);
  }

  function buildLimits(page) {
    var s = section("limits", null, "What this method cannot do",
      "Stated here rather than buried, because each of these is a way the output can be read " +
      "as more than it is.");
    var limits = [
      ["It cannot find what the scan frame does not ask for",
        "The frame is strongest on Technological and weakest on Social, Values and Environmental — "
        + "mirroring where free structured data exists, not where opportunities are."],
      ["It cannot size a market",
        "The opportunity index is a within-run ordering. There is no dollar figure anywhere in this pipeline."],
      ["It cannot tell a real trend from a well-populated artefact",
        "Only reading the evidence cards does that. Some topics will be clustering artefacts."],
      ["It cannot replace the qualitative work",
        "Scenario planning, visioning, Delphi and backcasting are participatory by nature. This "
        + "pipeline surfaces candidates so those sessions start from evidence instead of a blank wall."],
      ["Its rankings are not yet validated",
        "Until the pipeline is tested against a known past opportunity, the weights are a hypothesis."]
    ];
    s.appendChild(h("div", { class: "grid g2", style: "margin-top:12px" }, limits.map(function (l) {
      return h("div", { class: "quad" }, [
        h("div", { class: "qn", style: "text-transform:none;font-size:12.5px;color:var(--fg)", text: l[0] }),
        h("div", { class: "qd", text: l[1] })
      ]);
    })));
    page.appendChild(s);

    var r = section("refs", null, "References", null);
    r.appendChild(h("ul", { class: "refs", style: "margin-top:10px" }, [
      h("li", null, "Kleinberg, J. (2003). “Bursty and Hierarchical Structure in Streams.” "
        + "Data Mining and Knowledge Discovery 7:373–397."),
      h("li", null, "Rotolo, D., Hicks, D., & Martin, B. (2015). “What is an emerging technology?” "
        + "Research Policy 44(10):1827–1843."),
      h("li", null, "Boutaleb et al. (2024). BERTrend. ACL FuturED workshop."),
      h("li", null, "Curry, A., & Hodgson, A. (2008). “Seeing in Multiple Horizons.” "
        + "Journal of Futures Studies 13(1):1–20."),
      h("li", null, "Keeley, Pikkel, Quinn & Walters (2013). Ten Types of Innovation."),
      h("li", null, "Venna, J., & Kaski, S. (2001). “Neighborhood preservation in nonlinear "
        + "projection methods.” ICANN — the trustworthiness and continuity measures reported "
        + "on the Map view."),
      h("li", null, "UK Government Office for Science, Futures Toolkit (updated August 2024).")
    ]));
    r.appendChild(h("p", { class: "hint", style: "margin-top:12px" }, [
      "The full method document, the runbooks and the source are in the repository: ",
      h("a", { href: BT.D.repo_url + "/blob/main/docs/method.md", target: "_blank",
        rel: "noopener noreferrer", text: "docs/method.md" }), ". ",
      "This page was generated from run " + BT.D.run_id + " at " + BT.D.generated_at + "."
    ]));
    page.appendChild(r);
  }

  /* ---------------------------------------------------------------- api */

  return {
    mount: function () {
      root = document.getElementById("methodPage");
      BT.clear(root);
      exampleHooks = [];
      buildIntro(root);
      buildStage0(root);
      buildStage1(root);
      buildStage2(root);
      buildStage3(root);
      buildStage4(root);
      buildStage5(root);
      buildLimits(root);
    },
    /* Rebuilt on a theme change: the SVG charts bake resolved colours in. */
    refresh: function () { if (root) BT.views.method.mount(); }
  };
})();
