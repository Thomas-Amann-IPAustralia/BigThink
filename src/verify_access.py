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
import re
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
    code = _error_code(exc)
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


def _is_connection_error(exc: Exception) -> bool:
    """True when the request never reached R2's API at all.

    Cloudflare refuses the TLS handshake outright for an account ID it does
    not recognise — verified by comparing a real account, which completes the
    handshake and returns an S3 error, against a well-formed but non-existent
    one, which fails with sslv3 alert handshake failure on both the default
    and jurisdiction endpoints. So a handshake failure here is a statement
    about the account ID, not about TLS or the network, and must not be
    reported as a permissions problem: nothing was ever authorised or denied.
    """
    name = type(exc).__name__
    if name in ("SSLError", "EndpointConnectionError", "ConnectTimeoutError", "ConnectionError"):
        return True
    return "handshake failure" in str(exc).lower()


def _error_code(exc: Exception) -> str:
    response = getattr(exc, "response", None)
    if not isinstance(response, dict):
        return ""
    return str(response.get("Error", {}).get("Code", ""))


def _read_permission(client, bucket: str) -> str:
    """Ask whether the token may read, without needing an object to exist.

    GetObject on a key that is certainly absent separates the two grants a
    Cloudflare R2 token exposes as separate checkboxes. NoSuchKey means the
    request was authorised and merely found nothing — read is permitted.
    AccessDenied means it was refused before that point — read is not.
    Without this, a token that can write but not read looks identical to one
    that can do neither, and they need different fixes.
    """
    probe_key = "_verify/does-not-exist-permission-probe"
    try:
        client.get_object(Bucket=bucket, Key=probe_key)
    except Exception as exc:  # noqa: BLE001 - botocore raises its own types
        code = _error_code(exc)
        if code in ("NoSuchKey", "404", "NoSuchKeyError"):
            return "granted"
        if code in ("AccessDenied", "403"):
            return "denied"
        return "unknown"
    return "granted"


def _visible_buckets(client, wanted: str) -> str:
    """On failure, ask what this token *can* see.

    Every object call being denied has two very different causes that the
    error text cannot tell apart: the token is scoped to a different bucket,
    or the bucket name is wrong. ListBuckets separates them — if it works and
    the name is absent, the bucket does not exist under this account ID; if
    it works and the name is present, the token simply is not scoped to it.
    A token scoped to specific buckets may refuse this too, which is itself
    the answer: the scope is narrower than this bucket.
    """
    try:
        buckets = [b["Name"] for b in client.list_buckets().get("Buckets", [])]
    except Exception as exc:  # noqa: BLE001 - botocore raises its own types
        return (
            "\n           ListBuckets is also denied. That is normal for a "
            "bucket-scoped token and cannot confirm the bucket name from here, so "
            "check the scope in the Cloudflare UI. If it already shows this token "
            f"scoped to {wanted!r} with the right permissions, then the key pair "
            "in use is not this token's — an unsaved policy edit, or an access key "
            "from a different token."
        )
    if not buckets:
        return (
            "\n           ListBuckets succeeded but this account has no buckets — "
            "check R2_ACCOUNT_ID points at the account holding the bucket."
        )
    listed = ", ".join(repr(b) for b in sorted(buckets)[:20])
    if wanted in buckets:
        return (
            f"\n           The bucket {wanted!r} DOES exist in this account, so the "
            "name is right and the API token is not scoped to it. Buckets visible: "
            f"{listed}."
        )
    return (
        f"\n           No bucket named {wanted!r} in this account. Buckets visible: "
        f"{listed}. Either create it or set storage.r2.bucket to one of these."
    )


def check_r2(
    bucket: str,
    cleanup: bool = True,
    timeout: int = 30,
    jurisdiction_name: str = "",
) -> Result:
    """Round-trip a small object through the real bucket.

    A read-only check would pass against a token that cannot write, and the
    whole point of this bucket is that CI writes the corpus to it. So the
    check writes, reads back, compares bytes, and deletes — the full path
    push_corpus/pull_corpus take, at a few hundred bytes instead of a few
    hundred megabytes.

    Each operation is probed independently and reported by name, because
    R2 token scopes do not map cleanly onto "it works" or "it doesn't":
    an Object Read & Write token scoped to one bucket routinely refuses
    bucket-level calls while permitting every object-level one. Only two
    operations decide the verdict — PutObject and GetObject, the two
    storage.py actually performs. HeadBucket is advisory: storage.py never
    calls it, so failing the whole check on it would report a working
    corpus mirror as broken. ListObjectsV2 and DeleteObject are reported
    but not fatal either; only pull_raw needs listing, and nothing in this
    pipeline deletes.
    """
    where = f"bucket {bucket!r}"
    if jurisdiction_name:
        where += f", {jurisdiction_name} jurisdiction"
    result = Result(f"Cloudflare R2 ({where})")
    missing = [name for name in _R2_VARS if not os.environ.get(name, "").strip()]
    if missing:
        return result.skip(f"{', '.join(missing)} not set — R2 sync unavailable.")

    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        return result.failed("boto3 is not installed (pip install -r requirements.txt).")

    account_id = os.environ["R2_ACCOUNT_ID"].strip()
    # Checked before it reaches botocore, which rejects a malformed endpoint
    # with a bare ValueError naming only the URL it could not parse — and the
    # URL is secret-masked in CI logs, so the message there says nothing at
    # all. The usual mistakes are pasting the endpoint URL or the token's
    # resource key (<account>_<jurisdiction>_<bucket>) instead of the ID.
    if not re.fullmatch(r"[0-9a-fA-F]{32}", account_id):
        return result.failed(
            f"R2_ACCOUNT_ID is {len(account_id)} characters; a Cloudflare account ID "
            "is exactly 32 hexadecimal characters. Set it to the account ID alone — "
            "not the endpoint URL, and not the API token's resource key "
            "(<account>_<jurisdiction>_<bucket>), whose jurisdiction belongs in "
            "storage.r2.jurisdiction instead."
        )

    # Built by storage.py rather than rebuilt here: a check that constructs
    # its own endpoint can pass against one the pipeline never uses.
    from src.storage import endpoint_url

    endpoint = endpoint_url(account_id, jurisdiction_name)
    try:
        client = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.environ["R2_ACCESS_KEY_ID"].strip(),
            aws_secret_access_key=os.environ["R2_SECRET_ACCESS_KEY"].strip(),
            region_name="auto",
            config=Config(
                connect_timeout=timeout, read_timeout=timeout, retries={"max_attempts": 2}
            ),
        )
    except Exception as exc:  # noqa: BLE001 - botocore raises ValueError here
        return result.failed(f"could not build a client for {endpoint}: {exc}")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    key = f"_verify/access-check-{stamp}.txt"
    body = f"BigThink R2 access check {stamp}\n".encode("utf-8")

    outcomes: list[str] = []
    detail: dict[str, str] = {}
    errors: dict[str, Exception] = {}

    def probe(label: str, fn) -> Any:
        """Run one operation, record whether it worked, and keep going."""
        try:
            value = fn()
        except Exception as exc:  # noqa: BLE001 - botocore raises its own types
            outcomes.append(f"{label}=DENIED")
            detail[label] = _diagnose_r2(exc, bucket)
            errors[label] = exc
            return None
        outcomes.append(f"{label}=ok")
        return value if value is not None else True

    probe("HeadBucket", lambda: client.head_bucket(Bucket=bucket))
    wrote = probe("PutObject", lambda: client.put_object(Bucket=bucket, Key=key, Body=body))
    fetched = probe(
        "GetObject",
        lambda: client.get_object(Bucket=bucket, Key=key)["Body"].read(),
    ) if wrote else None
    listing = probe("ListObjectsV2", lambda: client.list_objects_v2(Bucket=bucket, MaxKeys=20))
    if wrote and cleanup:
        probe("DeleteObject", lambda: client.delete_object(Bucket=bucket, Key=key))

    summary = ", ".join(outcomes)
    reasons = "".join(f"\n           {label}: {msg}" for label, msg in detail.items())

    # PutObject and GetObject are the verdict: they are exactly what
    # push_corpus and pull_corpus do.
    if not wrote or fetched is None:
        if any(_is_connection_error(exc) for exc in errors.values()):
            # Never reached the API, so nothing here is evidence about the
            # token's grants — saying "denied" would send someone to the
            # permissions screen for a problem that is not there.
            return result.failed(
                f"{summary} — but none of these reached R2: the TLS handshake was "
                f"refused at {endpoint}. Cloudflare rejects the handshake for an "
                "account ID it does not recognise, so R2_ACCOUNT_ID is a "
                "well-formed value that is not this account's ID. It is 32 hex "
                "characters and so is the Access Key ID, which is the usual "
                "mix-up; the account ID is the one on the R2 overview page, and "
                "the one an API token's resource key spells as "
                "com.cloudflare.edge.r2.bucket.<account>_<jurisdiction>_<bucket>."
            )
        grants = ""
        if not wrote:
            # Which checkbox is missing, in the terms the Cloudflare UI uses.
            read_state = _read_permission(client, bucket)
            grants = (
                "\n           Token grants on this bucket: "
                f"read={read_state}, write=denied. Both are needed — push_corpus "
                "writes (PutObject) and pull_corpus reads (GetObject), so a token "
                "with only one of them leaves the mirror half-working."
            )
            if read_state == "denied":
                # Nothing at all is permitted. If the token you are looking at
                # does grant these, you are not looking at the token in use.
                grants += (
                    "\n           Neither is permitted, so this key pair has no "
                    "rights on this bucket at all. If the Cloudflare UI shows "
                    "otherwise, the policy edit was not saved or the secrets hold "
                    "a different token's access key — compare the Access Key ID "
                    "shown in Cloudflare against R2_ACCESS_KEY_ID."
                )
        return result.failed(
            f"{summary}{reasons}{grants}{_visible_buckets(client, bucket)}"
        )
    if fetched != body:
        return result.failed(
            f"read-back mismatch: wrote {len(body)} bytes, got {len(fetched)}. {summary}"
        )

    existing: list[str] = []
    if isinstance(listing, dict):
        existing = [obj["Key"] for obj in listing.get("Contents", []) if obj["Key"] != key]
    contents = ", ".join(existing[:5]) if existing else "(no other objects)"

    note = ""
    if detail:
        note = (
            f" Non-fatal: {', '.join(detail)} denied — storage.py does not call "
            f"{'HeadBucket' if 'HeadBucket' in detail else 'them'}, so the corpus "
            "mirror still works."
        ) + reasons
    return result.passed(
        f"corpus read/write path works — {summary} ({len(body)} bytes round-tripped). "
        f"Bucket contains: {contents}.{note}"
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _load_settings(config_path: str | None) -> tuple[str, str, str]:
    """Return (contact_email, bucket, jurisdiction) from the config, falling
    back to the documented defaults so this check still runs if the config
    cannot load — a broken config should not be able to mask a broken
    credential."""
    try:
        from src.config import get, load_config
        from src.storage import jurisdiction

        config: dict[str, Any] = load_config(config_path)
        email = os.environ.get("BIGTHINK_CONTACT_EMAIL") or str(
            get(config, "pipeline", "contact_email", default="") or ""
        )
        bucket = str(get(config, "storage", "r2", "bucket", default="bigthink-corpus") or "")
        return email, bucket, jurisdiction(config)
    except Exception as exc:  # noqa: BLE001 - config validation raises ConfigError
        logger.warning("Could not load config (%s); using defaults.", exc)
        return (
            os.environ.get("BIGTHINK_CONTACT_EMAIL", ""),
            "bigthink-corpus",
            os.environ.get("R2_JURISDICTION", "").strip().lower(),
        )


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
        "--jurisdiction",
        default=None,
        help=(
            "Override storage.r2.jurisdiction (e.g. 'eu'). Pass '' to force the "
            "default, unrestricted endpoint."
        ),
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

    contact_email, bucket, jurisdiction_name = _load_settings(args.config)
    bucket = args.bucket or bucket
    if args.jurisdiction is not None:
        jurisdiction_name = args.jurisdiction.strip().lower()

    print("Credential presence (values are never printed)")
    print("-" * 70)
    for name in ("OPENALEX_API_KEY", *_R2_VARS):
        print(f"  {describe_env(name)}")
    print()

    results: list[Result] = []
    if args.only in (None, "openalex"):
        results.append(check_openalex(contact_email))
    if args.only in (None, "r2"):
        results.append(
            check_r2(
                bucket,
                cleanup=not args.keep_test_object,
                jurisdiction_name=jurisdiction_name,
            )
        )

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
