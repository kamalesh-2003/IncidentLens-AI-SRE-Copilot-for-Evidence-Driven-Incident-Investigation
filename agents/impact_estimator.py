"""Impact estimator — the third orchestrator stage.

Sizes an incident from the RED signals over its window: how many requests were
served, how many failed, the error-rate lift over baseline, peak p99 latency, and
a rough estimate of affected users. Reads from :func:`metrics.get_metrics_store`
(Prometheus or the mock store), so it runs without external dependencies.

Entry point: :func:`estimate_impact` → :class:`ImpactEstimate`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from metrics import DEFAULT_STEP_SECONDS, MetricsStore, get_metrics_store

log = logging.getLogger("incidentlens.impact_estimator")

# Rough heuristic for translating failed requests into distinct affected users.
# Real deployments would derive this from session analytics.
AVERAGE_REQUESTS_PER_USER = 4


@dataclass(frozen=True)
class ImpactEstimate:
    """Quantified user/traffic impact of an incident over its window."""

    service: str
    window_start: datetime
    window_end: datetime
    duration_seconds: float
    total_requests: int
    failed_requests: int
    error_rate_pct: float
    baseline_error_rate_pct: float
    peak_latency_p99_ms: float
    estimated_affected_users: int
    source: str


def _ensure_utc(dt: datetime) -> datetime:
    """Treat naive timestamps as UTC so window arithmetic is unambiguous."""
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def estimate_impact(
    *,
    service: str,
    started_at: datetime,
    resolved_at: datetime | None = None,
    store: MetricsStore | None = None,
    step_seconds: int = DEFAULT_STEP_SECONDS,
) -> ImpactEstimate:
    """Estimate the impact of an incident on ``service`` over its window.

    The window runs from ``started_at`` to ``resolved_at`` (or now, if still
    firing). Missing metrics degrade to a zeroed estimate rather than raising.
    """
    store = store or get_metrics_store()
    start = _ensure_utc(started_at)
    end = _ensure_utc(resolved_at) if resolved_at else datetime.now(UTC)
    duration = max((end - start).total_seconds(), 0.0)

    rate = store.request_rate(service, start, end, step_seconds)
    errors = store.error_rate(service, start, end, step_seconds)
    latency = store.latency_p99_ms(service, start, end, step_seconds)

    # Integrate rate over the window (requests ≈ Σ rate · step).
    total_requests = int(sum(sample.value * step_seconds for sample in rate))
    # strict=False: series may differ in length (Prometheus); align on the shorter.
    failed_requests = int(
        sum(r.value * e.value * step_seconds for r, e in zip(rate, errors, strict=False))
    )

    error_rate_pct = (failed_requests / total_requests * 100) if total_requests else 0.0
    baseline_error_rate_pct = (errors[0].value * 100) if errors else 0.0
    peak_latency = max((sample.value for sample in latency), default=0.0)

    return ImpactEstimate(
        service=service,
        window_start=start,
        window_end=end,
        duration_seconds=duration,
        total_requests=total_requests,
        failed_requests=failed_requests,
        error_rate_pct=round(error_rate_pct, 2),
        baseline_error_rate_pct=round(baseline_error_rate_pct, 2),
        peak_latency_p99_ms=round(peak_latency, 1),
        estimated_affected_users=failed_requests // AVERAGE_REQUESTS_PER_USER,
        source=store.name,
    )
