"""
src/collectors/openalex.py — OpenAlex works (research publications).

The primary research-trend source: ~477M works, full metadata, citation counts,
institution affiliations. Feeds growth curves, the Rotolo impact attribute, and
the actor-dispersion term behind Rotolo uncertainty.

BUDGET WARNING (verified 2026-08-29). OpenAlex is no longer simply "free, no
key". Requests are metered in dollars, and an unauthenticated caller gets a
small daily allowance that resets at midnight UTC. From a shared or cloud IP
that allowance is typically already spent:

    {"error": "Rate limit exceeded",
     "message": "Insufficient budget. This request costs $0.001 but you only
                 have $0 remaining. Resets at midnight UTC."}

`mailto` alone does not fix this. Set OPENALEX_API_KEY to get the per-key
allowance. Without a key this collector will usually log a skip rather than
return data — which is why it fails soft: an exhausted budget must not take
down a scan that has five other working sources.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Iterator

from src.collectors.base import Collector, build_document, register
from src.errors import RetryableError

logger = logging.getLogger(__name__)

API_URL = "https://api.openalex.org/works"


@register
class OpenAlexCollector(Collector):
    name = "openalex"
    request_delay = 0.15  # polite pool allows ~10 rps; stay well under

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        per_page = min(int(self.settings.get("per_page", 200)), 200)
        max_pages = int(self.settings.get("max_pages_per_query", 5))
        min_year = max(int(self.settings.get("min_year", start_year)), start_year)
        min_relative_score = float(self.settings.get("min_relative_score", 0.4))
        steepv = self.steepv_for(frame)
        api_key = os.environ.get("OPENALEX_API_KEY", "")

        cursor = "*"
        emitted = 0
        top_score: float | None = None
        for page in range(max_pages):
            params: dict[str, Any] = {
                "search": query,
                "filter": f"from_publication_date:{min_year}-01-01,"
                          f"to_publication_date:{end_year}-12-31",
                "per-page": per_page,
                "cursor": cursor,
                "mailto": self.contact_email,
            }
            if api_key:
                params["api_key"] = api_key

            try:
                payload = self.fetch_json(API_URL, params)
            except RetryableError as exc:
                # A 429 here is nearly always the daily budget, not a transient
                # spike; retrying inside this run cannot help.
                logger.warning(
                    "OpenAlex unavailable for %r (%s). Set OPENALEX_API_KEY to raise the "
                    "budget. Continuing without this source.",
                    frame.get("key", query), exc,
                )
                return

            results = payload.get("results") or []
            if not results:
                return
            self.save_raw(f"{frame.get('key', 'query')}", page, payload)

            # OpenAlex cursor paging does preserve the relevance sort (unlike
            # Crossref's), but a `search` still returns a long weak tail. Cut it
            # at a fraction of this query's own top score, as Crossref does.
            if top_score is None:
                top_score = float(results[0].get("relevance_score") or 0.0)
            floor = (top_score or 0.0) * min_relative_score

            for work in results:
                score = work.get("relevance_score")
                if score is not None and float(score) < floor:
                    return
                doc = self._to_document(work, frame, steepv)
                if doc is None:
                    continue
                yield doc
                emitted += 1
                if self.cap(emitted):
                    return

            cursor = (payload.get("meta") or {}).get("next_cursor")
            if not cursor:
                return

    # -- normalisation ---------------------------------------------------
    def _to_document(
        self, work: dict[str, Any], frame: dict[str, Any], steepv: str
    ) -> dict[str, Any] | None:
        native_id = work.get("id") or work.get("doi")
        title = work.get("display_name") or work.get("title") or ""
        if not native_id or not title:
            return None

        authorships = work.get("authorships") or []
        authors = [
            (a.get("author") or {}).get("display_name", "")
            for a in authorships[:20]
        ]
        institutions = sorted(
            {
                inst.get("display_name", "")
                for a in authorships
                for inst in (a.get("institutions") or [])
                if inst.get("display_name")
            }
        )
        # `topics` superseded `concepts` in the OpenAlex schema; read both so a
        # schema change on their side degrades rather than empties this field.
        concepts = [
            t.get("display_name", "")
            for t in (work.get("topics") or work.get("concepts") or [])[:8]
            if t.get("display_name")
        ]

        source_name = ""
        primary = work.get("primary_location") or {}
        if isinstance(primary.get("source"), dict):
            source_name = primary["source"].get("display_name") or ""

        return build_document(
            source=self.name,
            native_id=str(native_id),
            title=title,
            abstract=_reconstruct_abstract(work.get("abstract_inverted_index")),
            published=work.get("publication_date") or work.get("publication_year"),
            url=work.get("doi") or str(native_id),
            venue=source_name,
            authors=[a for a in authors if a],
            institutions=institutions,
            concepts=concepts,
            citation_count=work.get("cited_by_count", 0),
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )


def _reconstruct_abstract(inverted: dict[str, list[int]] | None) -> str:
    """Rebuild plain text from OpenAlex's inverted index.

    OpenAlex stores abstracts as {word: [positions]} for copyright reasons.
    Without this the abstract field is empty and every research document
    contributes only its title to topic formation.
    """
    if not inverted:
        return ""
    positions: list[tuple[int, str]] = [
        (pos, word) for word, pos_list in inverted.items() for pos in pos_list
    ]
    if not positions:
        return ""
    positions.sort()
    return " ".join(word for _, word in positions)
