"""
src/collectors/arxiv.py — arXiv preprints.

The fastest emergence signal available for AI/CS/quantum: days from submission
to visibility, against the ~18-month lag that makes patents a trailing
indicator. For any Horizon 3 question about computational technology, arXiv is
where the signal appears first.

Atom XML, not JSON, so this collector parses rather than deserialises.
arXiv's terms ask for at least three seconds between requests; that is set as
`request_delay` in config and honoured by the base class, not negotiated.

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
            xml_text = self.fetch_text(API_URL, params)
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
