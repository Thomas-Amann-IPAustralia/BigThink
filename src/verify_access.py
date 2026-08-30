"""
src/verify_access.py — prove the optional credentials actually work.

The two optional integrations in this pipeline fail *soft* by design:
OpenAlex retires itself for the run when its budget is spent, and the R2
mirror in scan.yml swallows its own errors so a storage problem can never
cost a collected corpus. That is the right behaviour for a scheduled scan
and the wrong behaviour for answering "are my keys set up correctly?" —
both failures look like a couple of quiet log lines.

This module is the loud counterpart. It exercises each credential against
the live service and exits non-zero if any configured one does not work,
so the answer is a green check rather than an inference from silence.

    python -m src.verify_access                 # everything that is configured
    python -m src.verify_access --only openalex
    python -m src.verify_access --require       # unset credentials are failures

Secrets are never printed — only whether a variable is set and how long it
is, which is enough to catch the usual mistakes (empty secret, a newline or
quotes pasted along with the value) without putting the value in a log.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

OPENALEX_URL = "https://api.openalex.org/works"

_R2_VARS = ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


# ---------------------------------------------------------------------------
# Reporting helpers
# ---------------------------------------------------------------------------


class Result:
    """One check's outcome. `configured` is false when the credential simply
    is not set — distinct from being set and broken, because the first is a
    supported way to run this pipeline and the second is a bug."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.configured = False
        self.ok = False
        self.detail = ""

    def skip(self, detail: str) -> "Result":
        self.configured, self.ok, self.detail = False, False, detail
        return self

    def passed(self, detail: str) -> "Result":
        self.configured, self.ok, self.detail = True, True, detail
        return self

    def failed(self, detail: str) -> "Result":
        self.configured, self.ok, self.detail = True, False, detail
        return self

    @property
    def symbol(self) -> str:
        if not self.configured:
            return "SKIP"
        return "PASS" if self.ok else "FAIL"


def describe_env(name: str) -> str:
    """Report a variable's presence without revealing it.

    The length and the stripped-length are deliberately both reported: a
    value pasted with a trailing newline or wrapping quotes is the single
    most common cause of a credential that is 'set' and still rejected, and
    it is invisible in every other diagnostic.
    """
    raw = os.environ.get(name)
    if raw is None:
        return f"{name}: not set"
    if raw == "":
        return f"{name}: set but EMPTY"
    stripped = raw.strip().strip('"').strip("'")
    if stripped != raw:
        return (
            f"{name}: set, length {len(raw)} — WARNING: has surrounding "
            f"whitespace or quotes (length {len(stripped)} once trimmed)"
        )
    return f"{name}: set, length {len(raw)}"


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------


def check_openalex(contact_email: str = "", timeout: int = 30) -> Result:
    """Issue one real, minimal OpenAlex query using OPENALEX_API_KEY.

    The query is deliberately the cheapest thing that still proves the key
    works: one record, one page. A key that is present but not recognised
    comes back as the same 429 'Insufficient budget' an anonymous caller
    gets from a shared IP, so the two are reported differently — a 429 with
    a key set is a bad key, not an exhausted allowance.
    """
    result = Result("OpenAlex")
    api_key = os.environ.get("OPENALEX_API_KEY", "").strip()
    if not api_key:
        return result.skip(
            "OPENALEX_API_KEY not set — OpenAlex retires itself at the first "
            "frame on a shared IP, so the scan runs without its best source."
        )

    year = datetime.now(timezone.utc).year
    params = {
        "filter": f"from_publication_date:{year}-01-01",
        "per-page": "1",
        "api_key": api_key,
    }
    if contact_email:
        params["mailto"] = contact_email
    url = f"{OPENALEX_URL}?{urllib.parse.urlencode(params)}"

    try:
        with urllib.request.urlopen(url, timeout=timeout) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")[:300]
        if exc.code == 429:
            return result.failed(
                "HTTP 429 with a key set — the key is not being honoured "
                f"(wrong value, or not yet activated). Response: {body}"
            )
        if exc.code in (401, 403):
            return result.failed(f"HTTP {exc.code} — key rejected. Response: {body}")
        return result.failed(f"HTTP {exc.code}. Response: {body}")
    except Exception as exc:  # noqa: BLE001 - network stack raises many types
        return result.failed(f"{type(exc).__name__}: {exc}")

    meta = payload.get("meta") or {}
    results = payload.get("results") or []
    if not results:
        return result.failed(f"Query succeeded but returned no records. meta={meta}")

    title = str(results[0].get("display_name") or "")[:60]
    return result.passed(
        f"authenticated query returned {meta.get('count', '?'):,} works "
        f"for {year}; first record: {title!r}"
    )


# ---------------------------------------------------------------------------
# Cloudflare R2
# ---------------------------------------------------------------------------


def _diagnose_r2(exc: Exception, bucket: str) -> str:
    """Turn boto3's generic ClientError into the specific thing to go fix.

    R2 returns the same S3 error codes as AWS but the causes differ enough
    to be worth naming: almost every failure here is one of four setup
    mistakes, and the raw error names none of them.
    """
    response = getattr(exc, "response", None)
    code = ""
    if isinstance(response, dict):
        code = str(response.get("Error", {}).get("Code", ""))
    hints = {
        "InvalidAccessKeyId": "R2_ACCESS_KEY_ID is not a valid key for this account.",
        "SignatureDoesNotMatch": "R2_SECRET_ACCESS_KEY does not match the access key ID.",
        "AccessDenied": (
            f"Credentials are valid but not permitted on bucket {bucket!r}. "
            "The R2 API token needs Object Read & Write scoped to this bucket."
        ),
        "NoSuchBucket": f"No bucket named {bucket!r} in this account.",
        "404": f"Bucket {bucket!r} not found — check the name and R2_ACCOUNT_ID.",
        "403": (
            f"Forbidden on bucket {bucket!r} — the token is probably scoped to a "
            "different bucket, or is read-only."
        ),
    }
    hint = hints.get(code, "")
    return f"{code or type(exc).__name__}: {exc}" + (f"\n           → {hint}" if hint else "")


def check_r2(bucket: str, cleanup: bool = True, timeout: int = 30) -> Result:
    """Round-trip a small object through the real bucket.

    A read-only check would pass against a token that cannot write, and the
    whole point of this bucket is that CI writes the corpus to it. So the
    check writes, reads back, compares bytes, and deletes — the full path
    push_corpus/pull_corpus take, at a few hundred bytes instead of a few
    hundred megabytes.
    """
    result = Result(f"Cloudflare R2 (bucket {bucket!r})")
    missing = [name for name in _R2_VARS if not os.environ.get(name, "").strip()]
    if missing:
        return result.skip(f"{', '.join(missing)} not set — R2 sync unavailable.")

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return result.failed("boto3 is not installed (pip install -r requirements.txt).")

    account_id = os.environ["R2_ACCOUNT_ID"].strip()
    client = boto3.client(
        "s3",
        endpoint_url=f"https://{account_id}.r2.cloudflarestorage.com",
        aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(),
        aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
        region_name="auto",
        config=Config(connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 2}),
    )

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"_verify/access-check-{stamp}.txt"
    body = f"BigThink R2 access check {stamp}\n".encode("utf-8")
    steps: list[str] = []

    try:
        client.head_bucket(Bucket=bucket)
        steps.append("head_bucket")

        client.put_object(Bucket=bucket, Key=key, Body=body)
        steps.append("write")

        fetched = client.get_object(Bucket=bucket, Key=key)["Body"].read()
        steps.append("read")
        if fetched != body:
            return result.failed(f"Read-back mismatch: wrote {len(body)} bytes, got {len(fetched)}.")

        listing = client.list_objects_v2(Bucket=bucket, MaxKeys=20)
        steps.append("list")
        existing = [obj["Key"] for obj in listing.get("Contents", []) if obj["Key"] != key]

        if cleanup:
            client.delete_object(Bucket=bucket, Key=key)
            steps.append("delete")
    except Exception as exc:  # noqa: BLE001 - botocore raises its own error types
        done = ", ".join(steps) or "none"
        return result.failed(f"failed after [{done}] — {_diagnose_r2(exc, bucket)}")

    contents = ", ".join(existing[:5]) if existing else "(bucket is otherwise empty)"
    return result.passed(
        f"{' → '.join(steps)} all succeeded ({len(body)} bytes round-tripped). "
        f"Existing objects: {contents}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_settings(config_path: str | None) -> tuple[str, str]:
    """Return (contact_email, bucket) from the config, falling back to the
    documented defaults so this check still runs if the config cannot load —
    a broken config should not be able to mask a broken credential."""
    try:
        from src.config import get, load_config

        config: dict[str, Any] = load_config(config_path)
        email = os.environ.get("BIGTHINK_CONTACT_EMAIL") or str(
            get(config, "pipeline", "contact_email", default="") or ""
        )
        bucket = str(get(config, "storage", "r2", "bucket", default="bigthink-corpus") or "")
        return email, bucket
    except Exception as exc:  # noqa: BLE001 - config validation raises ConfigError
        logger.warning("Could not load config (%s); using defaults.", exc)
        return os.environ.get("BIGTHINK_CONTACT_EMAIL", ""), "bigthink-corpus"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Verify the OpenAlex and Cloudflare R2 credentials against the live services."
    )
    parser.add_argument("--config", default=None)
    parser.add_argument(
        "--only",
        choices=["openalex", "r2"],
        default=None,
        help="Run only one of the checks.",
    )
    parser.add_argument(
        "--bucket",
        default=None,
        help="Override the bucket name from storage.r2.bucket.",
    )
    parser.add_argument(
        "--require",
        action="store_true",
        help="Treat an unset credential as a failure rather than a skip.",
    )
    parser.add_argument(
        "--keep-test-object",
        action="store_true",
        help="Leave the R2 round-trip object in place instead of deleting it.",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level="INFO", format="%(levelname)-7s %(name)s: %(message)s")

    contact_email, bucket = _load_settings(args.config)
    bucket = args.bucket or bucket

    print("Credential presence (values are never printed)")
    print("-" * 70)
    for name in ("OPENALEX_API_KEY", *_R2_VARS):
        print(f"  {describe_env(name)}")
    print()

    results: list[Result] = []
    if args.only in (None, "openalex"):
        results.append(check_openalex(contact_email))
    if args.only in (None, "r2"):
        results.append(check_r2(bucket, cleanup=not args.keep_test_object))

    print("Live checks")
    print("-" * 70)
    for result in results:
        print(f"  [{result.symbol}] {result.name}")
        print(f"         {result.detail}")
    print()

    failures = [r for r in results if r.configured and not r.ok]
    skipped = [r for r in results if not r.configured]

    if failures:
        print(f"RESULT: {len(failures)} configured credential(s) FAILED.")
        return 1
    if skipped and args.require:
        print(f"RESULT: {len(skipped)} credential(s) not configured (--require).")
        return 1
    if skipped:
        print(f"RESULT: all configured credentials work; {len(skipped)} not configured.")
        return 0
    print("RESULT: all credentials verified against the live services.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
