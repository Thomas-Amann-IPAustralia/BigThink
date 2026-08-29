"""
src/embeddings.py

Text -> vector, behind one interface with two backends.

Why two:

  hashing  Dependency-free (numpy only). Hashed word uni/bigrams, sublinear TF,
           corpus-fitted IDF, L2-normalised. No model download, no torch, runs
           in any CI job in seconds. Good enough to build and test the whole
           pipeline against; good enough for term-level work.

  bge      BAAI/bge-base-en-v1.5 via sentence-transformers — the same
           bi-encoder Tripwire uses at Stage 5, so embeddings are comparable
           across the two systems and the model cache is shared. ~400 MB.

The backends are NOT interchangeable mid-project. Cosine values from hashing
are lexical-overlap-ish; cosine values from BGE are semantic. Thresholds
calibrated on one do not transfer to the other. `backend` is therefore part of
the vector cache key, and switching it invalidates the cache rather than
silently mixing vector spaces.

Rule of thumb: shape the pipeline on `hashing`, then switch to `bge` and
re-calibrate before anyone treats a strategic-fit score as meaningful.
"""

from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Any, Iterable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

_TOKEN_RE = re.compile(r"[a-z0-9]+(?:['\-][a-z0-9]+)*")

# Domain-agnostic stopwords plus the academic-abstract boilerplate that
# otherwise dominates every vector in a corpus of paper abstracts.
_STOPWORD_SOURCE = """
a about above after again against all also am an and any are as at be because been
before being below between both but by can cannot could did do does doing down during
each few for from further had has have having he her here hers herself him himself his
how i if in into is it its itself just me more most my myself no nor not now of off on
once only or other others our ours ourselves out over own same she should so some such
than that the their theirs them themselves then there these they this those through to
too under until up very was we were what when where which while who whom why will with
would you your yours yourself yourselves
paper study research article results method methods approach propose proposed present
presented show shown using used use based new novel however therefore thus abstract
introduction conclusion conclusions discussion review analysis data model models
"""


# Words whose trailing 's' is part of the stem, not a plural marker. Without
# these, "analysis" -> "analysi" and "business" -> "busines", which then fail to
# match their own singular forms and quietly split topics in two.
_S_ENDINGS_KEPT = ("ss", "us", "is", "as", "os")


def singularise(token: str) -> str:
    """Strip a plural suffix with a few suffix rules.

    Deliberately not a stemmer. A Porter stemmer would add a dependency and
    over-stem domain terms ("examination" -> "examin"), while the actual problem
    in this corpus is narrow: IP vocabulary appears in both forms constantly —
    "trade mark"/"trade marks", "patent"/"patents", "design"/"designs",
    "geographical indication"/"indications". Unmatched, those form separate
    topics and score separately against the same strategic objective.
    """
    if len(token) <= 3 or not token.endswith("s"):
        return token
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"          # technologies -> technology
    if token.endswith(("sses", "shes", "ches", "xes", "zes")):
        return token[:-2]                # classes -> class, patches -> patch
    if token.endswith(_S_ENDINGS_KEPT):
        return token                     # analysis, business, status
    return token[:-1]                    # patents -> patent


# Both the listed form and its singular. normalise_tokens() singularises before
# it filters, so a list holding only "results" would let "result" straight
# through — and the singular of a boilerplate plural is boilerplate too.
_STOPWORDS = frozenset(
    _STOPWORD_SOURCE.split()
) | frozenset(singularise(w) for w in _STOPWORD_SOURCE.split())


def normalise_tokens(text: str) -> list[str]:
    """Lowercase, tokenise, singularise, drop stopwords and 1-character tokens.

    Singularisation runs before the stopword check so that a plural stopword is
    still caught.
    """
    if not text:
        return []
    tokens = (singularise(t) for t in _TOKEN_RE.findall(text.lower()))
    return [t for t in tokens if len(t) > 1 and t not in _STOPWORDS]


def content_hash(text: str, backend: str) -> str:
    """Cache key for a piece of text under a given backend."""
    return hashlib.sha256(f"{backend}\x00{text}".encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class Embedder:
    """Common interface. `encode` returns L2-normalised row vectors."""

    name: str = "base"
    dimensions: int = 0

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        raise NotImplementedError

    def fit(self, texts: Sequence[str]) -> "Embedder":
        """Optional corpus-fitting step (IDF). Default: no-op."""
        return self


class HashingEmbedder(Embedder):
    """Hashed uni/bigram TF-IDF, L2-normalised.

    Deterministic across processes and machines: the hash is derived from
    blake2b, not Python's randomised `hash()`. Two runs on the same corpus
    produce identical vectors, which is a hard requirement for a pipeline whose
    outputs get compared week over week.
    """

    name = "hashing"

    def __init__(self, dimensions: int = 2048, use_bigrams: bool = True) -> None:
        self.dimensions = int(dimensions)
        self.use_bigrams = use_bigrams
        self._idf: dict[int, float] | None = None
        self._n_docs = 0

    # -- fitting ---------------------------------------------------------
    def fit(self, texts: Sequence[str]) -> "HashingEmbedder":
        """Fit IDF over the corpus.

        Without IDF, 'intelligence' in an AI-heavy corpus carries as much
        weight as the term that actually distinguishes one topic from another.
        """
        df: dict[int, int] = {}
        n = 0
        for text in texts:
            feats = self._features(text)
            if not feats:
                continue
            n += 1
            for idx in set(feats):
                df[idx] = df.get(idx, 0) + 1
        self._n_docs = n
        # Smoothed IDF, as in scikit-learn: log((1+n)/(1+df)) + 1, always > 0.
        self._idf = {idx: math.log((1 + n) / (1 + d)) + 1.0 for idx, d in df.items()}
        logger.debug("HashingEmbedder fitted on %d documents, %d features", n, len(df))
        return self

    # -- encoding --------------------------------------------------------
    def encode(self, texts: Sequence[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.dimensions), dtype=np.float64)
        for row, text in enumerate(texts):
            counts: dict[int, int] = {}
            for idx in self._features(text):
                counts[idx] = counts.get(idx, 0) + 1
            for idx, tf in counts.items():
                # Sublinear TF: a term appearing 20 times is not 20x as
                # informative as one appearing once.
                weight = 1.0 + math.log(tf)
                if self._idf is not None:
                    # Unseen feature at encode time: treat as maximally rare.
                    weight *= self._idf.get(idx, math.log(1 + self._n_docs) + 1.0)
                out[row, idx] += weight
        return _l2_normalise(out)

    # -- internals -------------------------------------------------------
    def _features(self, text: str) -> list[int]:
        tokens = normalise_tokens(text)
        feats = [self._bucket(t) for t in tokens]
        if self.use_bigrams:
            feats.extend(
                self._bucket(f"{a}_{b}") for a, b in zip(tokens, tokens[1:])
            )
        return feats

    def _bucket(self, feature: str) -> int:
        digest = hashlib.blake2b(feature.encode("utf-8"), digest_size=8).digest()
        return int.from_bytes(digest, "big") % self.dimensions


class BGEEmbedder(Embedder):
    """sentence-transformers BAAI/bge-base-en-v1.5.

    Imported lazily so that `import src.embeddings` never pulls in torch.
    Install with requirements-ml.txt (and torch from the CPU-only index, as
    Tripwire's README documents) before selecting this backend.
    """

    name = "bge"

    def __init__(self, model_name: str = "BAAI/bge-base-en-v1.5", batch_size: int = 32) -> None:
        try:
            from sentence_transformers import SentenceTransformer  # noqa: PLC0415
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError(
                "embeddings.backend='bge' requires sentence-transformers.\n"
                "  pip install torch --index-url https://download.pytorch.org/whl/cpu\n"
                "  pip install -r requirements-ml.txt"
            ) from exc
        self._model = SentenceTransformer(model_name)
        self.model_name = model_name
        self.batch_size = batch_size
        self.dimensions = int(self._model.get_sentence_embedding_dimension())

    def encode(self, texts: Sequence[str]) -> np.ndarray:
        vectors = self._model.encode(
            list(texts),
            batch_size=self.batch_size,
            normalize_embeddings=True,
            show_progress_bar=False,
            convert_to_numpy=True,
        )
        return np.asarray(vectors, dtype=np.float64)


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_embedder(config: dict[str, Any]) -> Embedder:
    """Construct the embedder named by `embeddings.backend` in the config."""
    from src.config import get  # local import avoids a circular import at module load

    backend = str(get(config, "embeddings", "backend", default="hashing"))
    if backend == "hashing":
        return HashingEmbedder(
            dimensions=int(get(config, "embeddings", "hashing_dimensions", default=2048))
        )
    if backend == "bge":
        return BGEEmbedder(
            model_name=str(get(config, "embeddings", "bge_model", default="BAAI/bge-base-en-v1.5"))
        )
    raise ValueError(f"Unknown embeddings.backend: {backend!r}")


# ---------------------------------------------------------------------------
# Vector maths
# ---------------------------------------------------------------------------


def _l2_normalise(matrix: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    # Zero vectors (empty text) stay zero: cosine with them is 0, which is the
    # honest answer, rather than a NaN that propagates into every score.
    norms[norms == 0] = 1.0
    return matrix / norms


def cosine_similarity(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    """Cosine similarity between row sets. Inputs are assumed L2-normalised."""
    a = np.atleast_2d(a)
    b = np.atleast_2d(b)
    return a @ b.T


def centroid(vectors: np.ndarray) -> np.ndarray:
    """L2-normalised mean vector. Zero-length input returns a zero vector."""
    if vectors.size == 0:
        return np.zeros(vectors.shape[1] if vectors.ndim == 2 else 0)
    mean = np.asarray(vectors).mean(axis=0)
    norm = float(np.linalg.norm(mean))
    return mean / norm if norm else mean


def encode_with_cache(
    embedder: Embedder,
    texts: Sequence[str],
    conn: Any | None = None,
    *,
    enabled: bool = True,
) -> np.ndarray:
    """Encode *texts*, reading and writing the DuckDB `vectors` cache.

    The cache is keyed on (content hash, backend). It saves the expensive
    re-encode when a weekly scan re-collects a mostly-unchanged corpus, which
    is the normal case for this pipeline.
    """
    if conn is None or not enabled:
        return embedder.encode(texts)

    from src import db  # local import keeps this module importable without duckdb

    hashes = [content_hash(t, embedder.name) for t in texts]
    cached = db.get_cached_vectors(conn, embedder.name, list(dict.fromkeys(hashes)))

    missing_idx = [i for i, h in enumerate(hashes) if h not in cached]
    if missing_idx:
        fresh = embedder.encode([texts[i] for i in missing_idx])
        new_vectors = {hashes[i]: fresh[row] for row, i in enumerate(missing_idx)}
        db.store_vectors(conn, embedder.name, embedder.dimensions, new_vectors)
        cached.update({h: list(v) for h, v in new_vectors.items()})

    return np.array([cached[h] for h in hashes], dtype=np.float64)
