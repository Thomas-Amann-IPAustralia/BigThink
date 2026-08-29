"""Tests for collector plumbing (src/collectors/). No network calls."""

from __future__ import annotations

import pytest

from src.collectors.base import (
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
