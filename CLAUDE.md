# CLAUDE.md — BigThink

## What this repo is

A computational opportunity-identification pipeline for **IPAVentures**, IP
Australia's innovation lab. It scans published research, patents, policy and
news, detects emerging topics, scores them against IP Australia's strategy and
assets, and produces a ranked shortlist of candidate ventures with evidence
behind every score.

The authoritative design source is
[`SuggestedConceptualApproach.md`](SuggestedConceptualApproach.md) — a desktop
research report. This repo implements Stages 0–5 of its recommendations.

**Read [`PROJECT_STATE.md`](PROJECT_STATE.md) first.** It is the living record
of what is done, what is not, what is uncertain, and what to do next. This file
tells you how the code works; that one tells you where the work is up to.

## Two ways of reading this repo

It is both a **research method** and a **codebase**, and the research half is
the part that is easy to break invisibly:

- A bug makes the pipeline fail loudly. **A bad threshold makes it produce a
  confident, wrong shortlist that nobody can tell is wrong.**
- So: anything that changes a number must be justified, tested, and recorded in
  `PROJECT_STATE.md`'s calibration log. A weight change with no recorded reason
  is indistinguishable from a bug six months later.

## Commands

All from the repo root. Python 3.11+.

The default config is `bge` + `bertopic`, which needs `requirements-ml.txt`
(and torch from the CPU-only index). Without it, add
`--embedding-backend hashing --clustering-method agglomerative` to any command
below, or edit the config — everything still runs, just with lexical vectors.

```bash
# Full pipeline. The run ID now defaults to the UTC date AND time to the
# minute, so two runs on one day cannot overwrite each other's outputs.
python -m src.pipeline

# Re-analyse without re-collecting — the fast loop for tuning
python -m src.pipeline --run-id RUN --skip-collect

# Small end-to-end run (caps records per query)
python -m src.pipeline --run-id dev --sample

# Cheap pairing, for a machine with no torch. Recorded in the config snapshot,
# so a run always says which pairing produced it.
python -m src.pipeline --run-id dev --sample \
    --embedding-backend hashing --clustering-method agglomerative

# Individual stages — each reads from DuckDB, so they run independently
python -m src.stage0_strategy --show            # print the reference set, no writes
python -m src.stage0_strategy --run-id RUN
python -m src.stage1_collect  --run-id RUN [--sources crossref,arxiv] [--frames ct_quantum] [--sample]
python -m src.stage2_emergence --run-id RUN [--top 25]
python -m src.stage3_scoring   --run-id RUN     # prints only; does not persist
python -m src.stage4_opportunity_index --run-id RUN
python -m src.stage5_synthesis --run-id RUN     # runs 3 and 4, then persists all
python -m src.report           --run-id RUN     # build docs/index.html
python -m src.dashboard        --run-id RUN     # build docs/dashboard.html (point-cloud explorer)
python -m src.notebook         --run-id RUN     # peer-review .ipynb for that run

# Calibration. Nothing here writes; all of it reads the accumulated corpus.
python -m src.calibrate threshold --show-labels   # sweep the clustering threshold
python -m src.calibrate bertopic --show-labels    # sweep BERTopic seeds and n_neighbors
python -m src.calibrate attachment                # where the attachment threshold belongs
python -m src.calibrate attributes                # attribute ranges and influence

# Tests — offline by design, no network calls. BERTopic tests run offline too
# (they are handed embeddings, so no model loads); they skip without
# requirements-ml.txt and run in the `ml` job in CI.
python -m pytest tests/ -q

# Are the optional credentials actually live? Hits OpenAlex and R2 for real.
python -m src.verify_access
```

Both optional integrations fail *soft* — OpenAlex retires itself when its
budget is spent, and the R2 mirror step swallows its own errors — which is
right for a scheduled scan and useless for confirming a setup, because a
missing key and a working one produce the same quiet log line.
`src/verify_access.py` is the loud counterpart. In CI, the **Verify
credentials** workflow is the only thing that can read the repository
secrets.

