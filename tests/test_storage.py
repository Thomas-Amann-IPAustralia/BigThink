"""Tests for src/storage.py (Cloudflare R2 sync). No network calls: every
test injects a fake client rather than touching boto3 or the network."""

from __future__ import annotations

import copy

import pytest

from src import storage
from src.config import load_config
from src.errors import ConfigError


class FakeClientError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.response = {"Error": {"Code": code}}


class FakeS3Client:
    """Records calls and serves files from an in-memory dict keyed by
    (bucket, key) -> local file contents, mimicking just enough of boto3's
    S3 client surface for storage.py to exercise."""

    def __init__(self) -> None:
        self.objects: dict[tuple[str, str], bytes] = {}
        self.uploaded: list[tuple[str, str, str]] = []
        self.downloaded: list[tuple[str, str, str]] = []

    def upload_file(self, path: str, bucket: str, key: str) -> None:
        with open(path, "rb") as fh:
            self.objects[(bucket, key)] = fh.read()
        self.uploaded.append((path, bucket, key))

    def download_file(self, bucket: str, key: str, dest: str) -> None:
        if (bucket, key) not in self.objects:
            raise FakeClientError("NoSuchKey")
        with open(dest, "wb") as fh:
            fh.write(self.objects[(bucket, key)])
        self.downloaded.append((bucket, key, dest))

    def get_paginator(self, name: str):
        assert name == "list_objects_v2"
        return _FakePaginator(self)


class _FakePaginator:
    def __init__(self, client: FakeS3Client) -> None:
        self._client = client

    def paginate(self, Bucket: str, Prefix: str):  # noqa: N803 - mirrors boto3's kwargs
        contents = [
            {"Key": key}
            for (bucket, key) in self._client.objects
            if bucket == Bucket and key.startswith(Prefix)
        ]
        yield {"Contents": contents}


@pytest.fixture()
def r2_config():
    config = copy.deepcopy(load_config())
    config["storage"]["r2"]["enabled"] = True
    config["storage"]["r2"]["bucket"] = "test-bucket"
    return config


@pytest.fixture()
def disabled_config():
    config = copy.deepcopy(load_config())
    config["storage"]["r2"]["enabled"] = False
    return config


# ---------------------------------------------------------------------------
# Disabled by default
# ---------------------------------------------------------------------------


def test_shipped_config_enables_r2():
    # Enabled 2026-08-30, once verify-access.yml confirmed a full
    # write/read/delete round-trip against the real bucket. Before that it
    # shipped disabled, because a run that believes it is persisting a corpus
    # and is not would be worse than one that never tried.
    assert storage.is_enabled(load_config()) is True


def test_push_corpus_noop_when_disabled(disabled_config, tmp_path):
    db_path = tmp_path / "bigthink.duckdb"
    db_path.write_text("not a real db, just needs to exist")
    assert storage.push_corpus(disabled_config, db_path, client=FakeS3Client()) is False


def test_pull_corpus_noop_when_disabled(disabled_config, tmp_path):
    assert storage.pull_corpus(disabled_config, tmp_path / "bigthink.duckdb", client=FakeS3Client()) is False


# ---------------------------------------------------------------------------
# Enabled but missing credentials fails loudly
# ---------------------------------------------------------------------------


def test_missing_credentials_raises(r2_config, monkeypatch):
    monkeypatch.delenv("R2_ACCOUNT_ID", raising=False)
    monkeypatch.delenv("R2_ACCESS_KEY_ID", raising=False)
    monkeypatch.delenv("R2_SECRET_ACCESS_KEY", raising=False)
    with pytest.raises(ConfigError, match="R2_ACCOUNT_ID"):
        storage.get_client(r2_config)


def test_enabled_without_bucket_raises(r2_config):
    r2_config["storage"]["r2"]["bucket"] = ""
    with pytest.raises(ConfigError, match="storage.r2.bucket"):
        storage._bucket(r2_config)


# ---------------------------------------------------------------------------
# Corpus round-trip
# ---------------------------------------------------------------------------


def test_push_then_pull_corpus_round_trips(r2_config, tmp_path):
    client = FakeS3Client()
    src = tmp_path / "bigthink.duckdb"
    src.write_bytes(b"pretend duckdb bytes")

    assert storage.push_corpus(r2_config, src, client=client) is True
    assert client.uploaded == [(str(src), "test-bucket", "bigthink.duckdb")]

    dest = tmp_path / "restored" / "bigthink.duckdb"
    assert storage.pull_corpus(r2_config, dest, client=client) is True
    assert dest.read_bytes() == b"pretend duckdb bytes"


