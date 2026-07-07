"""Tests for the Slack poster.

`build_brief` is pure and tested directly; delivery is tested without a real
Slack workspace (stdout fallback, and a monkeypatched httpx.post).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from agents.commit_analyzer import CommitCorrelation
from agents.impact_estimator import estimate_impact
from agents.runbook_retriever import RunbookRetriever
from agents.slack_poster import build_brief, post_brief
from config import Settings
from embeddings import HashingEmbedder
from metrics import MockMetricsStore
from schemas import AlertPayload, Severity

START = datetime(2026, 7, 3, 14, 0, tzinfo=UTC)


@pytest.fixture
def alert():
    return AlertPayload(
        alert_name="HighErrorRate",
        severity=Severity.critical,
        service="checkout",
        started_at=START,
        summary="5xx rate exceeded 5% for 5 minutes.",
    )


@pytest.fixture
def impact():
    return estimate_impact(
        service="checkout",
        started_at=START,
        resolved_at=START + timedelta(minutes=30),
        store=MockMetricsStore(),
    )


@pytest.fixture
def runbook():
    matches = RunbookRetriever("runbooks", embedder=HashingEmbedder()).retrieve(
        "high error rate 5xx after deploy", top_k=1
    )
    return matches[0]


@pytest.fixture
def commit():
    return CommitCorrelation(
        suspected_commit="abc1234567def",
        confidence="high",
        reasoning="Introduced an N+1 query in the checkout hot path.",
        recommended_action="Roll back the deploy.",
    )


def _block_types(payload):
    return [block["type"] for block in payload["blocks"]]


def test_brief_has_header_and_footer(alert):
    payload = build_brief(alert)
    types = _block_types(payload)
    assert types[0] == "header"
    assert types[-1] == "context"
    assert "HighErrorRate" in payload["blocks"][0]["text"]["text"]
    assert "🔴" in payload["blocks"][0]["text"]["text"]  # critical severity


def test_brief_omits_missing_findings(alert):
    # With no findings, only header + alert section + footer.
    assert _block_types(build_brief(alert)) == ["header", "section", "context"]


def test_brief_includes_all_findings(alert, commit, runbook, impact):
    payload = build_brief(alert, commit=commit, runbook=runbook, impact=impact)
    text = json.dumps(payload)
    assert "Impact" in text
    assert "Suspected change" in text
    assert commit.suspected_commit[:10] in text
    assert runbook.runbook.title in text
    # header + alert + 3 findings (each preceded by a divider) + footer
    assert _block_types(payload).count("divider") == 3


def test_none_suspect_renders_gracefully(alert):
    none_commit = CommitCorrelation(
        suspected_commit="none",
        confidence="none",
        reasoning="No correlating change found.",
        recommended_action="Investigate infrastructure.",
    )
    payload = build_brief(alert, commit=none_commit)
    assert "No commit correlated" in json.dumps(payload)


def test_post_brief_without_webhook_prints_and_returns_false(alert, capsys):
    settings = Settings(slack_webhook_url=None)
    sent = post_brief(build_brief(alert), settings=settings)
    assert sent is False
    printed = capsys.readouterr().out
    assert json.loads(printed)["blocks"]  # valid Block Kit JSON on stdout


def test_post_brief_with_webhook_posts_and_returns_true(alert, monkeypatch):
    calls = {}

    class _Resp:
        def raise_for_status(self):
            calls["raised"] = True

    def fake_post(url, json, timeout):  # noqa: A002 - mirror httpx.post signature
        calls["url"] = url
        calls["payload"] = json
        return _Resp()

    monkeypatch.setattr("agents.slack_poster.httpx.post", fake_post)

    settings = Settings(slack_webhook_url="https://hooks.slack.example/T/B/X")
    sent = post_brief(build_brief(alert), settings=settings)

    assert sent is True
    assert calls["url"] == "https://hooks.slack.example/T/B/X"
    assert calls["payload"]["blocks"]
