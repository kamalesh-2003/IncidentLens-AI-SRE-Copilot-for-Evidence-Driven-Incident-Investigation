"""Text embedding backends for runbook retrieval.

Exposes a small :class:`Embedder` abstraction with three tiers, selected
automatically by :func:`get_embedder`:

1. **Voyage AI** (``voyageai``) when ``VOYAGE_API_KEY`` is set — Anthropic's
   recommended embedding partner and the highest-quality option.
2. **Local** ``sentence-transformers`` (``all-MiniLM-L6-v2``) when the package is
   installed — no API key or network required after the first model download.
3. **Hashing** — a dependency-free, deterministic NumPy fallback so the retriever
   always works and is unit-testable in CI without extra installs.

Only NumPy is a hard dependency; tiers 1 and 2 are imported lazily.
"""
from __future__ import annotations

import hashlib
import logging
import re
from abc import ABC, abstractmethod
from collections.abc import Sequence
from functools import lru_cache

import numpy as np

from config import get_settings

log = logging.getLogger("incidentlens.embeddings")

DEFAULT_VOYAGE_MODEL = "voyage-3.5"
DEFAULT_LOCAL_MODEL = "all-MiniLM-L6-v2"

# Insert a break at camelCase/word-digit boundaries so alert names like
# "HighErrorRate" tokenize to ["high", "error", "rate"].
_CAMEL_BOUNDARY = re.compile(r"(?<=[a-z0-9])(?=[A-Z])")
_WORD = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> list[str]:
    return _WORD.findall(_CAMEL_BOUNDARY.sub(" ", text).lower())


def _l2_normalize(matrix: np.ndarray) -> np.ndarray:
    """L2-normalize each row so that cosine similarity reduces to a dot product."""
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (matrix / norms).astype(np.float32)


class Embedder(ABC):
    """Turns text into L2-normalized embedding vectors."""

    name: str

    @abstractmethod
    def embed(self, texts: Sequence[str], input_type: str = "document") -> np.ndarray:
        """Return an ``(len(texts), dim)`` float32 matrix of unit-norm rows.

        ``input_type`` (``"document"`` or ``"query"``) is honored by backends that
        distinguish the two (Voyage); others ignore it.
        """


class HashingEmbedder(Embedder):
    """Deterministic signed feature-hashing embedder (pure NumPy).

    Captures lexical overlap only — good enough for a fallback and fully
    reproducible across processes (unlike Python's salted ``hash``).
    """

    name = "hashing"

    def __init__(self, dim: int = 1024) -> None:
        self.dim = dim

    @staticmethod
    def _hash(token: str) -> int:
        return int.from_bytes(
            hashlib.blake2b(token.encode("utf-8"), digest_size=8).digest(), "big"
        )

    def embed(self, texts: Sequence[str], input_type: str = "document") -> np.ndarray:
        vecs = np.zeros((len(texts), self.dim), dtype=np.float32)
        for i, text in enumerate(texts):
            for token in _tokenize(text):
                h = self._hash(token)
                sign = 1.0 if (h >> 63) & 1 else -1.0
                vecs[i, h % self.dim] += sign
        return _l2_normalize(vecs)


class LocalEmbedder(Embedder):
    """`sentence-transformers` backend (lazy-loaded)."""

    name = "sentence-transformers"

    def __init__(self, model_name: str = DEFAULT_LOCAL_MODEL) -> None:
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(model_name)

    def embed(self, texts: Sequence[str], input_type: str = "document") -> np.ndarray:
        vectors = self._model.encode(
            list(texts), normalize_embeddings=True, convert_to_numpy=True
        )
        return vectors.astype(np.float32)


class VoyageEmbedder(Embedder):
    """Voyage AI backend (lazy-loaded)."""

    name = "voyage"

    def __init__(self, api_key: str, model_name: str = DEFAULT_VOYAGE_MODEL) -> None:
        import voyageai

        self._client = voyageai.Client(api_key=api_key)
        self._model = model_name

    def embed(self, texts: Sequence[str], input_type: str = "document") -> np.ndarray:
        result = self._client.embed(list(texts), model=self._model, input_type=input_type)
        return _l2_normalize(np.asarray(result.embeddings, dtype=np.float32))


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Return the best available embedder, degrading gracefully.

    Voyage (if a key is set and the package is importable) → local
    sentence-transformers (if installed) → the hashing fallback.
    """
    settings = get_settings()

    if settings.voyage_api_key:
        try:
            embedder = VoyageEmbedder(settings.voyage_api_key)
            log.info("using Voyage AI embeddings (%s)", DEFAULT_VOYAGE_MODEL)
            return embedder
        except ImportError:
            log.warning("VOYAGE_API_KEY set but `voyageai` not installed; trying local model")

    try:
        embedder = LocalEmbedder()
        log.info("using local sentence-transformers embeddings (%s)", DEFAULT_LOCAL_MODEL)
        return embedder
    except ImportError:
        log.warning(
            "sentence-transformers not installed; falling back to lexical hashing "
            "embeddings (install sentence-transformers or set VOYAGE_API_KEY for "
            "semantic retrieval)"
        )
        return HashingEmbedder()
