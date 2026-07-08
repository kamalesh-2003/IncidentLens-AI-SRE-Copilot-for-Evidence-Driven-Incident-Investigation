"""LangGraph orchestrator — the incident investigation workflow.

Ties the five stages into a single graph that threads one shared
:class:`IncidentState` through them, so the diagnostic findings are computed once
and passed along rather than each terminal stage re-deriving them.

    START → commit_analyzer → runbook_retriever → impact_estimator
          → (route on alert status)
              ├─ firing   → slack_poster    → END
              └─ resolved → postmortem_gen  → END

Every node is individually guarded: a stage that is unavailable (e.g. no
``ANTHROPIC_API_KEY``) or fails records ``None`` and the workflow continues, so a
single stage can never abort the incident response.

Entry point: :func:`run_incident`.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from typing import TYPE_CHECKING, TypedDict

from langgraph.graph import END, START, StateGraph

# Result types are imported at runtime (cheap — none pull the Anthropic SDK or the
# optional embedding backends at import time) so the TypedDict schema resolves when
# LangGraph introspects it. The agent *entry points* are imported lazily in nodes.
from agents.commit_analyzer import CommitCorrelation
from agents.impact_estimator import ImpactEstimate
from agents.runbook_retriever import RunbookMatch
from config import Settings, get_settings
from schemas import AlertPayload, AlertStatus

if TYPE_CHECKING:
    from langgraph.graph.state import CompiledStateGraph

log = logging.getLogger("incidentlens.orchestrator")


class IncidentState(TypedDict, total=False):
    """State threaded through the graph. Nodes write disjoint keys."""

    # Inputs, set before invocation.
    alert: AlertPayload
    settings: Settings
    # Diagnostic findings (``None`` when a stage was skipped or failed).
    commit: CommitCorrelation | None
    runbook: RunbookMatch | None
    impact: ImpactEstimate | None
    # Terminal outcomes.
    brief_posted: bool
    postmortem_path: str | None


# --- Nodes ------------------------------------------------------------------


def _commit_node(state: IncidentState) -> dict:
    """Correlate a recent commit with the alert (skipped without an API key)."""
    alert, settings = state["alert"], state["settings"]
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY unset; skipping commit analysis")
        return {"commit": None}
    try:
        from agents.commit_analyzer import analyze_commits

        verdict = analyze_commits(
            repo_path=settings.demo_git_repo_path,
            alert_name=alert.alert_name,
            service=alert.service,
            severity=alert.severity.value,
            started_at=alert.started_at,
            summary=alert.summary,
        )
        log.info(
            "commit analysis for %s: suspect=%s confidence=%s",
            alert.alert_name,
            verdict.suspected_commit,
            verdict.confidence,
        )
        return {"commit": verdict}
    except Exception:  # never let a diagnostic stage abort the workflow
        log.exception("commit analysis failed for %s", alert.alert_name)
        return {"commit": None}


def _runbook_node(state: IncidentState) -> dict:
    """Retrieve the most relevant runbook (keyless — always runs)."""
    alert, settings = state["alert"], state["settings"]
    try:
        from agents.runbook_retriever import retrieve_for_alert

        matches = retrieve_for_alert(
            alert_name=alert.alert_name,
            service=alert.service,
            summary=alert.summary,
            directory=settings.runbooks_dir,
            top_k=3,
        )
        if matches:
            top = matches[0]
            log.info(
                "runbook match for %s: '%s' (score=%.3f)",
                alert.alert_name,
                top.runbook.title,
                top.score,
            )
            return {"runbook": top}
        log.info("no runbook matched %s", alert.alert_name)
        return {"runbook": None}
    except Exception:  # never let a diagnostic stage abort the workflow
        log.exception("runbook retrieval failed for %s", alert.alert_name)
        return {"runbook": None}


def _impact_node(state: IncidentState) -> dict:
    """Estimate the incident's user/traffic impact (keyless — always runs)."""
    alert = state["alert"]
    try:
        from agents.impact_estimator import estimate_impact

        estimate = estimate_impact(
            service=alert.service,
            started_at=alert.started_at,
            resolved_at=alert.resolved_at,
        )
        log.info(
            "impact for %s: ~%d requests, %d failed (%.1f%%), ~%d users affected [source=%s]",
            alert.alert_name,
            estimate.total_requests,
            estimate.failed_requests,
            estimate.error_rate_pct,
            estimate.estimated_affected_users,
            estimate.source,
        )
        return {"impact": estimate}
    except Exception:  # never let a diagnostic stage abort the workflow
        log.exception("impact estimation failed for %s", alert.alert_name)
        return {"impact": None}


def _slack_node(state: IncidentState) -> dict:
    """Post the consolidated incident brief (firing path)."""
    alert, settings = state["alert"], state["settings"]
    try:
        from agents.slack_poster import post_incident_brief

        posted = post_incident_brief(
            alert,
            commit=state.get("commit"),
            runbook=state.get("runbook"),
            impact=state.get("impact"),
            settings=settings,
        )
        log.info(
            "incident brief %s for %s",
            "posted to Slack" if posted else "printed to stdout",
            alert.alert_name,
        )
        return {"brief_posted": posted}
    except Exception:  # never let the presentation stage abort the workflow
        log.exception("slack posting failed for %s", alert.alert_name)
        return {"brief_posted": False}


def _postmortem_node(state: IncidentState) -> dict:
    """Generate and write the postmortem (resolve path)."""
    alert, settings = state["alert"], state["settings"]
    try:
        from agents.postmortem_gen import generate_postmortem

        _, path = generate_postmortem(
            alert,
            commit=state.get("commit"),
            runbook=state.get("runbook"),
            impact=state.get("impact"),
            settings=settings,
        )
        log.info("postmortem for %s written to %s", alert.alert_name, path)
        return {"postmortem_path": str(path) if path else None}
    except Exception:  # never let the postmortem stage abort the workflow
        log.exception("postmortem generation failed for %s", alert.alert_name)
        return {"postmortem_path": None}


def _route_on_status(state: IncidentState) -> str:
    """Choose the terminal stage: postmortem for resolved alerts, else Slack."""
    if state["alert"].status is AlertStatus.resolved:
        return "postmortem_gen"
    return "slack_poster"


# --- Graph ------------------------------------------------------------------


@lru_cache(maxsize=1)
def get_graph() -> CompiledStateGraph:
    """Build and compile the incident workflow (cached for the process)."""
    builder = StateGraph(IncidentState)
    builder.add_node("commit_analyzer", _commit_node)
    builder.add_node("runbook_retriever", _runbook_node)
    builder.add_node("impact_estimator", _impact_node)
    builder.add_node("slack_poster", _slack_node)
    builder.add_node("postmortem_gen", _postmortem_node)

    builder.add_edge(START, "commit_analyzer")
    builder.add_edge("commit_analyzer", "runbook_retriever")
    builder.add_edge("runbook_retriever", "impact_estimator")
    builder.add_conditional_edges(
        "impact_estimator",
        _route_on_status,
        {"slack_poster": "slack_poster", "postmortem_gen": "postmortem_gen"},
    )
    builder.add_edge("slack_poster", END)
    builder.add_edge("postmortem_gen", END)
    return builder.compile()


def run_incident(payload: AlertPayload, settings: Settings | None = None) -> IncidentState:
    """Run the full incident workflow for ``payload`` and return the final state.

    Diagnostic findings are computed once and shared; the terminal stage is chosen
    by alert status (firing → Slack brief, resolved → postmortem). Node-level
    guards mean this returns a populated state even when individual stages fail.
    """
    settings = settings or get_settings()
    log.info(
        "orchestrating incident for %s (status=%s)",
        payload.alert_name,
        payload.status.value,
    )
    result: IncidentState = get_graph().invoke({"alert": payload, "settings": settings})
    return result
