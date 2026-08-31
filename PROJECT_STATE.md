# PROJECT_STATE.md

**The living record of this project.** Read this before doing anything else.
`CLAUDE.md` explains how the code works; this file says where the work is up
to, what is uncertain, and what to do next.

Update it whenever you change a weight, add to the scan frame, or learn
something that changes the method. A change with no recorded reason is
indistinguishable from a bug six months later.

---

## Orientation in 60 seconds

**Goal.** Desktop research to inform IPAVentures' next venture, done as a
repeatable pipeline rather than a one-off literature scan, so the method
outlives the sprint and can be pointed at future questions.

**Approach.** `SuggestedConceptualApproach.md` (the source research report),
implemented as Stages 0–5. `docs/method.md` explains what each number means.

**Where it stands.** The pipeline is built and has now completed a full
scheduled run end to end — collection, analysis, publication, corpus release and
R2 mirror. `2026-08-30` is the current baseline: 7,219 documents, 15 topics,
OpenAlex contributing for the first time. **The method is still not validated.**
No weight in it has been tested against a known past opportunity, and reading
the evidence cards for that run found **two of fifteen shortlisted topics to be
artefacts** (issue 11). Treat current output as proof the machinery works, not
as a finding.

**Time budget.** Roughly one week, of which day 1 is spent. See the plan below.

---

## Status

| Stage | State | Notes |
|---|---|---|
| 0 — Strategy encoding | **Working** | 34 references: 9 objectives, 6 initiatives, 7 DISR fields, 12 assets |
| 1 — Signal collection | **Working, five of six sources; rebuilt and exercised 2026-08-31** | OpenAlex 581 → **3,799 documents** across all 20 frames (issue 3). Crossref peer-review records excluded (issue 11). GDELT now genuinely spans 2024–2026 (issue 5). Failures recorded rather than swallowed — which immediately surfaced **arXiv losing 6 of 9 frames to HTTP 429** (issue 14, **now fixed, not yet re-measured against the live API**). PatentsView still disabled (issue 6) |
| 2 — Emergence detection | **Working; clustering replaced twice in two days** | Now **BGE embeddings + BERTopic over seeded UMAP + HDBSCAN** (issue 2), replacing the average linkage that had itself just replaced leader clustering. Measured on the accumulated corpus: largest topic **5.2% of assigned**, 75.0% of forming documents assigned, 112 topics, so `max_topics` no longer binds (issue 16). The seed is recorded and validated, but the topic set is **not stable across seeds** — issue 20 |
| 3 — Fit and leverage | **Working; the fix for its weakness is in, unmeasured** | Strategic fit is usable. Asset leverage was compressed to 0.03–0.10 under `hashing`; the BGE switch is meant to widen it and **nobody has yet checked whether it did** — see Open issue 2 |
| 4 — Opportunity index | **Working, partial** | `patent_activity` has no data without PatentsView; weight redistributes automatically |
| 5 — Synthesis | **Working; not yet read by a human** | Shortlist, 2×2 views, evidence cards, CSV, published HTML. **No one has read the `2026-08-31` evidence cards** — the check that caught both artefacts last time |
| Published explorer | **Rebuilt 2026-08-31; not yet run against the real corpus** | `src/dashboard.py` + `src/dashboard_assets/`. Five views over a finished run: an interactive `docs/method.md`, the point cloud, every topic and score in a sortable table, a configurable score scatter, and a browsable copy of the run's tables. The map now follows the clustering's UMAP settings and reports trustworthiness, continuity and a per-topic neighbour-purity pair. Those figures have **not yet been produced against the real corpus** — see the calibration log for what to look for in the first ones |
| Notebook export | **Working, not yet reviewed by anyone** | `src/notebook.py`; written automatically after Stage 5. Re-derives emergence, horizon, index and composite rank from stored inputs |
| Automation | **Fully exercised; scan.yml reworked 2026-08-31, not yet run under the new defaults** | `tests.yml` now has a second `ml` job covering the default BGE/BERTopic path, while the first job still installs `requirements.txt` only — which keeps "runs with no torch" a tested guarantee. `verify-access.yml` — **both credentials pass**. `scan.yml` installs the ML stack only when the resolved settings need it, caches the model, takes `embedding_backend`/`clustering_method` dispatch inputs, and its timeout is 300 min (issue 17) |
| Tests | **319 (311 passing + 8 skipped without `requirements-ml.txt`)** | Offline by design — the BERTopic tests included, since BERTopic is handed embeddings and never loads a model. They skip without `requirements-ml.txt` and run in CI's `ml` job |

**Current baseline — `2026-08-31`** (workflow run 33345343027, 164 min, from an
empty database with all collection fixes live). **Its outputs were overwritten and have
been restored** — `data/outputs/2026-08-31/` held a different, smaller run for
some hours (issue 23, now fixed). It again holds the 03:25 UTC run the figures
below describe. The run that displaced it is kept at
`data/outputs/2026-08-31T0648/`. **`docs/` still renders that other run** and
will until the next scan rebuilds the site. **15,036 documents, 120 topics**,
2018–2026. Stage 1 status `partial`, honestly: 7 failed and 9 partial
source/frame pairs, all named in `collection_log`.

| source | documents | span | frames | note |
|---|---:|---|---:|---|
| gdelt | 8,054 | 2024–2026 | 17 | the 24-month window is real for the first time |
| **openalex** | **3,799** | 2018–2026 | **20** | was 581 across the whole previous run |
| crossref | 2,357 | 2018–2026 | 13 | peer-review records now excluded |
| arxiv | 678 | 2018–2026 | 3 | **6 of 9 frames lost to HTTP 429** (issue 14) |
| datagovau | 148 | 2022–2026 | 3 | |

**The clustering fix removed the size bias on its own, with no weight changed.**
Correlation of each attribute with log(document count), 2026-08-30 → 2026-08-31:

| attribute | 2026-08-30 | 2026-08-31 |
|---|---:|---:|
| novelty | −0.80 | **+0.05** |
| coherence | −0.54 | +0.12 |
| uncertainty | −0.37 | −0.15 |
| **emergence score** | **−0.43** | **+0.05** |

Issue 12 was therefore mostly a *clustering* artefact rather than a scoring one:
under leader clustering the population was one 1,497-document mega-cluster and
fourteen small ones, so "small" and "coherent/novel" were perfectly confounded.
With 120 topics at a median of 74 documents the confound disappears. **This is
the argument for not having tuned the weights first.**

Largest topic **472 documents = 3.9% of everything assigned**, against 57%.
One topic below 20 documents, none below 15, so Stage 4 suppressed nothing.

| # | Topic | H | Emrg | Fit | Lev | Closest objective |
|---:|---|:-:|---:|---:|---:|---|
| 1 | geographical indication / protection | H2 | 0.88 | 0.31 | 0.06 | SI-1 Geographical indications |
| 2 | artificial intelligence / patent / inventorship | H1 | 0.82 | 0.15 | 0.07 | 4.2 Digital services |
| 3 | examination / artificial intelligence | H2 | 0.90 | 0.11 | **0.18** | SI-3 AI and ADM |
| 4 | adm / automated / decision-making / eu | H2 | 0.69 | 0.19 | 0.07 | SI-3 AI and ADM |
| 5 | trust / public institution / oecd | H2 | 0.70 | 0.18 | 0.06 | 1.1 Trust and confidence |
| 6 | decision making / automated decision / adm | H2 | 0.51 | **0.34** | 0.17 | SI-3 AI and ADM |
| 7 | service delivery / government / local | H2 | 0.73 | 0.14 | 0.05 | 4.2 Digital services |
| 11 | prior art / patent / art search / retrieval | H2 | 0.76 | 0.07 | 0.09 | 4.1 Stewardship |

**No artefact reached the shortlist.** The mappings are the ones a human would
make — GIs → SI-1, AI examination → SI-3, ADM → SI-3, trust → 1.1, service
delivery → 4.2, prior art → 4.1. Asset leverage now spans 0.017–0.239 against
0.030–0.101, and strategic fit 0.031–0.338.

**Still not validated.** A better-behaved instrument is not a validated one; the
ranking remains a hypothesis until issue 1 is done. **Nobody has read the
evidence cards for this run yet** — that is the next action, and on the last
baseline it was the only check that caught anything.

**Previous baseline — `2026-08-30`** (workflow run 33310810297, the first
`scan.yml` execution). Collected from an empty database, because no corpus
release existed to restore: 7,653 records fetched, 7,219 documents after
deduplication, across 2018–2026. It was originally recorded here as having
**zero failed or skipped source/frame pairs**; that was wrong — four GDELT
frames collected nothing and were logged `success` (issue 5). The counter could
not see the failure mode. Sources: Crossref 2,432,
GDELT 2,040, arXiv 2,018, **OpenAlex 581**, data.gov.au 148. 15 topics survived
filtering; 2,760 documents (38%) were assigned to one.

All four notebook verifications passed — emergence score, horizon band,
opportunity index and composite rank each re-derived from stored inputs against
the run's own config snapshot.

| # | Topic | H | Emrg | Fit | Lev | Note |
|---:|---|:-:|---:|---:|---:|---|
| 1 | artificial / intelligence / patent / examination | H2 | 0.58 | 0.19 | 0.10 | Holds up on reading. Maps to SI-3 |
| 2 | arc additive / wire arc / additive manufacturing | H1 | **0.84** | 0.07 | 0.07 | **Artefact** — one paper's peer reviews (issue 11) |
| 3 | automated decision-making / administrative law | H1 | 0.67 | 0.11 | 0.05 | Holds up. Maps to SI-3 |
| 4 | prior art / patent / search / retrieval | H2 | 0.56 | 0.05 | 0.10 | Holds up |
| 6 | delivery / service / government / local | H1 | 0.42 | 0.12 | 0.06 | **Artefact** — one paper's peer reviews (issue 11) |
| 15 | image / patent / learning / watermark | H2 | 0.32 | 0.07 | 0.08 | Catch-all: 1,497 docs = 54% of everything assigned. Correctly ranked last |

Three things worth noticing. **The strategy encoding still works** — AI-in-patent-examination
→ SI-3 is the mapping a human would make. **The scoring correctly demoted its own
catch-all** to last place on novelty 0.045 and coherence 0.337, unprompted. And
**the highest emergence score in the run belongs to an artefact**, which only
reading the evidence cards revealed — every other check in the pipeline passed it.

A stepwise walkthrough of how each of these was identified, written for a mixed
technical and non-technical audience, is in
`docs/signal-walkthrough-2026-08-30.html`.

**Previous run — `2026-08-29`** (local, no OpenAlex; its corpus was never
persisted and no longer exists). Not comparable to the baseline above — every
headline score is percentile-ranked within its own run, so a different corpus
means a different population and different numbers: 7,780 documents across 2018–2026 from
Crossref (2,431), GDELT (3,183), arXiv (2,018), data.gov.au (148). Roughly
400–640 documents per year, so growth curves have something to fit. 14 topics
survived filtering.

The top of that shortlist, with the objective each mapped to:

| # | Topic | H | Fit | Lev | Closest objective |
|---:|---|:-:|---:|---:|---|
| 1 | delivery / service / government / digital | H2 | 0.17 | 0.07 | 4.2 Digital and data-driven services |
| 2 | automated decision-making / administrative law | H1 | 0.09 | 0.06 | SI-3 AI and Automated Decision Making |
| 3 | trust / institution / public / citizen | H2 | 0.18 | 0.04 | 1.1 Trust and confidence |
| 4 | geographical indication / trade / protection | H2 | 0.31 | 0.05 | SI-1 Geographical indications (A-EU FTA) |
| 5 | genetic / resource / access / pgrfa | H1 | 0.11 | 0.03 | 2.1 Access to and benefits from IP rights |
| 6 | corporate / governance / accountability / ip | H1 | 0.09 | 0.07 | 2.2 Customer experience excellence |
| 7 | prior art / patent / search / retrieval | H2 | 0.05 | 0.10 | 4.2 Digital and data-driven services |
| 8 | indigenous / knowledge / post / treaty | H3 | 0.11 | **0.16** | 2.1 Access to and benefits from IP rights |

Three things in that table are worth noticing, and one of them is a warning.

- **The strategy encoding works.** GIs → SI-1 and ADM → SI-3 are the mappings a
  human would have made. So is Indigenous Knowledge → 2.1.
- **The asset axis is doing its job at the top end.** Indigenous Knowledge
  carries the highest asset leverage in the run (0.16, against a 0.03–0.10
  field), because the First Nations Strategy partnerships are genuinely
  distinctive to IP Australia — exactly the discrimination the axis exists to
  make. It is also ranked H3 and a weak signal: low volume, high growth. That
  combination is what a horizon scan is for.
- **The warning:** none of this is evidence the *ranking* is right. Sensible
  mappings and one well-behaved axis are evidence the machinery works. Whether
  the order is any good is Open issue 1, and it is still open.

---

## Open issues, most important first

*Numbers are stable identifiers, not ranks — they are referenced from elsewhere
in this file, so new issues keep the next free number wherever they sit in the
ordering.*

---

## Repository review — 2026-08-31

A full read of every file in the repo against its own stated rules, commissioned
because nothing here had been reviewed as a whole since the method changed twice
in two days. Findings are entered as issues 21-32 below and are interleaved into
the ordering with the existing ones. Three of them (21, 22, 25) change a number
the pipeline produces; one (23) means the repository's headline baseline is not
the run its own record describes.

The reviewing principle was this repo's own: **a bug fails loudly, a bad number
does not.** So the review weighted "a guard that is documented and absent" and
"a threshold that stopped discriminating" above anything that would raise.

Everything below was reproduced before it was written down. Where a measurement
is quoted, the script that produced it is described well enough to re-run.

---

### 21. ~~The DISR critical-technology threshold is hardcoded, and under the shipped default it matches every topic~~ — FIXED 2026-08-31

