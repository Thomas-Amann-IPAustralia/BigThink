"""
src/stage0_strategy.py — Stage 0: Strategy encoding.

Turns IP Australia's published strategy into the fixed reference set that every
detected trend is later scored against:

  * strategic objectives and significant initiatives   (data/strategy/objectives.yaml)
  * DISR critical technology fields                    (data/strategy/critical_technologies.yaml)
  * IP Australia's own data / capability assets        (data/strategy/asset_inventory.yaml)

Each becomes a row in `strategy_refs` carrying both a text block (embedded at
scoring time) and a hand-written lexicon (matched lexically). Both are needed:
embeddings catch a trend that means the same thing in different words, the
lexicon catches a trend that names the thing exactly and would otherwise be
diluted by surrounding boilerplate.

Cheap and deterministic — re-run it whenever a strategy file changes. It writes
nothing that depends on collected data, so it can run before Stage 1.

Run:
    python -m src.stage0_strategy
    python -m src.stage0_strategy --show
"""

from __future__ import annotations

import argparse
import hashlib
import logging
import sys
from pathlib import Path
from typing import Any

import yaml

from src import db
from src.config import REPO_ROOT, get, load_config, resolve_path, snapshot_config
from src.errors import ConfigError

logger = logging.getLogger(__name__)

STAGE = "stage0_strategy"


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def _load_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        raise ConfigError(f"Stage 0 input not found: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{path} must be a YAML mapping at the top level.")
    return data


def _ref_id(ref_type: str, code: str) -> str:
    return f"{ref_type}:{code}"


def _clean(text: str) -> str:
    """Collapse the whitespace YAML block scalars leave behind."""
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------------------
# Reference construction
# ---------------------------------------------------------------------------


def build_strategy_refs(config: dict[str, Any]) -> list[dict[str, Any]]:
    """Build the full strategic reference set from the Stage 0 input files."""
    refs: list[dict[str, Any]] = []

    # --- Objectives and initiatives -------------------------------------
    obj_path = resolve_path(config, "strategy", "objectives_file")
    objectives = _load_yaml(obj_path)
    source_doc = objectives.get("source_document", str(obj_path.name))

    for kind, ref_type in (("objectives", "objective"), ("initiatives", "initiative")):
        for entry in objectives.get(kind, []) or []:
            code = str(entry["code"])
            refs.append(
                {
                    "ref_id": _ref_id(ref_type, code),
                    "ref_type": ref_type,
                    "code": code,
                    "label": entry["label"],
                    # Label is prepended to the text so the embedded block leads
                    # with the distinctive words rather than shared plan prose.
                    "text": f"{entry['label']}. {_clean(entry['text'])}",
                    "lexicon": [str(t).lower() for t in entry.get("lexicon", [])],
                    "weight": float(entry.get("weight", 1.0)),
                    "source_document": source_doc,
                }
            )

    # --- DISR critical technologies -------------------------------------
    ct_path = resolve_path(config, "strategy", "critical_technologies_file")
    critical = _load_yaml(ct_path)
    if not critical.get("verified", False):
        logger.warning(
            "%s is marked verified: false — the critical technology list has not been "
            "checked against the current DISR publication. Confirm it before briefing "
            "any finding that leans on the critical-tech bonus.",
            ct_path.name,
        )
    for field in critical.get("fields", []) or []:
        code = str(field["code"])
        lexicon = [str(t).lower() for t in field.get("lexicon", [])]
        refs.append(
            {
                "ref_id": _ref_id("critical_tech", code),
                "ref_type": "critical_tech",
                "code": code,
                "label": field["label"],
                # These fields have no prose in the source list, so the lexicon
                # is the text. That is honest: it is all DISR publishes.
                "text": f"{field['label']}. {', '.join(lexicon)}.",
                "lexicon": lexicon,
                "weight": 1.0,
                "source_document": critical.get("source", "DISR critical technologies"),
            }
        )

    # --- Asset inventory -------------------------------------------------
    asset_path = resolve_path(config, "scoring", "asset_leverage", "inventory_file")
    assets = _load_yaml(asset_path)
    for asset in assets.get("assets", []) or []:
        code = str(asset["code"])
        refs.append(
            {
                "ref_id": _ref_id("asset", code),
                "ref_type": "asset",
                "code": code,
                "label": asset["label"],
                "text": f"{asset['label']}. {_clean(asset['text'])}",
                "lexicon": [str(t).lower() for t in asset.get("lexicon", [])],
                "weight": float(asset.get("strength", 1.0)),
                "source_document": f"asset_inventory.yaml ({asset.get('type', 'unknown')})",
            }
        )

    _validate_refs(refs)
    return refs


