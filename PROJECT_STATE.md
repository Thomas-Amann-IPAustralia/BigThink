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

**Where it stands.** The pipeline is built, runs end to end in about 13 seconds
on a collected corpus, and produces a plausible ranked shortlist with evidence
cards. **The method is not yet validated.** No weight in it has been tested
against a known past opportunity. Treat current output as proof the machinery
works, not as a finding.

**Time budget.** Roughly one week, of which day 1 is spent. See the plan below.

---

## Status

| Stage | State | Notes |
|---|---|---|
| 0 — Strategy encoding | **Working** | 34 references: 9 objectives, 6 initiatives, 7 DISR fields, 12 assets |
| 1 — Signal collection | **Working, one source down** | 4 of 6 sources live. OpenAlex needs a key; PatentsView disabled by default |
| 2 — Emergence detection | **Working** | Kleinberg bursts, logistic growth curves, Rotolo five-attribute score, Three Horizons |
| 3 — Fit and leverage | **Working, weak** | Strategic fit is usable; asset leverage is compressed — see Open issue 2 |
| 4 — Opportunity index | **Working, partial** | `patent_activity` has no data without PatentsView; weight redistributes automatically |
| 5 — Synthesis | **Working** | Shortlist, 2×2 views, evidence cards, CSV, published HTML |
| Notebook export | **Working, not yet reviewed by anyone** | `src/notebook.py`; written automatically after Stage 5. Re-derives emergence, horizon, index and composite rank from stored inputs |
| Automation | **Written, not yet exercised** | `scan.yml` and `tests.yml` — neither has run in Actions yet |
| Tests | **116 passing** | Offline by design; every defect found so far has one |

**First real run — `2026-08-29`:** 7,780 documents across 2018–2026 from
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

### 2. The asset-leverage axis is compressed and barely discriminates

Across all 15 topics it spans 0.03–0.10. Rank-normalisation means it still
contributes its configured 25% of the *ordering*, but the underlying signal is
thin.

**Cause:** the `hashing` embedding backend matches vocabulary, not meaning. A
topic's 30 terms and a 60-word asset description share few exact tokens, so
cosine is near zero and only the lexicon match carries signal.

**Fix:** switch to the `bge` backend (`docs/runbook-calibration.md`, Step 3),
then re-sweep the clustering threshold — the whole similarity scale changes.
This is the highest-value single change available.

**Second-order fix:** expand the lexicons in
`data/strategy/asset_inventory.yaml`. They currently carry 7–11 entries each;
more entries, and more of the phrasing the literature actually uses, would
help under either backend.

### 3. OpenAlex is metered and currently contributes nothing

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

**Fix:** free key at <https://openalex.org>, set `OPENALEX_API_KEY` locally and
as a repository secret. This is the best research source available and is worth
doing first thing.

### 4. The scan frame is strongest exactly where the data is easiest

20 frames: 9 Technological, 3 Political, 3 Economic, 2 Legal, 1 Social,
1 Values, 1 Environmental. That distribution mirrors where free structured data
exists, not where opportunities are.

The consequence is systematic, not random: a scan built this way will keep
finding technology trends and keep missing social, values-based and
environmental ones. **Compensate deliberately at the human synthesis session**,
and treat a thin Social/Values shortlist as a property of the instrument rather
than a finding about the world.

### 5. GDELT is unreliable from shared IPs

It rate-limits by source IP and drops connections mid-response with no error
code — a majority of requests failed even at 6 seconds apart. It still returned
3,183 records over the full run, so it works; it just cannot be relied on for
any single frame. Failures are logged and cost only the attention component for
that frame. Nothing to fix; know it when reading `collection_log`.

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

### 8. The `bge` threshold is a guess

`similarity_threshold_by_backend.bge: 0.62` has never been swept — only the
`hashing` value has. Run `python -m src.calibrate threshold` after switching
backends, before trusting any score computed under it.

### 9. Two Stage 3/5 outputs are computed but never persisted

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

---

## Calibration log

Append to this. Every entry should say what changed, why, and what moved.

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
| `hashing` embeddings by default | The pipeline must run and be testable with no torch and no model download. `bge` is a config switch, and the intended destination |
| Agglomerative clustering, not BERTopic | BERTopic finds better topics but shifts between runs unless carefully seeded. Week-over-week comparability matters more while weights are unsettled |
| Direct logistic fit, not logit linearisation | Linearisation reports an early-exponential topic as *saturated*, inverting the horizon band for exactly the technologies a horizon scan exists to find |
| GDELT excluded from topic formation | 70-character multilingual headlines at 44% of the corpus produced topics like "brainrot / ai art". It is an attention proxy, and good at that |
| Time series from full-window sources only | GDELT's rolling 24-month window put 5,025 of 7,378 documents in one slice, making flat topics read as bursting for eight straight years |
| arXiv collected per year | Sorting by date and taking the first N gave 1,449 documents dated 2026 and none before 2022 — an artefact that reads as an explosion |
| Opportunity index excluded from the ranking | It is the weakest-founded number here; folding it into the headline order would launder that weakness |
| Thin topics suppressed, not scored | A composite built on 8 documents looks identical to one built on 800 |
| Corpus persisted as a Release asset, not committed | Binary, grows, does not diff. Same pattern Tripwire uses for its SQLite corpus |

---

## Plan for the rest of the week

Day 1 is done. The rest is ordered so that each day's output is useful even if
the following day does not happen.

### Day 2 — Make the inputs real

The pipeline is only as good as what it collects and what it scores against.

1. **Get the API keys.** OpenAlex (issue 3) and PatentsView (issue 6). Half an
   hour, and it roughly doubles the evidence base.
2. **Re-run collection** with both live: `python -m src.pipeline --run-id $(date -u +%F)`.
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

For whoever — or whichever Claude instance — picks this up next:

1. `python -m pytest tests/ -q` — expect 95 passing. If not, start there.
2. Read `docs/method.md` if you have not; it is what the numbers mean.
3. Get the OpenAlex key (issue 3). Cheapest high-value action available.
4. `python -m src.pipeline --run-id $(date -u +%F)` for a fresh full run.
5. Open `data/outputs/<run_id>/shortlist.md` and read the top five evidence
   cards **before** looking at any score.
6. To hand the method to someone else — a colleague, a reviewer, anyone who
   should be able to disagree with it — send
   `data/outputs/<run_id>/horizon-scan-<run_id>.ipynb` rather than the
   shortlist. It shows the run stage by stage and re-derives its numbers, so
   the argument starts at the scan frame and the weights instead of at "where
   did this come from?". Write the answers back into
   `data/outputs/<run_id>/observations.yaml`; they are folded into the notebook
   the next time it is generated.
7. Work the day plan above from wherever it has got to.
8. Append to the calibration log whenever you change a number.

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
| `OPENALEX_API_KEY` | **Not set.** Blocks the best research source (issue 3) |
| `PATENTSVIEW_API_KEY` | **Not set.** No patent signal (issue 6) |
| Crossref, arXiv, GDELT, data.gov.au | Working, no keys needed |
| GitHub Actions | `scan.yml` weekly Sun 19:00 UTC; `tests.yml` on push. **Neither has run yet** — the first scheduled run should be watched |
| GitHub Pages | `docs/` is built by `src.report`; Pages needs enabling in repository settings |
| Local corpus | `data/bigthink.duckdb`, gitignored, ~10 MB at 7,780 documents |

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