## Architecture

```
Stage 0  stage0_strategy.py           Strategy → reference vectors + lexicons
Stage 1  stage1_collect.py            APIs → DuckDB, STEEPV-tagged, deduplicated
           collectors/                One module per source
Stage 2  stage2_emergence.py          Topics → bursts → growth curves → Rotolo score
           topics.py, burst.py        Clustering and Kleinberg detection
Stage 3  stage3_scoring.py            Strategic fit × asset leverage
Stage 4  stage4_opportunity_index.py  Relative composite index
Stage 5  stage5_synthesis.py          Ranking, evidence cards, outputs
         report.py                    GitHub Pages site (ranked shortlist)
         dashboard.py                 GitHub Pages site (interactive point-cloud explorer)
         notebook.py                  Peer-review .ipynb (reads a run back out)
```

`notebook.py` is the odd one out and deliberately so: it is the only module
that reads a *finished* run and writes nothing to DuckDB. It re-derives four
stored numbers from their stored inputs — emergence score, horizon band,
opportunity index, composite rank — so a reviewer can check the arithmetic
instead of trusting it. Two rules keep it honest, and both are load-bearing:

- **It verifies against `pipeline_runs.config_snapshot`, never against
  `bigthink_config.yaml`.** Otherwise editing a weight makes every past
  notebook silently re-verify against arithmetic that never happened.
- **It calls the production functions** (`composite_scores`, `assign_horizon`,
  `percentile_rank`) rather than reimplementing them, so a check cannot drift
  away from the code it checks.

**Stages communicate only through DuckDB.** No stage passes Python objects to
another. That is what makes a stage re-runnable alone and a months-old result
explainable — `pipeline_runs` stores the config snapshot that produced it.
Preserve this. Do not add cross-stage function calls that bypass the database.

## Key design decisions — do not undo these without reading why

