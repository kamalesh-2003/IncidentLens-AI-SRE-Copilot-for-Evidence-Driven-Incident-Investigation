"""Tests for the runbook retriever.

Run against the deterministic HashingEmbedder so they need neither an API key,
a network connection, nor a downloaded model.
"""
from __future__ import annotations

import pytest

from agents.runbook_retriever import (
    RunbookRetriever,
    _parse_frontmatter,
    load_runbooks,
    retrieve_for_alert,
)
from embeddings import HashingEmbedder

RUNBOOKS_DIR = "runbooks"


@pytest.fixture
def retriever():
    return RunbookRetriever(RUNBOOKS_DIR, embedder=HashingEmbedder())


def test_load_runbooks_parses_frontmatter():
    runbooks = load_runbooks(RUNBOOKS_DIR)
    assert len(runbooks) >= 4
    by_title = {rb.title: rb for rb in runbooks}
    assert "High HTTP Error Rate" in by_title
    err = by_title["High HTTP Error Rate"]
    assert err.severity == "critical"
    assert "5xx" in (err.symptoms or "")
    assert err.body.startswith("## High HTTP Error Rate")


def test_parse_frontmatter_without_fence():
    meta, body = _parse_frontmatter("# Just a heading\n\nno frontmatter here")
    assert meta == {}
    assert body.startswith("# Just a heading")


@pytest.mark.parametrize(
    "query, expected_keyword",
    [
        ("high error rate 5xx errors spiking after deploy", "error"),
        ("p99 latency slow responses and request timeouts", "latency"),
        ("pods OOMKilled memory leak rising heap usage", "memory"),
        ("connection pool exhausted too many database connections", "connection"),
        ("CPU saturation throttling hot loop compute bound", "cpu"),
    ],
)
def test_retrieve_ranks_relevant_runbook_first(retriever, query, expected_keyword):
    matches = retriever.retrieve(query, top_k=1)
    assert matches
    assert expected_keyword in matches[0].runbook.title.lower()


def test_retrieve_respects_top_k(retriever):
    matches = retriever.retrieve("high error rate after deploy", top_k=2)
    assert len(matches) == 2
    # Scores are sorted descending.
    assert matches[0].score >= matches[1].score


def test_retrieve_for_alert_camelcase_name():
    # Alert names are camelCase; the retriever must still route them correctly.
    matches = retrieve_for_alert(
        alert_name="HighErrorRate",
        service="checkout",
        summary="5xx rate exceeded threshold",
        directory=RUNBOOKS_DIR,
        top_k=1,
    )
    assert matches
    assert "error" in matches[0].runbook.title.lower()


def test_empty_directory_returns_no_matches(tmp_path):
    retriever = RunbookRetriever(str(tmp_path), embedder=HashingEmbedder())
    assert retriever.retrieve("anything") == []


def test_missing_directory_returns_no_matches():
    retriever = RunbookRetriever("does/not/exist", embedder=HashingEmbedder())
    assert retriever.retrieve("anything") == []
