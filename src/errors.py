"""
src/errors.py

Error hierarchy for the BigThink opportunity-identification pipeline.

Adapted from Tripwire (Thomas-Amann-IPAustralia/Tripwire, src/errors.py).
The Retryable/Permanent split is what lets src/retry.py decide, without any
per-call-site logic, whether a failure is worth a second attempt.

  RetryableError  — transient; a later attempt may succeed.
                    HTTP 5xx, HTTP 429, connection timeout, DNS failure.

  PermanentError  — retrying will not help; skip this source for this run.
                    HTTP 404/403, malformed payload, missing credentials.

Collectors are expected to raise these rather than bare requests exceptions,
so that a single failing source degrades the run instead of ending it.
"""

from __future__ import annotations

from typing import Any


class BigThinkError(Exception):
    """Base class for all BigThink pipeline errors."""

    def __init__(self, message: str, *, context: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.context: dict[str, Any] = context or {}

    def __repr__(self) -> str:
        ctx = f", context={self.context!r}" if self.context else ""
        return f"{type(self).__name__}({str(self)!r}{ctx})"


class RetryableError(BigThinkError):
    """Transient failure — the operation should be retried with backoff."""


class PermanentError(BigThinkError):
    """Non-transient failure — retrying will not help; skip this source."""


class ConfigError(BigThinkError):
    """Raised when bigthink_config.yaml fails to load or validate."""


# ---------------------------------------------------------------------------
# Convenience constructors
# ---------------------------------------------------------------------------


def http_error(status_code: int, url: str) -> BigThinkError:
    """Return the appropriate error type for an HTTP error response.

    429 and 5xx are retryable; everything else (401/403/404/422) is not.
    """
    ctx = {"status_code": status_code, "url": url}
    if status_code == 429 or status_code >= 500:
        return RetryableError(f"HTTP {status_code} fetching {url}", context=ctx)
    return PermanentError(
        f"HTTP {status_code} fetching {url} (non-retryable)", context=ctx
    )


def missing_credential_error(collector: str, env_var: str) -> PermanentError:
    """Raised when a collector needs an API key that is not configured.

    Permanent by design: the run should skip this source and carry on rather
    than retry a request that cannot possibly succeed.
    """
    return PermanentError(
        f"Collector '{collector}' requires environment variable {env_var}, which is not set",
        context={"collector": collector, "env_var": env_var},
    )


def malformed_response_error(collector: str, url: str, detail: str) -> PermanentError:
    return PermanentError(
        f"Malformed response from {collector} at {url}: {detail}",
        context={"collector": collector, "url": url, "detail": detail},
    )


def insufficient_data_error(stage: str, detail: str) -> PermanentError:
    """Raised when a stage cannot run because upstream produced too little data."""
    return PermanentError(
        f"Stage '{stage}' has insufficient input data: {detail}",
        context={"stage": stage, "detail": detail},
    )
