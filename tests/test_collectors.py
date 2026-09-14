"""Tests for collector plumbing (src/collectors/). No network calls."""

from __future__ import annotations

import pytest

from src.collectors.base import (
    Collector,
    _retry_after_seconds,
    build_document,
    document_text,
    make_doc_id,
    parse_date,
    to_time_slice,
)
from src.collectors.crossref import _issued_date, _strip_markup
from src.collectors.openalex import _reconstruct_abstract
from src.errors import PermanentError, RetryableError, http_error


class FakeResponse:
    def __init__(self, headers=None, body=None):
        self.headers = headers or {}
        self._body = body

    def json(self):
        if self._body is None:
            raise ValueError("not json")
        return self._body


# --- error classification -------------------------------------------------


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_transient_statuses_are_retryable(status):
    assert isinstance(http_error(status, "http://x"), RetryableError)


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_permanent(status):
    assert isinstance(http_error(status, "http://x"), PermanentError)


def test_retry_after_is_read_from_header_or_body():
    """OpenAlex reports its budget reset in the body, not the standard header."""
    assert _retry_after_seconds(FakeResponse({"Retry-After": "120"})) == 120.0
    assert _retry_after_seconds(
        FakeResponse(body={"error": "Rate limit exceeded", "retryAfter": 62606})
    ) == 62606.0
    assert _retry_after_seconds(FakeResponse({"Retry-After": "Wed, 21 Oct 2026 07:28:00 GMT"})) is None
    assert _retry_after_seconds(FakeResponse()) is None


# --- normalisation --------------------------------------------------------


@pytest.mark.parametrize(
    "value, expected_year",
    [
        ("2024-03-15", 2024),
        ("20260821T004500Z", 2026),   # GDELT
        ("2019", 2019),
        ("2023-07", 2023),
        ("2024/11/02", 2024),
        ("2024-02-30", 2024),          # invalid day: keep the year, which is all we slice on
    ],
)
def test_date_parsing_across_source_formats(value, expected_year):
    parsed = parse_date(value)
    assert parsed is not None and parsed.year == expected_year


@pytest.mark.parametrize("value", [None, "", "garbage", "not-a-date"])
def test_unparseable_dates_return_none(value):
    assert parse_date(value) is None


def test_time_slicing():
    assert to_time_slice(parse_date("2024-08-15"), "year") == "2024"
    assert to_time_slice(parse_date("2024-08-15"), "quarter") == "2024Q3"
    assert to_time_slice(None) is None


def test_doc_ids_are_stable_across_processes_and_unique_per_source():
    """Deduplication depends on this being reproducible run to run — it must not
    use Python's randomised hash()."""
    assert make_doc_id("openalex", "W1") == make_doc_id("openalex", "W1")
    assert make_doc_id("openalex", "W1") != make_doc_id("arxiv", "W1")


def test_build_document_derives_year_and_slice():
    doc = build_document(
        source="openalex", native_id="W1", title="T", published="2024-05-01", authors=["a"]
    )
    assert doc["year"] == 2024
    assert doc["time_slice"] == "2024"
    assert doc["authors"] == ["a"]
    assert document_text(doc).startswith("T")


# --- source-specific parsing ---------------------------------------------


def test_openalex_inverted_abstract_is_reconstructed_in_order():
    inverted = {"Quantum": [0], "error": [1], "correction": [2], "works": [3]}
    assert _reconstruct_abstract(inverted) == "Quantum error correction works"
    assert _reconstruct_abstract(None) == ""
    assert _reconstruct_abstract({}) == ""


def test_crossref_jats_markup_is_stripped():
    assert _strip_markup("&lt;p&gt;Role of <i>AI</i> in Examination&lt;/p&gt;") == (
        "Role of AI in Examination"
    )
    assert _strip_markup("") == ""


def test_crossref_partial_dates_default_to_january_first():
    assert _issued_date({"date-parts": [[2026]]}) == "2026-01-01"
    assert _issued_date({"date-parts": [[2024, 3, 15]]}) == "2024-03-15"
    assert _issued_date(None) is None


# --- scan frame validity --------------------------------------------------


def test_gdelt_queries_wrap_ord_groups_in_parentheses():
    """GDELT rejects an unparenthesised OR with a plain-text error, not JSON.

    The collector treats a non-JSON body as a permanent failure and skips the
    frame, so the only symptom is that source quietly collecting nothing —
    which looks exactly like "no news matched". Caught here instead.
    """
    import re

    from src.config import load_config
    from src.stage1_collect import load_scan_frame

    offenders = []
    for frame in load_scan_frame(load_config()):
        query = (frame.get("queries") or {}).get("gdelt")
        if not query:
            continue
        for clause in re.split(r"\s+AND\s+", query):
            clause = clause.strip()
            if " OR " in clause and not (clause.startswith("(") and clause.endswith(")")):
                offenders.append((frame["key"], query))
                break
    assert not offenders, f"GDELT queries need parenthesised OR groups: {offenders}"


# --- incident recording ---------------------------------------------------
#
# The 2026-08-30 run recorded four GDELT frames as `success` with zero records
# after every retry failed, and reported "0 failed pairs" on the strength of
# it. These tests pin the mechanism that makes that impossible.


