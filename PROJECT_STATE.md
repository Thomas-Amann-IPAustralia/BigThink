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
| 1 — Signal collection | **Working, five of six sources** | OpenAlex went live on the `2026-08-30` run: 20/20 queries succeeded, but the relevance floor holds it to 8% of the corpus (issue 3). PatentsView still disabled (issue 6). Crossref is returning peer-review records as if they were papers (issue 11) |
| 2 — Emergence detection | **Working** | Kleinberg bursts, logistic growth curves, Rotolo five-attribute score, Three Horizons |
| 3 — Fit and leverage | **Working, weak** | Strategic fit is usable; asset leverage is compressed — see Open issue 2 |
| 4 — Opportunity index | **Working, partial** | `patent_activity` has no data without PatentsView; weight redistributes automatically |
| 5 — Synthesis | **Working** | Shortlist, 2×2 views, evidence cards, CSV, published HTML |
| Notebook export | **Working, not yet reviewed by anyone** | `src/notebook.py`; written automatically after Stage 5. Re-derives emergence, horizon, index and composite rank from stored inputs |
| Automation | **Fully exercised** | `tests.yml` green. `verify-access.yml` — **both credentials pass**. `scan.yml` **first ran 2026-08-30 (run 33310810297) and succeeded end to end**: 66 min collection, 0 failed pairs, site built, outputs committed, first corpus release published, R2 mirror written |
| Tests | **195 passing** | Offline by design; every defect found so far has one |

**Current baseline — `2026-08-30`** (workflow run 33310810297, the first
`scan.yml` execution). Collected from an empty database, because no corpus
release existed to restore: 7,653 records fetched, 7,219 documents after
deduplication, across 2018–2026, with **zero failed or skipped source/frame
pairs** — the first clean sweep on this project. Sources: Crossref 2,432,
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

### 11. Crossref peer-review records are producing phantom topics — NEW 2026-08-30

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

**Not applied yet, deliberately.** It changes what every future run collects, so
it needs a test and a calibration-log entry rather than a quiet edit — and the
run it would change is the baseline everything is now measured against.

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

### 3. OpenAlex — ~~contributes nothing~~ key fixed, but the relevance floor now caps it at 8%

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

**Next:** lower `collection.sources.openalex.min_relative_score` toward 0.2 and
record what it does to corpus size and topic quality. The best research source
available is currently contributing less than data.gov.au and arXiv combined.

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

### 5. GDELT is unreliable from shared IPs — but behaved perfectly on 2026-08-30

It rate-limits by source IP and drops connections mid-response with no error
code — a majority of requests failed even at 6 seconds apart on 2026-08-29. It
still returned 3,183 records over that run, so it works; it just cannot be
relied on for any single frame.

**On the `2026-08-30` run, all 18 GDELT queries succeeded** and returned 2,069
records — no failures at all. That is one Actions runner on one day, not a fix,
and the failure mode should still be expected. But it does mean a run with zero
GDELT failures is achievable, and the 0-failure baseline is not evidence that
anything was skipped. Failures, when they happen, are logged and cost only the
attention component for that frame. Nothing to fix; know it when reading
`collection_log`.

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
| `hashing` embeddings by default | The pipeline must run and be testable with no torch and no model download. `bge` is a config switch, and the intended destination |
| Agglomerative clustering, not BERTopic | BERTopic finds better topics but shifts between runs unless carefully seeded. Week-over-week comparability matters more while weights are unsettled |
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

1. **Fix issue 11 (Crossref record types).** Filter `type == "peer-review"` in
   `src/collectors/crossref.py`, add a test with a peer-review fixture, and
   record it in the calibration log. This is the highest-value change available
   and it removes two artefacts from the top six.
2. **Loosen the OpenAlex relevance floor** (issue 3) —
   `collection.sources.openalex.min_relative_score` from 0.4 toward 0.2 — then
   re-run collection. The best research source is contributing 8%.
3. **Re-sweep the clustering threshold** on the new corpus:
   `python -m src.calibrate threshold --show-labels`. The 0.30 value was
   calibrated against a corpus that no longer exists, and the catch-all now
   holds 54% of everything assigned.
4. `python -m pytest tests/ -q` — expect 195 passing. If not, start there.
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
| GitHub Pages | `docs/` is built by `src.report`; Pages needs enabling in repository settings |
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