def test_pull_corpus_missing_object_returns_false(r2_config, tmp_path):
    client = FakeS3Client()
    dest = tmp_path / "bigthink.duckdb"
    assert storage.pull_corpus(r2_config, dest, client=client) is False
    assert not dest.exists()


def test_push_corpus_missing_local_file_returns_false(r2_config, tmp_path):
    client = FakeS3Client()
    assert storage.push_corpus(r2_config, tmp_path / "does-not-exist.duckdb", client=client) is False
    assert client.uploaded == []


# ---------------------------------------------------------------------------
# Raw payload round-trip
# ---------------------------------------------------------------------------


def test_push_then_pull_raw_round_trips(r2_config, tmp_path):
    client = FakeS3Client()
    run_dir = tmp_path / "raw" / "2026-08-29" / "arxiv"
    run_dir.mkdir(parents=True)
    (run_dir / "ct_quantum_p001.json").write_text('{"ok": true}')

    pushed = storage.push_raw(r2_config, "2026-08-29", raw_dir=tmp_path / "raw", client=client)
    assert pushed == 1
    assert client.uploaded[0][2] == "raw/2026-08-29/arxiv/ct_quantum_p001.json"

    pull_dest = tmp_path / "restored-raw"
    pulled = storage.pull_raw(r2_config, "2026-08-29", raw_dir=pull_dest, client=client)
    assert pulled == 1
    restored = pull_dest / "2026-08-29" / "arxiv" / "ct_quantum_p001.json"
    assert restored.read_text() == '{"ok": true}'


def test_push_raw_missing_run_dir_returns_zero(r2_config, tmp_path):
    client = FakeS3Client()
    assert storage.push_raw(r2_config, "no-such-run", raw_dir=tmp_path / "raw", client=client) == 0


# ---------------------------------------------------------------------------
# Jurisdiction endpoints
#
# An R2 bucket pinned to a jurisdiction lives on its own S3 endpoint and is
# invisible from the default one — every call returns AccessDenied, including
# ListBuckets, which reads exactly like a permissions problem and is not one.
# This cost a real investigation, so the mapping is pinned here.
# ---------------------------------------------------------------------------


ACCOUNT = "6cd7669a5e77a844abc49b6a0eecd0a3"


def test_default_endpoint_has_no_jurisdiction_segment():
    assert storage.endpoint_url(ACCOUNT) == f"https://{ACCOUNT}.r2.cloudflarestorage.com"


@pytest.mark.parametrize("name", ["", "default"])
def test_unrestricted_names_produce_the_default_endpoint(name):
    assert storage.endpoint_url(ACCOUNT, name) == f"https://{ACCOUNT}.r2.cloudflarestorage.com"


def test_eu_jurisdiction_gets_its_own_endpoint():
    assert storage.endpoint_url(ACCOUNT, "eu") == f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"


def test_jurisdiction_is_case_and_whitespace_insensitive():
    # Pasted out of the Cloudflare UI, a stray space or capital would
    # otherwise build a plausible endpoint that authenticates and denies.
    assert storage.endpoint_url(ACCOUNT, " EU ") == storage.endpoint_url(ACCOUNT, "eu")


def test_jurisdiction_reads_the_config(r2_config):
    r2_config["storage"]["r2"]["jurisdiction"] = "eu"
    assert storage.jurisdiction(r2_config) == "eu"


def test_jurisdiction_env_var_overrides_the_config(r2_config, monkeypatch):
    r2_config["storage"]["r2"]["jurisdiction"] = "eu"
    monkeypatch.setenv("R2_JURISDICTION", "fedramp")
    assert storage.jurisdiction(r2_config) == "fedramp"


def test_jurisdiction_absent_from_config_is_empty(r2_config, monkeypatch):
    monkeypatch.delenv("R2_JURISDICTION", raising=False)
    r2_config["storage"]["r2"].pop("jurisdiction", None)
    assert storage.jurisdiction(r2_config) == ""


def test_get_client_uses_the_jurisdiction_endpoint(r2_config, monkeypatch):
    # The regression that matters: the client the pipeline actually builds
    # must reach the jurisdiction the bucket is in.
    import boto3

    r2_config["storage"]["r2"]["jurisdiction"] = "eu"
    for name, value in (
        ("R2_ACCOUNT_ID", ACCOUNT),
        ("R2_ACCESS_KEY_ID", "key"),
        ("R2_SECRET_ACCESS_KEY", "secret"),
    ):
        monkeypatch.setenv(name, value)
    monkeypatch.delenv("R2_JURISDICTION", raising=False)

    captured = {}

    def fake_client(service, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(boto3, "client", fake_client)
    storage.get_client(r2_config)
    assert captured["endpoint_url"] == f"https://{ACCOUNT}.eu.r2.cloudflarestorage.com"
