# Runbook — when a run fails

## First: what actually happened?

Every stage records its outcome. Start here, not in the logs.

```bash
python - <<'PY'
from src import db
from src.config import load_config, resolve_path
with db.get_connection(resolve_path(load_config(), "storage", "duckdb_path")) as c:
    for r in c.execute("""
        SELECT run_id, stage, status, records_in, records_out, message
        FROM pipeline_runs ORDER BY id DESC LIMIT 15""").fetchall():
        print(r)
PY
```

A stage marked `partial` succeeded with holes. A stage marked `failed` has its
reason in `message`.

---

## "Could not set lock on file ... Conflicting lock is held"

DuckDB allows one writing process at a time and takes an exclusive file lock.
Two runs do not corrupt the database — they fail to open it.

```bash
pgrep -af "src.stage1[_]collect|src.pipeline"     # find the holder
```

Wait for it, or kill it. In GitHub Actions this is prevented by the
`bigthink-pipeline` concurrency group; if you see it there, two workflows are
missing that group.

---

## A source collected nothing

Look at `collection_log` before assuming the source is broken:

```sql
SELECT source, status, count(*) AS queries, sum(records) AS records, max(message)
FROM collection_log WHERE run_id = '2026-08-29'
GROUP BY source, status ORDER BY source;
```

| Status | Meaning |
|---|---|
| `success` with 0 records | The query genuinely matched nothing. Check the query |
| `skipped` | The source was retired this run — read `message` |
| `failed` | Transient failure that outlived its retries |

### OpenAlex: "Insufficient budget"

Expected without a key. OpenAlex is metered per dollar with a small daily
allowance per IP, reset at midnight UTC, and a shared runner IP has usually
spent it. The collector escalates this to permanent — a `Retry-After` of ~17
hours is a quota, not a transient failure — and retires the source for the run.

**Fix:** get a free key at <https://openalex.org>, set `OPENALEX_API_KEY`
locally and as a repository secret.

### GDELT: connections reset

Expected, intermittently. GDELT rate-limits by IP and drops connections with no
error code rather than returning 429. From a shared or cloud IP a large share of
requests fail this way even at six seconds apart.

Nothing to fix. It costs the attention component of the opportunity index for
the affected frames; the weight is redistributed automatically. If GDELT fails
persistently across a whole run, note it — the index is then measuring less than
the config describes, and any briefing should say so.

### PatentsView: missing credential

Expected. It is disabled by default. Get a key at
<https://patentsview.org/apis/keyrequest>, set `PATENTSVIEW_API_KEY`, then
enable it in `bigthink_config.yaml`.

---

## "insufficient input data"

| Message | Cause | Fix |
|---|---|---|
| "N documents collected; need at least M" | Stage 1 collected too little | Run Stage 1; check `collection_log` |
| "corpus spans N time slice(s)" | Everything landed in one year | Widen `collection.start_year`/`end_year` |
| "clustering produced no topics" | Similarity threshold too high for this backend | Lower it for the **active** backend |
| "no strategy references found" | Stage 0 never ran | `python -m src.stage0_strategy` |
| "no topics for run_id=..." | Stage 2 never ran for this run id | Check the run id — they must match across stages |

---

## Results changed and I do not know why

Every run stores the config that produced it:

```sql
SELECT run_id, stage, config_snapshot FROM pipeline_runs WHERE stage = 'stage2_emergence';
```

Diff two snapshots. If they match, the corpus changed — compare document counts
per source between the runs. If they differ, a threshold moved, and the change
should be in `PROJECT_STATE.md`'s calibration log.

The notebook does this diff for you against the current config, near the top:
open `data/outputs/<run_id>/horizon-scan-<run_id>.ipynb` and read the
"Has the repository moved since this run?" cell.

---

## The notebook reports FAIL, or a cell has a traceback in it

A `FAIL` line means a number in the database cannot be reproduced from the
inputs stored beside it. That is a real finding, not a display problem — treat
it as a bug in the stage that wrote the number, or as evidence the row was
written by a different version of the code than the snapshot claims.

The one expected near-miss is `opportunity_index`, which is checked at `1e-3`
rather than machine precision: Stage 4 stores its components and weights
rounded to 4 dp while computing the index from the unrounded values. A
deviation around `1e-4` there is the rounding, not an error. A deviation
anywhere else should be zero.

An embedded traceback means that cell raised during generation. The export does
not abort on one — a notebook that shows where it broke is more useful than no
notebook — so the run itself is unaffected and the rest of the document is
still valid. Reproduce it with:

```bash
python -m src.notebook --run-id 2026-08-29 --log-level DEBUG
```

If the whole export failed, the pipeline logs it at ERROR and carries on; the
scan's own outputs are already written.

---

## Recovering a corpus

The database is not in git — it is published as a `corpus-*` GitHub Release
asset by the scan workflow.

```bash
gh release list --limit 20 | grep corpus-
gh release download corpus-YYYYMMDD-HHMMSS --pattern 'bigthink.duckdb' --dir data --clobber
```

If no release exists, re-run collection. Raw payloads under `data/raw/` can
rebuild a corpus without re-fetching, but they are gitignored, so they only
survive locally.

---

## Rerunning a single stage

Stages are independent and read from DuckDB:

```bash
python -m src.stage0_strategy --run-id 2026-08-29          # cheap, safe anytime
python -m src.stage1_collect  --run-id 2026-08-29 --sources crossref
python -m src.stage2_emergence --run-id 2026-08-29
python -m src.stage5_synthesis --run-id 2026-08-29         # also runs 3 and 4
python -m src.report          --run-id 2026-08-29
python -m src.notebook        --run-id 2026-08-29          # read-only; safe anytime
```

Re-running Stage 3/4/5 never touches Stage 2 output, so scoring can be re-done
freely without re-detecting emergence.
