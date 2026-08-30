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
        self.read_keys: list[str] = []

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
        self.read_keys.append(Key)
        if Key not in self.objects:
            raise _FakeClientError("NoSuchKey")
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


# A Cloudflare account ID is exactly 32 hex characters, and check_r2 now
# rejects anything else before it reaches botocore — so the fixture has to
# use a realistic one.
ACCOUNT = "6cd7669a5e77a844abc49b6a0eecd0a3"


@pytest.fixture()
def r2_env(monkeypatch):
    monkeypatch.setenv("R2_ACCOUNT_ID", ACCOUNT)
    for name in ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY"):
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


def test_r2_does_not_read_back_an_object_it_could_not_write(monkeypatch, r2_env):
    # It does still call GetObject — but against a deliberately-absent probe
    # key, to find out whether read is permitted. What it must never do is
    # claim a round-trip by reading back a write that did not happen.
    fake = _FakeR2WithBuckets(["bigthink-corpus"], deny={"PutObject"})
    _install(monkeypatch, fake)
    result = check_r2("bigthink-corpus")
    assert result.symbol == "FAIL"
    assert fake.objects == {}
    assert all("access-check" not in k for k in fake.read_keys)


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


def test_denied_list_buckets_does_not_claim_the_bucket_is_out_of_scope(monkeypatch, r2_env):
    # ListBuckets is denied for every bucket-scoped token, so it cannot be
    # read as evidence about the bucket name. Saying otherwise sent a real
    # investigation to the wrong place.
    fake = _FakeR2WithBuckets([], deny={"PutObject"}, deny_list_buckets=True)
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "normal for a bucket-scoped token" in detail
    assert "do not include" not in detail


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


# ---------------------------------------------------------------------------
# Read and write are separate grants and need separate answers
#
# A Cloudflare R2 token exposes Read and Edit as independent checkboxes, and
# the pipeline needs both: push_corpus writes, pull_corpus reads. Reporting
# only "denied" would send someone to fix the wrong checkbox.
# ---------------------------------------------------------------------------


def test_write_denied_but_read_granted_is_reported_as_such(monkeypatch, r2_env):
    # The Edit-only token: GetObject on a missing key returns NoSuchKey, which
    # means the request was authorised and simply found nothing.
    fake = _FakeR2WithBuckets(["bigthink-corpus"], deny={"PutObject"})
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "read=granted, write=denied" in detail


def test_write_and_read_both_denied_is_reported_as_such(monkeypatch, r2_env):
    fake = _FakeR2WithBuckets(["bigthink-corpus"], deny={"PutObject", "GetObject"})
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "read=denied, write=denied" in detail


def test_read_probe_names_both_operations_as_needed(monkeypatch, r2_env):
    fake = _FakeR2WithBuckets(["bigthink-corpus"], deny={"PutObject"})
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "push_corpus" in detail and "pull_corpus" in detail


def test_read_permission_probe_is_skipped_when_the_write_succeeded(monkeypatch, r2_env):
    # Nothing to diagnose on the happy path, and no extra call to spend.
    fake = _FakeR2WithBuckets(["bigthink-corpus"])
    _install(monkeypatch, fake)
    result = check_r2("bigthink-corpus")
    assert result.symbol == "PASS"
    assert "Token grants" not in result.detail


def test_read_permission_returns_unknown_for_an_unexpected_error(monkeypatch, r2_env):
    from src.verify_access import _read_permission

    class _Boom:
        def get_object(self, Bucket, Key):  # noqa: N803
            raise _FakeClientError("InternalError")

    assert _read_permission(_Boom(), "bigthink-corpus") == "unknown"


def test_both_denied_points_at_a_stale_or_unsaved_token(monkeypatch, r2_env):
    # The observed case: the Cloudflare UI shows Edit granted on the right
    # bucket, and the key pair in use can do nothing. Those cannot both
    # describe the same token.
    fake = _FakeR2WithBuckets([], deny={"PutObject", "GetObject"}, deny_list_buckets=True)
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "R2_ACCESS_KEY_ID" in detail
    assert "not saved" in detail or "was not saved" in detail


def test_read_granted_case_does_not_claim_a_stale_token(monkeypatch, r2_env):
    fake = _FakeR2WithBuckets(["bigthink-corpus"], deny={"PutObject"})
    _install(monkeypatch, fake)
    detail = check_r2("bigthink-corpus").detail
    assert "read=granted" in detail
    assert "different token's access key" not in detail


# ---------------------------------------------------------------------------
# The check must talk to the same endpoint the pipeline does
# ---------------------------------------------------------------------------


def test_check_r2_uses_the_jurisdiction_endpoint(monkeypatch, r2_env):
    # A check that builds its own endpoint can pass against one storage.py
    # never uses, which is worse than no check at all.
    import boto3

    captured = {}

    def fake_client(service, **kwargs):
        captured.update(kwargs)
        return _FakeR2()

    monkeypatch.setattr(boto3, "client", fake_client)
    check_r2("bigthink-corpus", jurisdiction_name="eu")
    assert captured["endpoint_url"] == f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"


