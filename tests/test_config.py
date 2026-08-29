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
    ],
)
def test_validation_rejects_bad_values(raw, mutate, expected):
    config = copy.deepcopy(raw)
    mutate(config)
    with pytest.raises(ConfigError, match=expected):
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
    del config["emergence"]["topics"]["similarity_threshold_by_backend"]["hashing"]
    with pytest.raises(ConfigError, match="No emergence.topics"):
        topic_similarity_threshold(config)