class _FailingCollector(Collector):
    """A collector that fails soft, the way GDELT and OpenAlex do."""

    name = "fake_failing"

    def collect(self, query, frame, start_year, end_year):
        try:
            raise RetryableError("connection reset by peer")
        except RetryableError as exc:
            self.note_incident(f"artlist: {exc}")
            return
        yield  # pragma: no cover - unreachable, keeps this a generator


class _PartialCollector(Collector):
    """Yields something, then loses the rest of the window."""

    name = "fake_partial"

    def collect(self, query, frame, start_year, end_year):
        yield build_document(source=self.name, native_id="1", title="kept")
        self.note_incident("chunk 2/4: timeout")


def _collector(cls):
    config = {
        "pipeline": {"contact_email": "x@example.org"},
        "storage": {"raw_dir": "data/raw", "keep_raw_payloads": False},
    }
    return cls(config, "test-run")


def test_a_swallowed_failure_is_recorded_as_an_incident():
    c = _collector(_FailingCollector)
    c.begin_frame()
    assert list(c.collect("q", {"key": "f"}, 2018, 2026)) == []
    assert c.incidents, "a collector that returned nothing on failure must say so"
    assert "connection reset" in c.incidents[0]


def test_partial_results_keep_their_documents_and_still_report():
    """A partial window is worth keeping; a silent one is not."""
    c = _collector(_PartialCollector)
    c.begin_frame()
    docs = list(c.collect("q", {"key": "f"}, 2018, 2026))
    assert len(docs) == 1
    assert c.incidents == ["chunk 2/4: timeout"]


def test_begin_frame_clears_incidents_between_frames():
    """Collectors are reused across frames — one bad frame must not taint the next."""
    c = _collector(_FailingCollector)
    c.begin_frame()
    list(c.collect("q", {"key": "f1"}, 2018, 2026))
    assert c.incidents
    c.begin_frame()
    assert c.incidents == []


def test_a_clean_collector_records_no_incident():
    c = _collector(_PartialCollector)
    c.begin_frame()
    assert c.incidents == []


# --- relevance floor ------------------------------------------------------
#
# The OpenAlex floor anchored on the maximum score made a query's yield a
# function of how much of an outlier its top hit was. These are the real
# score heads from the 2026-08-30 scan frame, replayed 2026-08-30.


def _floor(scores, rank):
    c = _collector(_PartialCollector)
    return c.relevance_floor(scores, min_relative=0.4, anchor_rank=rank)


def _kept(scores, rank):
    """How many leading results survive the floor."""
    floor = _floor(scores, rank)
    for i, s in enumerate(scores):
        if s < floor:
            return i
    return len(scores)


CT_AI = [3011, 1628, 1500, 1450, 1409, 1180, 1100, 1050, 1000, 980, 950, 900]
CT_BIOTECH = [609, 573, 550, 530, 516, 505, 500, 495, 490, 485, 480, 470]


def test_anchoring_on_the_maximum_penalises_an_outlier_top_hit():
    """The defect: same query shape, wildly different yield."""
    assert _kept(CT_AI, 1) < _kept(CT_BIOTECH, 1) / 2


def test_anchoring_on_rank_ten_treats_comparable_queries_comparably():
    assert _kept(CT_AI, 10) == len(CT_AI)
    assert _kept(CT_BIOTECH, 10) == len(CT_BIOTECH)


def test_rank_one_reproduces_the_original_behaviour():
    """Old runs must stay reproducible from their config snapshot."""
    assert _floor(CT_AI, 1) == pytest.approx(3011 * 0.4)


def test_anchor_rank_beyond_the_result_set_falls_back_to_the_last_result():
    assert _floor([100.0, 50.0], 10) == pytest.approx(50.0 * 0.4)


def test_missing_relevance_scores_do_not_produce_a_floor():
    """No score means no basis to cut; keep everything rather than guess."""
    assert _floor([None, None], 10) == 0.0


# --- crossref record types ------------------------------------------------


def _crossref(**settings):
    from src.collectors.crossref import CrossrefCollector

    config = {
        "pipeline": {"contact_email": "x@example.org"},
        "storage": {"raw_dir": "data/raw", "keep_raw_payloads": False},
        "collection": {"sources": {"crossref": settings}},
    }
    return CrossrefCollector(config, "test-run")


PEER_REVIEW = {
    "DOI": "10.1002/eng2.70518/v1/review1",
    "title": ["Wire arc additive manufacturing of intelligent structures"],
    "type": "peer-review",
    "issued": {"date-parts": [[2025, 3, 1]]},
}


def test_peer_review_records_are_excluded():
    """Two of the fifteen 2026-08-30 topics were one paper's review reports."""
    c = _crossref(exclude_types=["peer-review", "component"])
    assert c._to_document(PEER_REVIEW, {"key": "f"}, "Technological") is None


def test_the_reviewed_paper_itself_is_kept():
    """The filter must remove the reviews, not the literature they review."""
    c = _crossref(exclude_types=["peer-review", "component"])
    paper = {**PEER_REVIEW, "DOI": "10.1002/eng2.70518", "type": "journal-article"}
    doc = c._to_document(paper, {"key": "f"}, "Technological")
    assert doc is not None and doc["native_id"] == "10.1002/eng2.70518"


def test_back_matter_titled_references_is_excluded():
    c = _crossref(exclude_titles=["references"])
    item = {"DOI": "10.1/x", "title": ["References"], "type": "book-chapter"}
    assert c._to_document(item, {"key": "f"}, "Technological") is None


def test_nothing_is_excluded_when_the_lists_are_empty():
    """An unconfigured collector must behave as it did before this change."""
    c = _crossref()
    assert c._to_document(PEER_REVIEW, {"key": "f"}, "Technological") is not None


# --- gdelt windowing ------------------------------------------------------
#
# `timespan=24m` returns the newest 250 articles and nothing older, because
# artlist sorts most-recent-first. Every GDELT document on the 2026-08-30 run
# carried a 2026 date as a result. These pin the chunked replacement.


def test_windows_are_contiguous_and_cover_the_whole_span():
    from src.collectors.gdelt import _windows

    windows = _windows(24, 4)
    assert len(windows) == 4
    for earlier, later in zip(windows, windows[1:]):
        assert earlier[1] == later[0], "a gap between windows is lost coverage"


def test_windows_run_oldest_first():
    """A frame that dies partway keeps the recent end, which matters most."""
    from src.collectors.gdelt import _windows

    starts = [start for start, _ in _windows(24, 4)]
    assert starts == sorted(starts)


def test_a_single_chunk_reproduces_the_old_full_width_request():
    from src.collectors.gdelt import _windows

    (start, end), = _windows(24, 1)
    assert _windows(24, 4)[0][0] == start
    assert _windows(24, 4)[-1][1] == end


def test_windows_use_gdelts_datetime_stamp_format():
    from src.collectors.gdelt import _windows

    for start, end in _windows(24, 4):
        assert len(start) == 14 and start.isdigit()
        assert len(end) == 14 and end.isdigit()


def test_a_wider_span_reaches_further_back():
    from src.collectors.gdelt import _windows

    assert _windows(24, 4)[0][0] < _windows(12, 4)[0][0]


# --- arXiv rate-limit containment (PROJECT_STATE issue 14) ----------------
# The 2026-08-31 run took 28 HTTP 429s from export.arxiv.org and lost six of
# nine frames: `_collect_year` called `fetch_text` outside any try, so an
# exhausted retry budget propagated out of `collect()` and took every remaining
# year of that frame with it. These pin both halves of the fix.


_ATOM = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/{year}.00001</id>
    <title>Quantum error correction in {year}</title>
    <summary>An abstract for {year}.</summary>
    <published>{year}-03-04T00:00:00Z</published>
    <category term="quant-ph"/>
    <author><name>A Researcher</name></author>
  </entry>
</feed>
"""


def _arxiv(fetch_text, **settings):
    """An ArxivCollector whose HTTP layer is replaced by *fetch_text*."""
    from src.collectors.arxiv import ArxivCollector

    config = {
        "pipeline": {"contact_email": "x@example.org"},
        "storage": {"raw_dir": "data/raw", "keep_raw_payloads": False},
        "collection": {"sources": {"arxiv": {"max_results_per_year": 30, **settings}}},
    }
    collector = ArxivCollector(config, "test-run")
    collector.fetch_text = fetch_text  # type: ignore[method-assign]
    collector.request_delay = 0.0      # no real sleeping in tests
    collector.begin_frame()
    return collector


def _rate_limited(*failing_years):
    """A fake fetch_text that 429s for *failing_years* and serves the rest."""
    def fetch_text(url, params=None):
        year = int((params or {})["search_query"].split("submittedDate:[")[1][:4])
        if year in failing_years:
            raise RetryableError(
                f"HTTP 429 fetching {url}", context={"status_code": 429, "url": url}
            )
        return _ATOM.format(year=year)
    return fetch_text


def test_a_rate_limited_year_does_not_take_the_rest_of_the_frame():
    """The actual 2026-08-31 defect: one 429 cost every later year."""
    c = _arxiv(_rate_limited(2019, 2020))
    docs = list(c.collect("quantum", {"key": "ct_quantum"}, 2018, 2022))

    years = sorted(d["year"] for d in docs)
    assert years == [2018, 2021, 2022], (
        "years after the failure must still be collected — losing 2019 is a hole "
        "in a growth curve, losing 2021 and 2022 is losing the frame"
    )


def test_a_lost_year_is_recorded_as_an_incident():
    """Silence is what let four GDELT frames log `success` with zero records."""
    c = _arxiv(_rate_limited(2019))
    list(c.collect("quantum", {"key": "ct_quantum"}, 2018, 2020))

    assert len(c.incidents) == 1
    assert "2019" in c.incidents[0] and "429" in c.incidents[0]


def test_a_frame_that_loses_every_year_yields_nothing_and_says_so():
    """Stage 1 reads `incidents` to decide `failed` vs `success`."""
    c = _arxiv(_rate_limited(2018, 2019, 2020))
    assert list(c.collect("quantum", {"key": "ct_quantum"}, 2018, 2020)) == []
    assert len(c.incidents) == 3


def test_a_429_widens_the_request_delay_for_the_rest_of_the_run():
    """Per-year containment alone just spends the retry budget 81 times over."""
    c = _arxiv(_rate_limited(2019), rate_limit_backoff_factor=2.0,
               max_request_delay_seconds=20.0)
    c.request_delay = 3.0
    list(c.collect("quantum", {"key": "ct_quantum"}, 2018, 2020))

    assert c.request_delay == 6.0


def test_the_widened_delay_is_capped():
    """Run time is the binding constraint; the backoff must not eat the timeout."""
    c = _arxiv(_rate_limited(*range(2018, 2027)), rate_limit_backoff_factor=2.0,
               max_request_delay_seconds=10.0)
    c.request_delay = 3.0
    list(c.collect("quantum", {"key": "ct_quantum"}, 2018, 2026))

    assert c.request_delay == 10.0


def test_the_widened_delay_persists_across_frames():
    """One instance serves every frame, and the throttle is per IP, not per query.

    The 2026-08-31 run lost frames 4 through 9 to a limit that frames 1 through
    3 had already discovered. A delay that reset per frame would rediscover it
    nine times and act on it none.
    """
    failing = {2019}
    def fetch_text(url, params=None):
        year = int((params or {})["search_query"].split("submittedDate:[")[1][:4])
        if year in failing:
            raise RetryableError(
                f"HTTP 429 fetching {url}", context={"status_code": 429, "url": url}
            )
        return _ATOM.format(year=year)

    c = _arxiv(fetch_text, rate_limit_backoff_factor=2.0)
    c.request_delay = 3.0
    list(c.collect("quantum", {"key": "frame_one"}, 2018, 2020))
    assert c.request_delay == 6.0

    failing.clear()   # frame two sees a healthy arXiv...
    c.begin_frame()   # ...and begin_frame clears incidents, not the measured delay
    list(c.collect("quantum", {"key": "frame_two"}, 2018, 2020))
    assert c.incidents == []
    assert c.request_delay == 6.0, (
        "the delay must not fall back — a source that has just rate-limited us "
        "is not evidence that it has stopped"
    )


def test_a_healthy_run_never_widens_the_delay():
    """The adaptive delay must cost nothing when arXiv is behaving."""
    c = _arxiv(_rate_limited())
    c.request_delay = 3.0
    docs = list(c.collect("quantum", {"key": "ct_quantum"}, 2018, 2020))

    assert len(docs) == 3
    assert c.request_delay == 3.0


def test_a_non_rate_limit_failure_is_contained_but_does_not_widen_the_delay():
    """A 500 is not evidence about our request rate."""
    def fetch_text(url, params=None):
        year = int((params or {})["search_query"].split("submittedDate:[")[1][:4])
        if year == 2019:
            raise RetryableError("HTTP 503", context={"status_code": 503, "url": url})
        return _ATOM.format(year=year)

    c = _arxiv(fetch_text)
    c.request_delay = 3.0
    docs = list(c.collect("quantum", {"key": "ct_quantum"}, 2018, 2020))

    assert sorted(d["year"] for d in docs) == [2018, 2020]
    assert c.incidents and c.request_delay == 3.0


# --- a raising collector must not discard what it already yielded ---------
#
# `collect` is a generator drained by Stage 1. Draining it with `list()` meant
# an exception after the first yield threw away every document already
# produced: OpenAlex pages five deep, so a budget exhaustion on page 3 lost
# pages 1 and 2 and the frame was logged `skipped` with zero records — true of
# the log, false of what happened. PROJECT_STATE.md issue 24.


class _RaisesAfterYieldingCollector(Collector):
    """Yields two documents, then hits its daily quota. OpenAlex, in miniature."""

    name = "fake_raises_after_yielding"

    def collect(self, query, frame, start_year, end_year):
        yield build_document(source=self.name, native_id="1", title="page one")
        yield build_document(source=self.name, native_id="2", title="page two")
        raise PermanentError("Insufficient budget. Resets at midnight UTC.")


def test_a_permanent_error_mid_frame_keeps_the_documents_already_collected():
    from src.stage1_collect import _drain

    collector = _collector(_RaisesAfterYieldingCollector)
    collector.begin_frame()

    docs, failure = _drain(collector, "q", {"key": "f"}, 2018, 2026)

    assert [d["title"] for d in docs] == ["page one", "page two"], (
        "documents produced before the failure must survive it"
    )
    assert failure is not None
    exc, kind = failure
    assert kind == "permanent"
    assert "Insufficient budget" in str(exc)

    # `list()` is what the stage used to do, and it is what loses them.
    collector.begin_frame()
    with pytest.raises(PermanentError):
        list(collector.collect("q", {"key": "f"}, 2018, 2026))


def test_drain_classifies_each_failure_kind():
    """The kind decides whether the source is retired or only this frame fails."""
    from src.stage1_collect import _drain

    class _Retryable(Collector):
        name = "fake_retryable"

        def collect(self, query, frame, start_year, end_year):
            yield build_document(source=self.name, native_id="1", title="kept")
            raise RetryableError("HTTP 503")

    class _Unexpected(Collector):
        name = "fake_unexpected"

        def collect(self, query, frame, start_year, end_year):
            raise KeyError("schema changed under us")
            yield  # pragma: no cover - unreachable, keeps this a generator

    docs, (_, kind) = _drain(_collector(_Retryable), "q", {"key": "f"}, 2018, 2026)
    assert kind == "retryable" and len(docs) == 1

    docs, (_, kind) = _drain(_collector(_Unexpected), "q", {"key": "f"}, 2018, 2026)
    assert kind == "unexpected" and docs == []


def test_a_clean_collector_drains_with_no_failure():
    from src.stage1_collect import _drain

    docs, failure = _drain(_collector(_PartialCollector), "q", {"key": "f"}, 2018, 2026)
    assert failure is None
    assert len(docs) == 1


# --- OECD -----------------------------------------------------------------
#
# One source, two channels: the web CMS search RSS feed and the SDMX
# statistics catalogue. Everything below is offline — the RSS and SDMX bodies
# are trimmed copies of what the live services returned on 2026-09-14.

from src.collectors.oecd import (  # noqa: E402
    OecdCollector,
    _clean_text,
    _match_score,
    _parse_rss,
    _release_dates,
    _rss_date,
    _with_paging,
    query_terms,
)

RSS_BODY = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
<title><![CDATA[OECD RSS Feed - patent]]></title>
<item>
  <title><![CDATA[Patent statistics for the digital age]]></title>
  <description><![CDATA[This report reviews how patent data can be used to
  measure digital innovation.]]></description>
  <link>https://www.oecd.org/en/publications/patent-stats_1234-en.html</link>
  <guid isPermaLink="true">https://www.oecd.org/en/publications/patent-stats_1234-en.html</guid>
  <pubDate>Tue, 08 Sep 2026 22:00:00 GMT</pubDate>
  <author>OECD</author>
  <category domain="content-type">Report</category>
  <category domain="policy-area">Science and technology</category>
</item>
<item>
  <title><![CDATA[Tackling the housing affordability gap]]></title>
  <description><![CDATA[Housing supply across OECD countries.]]></description>
  <link>https://www.oecd.org/en/publications/housing_5678-en.html</link>
  <guid isPermaLink="true">https://www.oecd.org/en/publications/housing_5678-en.html</guid>
  <pubDate>Thu, 09 Jul 2020 09:00:00 GMT</pubDate>
  <category domain="content-type">Policy paper</category>
</item>
</channel></rss>"""

SDMX_FLOWS = {
    "data": {
        "dataflows": [
            {
                "id": "DSD_PAT@DF_PATENTS",
                "version": "1.0",
                "agencyID": "OECD.STI.STP",
                "names": {"en": "Patents by technology"},
                "descriptions": {"en": "<p>Counts of <b>patent</b> families.</p>"},
            },
            {
                "id": "DSD_CPI@DF_CPI",
                "version": "2.1",
                "agencyID": "OECD.SDD.TPS",
                "names": {"en": "National CPI, growth rate"},
                # Mentions the query word only in the methodology prose.
                "descriptions": {"en": "Consumer prices including energy and patent-protected goods."},
            },
            {
                "id": "DSD_OLD@DF_OLD",
                "version": "1.0",
                "agencyID": "OECD.SDD.NAD",
                "names": {"en": "Patents, discontinued series"},
                "descriptions": {"en": "Retired."},
            },
        ]
    }
}

SDMX_CONSTRAINTS = {
    "data": {
        "contentConstraints": [
            {
                "type": "Allowed",
                "constraintAttachment": {
                    "dataflows": [
                        "urn:sdmx:org.sdmx.infomodel.datastructure."
                        "Dataflow=OECD.STI.STP:DSD_PAT@DF_PATENTS(1.0)"
                    ]
                },
            },
            {
                "type": "Actual",
                "validFrom": "2026-04-16T09:14:02Z",
                "annotations": [{"id": "obs_count", "title": "1602019", "type": "sdmx_metrics"}],
                "constraintAttachment": {
                    "dataflows": [
                        "urn:sdmx:org.sdmx.infomodel.datastructure."
                        "Dataflow=OECD.STI.STP:DSD_PAT@DF_PATENTS(1.0)"
                    ]
                },
            },
            {
                "type": "Actual",
                "validFrom": "2026-01-05T00:00:00Z",
                "constraintAttachment": {
                    "dataflows": [
                        "urn:sdmx:org.sdmx.infomodel.datastructure."
                        "Dataflow=OECD.SDD.TPS:DSD_CPI@DF_CPI(2.1)"
                    ]
                },
            },
            {
                "type": "Actual",
                "validFrom": "2011-02-02T00:00:00Z",
                "constraintAttachment": {
                    "dataflows": [
                        "urn:sdmx:org.sdmx.infomodel.datastructure."
                        "Dataflow=OECD.SDD.NAD:DSD_OLD@DF_OLD(1.0)"
                    ]
                },
            },
        ]
    }
}


