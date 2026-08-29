"""
src/collectors/gdelt.py — GDELT news attention.

The attention/salience proxy behind Stage 4's opportunity index: worldwide news
in 100+ languages, updated every 15 minutes, free and keyless.

OPERATIONAL REALITY (measured 2026-08-29). GDELT's DOC API rate-limits hard by
source IP and gives no polite error when it does — the connection is simply
dropped mid-response. From a shared or cloud IP (GitHub Actions included) a
majority of requests can fail this way even at six seconds apart. Two
consequences shaped this collector:

  * `timespan` works; `startdatetime`/`enddatetime` were unreliable in testing,
    so the window is expressed in months back from now.
  * Failure is a logged skip, never an exception that ends the scan. A GDELT
    outage should cost you the attention component of the index, not the run.

Article records carry no per-article tone. Tone is fetched once per query via
`mode=timelinetone` and applied as a query-level average — an honest
approximation, and recorded as such on every document.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from src.collectors.base import Collector, build_document, register
from src.errors import BigThinkError

logger = logging.getLogger(__name__)

API_URL = "https://api.gdeltproject.org/api/v2/doc/doc"


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
        steepv = self.steepv_for(frame)

        params = {
            "query": query,
            "mode": "artlist",
            "maxrecords": max_records,
            "format": "json",
            "timespan": f"{months}m",
        }
        try:
            payload = self.fetch_json(API_URL, params)
        except BigThinkError as exc:
            logger.warning(
                "GDELT unavailable for %r (%s) — skipping. GDELT rate-limits by IP and "
                "drops connections without an error code; this is expected from shared "
                "runners and costs only the attention signal for this frame.",
                frame.get("key", query), exc,
            )
            return

        articles = payload.get("articles") or []
        if not articles:
            return
        self.save_raw(str(frame.get("key", "query")), 0, payload)

        tone = self._average_tone(query, months)

        emitted = 0
        for article in articles:
            doc = self._to_document(article, frame, steepv, tone, start_year, end_year)
            if doc is None:
                continue
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
        except BigThinkError:
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
