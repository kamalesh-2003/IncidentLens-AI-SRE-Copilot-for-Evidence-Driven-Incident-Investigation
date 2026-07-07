"""Demo of the runbook retriever stage.

Runs a few sample alerts against the shipped runbooks and prints the ranked
matches. Works with no API key — it uses whichever embedder is available
(Voyage → local model → lexical hashing fallback).

    python -m demo.run_runbook_retriever
"""
from __future__ import annotations

from agents.runbook_retriever import RunbookRetriever
from embeddings import get_embedder

SAMPLE_QUERIES = [
    "HighErrorRate on checkout: 5xx rate spiked right after the last deploy",
    "Checkout p99 latency jumped from 120ms to 2.3s, requests timing out",
    "payments pods OOMKilled repeatedly, memory usage climbing",
    "cannot acquire database connection, pool exhausted under load",
]


def main() -> int:
    embedder = get_embedder()
    retriever = RunbookRetriever(embedder=embedder)
    print(f"Loaded {len(retriever.runbooks)} runbooks; embedder: {embedder.name}\n")

    for query in SAMPLE_QUERIES:
        print(f"Alert: {query}")
        for rank, match in enumerate(retriever.retrieve(query, top_k=2), start=1):
            print(f"  {rank}. {match.runbook.title}  (score={match.score})")
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
