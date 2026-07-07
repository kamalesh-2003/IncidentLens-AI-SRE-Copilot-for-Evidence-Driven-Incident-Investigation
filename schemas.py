"""Pydantic schemas for the IncidentLens API.

The alert schema is provider-agnostic: Prometheus, Datadog, or Grafana webhooks are
adapted to this shape before reaching the service.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class Severity(str, Enum):
    """Alert severity, ordered most to least urgent."""

    critical = "critical"
    high = "high"
    medium = "medium"
    low = "low"


class AlertStatus(str, Enum):
    """Whether the alert is currently firing or has resolved."""

    firing = "firing"
    resolved = "resolved"


class AlertPayload(BaseModel):
    """Normalized alert received on the webhook."""

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "alert_name": "HighErrorRate",
                "severity": "critical",
                "service": "checkout",
                "status": "firing",
                "started_at": "2026-07-03T14:12:00Z",
                "summary": "5xx rate on checkout exceeded 5% for 5 minutes.",
            }
        }
    )

    alert_name: str = Field(..., description="Short identifier of the alert rule.")
    severity: Severity = Field(..., description="Alert severity.")
    service: str = Field(..., description="Affected service or component.")
    status: AlertStatus = Field(
        default=AlertStatus.firing, description="Firing or resolved."
    )
    started_at: datetime = Field(..., description="When the alert began firing.")
    resolved_at: datetime | None = Field(
        default=None, description="When the alert resolved (required if status=resolved)."
    )
    summary: str | None = Field(default=None, description="Human-readable description.")

    @model_validator(mode="after")
    def _resolved_requires_timestamp(self) -> AlertPayload:
        # A resolution timestamp bounds the incident window for the postmortem stage.
        if self.status is AlertStatus.resolved and self.resolved_at is None:
            raise ValueError("resolved_at is required when status is 'resolved'")
        return self


class AlertAck(BaseModel):
    """Acknowledgement returned when an alert is accepted for processing."""

    received: bool = Field(default=True)
    alert_name: str


class HealthResponse(BaseModel):
    """Liveness probe response."""

    status: str = Field(default="ok")
    service: str = Field(default="incidentlens")
