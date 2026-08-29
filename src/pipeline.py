"""
src/pipeline.py — end-to-end orchestrator.

Runs Stages 0-5 in order. Each stage reads its inputs from DuckDB and writes
its outputs back, so any stage can also be run alone (see its own __main__)
and re-run without repeating the ones before it.

    python -m src.pipeline --run-id 2026-08-29
    python -m src.pipeline --run-id 2026-08-29 --skip-collect   # re-analyse
    python -m src.pipeline --run-id dev --sample                 # small dev run

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

from src.config import load_config
from src.errors import BigThinkError

logger = logging.getLogger(__name__)


def default_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the BigThink pipeline end to end.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", default=None, help="Defaults to today's UTC date.")
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

    logger.info(
        "Pipeline complete in %.0fs — outputs in data/outputs/%s/",
        time.monotonic() - started, run_id,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
