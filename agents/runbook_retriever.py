"""Runbook retriever — the second orchestrator stage.

Given a firing alert, this stage finds the most relevant operational runbook(s)
by semantic similarity over the Markdown files in ``runbooks/``. Each runbook
carries lightweight YAML-style frontmatter (title, service, severity, symptoms)
that is folded into its embedding alongside the body.

Embeddings come from :func:`embeddings.get_embedder` (Voyage → local model →
hashing fallback), so this stage runs without an API key.

Entry points: :class:`RunbookRetriever` and :func:`retrieve_for_alert`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from config import get_settings
from embeddings import Embedder, get_embedder

log = logging.getLogger("incidentlens.runbook_retriever")

_FRONTMATTER_FENCE = "---"


@dataclass(frozen=True)
class Runbook:
    """A single operational runbook loaded from disk."""

    path: str
    title: str
    body: str
    service: str | None = None
    severity: str | None = None
    symptoms: str | None = None

    @property
    def embed_text(self) -> str:
        """The text embedded for similarity search (metadata + body)."""
        parts = [self.title, self.service, self.severity, self.symptoms, self.body]
        return "\n".join(p for p in parts if p)


@dataclass(frozen=True)
class RunbookMatch:
    """A retrieved runbook with its similarity score (higher is more relevant)."""

    runbook: Runbook
    score: float


def _parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split leading ``---`` frontmatter from the body.

    Supports simple ``key: value`` lines — sufficient for the seed runbooks and
    dependency-free (no PyYAML). Returns ``({}, text)`` when no frontmatter fence
    is present.
    """
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_FENCE:
        return {}, text

    meta: dict[str, str] = {}
    for i in range(1, len(lines)):
        if lines[i].strip() == _FRONTMATTER_FENCE:
            body = "\n".join(lines[i + 1 :]).lstrip("\n")
            return meta, body
        key, sep, value = lines[i].partition(":")
        if sep:
            meta[key.strip().lower()] = value.strip()

    # Unterminated frontmatter — treat the whole file as body.
    return {}, text


def load_runbooks(directory: str) -> list[Runbook]:
    """Load and parse every ``*.md`` runbook in ``directory`` (sorted by name).

    Missing directories yield an empty list so the retriever degrades to "no
    match" rather than raising.
    """
    base = Path(directory)
    if not base.is_dir():
        log.warning("runbooks directory not found: %s", base)
        return []

    runbooks: list[Runbook] = []
    for path in sorted(base.glob("*.md")):
        meta, body = _parse_frontmatter(path.read_text(encoding="utf-8"))
        runbooks.append(
            Runbook(
                path=str(path),
                title=meta.get("title", path.stem.replace("-", " ").title()),
                body=body,
                service=meta.get("service"),
                severity=meta.get("severity"),
                symptoms=meta.get("symptoms"),
            )
        )
    return runbooks


class RunbookRetriever:
    """Embeds a corpus of runbooks once, then answers similarity queries."""

    def __init__(self, directory: str | None = None, embedder: Embedder | None = None) -> None:
        self.directory = directory or get_settings().runbooks_dir
        self.embedder = embedder or get_embedder()
        self.runbooks = load_runbooks(self.directory)
        # (n_runbooks, dim) matrix of unit-norm document embeddings, or None if empty.
        self._index: np.ndarray | None = (
            self.embedder.embed([rb.embed_text for rb in self.runbooks])
            if self.runbooks
            else None
        )

    def retrieve(self, query: str, top_k: int = 3) -> list[RunbookMatch]:
        """Return the ``top_k`` most similar runbooks, highest score first."""
        if self._index is None:
            return []

        query_vec = self.embedder.embed([query], input_type="query")[0]
        # Rows are L2-normalized, so the dot product is cosine similarity.
        scores = self._index @ query_vec
        ranked = np.argsort(-scores)[: max(top_k, 0)]
        return [
            RunbookMatch(runbook=self.runbooks[i], score=round(float(scores[i]), 4))
            for i in ranked
        ]


def retrieve_for_alert(
    *,
    alert_name: str,
    service: str,
    summary: str | None = None,
    directory: str | None = None,
    top_k: int = 3,
) -> list[RunbookMatch]:
    """Convenience wrapper: build a query from alert fields and retrieve."""
    query = " ".join(part for part in (alert_name, service, summary) if part)
    return RunbookRetriever(directory).retrieve(query, top_k=top_k)