**`src/stage3_scoring.py:153`** — `match_critical_technology(..., threshold: float = 0.25)`.
The only caller (line 261) passes no threshold, so 0.25 is what every run has
ever used. The `0.7 x cosine + 0.3 x lexical` blend inside it (lines 165-167) is
hardcoded the same way. Neither appears in `bigthink_config.yaml`.

That alone violates the rule `CLAUDE.md` states twice and
`bigthink_config.yaml` opens with: *nothing that changes a result belongs in a
`.py` file.* But the number is also now wrong, and wrong in the direction that
does not fail.

**Measured across the three committed runs** (`data/outputs/*/topics.csv`):

| run | backend + method | topics carrying a DISR field |
|---|---|---:|
| `2026-08-30` | hashing + leader | 0 of 15 (0%) |
| `2026-08-31` | hashing + agglomerative | 6 of 102 (6%) |
| **`verify-bertopic`** | **bge + bertopic — the shipped default** | **114 of 114 (100%)** |

**Why.** The same high cosine floor that defeated the asset-leverage axis
(issue 2): under BGE two unrelated pieces of text still score ~0.35-0.5, so
`0.7 x 0.45 = 0.315` clears a 0.25 cut-off for essentially any topic against
some critical-technology field. The threshold was calibrated — implicitly, by
being written once — against a lexical vector space that is no longer the
default.

**What it costs.** `critical_tech` is documented as a *policy designation* — "a
topic either falls in a national-interest field or it does not" — and it is
printed on every evidence card, in `topics.csv`, in `summary.json` and in the
dashboard's details panel. Under the shipped config it is noise presented as a
designation. Every topic also collects `critical_tech_bonus` (+0.10) on
strategic fit.

**The ranking survived this run by luck, not by design.** A constant +0.10 is
order-preserving, and on `verify-bertopic` no pre-bonus fit came near the 1.0
clip in `fit = clip(fit + bonus, 0, 1)`. Raise the bonus, or run a corpus whose
fits sit higher, and the clip starts compressing the top of the axis
non-uniformly. The failure is latent, not absent.

**Why nothing caught it.** `verify-bertopic` is the only run ever produced under
the shipped default, and this file says of it, in the calibration log: *"Read
the evidence cards there before trusting the topics. Nobody has."* A 100% match
rate is visible on the first card.

**FIXED 2026-08-31, per backend and in config**, next to the thresholds that
already work this way, because the quantity being compared has a different scale
under each backend — the same argument this repo already accepted for
clustering.

- `scoring.strategic_fit.critical_tech_match.thresholds.{hashing,bge}`, resolved
  by `config.critical_tech_match_threshold` (beside `topic_similarity_threshold`,
  which exists for the same reason). The blend weights, also hardcoded, moved to
  `critical_tech_match.{embedding_weight,lexicon_weight}` and are validated
  convex like every other blend here.
- `hashing: 0.25` — unchanged, so no run collected under it moves.
- **`bge:` is deliberately blank, and blank does not mean "use a default".**
  Stage 3 then matches nothing, awards no bonus, logs one loud warning naming
  the sweep, and writes "matching DISABLED" into its `pipeline_runs` message.
  A flag that matches everything is worse than an absent one, because it is
  printed on every evidence card as a policy designation. Reporting no match is
  a smaller error than reporting a false one, and unlike a false one it is
  visible.
- The value cannot be guessed and was not: **`python -m src.calibrate
  critical-tech` is new** and sweeps the cut-off against a real run, printing the
  match rate and the number of distinct DISR fields hit at each candidate. It
  scores through `stage3_scoring.critical_technology_scores`, the same function
  the pipeline matches on, so the sweep cannot drift from what it calibrates.
- A config snapshot with no `critical_tech_match` block at all resolves to the
  historical 0.25, so an old run still reproduces as it actually ran.

**Still open, and it is the next thing to do:** nobody has swept it. Until
`thresholds.bge` is set from a real corpus, the shipped default produces no DISR
matches at all and the `critical_tech_bonus` is inert. That is the honest
position, not the finished one.

### 22. ~~`cluster_agglomerative` is neither exact average linkage nor order-invariant~~ — FIXED 2026-08-31

**`src/topics.py:215-218`.** The nearest-neighbour cache is correct in its
premise and broken in one branch:

```python
fresh = float(sims[candidate, other])
if fresh < best - 1e-9:
    nn_sim[candidate] = fresh      # <- destroys the invariant
    continue
```

The comment above it states the invariant exactly right: *"Merging can only
lower a similarity, so a cached value is an upper bound."* Revalidating before
acting on it is indeed enough — **but only if the cache stays an upper bound.**
This branch overwrites the upper bound with the exact similarity to one specific
neighbour, which can be far below the row's true maximum, and leaves `nn_idx`
pointing at that neighbour. The next iteration then merges that pair as though
it were the best available.

**Measured.** Against a naive O(n^3) exact average-linkage reference, over 30
random 40x12 corpora of four latent groups at threshold 0.5:

- **3 of 30 trials produce different clusters** from exact average linkage.
- **9 of 40 permutations of the same corpus produce different clusters** from
  each other.

**FIXED 2026-08-31.** Both stale branches now call one `_refresh(cluster)`
helper that rescans the row, so nothing can write a value below the row maximum
into the cache. **0 of 30** disagreements with the exact reference afterwards,
and order-invariance restored.

**Why this matters more than a 10% error rate suggests.** Order-invariance is
not a nice property of this method — it is the entire published argument for
choosing it. Three documents rest on it:

- `CLAUDE.md`, decision table: *"Average linkage ... is order-invariant, so the
  chronological ordering of the corpus cannot bias which topics form."*
- `src/topics.py`, module docstring: *"It is also order-invariant, so the
  seeding bias cannot exist — pinned by `test_average_linkage_is_order_invariant`."*
- This file's decision log, and the 2026-08-31 calibration entry.

Documents arrive `ORDER BY published_date`. The bias that `cluster_leader` was
replaced to remove is therefore *not* fully removed — it is reduced to the
boundary cases, where it is invisible.

**Why the test passes.** `test_average_linkage_is_order_invariant`
(`tests/test_topics_embeddings.py:185`) uses four well-separated synthetic
blobs, one seed, and `min_topic_size=8` — which discards precisely the small
boundary clusters where the divergence appears. It is a real test of an easy
case. The regression test for the fix has to use overlapping groups and a small
`min_topic_size`, and must be run against the pre-fix code to confirm it catches
this.

**Blast radius.** `agglomerative` is not the default any more, so no current
headline number depends on it. It is, however: the documented no-torch
fallback, what CI's plain `test` job exercises, what any contributor without the
ML stack runs, and what the 0.14 threshold in the calibration log was swept
under. The sweep numbers in the 2026-08-30 calibration entry were produced by
this code and inherit the same imprecision.

### 23. ~~The `2026-08-31` baseline in this file is not the run in `data/outputs/2026-08-31/`~~ — FIXED 2026-08-31

Issue 18 happened a second time, on the run this file calls the current
baseline, and was not noticed.

| | this file's "Current baseline" | committed `data/outputs/2026-08-31/` |
|---|---|---|
| documents | 15,036 | **7,501** |
| topics | 120 | **102** |
| backend / method | (BGE / BERTopic implied by context) | **hashing / agglomerative** |
| generated | — | **06:48 UTC** |

`git log -- data/outputs/2026-08-31/summary.json` shows two commits:

```
2cdeefd  2026-08-31 03:25  scan: results for 2026-08-31   15,036 documents · 120 topics
acdb96b  2026-08-31 06:48  scan: results for 2026-08-31    7,501 documents · 102 topics
```

The 03:25 run is the one this file's baseline table, its source-by-source
breakdown, its correlation table and the 2026-08-31 calibration entry all
describe. It was overwritten in place three hours later by a smaller run wearing
the same id, exactly as issue 18 documents — and the published site went with
it: `docs/index.html`, `docs/latest.json` and `docs/dashboard.html` all describe
the 7,501-document run.

**The fix for issue 18 is in and is correct** (`default_run_id()` is now
minute-resolution). Both of these commits predate it. What was never done is the
cleanup: the record still asserts a baseline whose artefacts are not in the
repo.

**Recoverable.** `git show 2cdeefd:data/outputs/2026-08-31/` has the whole
directory — shortlist, evidence cards, `topics.csv`, `summary.json`, notebook.

**Consequence while it stood.** Anyone following this file's own instruction —
*"Open `data/outputs/<run_id>/shortlist.md` and read the top five evidence cards
before looking at any score"* — read a different run's cards than the one every
number here refers to, with nothing saying so.

**FIXED 2026-08-31.** `data/outputs/2026-08-31/` is restored from `2cdeefd` and
again holds the 03:25 run this file describes. The 06:48 run is **not**
discarded: it is preserved at `data/outputs/2026-08-31T0648/` — the id it would
have been given had `default_run_id()` been minute-resolution at the time —
with a `README.md` explaining how it got there and that the two are not
comparable.

**One piece deliberately not fixed:** `docs/index.html`, `docs/latest.json` and
`docs/dashboard.html` still render the 06:48 run under the label `2026-08-31`.
Rebuilding them needs the DuckDB corpus, which is gitignored and not in a review
environment, and hand-editing generated HTML to say something its numbers did
not come from would be a worse problem than the one it solves. `scan.yml`
rebuilds the site on every run, so the next scan corrects it.

### 24. ~~Stage 1 loses a frame's collected documents when a source is retired~~ — FIXED 2026-08-31

**`src/stage1_collect.py:169`.** `docs = list(collector.collect(...))` inside a
`try` whose `except PermanentError` retires the source. `collect` is a
generator: an exception raised after the first `yield` discards every document
already produced.

This is the exact mechanism this repo documented and worked around twice — the
GDELT per-window catch and the arXiv per-year catch both exist *because*
"raising after the first yield discards the documents already produced". The
retirement path was never given the same treatment.

**Concretely:** OpenAlex pages five deep. A budget exhaustion on page 3 raises
`PermanentError` from `Collector._request` (the >MAX_BACKOFF escalation), which
propagates out of the generator and throws away pages 1 and 2. The frame is
logged `skipped` with 0 records, which is *true of the log and false of what
happened*.

Same shape for `patentsview`, which raises `missing_credential_error` before its
first yield (harmless there, since nothing has been yielded) and for any
collector that hits a 403 mid-frame.

**FIXED 2026-08-31.** `stage1_collect._drain` replaces `list()`: it pulls the
generator by hand, keeps every document produced before the failure, and returns
`(documents, (exception, kind))` where kind is permanent / retryable /
unexpected. The caller retires the source exactly as before, but now logs
`partial` with the surviving records and an incident naming the failure when
anything survived, and `skipped` / `failed` with zero only when nothing did.
Pinned by `test_a_permanent_error_mid_frame_keeps_the_documents_already_collected`,
which also asserts that the old `list()` path loses them.

### 25. ~~`attention_tone` scores "no coverage" as "maximally negative coverage"~~ — FIXED 2026-08-31

**`src/stage4_opportunity_index.py:197-198`** — `attention_tone` is `0.0` when a
topic has no GDELT documents, and is then percentile-ranked against the topics
that do. Zero is not "unknown" on this scale: the component maps real tone
`-10..+10` onto `[0, 1]`, so 0.0 is the value a topic gets for uniformly
maximally negative coverage.

Under `forming_sources` GDELT never forms a topic and only attaches at
`attachment_threshold_ratio x threshold`. Every topic that attracts no news at
all is therefore ranked below every topic with genuinely hostile coverage, on a
component carrying **10% of the opportunity index**.

**The repo already solved this correctly, one file over.**
`stage2_emergence.citation_percentiles` returns a neutral 0.5 for a source with
no citation variation, with the comment: *"There is no impact signal here, so
say so with a neutral 0.5 rather than a confident wrong answer."* That is the
same problem and the right answer; Stage 4 gave the other one.

**FIXED 2026-08-31.** A topic with no GDELT documents now takes
`_NO_TONE_SIGNAL = 0.5`, the midpoint of the rescaled range. **This changes the
opportunity index**, and therefore the value reported beside every topic —
though not the composite rank, which excludes the index by design. See the
calibration log entry below.

### 26. ~~A documented guard that does not exist: `min_term_document_frequency`~~ — RESOLVED 2026-08-31 (removed, not implemented)

`bigthink_config.yaml`, under `emergence.topics`:

```yaml
    # Terms appearing in fewer than this many documents are dropped before
    # topic formation, so single-paper coinages cannot become "topics".
    min_term_document_frequency: 5
```

**Nothing reads this key.** `grep -rn min_term_document_frequency src/` returns
nothing. There is no such filter anywhere in `topics.py` or `embeddings.py`;
`label_topics` drops terms occurring fewer than twice *within a topic*, which is
a different guard against a different failure.

A guard that is documented and absent is worse than no guard, because it is
reasoned about as though present — and this one is described as protection
against exactly the artefact class (a handful of near-identical documents
becoming a topic) that produced the peer-review artefacts of issue 11.

Related, smaller, same family: `storage.strategy_dir` is read by nothing, and
`strategy.documents[].weight` (1.0 and 0.6) is read by nothing —
`load_strategy_corpus` concatenates the files and ignores it, so the annual
report is weighted equally with the corporate plan in policy salience despite
the config saying 0.6.

**RESOLVED 2026-08-31 by removing all three, not by implementing them.** Each
would change a number: a term-document-frequency filter changes every topic in
the run, and a per-document corpus weight changes every `policy_salience` score.
Both belong behind a sweep and a calibration-log entry, not behind a quiet
"finish the feature" commit — this project's own ordering rule is inputs before
weights, and neither has been measured. What replaces them is a comment at each
site saying the guard does not exist and what does: `label_topics` drops a term
occurring fewer than twice within a topic, and `min_distinct_terms` drops a
topic that cannot be described in three distinct terms.

**No result changes.** Nothing read any of the three.

### 27. ~~Stage 1 alone never records its own failure and never closes its connection~~ — FIXED 2026-08-31