def test_check_r2_names_the_jurisdiction_in_its_result(monkeypatch, r2_env):
    _install(monkeypatch, _FakeR2())
    assert "eu jurisdiction" in check_r2("bigthink-corpus", jurisdiction_name="eu").name


def test_check_r2_without_a_jurisdiction_uses_the_default_endpoint(monkeypatch, r2_env):
    import boto3

    captured = {}
    monkeypatch.setattr(boto3, "client", lambda service, **kw: (captured.update(kw), _FakeR2())[1])
    check_r2("bigthink-corpus")
    assert captured["endpoint_url"] == f"https://{ACCOUNT}.r2.cloudflarestorage.com"


# ---------------------------------------------------------------------------
# A malformed account ID must be named, not crashed on
#
# botocore raises a bare ValueError naming only the URL it could not parse,
# and in CI that URL is secret-masked — so the message reduces to
# "Invalid endpoint: https://***.eu.r2.cloudflarestorage.com", which says
# nothing about what to fix.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "6cd7669a5e77a844abc49b6a0eecd0a3_eu_bigthink-corpus",  # the resource key
        "https://6cd7669a5e77a844abc49b6a0eecd0a3.r2.cloudflarestorage.com",
        "too-short",
        "6cd7669a5e77a844abc49b6a0eecd0a",  # 31 characters
        "",
    ],
)
def test_malformed_account_id_is_reported_not_raised(monkeypatch, r2_env, value):
    monkeypatch.setenv("R2_ACCOUNT_ID", value)
    result = check_r2("bigthink-corpus", jurisdiction_name="eu")
    assert result.symbol in ("FAIL", "SKIP")
    if result.symbol == "FAIL":
        assert "32 hexadecimal" in result.detail


def test_malformed_account_id_reports_its_length(monkeypatch, r2_env):
    monkeypatch.setenv("R2_ACCOUNT_ID", "6cd7669a5e77a844abc49b6a0eecd0a3_eu_bigthink-corpus")
    assert "51 characters" in check_r2("bigthink-corpus").detail


def test_malformed_account_id_points_at_the_jurisdiction_setting(monkeypatch, r2_env):
    monkeypatch.setenv("R2_ACCOUNT_ID", "6cd7669a5e77a844abc49b6a0eecd0a3_eu_bigthink-corpus")
    assert "storage.r2.jurisdiction" in check_r2("bigthink-corpus").detail


def test_a_well_formed_account_id_proceeds(monkeypatch, r2_env):
    _install(monkeypatch, _FakeR2())
    assert check_r2("bigthink-corpus").symbol == "PASS"


def test_uppercase_account_id_is_accepted(monkeypatch, r2_env):
    monkeypatch.setenv("R2_ACCOUNT_ID", "6CD7669A5E77A844ABC49B6A0EECD0A3")
    _install(monkeypatch, _FakeR2())
    assert check_r2("bigthink-corpus").symbol == "PASS"


# ---------------------------------------------------------------------------
# A refused TLS handshake is a statement about the account ID
#
# Cloudflare completes the handshake for a real account and returns an S3
# error; for a well-formed but unknown account ID it refuses the handshake on
# both the default and jurisdiction endpoints. So this is not a TLS problem,
# not a network problem, and above all not a permissions problem — nothing was
# ever authorised or denied.
# ---------------------------------------------------------------------------


class _FakeSSLError(Exception):
    def __init__(self) -> None:
        super().__init__(
            "SSL validation failed for https://x.eu.r2.cloudflarestorage.com "
            "[SSL: SSLV3_ALERT_HANDSHAKE_FAILURE] sslv3 alert handshake failure"
        )


_FakeSSLError.__name__ = "SSLError"


class _UnreachableR2(_FakeR2):
    def _guard(self, op):
        self.calls.append(op)
        raise _FakeSSLError()


def test_handshake_failure_blames_the_account_id(monkeypatch, r2_env):
    _install(monkeypatch, _UnreachableR2())
    detail = check_r2("bigthink-corpus", jurisdiction_name="eu").detail
    assert "R2_ACCOUNT_ID" in detail
    assert "does not recognise" in detail


def test_handshake_failure_is_not_reported_as_a_permissions_problem(monkeypatch, r2_env):
    _install(monkeypatch, _UnreachableR2())
    detail = check_r2("bigthink-corpus", jurisdiction_name="eu").detail
    assert "read=" not in detail
    assert "Object Read & Write" not in detail


def test_handshake_failure_names_the_access_key_mixup(monkeypatch, r2_env):
    _install(monkeypatch, _UnreachableR2())
    assert "Access Key ID" in check_r2("bigthink-corpus").detail


def test_connection_error_detection():
    from src.verify_access import _is_connection_error

    assert _is_connection_error(_FakeSSLError()) is True
    assert _is_connection_error(_FakeClientError("AccessDenied")) is False


def test_access_denied_still_reports_grants(monkeypatch, r2_env):
    # The permissions narrative must survive for the case it is actually about.
    fake = _FakeR2WithBuckets(["bigthink-corpus"], deny={"PutObject"})
    _install(monkeypatch, fake)
    assert "read=granted" in check_r2("bigthink-corpus").detail
