"""
src/pipeline.py — end-to-end orchestrator.

Runs Stages 0-5 in order. Each stage reads its inputs from DuckDB and writes
its outputs back, so any stage can also be run alone (see its own __main__)
and re-run without repeating the ones before it.

    python -m src.pipeline                                       # id from the clock
    python -m src.pipeline --run-id 2026-08-29 --skip-collect   # re-analyse
    python -m src.pipeline --run-id dev --sample                 # small dev run

RUN IDs. An unset `--run-id` resolves to the UTC date and time to the minute
(`2026-08-31T0947`), not the date alone — see `default_run_id` for the run that
was destroyed by the date-only default. An explicit `--run-id` is still taken
verbatim and still overwrites: re-analysing a run with `--skip-collect` is meant
to rewrite that run's outputs in place, and is the fast loop for tuning.

CONCURRENCY. DuckDB permits one writing process at a time and takes an
exclusive file lock. Two overlapping runs do not corrupt the database, they
fail to open it — so the GitHub Actions workflows share a concurrency group,
exactly as Tripwire's do for SQLite.
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from datetime import datetime, timezone

from src.config import get, load_config
from src.errors import BigThinkError

logger = logging.getLogger(__name__)


#: Minute resolution, not day. A date-only default silently collides: the first
#: 2026-08-30 baseline ran at 12:11 UTC, the scheduled weekly run fired at 21:47
#: UTC the same UTC day, resolved to the same `2026-08-30`, and rewrote
#: data/outputs/2026-08-30/ in place — shortlist, evidence cards, notebook,
#: topics.csv and summary.json. Nothing warned; the commit read exactly like the
#: one it replaced and git saw a normal modification. The corpus is accumulated
#: and the outputs are per-run, so the second run is not a correction of the
#: first, it is a different run wearing its name.
#:
#: `T` rather than a space or a colon so the id stays usable everywhere it is
#: already used unquoted: a directory name (including on Windows), a topic_id
#: prefix, a shell argument and a workflow artefact name.
_RUN_ID_FORMAT = "%Y-%m-%dT%H%M"


def default_run_id() -> str:
    """Today's UTC date and time to the minute — e.g. `2026-08-31T0947`."""
    return datetime.now(timezone.utc).strftime(_RUN_ID_FORMAT)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the BigThink pipeline end to end.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", default=None,
                        help="Defaults to the UTC date and time to the minute, "
                             "e.g. 2026-08-31T0947. Minute resolution because two "
                             "runs on one UTC day would otherwise share an id and "
                             "the second would overwrite the first's outputs.")
    parser.add_argument("--skip-collect", action="store_true",
                        help="Reuse the existing corpus; run Stages 0 and 2-5 only.")
    parser.add_argument("--sample", action="store_true",
                        help="Cap records per query (development runs).")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )
    config = load_config(args.config)
    if args.sample:
        config["pipeline"]["sample_mode"] = True
    run_id = args.run_id or default_run_id()

    from src import stage0_strategy, stage1_collect, stage2_emergence, stage5_synthesis

    stages: list[tuple[str, object]] = [
        ("Stage 0 — strategy encoding", lambda: stage0_strategy.run(config, run_id)),
    ]
    if not args.skip_collect:
        stages.append(("Stage 1 — signal collection", lambda: stage1_collect.run(config, run_id)))
    stages += [
        ("Stage 2 — emergence detection", lambda: stage2_emergence.run(config, run_id)),
        # Stage 5 invokes Stages 3 and 4 internally so that topic_scores is
        # written in one transaction rather than left half-populated.
        ("Stages 3-5 — scoring and synthesis", lambda: stage5_synthesis.run(config, run_id)),
    ]

    logger.info("BigThink pipeline starting — run_id=%s", run_id)
    started = time.monotonic()
    for name, stage in stages:
        logger.info("=== %s ===", name)
        stage_start = time.monotonic()
        try:
            stage()
        except BigThinkError as exc:
            logger.error("%s failed: %s", name, exc)
            logger.error(
                "Pipeline stopped. Stage outcomes are in the pipeline_runs table; "
                "re-run this stage alone once the cause is fixed."
            )
            return 1
        logger.info("%s finished in %.0fs", name, time.monotonic() - stage_start)

    # The notebook export is deliberately outside the stage loop. It computes
    # nothing and persists nothing — it reads the finished run back out of
    # DuckDB and explains it — so a failure here means the scan is fine and the
    # write-up is not. Killing a three-hour collection over a report artefact
    # would be the wrong trade. It is logged at ERROR rather than swallowed.
    if get(config, "notebook", "enabled", default=True):
        from src import notebook

        try:
            path = notebook.run(config, run_id)
            logger.info("Notebook export: %s", path)
        except (Exception, SystemExit) as exc:  # noqa: BLE001 - reported, not hidden
            logger.error(
                "Notebook export failed (%s: %s). The run itself is unaffected; "
                "re-run it alone with: python -m src.notebook --run-id %s",
                type(exc).__name__, exc, run_id,
            )

    logger.info(
        "Pipeline complete in %.0fs — outputs in data/outputs/%s/",
        time.monotonic() - started, run_id,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