Every other stage wraps its body in `try / except / finally` and calls
`log_stage_finish(conn, entry_id, "failed", ...)` before re-raising, then closes
the connection in `finally`. **`stage1_collect.run` has neither** — its
`conn.close()` is the second-to-last statement of the happy path
(`src/stage1_collect.py:249`).

Anything raising between `log_stage_start` and the end — `load_scan_frame` on a
malformed scan frame, a `ConfigError` from an empty `--sources` filter, a
DuckDB error — leaves a `pipeline_runs` row stuck at `running` forever and leaks
the connection holding DuckDB's exclusive file lock.

The stuck row matters beyond tidiness: `pipeline_runs` is the observability
surface this project relies on, and the notebook resolves a run's config from
`ORDER BY id DESC LIMIT 1` over exactly this table.

**FIXED 2026-08-31.** Split into `run` (log, try / except / finally, close) and
`_run_inner`, matching every other stage exactly.

### 28. ~~Every published HTML page is missing its document head~~ — FIXED 2026-08-31

`docs/index.html`, `docs/dashboard.html` and `docs/signal-walkthrough-2026-08-30.html`
each begin with `<title>` and go straight into `<style>`. There is no
`<!doctype html>`, no `<meta charset="utf-8">` and no
`<meta name="viewport" content="width=device-width, initial-scale=1">`.

Three consequences, in increasing order of how much they matter:

1. **No doctype** puts every browser into quirks mode, where box-sizing and
   table layout follow pre-standards rules.
2. **No charset** means an en-dash or a curly quote — both are all over these
   pages — renders as mojibake whenever the file is opened directly rather than
   served by Pages with its own header. That includes the workflow artefact.
3. **No viewport meta** means a phone renders the page at a ~980px virtual
   width and scales it down. Both stylesheets carry a deliberate
   `@media (max-width: 720px)` breakpoint that **can never fire**, so the
   responsive work already done in `report.py` and `dashboard.py` is inert.

Generated by `report.py` and `dashboard.py`; the walkthrough is hand-written
and has the same gap.

**FIXED 2026-08-31** for both generators, via `config.PAGE_HEAD` / `PAGE_TAIL`
so the two cannot drift, with each line's reason recorded there. Pinned by
`test_every_published_page_declares_its_document_head` and
`test_every_published_page_is_a_closed_document`, which render both pages from
one fixture and assert doctype, charset, viewport and a closed document.

`docs/*.html` on disk still lack it until the next scan regenerates them; the
hand-written `docs/signal-walkthrough-2026-08-30.html` is not generated by
anything and is fixed in place.

### 29. ~~`report.py`'s docstring describes output the module does not produce~~ — FIXED 2026-08-31

*"Renders docs/index.html: the ranked shortlist, both 2x2 views, and links to
the evidence cards."* The page rendered **one** 2x2 (fit x leverage; the
fit x emergence view exists only in `shortlist.md`) and contained **no link to
any evidence card** — only a footer naming the directory.

**FIXED 2026-08-31 in both directions**, because of the two the links were the
half worth having: reading the evidence cards is the single check this project
says finds artefacts, and the page should not make a reader go and find them.
Every shortlisted row now links to its card on GitHub — at GitHub, not a
relative path, because only `docs/` is served by Pages and the cards live under
`data/outputs/`; the dashboard already linked them this way. The docstring now
describes what is actually rendered.

### 30. ~~`CLAUDE.md` states an architectural rule the code does not follow~~ — FIXED 2026-08-31

> **Stages communicate only through DuckDB.** No stage passes Python objects to
> another. [...] Do not add cross-stage function calls that bypass the database.

`stage5_synthesis._run_inner` calls `run_stage3(config, run_id)` and
`run_stage4(config, run_id)` directly and consumes their return values as Python
dicts. It has to: Stage 3 deliberately persists nothing (`CLAUDE.md`'s own
command list says *"prints only; does not persist"*), so there is nothing in the
database for Stage 5 to read.

The reason for the coupling is good and is stated at the call site — writing
`topic_scores` in one place so a crash cannot leave it half-populated. The rule
as written was simply false, and the next contributor would either believe it or
quietly conclude the documentation is unreliable. Both are bad outcomes.

**FIXED 2026-08-31 in `CLAUDE.md`**, by stating the exception and its reason
rather than softening the rule: the rule still governs any stage you add, and
the one place that breaks it says why at the call site.

### 31. ~~The vector cache and the model cache are each keyed on the wrong thing~~ — FIXED 2026-08-31

Two separate instances of the same mistake, in opposite directions.

**Too coarse — `embeddings.content_hash(text, backend)`** keys the DuckDB
`vectors` cache on the *backend name*, not the model. `embeddings.bge_model` is
a configurable key. Changing it to any other sentence-transformers model serves
back vectors from the previous one for every document already cached, silently
mixing two vector spaces — precisely what the module docstring promises does not
happen: *"`backend` is therefore part of the vector cache key, and switching it
invalidates the cache rather than silently mixing vector spaces."* True of the
backend, false of the model.

**Too fine — `scan.yml`'s model cache**, `key: hf-${{ hashFiles('bigthink_config.yaml') }}`,
under a comment reading *"Cached on the model name so a bump to
embeddings.bge_model invalidates it."* It is keyed on the hash of the entire
config file, so editing `max_topics`, a threshold or even a comment throws away
the 400 MB model cache and re-downloads it — inside the job whose run time is
issue 17.

**BOTH FIXED 2026-08-31.**

- `Embedder.cache_key` is new and names the vector space, not the backend:
  `hashing`, but `bge:BAAI/bge-base-en-v1.5`. `encode_with_cache` keys on it,
  so changing `embeddings.bge_model` now misses cleanly instead of serving the
  old model's vectors. **Cost: one re-embed of the stored corpus on the next
  run** — ~10-15 minutes, already inside the timeout budget of issue 17 — since
  every cached `bge` vector is under the old key.
- `scan.yml` resolves the model name in its own step and keys the Actions cache
  on `hf-<model name>`, which is what its comment always claimed.

### 32. ~~Smaller things found in the same pass~~ — FIXED 2026-08-31

None of these change a number. Recorded so they are not re-discovered.

- **`src/calibrate.py:75` bypasses the vector cache.** `_load_forming_corpus`
  calls `embedder.encode(texts)` where every stage calls `encode_with_cache`.
  This is the documented cause of *"An offline sweep said 118 and was wrong"*
  (issues 16 and 20): the sweep re-embeds at its own batch size, the pipeline
  reads cached vectors, the two differ by ~3e-7, and UMAP turns that into six
  clusters. Reading the same cache would remove the discrepancy rather than
  explain it. The connection is already opened and closed two lines above.
- **`src/calibrate.py:182`** assigns `run_id` from `pipeline.run_label` and
  overwrites it four lines later. Dead, and the only consumer of `run_label`.
- **`src/stage4_opportunity_index.py:206`**:
  `have_data["research_growth"] = have_data.get("research_growth", False) or True`
  is `= True` with extra steps.
- **`src/stage5_synthesis.py:349`**: `horizons = [("H1", "H2", "H3")[i] for i in range(3)]`.
- **Stage 4 issues one `fetch_topic_documents` query per topic** (120 queries,
  each joining `documents`) when `db.fetch_run_topic_documents` was added for
  exactly this shape of read.
- **`src/collectors/base.py:78`**: `datetime.strptime(text[: len(fmt) + 6], fmt)`.
  The `+ 6` is an unexplained magic slice that happens to work for the formats
  listed; it will not obviously survive a new one being added.
- **arXiv's budget arithmetic is inconsistent with its own config comment.**
  `max_results_per_query: 270` is described as a total spread across the window,
  but `per_year = max(configured_or_split, _MIN_PER_YEAR=25)` can push the real
  total above it (9 years x 25 = 225, and 30/year x 9 = 270 as configured — the
  floor binds whenever the window widens).
- **`topic_documents` and `topic_timeseries` still have no `run_id`**, relying
  entirely on `topic_id` now being run-qualified for their cleanup in
  `replace_topics`. It works, and it is the same latent shape that caused issue
  19; a schema change would be honest but is not urgent.
- **`src/stage3_scoring.py:218`** claims the embedder "must be fitted on the same
  corpus Stage 2 used" and then fits on a different one (documents **+ refs**,
  where Stage 2 fits on documents alone). Harmless — Stage 3 re-encodes
  everything it compares — but the comment asserts a property the code does not
  have.
- **Test-count drift.** This file says **276 passing** in the status table and
  *"expect 223 passing"* in Next Actions. The real number today is **277**
  (269 passing + 8 skipped without `requirements-ml.txt`).
- **`docs/method.md` does not mention the critical-technology match threshold at
  all**, only the bonus it gates. See issue 21.

**ALL FIXED 2026-08-31**, none of them changing a score. Two are worth calling
out because they were not cosmetic:

- `calibrate._load_forming_corpus` and `report_attachment` now read through
  `encode_with_cache`, so a sweep and the pipeline see **the same vectors**.
  That removes the discrepancy issues 16 and 20 both work around rather than
  continuing to explain it — the sweep no longer re-embeds at its own batch
  size and lands ~3e-7 away from what the pipeline clustered.
- Stage 4 issued one `fetch_topic_documents` query per topic; it now reads the
  run's membership in a single `fetch_run_topic_documents` call and joins in
  Python. Identical output, 120 fewer joins over the whole corpus.

### What the review did NOT find

Worth recording, because a review that only lists faults is not informative
about where the risk is.

- **No arithmetic error in any scoring path.** `percentile_rank`,
  `composite_scores`, `fit_logistic_maturity`, `compute_cagr`,
  `citation_percentiles`, `redistribute_weights` and the Kleinberg
  implementation all do what their docstrings say. The notebook's four
  verifications check the right things against the right source (the run's own
  config snapshot, via the production functions).
- **No missing input validation that would let a bad config reach a run.**
  `config.py` is genuinely strict, and the convex-combination checks are real.
- **No swallowed collector failure remaining.** The issue 5 fix holds across
  every collector.
- **No secret, credential or contact address in a tracked file** beyond the
  contact email, which is deliberate and required for the polite pools.
- **The `--skip-collect` loop works** — issue 19's run-qualified topic ids are
  correct and the regression test does reproduce the original crash.

### 19. ~~Stage 2 crashes the second time it runs against an accumulated database~~ — FIXED 2026-08-31

**Every real run to date started from an empty database**, each deliberately
(`fresh_baseline`, or the first-ever scheduled run) — so `--skip-collect`, the
fast loop for tuning this file and `CLAUDE.md` both document and recommend,
had never actually been exercised against a database Stage 2 had already
written to. The first time it was — triggering `scan.yml` with
`skip_collect: true` against the restored `corpus-20260830-230512` release,
to rebuild the site after this session's dashboard PR merged — it crashed
immediately:

```
_duckdb.ConstraintException: Constraint Error: Duplicate key "topic_id: T0000"
violates primary key constraint.
```

**Cause.** `topics.topic_id` is a bare `VARCHAR PRIMARY KEY` — global across
the whole table, not scoped by `run_id` the way `topic_scores` already is.
Every clustering method numbers its output fresh from `T0000` on every run,
regardless of `run_id`. `db.replace_topics` deletes existing rows for its
*own* `run_id` before inserting, but a **different** run_id's `T0000` (from
whichever run last wrote topics) is still sitting in the table, and the
insert collides with it. This is not a corner case — it is guaranteed on the
very next Stage 2 run against any non-empty `topics` table, because id
generation is deterministic and always restarts at zero.

**Consequence, if it had gone unnoticed longer:** every workflow retrying
`--skip-collect` after any Stage 1 fix, and every local tuning session
following the documented fast loop, would fail identically — silently ruling
out the one workflow this project's own docs point people toward for
iterating on Stage 2 configuration.

**Fixed** in `src/stage2_emergence.py`: once a run's topic ids are final
(after `drop_vocabulary_poor_topics`, the last renumbering step), each is
qualified with the run id — `topic_id = f"{run_id}-{topic_id}"` — so it is
unique everywhere it is stored or displayed without touching the schema or
any query. Evidence-card filenames and CSV/dashboard topic ids get slightly
longer (`2026-08-31-T0000` instead of `T0000`) but nothing parses the old
shape (checked: no code pattern-matches `T\d+`). Regression test:
`test_running_stage_two_twice_against_one_database_does_not_collide`, which
runs Stage 2 twice against one persisted database with two different run
ids and asserts the second does not raise and the two runs' topic ids are
disjoint — reproduced against the pre-fix code first, to confirm it actually
catches this.

**Note this does not fix issue 18** (two runs sharing one run_id on the same
UTC day) — under this fix they would generate the *same* qualified id and
the second correctly overwrites the first via `replace_topics`, exactly the
already-documented behaviour of issue 18.

### 11. ~~Crossref peer-review records are producing phantom topics~~ — FIXED 2026-08-31

`src/collectors/crossref.py` now drops `type` in
`collection.sources.crossref.exclude_types` (`peer-review`, `component`) and
titles in `exclude_titles` (*References*, *Bibliography*, *Index*), with tests
covering both the exclusion and the reviewed paper still being kept. The
original diagnosis, kept because the mechanism is worth remembering:

**Two of the fifteen topics on the `2026-08-30` baseline are not topics.** Both
are the peer-review history of a single paper.

- **Rank 2, `arc additive / wire arc / additive manufacturing`** — carrying the
  **highest emergence score in the run (0.843)**. All eight documents nearest
  its centre are review reports for one paper, `10.1002/eng2.70518`, registered
  as `/v1/review1`, `/v1/review2`, `/v1/review3`, `/v2/review1`, `/v2/review2`,
  `/v3/review1`, `/v4/review2`, `/v4/review3`. Nineteen of its twenty-seven
  documents fall in 2025.
