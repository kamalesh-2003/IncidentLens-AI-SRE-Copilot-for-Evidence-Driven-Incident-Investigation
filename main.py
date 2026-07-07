"""IncidentLens webhook receiver.

Accepts production alert payloads, validates them, and hands firing alerts to the
investigation pipeline (commit correlation, runbook retrieval, impact estimation,
Slack posting, and postmortem generation).

Run locally with::

    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, status

from config import Settings, configure_logging, get_settings
from schemas import AlertAck, AlertPayload, AlertStatus, HealthResponse

log = logging.getLogger("incidentlens.api")

router = APIRouter()


def _run_commit_analysis(payload: AlertPayload, settings: Settings) -> None:
    """Correlate a recent commit with a firing alert (background task).

    Imported lazily and guarded so the receiver has no hard dependency on the
    Anthropic SDK or an API key — it still accepts alerts without them.
    """
    if not settings.anthropic_api_key:
        log.info("ANTHROPIC_API_KEY unset; skipping commit analysis")
        return

    try:
        from agents.commit_analyzer import analyze_commits

        verdict = analyze_commits(
            repo_path=settings.demo_git_repo_path,
            alert_name=payload.alert_name,
            service=payload.service,
            severity=payload.severity.value,
            started_at=payload.started_at,
            summary=payload.summary,
        )
        log.info(
            "commit analysis for %s: suspect=%s confidence=%s",
            payload.alert_name,
            verdict.suspected_commit,
            verdict.confidence,
        )
    except Exception:  # never let a diagnostic stage crash the receiver
        log.exception("commit analysis failed for %s", payload.alert_name)


@router.get("/health", response_model=HealthResponse, tags=["ops"])
def health() -> HealthResponse:
    """Liveness probe."""
    return HealthResponse()


@router.post(
    "/webhook/alert",
    response_model=AlertAck,
    status_code=status.HTTP_202_ACCEPTED,
    tags=["alerts"],
)
def receive_alert(
    payload: AlertPayload,
    background: BackgroundTasks,
    settings: Annotated[Settings, Depends(get_settings)],
) -> AlertAck:
    """Accept an alert and, if firing, kick off investigation without blocking."""
    log.info(
        "alert received: name=%s severity=%s service=%s status=%s started_at=%s",
        payload.alert_name,
        payload.severity.value,
        payload.service,
        payload.status.value,
        payload.started_at.isoformat(),
    )

    if payload.status is AlertStatus.firing:
        background.add_task(_run_commit_analysis, payload, settings)

    return AlertAck(alert_name=payload.alert_name)


def create_app() -> FastAPI:
    """Build and configure the FastAPI application."""
    configure_logging()
    app = FastAPI(
        title="IncidentLens",
        version="0.1.0",
        summary="Autonomous AI SRE copilot for evidence-driven incident investigation.",
    )
    app.include_router(router)
    return app


app = create_app()
