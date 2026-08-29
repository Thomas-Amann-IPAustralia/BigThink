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

# Encode the strategy (fast, no network)
python -m src.stage0_strategy --show

# A small end-to-end run
python -m src.pipeline --run-id dev --sample

# Read the results
open data/outputs/dev/shortlist.md
```

A full run:

```bash
python -m src.pipeline --run-id $(date -u +%F)
python -m src.report   --run-id $(date -u +%F)
```

Re-analyse without re-collecting — the fast loop for tuning:

```bash
python -m src.pipeline --run-id $(date -u +%F) --skip-collect
```

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

## Repository layout

```
src/
  pipeline.py                  End-to-end orchestrator
  config.py                    Config loading and validation
  errors.py  retry.py          Error hierarchy and exponential backoff
  db.py                        DuckDB schema and I/O
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
data/
  strategy/                    The four YAML inputs above (committed)
  raw/                         Raw API payloads (gitignored)
  outputs/<run_id>/            Shortlist, evidence cards, topics.csv (committed)
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