def _oecd(**settings):
    config = {
        "pipeline": {"contact_email": "x@example.org"},
        "storage": {"raw_dir": "data/raw", "keep_raw_payloads": False},
        "collection": {"sources": {"oecd": settings}},
    }
    collector = OecdCollector(config, "test-run")
    collector.begin_frame()
    return collector


def _offline(collector, *, rss=RSS_BODY):
    """Serve both channels from the fixtures above instead of the network."""
    collector.fetch_text = lambda url, params=None, headers=None: rss
    collector.fetch_json = lambda url, params=None, headers=None: (
        SDMX_FLOWS if "/dataflow/" in url else SDMX_CONSTRAINTS
    )
    return collector


FRAME = {"key": "ip_search_retrieval", "steepv": "Technological"}


# -- RSS parsing -----------------------------------------------------------


def test_rss_items_are_parsed_with_their_categories():
    items = _parse_rss(RSS_BODY, "http://x", "oecd")
    assert len(items) == 2
    assert items[0]["guid"].endswith("patent-stats_1234-en.html")
    assert items[0]["categories"] == ["Report", "Science and technology"]


def test_rss_bodies_that_are_not_xml_are_a_permanent_failure():
    """An HTML error page must not be parsed as an empty feed — that would read
    downstream as 'the OECD published nothing about this'."""
    with pytest.raises(PermanentError):
        _parse_rss("<html>502 Bad Gateway", "http://x", "oecd")


def test_rfc822_dates_are_parsed():
    """The RSS date format, which base.parse_date does not cover — left to it,
    every OECD publication would fall out of the year window undated."""
    assert _rss_date("Tue, 08 Sep 2026 22:00:00 GMT").isoformat() == "2026-09-08"
    assert parse_date("Tue, 08 Sep 2026 22:00:00 GMT") is None  # why the above exists
    assert _rss_date("not a date") is None
    assert _rss_date(None) is None


def test_html_is_stripped_from_descriptions():
    assert _clean_text("<p>Counts of <b>patent</b> families &amp; more.</p>") == (
        "Counts of patent families & more."
    )
    assert _clean_text(None) == ""


# -- RSS relevance ---------------------------------------------------------
#
# The feed exposes no relevance score and sorts by date, and its matching is
# loose: measured 2026-09-14, `searchTerm=patent` returned 100 items of which
# 4 mentioned "patent" in their own title or abstract, and `intellectual
# property` returned 100 of which none mentioned either word. The pipeline
# embeds title + abstract and nothing else, so those items would arrive
# looking unrelated to the frame that collected them.


def test_publications_not_carrying_the_query_are_dropped():
    collector = _offline(_oecd(rss_max_pages_per_query=1, sdmx_enabled=False))
    docs = list(collector.collect("patent", FRAME, 2018, 2026))
    assert [d["title"] for d in docs] == ["Patent statistics for the digital age"]


def test_a_verbatim_feed_url_keeps_everything_and_skips_the_statistics_channel():
    """A faceted feed is a deliberate slice with no search term to check items
    against, and widening it back out with a keyword match over the statistics
    catalogue would misrepresent what its author asked for."""
    collector = _offline(_oecd(rss_max_pages_per_query=1))
    docs = list(collector.collect("https://api.oecd.org/webcms/search/rss?facets=x", FRAME, 2018, 2026))
    assert len(docs) == 2
    assert not any(d["native_id"].startswith("sdmx:") for d in docs)


def test_publications_outside_the_year_window_are_dropped():
    collector = _offline(_oecd(rss_max_pages_per_query=1, sdmx_enabled=False))
    assert list(collector.collect("housing patent", FRAME, 2024, 2026)) == []


def test_rss_documents_carry_the_permalink_as_a_stable_native_id():
    collector = _offline(_oecd(rss_max_pages_per_query=1, sdmx_enabled=False))
    doc = next(iter(collector.collect("patent", FRAME, 2018, 2026)))
    assert doc["native_id"] == "https://www.oecd.org/en/publications/patent-stats_1234-en.html"
    assert doc["venue"] == "OECD"
    assert doc["concepts"] == ["Report", "Science and technology"]


def test_paging_preserves_repeated_facet_parameters():
    """The OECD search UI emits one `facets` parameter per facet and the API
    comma-joins them. Rebuilding the query string through a dict would keep
    only the last one and silently widen the feed."""
    paged = _with_paging(
        "https://api.oecd.org/webcms/search/rss?facets=a&facets=b&page=9", 2, 100
    )
    assert paged.count("facets=") == 2
    assert "page=2" in paged and "page=9" not in paged
    assert "pageSize=100" in paged


# -- query terms -----------------------------------------------------------


def test_query_terms_strip_other_engines_syntax():
    """Frame queries are written for the API they address; the SDMX channel
    matches locally and has to read them as words."""
    assert query_terms("abs:(patent AND (classification OR retrieval))") == [
        "patent", "classification", "retrieval",
    ]
    assert query_terms("cat:cs.RO AND abs:(autonomous OR navigation)") == [
        "autonomous", "navigation",
    ]
    assert query_terms("") == []


