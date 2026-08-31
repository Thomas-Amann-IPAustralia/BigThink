"""
src/collectors/datagovau.py — data.gov.au (CKAN) dataset metadata.

The policy-salience signal, and the discovery path to IP Australia's own
published assets: IP RAPID, IPGOD and IPLoRD are all CKAN packages here, which
means the asset inventory can be checked against what is actually published
rather than what is remembered.

Endpoint note: the CKAN API sits under /data/api/3/action/, not /api/3/action/.
The shorter path returns 404, which is easy to misread as "the API is gone".

A dataset's publication date is the weakest of the time signals this pipeline
uses — CKAN records when a dataset was registered, which can lag the policy it
serves by years. Treat data.gov.au as evidence of salience, not of timing.
"""

from __future__ import annotations

import logging
from typing import Any, Iterator

from src.collectors.base import Collector, build_document, register

logger = logging.getLogger(__name__)

API_URL = "https://data.gov.au/data/api/3/action/package_search"


@register
class DataGovAuCollector(Collector):
    name = "datagovau"
    request_delay = 0.5

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        rows = min(int(self.settings.get("rows_per_query", 50)), 100)
        steepv = self.steepv_for(frame)

        payload = self.fetch_json(API_URL, {"q": query, "rows": rows})
        if not payload.get("success"):
            self.note_incident("CKAN returned success=false")
            logger.warning("CKAN returned success=false for %r", query)
            return

        results = (payload.get("result") or {}).get("results") or []
        if not results:
            return
        self.save_raw(str(frame.get("key", "query")), 0, payload)

        emitted = 0
        for package in results:
            doc = self._to_document(package, frame, steepv, start_year, end_year)
            if doc is None:
                continue
            yield doc
            emitted += 1
            if self.cap(emitted):
                return

    def _to_document(
        self,
        package: dict[str, Any],
        frame: dict[str, Any],
        steepv: str,
        start_year: int,
        end_year: int,
    ) -> dict[str, Any] | None:
        name = package.get("name")
        title = package.get("title") or ""
        if not name or not title:
            return None

        # Prefer last-modified over created: a dataset actively maintained this
        # year is a live policy signal, one registered in 2018 and untouched
        # since is not.
        modified = package.get("metadata_modified") or package.get("metadata_created")
        year = int(str(modified)[:4]) if str(modified)[:4].isdigit() else None
        if year is None or not (start_year <= year <= end_year):
            return None

        org = (package.get("organization") or {}).get("title", "")
        tags = [t.get("display_name", "") for t in (package.get("tags") or [])]

        return build_document(
            source=self.name,
            native_id=str(name),
            title=title,
            abstract=(package.get("notes") or "")[:4000],
            published=modified,
            url=f"https://data.gov.au/data/dataset/{name}",
            venue=org,
            institutions=[org] if org else [],
            concepts=[t for t in tags if t],
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )
