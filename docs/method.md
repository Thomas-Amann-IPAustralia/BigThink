# Method

How this pipeline turns published signals into a ranked shortlist of
opportunities, what each number means, and — more usefully — what each number
does not mean.

This document is the reference. `PROJECT_STATE.md` is the live record of what
has been decided, what is still open, and what to do next.

---

## The chain of reasoning

```
Strategy (Stage 0)  ──┐
                      ├──> Strategic fit    ──┐
Signals (Stage 1)  ───┤                       │
                      ├──> Emergence (S2)   ──┼──> Ranked shortlist (Stage 5)
Assets (Stage 0)   ───┤                       │
                      ├──> Asset leverage  ───┘
Attention (Stage 1) ──┴──> Opportunity index (S4)   [reported, not ranked on]
```

Each stage reads its inputs from DuckDB and writes its outputs back. No stage
passes Python objects to another. That is what makes any stage re-runnable in
isolation and what makes a month-old result explainable: the database holds the
config snapshot that produced it.

---

## Stage 0 — Strategy encoding

**Input.** `data/strategy/objectives.yaml` (hand-transcribed from the 2026-27
Strategic Corporate Plan), `critical_technologies.yaml` (DISR), and
`asset_inventory.yaml` (what IP Australia can bring).

**Output.** One `strategy_refs` row per objective, initiative, critical
technology field and asset. Each carries a text block (embedded at scoring
time) and a hand-written lexicon (matched lexically).

**Why both text and lexicon.** Embeddings catch a trend that means the same
thing in different words. The lexicon catches a trend that names the thing
exactly — "geographical indications" must score against initiative SI-1 on the
strength of the phrase, and embedding a whole paragraph can dilute that to
nothing.

**Why hand-curated.** There are fourteen objectives and initiatives. A parsing
error in any of them would corrupt every downstream score, and no automation
saves enough work to be worth that risk.

**Refresh when.** A new Corporate Plan is published. Fit scores are not
comparable across plan versions; record the change in `PROJECT_STATE.md`.

---

## Stage 1 — Signal collection

**The scan frame is the method.** `data/strategy/scan_frame.yaml` defines what
is searched for. A horizon scan cannot surface a trend it never collected, so
the frame — not the scoring weights — is the single biggest determinant of the
output. Read it before reading any result.

**Sources, and what each is good for.**

| Source | Signal | Lag | Notes |
|---|---|---|---|
| OpenAlex | Research volume, citations, institutions | ~months | **Now metered.** Needs `OPENALEX_API_KEY`; the keyless daily budget is usually spent on a shared IP |
| Crossref | Research and grey literature | ~months | Free, unmetered on the polite pool. The dependable backbone |
| arXiv | Preprints | days | Fastest signal for AI/CS/quantum. No citation counts |
| GDELT | News attention and tone | ~hours | Free, keyless, and heavily IP rate-limited — expect failures |
| data.gov.au | Policy salience, IP Australia's own datasets | years | CKAN. Registration date lags the policy it serves |
| PatentsView | US patent grants | ~18 months | Needs a key; US only. Australian filing behaviour is in IP RAPID |

Every document is tagged with a STEEPV category and the frame key that found
it, and deduplicated on a stable `doc_id`.

**Failure is expected and isolated.** One source failing must not end a scan.
A `PermanentError` (bad key, exhausted quota) retires that source for the run;
everything is recorded in `collection_log`, so a silently-dead collector shows
up as a skipped row rather than as "fewer results this week".

---

## Stage 2 — Emergence detection

### Topic formation

Documents are embedded, then clustered. Since 2026-08-31 the default is
**BERTopic over a seeded UMAP + HDBSCAN pair**: UMAP reduces the BGE vectors to
a handful of dimensions, and HDBSCAN finds density peaks in that space.
Documents HDBSCAN cannot place densely are reported as outliers and dropped
rather than forced into the nearest cluster, which would corrupt that cluster's
centroid. Two alternatives remain available in `emergence.topics.method`:
`agglomerative` (average linkage, numpy only, the right choice with no torch)
and `leader` (kept only to reproduce pre-2026-08-30 runs).

Labels come from c-TF-IDF — the same idea BERTopic uses — so a label carries
the terms that distinguish a topic from its neighbours, not merely the terms
common within it. One labelling path serves every method, so a label means the
same thing whichever produced it.

**What seeding does and does not buy.** UMAP's initialisation is stochastic, so
`emergence.topics.bertopic.random_state` is not optional bookkeeping — without
it, two runs over an identical corpus disagree about what the topics are. With
it, they agree exactly. What seeding cannot fix is that UMAP fits a manifold to
the *whole* corpus: next week's documents move this week's topics rather than
merely adding to them, and they move them more than average linkage would.

That is a real cost and it was accepted deliberately. A single scan should be
as accurate and as useful on its own as it can be; its value as a reference
point for a later run is secondary. Read alongside "**Do not compare scores
across runs**" below — which was already true, and is now more true.

### Kleinberg burst detection

Two-state case of the automaton from Kleinberg (2003), *Bursty and Hierarchical
Structure in Streams*. Per time slice, the topic's document count is tested
against the corpus total under a base rate and an elevated rate; the
minimum-cost state sequence is found exactly by Viterbi.

The critical property: a topic growing at the same rate as the corpus does
**not** burst. Without that, every topic in a growing corpus looks like it is
taking off.

Burst intensity is combined with recency, so a topic that peaked five years ago
scores below one peaking now.

### Growth and Three Horizons

A logistic curve is fitted to cumulative counts by direct least squares over a
grid of (carrying capacity, rate, inflection). Maturity is the fitted position
on that curve at the last slice, and that position — not the topic's age —
determines its Three Horizons band.

> **A trap worth naming.** The obvious way to fit a logistic is to linearise it:
> `ln(y/(K−y))` is linear in `t`. It fails here. For a series still in its
> exponential phase, that expression is near-linear for *any* sufficiently large
> `K`, so maximising linearity picks the smallest `K` and reports a young
> technology as saturated — precisely backwards, and precisely the case Three
> Horizons exists to identify. Leading zeros make it worse: clipped to a floor,
> they act as extreme outliers that dominate the fit. Fitting the curve itself,
> from the slice the topic actually starts in, has neither problem.
> `tests/test_emergence.py::test_early_exponential_growth_is_horizon_3` guards it.

### The Rotolo emergence score

Rotolo, Hicks & Martin (2015), *What is an emerging technology?*, define
emergence through five attributes: "(i) radical novelty, (ii) relatively fast
growth, (iii) coherence, (iv) prominent impact, and (v) uncertainty and
ambiguity."

| Attribute | Indicator | Default weight |
|---|---|---:|
| Novelty | Cosine distance from the early-corpus centroid | 0.25 |
| Growth | CAGR blended with Kleinberg burst intensity | 0.30 |
| Coherence | Mean cosine of members to their own centroid | 0.15 |
| Impact | Citation percentile **within source** | 0.20 |
| Uncertainty | Normalised entropy over actors and source types | 0.10 |

Impact is ranked within each source deliberately: arXiv reports no citations at
all, so a global ranking would put every preprint at the bottom and
systematically penalise the fastest-moving evidence in the corpus.

**The weights are a judgement, not a finding.** They were set by reading
Rotolo, not by fitting to a known outcome. Validate against a past opportunity
before trusting the ranking — see `PROJECT_STATE.md`.

### Weak-signal classification

Topics are placed on average-proportion × growth, split at the median:

|  | Low volume | High volume |
|---|---|---|
| **High growth** | **weak** — the horizon-scanning target | **strong** — already visible to everyone |
| **Low growth** | noise | latent — established, not moving |

---

## Stage 3 — Strategic fit and asset leverage

Both axes use the same blend: cosine similarity of the topic vector to a
reference vector, plus lexical overlap, times the reference's own priority
weight.

A topic takes the score of its single **best-matching** reference, not the mean
across all of them. A trend speaking directly to one objective is a strong fit;
averaging that against eight unrelated objectives would bury it and push every
topic toward the same middling score.

Topics are represented by their label terms rather than their member documents.
Document text carries a great deal of shared academic boilerplate that pulls
every topic vector toward the same region of the space.

A DISR critical-technology match adds a fixed bonus. It is binary because the
DISR list is a policy designation: a topic either falls in a national-interest
field or it does not.

**Whether it says anything depends on a cut-off, and the cut-off belongs to the
embedding backend.** The match is a blend that is 70% a cosine, and a cosine's
scale is a property of the backend: under `hashing` an unrelated pair scores
near zero, under `bge` it still scores 0.35-0.5. So a cut-off of 0.25 is a real
filter under one and no filter at all under the other. It was a hardcoded 0.25
until 2026-08-31 and matched **114 of 114 topics** on the only run ever produced
under the shipped `bge` default — a national-interest designation printed on
every evidence card, meaning nothing.

It now lives in
`scoring.strategic_fit.critical_tech_match.thresholds.<backend>`. **A blank
value means no topic is matched and no topic receives the bonus**, rather than
borrowing a number swept in a different vector space; the run log and
`pipeline_runs` both say so when that happens. `python -m src.calibrate
critical-tech` sweeps it.

So: an empty `critical_tech` column means either "this topic is not in a DISR
field" or "no cut-off has been swept for this backend". Check the run log to
tell which. As of 2026-08-31 the shipped `bge` default is the second.

---

## Stage 4 — Opportunity index

**Read this before using any number from this stage.**

It is not a market size. It is not a dollar figure. It cannot be converted into
one. McKinsey-style value pools are bottom-up gross-margin models built from
segment-level expert assumptions, and there is no free feed for them.

What it produces is an ordering: given two topics *in the same run*, which has
more of the signals that usually accompany a large opportunity? Components are
percentile-ranked within the run before combining, which is what makes them
addable at all — and which also means an index of 0.8 last month and 0.8 this
month say nothing about each other.

Two guards:

- **Thin topics are suppressed, not scored.** A composite built on eight
  documents looks identical to one built on eight hundred. Below
  `opportunity_index.min_documents` the index is null and flagged.
- **Missing components have their weight redistributed.** Without this,
  disabling PatentsView would silently shrink every index by 15% and the
  ranking would look unchanged while measuring something different.

---

## Stage 5 — Synthesis and ranking

Rank = weighted combination of emergence, strategic fit and asset leverage.

**The opportunity index is deliberately excluded from the ranking.** It is the
weakest-founded number in the pipeline, and folding it into the headline
ordering would launder that weakness. It is reported alongside, where a reader
can weigh it themselves.

Outputs: a ranked shortlist, one evidence card per shortlisted topic, a full
topic CSV, a machine-readable summary, a published HTML page, and a Jupyter
notebook (below).

**Every evidence card carries the primary documents behind the scores.** If
those documents do not look like a coherent theme, the topic is a clustering
artefact and should be discarded. That check is not optional — it is the
cheapest and most reliable quality control in the whole method.

Cards end with Doblin Ten Types and Seven Questions prompts, because the
qualitative stage is where a regulator's real options appear. Doblin's research
found breakthroughs usually combine several innovation types and that
product-only innovation returns the least; a regulator can innovate in process,
channel and engagement, not only in what it offers.

---

## The notebook — what is checkable, and what is only arguable

`python -m src.notebook --run-id RUN` writes
`data/outputs/<run_id>/horizon-scan-<run_id>.ipynb`: one frozen run, walked
stage by stage, with every code cell runnable against the same DuckDB and its
output already embedded from a real execution. The pipeline writes it
automatically after Stage 5.

It exists to move an argument. Four numbers are recomputed from their stored
inputs and checked against what the pipeline stored:

| Recomputed | From |
|---|---|
| `emergence_score` | the five Rotolo attributes, percentile-ranked, × `rotolo_weights` |
| `horizon` | fitted maturity × the Three Horizons cut-points |
| `opportunity_index` | stored components × effective (redistributed) weights |
| `composite_rank_score` | the three ranking axes × `rank_weights`, via `stage5_synthesis.composite_scores` itself |

The last one calls the production function rather than reimplementing it, so
the check cannot drift away from the code it is checking.

Two properties of that design are worth stating, because they are what make the
notebook worth anything:

- **Verification loads the config from `pipeline_runs.config_snapshot`, not
  from `bigthink_config.yaml`.** A weight edited after the run must not be able
  to change retrospectively what "reproduced" means. The notebook also diffs
  the two and says so when the repository has moved since the run.
- **Stage 1 is described, never re-executed.** Collectors call live,
  rate-limited, metered APIs; a cell claiming to reproduce the corpus would be
  a lie the moment a source changed its budget. The notebook reports collection
  from `collection_log` and re-derives only what is genuinely deterministic.

So what the notebook establishes is that *given this corpus and these weights,
the shortlist follows*. It establishes nothing about whether the weights are
right — which is the point. It moves the reviewable question off the arithmetic
and onto the scan frame, the weights and the topics, where the real
uncertainty lives.

Analyst commentary goes in `data/outputs/<run_id>/observations.yaml`, keyed by
anchor (`stage1`, `stage4`, `topic:<topic_id>`, …). Anything written there is
inserted as markdown the next time the notebook is generated, so the reading of
a run travels with its numbers instead of living in someone's inbox. The
notebook also derives its own observations from the run — silent sources,
topic coverage, compressed axes, suppressed indices, topics sitting within 0.05
of a horizon cut-point — because those are the caveats that are easy to miss
and expensive to miss.

---

## The published explorer — and what it measures about itself

`python -m src.dashboard --run-id RUN` writes `docs/dashboard.html`: five views
over one finished run, served from GitHub Pages.

| View | What it is for |
|---|---|
| Method | This document, made interactive and grounded in the run's own numbers |
| Map | Every collected document as a 2D point cloud |
| Topics | Every topic and every score, sortable, filterable, expandable to the arithmetic |
| Scores | Any score against any other, with the four pairs this method reasons about preset |
| Data | The run's tables, browsable, filterable and exportable |

Two things about it are worth stating here rather than leaving to the code.

**Every number on the Method view is read from the run, never hardcoded.** A
method page that describes weights the pipeline no longer uses is worse than no
method page at all — it is confidently wrong in exactly the way this project is
most anxious about. The Rotolo weights, the horizon cut-points, the
critical-technology threshold and whether it is switched on, the index
components and which of them had data: all of it comes from the payload.

**The map measures its own distortion rather than disclaiming it.** Flattening
768 dimensions to two is lossy, the loss is not uniform across topics, and none
of it is visible in the picture. So the page computes and prints four things
from the run's own vectors and coordinates:

- **Trustworthiness** — are the neighbours you can see real? It penalises points
  drawn close together that are far apart in the embedding space: the error that
  invents a cluster.
- **Continuity** — are the real neighbours visible? The dual measure, penalising
  genuinely close pairs the projection has pushed apart: the error that tears a
  real topic in half. Both are Venna & Kaski's, computed on a seeded sample
  whose size is printed beside them.
- **Neighbour purity, per topic, measured twice** — the share of a member
  document's nearest neighbours that share its topic, in the full space and
  again on the map. The gap between the two is the distortion this picture adds,
  and it lets a scattered-looking topic say whether it is incoherent or merely
  badly drawn.
- **Every plotted point's true nearest neighbours in the full space**, shipped
  to the page. Select a document, switch on "true neighbours", and the lines go
  to where its actual neighbours landed. Lines that shoot across the map are the
  projection distorting, on the reader's own data.

The projection also follows the clustering's UMAP settings by default
(`dashboard.projection.follow_clustering`), so the map is a 2-component view of
the manifold the topics were found in rather than an unrelated second opinion.
`min_dist` is deliberately not followed — the clustering runs it at 0.0, which
on a screen draws every topic as one indistinguishable dot.

None of this makes the map trustworthy. It makes the map's untrustworthiness
measurable, which is the most that can honestly be claimed for any projection —
and a trustworthiness figure well below 1.0 should be read as an instruction to
check a grouping in the evidence cards before believing it.

**Why it is not a DuckDB query console.** The obvious way to let a reader
interrogate the data is to ship duckdb-wasm and the database. Two things rule
it out: the WASM bundle lives on a CDN, and this page is deliberately CDN-free
because it is read behind corporate proxies where a silent script-load failure
would take the whole page down; and the database is gitignored precisely
because it is a large binary that does not diff, so Pages could not serve it
anyway. The Data view browses the rows the page already carries and tells a
reader how to rebuild and query the real database.

---

## What this method cannot do

- **It cannot find what the scan frame does not ask for.** The frame is
  strongest on Technological and weakest on Social, Values and Environmental —
  mirroring where free structured data exists, not where opportunities are.
- **It cannot size a market.** See Stage 4.
- **It cannot tell a real trend from a well-populated artefact.** Only reading
  the evidence cards does that.
- **It cannot replace the qualitative work.** Scenario planning, visioning,
  Delphi and backcasting are participatory by nature. This pipeline surfaces
  candidates so those sessions start from evidence instead of from a blank wall.
- **Its rankings are not yet validated.** Until the pipeline is tested against a
  known past opportunity, the weights are a hypothesis.

---

## References

- Kleinberg, J. (2003). "Bursty and Hierarchical Structure in Streams."
  *Data Mining and Knowledge Discovery* 7:373–397. DOI 10.1023/A:1024940629314
- Rotolo, D., Hicks, D., & Martin, B. (2015). "What is an emerging technology?"
  *Research Policy* 44(10):1827–1843. DOI 10.1016/j.respol.2015.06.006
  (preprint arXiv:1503.00673)
- Boutaleb et al. (2024). BERTrend. ACL FuturED workshop.
- Curry, A., & Hodgson, A. (2008). "Seeing in Multiple Horizons."
  *Journal of Futures Studies* 13(1):1–20.
- Venna, J., & Kaski, S. (2001). "Neighborhood Preservation in Nonlinear
  Projection Methods: An Experimental Study." *ICANN 2001*, LNCS 2130:485–491.
  The trustworthiness and continuity measures reported on the explorer's map.
- Keeley, Pikkel, Quinn & Walters (2013). *Ten Types of Innovation.*
- UK Government Office for Science, *Futures Toolkit* (updated August 2024).
- WIPO, *Manual on Open Source Patent Analytics* (Paul Oldham, 2nd ed.).
