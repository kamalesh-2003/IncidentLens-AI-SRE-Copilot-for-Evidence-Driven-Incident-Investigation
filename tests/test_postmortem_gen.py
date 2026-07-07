"""Tests for the postmortem generator.

`build_postmortem` is pure and deterministic (keyless); the AI narrative path is
exercised with a monkeypatched Anthropic client, and file writing against a
tmp_path.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from agents.commit_analyzer import CommitCorrelation
from agents.impact_estimator import estimate_impact
from agents.postmortem_gen import (
    Narrative,
    build_postmortem,
    generate_postmortem,
    write_postmortem,
)
from agents.runbook_retriever import RunbookRetriever
from config import Settings
from embeddings import HashingEmbedder
from metrics import MockMetricsStore
from schemas import AlertPayload, Severity

START = datetime(2026, 7, 3, 14, 0, tzinfo=UTC)
END = START + timedelta(minutes=83)


@pytest.fixture
def alert():
    return AlertPayload(
        alert_name="HighErrorRate",
        severity=Severity.critical,
        service="checkout",
        status="resolved",
        started_at=START,
        resolved_at=END,
        summary="5xx rate exceeded 5% for 5 minutes.",
    )


@pytest.fixture
def impact():
    return estimate_impact(
        service="checkout", started_at=START, resolved_at=END, store=MockMetricsStore()
    )


@pytest.fixture
def runbook():
    return RunbookRetriever("runbooks", embedder=HashingEmbedder()).retrieve(
        "high error rate 5xx after deploy", top_k=1
    )[0]


@pytest.fixture
def commit():
    return CommitCorrelation(
        suspected_commit="abc1234567def",
        confidence="high",
        reasoning="Introduced an N+1 query in the checkout hot path.",
        recommended_action="Roll back the deploy.",
    )


def test_build_has_core_sections(alert, commit, runbook, impact):
    doc = build_postmortem(alert, commit=commit, runbook=runbook, impact=impact)
    md = doc.markdown
    for heading in ("# Postmortem", "## Summary", "## Timeline", "## Impact",
                    "## Root Cause", "## Detection & Response", "## Action Items"):
        assert heading in md
    assert "checkout" in md
    assert "abc1234567" in md  # short SHA
    assert runbook.runbook.title in md


def test_duration_is_human_readable(alert):
    doc = build_postmortem(alert)
    assert "1h 23m" in doc.markdown  # 83 minutes
    assert doc.duration_seconds == pytest.approx(83 * 60)


def test_filename_is_slugified_and_dated(alert):
    doc = build_postmortem(alert)
    assert doc.filename == "2026-07-03-checkout-higherrorrate.md"


def test_missing_findings_render_gracefully(alert):
    doc = build_postmortem(alert)
    md = doc.markdown
    assert "No impact metrics" in md
    assert "Commit correlation was not run" in md
    assert "No runbook matched" in md


def test_none_suspect_renders_inconclusive(alert):
    none_commit = CommitCorrelation(
        suspected_commit="none",
        confidence="none",
        reasoning="Nothing correlated.",
        recommended_action="Investigate infra.",
    )
    md = build_postmortem(alert, commit=none_commit).markdown
    assert "did not identify a likely culprit" in md


def test_ongoing_incident_marked(impact):
    firing = AlertPayload(
        alert_name="HighLatency",
        severity=Severity.high,
        service="api",
        started_at=START,
    )
    md = build_postmortem(firing).markdown
    assert "ongoing" in md.lower()


def test_narrative_override_used(alert):
    narrative = Narrative(summary="Custom summary text.", action_items=["Do the thing."])
    md = build_postmortem(alert, narrative=narrative).markdown
    assert "Custom summary text." in md
    assert "- [ ] Do the thing." in md


def test_write_creates_file(alert, tmp_path):
    doc = build_postmortem(alert)
    path = write_postmortem(doc, tmp_path)
    assert path.exists()
    assert path.name == doc.filename
    assert path.read_text(encoding="utf-8") == doc.markdown


def test_generate_without_key_uses_template(alert, tmp_path, commit):
    settings = Settings(anthropic_api_key=None, postmortems_dir=str(tmp_path))
    doc, path = generate_postmortem(alert, commit=commit, settings=settings)
    assert doc.ai_drafted is False
    assert path is not None and path.exists()
    # Default template summary mentions the suspected commit.
    assert "abc1234567" in doc.markdown


def test_generate_with_ai_narrative(alert, tmp_path, monkeypatch):
    # Fake a tool-use response from Claude.
    class _Block:
        type = "tool_use"
        name = "report_narrative"
        input = {"summary": "AI drafted summary.", "action_items": ["AI action one."]}

    class _Resp:
        content = [_Block()]

    class _Messages:
        def create(self, **kwargs):
            return _Resp()

    class _Client:
        messages = _Messages()

    monkeypatch.setattr("config.get_anthropic_client", lambda: _Client())

    settings = Settings(anthropic_api_key="sk-test", postmortems_dir=str(tmp_path))
    doc, path = generate_postmortem(alert, settings=settings)

    assert doc.ai_drafted is True
    assert "AI drafted summary." in doc.markdown
    assert "- [ ] AI action one." in doc.markdown


def test_generate_ai_failure_falls_back(alert, tmp_path, monkeypatch):
    def _boom():
        raise RuntimeError("API down")

    monkeypatch.setattr("config.get_anthropic_client", _boom)

    settings = Settings(anthropic_api_key="sk-test", postmortems_dir=str(tmp_path))
    doc, _ = generate_postmortem(alert, settings=settings)
    # Falls back to the deterministic template rather than raising.
    assert doc.ai_drafted is False
    assert "## Summary" in doc.markdown