| Decision | Why |
|---|---|
| **DuckDB, not SQLite** | Every stage aggregates. `BasicInfraSuggestion.md` specifies it. Single-writer — hence the Actions concurrency group |
| **Pluggable embeddings, `bge` default since 2026-08-31** | `hashing` matches vocabulary, not meaning, so a topic's terms and an asset description shared few exact tokens and the asset-leverage axis spanned only 0.03-0.10 across the whole shortlist. `hashing` stays fully supported and is what the tests and any torch-less machine run on — the pipeline must remain runnable with no torch and no model download |
| **Per-method AND per-backend similarity thresholds** | The backend sets a cosine's *scale* (hashed TF-IDF puts a related pair around 0.28; BGE above 0.8). The method sets what the cosine is *between* — `leader` compares to a centroid, `agglomerative` to a mean pairwise similarity, which is far lower on identical data. At the leader value of 0.30, average linkage assigned 23 of 2,987 documents. `topic_similarity_threshold()` is the only place this is resolved |
| **True average-linkage clustering, not leader clustering** | The pre-2026-08-30 method updated a centroid in place as it accreted, so a growing cluster drifted toward the corpus mean and then resembled everything. It produced a catch-all holding 57% of everything assigned, whose centroid sat at cosine 0.955 from the corpus centroid. Average linkage makes a cluster *harder* to join as it grows, and is order-invariant, so the chronological ordering of the corpus cannot bias which topics form |
| **BERTopic by default since 2026-08-31, over seeded UMAP + HDBSCAN** | Reverses the earlier call, on the owner's instruction: a single scan should be as accurate on its own as it can be, and its value as a reference point for a later run is explicitly secondary. Every argument for average linkage was an argument about the second thing. Within a corpus it is still deterministic — UMAP is seeded from `emergence.topics.bertopic.random_state` — so a result stays checkable; across corpora it moves more than average linkage, and that is the accepted cost |
| **Every BERTopic hyperparameter is in the config, none left to a library default** | A default is a hyperparameter nobody wrote down. `BertopicParams` holds all of them, `describe()` puts them in the run log, and the config snapshot carries them with the run. The seed especially: unseeded UMAP makes two runs of one corpus disagree about what the topics are, so `config.py` refuses a non-integer `random_state` rather than letting an unreproducible run start |
| **Direct logistic fit, not logit linearisation** | Linearisation reports an early-exponential topic as *saturated* — inverting the Three Horizons band for exactly the technologies a horizon scan exists to find. Guarded by `test_early_exponential_growth_is_horizon_3` |
| **Crossref offset paging, not cursor** | Cursor paging silently discards relevance ordering (top scores fell 31.7 → 7.4 in testing). Do not "fix" this back to a cursor |
| **A long `Retry-After` escalates to permanent** | OpenAlex sends ~62,000 s when its daily budget is spent. Retrying inside a run cannot succeed; it burns the job timeout. The source is retired for the run |
| **A relevance floor is anchored on rank 10, not the maximum** | OpenAlex relevance blends text match with citation count, so a query naming a well-known field returns one enormous score and a normal tail. Anchoring on that maximum made a frame's yield a function of how much of an outlier its top hit was — 3 records for `ct_quantum`, 110 for `ct_biotech`, on comparable literature. Crossref stays on rank 1, where its gentler decay makes the floor near-inert |
| **A collector that handles its own error records an incident** | `collect` is a generator drained with `list()`, so raising after the first yield discards the documents already produced. A partial window is worth keeping. But an unrecorded failure is how four GDELT frames were logged `success` with zero records, and the run reported a clean sweep it did not have |
| **GDELT's window is split into date-range chunks** | `timespan` does not do what it says: `artlist` sorts most-recent-first and `maxrecords` caps at 250, so one request returns the newest 250 articles however wide the window. Every GDELT document on the 2026-08-30 run carried a 2026 date |
| **Impact percentiles computed within source** | arXiv reports no citations. Ranked globally, every preprint sits at the bottom and the fastest-moving evidence is systematically penalised |
| **Opportunity index excluded from the ranking** | It is the weakest-founded number here. Folding it into the headline order would launder that weakness |
| **Thin topics suppressed, not scored** | A composite on 8 documents looks identical to one on 800. That is how a horizon scan misleads people |
| **Weight redistribution when a component has no data** | Otherwise disabling PatentsView silently shrinks every index by 15% and the ranking looks unchanged while measuring something different |
| **The notebook explains a run, it does not re-run one** | Stages 2–5 are deterministic given the corpus, so re-deriving them proves something. Stage 1 hits live metered APIs, so a cell claiming to reproduce the corpus would be false the moment a source changed its budget |
| **The notebook is executed at generation time, in-process** | It must read correctly without being run and stay runnable. An `.ipynb` is only JSON; `nbformat`/`nbclient` would add dependencies and buy nothing |
| **The dashboard re-embeds the corpus rather than reading stored vectors** | The `hashing` backend never persists vectors — cheap enough that caching them would cost more in database writes than it saves (see `src/embeddings.py`). Re-embedding with the same recipe Stage 2 uses is deterministic and, for `hashing`, milliseconds; for `bge` it reads the cache Stage 2 already wrote. Topic membership itself is not recomputed — it comes straight from `topic_documents` |
| **UMAP with a numpy-only PCA fallback, not a hard `umap-learn` requirement** | Same graceful-degradation shape as the `bge` embedding backend: the dashboard must still render a real map if the optional dependency is missing, just a flatter one |

## Configuration

`bigthink_config.yaml` is the single source of truth, validated at the start of
every run. **Nothing that changes a result belongs in a `.py` file.** If you
find yourself editing a threshold in code, it belongs in the config.

Validation is deliberately strict — the Rotolo weights, the opportunity-index
components and the rank weights are all convex combinations, and a set that
does not sum to 1.0 silently rescales every score in the output.

Four YAML files under `data/strategy/` decide what the scan can find and what
it means. They are the reviewable research artefacts; a colleague should be
able to critique them without reading Python:

