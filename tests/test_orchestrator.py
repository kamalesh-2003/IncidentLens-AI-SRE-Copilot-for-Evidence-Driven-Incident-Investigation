"""Tests for the LangGraph orchestrator.

Runs the graph end-to-end with keyless stages (no ANTHROPIC_API_KEY, mock metrics,
hashing embeddings) so it exercises real node wiring without external calls.
"""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from config import Settings
from orchestrator import _route_on_status, get_graph, run_incident
from schemas import AlertPayload, AlertStatus, Severity

START = datetime(2026, 7, 3, 14, 0, tzinfo=UTC)


@pytest.fixture
def firing():
    return AlertPayload(
        alert_name="HighErrorRate",
        severity=Severity.critical,
        service="checkout",
        started_at=START,
        summary="5xx rate exceeded 5% for 5 minutes.",
    )


@pytest.fixture
def resolved():
    return AlertPayload(
        alert_name="HighErrorRate",
        severity=Severity.high,
        service="checkout",
        status=AlertStatus.resolved,
        started_at=START,
        resolved_at=START + timedelta(minutes=30),
    )


def test_graph_has_all_stage_nodes():
    nodes = set(get_graph().get_graph().nodes)
    for name in (
        "commit_analyzer",
        "runbook_retriever",
        "impact_estimator",
        "slack_poster",
        "postmortem_gen",
    ):
        assert name in nodes


def test_route_on_status(firing, resolved):
    assert _route_on_status({"alert": firing}) == "slack_poster"
    assert _route_on_status({"alert": resolved}) == "postmortem_gen"


def test_firing_runs_diagnostics_and_slack(firing, capsys):
    settings = Settings(anthropic_api_key=None, slack_webhook_url=None)
    state = run_incident(firing, settings)

    # All three diagnostic keys are present; commit skipped (no key), others found.
    assert state["commit"] is None
    assert state["runbook"] is not None
    assert state["impact"] is not None
    # Routed to Slack (stdout fallback), not postmortem.
    assert state["brief_posted"] is False
    assert "postmortem_path" not in state
    # A valid Block Kit brief was printed.
    assert json.loads(capsys.readouterr().out)["blocks"]


def test_resolved_runs_diagnostics_and_postmortem(resolved, tmp_path):
    settings = Settings(anthropic_api_key=None, postmortems_dir=str(tmp_path))
    state = run_incident(resolved, settings)

    assert state["impact"] is not None
    assert state["runbook"] is not None
    # Routed to postmortem, not Slack.
    assert "brief_posted" not in state
    path = state["postmortem_path"]
    assert path is not None and path.endswith(".md")
    assert (tmp_path / "2026-07-03-checkout-higherrorrate.md").read_text(encoding="utf-8")


def test_findings_computed_once_and_shared(monkeypatch, firing):
    # The Slack node should receive the same impact object the impact node produced
    # (i.e. findings are threaded through state, not recomputed per terminal stage).
    seen = {}

    def spy_post_incident_brief(alert, *, commit, runbook, impact, settings=None):
        seen["impact"] = impact
        seen["runbook"] = runbook
        return False

    monkeypatch.setattr(
        "agents.slack_poster.post_incident_brief", spy_post_incident_brief
    )
    settings = Settings(anthropic_api_key=None, slack_webhook_url=None)
    state = run_incident(firing, settings)

    assert seen["impact"] is state["impact"]
    assert seen["runbook"] is state["runbook"]
