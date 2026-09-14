"""
src/collectors/oecd.py — OECD, as one source with two channels.

The OECD is the closest thing this scan has to a standing, comparative view of
what governments are about to be asked to do. Two very different endpoints
carry that signal, and both are registered here under the single source name
`oecd` so a reader of the shortlist sees one provenance rather than two.

CHANNEL 1 — PUBLICATIONS (RSS).  `https://api.oecd.org/webcms/search/rss`
is the OECD web CMS search, rendered as RSS. Each item is a published OECD
report with a title, a real 200-400 character abstract, a permalink, a
publication date and policy-area categories — the richest per-record text of
any keyless source in this pipeline, and the longest reach: one frame's query
came back spread evenly across 2018-2026, where arXiv needs per-year quotas to
manage that and GDELT cannot do it at all.

WHAT THIS SEARCH ENGINE ACTUALLY DOES, AND THE FILTER THAT ANSWERS IT
(measured 2026-09-14, publications facet, 100 items per query)

It is a loose matcher with no exposed relevance score, and the feed is sorted
by date, so whatever ranking exists is discarded before it reaches us. It has
no phrase operator either — quotes and `AND` change nothing.

  searchTerm             items   mentioning a query word in title or abstract
  counterfeit               37   31
  trademark                 17    9
  productivity             100   29
  artificial intelligence  100   18
  patent                   100    4
  intellectual property    100    0   <- toxicology, AI skills, housing

Single distinctive words behave; a phrase whose second word is common
("property", "economy") returns that common word's literature. Four of 100 for
`patent`, none of 100 for `intellectual property`.

The pipeline embeds title + abstract and nothing else, so an item that matched
on something outside that text arrives in the corpus looking unrelated to the
frame that collected it, and clusters accordingly. The filter therefore asks
the only question that matters here: is the frame's query visible in the text
this pipeline will actually read? Items failing `rss_min_term_matches` are
dropped. Matching is by substring, which stems for free in the direction that
helps — `counterfeit` finds "counterfeiting", `biodiversity` finds
"biodiversity-related".

The consequence for the scan frame: **write an `oecd:` query as one or two
DISTINCTIVE words.** A generic second word does not narrow this engine, it
replaces the query.

CHANNEL 2 — STATISTICS (SDMX).  `https://sdmx.oecd.org/public/rest` is the
OECD Data API described in docs/OECD-Data-API-documentation.md. It serves
statistical *observations*, which are not documents and would be worthless in a
topic model — a row of (country, year, value) has no text to cluster. What it
also serves, and what this collector actually reads, is the **catalogue**: 1,546
dataflows, each with a name and a descriptive abstract, plus a content
constraint carrying the date that dataflow was last released and how many
observations it holds.

That is the same kind of evidence data.gov.au gives and should be read the same
way: *the OECD has stood up a statistical series on this* is a claim about
institutional salience, not about when the underlying phenomenon started. The
release dates cluster hard in the most recent year or two because they record
the latest refresh, not first publication. Treat OECD statistics as salience,
never as timing.

WHY THE CATALOGUE IS FETCHED WHOLE, ONCE PER RUN

The API allows 60 downloads an hour and its own documentation asks callers to
"run larger, consolidated queries whenever possible" and to cache locally. Two
requests — one for every dataflow, one for every content constraint — cover
every frame in the scan frame, however many there are, and the matching is then
done in this process against the cached catalogue. A per-frame query would have
cost one request per frame for a worse result, and a scan frame of any size
would eventually trip the limit.

QUERY FORMS

A frame's `oecd:` query is read one of two ways:

  * A plain string        — used as the RSS `searchTerm`, AND matched against
                            the cached dataflow catalogue. Both channels run.
  * An `https://…` URL    — used verbatim as the feed, paginated in place.
                            This is how a hand-built faceted feed from the OECD
                            search UI is pinned into the scan frame exactly as
                            it was composed. The SDMX channel is skipped: a
                            faceted feed is a deliberate slice, and silently
                            widening it back out with a keyword match over the
                            statistics catalogue would misrepresent it.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Iterator
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from xml.etree import ElementTree

from src.collectors.base import Collector, build_document, register
from src.errors import BigThinkError, malformed_response_error

logger = logging.getLogger(__name__)

RSS_URL = "https://api.oecd.org/webcms/search/rss"
SDMX_DATAFLOW_URL = "https://sdmx.oecd.org/public/rest/dataflow/all/all/latest"
SDMX_CONSTRAINT_URL = "https://sdmx.oecd.org/public/rest/contentconstraint/all/all/latest"

#: SDMX serves several mutually incompatible JSON schemas from one URL and
#: chooses between them on the Accept header — with a bare `application/json`
#: the same endpoint returns structure message 2.0.0, which nests things
#: differently. Pin the version this parser was written against rather than
#: taking whatever the server defaults to this month.
SDMX_STRUCTURE_JSON = "application/vnd.sdmx.structure+json; charset=utf-8; version=1.0"

#: `urn:sdmx:org.sdmx.infomodel.datastructure.Dataflow=OECD.STI.STP:DSD_X@DF_Y(1.0)`
_DATAFLOW_URN = re.compile(r"Dataflow=([^:]+):([^(]+)\((.+)\)$")

_TAGS = re.compile(r"<[^>]+>")
_WHITESPACE = re.compile(r"\s+")

#: Query syntax belonging to the *other* sources' search engines. A frame's
#: query is written for an API; this channel is a local match over a catalogue,
#: so the operators have to come out before the words can be used as words.
_FIELD_PREFIX = re.compile(r"\b[a-z_]{2,12}:", re.IGNORECASE)
_BOOLEAN = {"and", "or", "not"}
_STOPWORDS = {
    "the", "for", "with", "from", "into", "that", "this", "are", "was", "were",
    "its", "their", "how", "why", "who", "all", "any", "new",
}


def _clean_text(value: str | None) -> str:
    """Plain text from a field that may carry HTML, entities or both.

    Dataflow descriptions are authored in the OECD's CMS and arrive as HTML
    fragments — headings, lists, styled divs. Left in place they would put
    `<div class=...>` into the embedding and, worse, give every dataflow from
    one directorate the same markup boilerplate to cluster on.
    """
    if not value:
        return ""
    text = _TAGS.sub(" ", str(value))
    text = html.unescape(text)
    return _WHITESPACE.sub(" ", text).strip()


def _rss_date(value: str | None) -> date | None:
    """RFC 822 (`Tue, 08 Sep 2026 22:00:00 GMT`) — the RSS date format.

    `base.parse_date` covers the ISO-ish and compact forms the JSON APIs use
    and would fall through this one to None, which would silently drop every
    OECD publication out of the year window.
    """
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(str(value).strip())
    except (TypeError, ValueError):
        return None
    return parsed.date() if parsed is not None else None


def _sdmx_date(value: str | None) -> date | None:
    """`validFrom`, e.g. `2026-03-11T09:14:02Z`."""
    if not value:
        return None
    try:
        return datetime.strptime(str(value)[:10], "%Y-%m-%d").date()
    except ValueError:
        return None


def query_terms(query: str) -> list[str]:
    """The words of a scan-frame query, with search-engine syntax removed.

    Frame queries are written for the source they address — `abs:(patent AND
    retrieval)` for arXiv, quoted phrases and OR groups for OpenAlex. Matching
    against a local catalogue means treating them as a bag of words, so the
    field prefixes, operators and punctuation come out first. Order is
    preserved and duplicates dropped so the result is deterministic.
    """
    text = _FIELD_PREFIX.sub(" ", str(query or "").lower())
    words = re.findall(r"[a-z][a-z0-9-]{2,}", text)
    terms: list[str] = []
    for word in words:
        if word in _BOOLEAN or word in _STOPWORDS or word in terms:
            continue
        terms.append(word)
    return terms


def _with_paging(url: str, page: int, page_size: int) -> str:
    """A feed URL with paging applied, preserving every other parameter.

    `facets` legitimately repeats — the OECD search UI emits one parameter per
    facet and the API comma-joins them server-side — so the query string is
    rebuilt from a list of pairs rather than a dict, which would keep only the
    last facet and quietly widen the feed.
    """
    parts = urlsplit(url)
    pairs = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True)
             if k not in ("page", "pageSize")]
    pairs += [("pageSize", str(page_size)), ("page", str(page))]
    return urlunsplit(parts._replace(query=urlencode(pairs)))


@register
class OecdCollector(Collector):
    """OECD publications (RSS) and statistical dataflows (SDMX)."""

    name = "oecd"
    # Neither endpoint publishes a per-second limit; the SDMX API documents 60
    # downloads an hour, and this collector spends two of them for the whole
    # run. One second between requests is politeness, not a measured ceiling.
    request_delay = 1.0

    def __init__(self, config: dict[str, Any], run_id: str) -> None:
        super().__init__(config, run_id)
        # Fetched at most once per run and reused across every frame. None
        # means "not attempted yet"; an empty list means "attempted and
        # unavailable", which must not trigger a second attempt per frame.
        self._catalogue: list[dict[str, Any]] | None = None

    # -- entry point -------------------------------------------------------

    def collect(
        self, query: str, frame: dict[str, Any], start_year: int, end_year: int
    ) -> Iterator[dict[str, Any]]:
        steepv = self.steepv_for(frame)
        frame_key = str(frame.get("key", "query"))
        verbatim_feed = str(query).strip().lower().startswith(("http://", "https://"))

        emitted = 0
        for doc in self._collect_rss(query, frame, steepv, start_year, end_year):
            yield doc
            emitted += 1
            if self.cap(emitted):
                return

        if verbatim_feed or not self._sdmx_enabled():
            return
        for doc in self._collect_sdmx(query, frame, steepv, start_year, end_year):
            yield doc
            emitted += 1
            if self.cap(emitted):
                return
        logger.debug("[%s/oecd] %d document(s)", frame_key, emitted)

    # -- channel 1: publications ------------------------------------------

    def _collect_rss(
        self,
        query: str,
        frame: dict[str, Any],
        steepv: str,
        start_year: int,
        end_year: int,
    ) -> Iterator[dict[str, Any]]:
        page_size = min(int(self.settings.get("rss_page_size", 100)), 100)
        max_pages = max(int(self.settings.get("rss_max_pages_per_query", 2)), 1)
        frame_key = str(frame.get("key", "query"))
        query = str(query).strip()

        if query.lower().startswith(("http://", "https://")):
            base_url, params = query, None
        else:
            base_url = RSS_URL
            params = [
                ("siteName", str(self.settings.get("rss_site_name", "oecd"))),
                ("interfaceLanguage", str(self.settings.get("rss_interface_language", "en"))),
                ("searchTerm", query),
            ]
            for facet in self.settings.get("rss_hidden_facets", []) or []:
                params.append(("hiddenFacets", str(facet)))
            for facet in self.settings.get("rss_facets", []) or []:
                params.append(("facets", str(facet)))

        # Empty for a verbatim faceted feed, which carries no search term: every
        # item in it was selected by the facets the frame author wrote, and
        # there is nothing to check them against. `_is_relevant` passes them
        # all, which is the correct reading of a deliberate slice.
        terms = [] if params is None else query_terms(query)

        seen: set[str] = set()
        dropped = 0
        for page in range(max_pages):
            if params is None:
                url, request_params = _with_paging(base_url, page, page_size), None
            else:
                url = base_url
                request_params = params + [("pageSize", str(page_size)), ("page", str(page))]

            try:
                body = self.fetch_text(
                    url,
                    request_params,
                    headers={"Accept": "application/rss+xml, application/xml"},
                )
                items = _parse_rss(body, url, self.name)
            except BigThinkError as exc:
                # One page, not the frame, and not the SDMX channel that runs
                # after it. The first page is the one that matters; losing page
                # two costs history, not the signal.
                self.note_incident(f"rss page {page + 1}/{max_pages}: {exc}")
                logger.warning("[%s/oecd] RSS page %d unavailable: %s", frame_key, page + 1, exc)
                break

            if not items:
                break
            self.save_raw(f"{frame_key}_rss", page, {"url": url, "items": items})

            for item in items:
                doc = self._rss_document(item, frame, steepv, start_year, end_year)
                if doc is None or doc["doc_id"] in seen:
                    continue
                if not self._is_relevant(doc, terms):
                    dropped += 1
                    continue
                seen.add(doc["doc_id"])
                yield doc

            if len(items) < page_size:
                break  # last page

        if dropped:
            logger.info(
                "[%s/oecd] kept %d publication(s), dropped %d whose title and abstract "
                "carry none of the query.", frame_key, len(seen), dropped,
            )

    def _is_relevant(self, doc: dict[str, Any], terms: list[str]) -> bool:
        """Is the frame's query visible in the text the pipeline will embed?

        See the relevance note in the module docstring: this is the only
        relevance control the feed makes possible, because it exposes no score
        and sorts by date.
        """
        if not terms:
            return True
        required = min(max(int(self.settings.get("rss_min_term_matches", 2)), 1), len(terms))
        text = f"{doc.get('title', '')} {doc.get('abstract', '')}".lower()
        return sum(1 for term in terms if term in text) >= required

    def _rss_document(
        self,
        item: dict[str, Any],
        frame: dict[str, Any],
        steepv: str,
        start_year: int,
        end_year: int,
    ) -> dict[str, Any] | None:
        title = _clean_text(item.get("title"))
        # The guid is the publication's permalink and is what makes this
        # source deduplicate across frames and across runs. An item without
        # one has no stable identity, so it is dropped rather than keyed on a
        # title that the CMS may re-word.
        native_id = (item.get("guid") or item.get("link") or "").strip()
        if not title or not native_id:
            return None

        published = _rss_date(item.get("pubDate"))
        if published is None or not (start_year <= published.year <= end_year):
            return None

        categories = [c for c in (item.get("categories") or []) if c]
        return build_document(
            source=self.name,
            native_id=native_id,
            title=title,
            abstract=_clean_text(item.get("description"))[:4000],
            published=published,
            url=(item.get("link") or native_id).strip(),
            venue="OECD",
            institutions=["OECD"],
            concepts=categories,
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )

    # -- channel 2: statistical dataflows ---------------------------------

    def _sdmx_enabled(self) -> bool:
        return bool(self.settings.get("sdmx_enabled", True))

    def _collect_sdmx(
        self,
        query: str,
        frame: dict[str, Any],
        steepv: str,
        start_year: int,
        end_year: int,
    ) -> Iterator[dict[str, Any]]:
        catalogue = self._load_catalogue()
        if not catalogue:
            return

        terms = query_terms(query)
        if not terms:
            return
        min_score = float(self.settings.get("sdmx_min_match_score", 3.0))
        limit = max(int(self.settings.get("sdmx_max_per_query", 15)), 0)
        if limit == 0:
            return

        scored: list[tuple[float, str, dict[str, Any]]] = []
        for flow in catalogue:
            score = _match_score(terms, flow)
            if score < min_score:
                continue
            scored.append((score, flow.get("released") or "", flow))
        # Best match first; a tie goes to the more recently released series,
        # which is the more useful of two equally good matches.
        scored.sort(key=lambda row: (row[0], row[1]), reverse=True)

        for _, _, flow in scored[:limit]:
            doc = self._sdmx_document(flow, frame, steepv, start_year, end_year)
            if doc is not None:
                yield doc

    def _sdmx_document(
        self,
        flow: dict[str, Any],
        frame: dict[str, Any],
        steepv: str,
        start_year: int,
        end_year: int,
    ) -> dict[str, Any] | None:
        released = _sdmx_date(flow.get("released"))
        if released is None or not (start_year <= released.year <= end_year):
            return None

        agency = str(flow.get("agency", ""))
        flow_id = str(flow.get("id", ""))
        version = str(flow.get("version", ""))
        observations = flow.get("observations")

        # The Data Explorer's own permalink shape. A reader following an
        # evidence card needs the dataset, not a machine-readable payload.
        url = (
            "https://data-explorer.oecd.org/vis?"
            + urlencode({"df[ds]": "dsDisseminateFinalDMZ", "df[id]": flow_id, "df[ag]": agency})
        )
        abstract = flow.get("description", "")
        if observations:
            abstract = f"{abstract} OECD statistical dataflow ({observations} observations)."
        return build_document(
            source=self.name,
            native_id=f"sdmx:{agency}:{flow_id}({version})",
            title=str(flow.get("name", "")),
            abstract=abstract.strip()[:4000],
            published=released,
            url=url,
            venue="OECD Data Explorer",
            institutions=["OECD", agency] if agency else ["OECD"],
            concepts=[c for c in ("statistics", agency) if c],
            steepv=steepv,
            scan_frame_key=str(frame.get("key", "")),
            run_id=self.run_id,
            time_granularity=self.time_granularity,
        )

    def _load_catalogue(self) -> list[dict[str, Any]]:
        """Every OECD dataflow with its release date, fetched once per run.

        Two requests, shared by every frame. A failure here disables the
        statistics channel for the rest of the run and is recorded, but leaves
        the publications channel alone — they are different services on
        different hosts and fail independently.
        """
        if self._catalogue is not None:
            return self._catalogue

        self._catalogue = []
        headers = {"Accept": SDMX_STRUCTURE_JSON, "Accept-Encoding": "gzip, deflate"}
        try:
            flows_payload = self.fetch_json(SDMX_DATAFLOW_URL, None, headers=headers)
            constraints_payload = self.fetch_json(SDMX_CONSTRAINT_URL, None, headers=headers)
        except BigThinkError as exc:
            self.note_incident(f"sdmx catalogue: {exc}")
            logger.warning(
                "OECD SDMX catalogue unavailable (%s) — this run's OECD evidence is "
                "publications only.", exc,
            )
            return self._catalogue

        releases = _release_dates(constraints_payload)
        flows = ((flows_payload or {}).get("data") or {}).get("dataflows") or []
        catalogue: list[dict[str, Any]] = []
        for flow in flows:
            key = (flow.get("agencyID"), flow.get("id"), flow.get("version"))
            released, observations = releases.get(key, (None, None))
            if not released:
                # No content constraint means no release date, and a document
                # with no date cannot contribute to emergence detection at all.
                continue
            name = _clean_text(_localised(flow, "name"))
            if not name:
                continue
            catalogue.append(
                {
                    "agency": flow.get("agencyID", ""),
                    "id": flow.get("id", ""),
                    "version": flow.get("version", ""),
                    "name": name,
                    "description": _clean_text(_localised(flow, "description")),
                    "released": released,
                    "observations": observations,
                }
            )

        self.save_raw("sdmx_catalogue", 0, catalogue)
        logger.info("OECD SDMX catalogue: %d dataflow(s) with a release date.", len(catalogue))
        self._catalogue = catalogue
        return self._catalogue


# ---------------------------------------------------------------------------
# Parsing helpers — module level so the tests can reach them without a network
# ---------------------------------------------------------------------------


def _localised(obj: dict[str, Any], field: str) -> str:
    """An SDMX name/description, preferring the English localisation."""
    localised = obj.get(f"{field}s")
    if isinstance(localised, dict):
        for language in ("en", "en-GB", "en-US"):
            if localised.get(language):
                return str(localised[language])
    return str(obj.get(field) or "")


def _parse_rss(body: str, url: str, source: str) -> list[dict[str, Any]]:
    """Items from an RSS 2.0 feed, as plain dicts."""
    try:
        root = ElementTree.fromstring(body)
    except ElementTree.ParseError as exc:
        raise malformed_response_error(source, url, f"response is not XML ({exc})") from exc

    items: list[dict[str, Any]] = []
    for item in root.iter("item"):
        record: dict[str, Any] = {"categories": []}
        for child in item:
            text = (child.text or "").strip()
            if child.tag == "category":
                if text:
                    record["categories"].append(text)
            elif child.tag in ("title", "description", "link", "guid", "pubDate", "author"):
                record.setdefault(child.tag, text)
        items.append(record)
    return items


def _release_dates(payload: Any) -> dict[tuple[str, str, str], tuple[str | None, str | None]]:
    """`(agency, id, version) -> (validFrom, obs_count)` from content constraints.

    The OECD documents this query as the way to find out when a dataset was
    last updated without downloading it. Each dataflow carries two constraints
    — an `Actual` one that has the timestamp and an `Allowed` one that does not
    — so the latest timestamp seen for a dataflow wins rather than the last one
    parsed.
    """
    constraints = ((payload or {}).get("data") or {}).get("contentConstraints") or []
    out: dict[tuple[str, str, str], tuple[str | None, str | None]] = {}
    for constraint in constraints:
        valid_from = constraint.get("validFrom")
        if not valid_from:
            continue
        observations = None
        for annotation in constraint.get("annotations") or []:
            if annotation.get("type") == "sdmx_metrics" and annotation.get("id") == "obs_count":
                observations = annotation.get("title")
        attachment = (constraint.get("constraintAttachment") or {}).get("dataflows") or []
        for urn in attachment:
            match = _DATAFLOW_URN.search(str(urn))
            if not match:
                continue
            key = (match.group(1), match.group(2), match.group(3))
            previous = out.get(key)
            if previous is None or str(valid_from) > str(previous[0]):
                out[key] = (valid_from, observations)
    return out


def _match_score(terms: list[str], flow: dict[str, Any]) -> float:
    """How well one dataflow answers a frame's query.

    A term in the dataflow's NAME counts three times one in its description.
    That ratio is the whole filter, not a tuning knob: the name is what the
    OECD chose to call the series, while the description is several paragraphs
    of methodology in which almost any word appears somewhere. At the default
    `sdmx_min_match_score` of 3.0 a single name hit qualifies and a single
    description hit does not, which is the difference between "the OECD
    publishes a series on this" and "the OECD mentioned this in a footnote".

    Measured over the 1,500-dataflow catalogue on 2026-09-14: counting
    description hits alone put "National CPI, growth rate" at the top of a
    query for energy, because its methodology note mentions energy prices.
    Requiring a name hit returned "Transport energy and environment
    indicators" instead, and returned nothing at all for `counterfeit` and
    `indigenous knowledge` — which is the honest answer, because the OECD has
    no statistical series on either.
    """
    name = flow.get("name", "").lower()
    description = flow.get("description", "").lower()
    return sum(
        3.0 * (term in name) + 1.0 * (term in description) for term in terms
    )