- **Rank 6, `delivery / service / government / local`** — carrying the
  **highest opportunity index in the run (0.689)**. Its eight nearest documents
  are review reports of *Financial Autonomy: Panacea for Improved Service
  Delivery in Imo State Local Government System*. The label reads like
  Australian public-service delivery; the centre is Nigerian local-government
  finance.

**Why every guard missed it.** Crossref registers peer-review reports as
first-class records with their own DOIs, so `native_id` deduplication is working
correctly — these genuinely are distinct identifiers. The reviews share the
reviewed paper's title, so they cluster *very* tightly (coherence 0.779, the
highest in the run), look maximally novel (0.878 — nothing like them existed
earlier), and all arrive in one year, so Kleinberg flags a real burst. Every
measurement was correct. The input was wrong.

A related case sits at rank 8, where three of the nearest documents are titled
simply *References* — book bibliographies registered with their own DOIs.

**Measured extent.** Of the 120 documents nearest the fifteen topic centres, 16
are `peer-review` — and all 16 sit inside those two topics rather than being
spread thin. Also present: 4 `posted-content` (preprints, legitimate) and 1
`component`.

**Fix.** `src/collectors/crossref.py` already requests the `type` field but uses
it only as a fallback venue label when a record has no container title; it never
filters on it. Excluding `peer-review`, and probably `component` and back-matter
titled *References*, removes both artefacts at source.

**Applied 2026-08-31**, together with the other collection changes, since the
baseline is being recut anyway. Note this removes the *documents*; it does not
remove the scoring pressure that promoted them. A tight cluster of near-identical
text scores maximally on novelty and coherence whatever produced it — see issue
12.

### 12. ~~The emergence score is structurally biased toward small topics~~ — LARGELY RESOLVED 2026-08-31

Measured across the fifteen topics of the 2026-08-30 run:

| attribute | rotolo weight | correlation with log(document count) |
|---|---:|---:|
| novelty | 0.25 | **-0.80** |
| coherence | 0.15 | -0.54 |
| uncertainty | 0.10 | -0.37 |
| growth | 0.30 | +0.37 |
| impact | 0.20 | -0.30 |
| **emergence score** | **1.00** | **-0.43** |

Half the Rotolo weight is anti-correlated with topic size, through three
mechanisms that are each defensible alone:

- **Novelty** is cosine distance from the early-corpus centroid, and a large
  topic's centroid sits near the corpus centroid by construction.
- **Coherence** is mean member-to-centroid cosine. Small clusters are tighter —
  that is what makes them small.
- **Uncertainty** normalises entropy by `log(observed categories)`, so a topic
  whose ten documents each name a different institution scores a perfect 1.0
  for actor dispersion, when the honest answer is "not measurable".

This is the mechanism behind the artefacts, not just bad luck: the
`arc additive / wire arc` topic took the highest emergence score in the run
(0.843) on novelty 0.878 and coherence 0.779, which are *correct measurements
of the wrong thing*.

**LARGELY RESOLVED 2026-08-31 by the clustering fix, with no weight changed.**
On the `2026-08-31` baseline the correlations are novelty **+0.05** (was −0.80),
coherence +0.12 (−0.54), uncertainty −0.15 (−0.37), emergence **+0.05** (−0.43).

The bias was mostly a property of the *population*, not of the attributes. Under
leader clustering the run held one 1,497-document mega-cluster and fourteen
small topics, so "small" and "coherent/novel" were the same variable. With 120
topics at a median of 74 documents they separate.

**Keep the issue open, at low priority.** Two of the three mechanisms are still
real in principle — `_normalised_entropy` still divides by observed rather than
possible categories, and novelty is still measured against an early-corpus
centroid — so a future corpus with a lopsided size distribution would surface
them again. The remedy is a better-behaved topic population, which is what the
bake-off harness is for. Do not tune weights for this: the measurement above is
what "fix the inputs before the weights" looks like when it works.

### 13. `min_docs_per_topic` does not gate anything — open, but moot on the current corpus

`CLAUDE.md` states the rule as "thin topics suppressed, not scored". Stage 4
genuinely suppresses below `opportunity_index.min_documents: 15`. Stage 2's
`emergence.min_docs_per_topic: 20` only emits a log warning — the topic is
scored, ranked and published anyway.

On the 2026-08-30 run that put `genetic / resource / access / pgrfa` at **rank
5**: 10 documents, nothing since 2024, CAGR -100%/yr, classified `noise`,
opportunity index suppressed as unmeasurable — ranked above geographical
indications, IP enforcement and trust in institutions. It got there on novelty
0.709 and impact 0.802, both computed from ten documents.

**Fix is small** (gate in `stage2_emergence._run_inner`), but it changes the
ranking, so it belongs behind issue 1 with the rest of issue 12.

**Moot in practice on the `2026-08-31` baseline**, which is worth knowing before
spending effort on it: of 120 topics, exactly one falls below 20 documents and
none below 15, so Stage 4 suppressed nothing and no thin topic reached the
shortlist. The gate is still absent and would still matter on a smaller corpus
or a higher threshold — but the 10-document topic at rank 5 was a symptom of the
clustering, not of the missing gate.

### 14. ~~arXiv lost 6 of 9 frames to rate limiting~~ — FIXED 2026-08-31

The `2026-08-31` baseline recorded **28 HTTP 429s** from `export.arxiv.org`.
Three frames succeeded (679 documents); six returned nothing. The previous run
collected 2,018 documents across 9 frames, so this is a real loss of the fastest
emergence signal in the corpus — and the first run in which it is visible rather
than silent.

**Two things to fix, in this order.**

1. **A 429 kills the whole frame, not just one year.** `_collect_year` calls
   `fetch_text` outside any try block, so once retries are exhausted the error
   propagates out of `collect()` and every remaining year of that frame is lost.
   The same lesson as the GDELT windows: catch per year, record an incident,
   keep the years already collected. A frame that lost 2024 is worth far more
   than a frame that lost everything.
2. **`request_delay_seconds: 3.0` may no longer be enough** from a shared
   runner. arXiv asks for 3 s; it is evidently enforcing something stricter
   under load. Raising the delay costs run time, which is now the binding
   constraint (see below) — so fix (1) first and re-measure before touching it.

**Fixed 2026-08-31.** Both halves, because (1) alone is not enough: contained
per year, nine frames by nine years is 81 requests that each still spend the
full retry budget against a server refusing all of them, which converts a lost
frame into a lost hour.

1. `_collect_year` now catches `BigThinkError` around `fetch_text`, records an
   incident and returns, exactly as GDELT's per-window catch does. A frame that
   loses 2024 keeps 2018-2023, and Stage 1 logs it `partial` rather than
   `success`. It cannot simply raise — `collect` is a generator drained with
   `list()`, so raising after the first yield discards the documents already
   produced.
2. `request_delay_seconds` stays at arXiv's published 3 s, but a 429 now
   multiplies the delay by `rate_limit_backoff_factor` (1.5) for the rest of
   the run, up to `max_request_delay_seconds` (20 s). One-way, and held on the
   collector instance, which Stage 1 reuses across frames — the throttle is per
   IP, not per query, and on 2026-08-31 frames 4 through 9 were lost to a limit
   frames 1 through 3 had already found.

The delay is measured rather than configured because the published limit is not
what a shared IP actually gets, and what it gets varies with arXiv's load: a
single hand-set number is either too slow every week or too fast on the week it
matters. It costs nothing when arXiv is healthy.

**Worst case is ~25 extra minutes** (81 requests at the 20 s ceiling against 4
minutes at 3 s), which is part of why the job timeout went to 300 — see issue
17. Lower `max_request_delay_seconds` first if a run starts running long.

Pinned by nine tests in `tests/test_collectors.py`, including that a healthy
run never widens the delay and that a 503 is contained without widening it —
a 500 is not evidence about our request rate.

**Not yet re-measured against the live API.** The next scheduled or manual run
is the measurement; check `collection_log` for arXiv frames logged `partial`
and the run log for "widening the request delay".

### 15. data.gov.au descriptions keep their HTML — NEW 2026-08-31

`src/collectors/datagovau.py:89` takes CKAN `notes` verbatim, where
`crossref.py` strips markup with `_strip_markup`. CKAN descriptions carry HTML,
so list tags become tokens: the `2026-08-31` baseline produced a topic labelled
**`li / ul / nsw / opengov`** at rank 34 on 53 documents.

It did not reach the shortlist, and data.gov.au is only 148 documents, so this
is small — but it is exactly the class of defect that produced the peer-review
artefacts, and the fix is one function call.

### 20. BERTopic's topic set is not stable across seeds — NEW 2026-08-31

The seed sweep behind the 2026-08-31 calibration entry found a bimodal result,
not a tight one. At `n_neighbors` 15 on the same 5,184-document corpus:

| seed | topics | assigned | largest cluster |
|---|---|---|---|
| 0 | 115 | 73.5% | 5.3% |
| 1 | 112 | 73.4% | 5.3% |
| 7 | 105 | 77.3% | **11.8%** |
| 42 | 112 | 75.0% | 5.2% |
| 1234 | 107 | 76.4% | **12.1%** |

Three of five seeds produce a largest cluster around 5%; two produce one around
12%, roughly a 480-document quantum cluster that the other three split up.
Topic count varies 105–115 (±9%) and assigned share 73.4–77.3%.

**Why this matters more than it looks.** The seed is recorded, so any single
run is reproducible — that part is fine and is tested. The problem is
interpretive: "quantum is one 480-document topic" and "quantum is several
smaller topics" are different findings about the corpus, and which one a reader
gets is currently decided by an arbitrary integer. A reader has no way to tell
from the output that the alternative existed.

**Not a reason to revert.** Every seed here still avoids the catch-all that
made the previous method unusable — the worst seed's 12% against the old
method's 54% — so this is a smaller problem than the one it replaced.

**What would settle it.** Cluster at several seeds and keep only what is stable
across them, or report per-topic a "how often did this topic appear across
seeds" figure alongside the score. The second is cheaper and more honest: it
puts the uncertainty in front of the reader instead of resolving it silently.
Neither is built.

**Until then**, treat a single topic's boundaries as softer than its existence,
and re-read the evidence cards before believing any one large topic is really
one thing.

### The same instability, at float precision — found 2026-08-31

Worse than the seed spread, and found by accident while checking the sweep
against the real pipeline.

The calibration sweep and the verification run clustered **the same 5,184
documents** with the same model, the same seed and the same parameters. The
sweep encoded in batches of 500; the pipeline encoded through
`encode_with_cache` at its own batch size. The resulting vectors agree to
**cosine 0.9999998** — maximum absolute difference 3.3e-7, mean 9.2e-9, which
is float32 batching noise and nothing else.

They produced **118 and 124 clusters** respectively.

**What this does and does not mean.**

- It does **not** break reproducibility in practice. Vectors are cached in
  DuckDB's `vectors` table and re-read, so a re-analysis of a stored corpus
  gets bit-identical input and therefore identical topics. That is why
  `--skip-collect` remains a sound tuning loop.
- It does mean the topic set is **not robust**, only deterministic. UMAP's
  nearest-neighbour graph resolves near-ties differently under a perturbation
  far below any meaningful precision, and HDBSCAN turns a changed graph into
  changed cluster boundaries. Determinism and robustness are not the same
  property, and only the first is tested.
- Practically: **do not compare a topic count, a topic's size, or a topic id
  across two runs** even of the same corpus, unless both read the same cached
  vectors. Re-embedding on a different machine, a different batch size or a
  different sentence-transformers build is enough to move them.

This makes the "report how often a topic survives across seeds" idea above the
right fix rather than a nice-to-have, and it should perturb the vectors too,
not only the seed. A topic that survives both is a finding; one that does not
is an artefact of the arithmetic.

### 16. `max_topics` is now binding — STILL BINDING under BERTopic 2026-08-31

The `2026-08-31` run produced exactly **120** topics, which is
`emergence.topics.max_topics`. More clusters passed `min_topic_size` and the
largest 120 were kept. That is the documented behaviour and it is no longer a
silent drop, but the cap is now deciding how much of the corpus is described
rather than acting as a safety limit. Raise it, or raise the threshold, at the
next sweep — and treat 120 as a number that was hit rather than chosen.

**Not resolved by the clustering switch — still binding, by a little.** The
verification run on 2026-08-31 logged:

```
Keeping the 120 largest of 124 topics (emergence.topics.max_topics)
```

So the cap still decides the last four. Better than the previous run, which hit
it exactly with more clusters behind it, but not fixed.

**An offline sweep said 118 and was wrong**, which is worth recording because
of *why* — see issue 20. The sweep and the pipeline embedded the same 5,184
documents with the same model and got vectors agreeing to a cosine of
0.9999998; that was enough to move the cluster count from 118 to 124. Trust the
pipeline's own log line over any reproduction of it, including this file's.

The direction of `n_neighbors` is unchanged and still matters: 163 raw clusters
at 5 and 139 at 10, both well over the cap. Lowering it makes this much worse.

**Suggested fix:** raise `max_topics` to ~200. It was set as a safety limit
against a runaway clustering pass, and at 124 it is doing something else. The
argument for leaving it is that nothing downstream reads more than the
shortlist — but Stage 4 percentile-ranks *within* the topic set, so the cap
silently changes every index it computes.

### 18. ~~Two runs on one UTC day share a run_id and the second overwrites the first~~ — FIXED 2026-08-31

Found while merging `main` into the fix branch, and it had already destroyed
something.

`scan.yml` resolves an unset run ID with `date -u +%Y-%m-%d`. The first
`2026-08-30` baseline ran at 12:11 UTC; the weekly scheduled run fired at 21:47
UTC **the same UTC day**, resolved to the same `2026-08-30`, and rewrote
`data/outputs/2026-08-30/` in place — shortlist, evidence cards, notebook,
`topics.csv` and `summary.json`. On `main` that directory now describes a
7,501-document run generated at 23:05, not the 7,219-document run at 13:18 that
this file, the calibration log and every analysis of the baseline refer to.

Nothing warned. The commit reads `scan: results for 2026-08-30`, exactly like
the commit it replaced, and `git` saw a normal modification.

**Resolved for this branch** by taking our copy of `data/outputs/2026-08-30/` in
the merge, so the documented baseline survives. The scheduled run's output is
still recoverable from its workflow artefact and corpus release if anyone wants
it; it was collected with the pre-fix code, so nothing here depends on it.

**Fixed 2026-08-31** by the second option — the owner's call. `default_run_id()`
now resolves to `%Y-%m-%dT%H%M` (`2026-08-31T0947`), and `scan.yml` calls that
function instead of its own `date`, so the workflow and the CLI cannot drift
into two formats. `T`, not a space or a colon, so the id stays usable unquoted
as a directory name (Windows included), a `topic_id` prefix, a shell argument
and a workflow artefact name.

The refuse-to-overwrite option was the one this file argued for, and it was
rejected for a concrete reason: `--skip-collect` re-analysis is *meant* to
rewrite a run's outputs in place — it is the documented fast loop for tuning —
so a guard would fire on the normal case and have to be suppressed every time.
An explicit `--run-id` is therefore still taken verbatim and still overwrites.
What is fixed is the *silent* collision, which only ever arose from the default.

This is the same failure shape as issue 5 — a real event that the record showed
as normal — and the same remedy: make the machine say what happened.

### 17. Run time is now the binding constraint — NEW 2026-08-31

164 minutes against a 240-minute timeout, up from 67. GDELT dominates: 4 windows
x 18 frames at a measured 32-36 s per request. The headroom is real but no
longer generous, and both the arXiv fix (issue 14) and any increase to
`window_chunks` spend it. Measure before adding requests.

**Updated 2026-08-31 — timeout raised to 300 minutes.** Three additions landed
together, each small and together not, against that 164-minute baseline:

| Addition | Cost |
|---|---|
| Installing torch and the ML stack | +3-5 min, cold pip cache only |
| BGE embedding the corpus | +10-15 min, cold vector cache only |
| UMAP + HDBSCAN over the forming corpus | +1-3 min |
| arXiv's adaptive backoff at its 20 s ceiling | up to +25 min, worst case |

~212 minutes in the worst case, which 240 does not comfortably hold. Both
caches — pip, and the DuckDB `vectors` table — make the two embedding-related
rows near-free after the first run, so the steady state is closer to 180.

The measured cost of BGE embedding here was **~7 documents/second on one
contended core**, or roughly 18 minutes for 7,501 documents. An Actions runner
should do better, but budget on that number rather than a hoped-for one.

**Cheapest lever if a run starts running long**, in order: lower
`collection.sources.arxiv.max_request_delay_seconds`; make sure the corpus
release is being restored, so the vector cache is warm; then reconsider GDELT's
`window_chunks`, which is still the single biggest consumer.

### 1. The ranking has never been validated — do this before trusting anything

Every weight in the pipeline was set by reading Rotolo et al. and thinking, not
by fitting to a known outcome. Until the validation test in
`docs/runbook-calibration.md` (Step 1) is run, the ranking is a hypothesis
about ranking.

**The test:** pick an opportunity IP Australia already pursued — IP First
Response, the AI patent analytics for the Critical Technologies Hub, TM
Checker, GI work under the A-EU FTA — set `collection.end_year` to the year
before that work began, re-run, and see where it lands. Repeat for two or three.

The crucial distinction the runbook draws: **a miss caused by the scan frame
cannot be fixed by re-weighting.** Trying is the standard way to overfit a
method like this into uselessness.

### 2. The asset-leverage axis is compressed and barely discriminates — STILL OPEN; the expected fix was tried 2026-08-31 and did not work

Across all 15 topics it spans 0.03–0.10. Rank-normalisation means it still
contributes its configured 25% of the *ordering*, but the underlying signal is
thin.

**Cause:** the `hashing` embedding backend matches vocabulary, not meaning. A
topic's 30 terms and a 60-word asset description share few exact tokens, so
cosine is near zero and only the lexicon match carries signal.

**Fix:** switch to the `bge` backend (`docs/runbook-calibration.md`, Step 3),
then re-sweep the clustering threshold — the whole similarity scale changes.
This is the highest-value single change available.

**Done 2026-08-31.** `embeddings.backend` is now `bge`, and
`emergence.topics.method` is now `bertopic` over a seeded UMAP + HDBSCAN pair.
The clustering switch was asked for alongside the backend and reverses this
repo's previous argument for average linkage — see the calibration log entry
below and the decision log for why that trade was made the other way round.

**Measured on the verification run, and the axis did not widen.** Top 15 by
rank: 0.044–0.233 (span 0.189, span/mean 1.86) under `hashing`, against
0.369–0.501 (span 0.132, span/mean **0.30**) under BGE. The values moved up and
the relative spread got six times *narrower*. BGE cosines have a high floor —
unrelated text still scores ~0.35 — so everything resembles everything.

The diagnosis was right about the cause and wrong about the cure. Stage 5
percentile-ranks the axis before weighting, so it still contributes its 25% of
the ordering; what a narrow spread costs is signal-to-noise, since the gaps
deciding the order shrink relative to the level.

Keep BGE regardless — it is what made the clustering work (largest topic 54% →
5.2%). It simply is not what fixes this.

**The second-order fix is now the main one:** expand the lexicons in
`data/strategy/asset_inventory.yaml`. They currently carry 7–11 entries each;
more entries, and more of the phrasing the literature actually uses, would
help under either backend.

### 3. ~~OpenAlex is throttled to 8% of the corpus~~ — FIXED 2026-08-31 (the anchor, not the coefficient)

Verified 2026-08-29: OpenAlex is no longer simply "free, no key". Requests are
metered in dollars against a small daily allowance per IP, reset at midnight
UTC, and a shared or cloud IP has usually spent it:

```
{"error": "Rate limit exceeded",
 "message": "Insufficient budget. This request costs $0.001 but you only have
             $0 remaining. Resets at midnight UTC.", "retryAfter": 62606}
```

`mailto` does not fix this. It was retired at the first frame of the real run
and contributed zero of the 7,378 documents.

**Fixed.** `OPENALEX_API_KEY` is set as a repository secret and verified
against the live API on 2026-08-30 by `verify-access.yml`: an authenticated
query returned 29,771,915 works for 2026. The key is 22 characters.

**Confirmed live on the `2026-08-30` run, but throttled by the relevance floor
— REOPENED as a calibration question.** All 20 queries logged `success` and the
source was never retired, which is the change that matters. But it returned only
**592 records, 581 documents, 8% of the corpus**, against a configured ceiling of
5 pages × 200 = 20,000. That is a mean of **29.6 records per query** — and since
a single page holds 200, *no query reached even the end of its first page*.

The cut is not the budget: a budget failure raises `PermanentError` and retires
the source, and nothing was retired. It is `min_relative_score: 0.4`, which stops
the collector at the first result scoring below 40% of that query's own top
relevance score. OpenAlex relevance scores decay steeply, so the floor bites
almost immediately — it is a much tighter constraint against OpenAlex's scoring
than against Crossref's, even though both are configured at 0.4.

**Root cause found and fixed 2026-08-31 — the anchor, not the coefficient.**
The earlier diagnosis above is right that `min_relative_score` is the cut, and
wrong about the shape of it. Replaying all 20 frames against the live API
reproduces the run's per-frame yields **exactly, 20 of 20** at `0.4 x max` —
but those yields are bimodal, not a mean of 29.6: 3, 3, 5, 6, 7, 9, 9 on seven
frames and 100, 110, 60 on three.

The cause is that OpenAlex `relevance_score` is unnormalised and blends text
match with citation count. A query naming a well-known field returns one
enormous top score and an ordinary tail, and a floor set at 40% of *that*
becomes unreachable. `ct_ai` scored 3,011 at rank 1 and 1,628 at rank 2, so the
floor cut at six; `ct_biotech` scored 609 then 573 and kept 110. Same query
shape, comparable literature, 18x the yield. **The floor was measuring how much
of an outlier the top hit was.**

Worse, it is not random. The five hardest-hit frames are `ct_quantum`,
`ip_admin_automation`, `ip_policy_reform`, `ct_ai` and `ct_advanced_ict` —
three DISR critical technologies and the two frames mapping most directly to
SI-3 and the Corporate Plan. The floor bit hardest exactly where a query names
an established field cleanly, which is IP Australia's own subject matter.

**Fixed** by anchoring on rank 10 rather than the maximum
(`collection.sources.openalex.relevance_anchor_rank`). Lowering the coefficient
to 0.2, as this issue previously proposed, would have raised the yield while
leaving a 14x spread between frames. See the 2026-08-31 calibration entry.

**Budget was never the constraint, and the config comment was wrong about it.**
Measured from the live rate-limit headers: unauthenticated is $0.10/day, a key
gives $1.00/day, and a request costs $0.001 (10 credits) — so 100 requests/day
without a key and **1,000 with one**. The 2026-08-30 run used **20**. Every
frame stopped inside page one, so `max_pages_per_query: 5` has never been
exercised. The run received 4,000 records over the wire and kept 592.

Note this does **not** retroactively improve any existing run. The 7,378-document
corpus was collected without OpenAlex and still contains zero of its records;
the source only starts contributing at the next collection run. Because that
changes the corpus, results before and after are not comparable — the first run
with OpenAlex live is effectively a new baseline, not a continuation of the
growth curves.

### 4. The scan frame is strongest exactly where the data is easiest

20 frames: 9 Technological, 3 Political, 3 Economic, 2 Legal, 1 Social,
1 Values, 1 Environmental. That distribution mirrors where free structured data
exists, not where opportunities are.

The consequence is systematic, not random: a scan built this way will keep
finding technology trends and keep missing social, values-based and
environmental ones. **Compensate deliberately at the human synthesis session**,
and treat a thin Social/Values shortlist as a property of the instrument rather
than a finding about the world.

### 5. GDELT is unreliable from shared IPs, and the 2026-08-30 run hid four total failures

It rate-limits by source IP and drops connections mid-response with no error
code — a majority of requests failed even at 6 seconds apart on 2026-08-29. It
still returned 3,183 records over that run, so it works; it just cannot be
relied on for any single frame.

**CORRECTED 2026-08-31. It did not behave perfectly on 2026-08-30, and the run
could not tell you.** Four frames — `ip_sme_access`, `ai_authorship_inventorship`,
`ct_biotech` and `ct_advanced_ict` — returned **zero records** after exhausting
all four retry attempts, and all four were written to `collection_log` with
status `success`. 14 of 18, not 18 of 18.

The path: the collector caught `BigThinkError`, logged a warning and returned an
empty generator; Stage 1 saw a clean return and recorded `success` with zero
records; `failed_pairs` stayed at 0; Stage 1's own status was `success`; and
this file recorded "zero failed or skipped source/frame pairs — the first clean
sweep on this project". None of that was true, and nothing in the output said
so. `ct_biotech` — OpenAlex's single most productive frame — has no attention
signal at all, feeding a component that carries 25% of the opportunity index.

This violated the repo's own convention: *"Collectors raise, never swallow. A
collector that returns an empty list on failure produces a silent scan."*

**Fixed 2026-08-31.** Collectors record an incident (`Collector.note_incident`)
rather than swallowing; Stage 1 reads it after draining the generator and logs
`partial` when documents survived or `failed` when none did, with the reason.
Raising instead was not an option: `collect` is a generator consumed with
`list()`, so an exception after the first yield discards the documents already
produced, and a partial window is worth keeping.

The underlying flakiness is unchanged and should still be expected — GDELT
rate-limited this investigation's own probes hard enough to kill a local smoke
test. What changed is that the run now says so.

### 6. PatentsView is off, so there is no patent signal at all

The `patent_activity` component of the opportunity index has no data and its
weight is redistributed across the others. For an IP office's horizon scan this
is a real gap: patents are the lagging confirmation half of the picture.

**Fix:** free key at <https://patentsview.org/apis/keyrequest>, set
`PATENTSVIEW_API_KEY`, enable it in `bigthink_config.yaml`.

**Scope caveat to carry into any briefing:** PatentsView is US grants only.
Australian filing behaviour is in IP RAPID, and the two do not substitute for
each other.

### 7. IP RAPID and IPGOD are described but not ingested

The asset inventory names them and the CKAN collector *discovers* them — the
first real run surfaced IPLoRD and several IPGOD releases as documents. But
nothing downloads or analyses the actual registry tables, so the asset-leverage
axis reasons about IP Australia's data holdings from a description of them
rather than from the data.

This is the largest piece of unbuilt work and is a genuine option for the rest
of the sprint. See Day 4 below.

### 8. Every `bge` threshold is a guess — PARTLY ADDRESSED 2026-08-31

`similarity_thresholds.leader.bge: 0.62` and
`similarity_thresholds.agglomerative.bge: 0.45` have never been swept — only
the `hashing` values have, and the agglomerative one only against an
OpenAlex-only corpus. Run `python -m src.calibrate threshold` after switching
backends, before trusting any score computed under it.

**Partly overtaken 2026-08-31.** The active method is now `bertopic`, where
HDBSCAN takes no cosine cut-off, so no clustering threshold is read at all.
`similarity_thresholds.bertopic.bge` is measured (see the calibration log) and
is used for one thing only: attaching GDELT documents to the nearest finished
topic.

**Still open** for the two methods that do cluster on a cosine. Both
`agglomerative.bge: 0.45` and `leader.bge: 0.62` remain unswept guesses, and
`agglomerative` is the fallback whenever the ML stack is missing — so a
torch-less run is still clustering at a number nobody has measured. Sweep it
with `python -m src.calibrate threshold` before trusting one.

### 9. ~~Two Stage 3/5 outputs are computed but never persisted~~ — FIXED 2026-08-31

`best_asset` (which agency asset a topic is closest to) and `fit_quadrant`
(its 2×2 placement) are produced in memory and reach `topics.csv`, the evidence
cards and `summary.json` — but neither is written to `topic_scores`.
`best_asset` has a column in the schema that nothing populates; `fit_quadrant`
has none.

Found while building the notebook export, which reads everything back from
DuckDB and so cannot see either. The notebook recomputes `fit_quadrant` from
the stored axes (identical result, and it shows the derivation); `best_asset`
is simply not recoverable and is omitted from its Stage 3 table.

**Consequence.** Anything that reads a past run from the database rather than
from that run's CSV is missing the asset axis's most interpretable output —
including the notebook, which is the artefact meant to explain it. Low effort
to fix: add both to `_SCORE_COLUMNS` in `db.py` and to the dict Stage 5 writes.

**Fixed 2026-08-31**, surfaced by building the dashboard (below), which needed
both fields for its details panel and would otherwise have inherited the same
silent gap `report.py`'s 2×2 grid already had (`row.get("fit_quadrant")`
always read `None` there too — same root cause, now also fixed). Added
`fit_quadrant VARCHAR` to `topic_scores` via `ALTER TABLE ... ADD COLUMN IF
NOT EXISTS` (a restored corpus predates the column; plain `CREATE TABLE IF NOT
EXISTS` is a no-op against it), added both columns to `_SCORE_COLUMNS` and to
`fetch_ranked_topics`'s select list, and Stage 5 now writes both. No score
changes — this only makes two already-computed values readable from the
database.

### 10. ~~The R2 key pair has no rights on `bigthink-corpus`~~ — RESOLVED 2026-08-30

Verified working 2026-08-30 by `verify-access.yml`:

```
[PASS] Cloudflare R2 (bucket 'bigthink-corpus', eu jurisdiction)
       HeadBucket=ok, PutObject=ok, GetObject=ok, ListObjectsV2=ok,
       DeleteObject=ok (42 bytes round-tripped)
```

**The cause was the endpoint, not the permissions.** The bucket was created in
the **EU jurisdiction**, which puts it on `<account>.eu.r2.cloudflarestorage.com`
and makes it invisible from the default endpoint `storage.py` hardcoded. Every
request went to a jurisdiction the token has no resources in, and R2 answers
that with `AccessDenied` on everything — including `ListBuckets` — which is
indistinguishable from a permissions problem by inspection.

The jurisdiction is visible in an API token's resource key:
`com.cloudflare.edge.r2.bucket.<account>_eu_<bucket>`. An unrestricted bucket
reads `_default_`. That string is the fastest way to confirm which endpoint a
bucket needs, and is now the first thing to check if this recurs.

**Two false diagnoses on the way, both worth remembering**, because both were
confident and both were wrong in the same direction — reading a transport-level
failure as an authorisation one:

1. *"The token is scoped to a bucket not named `bigthink-corpus`."* Inferred
   from `ListBuckets` being denied. But `ListBuckets` is denied for **every**
   bucket-scoped token, so it was never evidence about the name.
2. *"The key pair is stale or the policy was never saved."* Inferred from
   `PutObject` being refused while the Cloudflare UI showed Edit granted. The
   contradiction was real; the explanation was not.

**A third cause sat underneath**: `R2_ACCOUNT_ID` held the wrong 32 characters
(the Access Key ID is also 32 hex — an easy swap). This produced a refused TLS
handshake rather than an S3 error, because Cloudflare rejects the handshake
outright for an account ID it does not recognise. Confirmed by testing a real
account against a fabricated one on both endpoints.

**What the checker learned**, so none of this costs the same time twice:
`storage.r2.jurisdiction` with strict validation; `verify_access.py` importing
`storage.endpoint_url` so a check cannot pass against an endpoint the pipeline
never uses; per-operation probing rather than failing at the first refusal; a
`GetObject` on a deliberately-absent key to separate a denied read from a
denied write; account-ID shape validation; and connection-level failures
short-circuiting to their own verdict instead of being narrated as permissions.

**`storage.r2.enabled` is now `true`.** `scan.yml` mirrors the corpus to R2
after each run. The `corpus-*` GitHub Release asset remains the source of truth
CI restores from — R2 is a convenience pull point for local Stage 3-5 tuning,
not a replacement.

---

## Calibration log

Append to this. Every entry should say what changed, why, and what moved.

### 2026-08-31 — the dashboard becomes an analytical tool, and starts measuring its own map

**No score changes.** Every number in `topics`, `topic_scores` and
`pipeline_runs` is untouched. What changed is `src/dashboard.py`, which now
renders five views over a finished run instead of one point cloud, and two new
config blocks that change how the map is *laid out* and what it reports about
itself.

**1. The map now follows the clustering configuration.**
`dashboard.projection.follow_clustering` (new, default `true`) takes
`n_neighbors`, `metric` and `random_state` from
`emergence.topics.bertopic` when BERTopic is the clustering method.

*Why.* Under BERTopic the clusters are found by HDBSCAN in a UMAP space built
with a particular neighbourhood size, metric and seed. The dashboard was
projecting with its own unrelated settings, so the picture answered a different
question from the one the topics were formed by, and the disagreement showed up
as a topic scattered across the map for no reason a reader could see. Following
the clustering makes the map a 2-component view of the same manifold.

`min_dist` is deliberately **not** followed: the clustering runs it at 0.0 to
pack clusters as tightly as HDBSCAN likes, which on a screen draws every topic
as one indistinguishable dot.

*What this moves.* The layout of `docs/dashboard.html` only. Under the shipped
`bge` + `bertopic` default, `n_neighbors` and `random_state` both already
matched at 15 and 42, so on the current defaults **nothing moves at all** — the
setting exists so that a future change to the BERTopic block cannot silently
desynchronise the map from the clustering. Under `agglomerative` there is no
UMAP stage to follow and the dashboard's own settings are used, as before.

**2. The map now measures how much it distorts, rather than disclaiming it.**
New `dashboard.fidelity` block. Four measurements, all computed from the run's
own vectors and coordinates:

| measure | question it answers |
|---|---|
| trustworthiness | are the neighbours you can see real? |
| continuity | are the real neighbours visible? |
| neighbour purity, per topic, in *n*-D and in 2D | is a scattered-looking topic incoherent, or merely badly drawn? |
| the k nearest high-dimensional neighbours of every plotted point | shipped to the page so a reader can select a document and watch where its true neighbours landed |

Trustworthiness and continuity are Venna & Kaski's pair; both are quadratic in
memory, so they run on a seeded sample (`dashboard.fidelity.sample`, default
2,500) and the page prints the sample size beside the number. The k-NN pass is
exact and quadratic in time, so past `dashboard.fidelity.max_points` the whole
measurement is skipped and the page says so, rather than putting a free Actions
runner into a long matrix multiply.

That cap is held **at** `dashboard.max_points` (25,000), not below it. The
first draft of this shipped at 15,000 against a 15,036-document corpus, which
would have silently dropped the readout for the sake of 36 documents — a cap
binding by accident rather than by decision. The cost at 25,000 x 768 is on the
order of a TFLOP, tens of seconds inside a scan that takes hours, so the cap is
there for a corpus an order of magnitude larger than this one.

*What this moves.* Nothing in the database. It adds a readout to the map, a
per-topic purity pair to every topic detail, and a `projection_fidelity` table
to the new Data view.

*The metric pairing is deliberate and pinned by a test.* The high-dimensional
side is scored by **cosine** — the metric the clustering used, and the one that
is invariant to the vector magnitudes that differ between backends — and the 2D
side by **euclidean**, because euclidean is what a reader's eye does with the
picture. `test_trustworthiness_scores_the_high_side_by_cosine` guards it.

**Nobody has run this against the real corpus.** The figures above are from a
3,370-document development corpus. The first real numbers will arrive with the
next scan, and a trustworthiness below about 0.9 there would be worth acting on
— it would mean the map is inventing adjacency, and the honest response would
be to say so on the page rather than to tune the projection until the number
looks better.

### 2026-08-31 — review fixes: two numbers move, and one axis is switched off

**What changed, and it is short on purpose.** The repository review (issues
21-32) found three defects that change a result. Two are corrected here; the
third was corrected by removing a wrong number rather than replacing it with a
guessed one.

**1. The DISR critical-technology match is OFF under the shipped default, on
purpose.** The cut-off was a bare `0.25` in `stage3_scoring.py`, never in
config, and under `bge` it matched 114 of 114 topics. It is now
`scoring.strategic_fit.critical_tech_match.thresholds`, keyed per backend:
`hashing: 0.25` (unchanged), `bge:` blank.

Blank means *no topic is matched and no topic receives the +0.10
`critical_tech_bonus`*, with a warning in the run log and "matching DISABLED" in
`pipeline_runs`. That is a deliberate reduction in what the pipeline claims, not
an oversight — the alternative was carrying a cut-off swept in a lexical vector
space into a semantic one, which is what produced the 100% match rate.

*What this moves.* On the shipped default, `critical_tech` becomes empty for
every topic and strategic fit loses a **uniform** +0.10. Uniform, so under
`percentile_rank` the ordering is unchanged — Stage 5 rank-normalises each axis
before weighting. What changes is the reported fit level, and the disappearance
of a column that was noise. Under `hashing` nothing at all changes.

*Where the number comes from next.* `python -m src.calibrate critical-tech`,
which is new, sweeps it against a real corpus and reports the match rate and
distinct DISR fields hit at each candidate. **Nobody has run it** — the corpus
is not in a review environment. Until someone does, this axis is off. The DISR
list is seven fields over a broad horizon scan, so the answer to look for is a
minority of topics matching, not 100% and not 0%.

**2. A topic with no news coverage now scores neutral tone, not the worst
possible tone.** `attention_tone` mapped an absent GDELT signal to 0.0, which on
its own rescaled -10..+10 range is uniformly maximally negative coverage. It is
now 0.5, the midpoint — the same answer `citation_percentiles` already gives for
a source with no citation signal.

*What this moves.* The opportunity index for every topic with no attached news,
upward, on a component carrying 10% of it. GDELT forms no topics and only
attaches at 0.6 x the clustering threshold, so on a research-heavy corpus this
is a lot of topics. **It does not move the composite rank**, which excludes the
index by design.

**3. Average linkage now actually is average linkage.** `cluster_agglomerative`
disagreed with an exact reference on 3 of 30 random corpora and was not
order-invariant (9 of 40 permutations differed). Fixed.

*What this moves.* Nothing in any current headline number — `bertopic` is the
default and takes no part in this. It moves any future `agglomerative` run, and
it means **the 0.14 threshold in the 2026-08-30 entry below was swept with the
defective implementation**. Re-sweep before trusting it on a torch-less run.

**Two numbers were deliberately NOT changed**, and both would have been easy:

- **`min_docs_per_topic` still only warns** (issue 13). Gating it changes which
  topics reach the shortlist, and this file has parked that behind issue 1
  since it was found. It stays parked. What changed is that `CLAUDE.md` and
  `burst.py` no longer describe the guard as though it exists.
- **`min_term_document_frequency` was deleted, not implemented** (issue 26).
  Same reasoning: it was never wired up, and wiring it up now would change every
  topic in the run on the strength of a number nobody has swept.

**Nothing here has been run end to end.** The corpus is gitignored and absent
from the environment these fixes were made in, so every claim above about what
moves is derived from the code, not measured on a run. The next scan is the
measurement.

### 2026-08-31 — BGE embeddings and BERTopic clustering; both defaults changed

**What changed.** `embeddings.backend` `hashing` → `bge`.
`emergence.topics.method` `agglomerative` → `bertopic`, over an explicitly
seeded UMAP + HDBSCAN pair. New `emergence.topics.bertopic` block holding every
hyperparameter. New `similarity_thresholds.bertopic.{bge,hashing}`.

**Why, and who decided.** The owner asked for both, and for the priority behind
them: *a single horizon scan snapshot should be as accurate and useful as a
self-contained run as it can be; its usefulness as a reference point for future
runs is absolutely secondary.* That reverses the argument this repo previously
made for average linkage, which rested entirely on week-over-week
comparability. Recorded in the decision log, because it is a standing
tie-breaker and not a one-off.

**Measured on** the 2026-08-30 corpus restored from release
`corpus-20260831-064857` — 7,501 documents, of which 5,184 are from forming
sources. The verification run's own outputs are committed at
`data/outputs/verify-bertopic/` — shortlist, evidence cards, topics.csv and the
peer-review notebook — so every number below can be checked rather than taken
on trust. It is a Stages 0 and 2-5 re-analysis (`--skip-collect`) of a restored
corpus, not a collection run, and is named to make that obvious.

**Read the evidence cards there before trusting the topics.** Nobody has. That
is the check that caught both clustering artefacts last time, and it has not
been done for this method. Embedded with `BAAI/bge-base-en-v1.5` (768 dimensions) in 780 s at
~10 documents/second on one contended core.

**`n_neighbors` sweep**, seed 42, `min_topic_size` 8, `max_topics` 120. `raw`
is clusters before the cap; `largest` is a share of assigned documents:

| n_neighbors | raw | kept | assigned | largest |
|---|---|---|---|---|
| 5 | 163 | 115 | 67.3% | 6.5% |
| 10 | 139 | 114 | 70.7% | 5.5% |
| **15** | **118** | **112** | **75.0%** | **5.2%** |
| 20 | 99 | 94 | 78.2% | 11.8% |
| 30 | 93 | 90 | 77.1% | 11.6% |
| 50 | 86 | 83 | 72.6% | 12.9% |

15 is the inflection and is what is set. Below it the corpus fragments badly —
163 and 139 clusters against a cap of 120. At 20 and above a single
~480-document quantum cluster forms and the largest share more than doubles.

**These are sweep numbers, and the pipeline does not reproduce them exactly.**
The verification run over the same 5,184 documents at the same settings formed
**124** clusters, not 118, and `max_topics` bound (issue 16). The two encodings
agree to a cosine of 0.9999998; that was enough. Read the shape of this table,
not its individual counts, and take the pipeline's own log line as the number
for any given run. See issue 20.

