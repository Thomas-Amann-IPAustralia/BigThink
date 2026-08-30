"""Tests for src/verify_access.py. No network calls: every test either takes
the unset-credential path, which returns before any request is made, or
inspects a pure helper."""

from __future__ import annotations

import pytest

from src import verify_access
from src.verify_access import Result, check_openalex, check_r2, describe_env

_ALL_VARS = (
    "OPENALEX_API_KEY",
    "R2_ACCOUNT_ID",
    "R2_ACCESS_KEY_ID",
    "R2_SECRET_ACCESS_KEY",
)


@pytest.fixture(autouse=True)
def _no_ambient_credentials(monkeypatch):
    """The checker reads process environment directly, so a developer with
    real keys exported would otherwise make these tests hit the network."""
    for name in _ALL_VARS:
        monkeypatch.delenv(name, raising=False)


# ---------------------------------------------------------------------------
# Presence reporting never reveals a value
# ---------------------------------------------------------------------------


def test_describe_env_reports_unset(monkeypatch):
    assert describe_env("OPENALEX_API_KEY") == "OPENALEX_API_KEY: not set"


def test_describe_env_distinguishes_empty_from_unset(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "")
    assert "EMPTY" in describe_env("OPENALEX_API_KEY")


def test_describe_env_never_prints_the_value(monkeypatch):
    secret = "super-secret-key-value"
    monkeypatch.setenv("OPENALEX_API_KEY", secret)
    described = describe_env("OPENALEX_API_KEY")
    assert secret not in described
    assert str(len(secret)) in described


def test_describe_env_flags_stray_whitespace(monkeypatch):
    # The most common reason a correctly-copied key is still rejected, and
    # invisible in every other diagnostic.
    monkeypatch.setenv("R2_SECRET_ACCESS_KEY", "value-with-newline\n")
    assert "WARNING" in describe_env("R2_SECRET_ACCESS_KEY")


def test_describe_env_flags_wrapping_quotes(monkeypatch):
    monkeypatch.setenv("R2_ACCESS_KEY_ID", '"quoted-value"')
    assert "WARNING" in describe_env("R2_ACCESS_KEY_ID")


# ---------------------------------------------------------------------------
# Unset credentials skip rather than fail — running without them is supported
# ---------------------------------------------------------------------------


def test_openalex_skips_when_key_unset():
    result = check_openalex()
    assert result.symbol == "SKIP"
    assert result.configured is False


def test_openalex_treats_empty_key_as_unset(monkeypatch):
    monkeypatch.setenv("OPENALEX_API_KEY", "   ")
    assert check_openalex().symbol == "SKIP"


def test_r2_skips_when_all_vars_unset():
    result = check_r2("bigthink-corpus")
    assert result.symbol == "SKIP"


def test_r2_skips_and_names_the_missing_variable(monkeypatch):
    # A partially-configured R2 is the likeliest real mistake, so the message
    # has to say which of the three is absent.
    monkeypatch.setenv("R2_ACCOUNT_ID", "acct")
    monkeypatch.setenv("R2_ACCESS_KEY_ID", "key")
    result = check_r2("bigthink-corpus")
    assert result.symbol == "SKIP"
    assert "R2_SECRET_ACCESS_KEY" in result.detail
    assert "R2_ACCOUNT_ID" not in result.detail


# ---------------------------------------------------------------------------
# Result states
# ---------------------------------------------------------------------------


def test_result_states():
    assert Result("x").passed("ok").symbol == "PASS"
    assert Result("x").failed("bad").symbol == "FAIL"
    assert Result("x").skip("absent").symbol == "SKIP"


def test_failed_result_is_configured_but_not_ok():
    # The distinction the exit code depends on: a broken credential is a
    # failure, an absent one is not.
    result = Result("x").failed("bad")
    assert result.configured is True and result.ok is False


# ---------------------------------------------------------------------------
# R2 error diagnosis turns an S3 code into the setup step to go fix
# ---------------------------------------------------------------------------


class _FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


@pytest.mark.parametrize(
    "code, expected",
    [
        ("InvalidAccessKeyId", "R2_ACCESS_KEY_ID"),
        ("SignatureDoesNotMatch", "R2_SECRET_ACCESS_KEY"),
        ("AccessDenied", "Object Read & Write"),
        ("NoSuchBucket", "bigthink-corpus"),
    ],
)
def test_diagnose_r2_names_the_fix(code, expected):
    assert expected in verify_access._diagnose_r2(_FakeClientError(code), "bigthink-corpus")


def test_diagnose_r2_handles_an_error_without_a_response():
    message = verify_access._diagnose_r2(ValueError("boom"), "bigthink-corpus")
    assert "ValueError" in message and "boom" in message


# ---------------------------------------------------------------------------
# Exit codes
# ---------------------------------------------------------------------------


def test_main_returns_zero_when_nothing_is_configured(capsys):
    assert verify_access.main([]) == 0


def test_main_returns_one_when_require_and_nothing_configured(capsys):
    assert verify_access.main(["--require"]) == 1


def test_main_output_lists_both_checks(capsys):
    verify_access.main([])
    out = capsys.readouterr().out
    assert "OpenAlex" in out and "Cloudflare R2" in out
