# BigThink

A computational opportunity-identification pipeline for **IPAVentures**, the
innovation lab at IP Australia.

It turns four inputs — published strategy, IP Australia's own assets, emerging
technology and research trends, and attention signals — into a ranked shortlist
of candidate ventures, with the evidence behind every score.

It operationalises the approach in [`SuggestedConceptualApproach.md`](SuggestedConceptualApproach.md):
Three Horizons for time-phasing, STEEPV for the scan taxonomy, Rotolo et al.'s
five attributes of emergence as the quantitative backbone, Kleinberg burst
detection for temporal signal, and a strategic-fit × asset-leverage 2×2 for
ranking — as code that runs unattended, rather than as a workshop.

> **These outputs are candidates, not conclusions.** The pipeline surfaces
> things worth a human looking at. It does not decide anything, its ranking
> weights have not yet been validated against a known past opportunity, and its
> opportunity index is a relative ordering, never a market size. See
> [`docs/method.md`](docs/method.md) for what each number does and does not mean.

---

## The pipeline

```
Stage 0  Strategy encoding    Corporate Plan + DISR critical tech + asset inventory
                              → reference vectors and lexicons
Stage 1  Signal collection    OpenAlex · Crossref · arXiv · GDELT · data.gov.au
                              · PatentsView → DuckDB, STEEPV-tagged, deduplicated
Stage 2  Emergence detection  Topics → Kleinberg bursts → logistic growth curves
                              → Rotolo five-attribute score → Three Horizons
Stage 3  Fit and leverage     Strategic fit × asset leverage, per topic
Stage 4  Opportunity index    Relative composite index (NOT a market size)
Stage 5  Synthesis            Ranked shortlist, 2×2 views, evidence cards, site
```

Each stage reads from DuckDB and writes back to it, so any stage can be re-run
alone and a months-old result can still be explained — the database stores the
config snapshot that produced it.

---

## Quick start

```bash
git clone https://github.com/Thomas-Amann-IPAustralia/BigThink.git
cd BigThink
python3.11 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# The shipped config embeds with BGE and clusters with BERTopic, both of which
# need the optional ML stack (~2 GB installed, ~400 MB model on first run):
pip install torch --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements-ml.txt

# Encode the strategy (fast, no network)
python -m src.stage0_strategy --show

# A small end-to-end run
python -m src.pipeline --run-id dev --sample

# Read the results
open data/outputs/dev/shortlist.md

# Or read how they were arrived at — the same run, walked stage by stage,
# with every number re-derived from its stored inputs
jupyter lab data/outputs/dev/horizon-scan-dev.ipynb
```

Skipping the ML stack is supported — the pipeline falls back to hashed TF-IDF
vectors and average-linkage clustering, which run anywhere with no model
download. The pairing used is recorded in the run's config snapshot either way:

```bash
python -m src.pipeline --run-id dev --sample \
    --embedding-backend hashing --clustering-method agglomerative
```

A full run. The run ID defaults to the UTC date **and time to the minute**, so
two runs on one day cannot overwrite each other's outputs:

```bash
python -m src.pipeline
python -m src.report --run-id RUN
```

Re-analyse without re-collecting — the fast loop for tuning:

```bash
python -m src.pipeline --run-id RUN --skip-collect
```

### Running it on GitHub Actions

The **Horizon scan** workflow can be started by hand from the repository's
Actions tab (Actions → Horizon scan → Run workflow). Every input is optional:

| Input | Use it for |
|---|---|
| `run_id` | Naming a run. Blank gives the UTC date and time to the minute |
| `skip_collect` | Re-analysing the accumulated corpus without re-collecting |
| `sample` | A fast smoke test that caps records per query |
| `fresh_baseline` | Starting from an empty database after a collector change |
| `embedding_backend` | `hashing` for a cheap run; blank/`config` uses BGE |
| `clustering_method` | `agglomerative` for a cheap run; blank/`config` uses BERTopic |
| `log_level` | `DEBUG` when a run needs explaining |

A scheduled run fires weekly at 19:00 UTC Sunday. Runs are serialised through a
concurrency group — DuckDB takes an exclusive lock, so two at once fail to open
the database rather than corrupting it — and a manual run will queue behind a
scheduled one rather than cancelling it.

---

## Configuration

Everything tunable lives in [`bigthink_config.yaml`](bigthink_config.yaml),
validated at the start of every run. Nothing that changes a result belongs in
code.

Four files decide what the scan can find and what it means. They are plain YAML
and reviewable without reading any Python:

