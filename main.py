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
import secrets
from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Depends, FastAPI, Header, HTTPException, status

from config import Settings, configure_logging, get_settings
from schemas import AlertAck, AlertPayload, HealthResponse

log = logging.getLogger("incidentlens.api")

router = APIRouter()


def verify_webhook_token(
    settings: Annotated[Settings, Depends(get_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    """Authenticate the alert webhook against ``WEBHOOK_TOKEN`` when configured.

    When no token is set the webhook is open (dev convenience). When set, callers
    must send ``Authorization: Bearer <token>``; the comparison is constant-time.
    """
    if not settings.webhook_token:
        return
    scheme, _, value = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(value, settings.webhook_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing webhook token",
            headers={"WWW-Authenticate": "Bearer"},
        )


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
    dependencies=[Depends(verify_webhook_token)],
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
