"""Demo of the full LangGraph incident workflow.

Runs one alert end-to-end through the orchestrator twice: first firing (→ Slack
brief, printed if SLACK_WEBHOOK_URL is unset), then resolved (→ postmortem written
to postmortems/). Uses whatever backends are configured; with no keys it runs
fully on the keyless fallbacks.

    python -m demo.run_orchestrator
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from orchestrator import run_incident
from schemas import AlertPayload, AlertStatus, Severity

STARTED_AT = datetime.now(UTC) - timedelta(hours=1, minutes=23)


def main() -> int:
    firing = AlertPayload(
        alert_name="HighErrorRate",
        severity=Severity.critical,
        service="checkout",
        started_at=STARTED_AT,
        summary="5xx rate on checkout exceeded 5% for 5 minutes.",
    )

    print("=== FIRING: running investigation + Slack brief ===\n")
    fired = run_incident(firing)
    print(
        f"\n-> commit={_desc(fired.get('commit'))}, "
        f"runbook={_desc(fired.get('runbook'))}, "
        f"impact={'yes' if fired.get('impact') else 'no'}, "
        f"brief_posted={fired.get('brief_posted')}"
    )

    resolved = firing.model_copy(
        update={"status": AlertStatus.resolved, "resolved_at": datetime.now(UTC)}
    )
    print("\n=== RESOLVED: running investigation + postmortem ===\n")
    closed = run_incident(resolved)
    print(f"\n-> postmortem written to: {closed.get('postmortem_path')}")
    return 0


def _desc(finding: object) -> str:
    return "none" if finding is None else type(finding).__name__


if __name__ == "__main__":
    raise SystemExit(main())
