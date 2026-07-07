"""Postmortem generator — the final orchestrator stage.

Runs on the *resolve* path: once an incident closes, it consolidates the
investigation findings (alert, impact estimate, suspected commit, matched
runbook) into a Markdown postmortem and writes it to ``postmortems/``.

The document is assembled deterministically from the structured findings, so the
stage runs without an API key. When ``ANTHROPIC_API_KEY`` is set, the narrative
sections (summary and action items) are drafted by Claude and folded in; on any
failure it silently falls back to the deterministic template.

Entry points: :func:`build_postmortem` (pure), :func:`generate_postmortem`
(build + write).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from config import Settings, get_settings
from schemas import AlertPayload

# Type-only imports of the other stages' result types, kept lazy at call sites so
# this module has no hard dependency on their runtime deps.
if TYPE_CHECKING:
    from agents.commit_analyzer import CommitCorrelation
    from agents.impact_estimator import ImpactEstimate
    from agents.runbook_retriever import RunbookMatch

log = logging.getLogger("incidentlens.postmortem_gen")


@dataclass(frozen=True)
class Narrative:
    """The prose sections of a postmortem (deterministic default or AI-drafted)."""

    summary: str
    action_items: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class Postmortem:
    """A rendered postmortem report and its metadata."""

    title: str
    service: str
    incident_start: datetime
    incident_end: datetime
    duration_seconds: float
    markdown: str
    filename: str
    ai_drafted: bool = False


def _ensure_utc(dt: datetime) -> datetime:
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=UTC)


def _format_duration(seconds: float) -> str:
    """Human-readable duration, e.g. ``1h 23m`` or ``4m 12s``."""
    total = int(max(seconds, 0))
    hours, rem = divmod(total, 3600)
    minutes, secs = divmod(rem, 60)
    parts = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if secs or not parts:
        parts.append(f"{secs}s")
    return " ".join(parts)


def _slugify(text: str) -> str:
    """Filesystem-safe lowercase slug."""
    slug = re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")
    return slug or "incident"


def _default_summary(
    alert: AlertPayload,
    impact: ImpactEstimate | None,
    commit: CommitCorrelation | None,
) -> str:
    """A one-paragraph summary assembled from the structured findings."""
    sentences = [
        f"A '{alert.severity.value}' incident, '{alert.alert_name}', affected the "
        f"{alert.service} service."
    ]
    if impact is not None and impact.failed_requests:
        sentences.append(
            f"Approximately {impact.failed_requests:,} of {impact.total_requests:,} "
            f"requests failed ({impact.error_rate_pct}% error rate vs a "
            f"{impact.baseline_error_rate_pct}% baseline), affecting an estimated "
            f"{impact.estimated_affected_users:,} users."
        )
    if commit is not None and commit.suspected_commit != "none":
        sentences.append(
            f"The most likely cause was commit {commit.suspected_commit[:10]} "
            f"(confidence: {commit.confidence})."
        )
    return " ".join(sentences)


def _default_action_items(
    commit: CommitCorrelation | None,
    runbook: RunbookMatch | None,
) -> list[str]:
    """Deterministic follow-up actions derived from the findings."""
    items: list[str] = []
    if commit is not None and commit.suspected_commit != "none":
        items.append(commit.recommended_action)
        items.append(
            f"Add a regression test covering the change in "
            f"{commit.suspected_commit[:10]}."
        )
    else:
        items.append("Identify the root cause; the automated commit correlation was inconclusive.")
    if runbook is not None:
        items.append(
            f"Review and update the runbook '{runbook.runbook.title}' with lessons learned."
        )
    items.append("Add or tune alerting to catch this failure mode earlier.")
    return items


def _default_narrative(
    alert: AlertPayload,
    commit: CommitCorrelation | None,
    runbook: RunbookMatch | None,
    impact: ImpactEstimate | None,
) -> Narrative:
    return Narrative(
        summary=_default_summary(alert, impact, commit),
        action_items=_default_action_items(commit, runbook),
    )


# --- AI narrative -----------------------------------------------------------

_SYSTEM_PROMPT = """\
You are IncidentLens, an SRE copilot drafting a blameless postmortem. You are
given structured findings from an automated investigation. Write a concise,
factual summary and concrete, actionable follow-up items. Stay grounded strictly
in the findings provided — do not invent causes, metrics, or events. Keep the
tone blameless: focus on systems and process, not individuals."""

_NARRATIVE_TOOL = {
    "name": "report_narrative",
    "description": "Report the drafted postmortem narrative. Call exactly once.",
    "strict": True,
    "input_schema": {
        "type": "object",
        "properties": {
            "summary": {
                "type": "string",
                "description": "A concise blameless summary paragraph of the incident.",
            },
            "action_items": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Concrete follow-up actions grounded in the findings.",
            },
        },
        "required": ["summary", "action_items"],
        "additionalProperties": False,
    },
}


def _findings_brief(
    alert: AlertPayload,
    commit: CommitCorrelation | None,
    runbook: RunbookMatch | None,
    impact: ImpactEstimate | None,
) -> str:
    """A compact plain-text rollup of the findings for the model."""
    lines = [
        f"Alert: {alert.alert_name} (severity {alert.severity.value}) on {alert.service}",
        f"Started: {alert.started_at.isoformat()}",
    ]
    if alert.resolved_at:
        lines.append(f"Resolved: {alert.resolved_at.isoformat()}")
    if alert.summary:
        lines.append(f"Alert summary: {alert.summary}")
    if impact is not None:
        lines.append(
            f"Impact: {impact.failed_requests:,}/{impact.total_requests:,} requests failed "
            f"({impact.error_rate_pct}% vs {impact.baseline_error_rate_pct}% baseline), "
            f"peak p99 {impact.peak_latency_p99_ms}ms, ~{impact.estimated_affected_users:,} users."
        )
    if commit is not None:
        lines.append(
            f"Suspected commit: {commit.suspected_commit} (confidence {commit.confidence}). "
            f"{commit.reasoning} Recommended: {commit.recommended_action}"
        )
    if runbook is not None:
        lines.append(f"Matched runbook: {runbook.runbook.title} ({runbook.runbook.path})")
    return "\n".join(lines)


def _ai_narrative(
    alert: AlertPayload,
    commit: CommitCorrelation | None,
    runbook: RunbookMatch | None,
    impact: ImpactEstimate | None,
    settings: Settings,
) -> Narrative | None:
    """Draft the narrative with Claude, or return ``None`` if unavailable/failed."""
    if not settings.anthropic_api_key:
        return None
    try:
        from config import get_anthropic_client

        client = get_anthropic_client()
        prompt = (
            "Draft the postmortem narrative for the following investigation "
            "findings. Call report_narrative with your result.\n\n"
            + _findings_brief(alert, commit, runbook, impact)
        )
        response = client.messages.create(
            model=settings.claude_model_reasoning,
            max_tokens=4000,
            system=_SYSTEM_PROMPT,
            tools=[_NARRATIVE_TOOL],
            tool_choice={"type": "tool", "name": "report_narrative"},
            messages=[{"role": "user", "content": prompt}],
        )
        for block in response.content:
            if block.type == "tool_use" and block.name == "report_narrative":
                data = block.input
                return Narrative(
                    summary=data["summary"],
                    action_items=list(data.get("action_items", [])),
                )
        log.warning("postmortem narrative: model returned no tool call")
        return None
    except Exception:  # never let the drafting failure break postmortem generation
        log.exception("AI postmortem narrative failed; falling back to template")
        return None


# --- Rendering --------------------------------------------------------------


def build_postmortem(
    alert: AlertPayload,
    *,
    commit: CommitCorrelation | None = None,
    runbook: RunbookMatch | None = None,
    impact: ImpactEstimate | None = None,
    narrative: Narrative | None = None,
) -> Postmortem:
    """Assemble a Markdown postmortem from the investigation findings.

    Deterministic and keyless: pass ``narrative`` to override the default
    template-derived prose (that is how the AI path injects its draft). The
    incident window is bounded by ``resolved_at`` when present, else now.
    """
    start = _ensure_utc(alert.started_at)
    end = _ensure_utc(alert.resolved_at) if alert.resolved_at else datetime.now(UTC)
    duration = max((end - start).total_seconds(), 0.0)
    narrative = narrative or _default_narrative(alert, commit, runbook, impact)

    title = f"Postmortem: {alert.alert_name} — {alert.service}"

    lines: list[str] = [
        f"# {title}",
        "",
        f"- **Service:** {alert.service}",
        f"- **Severity:** {alert.severity.value}",
        f"- **Status:** {alert.status.value}",
        f"- **Started:** {start.isoformat()}",
        f"- **Resolved:** {end.isoformat()}"
        + ("" if alert.resolved_at else " _(ongoing — generated before resolution)_"),
        f"- **Duration:** {_format_duration(duration)}",
        "",
        "## Summary",
        "",
        narrative.summary,
        "",
        "## Timeline",
        "",
        f"- `{start.isoformat()}` — Alert **{alert.alert_name}** started firing.",
    ]
    if alert.summary:
        lines.append(f"  - {alert.summary}")
    lines.append(
        f"- `{end.isoformat()}` — Incident "
        + ("resolved." if alert.resolved_at else "still open at time of writing.")
    )

    lines += ["", "## Impact", ""]
    if impact is not None:
        lines += [
            f"- **Requests:** ~{impact.total_requests:,} total, "
            f"{impact.failed_requests:,} failed",
            f"- **Error rate:** {impact.error_rate_pct}% "
            f"(baseline {impact.baseline_error_rate_pct}%)",
            f"- **Peak p99 latency:** {impact.peak_latency_p99_ms} ms",
            f"- **Estimated users affected:** ~{impact.estimated_affected_users:,}",
        ]
    else:
        lines.append("_No impact metrics were available for this incident._")

    lines += ["", "## Root Cause", ""]
    if commit is not None and commit.suspected_commit != "none":
        lines += [
            f"Suspected commit `{commit.suspected_commit[:10]}` "
            f"(confidence: **{commit.confidence}**).",
            "",
            commit.reasoning,
            "",
            f"**Recommended action:** {commit.recommended_action}",
        ]
    elif commit is not None:
        lines.append(
            "The automated commit correlation did not identify a likely culprit "
            f"(confidence: {commit.confidence})."
        )
    else:
        lines.append("_Commit correlation was not run for this incident._")

    lines += ["", "## Detection & Response", ""]
    if runbook is not None:
        lines.append(
            f"Matched runbook: **{runbook.runbook.title}** "
            f"(`{runbook.runbook.path}`, match score {runbook.score})."
        )
    else:
        lines.append("_No runbook matched this incident._")

    lines += ["", "## Action Items", ""]
    if narrative.action_items:
        lines += [f"- [ ] {item}" for item in narrative.action_items]
    else:
        lines.append("_No action items were generated._")

    lines += [
        "",
        "---",
        "",
        "_Generated by IncidentLens — read-only diagnosis; no changes were made to "
        "any system. Review and edit before publishing._",
        "",
    ]

    date_str = end.date().isoformat()
    filename = f"{date_str}-{_slugify(alert.service)}-{_slugify(alert.alert_name)}.md"

    return Postmortem(
        title=title,
        service=alert.service,
        incident_start=start,
        incident_end=end,
        duration_seconds=duration,
        markdown="\n".join(lines),
        filename=filename,
        # build_postmortem can't tell whether `narrative` was AI-drafted or the
        # default template; generate_postmortem stamps the true value.
        ai_drafted=False,
    )


def write_postmortem(doc: Postmortem, directory: str | Path) -> Path:
    """Write ``doc`` to ``directory/<filename>`` and return the path."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / doc.filename
    path.write_text(doc.markdown, encoding="utf-8")
    return path


def generate_postmortem(
    alert: AlertPayload,
    *,
    commit: CommitCorrelation | None = None,
    runbook: RunbookMatch | None = None,
    impact: ImpactEstimate | None = None,
    settings: Settings | None = None,
    directory: str | Path | None = None,
    use_ai: bool = True,
    write: bool = True,
) -> tuple[Postmortem, Path | None]:
    """Build and (optionally) write a postmortem for a resolved incident.

    Uses an AI-drafted narrative when ``use_ai`` and an API key are available,
    else the deterministic template. Returns the document and the path it was
    written to (or ``None`` when ``write`` is False).
    """
    settings = settings or get_settings()
    narrative = None
    ai_drafted = False
    if use_ai:
        narrative = _ai_narrative(alert, commit, runbook, impact, settings)
        ai_drafted = narrative is not None

    doc = build_postmortem(
        alert, commit=commit, runbook=runbook, impact=impact, narrative=narrative
    )
    # build_postmortem can't know whether the narrative came from AI; stamp it here.
    doc = Postmortem(**{**doc.__dict__, "ai_drafted": ai_drafted})

    path = None
    if write:
        path = write_postmortem(doc, directory or settings.postmortems_dir)
        log.info(
            "postmortem written to %s (ai_drafted=%s)", path, ai_drafted
        )
    return doc, path
