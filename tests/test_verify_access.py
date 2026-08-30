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


# ---------------------------------------------------------------------------
# Which R2 operations decide the verdict
#
# storage.py performs PutObject and GetObject and nothing else, so those two
# alone are fatal. An R2 token scoped to a single bucket commonly refuses
# HeadBucket while permitting every object call; failing on that would report
# a working corpus mirror as broken.
# ---------------------------------------------------------------------------


class _FakeR2:
    """Minimal S3 surface where any named operation can be made to deny."""

    def __init__(self, deny: set[str] | None = None) -> None:
        self.deny = deny or set()
        self.objects: dict[str, bytes] = {}
        self.calls: list[str] = []

    def _guard(self, op: str) -> None:
        self.calls.append(op)
        if op in self.deny:
            raise _FakeClientError("403")

    def head_bucket(self, Bucket):  # noqa: N803 - boto3's parameter casing
        self._guard("HeadBucket")

    def put_object(self, Bucket, Key, Body):  # noqa: N803
        self._guard("PutObject")
        self.objects[Key] = Body

    def get_object(self, Bucket, Key):  # noqa: N803
        self._guard("GetObject")
        return {"Body": _FakeBody(self.objects[Key])}

    def list_objects_v2(self, Bucket, MaxKeys=None, Prefix=""):  # noqa: N803
        self._guard("ListObjectsV2")
        return {"Contents": [{"Key": k} for k in self.objects]}

    def delete_object(self, Bucket, Key):  # noqa: N803
        self._guard("DeleteObject")
        self.objects.pop(Key, None)


class _FakeBody:
    def __init__(self, data: bytes) -> None:
        self._data = data

    def read(self) -> bytes:
        return self._data


@pytest.fixture()
def r2_env(monkeypatch):
    for name in ("R2_ACCOUNT_ID", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(name, "value")


def _install(monkeypatch, fake):
    import boto3

    monkeypatch.setattr(boto3, "client", lambda *a, **k: fake)
    return fake


def test_r2_passes_when_every_operation_is_permitted(monkeypatch, r2_env):
    fake = _install(monkeypatch, _FakeR2())
    result = check_r2("bigthink-corpus")
    assert result.symbol == "PASS"
    assert "PutObject=ok" in result.detail and "GetObject=ok" in result.detail


def test_r2_still_passes_when_head_bucket_is_denied(monkeypatch, r2_env):
    # The case a bucket-scoped R2 token actually produces.
    fake = _install(monkeypatch, _FakeR2(deny={"HeadBucket"}))
    result = check_r2("bigthink-corpus")
    assert result.symbol == "PASS"
    assert "HeadBucket=DENIED" in result.detail
    assert "Non-fatal" in result.detail


def test_r2_still_passes_when_listing_and_delete_are_denied(monkeypatch, r2_env):
    fake = _install(monkeypatch, _FakeR2(deny={"ListObjectsV2", "DeleteObject"}))
    assert check_r2("bigthink-corpus").symbol == "PASS"


def test_r2_fails_when_write_is_denied(monkeypatch, r2_env):
    # push_corpus is a PutObject; a token that cannot write cannot mirror.
    fake = _install(monkeypatch, _FakeR2(deny={"PutObject"}))
    result = check_r2("bigthink-corpus")
    assert result.symbol == "FAIL"
    assert "PutObject=DENIED" in result.detail


def test_r2_fails_when_read_is_denied(monkeypatch, r2_env):
    fake = _install(monkeypatch, _FakeR2(deny={"GetObject"}))
    assert check_r2("bigthink-corpus").symbol == "FAIL"


def test_r2_does_not_attempt_a_read_it_could_not_have_written(monkeypatch, r2_env):
    fake = _install(monkeypatch, _FakeR2(deny={"PutObject"}))
    check_r2("bigthink-corpus")
    assert "GetObject" not in fake.calls


def test_r2_cleans_up_its_test_object(monkeypatch, r2_env):
    fake = _install(monkeypatch, _FakeR2())
    check_r2("bigthink-corpus")
    assert fake.objects == {}


def test_r2_keeps_test_object_when_cleanup_disabled(monkeypatch, r2_env):
    fake = _install(monkeypatch, _FakeR2())
    check_r2("bigthink-corpus", cleanup=False)
    assert len(fake.objects) == 1
    assert "DeleteObject" not in fake.calls


# ---------------------------------------------------------------------------
# When everything is denied, say whether the bucket exists at all
# ---------------------------------------------------------------------------


class _FakeR2WithBuckets(_FakeR2):
    def __init__(self, buckets, deny=None, deny_list_buckets=False):
        super().__init__(deny=deny)
        self._buckets = buckets
        self._deny_list_buckets = deny_list_buckets

    def list_buckets(self):
        if self._deny_list_buckets:
            raise _FakeClientError("AccessDenied")
        return {"Buckets": [{"Name": b} for b in self._buckets]}


def test_denied_but_bucket_exists_points_at_the_token_scope(monkeypatch, r2_env):
    fake = _FakeR2WithBuckets(["bigthink-corpus", "other"], deny={"PutObject"})
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "DOES exist" in detail and "not scoped to it" in detail


def test_denied_and_bucket_absent_points_at_the_name(monkeypatch, r2_env):
    fake = _FakeR2WithBuckets(["something-else"], deny={"PutObject"})
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "No bucket named" in detail and "'something-else'" in detail


def test_denied_list_buckets_is_itself_reported_as_a_narrow_scope(monkeypatch, r2_env):
    fake = _FakeR2WithBuckets([], deny={"PutObject"}, deny_list_buckets=True)
    _install(monkeypatch, fake)
    assert "scoped to specific buckets" in check_r2("bigthink-corpus").detail


def test_empty_account_points_at_the_account_id(monkeypatch, r2_env):
    fake = _FakeR2WithBuckets([], deny={"PutObject"})
    _install(monkeypatch, fake)
    assert "R2_ACCOUNT_ID" in check_r2("bigthink-corpus").detail


def test_bucket_listing_is_not_consulted_when_the_check_passes(monkeypatch, r2_env):
    # The happy path must not spend an extra call, and must not depend on a
    # permission the pipeline never needs.
    fake = _FakeR2WithBuckets(["bigthink-corpus"], deny_list_buckets=True)
    _install(monkeypatch, fake)
    assert check_r2("bigthink-corpus").symbol == "PASS"
