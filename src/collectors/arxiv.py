"""
src/collectors/arxiv.py — arXiv preprints.

The fastest emergence signal available for AI/CS/quantum: days from submission
to visibility, against the ~18-month lag that makes patents a trailing
indicator. For any Horizon 3 question about computational technology, arXiv is
where the signal appears first.

Atom XML, not JSON, so this collector parses rather than deserialises.
arXiv's terms ask for at least three seconds between requests; that is set as
`request_delay` in config and honoured by the base class, not negotiated.
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


@register
class ArxivCollector(Collector):
    name = "arxiv"

    def __init__(self, config: dict[str, Any], run_id: str) -> None:
        super().__init__(config, run_id)
        self.request_delay = float(self.settings.get("request_delay_seconds", 3.0))

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        max_results = int(self.settings.get("max_results_per_query", 200))
        steepv = self.steepv_for(frame)
        emitted = 0

        for start in range(0, max_results, _PAGE_SIZE):
            params = {
                "search_query": query,
                "start": start,
                "max_results": min(_PAGE_SIZE, max_results - start),
                "sortBy": "submittedDate",
                "sortOrder": "descending",
            }
            xml_text = self.fetch_text(API_URL, params)
            try:
                root = ET.fromstring(xml_text)
            except ET.ParseError as exc:
                logger.warning("arXiv returned unparseable XML for %r: %s", query, exc)
                return

            entries = root.findall("atom:entry", _NS)
            if not entries:
                return
            self.save_raw(str(frame.get("key", "query")), start // _PAGE_SIZE, xml_text)

            page_yield = 0
            for entry in entries:
                doc = self._to_document(entry, frame, steepv, start_year, end_year)
                if doc is None:
                    continue
                yield doc
                emitted += 1
                page_yield += 1
                if self.cap(emitted):
                    return

            # Results are date-descending, so once a full page falls outside the
            # window every later page will too — stop rather than page to the end.
            if page_yield == 0:
                return
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
