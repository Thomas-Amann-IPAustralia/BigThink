"""
src/stage1_collect.py — Stage 1: Signal collection.

Walks the scan frame (data/strategy/scan_frame.yaml) and, for every
(frame, source) pair with a query, runs the matching collector and writes
normalised documents into DuckDB.

Design commitments:

  * One source failing must not end the run. Each (frame, source) pair is
    isolated; failures are recorded in `collection_log` with a reason and the
    scan continues. A partial scan you can see the holes in beats no scan.

  * Deduplication is by doc_id, so the same paper found under three seed
    queries is stored once. The frame key of the first sighting is kept.
    That means per-frame counts undercount overlap — read `collection_log`
    for reach, `documents` for corpus size.

  * Raw payloads are written under data/raw/<run_id>/<source>/ so any result
    can be re-derived without re-fetching. Free APIs change schemas without
    notice; this is the only defence.

Run:
    python -m src.stage1_collect --run-id 2026-08-29
    python -m src.stage1_collect --sources arxiv,crossref --frames ct_quantum
    python -m src.stage1_collect --sample          # 50 records per query
"""

from __future__ import annotations

import argparse
import logging
import sys
import time
from typing import Any, Iterable

import yaml

from src import db
# Importing the package registers every collector; get_collector() then resolves
# a source name to its class.
from src.collectors import get_collector
from src.config import get, load_config, resolve_path, snapshot_config
from src.errors import BigThinkError, ConfigError, PermanentError

logger = logging.getLogger(__name__)

STAGE = "stage1_collect"


# ---------------------------------------------------------------------------
# Scan frame
# ---------------------------------------------------------------------------


