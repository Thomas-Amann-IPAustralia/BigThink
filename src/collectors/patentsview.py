"""
src/collectors/patentsview.py — USPTO PatentsView (US patent grants).

Patents are the lagging half of the emergence picture: an ~18-month publication
delay makes them useless for calling a fast-moving software trend, and
essential for confirming one. The Rotolo "impact" attribute and Stage 4's
patent-activity component both lean on this source.

REQUIRES A FREE API KEY. The Search API rejects unauthenticated requests
outright (measured: an empty response, not a 401, which is easy to mistake for
"no results"). Request one at https://patentsview.org/apis/keyrequest and set
PATENTSVIEW_API_KEY. This collector is disabled by default in the config so
that a run without the key is honest rather than silently patent-free.

Scope caveat worth stating in any briefing: PatentsView is US grants only.
Australian filing behaviour is in IP RAPID, and the two do not substitute for
each other. If a finding depends on Australian patent activity, IP RAPID is
the source — see PROJECT_STATE.md.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Iterator

from src.collectors.base import Collector, build_document, register
from src.errors import missing_credential_error

logger = logging.getLogger(__name__)

API_URL = "https://search.patentsview.org/api/v1/patent/"
ENV_KEY = "PATENTSVIEW_API_KEY"


@register
class PatentsViewCollector(Collector):
    name = "patentsview"
    request_delay = 1.5  # documented limit is 45 requests/minute

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        api_key = os.environ.get(ENV_KEY, "")
        if not api_key:
            raise missing_credential_error(self.name, ENV_KEY)
        self._session.headers["X-Api-Key"] = api_key

        size = min(int(self.settings.get("rows_per_query", 1000)), 1000)
        max_pages = int(self.settings.get("max_pages_per_query", 5))
        steepv = self.steepv_for(frame)

        search = {
            "_and": [
                {"_gte": {"patent_date": f"{start_year}-01-01"}},
                {"_lte": {"patent_date": f"{end_year}-12-31"}},
                {"_text_any": {"patent_title": query}},
            ]
        }
        fields = [
            "patent_id", "patent_title", "patent_abstract", "patent_date",
            "patent_type", "assignees.assignee_organization",
            "cpc_current.cpc_group_id",
        ]

        emitted = 0
        after: str | None = None
        for page in range(max_pages):
            options: dict[str, Any] = {"size": size}
            if after:
                options["after"] = after
            params = {
                "q": json.dumps(search),
                "f": json.dumps(fields),
                "o": json.dumps(options),
                "s": json.dumps([{"patent_id": "asc"}]),
            }
            payload = self.fetch_json(API_URL, params)
            patents = payload.get("patents") or []
            if not patents:
                return
            self.save_raw(str(frame.get("key", "query")), page, payload)

            for patent in patents:
                doc = self._to_document(patent, frame, steepv)
                if doc is None:
                    continue
                yield doc
                emitted += 1
                if self.cap(emitted):
                    return

            after = patents[-1].get("patent_id")
            if len(patents) < size or not after:
                return

    def _to_document(
        self, patent: dict[str, Any], frame: dict[str, Any], steepv: str
    ) -> dict[str, Any] | None:
        patent_id = patent.get("patent_id")
        title = patent.get("patent_title") or ""
        if not patent_id or not title:
            return None

        assignees = [
            a.get("assignee_organization", "")
            for a in (patent.get("assignees") or [])
            if a.get("assignee_organization")
        ]
        cpc = [
            c.get("cpc_group_id", "")
            for c in (patent.get("cpc_current") or [])
            if c.get("cpc_group_id")
        ]

        return build_document(
            source=self.name,
            native_id=str(patent_id),
            title=title,
            abstract=patent.get("patent_abstract") or "",
            published=patent.get("patent_date"),
            url=f"https://patents.google.com/patent/US{patent_id}",
            venue="USPTO",
            # Assignees are the actors here; they feed the same dispersion
            # measure that institutions feed for research documents.
            institutions=assignees,
            concepts=cpc,
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )
