"""Demo of the impact estimator stage.

Estimates the impact of a sample 30-minute checkout incident. Works with no
external dependencies — it uses the mock metrics store unless PROMETHEUS_URL is
set.

    python -m demo.run_impact_estimator
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.impact_estimator import estimate_impact

START = datetime.now(UTC) - timedelta(minutes=30)
END = datetime.now(UTC)


def main() -> int:
    estimate = estimate_impact(service="checkout", started_at=START, resolved_at=END)

    print("=== Incident impact estimate ===")
    print(f"Service          : {estimate.service}")
    print(f"Window           : {estimate.duration_seconds / 60:.0f} min")
    print(f"Total requests   : {estimate.total_requests:,}")
    print(f"Failed requests  : {estimate.failed_requests:,} ({estimate.error_rate_pct}%)")
    print(f"Baseline error   : {estimate.baseline_error_rate_pct}%")
    print(f"Peak p99 latency : {estimate.peak_latency_p99_ms} ms")
    print(f"Affected users   : ~{estimate.estimated_affected_users:,}")
    print(f"Source           : {estimate.source}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
