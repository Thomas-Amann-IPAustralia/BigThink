"""
src/collectors/gdelt.py — GDELT news attention.

The attention/salience proxy behind Stage 4's opportunity index: worldwide news
in 100+ languages, updated every 15 minutes, free and keyless.

OPERATIONAL REALITY (measured 2026-08-29). GDELT's DOC API rate-limits hard by
source IP and gives no polite error when it does — the connection is simply
dropped mid-response. From a shared or cloud IP (GitHub Actions included) a
majority of requests can fail this way even at six seconds apart. Two
consequences shaped this collector:

  * Failure is a recorded skip, never an exception that ends the scan. A GDELT
    outage should cost you the attention component of the index, not the run —
    but it is written to `collection_log`, because a frame with no attention
    signal and a frame with genuinely no coverage are different claims.

WHY THE WINDOW IS CHUNKED (measured 2026-08-30)

`timespan=24m` does not return 24 months. `artlist` sorts most-recent-first and
`maxrecords` caps at 250, so a single request returns the newest 250 articles
and nothing older: on the 2026-08-30 run every one of the 2,040 GDELT documents
carried a 2026 date, and a live re-test returned only 2026-06 to 2026-08. The
knob said two years and delivered one quarter.

`startdatetime`/`enddatetime` do work — verified against the live API on
2026-08-30, returning genuine 2025 articles — so the window is split into
`window_chunks` equal ranges and each is requested separately. That costs one
request per chunk per frame, at a measured 32-36 s each, which is why the count
is configurable and why a failed chunk is recorded and the rest still
attempted: a partial window is worth keeping, and worth knowing about.

Article records carry no per-article tone. Tone is fetched once per query via
`mode=timelinetone` and applied as a query-level average — an honest
approximation, and recorded as such on every document.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Iterator

from src.collectors.base import Collector, build_document, register
from src.errors import BigThinkError

logger = logging.getLogger(__name__)

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"

#: GDELT's datetime format for startdatetime/enddatetime.
_STAMP = "%Y%m%d%H%M%S"


def _windows(months: int, chunks: int) -> list[tuple[str, str]]:
    """Split the last *months* into *chunks* consecutive GDELT date ranges.

    Oldest first, so a frame that runs out of budget or hits a failing streak
    still has the recent end of the window — which is the half an attention
    signal most needs.

    `chunks=1` produces a single full-width range, which returns the same most
    recent `maxrecords` articles that `timespan` did. That is the pre-2026-08-30
    behaviour, kept reachable so an old run can be reproduced from its config
    snapshot.
    """
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=months * 30.44)
    step = (end - start) / chunks
    edges = [start + step * i for i in range(chunks + 1)]
    return [
        (edges[i].strftime(_STAMP), edges[i + 1].strftime(_STAMP))
        for i in range(chunks)
    ]


@register
class GdeltCollector(Collector):
    name = "gdelt"
    # GDELT publishes no documented limit; 6 s is the shortest interval that
    # was not immediately dropped in testing, and it is still not reliable.
    request_delay = 6.0

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        max_records = min(int(self.settings.get("max_records_per_query", 250)), 250)
        months = int(self.settings.get("timespan_months", 24))
        chunks = max(int(self.settings.get("window_chunks", 1)), 1)
        steepv = self.steepv_for(frame)
        frame_key = str(frame.get("key", "query"))

        tone = self._average_tone(query, months)

        emitted = 0
        seen: set[str] = set()
        windows = _windows(months, chunks)
        for index, (window_start, window_end) in enumerate(windows):
            params = {
                "query": query,
                "mode": "artlist",
                "maxrecords": max_records,
                "format": "json",
                "startdatetime": window_start,
                "enddatetime": window_end,
            }
            try:
                payload = self.fetch_json(API_URL, params)
            except BigThinkError as exc:
                # One window, not the frame. GDELT rate-limits by IP and drops
                # connections without an error code, so losing a chunk is
                # routine from a shared runner; losing the remaining chunks
                # because of it is a choice, and the wrong one.
                self.note_incident(
                    f"window {index + 1}/{len(windows)} "
                    f"({window_start[:6]}-{window_end[:6]}): {exc}"
                )
                logger.warning(
                    "GDELT window %d/%d unavailable for %r (%s) — continuing with the "
                    "remaining windows. This frame's attention signal will be partial.",
                    index + 1, len(windows), frame_key, exc,
                )
                continue

            articles = payload.get("articles") or []
            if not articles:
                continue
            self.save_raw(frame_key, index, payload)

            for article in articles:
                doc = self._to_document(
                    article, frame, steepv, tone, start_year, end_year
                )
                if doc is None or doc["doc_id"] in seen:
                    continue
                seen.add(doc["doc_id"])
                yield doc
                emitted += 1
                if self.cap(emitted):
                    return

    def _average_tone(self, query: str, months: int) -> float | None:
        """Query-level mean tone, or None if the tone call fails.

        None rather than 0.0 on failure: zero is a real tone value (neutral
        coverage) and must not be confused with an absent measurement.
        """
        try:
            payload = self.fetch_json(
                API_URL,
                {
                    "query": query,
                    "mode": "timelinetone",
                    "format": "json",
                    "timespan": f"{months}m",
                },
            )
        except BigThinkError as exc:
            self.note_incident(f"tone: {exc}")
            logger.debug("GDELT tone unavailable for %r", query)
            return None

        series = payload.get("timeline") or []
        values = [
            float(point["value"])
            for entry in series
            for point in (entry.get("data") or [])
            if point.get("value") is not None
        ]
        return sum(values) / len(values) if values else None

    def _to_document(
        self,
        article: dict[str, Any],
        frame: dict[str, Any],
        steepv: str,
        tone: float | None,
        start_year: int,
        end_year: int,
    ) -> dict[str, Any] | None:
        url = article.get("url")
        title = article.get("title") or ""
        if not url or not title:
            return None

        seendate = article.get("seendate")
        year = int(str(seendate)[:4]) if str(seendate)[:4].isdigit() else None
        if year is None or not (start_year <= year <= end_year):
            return None

        # English only. GDELT covers 100+ languages, and a headline the
        # embedder cannot model still contributes tokens — German and Spanish
        # headlines were a visible share of the noise in early runs. Filtering
        # here rather than in the query keeps the request simple and avoids
        # GDELT's operator syntax, which is easy to get subtly wrong.
        language = str(article.get("language", "")).strip().lower()
        if language and language not in ("english", "en"):
            return None

        return build_document(
            source=self.name,
            native_id=str(url),
            title=title,
            # News articles carry no abstract in artlist mode; the headline is
            # the whole signal. Domain is recorded as the venue so actor
            # dispersion (Rotolo uncertainty) still has something to measure.
            abstract="",
            published=seendate,
            url=str(url),
            venue=article.get("domain", ""),
            concepts=[c for c in (article.get("sourcecountry"), article.get("language")) if c],
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            tone=tone,
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )
