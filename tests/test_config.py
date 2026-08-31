"""Tests for config loading and validation (src/config.py)."""

from __future__ import annotations

import copy

import pytest
import yaml

from src.config import (
    REPO_ROOT,
    _validate,
    load_config,
    snapshot_config,
    topic_similarity_threshold,
)
from src.errors import ConfigError


@pytest.fixture()
def raw():
    with (REPO_ROOT / "bigthink_config.yaml").open(encoding="utf-8") as fh:
        return yaml.safe_load(fh)


def test_shipped_config_validates():
    load_config()


def test_snapshot_omits_internal_keys():
    snapshot = snapshot_config(load_config())
    assert "_config_path" not in snapshot
    assert "rotolo_weights" in snapshot


@pytest.mark.parametrize(
    "mutate, expected",
    [
        (lambda c: c["emergence"]["rotolo_weights"].update(novelty=0.9), "sum to 1.0"),
        (lambda c: c["emergence"]["burst"].update(s=0.5), "must be > 1.0"),
        (lambda c: c["collection"]["sources"].update(scopus={"enabled": True}), "unknown source"),
        (lambda c: c["collection"]["steepv_default_by_source"].update(gdelt="Vibes"), "STEEPV"),
        (lambda c: c["emergence"]["three_horizons"].update(h1_max_maturity=0.2), "three_horizons"),
        (lambda c: c["scoring"]["strategic_fit"].update(embedding_weight=0.9), "sum to 1.0"),
        (lambda c: c["pipeline"].update(contact_email="nope"), "contact_email"),
        (lambda c: c["synthesis"].update(evidence_documents_per_topic=0), "audit trail"),
        (lambda c: c["opportunity_index"]["components"].update(attention=0.9), "sum to 1.0"),
        (lambda c: c["embeddings"].update(backend="word2vec"), "embeddings.backend"),
        (lambda c: c["notebook"].update(topics_detailed=0), "notebook.topics_detailed"),
        (lambda c: c["notebook"].update(include_verification="yes"), "must be true or false"),
    ],
)
def test_validation_rejects_bad_values(raw, mutate, expected):
    config = copy.deepcopy(raw)
    mutate(config)
    with pytest.raises(ConfigError, match=expected):
        _validate(config)


def test_config_without_a_notebook_section_still_validates(raw):
    """The notebook export computes no score, so a config predating it must
    still load and still run the pipeline."""
    config = copy.deepcopy(raw)
    del config["notebook"]
    _validate(config)


def test_all_sources_disabled_is_rejected(raw):
    config = copy.deepcopy(raw)
    for source in config["collection"]["sources"].values():
        source["enabled"] = False
    with pytest.raises(ConfigError, match="no enabled source"):
        _validate(config)


def test_rotolo_weights_must_name_exactly_the_five_attributes(raw):
    config = copy.deepcopy(raw)
    config["emergence"]["rotolo_weights"] = {"novelty": 0.5, "growth": 0.5}
    with pytest.raises(ConfigError, match="five Rotolo"):
        _validate(config)


def test_similarity_threshold_follows_the_active_backend():
    """Cosine values differ by an order of magnitude between backends. Reading
    the wrong one produces either one giant topic or none, and both look like
    data problems rather than config ones."""
    config = load_config()
    config["embeddings"]["backend"] = "hashing"
    hashing = topic_similarity_threshold(config)
    config["embeddings"]["backend"] = "bge"
    assert topic_similarity_threshold(config) > hashing


def test_missing_threshold_for_active_backend_raises():
    config = load_config()
    del config["emergence"]["topics"]["similarity_thresholds"]["agglomerative"]["hashing"]
    with pytest.raises(ConfigError, match="No emergence.topics"):
        topic_similarity_threshold(config)


def test_the_threshold_depends_on_the_method_as_well_as_the_backend():
    """`leader` compares to a centroid, `agglomerative` to a mean pairwise
    similarity. On the same 2,987-document corpus the leader value of 0.30
    assigned 23 documents under average linkage. One number cannot serve both."""
    config = load_config()
    config["emergence"]["topics"]["method"] = "agglomerative"
    agglomerative = topic_similarity_threshold(config)
    config["emergence"]["topics"]["method"] = "leader"
    assert topic_similarity_threshold(config) != agglomerative


def test_the_pre_2026_08_30_threshold_shape_is_still_honoured():
    """Old runs must stay reproducible from their own config snapshot."""
    config = load_config()
    del config["emergence"]["topics"]["similarity_thresholds"]
    config["emergence"]["topics"]["similarity_threshold_by_backend"] = {"hashing": 0.30}
    assert topic_similarity_threshold(config) == 0.30


def test_an_unknown_method_and_backend_pairing_is_refused():
    config = load_config()
    del config["emergence"]["topics"]["similarity_thresholds"]
    with pytest.raises(ConfigError, match="No clustering threshold"):
        topic_similarity_threshold(config)


def test_r2_jurisdiction_accepts_the_known_values():
    from src.config import _validate_storage

    for value in ("", "default", "eu", "fedramp", "EU"):
        _validate_storage({"r2": {"enabled": True, "bucket": "b", "jurisdiction": value}})


def test_r2_jurisdiction_rejects_a_typo():
    # A bad value here does not fail loudly at runtime — it builds a valid
    # endpoint in a jurisdiction the token has no resources in, and every
    # call comes back AccessDenied as though the permissions were wrong.
    from src.config import _validate_storage
    from src.errors import ConfigError

    with pytest.raises(ConfigError, match="jurisdiction"):
        _validate_storage({"r2": {"enabled": True, "bucket": "b", "jurisdiction": "europe"}})


def test_r2_jurisdiction_is_optional():
    from src.config import _validate_storage

    _validate_storage({"r2": {"enabled": True, "bucket": "b"}})
