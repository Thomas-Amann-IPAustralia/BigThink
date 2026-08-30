"""
src/config.py

Load and validate bigthink_config.yaml.

Adapted from Tripwire (Thomas-Amann-IPAustralia/Tripwire, src/config.py).
Validation runs at the start of every stage; a bad config fails before any
network call is made, not three hours into a scan.

The weight-sum checks matter more here than they did in Tripwire: the Rotolo
emergence score, the opportunity index and the final ranking are all convex
combinations, and a set of weights that does not sum to 1.0 silently rescales
every result. That is the kind of error a horizon scan can carry all the way
to a briefing without anyone noticing.
"""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

import yaml

from src.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parent.parent
_DEFAULT_CONFIG_PATH = REPO_ROOT / "bigthink_config.yaml"

_VALID_EMBEDDING_BACKENDS = {"hashing", "bge"}
_VALID_TOPIC_METHODS = {"agglomerative", "bertopic"}
_VALID_TIME_SLICES = {"year", "quarter"}
_KNOWN_SOURCES = {
    "openalex",
    "arxiv",
    "crossref",
    "gdelt",
    "datagovau",
    "patentsview",
}
# STEEPV: the horizon-scanning taxonomy from the UK GO-Science Futures Toolkit,
# extended with Values as the scan frame in SuggestedConceptualApproach.md uses.
STEEPV_CATEGORIES = {
    "Social",
    "Technological",
    "Economic",
    "Environmental",
    "Political",
    "Values",
    "Legal",
}

_WEIGHT_SUM_TOLERANCE = 1e-6


# ---------------------------------------------------------------------------
# Public interface
# ---------------------------------------------------------------------------


def load_config(config_path: str | Path | None = None) -> dict[str, Any]:
    """Load bigthink_config.yaml and return the validated config dict."""
    path = Path(config_path) if config_path is not None else _DEFAULT_CONFIG_PATH
    if not path.exists():
        raise ConfigError(f"Config file not found: {path}")

    try:
        with path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{path} must be a YAML mapping at the top level.")

    _validate(raw)
    raw["_config_path"] = str(path)
    return raw


def get(config: dict[str, Any], *keys: str, default: Any = None) -> Any:
    """Safely retrieve a nested value.

    >>> get(cfg, "emergence", "burst", "gamma", default=1.0)
    1.0
    """
    node: Any = config
    for key in keys:
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node if node is not None else default


def resolve_path(config: dict[str, Any], *keys: str, default: str | None = None) -> Path:
    """Resolve a repo-relative config path to an absolute Path."""
    value = get(config, *keys, default=default)
    if value is None:
        raise ConfigError(f"Missing path config for {'.'.join(keys)}")
    p = Path(value)
    return p if p.is_absolute() else REPO_ROOT / p


def snapshot_config(config: dict[str, Any]) -> str:
    """Compact, sorted JSON of the config, stored against each run.

    Every run row in DuckDB carries one of these. It is the difference between
    'the score changed' and 'the score changed because we moved a threshold'.
    """
    clean = {k: v for k, v in copy.deepcopy(config).items() if not k.startswith("_")}
    return json.dumps(clean, sort_keys=True, default=str)


def topic_similarity_threshold(config: dict[str, Any]) -> float:
    """Clustering threshold for the *currently selected* embedding backend.

    Kept as a function rather than a plain config read so there is exactly one
    place where the backend/threshold pairing is resolved. Reading the wrong
    backend's threshold produces either one giant topic or none at all, and
    both failure modes look like a data problem rather than a config one.
    """
    backend = str(get(config, "embeddings", "backend", default="hashing"))
    thresholds = get(config, "emergence", "topics", "similarity_threshold_by_backend", default={})
    if backend not in thresholds:
        raise ConfigError(
            f"No emergence.topics.similarity_threshold_by_backend entry for active "
            f"backend {backend!r}. Add one before running Stage 2."
        )
    return float(thresholds[backend])


def contact_email(config: dict[str, Any]) -> str:
    """Contact address for API polite pools; env var wins over config."""
    return os.environ.get("BIGTHINK_CONTACT_EMAIL") or str(
        get(config, "pipeline", "contact_email", default="")
    )


def user_agent(config: dict[str, Any]) -> str:
    return str(get(config, "pipeline", "user_agent", default="IPAVentures-BigThink/0.1"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


def _validate(cfg: dict[str, Any]) -> None:
    for section in (
        "pipeline",
        "storage",
        "embeddings",
        "strategy",
        "collection",
        "emergence",
        "scoring",
        "opportunity_index",
        "synthesis",
    ):
        if section not in cfg or not isinstance(cfg[section], dict):
            raise ConfigError(f"Config is missing required section: '{section}'")

    _validate_pipeline(cfg["pipeline"])
    _validate_storage(cfg["storage"])
    _validate_embeddings(cfg["embeddings"])
    _validate_collection(cfg["collection"])
    _validate_emergence(cfg["emergence"])
    _validate_scoring(cfg["scoring"])
    _validate_opportunity_index(cfg["opportunity_index"])
    _validate_synthesis(cfg["synthesis"])
    # Optional, and deliberately not in the required list above: the notebook
    # export produces no score, so a config predating it must still load and
    # still run the pipeline. It is validated only when present.
    if "notebook" in cfg:
        _validate_notebook(cfg["notebook"])


def _validate_pipeline(p: dict[str, Any]) -> None:
    if int(p.get("max_retries", 3)) < 0:
        raise ConfigError("pipeline.max_retries must be >= 0")
    if float(p.get("retry_base_delay_seconds", 2.0)) <= 0:
        raise ConfigError("pipeline.retry_base_delay_seconds must be > 0")
    email = str(p.get("contact_email", "") or "")
    # OpenAlex and Crossref polite pools require a real address; a scan that
    # silently drops to the common pool gets rate-limited into uselessness.
    if "@" not in email:
        raise ConfigError(
            "pipeline.contact_email must be a valid address — OpenAlex and "
            "Crossref polite-pool access depends on it."
        )


def _validate_storage(s: dict[str, Any]) -> None:
    r2 = s.get("r2", {}) or {}
    if bool(r2.get("enabled")) and not str(r2.get("bucket", "") or "").strip():
        raise ConfigError(
            "storage.r2.bucket must be set when storage.r2.enabled is true."
        )


def _validate_embeddings(e: dict[str, Any]) -> None:
    backend = str(e.get("backend", ""))
    if backend not in _VALID_EMBEDDING_BACKENDS:
        raise ConfigError(
            f"embeddings.backend must be one of {sorted(_VALID_EMBEDDING_BACKENDS)}, got {backend!r}"
        )
    dims = int(e.get("hashing_dimensions", 2048))
    if dims < 256:
        raise ConfigError("embeddings.hashing_dimensions must be >= 256")


def _validate_collection(c: dict[str, Any]) -> None:
    start = int(c.get("start_year", 0))
    end = int(c.get("end_year", 0))
    if start < 1900 or end < start:
        raise ConfigError(
            f"collection.start_year/end_year invalid: {start}–{end}"
        )
    sources = c.get("sources", {}) or {}
    unknown = set(sources) - _KNOWN_SOURCES
    if unknown:
        raise ConfigError(
            f"collection.sources contains unknown source(s): {sorted(unknown)}. "
            f"Known sources: {sorted(_KNOWN_SOURCES)}"
        )
    if not any(bool(s.get("enabled")) for s in sources.values() if isinstance(s, dict)):
        raise ConfigError("collection.sources has no enabled source — nothing to collect.")

    for name, cat in (c.get("steepv_default_by_source", {}) or {}).items():
        if cat not in STEEPV_CATEGORIES:
            raise ConfigError(
                f"collection.steepv_default_by_source.{name} = {cat!r} is not a "
                f"STEEPV category {sorted(STEEPV_CATEGORIES)}"
            )


def _validate_emergence(e: dict[str, Any]) -> None:
    slice_ = str(e.get("time_slice", "year"))
    if slice_ not in _VALID_TIME_SLICES:
        raise ConfigError(
            f"emergence.time_slice must be one of {sorted(_VALID_TIME_SLICES)}, got {slice_!r}"
        )
    if int(e.get("min_docs_per_topic", 20)) < 1:
        raise ConfigError("emergence.min_docs_per_topic must be >= 1")

    burst = e.get("burst", {}) or {}
    if float(burst.get("s", 2.0)) <= 1.0:
        raise ConfigError(
            "emergence.burst.s must be > 1.0 — the burst state must have a higher "
            "rate than the base state or the automaton can never enter it."
        )
    if float(burst.get("gamma", 1.0)) < 0:
        raise ConfigError("emergence.burst.gamma must be >= 0")

    topics = e.get("topics", {}) or {}
    method = str(topics.get("method", "agglomerative"))
    if method not in _VALID_TOPIC_METHODS:
        raise ConfigError(
            f"emergence.topics.method must be one of {sorted(_VALID_TOPIC_METHODS)}, got {method!r}"
        )
    by_backend = topics.get("similarity_threshold_by_backend", {}) or {}
    if not isinstance(by_backend, dict) or not by_backend:
        raise ConfigError(
            "emergence.topics.similarity_threshold_by_backend must map each embedding "
            "backend to its own threshold — cosine values are not comparable across backends."
        )
    for backend, value in by_backend.items():
        if backend not in _VALID_EMBEDDING_BACKENDS:
            raise ConfigError(
                f"emergence.topics.similarity_threshold_by_backend has unknown backend {backend!r}"
            )
        if not 0.0 < float(value) < 1.0:
            raise ConfigError(
                f"emergence.topics.similarity_threshold_by_backend.{backend} must be in (0, 1)"
            )
    ratio = float(topics.get("attachment_threshold_ratio", 0.6))
    if not 0.0 < ratio <= 1.0:
        raise ConfigError(
            "emergence.topics.attachment_threshold_ratio must be in (0, 1]. Above 1.0 "
            "would make attachment stricter than clustering, which cannot help — a "
            "document that fails the looser test never reaches attachment."
        )

    _require_weight_sum(e.get("rotolo_weights", {}), "emergence.rotolo_weights")
    expected_attrs = {"novelty", "growth", "coherence", "impact", "uncertainty"}
    got = set(e.get("rotolo_weights", {}))
    if got != expected_attrs:
        raise ConfigError(
            "emergence.rotolo_weights must name exactly the five Rotolo et al. (2015) "
            f"attributes {sorted(expected_attrs)}; got {sorted(got)}"
        )

    th = e.get("three_horizons", {}) or {}
    h1, h2 = float(th.get("h1_max_maturity", 0.75)), float(th.get("h2_max_maturity", 0.35))
    if not 0.0 < h2 < h1 < 1.0:
        raise ConfigError(
            "emergence.three_horizons requires 0 < h2_max_maturity < h1_max_maturity < 1"
        )


def _validate_scoring(s: dict[str, Any]) -> None:
    for key in ("strategic_fit", "asset_leverage"):
        block = s.get(key, {}) or {}
        ew = float(block.get("embedding_weight", 0.0))
        lw = float(block.get("lexicon_weight", 0.0))
        if abs((ew + lw) - 1.0) > _WEIGHT_SUM_TOLERANCE:
            raise ConfigError(
                f"scoring.{key}: embedding_weight + lexicon_weight must sum to 1.0 "
                f"(got {ew} + {lw} = {ew + lw})"
            )


def _validate_opportunity_index(o: dict[str, Any]) -> None:
    _require_weight_sum(o.get("components", {}), "opportunity_index.components")
    if int(o.get("min_documents", 15)) < 1:
        raise ConfigError("opportunity_index.min_documents must be >= 1")


def _validate_synthesis(s: dict[str, Any]) -> None:
    if int(s.get("shortlist_size", 15)) < 1:
        raise ConfigError("synthesis.shortlist_size must be >= 1")
    _require_weight_sum(s.get("rank_weights", {}), "synthesis.rank_weights")
    if int(s.get("evidence_documents_per_topic", 8)) < 1:
        raise ConfigError(
            "synthesis.evidence_documents_per_topic must be >= 1 — evidence cards are "
            "the audit trail for every score this pipeline produces."
        )


def _validate_notebook(n: Any) -> None:
    if not isinstance(n, dict):
        raise ConfigError("notebook must be a mapping if present.")
    for key in ("topics_detailed", "evidence_documents_per_topic"):
        if key in n:
            try:
                value = int(n[key])
            except (TypeError, ValueError):
                raise ConfigError(f"notebook.{key} must be an integer, got {n[key]!r}") from None
            if value < 1:
                raise ConfigError(
                    f"notebook.{key} must be >= 1 — a notebook with no evidence in it "
                    "is not a peer-review artefact."
                )
    for key in ("enabled", "include_verification"):
        if key in n and not isinstance(n[key], bool):
            raise ConfigError(f"notebook.{key} must be true or false, got {n[key]!r}")


def _require_weight_sum(weights: Any, label: str, target: float = 1.0) -> None:
    if not isinstance(weights, dict) or not weights:
        raise ConfigError(f"{label} must be a non-empty mapping of weights.")
    for name, value in weights.items():
        if not isinstance(value, (int, float)) or value < 0:
            raise ConfigError(f"{label}.{name} must be a non-negative number, got {value!r}")
    total = float(sum(weights.values()))
    if abs(total - target) > 1e-3:
        raise ConfigError(
            f"{label} must sum to {target} (got {total:.4f}). These weights form a "
            "convex combination; a bad sum silently rescales every score."
        )