- `scan_frame.yaml` — **what is searched for.** The biggest determinant of the
  output. The scan cannot find what this file does not ask for
- `objectives.yaml` — the 2026-27 Corporate Plan objectives and initiatives
- `asset_inventory.yaml` — what IP Australia would bring to an opportunity
- `critical_technologies.yaml` — DISR national-interest fields

## Environment variables

Neither is required; the pipeline runs on Crossref, arXiv, GDELT and
data.gov.au with no keys at all.

| Variable | Effect if unset |
|---|---|
| `OPENALEX_API_KEY` | OpenAlex is retired at the first frame on a shared IP (metered, daily budget). **Recommended** — it is the best research source |
| `PATENTSVIEW_API_KEY` | PatentsView stays disabled; the `patent_activity` index component has no data and its weight is redistributed |
| `BIGTHINK_CONTACT_EMAIL` | Falls back to `pipeline.contact_email` in the config. Used for OpenAlex/Crossref polite pools |
| `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY` | Required together only when `storage.r2.enabled` is true, where a missing one raises rather than skipping — a run that believes it is persisting a corpus and is not would be worse than one that never tried |

## Conventions

- **No async.** Synchronous throughout, one source at a time, predictable
  resource use. Delays here are deliberate rate limiting, not a performance
  problem to solve.
- **No web frameworks, no ORM.** DuckDB and the standard library.
- **Collectors raise, never swallow.** `RetryableError` for 429/5xx and
  timeouts; `PermanentError` for anything a retry cannot fix. A collector that
  returns an empty list on failure produces a silent scan.
- **`native_id` must be stable across runs.** Deduplication depends on it.
- **Tests never touch the network.** CI must not depend on a third-party API
  being up. Use fixtures.
- **Comments explain why, not what.** The non-obvious decisions above are all
  documented at their call site; keep it that way.

## When adding things

- **A new scan query** → edit `data/strategy/scan_frame.yaml`. No code. But it
  changes the corpus, so results before and after are not comparable — record
  it in `PROJECT_STATE.md`.
- **A new source** → see `docs/runbook-add-source.md`. Register in three
  places: `collectors/__init__.py`, `_KNOWN_SOURCES` in `config.py`, and the
  config. If it is research/attention/patent evidence, also add it to the
  matching set in `stage4_opportunity_index.py`.
- **A new score or weight** → `bigthink_config.yaml`, with validation in
  `config.py`, a test, and an entry in the calibration log.
- **A new stored number worth reviewing** → also re-derive it in
  `notebook.py`. A number nobody can check is a number nobody should believe,
  and the check must recompute it from stored inputs rather than read it twice.

## Documentation map

| File | Purpose |
|---|---|
| `PROJECT_STATE.md` | **Start here.** Live state, open decisions, next actions, calibration log |
| `docs/method.md` | What every number means and does not mean |
| `docs/runbook-calibration.md` | Tuning weights, and the validation test that matters |
| `docs/runbook-add-source.md` | Adding a query or a source |
| `docs/runbook-failure-response.md` | When a run fails |
| `SuggestedConceptualApproach.md` | The original research report this implements |
| `BasicInfraSuggestion.md` | The zero-budget infrastructure pattern |

## Things to be careful about

- **Do not present the opportunity index as a market size.** It is a relative,
  within-run ordering. This is the most important caveat in the whole method
  and the easiest one to lose in a slide.
- **Do not compare scores across runs** unless the config snapshot and corpus
  are the same. Check `pipeline_runs.config_snapshot`.
- **Do not trust the ranking yet.** No weight here has been validated against a
  known past opportunity. Until that test is run (see
  `docs/runbook-calibration.md`, Step 1), the ranking is a hypothesis.
- **Do not skip the evidence cards.** Reading the primary documents behind a
  topic is the cheapest and most reliable quality control in the method. Some
  topics will be clustering artefacts; only reading finds them.
