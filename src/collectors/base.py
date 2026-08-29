"""
src/collectors/base.py

Shared machinery for every signal collector.

The contract each collector implements is deliberately small: given a query
string and a year range, yield normalised document dicts. Everything else —
HTTP, retry, rate limiting, error classification, deduplication, raw-payload
persistence — lives here, so a new source is one small file rather than a
re-implementation of the same plumbing.

Error handling follows Tripwire: HTTP 429/5xx raise RetryableError and are
retried with backoff; everything else raises PermanentError and the run
continues without that source. A horizon scan that dies because one API is
down is a horizon scan nobody runs twice.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from datetime import date, datetime, timezone
from typing import Any, Iterator, Sequence

import requests

from src.errors import PermanentError, RetryableError, http_error, malformed_response_error
from src.retry import MAX_BACKOFF_SECONDS, retry_call

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 60


# ---------------------------------------------------------------------------
# Normalised document
# ---------------------------------------------------------------------------


def make_doc_id(source: str, native_id: str) -> str:
    """Stable id for a document. Same source + native id => same id, always.

    Deduplication depends on this being deterministic across runs and machines,
    so it hashes rather than relying on any in-process identity.
    """
    return hashlib.sha256(f"{source}\x00{native_id}".encode("utf-8")).hexdigest()[:32]


def to_time_slice(published: date | None, granularity: str = "year") -> str | None:
    """Bucket a date into the configured time slice ('2024' or '2024Q3')."""
    if published is None:
        return None
    if granularity == "quarter":
        return f"{published.year}Q{(published.month - 1) // 3 + 1}"
    return str(published.year)


def parse_date(value: Any) -> date | None:
    """Best-effort date parsing across the formats these APIs actually return."""
    if value is None:
        return None
    if isinstance(value, date) and not isinstance(value, datetime):
        return value
    if isinstance(value, datetime):
        return value.date()
    text = str(value).strip()
    if not text:
        return None
    # GDELT: 20260821T004500Z
    m = re.match(r"^(\d{4})(\d{2})(\d{2})T", text)
    if m:
        return _safe_date(*(int(g) for g in m.groups()))
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%SZ", "%Y/%m/%d", "%Y-%m", "%Y"):
        try:
            return datetime.strptime(text[: len(fmt) + 6], fmt).date()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})", text)
    return _safe_date(int(m.group(1)), 1, 1) if m else None


def _safe_date(year: int, month: int, day: int) -> date | None:
    try:
        return date(year, month, day)
    except ValueError:
        return None


def build_document(
    *,
    source: str,
    native_id: str,
    title: str,
    abstract: str = "",
    published: Any = None,
    url: str = "",
    venue: str = "",
    authors: Sequence[str] | None = None,
    institutions: Sequence[str] | None = None,
    concepts: Sequence[str] | None = None,
    citation_count: int = 0,
    steepv: str = "",
    scan_frame_key: str = "",
    tone: float | None = None,
    raw_path: str | None = None,
    run_id: str = "",
    time_granularity: str = "year",
) -> dict[str, Any]:
    """Assemble a normalised document row."""
    published_date = parse_date(published)
    return {
        "doc_id": make_doc_id(source, native_id),
        "source": source,
        "native_id": native_id,
        "title": (title or "").strip(),
        "abstract": (abstract or "").strip(),
        "published_date": published_date,
        "year": published_date.year if published_date else None,
        "time_slice": to_time_slice(published_date, time_granularity),
        "url": url,
        "venue": venue,
        "authors": list(authors or []),
        "institutions": list(institutions or []),
        "concepts": list(concepts or []),
        "citation_count": int(citation_count or 0),
        "steepv": steepv,
        "scan_frame_key": scan_frame_key,
        "tone": tone,
        "raw_path": raw_path,
        "collected_at": datetime.now(timezone.utc),
        "run_id": run_id,
    }


def document_text(doc: dict[str, Any]) -> str:
    """The text used for embedding and term extraction: title + abstract."""
    return f"{doc.get('title', '')}. {doc.get('abstract', '')}".strip()


# ---------------------------------------------------------------------------
# Base collector
# ---------------------------------------------------------------------------


class Collector:
    """Base class. Subclasses implement `collect`."""

    #: Registry key — must match a key under `collection.sources` in the config.
    name: str = "base"
    #: Seconds to wait between requests, on top of any retry backoff.
    request_delay: float = 0.0

    def __init__(self, config: dict[str, Any], run_id: str) -> None:
        from src.config import contact_email, get, resolve_path, user_agent

        self.config = config
        self.run_id = run_id
        self.settings: dict[str, Any] = get(config, "collection", "sources", self.name, default={}) or {}
        self.contact_email = contact_email(config)
        self.time_granularity = str(get(config, "emergence", "time_slice", default="year"))
        self.sample_mode = bool(get(config, "pipeline", "sample_mode", default=False))
        self.sample_limit = int(get(config, "pipeline", "sample_limit", default=50))
        self.keep_raw = bool(get(config, "storage", "keep_raw_payloads", default=True))
        self.raw_dir = resolve_path(config, "storage", "raw_dir")

        self._session = requests.Session()
        self._session.headers.update(
            {"User-Agent": user_agent(config), "Accept": "application/json"}
        )
        self._last_request_at = 0.0

    # -- to implement ----------------------------------------------------
    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        raise NotImplementedError

    # -- HTTP ------------------------------------------------------------
    def fetch_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        """GET returning parsed JSON, with rate limiting and retry."""
        return retry_call(
            self._fetch_json_once,
            url,
            params,
            max_retries=int(self.config.get("pipeline", {}).get("max_retries", 3)),
            base_delay=float(
                self.config.get("pipeline", {}).get("retry_base_delay_seconds", 2.0)
            ),
        )

    def fetch_text(self, url: str, params: dict[str, Any] | None = None) -> str:
        return retry_call(
            self._fetch_text_once,
            url,
            params,
            max_retries=int(self.config.get("pipeline", {}).get("max_retries", 3)),
            base_delay=float(
                self.config.get("pipeline", {}).get("retry_base_delay_seconds", 2.0)
            ),
        )

    def _throttle(self) -> None:
        if self.request_delay <= 0:
            return
        elapsed = time.monotonic() - self._last_request_at
        if elapsed < self.request_delay:
            time.sleep(self.request_delay - elapsed)

    def _request(self, url: str, params: dict[str, Any] | None) -> requests.Response:
        self._throttle()
        try:
            response = self._session.get(url, params=params, timeout=DEFAULT_TIMEOUT)
        except requests.Timeout as exc:
            raise RetryableError(f"Timeout fetching {url}", context={"url": url}) from exc
        except requests.RequestException as exc:
            raise RetryableError(
                f"Connection error fetching {url}: {exc}", context={"url": url}
            ) from exc
        finally:
            self._last_request_at = time.monotonic()

        if response.status_code >= 400:
            err = http_error(response.status_code, response.url)
            if isinstance(err, RetryableError):
                retry_after = _retry_after_seconds(response)
                if retry_after is not None and retry_after > MAX_BACKOFF_SECONDS:
                    # A server asking us to wait longer than any single run
                    # should sleep is not describing a transient spike — it is
                    # a quota resetting on its own clock. OpenAlex sends
                    # Retry-After ~62000 s (its daily budget, resetting at
                    # midnight UTC). Retrying that inside this run cannot
                    # succeed; it only burns the job timeout. Escalate to
                    # permanent so the source is retired and the scan moves on.
                    raise PermanentError(
                        f"HTTP {response.status_code} fetching {response.url} with "
                        f"Retry-After={retry_after:.0f}s, beyond this run's tolerance "
                        f"({MAX_BACKOFF_SECONDS:.0f}s). Treating as a quota, not a "
                        f"transient failure.",
                        context={
                            "status_code": response.status_code,
                            "url": response.url,
                            "retry_after": retry_after,
                            "body": response.text[:300],
                        },
                    )
                if retry_after is not None:
                    err.context["retry_after"] = retry_after
            raise err
        return response

    def _fetch_json_once(self, url: str, params: dict[str, Any] | None) -> Any:
        response = self._request(url, params)
        try:
            return response.json()
        except ValueError as exc:
            raise malformed_response_error(
                self.name, url, f"response is not JSON ({response.text[:120]!r})"
            ) from exc

    def _fetch_text_once(self, url: str, params: dict[str, Any] | None) -> str:
        return self._request(url, params).text

    # -- raw payload persistence -----------------------------------------
    def save_raw(self, frame_key: str, page: int, payload: Any) -> str | None:
        """Persist a raw API payload so a result can be re-derived without re-fetching.

        Returns the repo-relative path, or None when raw retention is off.
        Kept because free APIs change their schemas without notice, and the
        alternative to a stored payload is re-running a scan you cannot
        reproduce.
        """
        if not self.keep_raw:
            return None
        out_dir = self.raw_dir / self.run_id / self.name
        out_dir.mkdir(parents=True, exist_ok=True)
        safe_key = re.sub(r"[^A-Za-z0-9_.-]", "_", frame_key)[:80]
        path = out_dir / f"{safe_key}_p{page:03d}.json"
        try:
            path.write_text(
                json.dumps(payload, ensure_ascii=False, default=str), encoding="utf-8"
            )
        except OSError as exc:
            logger.warning("Could not write raw payload %s: %s", path, exc)
            return None
        from src.config import REPO_ROOT

        try:
            return str(path.relative_to(REPO_ROOT))
        except ValueError:
            return str(path)

    # -- helpers ----------------------------------------------------------
    def steepv_for(self, frame: dict[str, Any]) -> str:
        """STEEPV category for a document: the frame's own, else the source default."""
        from src.config import get

        if frame.get("steepv"):
            return str(frame["steepv"])
        return str(
            get(self.config, "collection", "steepv_default_by_source", self.name, default="Technological")
        )

    def cap(self, count: int) -> bool:
        """True when sample mode says to stop."""
        return self.sample_mode and count >= self.sample_limit


def _retry_after_seconds(response: requests.Response) -> float | None:
    """Seconds the server wants us to wait, from the header or the JSON body.

    OpenAlex reports its budget reset in a `retryAfter` body field rather than
    the standard header, so both are checked.
    """
    header = response.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass  # HTTP-date form; treat as unknown rather than mis-parse it
    try:
        body = response.json()
    except ValueError:
        return None
    if isinstance(body, dict):
        for key in ("retryAfter", "retry_after"):
            if key in body:
                try:
                    return float(body[key])
                except (TypeError, ValueError):
                    return None
    return None


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------

_REGISTRY: dict[str, type[Collector]] = {}


def register(cls: type[Collector]) -> type[Collector]:
    _REGISTRY[cls.name] = cls
    return cls


def get_collector(name: str) -> type[Collector]:
    if name not in _REGISTRY:
        raise PermanentError(
            f"No collector registered for source {name!r}. Registered: {sorted(_REGISTRY)}"
        )
    return _REGISTRY[name]


def registered_collectors() -> list[str]:
    return sorted(_REGISTRY)
