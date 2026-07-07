"""Slack poster — the presentation stage.

Assembles the investigation findings (alert, impact estimate, suspected commit,
matched runbook) into a Slack Block Kit brief and delivers it. When
``SLACK_WEBHOOK_URL`` is unset, the Block Kit JSON is printed to stdout instead,
so the stage is fully exercisable without a Slack workspace.

Entry points: :func:`build_brief` (pure) and :func:`post_brief` / :func:`post_incident_brief`.
"""
from __future__ import annotations

import json
import logging

import httpx

from agents.commit_analyzer import CommitCorrelation
from agents.impact_estimator import ImpactEstimate
from agents.runbook_retriever import RunbookMatch
from config import Settings, get_settings
from schemas import AlertPayload

log = logging.getLogger("incidentlens.slack_poster")

_SEVERITY_EMOJI = {"critical": "🔴", "high": "🟠", "medium": "🟡", "low": "🔵"}
_DIVIDER = {"type": "divider"}


def _header(text: str) -> dict:
    # Slack caps header plain_text at 150 characters.
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def _section(markdown: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": markdown}}


def _context(markdown: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": markdown}]}


def _alert_section(alert: AlertPayload) -> dict:
    lines = [
        f"*Severity:* {alert.severity.value}",
        f"*Service:* {alert.service}",
        f"*Started:* {alert.started_at.isoformat()}",
    ]
    if alert.summary:
        lines.append(f"*Summary:* {alert.summary}")
    return _section("\n".join(lines))


def _impact_section(impact: ImpactEstimate) -> dict:
    return _section(
        "*📊 Impact*\n"
        f"~{impact.total_requests:,} requests, {impact.failed_requests:,} failed "
        f"({impact.error_rate_pct}% vs {impact.baseline_error_rate_pct}% baseline)\n"
        f"Peak p99 latency {impact.peak_latency_p99_ms} ms • "
        f"~{impact.estimated_affected_users:,} users affected"
    )


def _commit_section(commit: CommitCorrelation) -> dict:
    if commit.suspected_commit == "none":
        return _section(
            f"*🔍 Suspected change*\nNo commit correlated (confidence: {commit.confidence})."
        )
    return _section(
        "*🔍 Suspected change*\n"
        f"`{commit.suspected_commit[:10]}` — confidence: {commit.confidence}\n"
        f"{commit.reasoning}\n"
        f"*Recommended:* {commit.recommended_action}"
    )


def _runbook_section(match: RunbookMatch) -> dict:
    return _section(
        f"*📖 Runbook*\n{match.runbook.title}\n"
        f"`{match.runbook.path}` (match score {match.score})"
    )


def build_brief(
    alert: AlertPayload,
    *,
    commit: CommitCorrelation | None = None,
    runbook: RunbookMatch | None = None,
    impact: ImpactEstimate | None = None,
) -> dict:
    """Build the Slack Block Kit payload for an incident brief.

    Findings that weren't produced (``None``) are simply omitted.
    """
    emoji = _SEVERITY_EMOJI.get(alert.severity.value, "⚪")
    blocks: list[dict] = [
        _header(f"{emoji} {alert.alert_name} — {alert.service}"),
        _alert_section(alert),
    ]
    if impact is not None:
        blocks += [_DIVIDER, _impact_section(impact)]
    if commit is not None:
        blocks += [_DIVIDER, _commit_section(commit)]
    if runbook is not None:
        blocks += [_DIVIDER, _runbook_section(runbook)]
    blocks.append(_context("IncidentLens • read-only diagnosis — no changes were made"))
    return {"blocks": blocks}


def post_brief(payload: dict, settings: Settings | None = None) -> bool:
    """Deliver a Block Kit payload.

    Posts to ``SLACK_WEBHOOK_URL`` when configured and returns ``True``; otherwise
    prints the JSON to stdout and returns ``False``.
    """
    settings = settings or get_settings()
    if not settings.slack_webhook_url:
        # ensure_ascii escapes emoji to \uXXXX — valid JSON that prints safely on
        # any console codepage; Slack still renders it correctly when posted.
        print(json.dumps(payload, indent=2))
        return False

    resp = httpx.post(settings.slack_webhook_url, json=payload, timeout=10.0)
    resp.raise_for_status()
    return True


def post_incident_brief(
    alert: AlertPayload,
    *,
    commit: CommitCorrelation | None = None,
    runbook: RunbookMatch | None = None,
    impact: ImpactEstimate | None = None,
    settings: Settings | None = None,
) -> bool:
    """Build and deliver an incident brief in one call."""
    return post_brief(
        build_brief(alert, commit=commit, runbook=runbook, impact=impact), settings
    )
