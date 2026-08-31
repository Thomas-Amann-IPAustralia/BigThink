/* ==========================================================================
   boot.js — routing, theme and the top bar. Loaded last.

   Views are addressable: the hash carries the view and, where it makes sense,
   the topic. That means a colleague can be sent a link to one topic's
   breakdown rather than to "the dashboard, then click around" — which is the
   difference between a page people cite and a page people describe.
   ========================================================================== */

(function () {
  var h = BT.h, D = BT.D;
  var VIEWS = ["method", "map", "topics", "scores", "data"];
  var current = null;

  /* ---------------------------------------------------------------- theme */

  function applyTheme(name) {
    document.documentElement.setAttribute("data-theme", name);
    try { localStorage.setItem("bigthink-theme", name); } catch (e) { /* private mode */ }
    var btn = document.getElementById("themeBtn");
    if (btn) {
      btn.textContent = name === "dark" ? "☾" : "☀";
      btn.setAttribute("aria-label",
        name === "dark" ? "Switch to the light theme" : "Switch to the dark theme");
    }
    /* The canvases and the inline SVGs bake resolved colours in, so every
       view has to be told rather than left to a CSS variable it already read. */
    VIEWS.forEach(function (v) {
      if (BT.views[v] && BT.views[v].refresh) BT.views[v].refresh();
    });
  }

  /* ---------------------------------------------------------------- routing */

  function parseHash() {
    var raw = (location.hash || "").replace(/^#/, "");
    if (!raw) return { view: "method", params: {} };
    var parts = raw.split("&");
    var out = { view: parts[0] || "method", params: {} };
    parts.slice(1).forEach(function (p) {
      var kv = p.split("=");
      out.params[kv[0]] = decodeURIComponent(kv.slice(1).join("=") || "");
    });
    if (VIEWS.indexOf(out.view) < 0) out.view = "method";
    return out;
  }

  function show(view, params, pushHash) {
    params = params || {};
    VIEWS.forEach(function (v) {
      var section = document.getElementById("view-" + v);
      var tab = document.getElementById("tab-" + v);
      var on = v === view;
      section.classList.toggle("active", on);
      tab.setAttribute("aria-selected", String(on));
      tab.tabIndex = on ? 0 : -1;
    });

    if (BT.views[view] && BT.views[view].mount) BT.views[view].mount();
    if (BT.views[view] && BT.views[view].resize) BT.views[view].resize();

    if (params.topic) {
      if (view === "topics" && BT.views.topics.openTopic) BT.views.topics.openTopic(params.topic);
      if (view === "map" && BT.views.map.focusTopicId) BT.views.map.focusTopicId(params.topic);
      if (view === "scores" && BT.views.scores.selectTopic) BT.views.scores.selectTopic(params.topic);
    }

    current = view;
    if (pushHash !== false) {
      var hash = "#" + view + (params.topic ? "&topic=" + encodeURIComponent(params.topic) : "");
      if (location.hash !== hash) history.replaceState(null, "", hash);
    }
  }

  /* Cross-view navigation, used by every "→" button on the page. */
  BT.go = function (view, params) { show(view, params || {}, true); };

  /* ---------------------------------------------------------------- top bar */

  function buildTopBar() {
    var pill = document.getElementById("runPill");
    BT.clear(pill);
    pill.appendChild(h("b", { text: D.run_id }));
    pill.appendChild(document.createTextNode(
      " · " + Number(D.documents_total).toLocaleString() + " docs · " +
      D.topics_total + " topics · " + D.backend + "/" + D.method.clustering_method));
    pill.title =
      "Run " + D.run_id + ", generated " + D.generated_at + ". " +
      Number(D.documents_plotted).toLocaleString() + " of " +
      Number(D.documents_total).toLocaleString() + " documents are plotted on the map. " +
      "Embedding backend " + D.backend + " (" + D.dimensions + " dimensions), clustered by " +
      D.method.clustering_method + ", projected with " + D.projection.resolved + ". " +
      "Scores are not comparable with another run.";

    document.getElementById("themeBtn").addEventListener("click", function () {
      applyTheme(document.documentElement.getAttribute("data-theme") === "dark" ? "light" : "dark");
    });

    var tabs = Array.prototype.slice.call(document.querySelectorAll(".tab"));
    tabs.forEach(function (tab, i) {
      tab.addEventListener("click", function () { BT.go(tab.dataset.view); });
      /* Arrow-key movement between tabs is what makes a role="tablist"
         truthful rather than decorative. */
      tab.addEventListener("keydown", function (e) {
        var delta = e.key === "ArrowRight" ? 1 : e.key === "ArrowLeft" ? -1 : 0;
        if (!delta) return;
        e.preventDefault();
        var next = tabs[(i + delta + tabs.length) % tabs.length];
        next.focus();
        BT.go(next.dataset.view);
      });
    });
  }

  /* ------------------------------------------------------------------ boot */

  var stored = null;
  try { stored = localStorage.getItem("bigthink-theme"); } catch (e) { /* private mode */ }
  applyTheme(stored === "dark" || stored === "light" ? stored
    : (window.matchMedia && window.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light"));

  buildTopBar();

  var route = parseHash();
  show(route.view, route.params, false);

  window.addEventListener("hashchange", function () {
    var r = parseHash();
    if (r.view !== current) show(r.view, r.params, false);
  });

  window.addEventListener("resize", function () {
    if (current && BT.views[current] && BT.views[current].resize) BT.views[current].resize();
  });

  /* A keyboard shortcut per view. Cheap, and the difference between a tool
     someone uses daily and one they click through. */
  window.addEventListener("keydown", function (e) {
    if (e.metaKey || e.ctrlKey || e.altKey) return;
    var tag = (e.target.tagName || "").toLowerCase();
    if (tag === "input" || tag === "select" || tag === "textarea") return;
    var idx = ["1", "2", "3", "4", "5"].indexOf(e.key);
    if (idx >= 0) { e.preventDefault(); BT.go(VIEWS[idx]); }
  });
})();