| File | Decides |
|---|---|
| `data/strategy/scan_frame.yaml` | **What is searched for.** The single biggest determinant of the output |
| `data/strategy/objectives.yaml` | What "on strategy" means — from the 2026-27 Corporate Plan |
| `data/strategy/asset_inventory.yaml` | What IP Australia would bring to an opportunity |
| `data/strategy/critical_technologies.yaml` | DISR national-interest technology fields |

---

## API keys

The pipeline runs with no keys at all, on Crossref, arXiv, GDELT and
data.gov.au. Two sources are better with them:

| Variable | Source | Why |
|---|---|---|
| `OPENALEX_API_KEY` | OpenAlex | **Recommended.** OpenAlex is now metered; the keyless daily budget is usually already spent on a shared IP. Free at <https://openalex.org> |
| `PATENTSVIEW_API_KEY` | PatentsView | Optional; adds US patent grants. Free at <https://patentsview.org/apis/keyrequest>. Then enable it in the config |

---

## Cloudflare R2 (optional — durable corpus and raw-payload storage)

`storage.r2.enabled` in `bigthink_config.yaml` is `false` by default; the
pipeline runs fully without it, same as the API keys above. Turn it on when
you want to (a) pull the already-collected corpus onto a laptop and iterate
on Stages 3-5 without re-running collection, or (b) keep raw API payloads
across GitHub Actions runs instead of losing them when the job ends. See
`BasicInfraSuggestion.md` for why R2 rather than something else — free to
10 GB, and unlike most object storage it does not charge for reads, which
matters because Actions re-reads it every run.

**1. Create the bucket** (once, in the Cloudflare dashboard)
- Cloudflare dashboard → **R2 Object Storage** → **Create bucket**. Name it
  anything (e.g. `bigthink-corpus`) — it does not need to be public.
- Note the **Account ID**, shown on the R2 overview page.

**2. Create an API token scoped to that bucket**
- R2 → **Manage API Tokens** → **Create API Token**.
- Permissions: **Object Read & Write**, scoped to the bucket you just made
  (not "Apply to all buckets" — least privilege).
- Save the **Access Key ID** and **Secret Access Key** it shows you once —
  Cloudflare does not show the secret again.

**3. Add the credentials as GitHub Actions secrets** (repo → Settings →
Secrets and variables → Actions → New repository secret)

| Secret | Value |
|---|---|
| `R2_ACCOUNT_ID` | Account ID from step 1 |
| `R2_ACCESS_KEY_ID` | Access Key ID from step 2 |
| `R2_SECRET_ACCESS_KEY` | Secret Access Key from step 2 |

**3a. Check it before trusting it**

```bash
python -m src.verify_access            # both credentials, against the live services
python -m src.verify_access --only r2  # just R2
```

This writes a small object to the bucket, reads it back, compares the bytes and
deletes it — the same PutObject/GetObject path `push_corpus` and `pull_corpus`
take. A read-only check would pass against a token that cannot write, which is
the one thing this bucket exists for. Credential values are never printed.

It reports each operation by name rather than stopping at the first refusal,
because R2 token scopes do not divide neatly into working and not: a token
scoped to one bucket routinely refuses `HeadBucket` while permitting every
object call. Only `PutObject` and `GetObject` decide the verdict. If they are
denied, the check then asks which buckets the token *can* see, which separates
a wrong bucket name from a wrong token scope.

In CI, run the **Verify credentials** workflow (Actions → Verify credentials →
Run workflow). That is the only place the repository secrets can be exercised —
nobody, including a Claude session working on this repo, can read them
otherwise.

**3b. If the bucket has a jurisdiction, say so**

A bucket created under a jurisdiction (EU, FedRAMP) lives on its own endpoint,
`<account>.<jurisdiction>.r2.cloudflarestorage.com`, and is **invisible from
the default one** — every call returns `AccessDenied`, including `ListBuckets`,
which is indistinguishable from a permissions problem by inspection. Set
`storage.r2.jurisdiction` to match.

An API token's resource key names it:
`com.cloudflare.edge.r2.bucket.<account>_<jurisdiction>_<bucket>`, where an
unrestricted bucket reads `_default_`. Check that string first if access is
refused and the permissions look right.

Note `R2_ACCOUNT_ID` is the account ID alone — 32 hex characters, from the R2
overview page. Not the endpoint URL, and not the Access Key ID, which is also
32 hex characters and is the easier of the two mistakes to make. A wrong
account ID shows up as a refused TLS handshake rather than an S3 error, because
Cloudflare rejects the handshake for an account it does not recognise.

**4. Turn it on in config** — edit `bigthink_config.yaml`:

```yaml
storage:
  r2:
    enabled: true
    bucket: "bigthink-corpus"   # must match the bucket name from step 1
```

This is the one line that changes behaviour, so it goes in config, not code,
same as everything else in this file. Commit it.

**5. Use it locally** — export the same three variables in your shell
(`export R2_ACCOUNT_ID=...`, etc. — never commit them), then:

```bash
# Pull the corpus the scheduled scan has already built, instead of collecting
# it yourself — this is the fast path into Stage 3-5 tuning.
python -m src.storage pull-corpus

# Now iterate without touching the network:
python -m src.pipeline --run-id dev --skip-collect

# Push your local corpus back up if you want it as the shared copy
python -m src.storage push-corpus

# Raw payloads for a given run, if storage.keep_raw_payloads is on
python -m src.storage push-raw --run-id 2026-08-29
python -m src.storage pull-raw --run-id 2026-08-29
```

**What `scan.yml` does with this.** The corpus's source of truth stays the
`corpus-*` GitHub Release asset it already restores from and publishes to —
that mechanism needs no setup and this does not replace it. When R2 is
configured, the workflow additionally mirrors the corpus to R2 after
publishing (step "Mirror corpus to Cloudflare R2") so there is one fixed
object to pull from locally instead of hunting through release tags. If R2
is not configured, that step logs a line and does nothing — it cannot fail
the run.

---

## Repository layout

```
src/
  pipeline.py                  End-to-end orchestrator
  config.py                    Config loading and validation
  errors.py  retry.py          Error hierarchy and exponential backoff
  db.py                        DuckDB schema and I/O
  storage.py                   Cloudflare R2 sync (optional; see below)
  embeddings.py                Pluggable embeddings: hashing (default) | BGE
  topics.py                    Topic formation and c-TF-IDF labelling
  burst.py                     Kleinberg two-state burst detection
  stage0_strategy.py           Stage 0
  stage1_collect.py            Stage 1 orchestrator
  collectors/                  One module per source
  stage2_emergence.py          Stage 2
  stage3_scoring.py            Stage 3
  stage4_opportunity_index.py  Stage 4
  stage5_synthesis.py          Stage 5 (invokes 3 and 4)
  report.py                    GitHub Pages site
  notebook.py                  Peer-review notebook for a finished run
data/
  strategy/                    The four YAML inputs above (committed)
  raw/                         Raw API payloads (gitignored)
  outputs/<run_id>/            Shortlist, evidence cards, topics.csv,
                               horizon-scan-<run_id>.ipynb (committed)
  bigthink.duckdb              Everything the pipeline knows (gitignored)
docs/
  method.md                    What every number means
  runbook-add-source.md        Adding a query or a source
  runbook-calibration.md       Tuning the weights, and validating them
  runbook-failure-response.md  When a run fails
tests/                         Offline pytest suite
```

---

## Operations

Two workflows, both zero-cost:

| Workflow | Trigger | Purpose |
|---|---|---|
| `scan.yml` | Weekly (Sun 19:00 UTC) + manual | Full pipeline, commit outputs, publish site |
| `tests.yml` | Push and PR | Test suite plus config and strategy-file validation |

The corpus accumulates across runs via `corpus-*` GitHub Release assets rather
than being committed — the DuckDB file is binary, grows, and does not diff.
Both workflows share the `bigthink-pipeline` concurrency group, because DuckDB
permits one writer at a time.

---

## Reuse from other IPAVentures repositories

This pipeline was built to reuse what already works rather than to start over:

| From | What was reused |
|---|---|
| [Tripwire](https://github.com/Thomas-Amann-IPAustralia/Tripwire) | The `RetryableError`/`PermanentError` split and the retry decorator; single-validated-YAML config; the schema-owning db module with a run log; scheduled-Actions operating pattern with state persisted via releases; runbooks-in-repo; the BGE bi-encoder choice, so vectors are comparable across both systems |
| `BasicInfraSuggestion.md` | Zero-budget infrastructure: Actions for compute, DuckDB for analysis, the repo for versioned results, Pages for publication |
| Octavius, Wiseau, Tripwire | Recorded in the asset inventory as engineering capability the lab can bring to a venture |

---

## Testing

```bash
python -m pytest tests/ -q
```

Offline by design — no test makes a network call, so CI never depends on a
third-party API being up.

---

## Current state

See [`PROJECT_STATE.md`](PROJECT_STATE.md) for what has been built, what is
known to be unfinished, the open calibration decisions, and the day-by-day plan
for the rest of the sprint. Start there before continuing the work.
