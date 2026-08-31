"""
src/collectors/arxiv.py — arXiv preprints.

The fastest emergence signal available for AI/CS/quantum: days from submission
to visibility, against the ~18-month lag that makes patents a trailing
indicator. For any Horizon 3 question about computational technology, arXiv is
where the signal appears first.

Atom XML, not JSON, so this collector parses rather than deserialises.

RATE LIMITING. arXiv's terms ask for at least three seconds between requests,
and `request_delay_seconds` honours that. It is a floor, not the actual limit:
export.arxiv.org evidently enforces something stricter under load, and from a
shared GitHub Actions IP the 2026-08-31 run took 28 HTTP 429s and lost six of
nine frames outright. Two mechanisms below, and they solve different halves of
that:

  * `_collect_year` contains a failure to one year (see there). A frame that
    lost 2024 is worth far more than a frame that lost everything, and losing
    the frame is what actually happened.
  * `_note_rate_limit` widens the delay for the rest of the run whenever a 429
    arrives. Published limits do not describe what a shared IP actually gets,
    so the delay is measured rather than configured: it costs nothing when
    arXiv is healthy and climbs only when arXiv says to. Without it, per-year
    containment alone just spends the retry budget 81 times over — nine frames
    by nine years — against a server that is refusing all of them.

SAMPLING (this is a methodological point, not an implementation detail).
The obvious way to query arXiv is to sort by submission date descending and
take the first N results. That produces a corpus with no history: on the
2026-08-29 run it returned 1,449 arXiv documents dated 2026 and none before
2022, which then read downstream as an explosion of activity in 2026 rather
than as what it was — a sampling artefact.

This collector therefore queries one year at a time, with a per-year quota, so
the resulting corpus can support a growth curve. It costs more requests and is
the only way the source is usable for emergence detection at all.
"""

from __future__ import annotations

import logging
import xml.etree.ElementTree as ET
from typing import Any, Iterator

from src.collectors.base import Collector, build_document, register
from src.errors import BigThinkError, RetryableError

logger = logging.getLogger(__name__)