# -- SDMX ------------------------------------------------------------------


def test_a_name_match_outscores_a_description_match():
    """The whole statistics filter. A dataflow whose methodology note mentions
    the query is not a dataflow about the query."""
    named = {"name": "Patents by technology", "description": ""}
    mentioned = {"name": "National CPI, growth rate", "description": "including patent goods"}
    assert _match_score(["patent"], named) > _match_score(["patent"], mentioned)
    assert _match_score(["patent"], mentioned) < 3.0  # below the shipped threshold


def test_release_dates_prefer_the_constraint_that_has_one():
    """Each dataflow carries an Allowed constraint with no timestamp and an
    Actual one with it; parsing order must not decide which wins."""
    releases = _release_dates(SDMX_CONSTRAINTS)
    assert releases[("OECD.STI.STP", "DSD_PAT@DF_PATENTS", "1.0")] == (
        "2026-04-16T09:14:02Z", "1602019",
    )


def test_statistics_are_matched_on_the_name_and_dated_from_the_release():
    collector = _offline(_oecd(rss_max_pages_per_query=1, sdmx_max_per_query=10))
    docs = [d for d in collector.collect("patent", FRAME, 2018, 2026)
            if d["native_id"].startswith("sdmx:")]
    # "National CPI" only mentions patents in prose; the 2011 series predates
    # the window. Only the one named after the query survives both.
    assert [d["title"] for d in docs] == ["Patents by technology"]
    assert docs[0]["published_date"].isoformat() == "2026-04-16"
    assert "1602019 observations" in docs[0]["abstract"]
    assert docs[0]["native_id"] == "sdmx:OECD.STI.STP:DSD_PAT@DF_PATENTS(1.0)"


def test_an_unavailable_catalogue_records_an_incident_and_keeps_the_publications():
    """The two channels are different services on different hosts. SDMX being
    down must cost the statistics, not the frame."""
    collector = _offline(_oecd(rss_max_pages_per_query=1))

    def explode(url, params=None, headers=None):
        raise RetryableError("connection reset by peer")

    collector.fetch_json = explode
    docs = list(collector.collect("patent", FRAME, 2018, 2026))
    assert len(docs) == 1 and not docs[0]["native_id"].startswith("sdmx:")
    assert collector.incidents and "sdmx catalogue" in collector.incidents[0]


def test_the_catalogue_is_fetched_once_per_run_not_once_per_frame():
    """Two requests cover every frame in the scan frame. The OECD allows 60
    downloads an hour and asks for consolidated queries; a per-frame fetch
    would spend that budget on an answer it already had."""
    collector = _offline(_oecd(rss_max_pages_per_query=1))
    calls = []
    inner = collector.fetch_json
    collector.fetch_json = lambda url, params=None, headers=None: (
        calls.append(url) or inner(url, params, headers)
    )
    for frame_key in ("f1", "f2", "f3"):
        collector.begin_frame()
        list(collector.collect("patent", {"key": frame_key, "steepv": "Technological"}, 2018, 2026))
    assert len(calls) == 2


def test_every_oecd_query_is_a_url_or_a_short_distinctive_phrase():
    """The scan-frame contract for this source. Its search has no phrase
    operator and no relevance score, so a generic extra word replaces the
    query rather than narrowing it — `intellectual property` returned 100
    items mentioning neither word. Long queries are the failure mode."""
    from src.config import load_config
    from src.stage1_collect import load_scan_frame

    offenders = [
        (frame["key"], query)
        for frame in load_scan_frame(load_config())
        for query in [(frame.get("queries") or {}).get("oecd")]
        if query
        and not query.startswith("https://")
        and len(query_terms(query)) > 2
    ]
    assert not offenders, f"OECD queries should be one or two distinctive words: {offenders}"


# --- arXiv source retirement ----------------------------------------------
#
# The 2026-09-14 run hit its six-hour timeout four frames short of the end of
# the scan frame. arXiv took 212 of the 332 collection minutes — 64% — and
# returned 138 documents. Every frame cost ~20 minutes whether it returned 60
# documents or none, because the adaptive delay was pinned at its ceiling and
# each of the nine years still spent four retry attempts against a server
# refusing all of them.
#
# Per-year and per-frame containment (issue 14) were both correct and both
# local; together they turned a fast failure into a slow one. These tests pin
# the missing observation: a throttled IP is not a per-frame condition.

def _throttled_arxiv(**settings):
    config = {
        "pipeline": {"contact_email": "x@example.org"},
        "storage": {"raw_dir": "data/raw", "keep_raw_payloads": False},
        "collection": {"sources": {"arxiv": settings}},
    }
    from src.collectors.arxiv import ArxivCollector

    collector = ArxivCollector(config, "test-run")
    collector.request_delay = 0.0   # no real sleeping in tests
    return collector


def _dead_frame(collector, key):
    """A frame where every request fails, as a rate-limited runner sees it."""
    collector.begin_frame()
    collector.fetch_text = _raise_429
    return list(collector.collect("q", {"key": key, "steepv": "Technological"}, 2024, 2024))


