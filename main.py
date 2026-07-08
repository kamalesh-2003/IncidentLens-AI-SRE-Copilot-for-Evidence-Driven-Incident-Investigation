"""IncidentLens webhook receiver.

Accepts production alert payloads, validates them, and hands each one to the
LangGraph incident workflow (:mod:`orchestrator`) as a non-blocking background
task. The workflow runs the diagnostic stages (commit correlation, runbook
retrieval, impact estimation) and then, by alert status, either posts a Slack
brief (firing) or writes a postmortem (resolved).

Run locally with::

    uvicorn main:app --reload --port 8000
"""
from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, status

from config import Settings, configure_logging, get_settings
from schemas import AlertAck, AlertPayload, HealthResponse

log = logging.getLogger("incidentlens.api")

router = APIRouter()


def _run_incident(payload: AlertPayload, settings: Settings) -> None:
    """Run the full LangGraph incident workflow (background task).

    Delegates to the orchestrator, which threads the diagnostic findings through
    the stages and picks the terminal stage by alert status (firing → Slack
    brief, resolved → postmortem). Imported lazily so the receiver boots without
    loading the agent stack, and guarded so orchestration never crashes the app.
    """
    try:
        from orchestrator import run_incident

        run_incident(payload, settings)
    except Exception:  # never let orchestration crash the receiver
        log.exception("incident orchestration failed for %s", payload.alert_name)


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
    """Accept an alert and kick off the incident workflow without blocking."""
    log.info(
        "alert received: name=%s severity=%s service=%s status=%s started_at=%s",
        payload.alert_name,
        payload.severity.value,
        payload.service,
        payload.status.value,
        payload.started_at.isoformat(),
    )

    background.add_task(_run_incident, payload, settings)

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