**Seed sweep**, `n_neighbors` 15:

| seed | topics | assigned | largest |
|---|---|---|---|
| 0 | 115 | 73.5% | 5.3% |
| 1 | 112 | 73.4% | 5.3% |
| 7 | 105 | 77.3% | 11.8% |
| **42** | **112** | **75.0%** | **5.2%** |
| 1234 | 107 | 76.4% | 12.1% |

**42 is recorded because it was chosen, not because it was the library
default.** It has the smallest largest-cluster share and the highest assigned
share among the seeds that avoid the big cluster. Chosen on those structural
criteria and not on which shortlist read best — that would be fitting the
instrument to the answer it exists to produce.

**Attachment threshold.** Under `bertopic` the clustering threshold does not
cluster anything — HDBSCAN takes no cosine cut-off — and is read for exactly
one purpose: attaching GDELT documents to the nearest finished topic, at
`attachment_threshold_ratio` (0.6) of its value. Measured over 2,317 GDELT
documents against the 112 centroids: min 0.406, 10th 0.529, median 0.617, 90th
0.729, max 0.902.

The first guess of 0.75 was **wrong and would have gone unnoticed**: it gives
an operative 0.45, below even the observed minimum, and attached 99.5% of
GDELT — the "has stopped discriminating" failure the runbook warns about, which
looks exactly like a working attention signal from the outside. Set to 0.90,
operative 0.54, attaching 86%. The distribution is unimodal with no natural
cut, so this trims the worst ~14% by judgement rather than finding a boundary.

**What moved.** Measured on the verification run (`verify-bertopic`, Stages
0 and 2-5 over the restored corpus): 120 topics formed from 5,184 forming
documents, **4,030 assigned (78%)**, 1,121 left as HDBSCAN outliers, 114 topics
surviving the distinct-vocabulary drop, of which 25 weak, 33 strong and 6 in
Horizon 3.

Against the previous `hashing` + `agglomerative` baseline, the largest topic
went from **1,497 documents (54% of everything assigned)** to **5.2%**, and
assigned share from 56% to 78%. Labels are
legible without reading the members: "geographical indication / gi / trade /
agreement", "service delivery / government / digital / public service",
"watermark / image / attack / dnn", "artificial intelligence / patent /
inventorship / ai". The catch-all that dominated every previous run is gone.

**What is NOT yet known, and must not be assumed.**

- ~~Whether the asset-leverage axis widens.~~ **Measured on the verification
  run, and it does not.** See below — this was the stated point of the BGE
  switch and it did not deliver it.
- **Whether the shortlist is better.** No ranking here has ever been validated
  against a known past opportunity (issue 1). Cleaner topics are not a
  validated ranking, and this entry is not evidence for one.
- **Nothing here is comparable with any earlier run.** Different vector space,
  different clustering method, different topic ids.
- **The topic set is deterministic but not robust.** Re-embedding the same
  corpus at a different batch size moved the cluster count by 6. See issue 20
  before comparing any two runs' topic counts.
- **27 of the 114 topics fall below `min_docs_per_topic` (20)** and are
  suppressed rather than scored, which is the intended behaviour but is a
  larger share than the previous run had.

---

**The asset-leverage axis did NOT widen — the expected win did not happen.**
Top 15 by rank, the same comparison issue 2 was written from:

| run | asset leverage | span | span / mean |
|---|---|---:|---:|
| `2026-08-31` (hashing + agglomerative) | 0.044–0.233 | 0.189 | **1.86** |
| `verify-bertopic` (bge + bertopic) | 0.369–0.501 | 0.132 | **0.30** |

The values moved *up* — the floor goes from 0.04 to 0.37 — but the spread got
**six times narrower in relative terms**. BGE cosines have a high floor: two
unrelated pieces of text still score ~0.35, so everything resembles everything
and the axis separates less, not more. Strategic fit shows the same pattern
(span 0.366 → 0.276 on the shortlist).

**Why this is not a disaster, and not nothing.** Stage 5 percentile-ranks each
axis before weighting (`percentile_rank` in `stage5_synthesis.py`), so asset
leverage still contributes its configured 25% of the *ordering* whatever its
raw scale — a compressed axis that orders correctly still ranks correctly. What
a narrow spread costs is signal-to-noise: the gaps deciding the order are now
smaller relative to the level, so the ordering is more easily moved by noise.

**So issue 2 is not fixed by the backend switch, and the second-order fix
becomes the main one:** expand the lexicons in
`data/strategy/asset_inventory.yaml`, which carry only 7–11 entries each. The
diagnosis in issue 2 — that `hashing` matched vocabulary rather than meaning —
was probably right about the *cause* and wrong about the *cure*.

There is still a good reason to keep BGE: it is what makes the clustering work
(largest topic 54% → 5.2%). It just is not what fixes Stage 3.

---

### 2026-08-31 — the fresh baseline itself (run 33345343027)

**What changed.** Nothing in the config. The corpus was recollected from empty
with every fix from the entry below live. 164 minutes.

**What moved, and none of it is comparable with 2026-08-30** — a different
corpus means a different population, and every headline score is percentile-
ranked within its run:

| | 2026-08-30 | 2026-08-31 |
|---|---:|---:|
| documents | 7,219 | **15,036** |
| topics | 15 | **120** (= `max_topics`, see issue 16) |
| largest topic, share of assigned | **57%** | **3.9%** |
| OpenAlex documents / frames | 581 / 20 | **3,799 / 20** |
| arXiv documents / frames | 2,018 / 9 | **678 / 3** (issue 14) |
| GDELT span | 2026 only | **2024–2026** |
| Stage 1 reported status | `success`, 0 failures | **`partial`, 7 failed + 9 partial** |
| emergence vs log(size) | −0.43 | **+0.05** |
| asset leverage range | 0.030–0.101 | 0.017–0.239 |

**What is newly known.** Three things worth carrying forward.

1. **Fixing the clustering removed the scoring bias by itself.** No weight was
   touched. See issue 12 — this is the strongest evidence so far for the
   ordering rule this project already follows: inputs before weights.
2. **The honest collection log immediately earned its keep.** It surfaced the
   arXiv collapse (issue 14) on its first real run. Under the previous code that
   would have been six frames silently contributing nothing while the run
   reported a clean sweep.
3. **Two new artefact classes are visible now that the mega-cluster is gone**,
   both small and both cheap to fix: HTML in CKAN descriptions (issue 15) and a
   `kilometre / square kilometre / africa / population` cluster at rank 49 that
   is worth a look on the evidence cards.

### 2026-08-31 — clustering method replaced; OpenAlex floor re-anchored; GDELT windowed

**Nothing here has been run end to end on a real scan yet.** The numbers below
come from a 2,987-document OpenAlex corpus pulled specifically to measure them
(one page per frame, 2026-08-30) and from replaying the 2026-08-30 scan frame
against the live API. The next full run is the test.

**1. Clustering: `leader` -> true average-linkage `agglomerative`.**

*Why.* The method named "agglomerative" was not agglomerative — it was leader
clustering, which updates a cluster's centroid in place as it accretes. A
growing cluster's centroid drifts toward the corpus mean, a mean-ward centroid
resembles everything, so it absorbs more. The 2026-08-30 run recorded the end
state: `T0000` held 1,497 documents (57% of everything assigned) under the
label "image / patent / learning / watermark", and its stored novelty of 0.045
means its centroid sat at **cosine 0.955 from the corpus centroid**, against
0.12-0.43 for every other topic.

*Evidence.* Both methods run over the same 2,987 real documents, `hashing`:

| method | thr | topics | assigned | largest | share of assigned | cos(centroid, corpus) |
|---|---:|---:|---:|---:|---:|---:|
| leader | 0.26 | 1 | 2,074 | 2,074 | 100% | 0.998 |
| leader | 0.30 | 2 | 1,284 | 1,274 | **99%** | 0.995 |
| agglomerative | 0.14 | 61 | 1,659 | 173 | **10%** | 0.967 |

*Also fixed by construction.* Documents arrive `ORDER BY published_date`, so
leader clustering seeded clusters from the oldest documents and spent eight
years accreting — a topic first appearing in 2024 had to out-compete centroids
that had already absorbed everything before it. Average linkage is
order-invariant (`test_average_linkage_is_order_invariant`), so the bias cannot
exist. Leader's silent drop of every unmatched document once `max_topics` was
reached is also gone; the new method keeps the largest `max_topics` and says so.

`leader` is retained as a config value so a pre-2026-08-30 run can be
reproduced from its own snapshot.

**2. Clustering threshold: 0.30 -> 0.14, and the key is now per method.**

*Why.* A threshold belongs to a method as well as a backend. `leader` compares
a document to a centroid; `agglomerative` compares the mean pairwise similarity
between two clusters' members, which is far lower on identical data. Measured
on those 2,987 documents: mean pairwise cosine **0.075**, 99th percentile
**0.191**. At 0.30, average linkage assigned **23 of 2,987 documents**.

*Sweep (agglomerative, hashing).* 0.14 chosen: first value with coverage above
30% and no cluster over 25% of what is assigned.

| thr | topics | coverage | largest share |
|---:|---:|---:|---:|
| 0.10 | 31 | 82% | 40% |
| 0.12 | 48 | 71% | 25% |
| **0.14** | **61** | **56%** | **10%** |
| 0.16 | 63 | 40% | 9% |
| 0.18 | 39 | 22% | 11% |

Labels at 0.14 are interpretable without help: "energy / storage / battery /
renewable", "quantum / cryptography / cryptographic / security", "traditional
cultural / traditional knowledge / indigenous / cultural expression".

`similarity_threshold_by_backend` becomes `similarity_thresholds`, keyed by
method then backend. The old shape is still read, so old snapshots resolve.

**Re-sweep this on the fresh baseline.** The corpus above is OpenAlex-only and
the real one will not be. `bge: 0.45` is a shape-preserving guess and has never
been swept (issue 8, still open).

**3. OpenAlex relevance floor re-anchored: `0.4 x max` -> `0.4 x rank-10`.**

*Why.* OpenAlex relevance blends text match with citation count, so a query
naming a well-known field returns one enormous score and a normal tail.
Anchoring the floor on that maximum made a frame's yield a function of how much
of an outlier its top hit was rather than of how much literature existed. See
the rewritten issue 3.

*Evidence.* Replaying all 20 frames against the live API, `0.4 x max`
reproduces the 2026-08-30 per-frame yields **exactly, 20 of 20** — which is
what identifies the floor as the sole cause. Switching the anchor to rank 10:

| | total kept | min frame | max frame | spread |
|---|---:|---:|---:|---:|
| 0.4 x max (old) | 592 | 3 | 110 | 37x |
| 0.2 x max (previously proposed) | 3,544 | 29 | 400 | 14x |
| **0.4 x rank-10** | **3,857** | **52** | **400** | **8x** |

Confirmed live: one page per frame under the new anchor returned **3,120
records**, against 592 for the entire previous run at five pages. Narrow queries
still get less, correctly — `ip_enforcement_counterfeit` (1,283 works available)
keeps 52.

`relevance_anchor_rank: 1` reproduces the old behaviour and is what Crossref
uses, where the gentler score decay makes the floor near-inert (197.8 of a
possible 200 records per query on the 2026-08-30 run).

**4. Crossref record types excluded: `peer-review`, `component`, and
back-matter titled *References* / *Bibliography* / *Index*.** Closes issue 11.

**5. GDELT window split into 4 date-range chunks.**

*Why.* `timespan=24m` never returned 24 months. `artlist` sorts
most-recent-first and `maxrecords` caps at 250, so one request returns the
newest 250 articles however wide the window — every one of the 2,040 GDELT
documents on the 2026-08-30 run carried a 2026 date, and a live re-test
returned only 2026-06 to 2026-08. `startdatetime`/`enddatetime` do work
(verified 2026-08-30, returning genuine 2025 articles), contrary to the note in
the collector, which is now corrected.

*Cost.* 32-36 s per request measured, so 4 windows x 18 frames is 72 artlist
requests against 18. `scan.yml` timeout raised 180 -> 240 min. A failed window
is recorded and the remaining windows still attempted. **Whether this survives
a shared runner is the open question for the next run** — GDELT rate-limited
this investigation's own probes hard enough that a local smoke test could not
finish. If it does not hold, lower `window_chunks` rather than reverting to a
window that lies.

**6. Collector failures are now recorded, not swallowed.** See the rewritten
issue 5. No result changes; it changes what the run can tell you about itself.

### 2026-08-30 — new baseline corpus (OpenAlex live); no weight changed

**What changed.** Nothing in the config. The *corpus* changed: `scan.yml` ran
for the first time, from an empty database, with `OPENALEX_API_KEY` set. The
2026-08-29 corpus was local and no longer exists.

**What moved.** Everything, and none of it is comparable. 7,219 documents
against 7,780; 15 topics against 14; a different source mix (OpenAlex 581 in,
GDELT down 3,183 → 2,040). Because emergence, the opportunity index and the
composite rank are all percentile-ranked *within a run*, a changed population
changes every headline score whether or not the world moved. Recorded here so
that a future reader comparing the two shortlists knows not to.

**What is newly known.** The clustering threshold `0.30`, swept against the
4,195-document topic-forming corpus of 2026-08-29, does not transfer: on this
corpus the catch-all topic `T0000` holds 1,497 documents — **54% of everything
assigned to any topic**, against the 10% largest-cluster figure that justified
0.30. Topic coverage is 38% of the corpus. Re-sweep before trusting any
cluster-derived number on a corpus this size.

### 2026-08-29 — clustering threshold 0.18 → 0.30 (hashing backend)

**Why.** Topic labels on the first real run were incoherent
("patent / watermark / llm / quantum" over 1,804 documents).

**Evidence.** Sweep over the 4,195-document topic-forming corpus
(`python -m src.calibrate threshold`):

