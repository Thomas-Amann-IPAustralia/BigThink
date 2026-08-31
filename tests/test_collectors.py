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