API_URL = "https://export.arxiv.org/api/query"
_NS = {"atom": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
_PAGE_SIZE = 100
# Floor on the per-year quota. A year that returns only a handful of papers is
# information too — it is the low end of the growth curve.
_MIN_PER_YEAR = 25


@register
class ArxivCollector(Collector):
    name = "arxiv"

    def __init__(self, config: dict[str, Any], run_id: str) -> None:
        super().__init__(config, run_id)
        self.request_delay = float(self.settings.get("request_delay_seconds", 3.0))
        # Ceiling on the adaptive delay. One instance serves every frame, so
        # this budget is spent across the whole run: nine arXiv frames over the
        # 2018-2026 window is 81 year-requests, which is ~4 minutes at 3 s and
        # ~27 at 20 s. Affordable against a 240-minute job that currently takes
        # 164 (PROJECT_STATE issue 17) — and far cheaper than the alternative,
        # which is losing the source and re-running the scan.
        self._max_request_delay = float(
            self.settings.get("max_request_delay_seconds", 20.0)
        )
        self._rate_limit_backoff = float(
            self.settings.get("rate_limit_backoff_factor", 1.5)
        )
        self._rate_limit_hits = 0

    # -- adaptive throttling ---------------------------------------------
    def _note_rate_limit(self, exc: BigThinkError) -> bool:
        """Widen the inter-request delay after a 429. True if this was one.

        Deliberately one-way: the delay climbs and never falls back over the
        run. A source that has just rate-limited us is not evidence that it has
        stopped, and the cost of being wrong is asymmetric — a slightly slow
        frame against a lost one.

        Kept on the collector instance, which Stage 1 reuses across frames, so
        a frame that hits the limit slows the frames after it too. That is the
        point: the throttle is per IP, not per query, and the 2026-08-31 run
        lost frames 4 through 9 to a limit that frames 1 through 3 had already
        discovered.
        """
        if not isinstance(exc, RetryableError):
            return False
        if exc.context.get("status_code") != 429:
            return False

        self._rate_limit_hits += 1
        previous = self.request_delay
        self.request_delay = min(
            self.request_delay * self._rate_limit_backoff, self._max_request_delay
        )
        if self.request_delay > previous:
            logger.warning(
                "arXiv rate-limited this runner (%d time(s) so far); widening the "
                "request delay %.1fs -> %.1fs for the rest of the run.",
                self._rate_limit_hits, previous, self.request_delay,
            )
        return True

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        total_budget = int(self.settings.get("max_results_per_query", 200))
        years = list(range(start_year, end_year + 1))
        # Spread the budget evenly across years so the corpus carries a history
        # rather than a snapshot of the last few months.
        per_year = max(int(self.settings.get("max_results_per_year", 0))
                       or (total_budget // max(len(years), 1)), _MIN_PER_YEAR)
        steepv = self.steepv_for(frame)
        emitted = 0

        for year in years:
            for doc in self._collect_year(query, frame, steepv, year, per_year):
                yield doc
                emitted += 1
                if self.cap(emitted):
                    return

    def _collect_year(
        self, query: str, frame: dict[str, Any], steepv: str, year: int, quota: int
    ) -> Iterator[dict[str, Any]]:
        """Fetch up to *quota* documents submitted in *year*."""
        dated = f"{query} AND submittedDate:[{year}0101 TO {year}1231]"
        for start in range(0, quota, _PAGE_SIZE):
            params = {
                "search_query": dated,
                "start": start,
                "max_results": min(_PAGE_SIZE, quota - start),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            try:
                xml_text = self.fetch_text(API_URL, params)
            except BigThinkError as exc:
                # One year, not the frame. `fetch_text` is called outside any
                # try until now, so an exhausted retry budget propagated out of
                # `collect()` and took every remaining year of the frame with
                # it — which is how the 2026-08-31 run turned 28 rate-limit
                # responses into six lost frames and 1,339 lost documents.
                #
                # The same reasoning as GDELT's per-window catch, and the same
                # reason it cannot simply raise: `collect` is a generator drained
                # with `list()`, so raising after the first yield discards the
                # documents already produced. A partial frame is worth keeping;
                # a silent one is not, so the loss is recorded as an incident
                # and Stage 1 logs the frame `partial` rather than `success`.
                self._note_rate_limit(exc)
                self.note_incident(f"{year}: {exc}")
                logger.warning(
                    "arXiv unavailable for %r (%d): %s — continuing with the "
                    "remaining years. This frame's growth curve will have a hole.",
                    frame.get("key", query), year, exc,
                )
                return

            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError as exc:
                # This year is lost and the remaining years still run, because
                # each year is its own generator. Recorded so the frame is
                # logged `partial` — a corpus missing one year of arXiv history
                # is a growth curve fitted to a hole, and nothing downstream
                # can tell unless the gap is written down here.
                self.note_incident(f"{year}: unparseable XML ({exc})")
                logger.warning(
                    "arXiv returned unparseable XML for %r (%d): %s", query, year, exc
                )
                return

            entries = root.findall("atom:entry", _NS)
            if not entries:
                return
            self.save_raw(f"{frame.get('key', 'query')}_{year}", start // _PAGE_SIZE, xml_text)

            for entry in entries:
                doc = self._to_document(entry, frame, steepv, year, year)
                if doc is not None:
                    yield doc

            if len(entries) < params["max_results"]:
                return

    def _to_document(
        self,
        entry: ET.Element,
        frame: dict[str, Any],
        steepv: str,
        start_year: int,
        end_year: int,
    ) -> dict[str, Any] | None:
        native_id = _text(entry, "atom:id")
        title = _text(entry, "atom:title")
        if not native_id or not title:
            return None

        published = _text(entry, "atom:published")
        year = int(published[:4]) if published[:4].isdigit() else None
        if year is None or not (start_year <= year <= end_year):
            return None

        categories = [
            c.attrib.get("term", "")
            for c in entry.findall("atom:category", _NS)
            if c.attrib.get("term")
        ]
        authors = [
            _text(a, "atom:name")
            for a in entry.findall("atom:author", _NS)[:20]
        ]

        return build_document(
            source=self.name,
            native_id=native_id,
            title=" ".join(title.split()),
            abstract=" ".join(_text(entry, "atom:summary").split()),
            published=published,
            url=native_id,
            venue=_text(entry, "arxiv:journal_ref") or "arXiv",
            authors=[a for a in authors if a],
            concepts=categories,
            # arXiv publishes no citation counts. Left at 0 deliberately —
            # Stage 2 percentile-ranks impact within a source, so preprints are
            # compared with preprints, not with cited journal articles.
            citation_count=0,
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )


def _text(element: ET.Element, path: str) -> str:
    node = element.find(path, _NS)
    return (node.text or "").strip() if node is not None and node.text else ""