def _validate_refs(refs: list[dict[str, Any]]) -> None:
    """Catch the failure modes that would silently flatten downstream scores."""
    if not refs:
        raise ConfigError("Stage 0 produced no strategy references.")

    seen: set[str] = set()
    for r in refs:
        if r["ref_id"] in seen:
            raise ConfigError(f"Duplicate strategy ref_id: {r['ref_id']}")
        seen.add(r["ref_id"])
        if len(r["text"]) < 40:
            raise ConfigError(
                f"Strategy ref {r['ref_id']} has a text block of {len(r['text'])} chars. "
                "Too short to embed meaningfully — every topic would score near-identically "
                "against it."
            )
        if not r["lexicon"]:
            raise ConfigError(
                f"Strategy ref {r['ref_id']} has an empty lexicon. The lexical half of "
                "the fit score would be zero for this reference regardless of the trend."
            )

    for required in ("objective", "critical_tech", "asset"):
        if not any(r["ref_type"] == required for r in refs):
            raise ConfigError(f"Stage 0 produced no references of type '{required}'.")


# ---------------------------------------------------------------------------
# Strategy corpus (for policy-salience scoring at Stage 4)
# ---------------------------------------------------------------------------


def load_strategy_corpus(config: dict[str, Any]) -> str:
    """Concatenated text of the strategy documents, lowercased.

    Stage 4's policy-salience component counts how often a topic's terms appear
    here. Kept as one flat string because that is all the measure needs, and a
    document-structure parser is one more thing to keep correct for no gain.
    """
    parts: list[str] = []
    for doc in get(config, "strategy", "documents", default=[]) or []:
        path = Path(doc["path"])
        full = path if path.is_absolute() else REPO_ROOT / path
        if not full.exists():
            logger.warning("Strategy document not found, skipping: %s", full)
            continue
        parts.append(full.read_text(encoding="utf-8", errors="replace").lower())
    if not parts:
        logger.warning(
            "No strategy documents loaded — Stage 4 policy salience will be zero for "
            "every topic, which flattens that component of the opportunity index."
        )
    return "\n".join(parts)


def strategy_corpus_fingerprint(corpus: str) -> str:
    return hashlib.sha256(corpus.encode("utf-8")).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def run(config: dict[str, Any], run_id: str) -> list[dict[str, Any]]:
    """Build and persist the strategic reference set."""
    conn = db.init_db(resolve_path(config, "storage", "duckdb_path"))
    entry_id = db.log_stage_start(conn, run_id, STAGE, snapshot_config(config))
    try:
        refs = build_strategy_refs(config)
        written = db.replace_strategy_refs(conn, refs)

        corpus = load_strategy_corpus(config)
        message = (
            f"{written} refs "
            f"({sum(r['ref_type'] == 'objective' for r in refs)} objectives, "
            f"{sum(r['ref_type'] == 'initiative' for r in refs)} initiatives, "
            f"{sum(r['ref_type'] == 'critical_tech' for r in refs)} critical-tech fields, "
            f"{sum(r['ref_type'] == 'asset' for r in refs)} assets); "
            f"strategy corpus {len(corpus):,} chars "
            f"(fingerprint {strategy_corpus_fingerprint(corpus)})"
        )
        db.log_stage_finish(conn, entry_id, "success", records_out=written, message=message)
        logger.info("Stage 0 complete: %s", message)
        return refs
    except Exception as exc:
        db.log_stage_finish(conn, entry_id, "failed", message=str(exc))
        raise
    finally:
        conn.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage 0 — encode IP Australia strategy.")
    parser.add_argument("--config", default=None)
    parser.add_argument("--run-id", default="stage0")
    parser.add_argument("--log-level", default="INFO")
    parser.add_argument("--show", action="store_true", help="Print the reference set and exit.")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=args.log_level.upper(), format="%(asctime)s %(levelname)-7s %(name)s: %(message)s"
    )
    config = load_config(args.config)

    if args.show:
        for ref in build_strategy_refs(config):
            print(
                f"{ref['ref_type']:14s} {str(ref['code']):12s} {ref['label'][:56]:58s} "
                f"w={ref['weight']:.2f} lex={len(ref['lexicon'])}"
            )
        return 0

    run(config, args.run_id)
    return 0


if __name__ == "__main__":
    sys.exit(main())
