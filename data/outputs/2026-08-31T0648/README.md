# `2026-08-31T0648` — a run recovered from a run-id collision

This directory was `data/outputs/2026-08-31/` until 2026-08-31. It was not
supposed to be: it is the **second** run to resolve to that id on that UTC day,
and committing it overwrote the first in place — the 15,036-document,
120-topic run at 03:25 UTC that `PROJECT_STATE.md` calls the current baseline
and that every figure in that file refers to.

That is issue 18 happening a second time, on the baseline itself, and issue 23
records it. The run below is a `--skip-collect` re-analysis of the restored
`corpus-20260831-064857` release, not a collection run.

| | this run | the baseline it overwrote |
|---|---|---|
| generated | 2026-08-31 06:48 UTC | 2026-08-31 03:25 UTC |
| documents | 7,501 | 15,036 |
| topics | 102 | 120 |
| backend / method | hashing / agglomerative | hashing / agglomerative |
| commit | `acdb96b` | `2cdeefd` |

`data/outputs/2026-08-31/` now holds the 03:25 run again. This one is kept
under the id it would have been given had `default_run_id()` been
minute-resolution at the time — which it now is, so this cannot recur.

**Nothing here is wrong**, and none of it is comparable with the baseline: the
two ran over different corpora, and every headline score in this pipeline is
percentile-ranked within its own run.