| threshold | topics | assigned | largest cluster |
|---:|---:|---:|---:|
| 0.18 | 21 | 93% | 64% of corpus |
| 0.26 | 16 | 81% | 52% |
| 0.28 | 17 | 75% | 43% |
| **0.30** | **16** | **31%** | **10%** |
| 0.38 | 10 | 15% | 7% |

**Decision.** 0.30. Sharp inflection: the mega-cluster collapses from 43% to
10% between 0.28 and 0.30, and every label becomes interpretable
("geographical indication / protection / trade", "automated decision-making /
administrative law", "prior art / patent / search / retrieval").

**Cost, stated plainly.** Only 31% of topic-forming documents are assigned to
any topic. The rest genuinely do not cluster tightly under a lexical backend.
This is the strongest argument for moving to `bge`.

### 2026-08-29 — scoring terms per topic 8 → 30

**Why.** Asset-leverage scores were near zero for every topic. With 8 terms —
several near-duplicates ("geographical indication" / "indication" /
"geographical") — almost no multi-word lexicon entry could match.

**Decision.** Keep 4 terms for labels (people read those), keep 30 for scoring
(lexicons have 7–12 multi-word entries). Fit scores gained spread; leverage
improved but remains weak — see Open issue 2.

### 2026-08-29 — attributes and ranking axes rank-normalised before weighting

**Why.** A weighted sum of raw values is dominated by whichever input has the
widest spread, so the configured weights described something other than what
the code did. Measured on the real run: asset leverage configured at 0.25 drove
5.7% of the ranking; `novelty` configured at 0.25 drove ~6% of the emergence
score.

**Decision.** Percentile-rank within the run before weighting, at both levels.
Influence now matches configuration exactly (40.0% / 35.0% / 25.0%).

**Consequence to state when presenting.** Emergence scores and composite ranks
are now explicitly *relative to the run's population*. A run of uniformly dull
topics still yields one scoring near 1.0. Raw attribute values are stored
alongside and keep their absolute meaning.

---

## Decision log

Design choices worth not relitigating. Fuller reasoning is in `CLAUDE.md`.

| Decision | Reason |
|---|---|
| DuckDB, not SQLite | Every stage aggregates; `BasicInfraSuggestion.md` specifies it. Single-writer, hence the Actions concurrency group |
| ~~`hashing` embeddings by default~~ → **`bge` since 2026-08-31** | `hashing` matches vocabulary, not meaning, which is why the asset-leverage axis barely discriminated (issue 2). `hashing` stays supported and is what the tests and any torch-less machine run on — the pipeline must remain runnable with no torch and no model download |
| ~~Agglomerative clustering, not BERTopic~~ → **BERTopic since 2026-08-31** | Reversed on the owner's instruction. The old reason was week-over-week comparability; the owner's stated priority is that a single scan be as accurate and useful **on its own** as it can be, with its value as a reference point for later runs explicitly secondary. Every argument for average linkage was an argument about the second thing |
| A single snapshot beats cross-run comparability, where they conflict | The standing tie-breaker behind the row above, and worth applying beyond it. Where a choice trades accuracy *within* a run against stability *across* runs, take the accuracy. Cross-run comparison was already fragile — a changed corpus or config snapshot invalidates it — and `docs/method.md` has always said not to compare scores across runs without checking both |
| Every BERTopic hyperparameter in config, none left to a library default | A default is a hyperparameter nobody wrote down. The seed especially: unseeded UMAP makes two runs of one corpus disagree about what the topics are, so `config.py` refuses a non-integer `random_state` rather than letting an unreproducible run start |
| Agglomerative kept as the no-torch fallback, not deleted | A contributor without the ML stack must still be able to run and test the pipeline. It is also what the plain CI job exercises, which keeps that guarantee from rotting |
| Direct logistic fit, not logit linearisation | Linearisation reports an early-exponential topic as *saturated*, inverting the horizon band for exactly the technologies a horizon scan exists to find |
| GDELT excluded from topic formation | 70-character multilingual headlines at 44% of the corpus produced topics like "brainrot / ai art". It is an attention proxy, and good at that |
| Time series from full-window sources only | GDELT's rolling 24-month window put 5,025 of 7,378 documents in one slice, making flat topics read as bursting for eight straight years |
| arXiv collected per year | Sorting by date and taking the first N gave 1,449 documents dated 2026 and none before 2022 — an artefact that reads as an explosion |
| Opportunity index excluded from the ranking | It is the weakest-founded number here; folding it into the headline order would launder that weakness |
| Thin topics suppressed, not scored | A composite built on 8 documents looks identical to one built on 800 |
| Corpus persisted as a Release asset, not committed | Binary, grows, does not diff. Same pattern Tripwire uses for its SQLite corpus |
| R2 added as a mirror, not a replacement for the Release asset | The Release restore/publish cycle in `scan.yml` already works; swapping its only state mechanism for one requiring a not-yet-created bucket right before its first scheduled run was the wrong time to take that risk. R2 is additive and optional (`storage.r2.enabled: false` by default) |

---

## Plan for the rest of the week

Day 1 is done. The rest is ordered so that each day's output is useful even if
the following day does not happen.

### Day 2 — Make the inputs real

The pipeline is only as good as what it collects and what it scores against.

1. ~~**Get the OpenAlex key.**~~ Done — live and contributing as of 2026-08-30,
   though throttled by the relevance floor (issue 3). **PatentsView is still
   unset** (issue 6), so there is no patent signal at all.
2. ~~**Re-run collection.**~~ Done — the `2026-08-30` baseline. The next
   collection run should carry the issue 11 and issue 3 fixes, since both change
   what is collected and both are cheaper to do before more runs accumulate.
3. **Review the scan frame** (`data/strategy/scan_frame.yaml`) with fresh eyes,
   and if possible with a colleague. It determines what can be found at all.
   Specifically: is anything IPAVentures cares about absent? Are the Social and
   Values frames good enough to be worth keeping, or should they be dropped as
   honestly out of reach (issue 4)?
4. **Verify the DISR critical technology list** against the current
   industry.gov.au publication and set `verified: true`. It is transcribed, not
   fetched.

### Day 3 — Switch to real embeddings and re-calibrate

1. `pip install torch --index-url https://download.pytorch.org/whl/cpu` then
   `pip install -r requirements-ml.txt`.
2. Set `embeddings.backend: "bge"`.
3. `python -m src.calibrate threshold --show-labels` and choose a new threshold
   from the sweep. **Record it here.**
4. Re-run and compare shortlists against Day 2's. Where the two disagree is
   where the lexical backend was misleading you.
5. Expect asset leverage to become usable (issue 2). If it does not, the
   inventory lexicons are the next thing to expand.

### Day 4 — Validate, or ingest IP RAPID

Two genuinely different options. **Pick one; do not start both.**

**Option A — Validate the method (recommended).** Run the known-opportunity
test from `docs/runbook-calibration.md` Step 1 against two or three past
opportunities. This is what converts the shortlist from "a thing the pipeline
produced" into "a thing with reason to be believed", and it is the single
biggest gap in the project.

**Option B — Ingest IP RAPID.** Adds real registry data to the asset-leverage
axis (issue 7) and would let white-space analysis follow. Higher ceiling,
bigger build, and it leaves the ranking still unvalidated.

Choose A unless someone senior specifically needs the registry work first.

### Day 5 — Human synthesis

This is where the method earns its keep, and it is not automatable.

1. **Discard the artefacts.** Read the top evidence cards. Any topic whose
   documents are not a coherent theme is a clustering artefact. Expect some;
   finding them is the point.
2. **Run the session** over the survivors — Seven Questions on each, with
   Doblin Ten Types to widen the framing past "new product". Both prompts are
   already on every card. A regulator can innovate in process, channel and
   engagement.
3. **Write up the top three to five** as venture candidates, with the evidence
   card as the appendix for each.

### If a day is lost

Cut in this order: Day 4 Option B first, then Day 3 (the `hashing` backend
still produces a usable ordering), then Day 2's scan-frame review. **Do not cut
Day 5.** A ranked list nobody has interrogated is not research.

---

## Next actions, concretely

For whoever — or whichever Claude instance — picks this up next. The first two
come out of the `2026-08-30` baseline and are both small:

1. ~~**Collect the fresh baseline.**~~ Done — `2026-08-31`, run 33345343027.
2. **Read the evidence cards for the top ten.** Nobody has done this yet for
   this run, and on the last baseline it was the only check that caught the
   artefacts. Start with rank 12 (`copyright / page / exception limitation`,
   292 documents, highest emergence in the run) and rank 49
   (`kilometre km / square kilometre / africa / population`).
3. **Fix arXiv (issue 14).** It is the fastest emergence signal in the corpus
   and it lost two thirds of its frames. Catch per year rather than per frame.
4. **Re-sweep the threshold and `max_topics` on this corpus**:
   `python -m src.calibrate threshold --show-labels`. 0.14 was swept against
   2,987 OpenAlex-only documents; this corpus is 15,036 across five sources, and
   the topic cap is now binding (issue 16).
4. `python -m pytest tests/ -q` — expect 298 (290 passing, 8 skipped without
   `requirements-ml.txt`; all 298 pass with it). If not, start there.
5. Read `docs/method.md` if you have not; it is what the numbers mean.
6. `python -m src.verify_access` — confirms the OpenAlex and R2 credentials
   still work before a run depends on them. Locally it needs the variables
   exported; in CI the **Verify credentials** workflow is the only thing that
   can read the repository secrets. Note that a *local* full run now starts from
   an empty database unless you pull the corpus first:
   `python -m src.storage pull-corpus`.
7. Open `data/outputs/<run_id>/shortlist.md` and read the top five evidence
   cards **before** looking at any score. On the baseline run that check is what
   found both artefacts; nothing else did.
8. To hand the method to someone else — a colleague, a reviewer, anyone who
   should be able to disagree with it — send
   `data/outputs/<run_id>/horizon-scan-<run_id>.ipynb` rather than the
   shortlist. It shows the run stage by stage and re-derives its numbers, so
   the argument starts at the scan frame and the weights instead of at "where
   did this come from?". Write the answers back into
   `data/outputs/<run_id>/observations.yaml`; they are folded into the notebook
   the next time it is generated.
9. Work the day plan above from wherever it has got to.
10. Append to the calibration log whenever you change a number.
11. **Check the dashboard on the next real scan.** `src/dashboard.py`
    (`docs/dashboard.html`, linked from the top of `docs/index.html`) was
    added 2026-08-31 and exercised against synthetic fixtures and a headless
    browser, but not yet against a real corpus — `scan.yml` builds it, but no
    scheduled run has done so yet at time of writing. Confirm it renders on
    the published Pages site, that UMAP (not the PCA fallback) is what
    actually ran (`docs/dashboard.html` embeds `"projection_method"` in its
    data — check it says `"umap"`), and that install time for `umap-learn`/
    `scikit-learn` did not meaningfully eat into the run-time budget (issue 17).

### Things not to do

- Do not present the opportunity index as a market size. It is a relative,
  within-run ordering. This is the most important caveat in the method and the
  easiest to lose in a slide.
- Do not compare scores across runs unless the config snapshot and corpus
  match. `pipeline_runs.config_snapshot` records both.
- Do not tune weights to make a favoured topic rise. That is how a method
  becomes a way of confirming what you already thought.
- Do not skip the evidence cards.

---

## Environment

| What | State |
|---|---|
| `OPENALEX_API_KEY` | **Set, verified, and now exercised** — 20/20 queries succeeded on the `2026-08-30` run, where every previous run retired the source at the first frame. It contributes only 8% of the corpus; the cause is the relevance floor, not the key (issue 3) |
| `PATENTSVIEW_API_KEY` | **Not set.** No patent signal (issue 6) |
| Crossref, arXiv, GDELT, data.gov.au | Working, no keys needed |
| GitHub Actions | `tests.yml` on push/PR — green. `scan.yml` weekly Sun 19:00 UTC — **first ran 2026-08-30 (run 33310810297) and succeeded end to end.** Every step passed: corpus restore (no-op, nothing to restore), 66 min collection, analysis, site build, output commit, corpus release, R2 mirror, artefact upload |
| GitHub Pages | `docs/` is built by `src.report` (ranked shortlist) and, **new 2026-08-31**, `src.dashboard` (interactive point-cloud explorer, `docs/dashboard.html`, linked from the top of the shortlist). Pages needs enabling in repository settings |
| Local corpus | `data/bigthink.duckdb`, gitignored, ~10 MB at 7,780 documents |
| Cloudflare R2 | **Working, and now carrying a real corpus.** Verified 2026-08-30, then exercised for real by the baseline run: `Pushed data/bigthink.duckdb -> r2://bigthink-corpus/bigthink.duckdb`. Bucket `bigthink-corpus` in the **eu** jurisdiction (`storage.r2.jurisdiction: eu` — not reachable from the default endpoint; see issue 10) |
| Corpus release | **Exists for the first time.** The baseline run published the first `corpus-*` Release asset. Before 2026-08-30 there were none, which is why that run collected from an empty database. This is the state `scan.yml` restores from |

---

## Reuse from other IPAVentures repositories

Recorded so the lineage is not lost, and because these are also assets in their
own right (`data/strategy/asset_inventory.yaml`).

| From | Reused here |
|---|---|
| **Tripwire** | `RetryableError`/`PermanentError` split and the retry decorator; single validated YAML config; schema-owning db module with a run log; scheduled-Actions pattern with state persisted via Releases; runbooks in repo; the BGE bi-encoder choice, so vectors stay comparable between the two systems |
| **BasicInfraSuggestion.md** | Actions for compute, DuckDB for analysis, repo for versioned results, Pages for publication — the zero-budget shape of the whole thing |
| **Octavius, Wiseau** | Not reused in code; recorded in the asset inventory as engineering capability the lab can bring to a venture |

Tripwire is also the natural home for anything this project needs to *monitor*
continuously — it already watches ~156 authoritative sources with change
detection and semantic scoring. If a shortlisted opportunity needs ongoing
tracking, extend Tripwire rather than rebuilding it here.