def _raise_429(url, params=None, headers=None):
    raise RetryableError("HTTP 429", context={"status_code": 429, "url": url})


def test_arxiv_retires_itself_after_consecutive_dead_frames():
    collector = _throttled_arxiv(retire_after_failed_frames=2, max_results_per_year=25)
    assert _dead_frame(collector, "f1") == []
    assert _dead_frame(collector, "f2") == []
    # The third frame must not spend twenty minutes rediscovering this.
    collector.begin_frame()
    with pytest.raises(PermanentError, match="consecutive frames"):
        next(iter(collector.collect("q", {"key": "f3"}, 2024, 2024)))


def test_retirement_is_raised_before_any_request_is_made():
    """Retirement has to be free. Confirming it by spending one more frame is
    the cost this exists to avoid."""
    collector = _throttled_arxiv(retire_after_failed_frames=1, max_results_per_year=25)
    _dead_frame(collector, "f1")

    calls = []
    collector.begin_frame()
    collector.fetch_text = lambda *a, **k: calls.append(a) or ""
    with pytest.raises(PermanentError):
        next(iter(collector.collect("q", {"key": "f2"}, 2024, 2024)))
    assert calls == []


def test_a_frame_that_simply_matched_nothing_does_not_count_as_dead():
    """Evidence about the query, not about the server. Counting it would retire
    arXiv over a narrow scan frame rather than over a rate limit."""
    collector = _throttled_arxiv(retire_after_failed_frames=2, max_results_per_year=25)
    empty = "<feed xmlns='http://www.w3.org/2005/Atom'></feed>"
    for key in ("f1", "f2", "f3"):
        collector.begin_frame()
        collector.fetch_text = lambda *a, **k: empty
        assert list(collector.collect("q", {"key": key}, 2024, 2024)) == []
    assert collector._dead_frames == 0


def test_a_productive_frame_resets_the_counter():
    """One bad frame between two good ones is a blip, not a throttle."""
    collector = _throttled_arxiv(retire_after_failed_frames=2, max_results_per_year=25)
    _dead_frame(collector, "f1")
    assert collector._dead_frames == 1

    entry = (
        "<feed xmlns='http://www.w3.org/2005/Atom'><entry>"
        "<id>http://arxiv.org/abs/2401.00001v1</id><title>A paper</title>"
        "<summary>Text.</summary><published>2024-01-05T00:00:00Z</published>"
        "</entry></feed>"
    )
    collector.begin_frame()
    collector.fetch_text = lambda *a, **k: entry
    assert len(list(collector.collect("q", {"key": "f2"}, 2024, 2024))) == 1
    assert collector._dead_frames == 0


def test_retirement_is_off_when_the_threshold_is_zero():
    """The pre-2026-09-14 behaviour, kept reachable so an old run's config
    snapshot still describes what that run did."""
    collector = _throttled_arxiv(retire_after_failed_frames=0, max_results_per_year=25)
    for key in ("f1", "f2", "f3"):
        assert _dead_frame(collector, key) == []  # no raise


def test_the_shipped_config_retires_arxiv():
    from src.config import load_config as _load, get as _get

    assert _get(_load(), "collection", "sources", "arxiv",
                "retire_after_failed_frames", default=0) >= 1


# --- the corpus must survive the job being killed --------------------------


def test_checkpoint_folds_the_write_ahead_log_into_the_database_file(tmp_path):
    """The cancelled 2026-09-14 run collected 10,057 documents over six hours,
    was SIGKILLed at the job timeout before `conn.close()` could run, and
    published a corpus byte-identical in size to the one it started from — the
    `.duckdb` file the workflow uploads held none of them, because they were
    still in the `.wal` that nothing uploads.

    So this tests what the workflow actually does: copy the `.duckdb` file and
    nothing else, then read the copy. Without the checkpoint that copy does not
    even have the schema.
    """
    import shutil

    import duckdb

    from src import db

    live = tmp_path / "corpus.duckdb"
    conn = db.init_db(live)
    db.upsert_documents(
        conn, [build_document(source="oecd", native_id="1", title="A report")]
    )

    # What `scan.yml` uploads, before the fix: the database file alone.
    shutil.copy(live, tmp_path / "before.duckdb")
    stranded = duckdb.connect(str(tmp_path / "before.duckdb"))
    with pytest.raises(duckdb.CatalogException):
        stranded.execute("SELECT count(*) FROM documents")
    stranded.close()

    db.checkpoint(conn)

    shutil.copy(live, tmp_path / "after.duckdb")
    published = duckdb.connect(str(tmp_path / "after.duckdb"))
    assert published.execute("SELECT count(*) FROM documents").fetchone()[0] == 1
    published.close()
    conn.close()


def test_stage1_checkpoints_around_every_frame():
    """Bounds the loss to one frame rather than the whole collection."""
    import inspect

    from src import stage1_collect

    source = inspect.getsource(stage1_collect._run_inner)
    assert source.count("db.checkpoint(conn)") >= 2, (
        "Stage 1 must checkpoint inside the frame loop and once after it"
    )
