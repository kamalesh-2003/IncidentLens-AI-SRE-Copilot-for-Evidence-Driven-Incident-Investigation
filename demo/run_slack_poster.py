"""Demo of the Slack poster stage.

Assembles an incident brief from real (keyless) runbook and impact findings plus
a sample commit correlation, then delivers it — posting to Slack if
SLACK_WEBHOOK_URL is set, otherwise printing the Block Kit JSON.

    python -m demo.run_slack_poster
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.commit_analyzer import CommitCorrelation
from agents.impact_estimator import estimate_impact
from agents.runbook_retriever import retrieve_for_alert
from agents.slack_poster import post_incident_brief
from schemas import AlertPayload, Severity

STARTED_AT = datetime.now(UTC) - timedelta(minutes=30)


def main() -> int:
    alert = AlertPayload(
        alert_name="HighErrorRate",
        severity=Severity.critical,
        service="checkout",
        started_at=STARTED_AT,
        summary="5xx rate on checkout exceeded 5% for 5 minutes.",
    )

    impact = estimate_impact(service=alert.service, started_at=alert.started_at)
    matches = retrieve_for_alert(
        alert_name=alert.alert_name, service=alert.service, summary=alert.summary, top_k=1
    )
    commit = CommitCorrelation(
        suspected_commit="abc1234567def",
        confidence="high",
        reasoning="A recent deploy added a per-item DB lookup in the checkout hot path.",
        recommended_action="Roll back the deploy for the checkout service.",
    )

    sent = post_incident_brief(
        alert,
        commit=commit,
        runbook=matches[0] if matches else None,
        impact=impact,
    )
    print("\nPosted to Slack." if sent else "\n(SLACK_WEBHOOK_URL unset — printed JSON above.)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
