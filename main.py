"""IncidentLens webhook receiver.

Accepts production alert payloads, validates them, and hands them off to the
orchestrator for commit correlation, runbook retrieval, impact estimation,
Slack posting, and postmortem generation.
"""
from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, status
from pydantic import BaseModel, Field

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
log = logging.getLogger("incidentlens")

app = FastAPI(title="IncidentLens", version="0.1.0")


class Severity(str, Enum):
    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class AlertStatus(str, Enum):
    firing = "firing"
    resolved = "resolved"


class AlertPayload(BaseModel):
    """Normalized alert schema. Prometheus/Datadog webhooks can be adapted to this."""

    alert_name: str = Field(..., description="Short identifier of the alert rule")
    severity: Severity
    service: str = Field(..., description="Affected service or component")
    status: AlertStatus = AlertStatus.firing
    started_at: datetime
    resolved_at: Optional[datetime] = None
    summary: Optional[str] = Field(None, description="Human-readable alert description")


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "service": "incidentlens"}


@app.post("/webhook/alert", status_code=status.HTTP_202_ACCEPTED)
def receive_alert(payload: AlertPayload) -> dict:
    # Resolved alerts must carry a resolution timestamp so the postmortem
    # generator can bound the incident window.
    if payload.status == AlertStatus.resolved and payload.resolved_at is None:
        raise HTTPException(400, "resolved_at is required when status=resolved")

    log.info(
        "alert received: name=%s severity=%s service=%s status=%s started_at=%s",
        payload.alert_name,
        payload.severity.value,
        payload.service,
        payload.status.value,
        payload.started_at.isoformat(),
    )
    return {"received": True, "alert_name": payload.alert_name}
