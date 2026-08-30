"""
src/collectors/crossref.py — Crossref works (DOI-level scholarly metadata).

Broader than OpenAlex in coverage of the grey and policy-adjacent literature
(reports, book chapters, posted content), and — unlike OpenAlex — genuinely
free and unmetered on the polite pool. That makes it the dependable backbone
of the research signal, with OpenAlex layered on top when a key is available.

Abstracts arrive as JATS XML embedded in a JSON string, so they are stripped
to plain text here rather than downstream.

PAGING (measured 2026-08-29): Crossref supports cursor-based deep paging, but
using it DESTROYS relevance ordering — a `query.title` search that returns
results scoring 31.7 without a cursor returns results scoring 7.4 with one.
Cursor paging is for exhaustive harvesting, not for search. This collector
therefore uses offset paging, which preserves the relevance sort, and caps
depth accordingly. Do not "fix" this back to a cursor.

RELEVANCE FLOOR: Crossref scores are not comparable across queries, so an
absolute cut-off cannot work. Instead each result set is cut at a fraction of
its own top score (`min_relative_score`). Without it, a query with few genuine
matches pads the corpus with whatever else shares a word, and those documents
go on to form topics of their own.
"""

from __future__ import annotations

import html
import logging
import re
from typing import Any, Iterator

from src.collectors.base import Collector, build_document, register

logger = logging.getLogger(__name__)

API_URL = "https://api.crossref.org/works"
_TAG_RE = re.compile(r"<[^>]+>")


@register
class CrossrefCollector(Collector):
    name = "crossref"
    request_delay = 0.2

    def __init__(self, config: dict[str, Any], run_id: str) -> None:
        super().__init__(config, run_id)
        self._excluded_types = {
            str(t).strip().lower() for t in (self.settings.get("exclude_types") or [])
        }
        self._excluded_titles = {
            str(t).strip().lower() for t in (self.settings.get("exclude_titles") or [])
        }

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        rows = min(int(self.settings.get("rows_per_query", 100)), 1000)
        max_pages = int(self.settings.get("max_pages_per_query", 2))
        min_relative_score = float(self.settings.get("min_relative_score", 0.4))
        steepv = self.steepv_for(frame)

        emitted = 0
        floor: float | None = None
        for page in range(max_pages):
            params = {
                # query.bibliographic searches title, container and author
                # metadata — tighter than the catch-all `query`, which matches
                # loosely enough to return unrelated engineering papers.
                "query.bibliographic": query,
                "rows": rows,
                "offset": page * rows,
                "filter": f"from-pub-date:{start_year}-01-01,until-pub-date:{end_year}-12-31",
                "mailto": self.contact_email,
                "select": "DOI,title,abstract,issued,container-title,"
                          "is-referenced-by-count,author,subject,URL,type,score",
            }
            payload = self.fetch_json(API_URL, params)
            message = payload.get("message") or {}
            items = message.get("items") or []
            if not items:
                return
            self.save_raw(str(frame.get("key", "query")), page, payload)

            # Anchored on rank 1 — the maximum — unlike OpenAlex. Crossref's
            # `score` decays gently enough that the floor rarely binds here:
            # on the 2026-08-30 run Crossref returned 197.8 records per query
            # against a 200 ceiling, so the page cap, not the floor, was the
            # limit. Left at 1 so this source's behaviour is unchanged by the
            # OpenAlex fix; see Collector.relevance_floor for why OpenAlex
            # cannot use the same anchor.
            if floor is None:
                floor = self.relevance_floor(
                    [i.get("score") for i in items],
                    min_relative=min_relative_score,
                    anchor_rank=int(self.settings.get("relevance_anchor_rank", 1)),
                )

            for item in items:
                if float(item.get("score") or 0.0) < floor:
                    # Relevance is monotonically decreasing, so the first item
                    # below the floor ends the useful part of the result set.
                    return
                doc = self._to_document(item, frame, steepv)
                if doc is None:
                    continue
                yield doc
                emitted += 1
                if self.cap(emitted):
                    return

            if len(items) < rows:
                return

    def _to_document(
        self, item: dict[str, Any], frame: dict[str, Any], steepv: str
    ) -> dict[str, Any] | None:
        doi = item.get("DOI")
        titles = item.get("title") or []
        title = titles[0] if titles else ""
        if not doi or not title:
            return None

        # Crossref registers peer-review reports, component parts and book
        # back-matter as first-class records with their own DOIs. They are not
        # papers, and `native_id` deduplication cannot remove them because the
        # identifiers genuinely are distinct — while the reviews of one paper
        # all carry that paper's title, so they cluster more tightly than any
        # real topic. Filtering here rather than downstream is deliberate: by
        # the time a cluster exists the damage is a topic, not a document.
        if str(item.get("type", "")).strip().lower() in self._excluded_types:
            return None
        if _strip_markup(title).strip().lower() in self._excluded_titles:
            return None

        authors = [
            " ".join(filter(None, [a.get("given"), a.get("family")]))
            for a in (item.get("author") or [])[:20]
        ]
        containers = item.get("container-title") or []

        return build_document(
            source=self.name,
            native_id=str(doi),
            title=_strip_markup(title),
            abstract=_strip_markup(item.get("abstract", "")),
            published=_issued_date(item.get("issued")),
            url=item.get("URL") or f"https://doi.org/{doi}",
            venue=containers[0] if containers else str(item.get("type", "")),
            authors=[a for a in authors if a],
            concepts=item.get("subject") or [],
            citation_count=item.get("is-referenced-by-count", 0),
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )


def _issued_date(issued: dict[str, Any] | None) -> str | None:
    """Crossref `issued` is {'date-parts': [[YYYY, MM, DD]]} with MM/DD optional."""
    if not issued:
        return None
    parts = (issued.get("date-parts") or [[]])[0]
    if not parts:
        return None
    year = parts[0]
    month = parts[1] if len(parts) > 1 else 1
    day = parts[2] if len(parts) > 2 else 1
    return f"{year:04d}-{month:02d}-{day:02d}"


def _strip_markup(text: str) -> str:
    """JATS/HTML to plain text. Entities are unescaped before tags are removed."""
    if not text:
        return ""
    return " ".join(_TAG_RE.sub(" ", html.unescape(str(text))).split())
