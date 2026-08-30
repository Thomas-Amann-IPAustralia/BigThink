"""
src/storage.py

Cloudflare R2 sync (BasicInfraSuggestion.md).

R2 is the durable store for the two things that do not belong in a git
history: the accumulated DuckDB corpus and the raw API payloads collectors
write under data/raw/. GitHub Actions runners are ephemeral — anything not
saved elsewhere is gone when the job ends — and the working corpus-carryover
mechanism (corpus-* GitHub Release assets in .github/workflows/scan.yml)
already covers CI. This module adds R2 as a mirror of that corpus for local
use (pull it once, then iterate on Stages 3-5 with --skip-collect instead of
re-collecting) and as the first durable home raw payloads have had.

R2 is S3-compatible, so boto3's S3 client works unmodified against it with a
per-account endpoint URL. See README.md for the one-time Cloudflare setup.

Optional throughout: the pipeline must run with no external storage
configured, exactly like OPENALEX_API_KEY and PATENTSVIEW_API_KEY. Every
public function no-ops (logs and returns) when storage.r2.enabled is false.
If it is true, the three credential environment variables are required —
that combination raises rather than silently skipping, because a run that
believes it is persisting a corpus and is not would be worse than one that
never tried.

Run directly:
    python -m src.storage pull-corpus
    python -m src.storage push-corpus
    python -m src.storage push-raw --run-id 2026-08-29
    python -m src.storage pull-raw --run-id 2026-08-29
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from src.config import get, load_config, resolve_path
from src.errors import ConfigError

logger = logging.getLogger(__name__)

_ENV_ACCOUNT_ID = "R2_ACCOUNT_ID"
_ENV_ACCESS_KEY = "R2_ACCESS_KEY_ID"
_ENV_SECRET_KEY = "R2_SECRET_ACCESS_KEY"
_ENV_JURISDICTION = "R2_JURISDICTION"

# R2 buckets can be pinned to a jurisdiction at creation time, which puts them
# on a different S3 endpoint. A bucket created under one is invisible from the
# default endpoint: every call comes back AccessDenied — including ListBuckets,
# because the token's resources are all in the other jurisdiction — which reads
# exactly like a permissions problem and is not one.
_JURISDICTIONS = ("", "default", "eu", "fedramp")


# ---------------------------------------------------------------------------
# Configuration and client
# ---------------------------------------------------------------------------


def is_enabled(config: dict[str, Any]) -> bool:
    return bool(get(config, "storage", "r2", "enabled", default=False))


def _credentials() -> dict[str, str]:
    missing = [
        name
        for name in (_ENV_ACCOUNT_ID, _ENV_ACCESS_KEY, _ENV_SECRET_KEY)
        if not os.environ.get(name)
    ]
    if missing:
        raise ConfigError(
            f"storage.r2.enabled is true but {', '.join(missing)} "
            f"{'is' if len(missing) == 1 else 'are'} not set. Either export them "
            "or set storage.r2.enabled: false."
        )
    return {
        "account_id": os.environ[_ENV_ACCOUNT_ID],
        "access_key": os.environ[_ENV_ACCESS_KEY],
        "secret_key": os.environ[_ENV_SECRET_KEY],
    }


def jurisdiction(config: dict[str, Any]) -> str:
    """The bucket's R2 jurisdiction, env var winning over config.

    The env var exists so a laptop pointed at a differently-located bucket
    does not need a config edit that would then be committed.
    """
    from_env = os.environ.get(_ENV_JURISDICTION, "").strip()
    if from_env:
        return from_env.lower()
    return str(get(config, "storage", "r2", "jurisdiction", default="") or "").strip().lower()


def endpoint_url(account_id: str, jurisdiction_name: str = "") -> str:
    """The S3 endpoint for an account, in a jurisdiction if the bucket has one.

    Cloudflare names these `<account>.<jurisdiction>.r2.cloudflarestorage.com`;
    the unrestricted default has no segment. The jurisdiction of a bucket shows
    up in an API token's resource key as
    `com.cloudflare.edge.r2.bucket.<account>_<jurisdiction>_<bucket>`, where an
    unrestricted bucket reads `_default_`. That string is the quickest way to
    confirm which endpoint a bucket needs.
    """
    name = (jurisdiction_name or "").strip().lower()
    if name in ("", "default"):
        return f"https://{account_id}.r2.cloudflarestorage.com"
    return f"https://{account_id}.{name}.r2.cloudflarestorage.com"


def get_client(config: dict[str, Any]):
    """Build a boto3 S3 client pointed at the account's R2 endpoint.

    Imports boto3 lazily so the rest of the pipeline never pays for it —
    requirements.txt stays installable in seconds even if this module is
    never touched.
    """
    import boto3

    creds = _credentials()
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url(creds["account_id"], jurisdiction(config)),
        aws_access_key_id=creds["access_key"],
        aws_secret_access_key=creds["secret_key"],
        region_name="auto",
    )


def _bucket(config: dict[str, Any]) -> str:
    bucket = str(get(config, "storage", "r2", "bucket", default="") or "")
    if not bucket:
        raise ConfigError("storage.r2.bucket must be set when storage.r2.enabled is true.")
    return bucket


# ---------------------------------------------------------------------------
# Corpus (the DuckDB file)
# ---------------------------------------------------------------------------


def push_corpus(config: dict[str, Any], db_path: str | Path | None = None, client=None) -> bool:
    """Upload the local DuckDB corpus to R2. Returns False if R2 is disabled."""
    if not is_enabled(config):
        logger.info("R2 disabled (storage.r2.enabled: false) — not pushing corpus.")
        return False
    path = Path(db_path) if db_path else resolve_path(config, "storage", "duckdb_path")
    if not path.exists():
        logger.warning("No corpus at %s to push.", path)
        return False
    bucket = _bucket(config)
    key = str(get(config, "storage", "r2", "corpus_key", default="bigthink.duckdb"))
    client = client or get_client(config)
    client.upload_file(str(path), bucket, key)
    logger.info("Pushed %s -> r2://%s/%s", path, bucket, key)
    return True


def pull_corpus(config: dict[str, Any], db_path: str | Path | None = None, client=None) -> bool:
    """Download the corpus from R2 to the local path. Returns False if unavailable."""
    if not is_enabled(config):
        logger.info("R2 disabled (storage.r2.enabled: false) — not pulling corpus.")
        return False
    path = Path(db_path) if db_path else resolve_path(config, "storage", "duckdb_path")
    bucket = _bucket(config)
    key = str(get(config, "storage", "r2", "corpus_key", default="bigthink.duckdb"))
    client = client or get_client(config)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        client.download_file(bucket, key, str(path))
    except Exception as exc:  # noqa: BLE001 - boto3 raises its own ClientError types
        if _is_not_found(exc):
            logger.info("No corpus found at r2://%s/%s yet.", bucket, key)
            return False
        raise
    logger.info("Pulled r2://%s/%s -> %s", bucket, key, path)
    return True


# ---------------------------------------------------------------------------
# Raw payloads (data/raw/<run_id>/)
# ---------------------------------------------------------------------------


def push_raw(config: dict[str, Any], run_id: str, raw_dir: str | Path | None = None, client=None) -> int:
    """Upload every raw payload file for *run_id*. Returns the count uploaded."""
    if not is_enabled(config):
        logger.info("R2 disabled (storage.r2.enabled: false) — not pushing raw payloads.")
        return 0
    base = Path(raw_dir) if raw_dir else resolve_path(config, "storage", "raw_dir")
    run_dir = base / run_id
    if not run_dir.exists():
        logger.warning("No raw payloads at %s to push.", run_dir)
        return 0
    bucket = _bucket(config)
    prefix = str(get(config, "storage", "r2", "raw_prefix", default="raw")).strip("/")
    client = client or get_client(config)
    count = 0
    for file_path in sorted(run_dir.rglob("*")):
        if not file_path.is_file():
            continue
        key = f"{prefix}/{run_id}/{file_path.relative_to(run_dir).as_posix()}"
        client.upload_file(str(file_path), bucket, key)
        count += 1
    logger.info("Pushed %d raw payload file(s) for run %s -> r2://%s/%s/%s", count, run_id, bucket, prefix, run_id)
    return count


def pull_raw(config: dict[str, Any], run_id: str, raw_dir: str | Path | None = None, client=None) -> int:
    """Download every raw payload file for *run_id*. Returns the count downloaded."""
    if not is_enabled(config):
        logger.info("R2 disabled (storage.r2.enabled: false) — not pulling raw payloads.")
        return 0
    base = Path(raw_dir) if raw_dir else resolve_path(config, "storage", "raw_dir")
    run_dir = base / run_id
    bucket = _bucket(config)
    prefix = str(get(config, "storage", "r2", "raw_prefix", default="raw")).strip("/")
    client = client or get_client(config)
    remote_prefix = f"{prefix}/{run_id}/"
    count = 0
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=remote_prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel = key[len(remote_prefix):]
            if not rel:
                continue
            dest = run_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            client.download_file(bucket, key, str(dest))
            count += 1
    logger.info("Pulled %d raw payload file(s) for run %s <- r2://%s/%s", count, run_id, bucket, remote_prefix)
    return count


def _is_not_found(exc: Exception) -> bool:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return False
    code = response.get("Error", {}).get("Code", "")
    return code in ("404", "NoSuchKey", "NotFound")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Sync the BigThink corpus and raw payloads with Cloudflare R2.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--log-level", default="INFO")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("pull-corpus", help="Download the corpus from R2 into data/bigthink.duckdb.")
    sub.add_parser("push-corpus", help="Upload the local corpus to R2.")
    for name, help_text in (
        ("push-raw", "Upload a run's raw payloads to R2."),
        ("pull-raw", "Download a run's raw payloads from R2."),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--run-id", required=True)

    args = parser.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), format="%(levelname)-7s %(name)s: %(message)s")
    config = load_config(args.config)

    if args.command == "pull-corpus":
        ok = pull_corpus(config)
    elif args.command == "push-corpus":
        ok = push_corpus(config)
    elif args.command == "push-raw":
        ok = push_raw(config, args.run_id) > 0
    elif args.command == "pull-raw":
        ok = pull_raw(config, args.run_id) > 0
    else:  # pragma: no cover - argparse enforces the choices above
        parser.error(f"Unknown command {args.command!r}")
        return 2
    return 0 if ok or not is_enabled(config) else 1


if __name__ == "__main__":
    sys.exit(main())