def load_scan_frame(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Load and validate the scan frame."""
    path = resolve_path(config, "collection", "scan_frame_file")
    if not path.exists():
        raise ConfigError(f"Scan frame not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh) or {}

    frames = data.get("frames") or []
    if not frames:
        raise ConfigError(f"{path} defines no frames — there is nothing to scan for.")

    from src.config import STEEPV_CATEGORIES

    seen: set[str] = set()
    for frame in frames:
        key = frame.get("key")
        if not key:
            raise ConfigError(f"A frame in {path.name} has no 'key'.")
        if key in seen:
            raise ConfigError(f"Duplicate frame key in {path.name}: {key!r}")
        seen.add(key)
        if frame.get("steepv") not in STEEPV_CATEGORIES:
            raise ConfigError(
                f"Frame {key!r} has steepv={frame.get('steepv')!r}, which is not a "
                f"STEEPV category {sorted(STEEPV_CATEGORIES)}"
            )
        if not (frame.get("queries") or {}):
            raise ConfigError(f"Frame {key!r} defines no queries — it can never match anything.")
    return frames


def enabled_sources(config: dict[str, Any]) -> list[str]:
    sources = get(config, "collection", "sources", default={}) or {}
    return sorted(name for name, s in sources.items() if isinstance(s, dict) and s.get("enabled"))


# ---------------------------------------------------------------------------
# Collection
# ---------------------------------------------------------------------------


def run(
    config: dict[str, Any],
    run_id: str,
    *,
    only_sources: Iterable[str] | None = None,
    only_frames: Iterable[str] | None = None,
) -> dict[str, int]:
    """Execute Stage 1. Returns {'fetched': n, 'new': n, 'failed_pairs': n}."""
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    entry_id = db.log_stage_start(conn, run_id, STAGE, snapshot_config(config))

    frames = load_scan_frame(config)
    if only_frames:
        wanted = set(only_frames)
        frames = [f for f in frames if f["key"] in wanted]
        if not frames:
            raise ConfigError(f"No frames matched {sorted(wanted)}")

    sources = enabled_sources(config)
    if only_sources:
        sources = [s for s in sources if s in set(only_sources)]
        if not sources:
            raise ConfigError(
                f"None of {sorted(set(only_sources))} are enabled in collection.sources"
            )

    start_year = int(get(config, "collection", "start_year", default=2018))
    end_year = int(get(config, "collection", "end_year", default=2026))

    logger.info(
        "Stage 1 starting: %d frames x %d sources, window %d-%d, run_id=%s",
        len(frames), len(sources), start_year, end_year, run_id,
    )

    # One collector instance per source, reused across frames so the HTTP
    # session (and its connection pool and throttle state) is shared.
    collectors: dict[str, Any] = {}
    for source in sources:
        try:
            collectors[source] = get_collector(source)(config, run_id)
        except Exception as exc:
            logger.error("Could not construct collector %s: %s", source, exc)
            db.log_collection(conn, run_id, source, "*", "failed", 0, f"init: {exc}")

    totals = {"fetched": 0, "new": 0, "failed_pairs": 0, "partial_pairs": 0}
    started = time.monotonic()

    # Circuit breaker. A PermanentError is by definition not frame-specific —
    # a missing API key or an exhausted daily quota fails identically for every
    # remaining frame. Without this, an exhausted OpenAlex budget costs one
    # failed request per frame; across 20 frames that is most of a scan's
    # runtime spent learning the same fact twenty times.
    retired: dict[str, str] = {}

    for frame in frames:
        frame_key = str(frame["key"])
        for source, collector in collectors.items():
            query = (frame.get("queries") or {}).get(source)
            if not query:
                continue

            if source in retired:
                db.log_collection(
                    conn, run_id, source, frame_key, "skipped", 0,
                    f"source retired earlier this run: {retired[source]}",
                )
                totals["failed_pairs"] += 1
                continue

            collector.begin_frame()
            try:
                docs = list(collector.collect(query, frame, start_year, end_year))
            except PermanentError as exc:
                # Missing credentials, 403, exhausted quota, malformed schema:
                # this source cannot succeed again this run. Retire it and keep
                # scanning with the others.
                logger.warning(
                    "[%s/%s] retiring source for this run: %s", frame_key, source, exc
                )
                retired[source] = str(exc)[:200]
                db.log_collection(conn, run_id, source, frame_key, "skipped", 0, str(exc))
                totals["failed_pairs"] += 1
                continue
            except BigThinkError as exc:
                logger.warning("[%s/%s] failed: %s", frame_key, source, exc)
                db.log_collection(conn, run_id, source, frame_key, "failed", 0, str(exc))
                totals["failed_pairs"] += 1
                continue
            except Exception as exc:  # unexpected: log loudly, keep scanning
                logger.exception("[%s/%s] unexpected error", frame_key, source)
                db.log_collection(
                    conn, run_id, source, frame_key, "failed", 0, f"{type(exc).__name__}: {exc}"
                )
                totals["failed_pairs"] += 1
                continue

            new = db.upsert_documents(conn, docs)
            totals["fetched"] += len(docs)
            totals["new"] += new

            # A collector that handled its own failure returns normally, so the
            # absence of an exception is not evidence the query succeeded. Ask
            # the collector what went wrong before recording a status: without
            # this, a frame that fetched nothing because the API dropped every
            # connection is written to `collection_log` as `success` with zero
            # records, and the run reports a clean sweep it did not have.
            incidents = collector.incidents
            if not incidents:
                status = "success"
            elif docs:
                status = "partial"
                totals["partial_pairs"] += 1
            else:
                status = "failed"
                totals["failed_pairs"] += 1

            db.log_collection(
                conn, run_id, source, frame_key, status, len(docs),
                "; ".join(incidents),
            )
            log = logger.info if status == "success" else logger.warning
            log(
                "[%s/%s] %s: %d fetched, %d new (corpus %d)%s",
                frame_key, source, status, len(docs), new, db.count_documents(conn),
                f" — {'; '.join(incidents)}" if incidents else "",
            )

    elapsed = time.monotonic() - started
    for source, reason in retired.items():
        logger.warning("Source retired during this run — %s: %s", source, reason)
    status = (
        "success"
        if totals["failed_pairs"] == 0 and totals["partial_pairs"] == 0
        else "partial"
    )
    message = (
        f"{totals['fetched']} fetched, {totals['new']} new, "
        f"{totals['failed_pairs']} failed/skipped pairs, "
        f"{totals['partial_pairs']} partial in {elapsed:.0f}s"
    )
    db.log_stage_finish(
        conn, entry_id, status,
        records_in=totals["fetched"], records_out=totals["new"], message=message,
    )
    logger.info("Stage 1 complete (%s): %s", status, message)

    for row in db.collection_summary(conn, run_id):
        logger.info(
            "  %-12s %-8s %3d queries, %6s records",
            row["source"], row["status"], row["queries"], row["records"] or 0,
        )
    conn.close()
    return totals


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 1 — collect signals.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", required=True, help="e.g. 2026-08-29 or 2026-08-29-quantum")
    parser.add_argument("--sources", default="", help="Comma-separated subset of enabled sources.")
    parser.add_argument("--frames", default="", help="Comma-separated subset of scan-frame keys.")
    parser.add_argument("--sample", action="store_true", help="Cap records per query (dev runs).")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    config = load_config(args.config)
    if args.sample:
        config["pipeline"]["sample_mode"] = True

    totals = run(
        config,
        args.run_id,
        only_sources=[s.strip() for s in args.sources.split(",") if s.strip()] or None,
        only_frames=[f.strip() for f in args.frames.split(",") if f.strip()] or None,
    )
    # Non-zero exit when nothing at all was collected: a cron job that silently
    # collects zero documents every week is worse than one that fails.
    return 0 if totals["fetched"] > 0 else 1


if __name__ == "__main__":
    sys.exit(main())
