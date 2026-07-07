"""Demo of the postmortem generator stage.

Simulates a resolved incident, gathers real (keyless) runbook and impact findings
plus a sample commit correlation, then generates and writes a Markdown postmortem
(AI-drafted narrative if ANTHROPIC_API_KEY is set, else the deterministic
template).

    python -m demo.run_postmortem_gen
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from agents.commit_analyzer import CommitCorrelation
from agents.impact_estimator import estimate_impact
from agents.postmortem_gen import generate_postmortem
from agents.runbook_retriever import retrieve_for_alert
from schemas import AlertPayload, Severity

STARTED_AT = datetime.now(UTC) - timedelta(hours=1, minutes=23)
RESOLVED_AT = datetime.now(UTC)


def main() -> int:
    alert = AlertPayload(
        alert_name="HighErrorRate",
        severity=Severity.critical,
        service="checkout",
        status="resolved",
        started_at=STARTED_AT,
        resolved_at=RESOLVED_AT,
        summary="5xx rate on checkout exceeded 5% for 5 minutes.",
    )

    impact = estimate_impact(
        service=alert.service, started_at=alert.started_at, resolved_at=alert.resolved_at
    )
    matches = retrieve_for_alert(
        alert_name=alert.alert_name, service=alert.service, summary=alert.summary, top_k=1
    )
    commit = CommitCorrelation(
        suspected_commit="abc1234567def",
        confidence="high",
        reasoning="A recent deploy added a per-item DB lookup in the checkout hot path.",
        recommended_action="Roll back the deploy for the checkout service.",
    )

    doc, path = generate_postmortem(
        alert,
        commit=commit,
        runbook=matches[0] if matches else None,
        impact=impact,
    )

    print(doc.markdown)
    print(f"\n{'AI-drafted' if doc.ai_drafted else 'Template'} postmortem written to: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
