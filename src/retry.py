"""
src/retry.py

Exponential-backoff retry for the BigThink pipeline.

Adapted from Tripwire (Thomas-Amann-IPAustralia/Tripwire, src/retry.py), with
one addition BigThink needs that Tripwire did not: `Retry-After` support, since
OpenAlex, Crossref and GDELT all return 429 with a documented backoff hint and
ignoring it gets the shared runner IP throttled harder.

Only RetryableError triggers a retry. PermanentError and every other exception
propagate immediately.

Backoff:  delay = base_delay * (2 ** attempt) + jitter
          jitter = uniform(0, base_delay * 0.1)
giving roughly 2 s, 4 s, 8 s at the default base of 2.0.
"""

from __future__ import annotations

import functools
import logging
import random
import time
from typing import Any, Callable, TypeVar

from src.errors import RetryableError

logger = logging.getLogger(__name__)

F = TypeVar("F", bound=Callable[..., Any])

# Never sleep longer than this for a single attempt, even if a server sends an
# absurd Retry-After. A collector that wants a longer pause should be
# rescheduled, not blocked inside a GitHub Actions job.
MAX_BACKOFF_SECONDS = 120.0


def retry_call(
    func: Callable[..., Any],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 2.0,
    **kwargs: Any,
) -> Any:
    """Call *func* with retries on RetryableError.

    Total calls = max_retries + 1. Raises the last RetryableError if every
    attempt fails; any other exception propagates on its first occurrence.

    If the raised RetryableError carries a ``retry_after`` value in its
    ``context`` (seconds), that value is honoured in preference to the
    computed backoff, capped at MAX_BACKOFF_SECONDS.
    """
    last_exc: RetryableError | None = None
    for attempt in range(max_retries + 1):
        try:
            return func(*args, **kwargs)
        except RetryableError as exc:
            last_exc = exc
            if attempt >= max_retries:
                break
            delay = _resolve_delay(exc, base_delay, attempt)
            logger.warning(
                "RetryableError on attempt %d/%d for %s: %s — retrying in %.1f s",
                attempt + 1,
                max_retries + 1,
                getattr(func, "__name__", repr(func)),
                exc,
                delay,
            )
            time.sleep(delay)

    logger.error(
        "All %d attempt(s) failed for %s: %s",
        max_retries + 1,
        getattr(func, "__name__", repr(func)),
        last_exc,
    )
    raise last_exc  # type: ignore[misc]


def with_retry(max_retries: int = 3, base_delay: float = 2.0) -> Callable[[F], F]:
    """Decorator wrapping a function with retry-on-RetryableError logic."""

    def decorator(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            return retry_call(
                func, *args, max_retries=max_retries, base_delay=base_delay, **kwargs
            )

        return wrapper  # type: ignore[return-value]

    return decorator


def with_retry_from_config(config: dict[str, Any]) -> Callable[[F], F]:
    """Decorator variant reading retry parameters from the loaded config dict."""
    pipeline = config.get("pipeline", {}) or {}
    return with_retry(
        max_retries=int(pipeline.get("max_retries", 3)),
        base_delay=float(pipeline.get("retry_base_delay_seconds", 2.0)),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _resolve_delay(exc: RetryableError, base: float, attempt: int) -> float:
    """Prefer a server-supplied Retry-After; otherwise use exponential backoff."""
    hinted = exc.context.get("retry_after")
    if hinted is not None:
        try:
            return min(float(hinted), MAX_BACKOFF_SECONDS)
        except (TypeError, ValueError):
            pass
    return _backoff_delay(base, attempt)


def _backoff_delay(base: float, attempt: int) -> float:
    """Compute the delay for *attempt* (0-indexed) with jitter, capped."""
    delay = base * (2**attempt) + random.uniform(0, base * 0.1)
    return min(delay, MAX_BACKOFF_SECONDS)
