"""Commit analyzer — the first orchestrator stage.

Given a firing alert, this stage lets Claude investigate the target repository's
recent history (via read-only git tools) and correlate a deploy/commit with the
metric degradation. It is strictly diagnostic: the tools cannot write, revert,
or execute anything — Claude reads `git log` / `git diff` and returns a verdict.

Entry point: `analyze_commits(...)` → `CommitCorrelation`.
"""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from datetime import datetime

from config import get_anthropic_client, get_settings
from tools.git_tools import GitError, get_commit_diff, list_recent_commits

log = logging.getLogger("incidentlens.commit_analyzer")

# Cap the agentic loop so a confused model can't spin indefinitely against a
# large repo. Investigations resolve in a handful of tool calls in practice.
MAX_ITERATIONS = 12

SYSTEM_PROMPT = """\
You are IncidentLens, an autonomous SRE copilot investigating a production alert.

Your job in this stage is to determine whether a recent code change likely
caused the degradation described by the alert. You are STRICTLY READ-ONLY: you
diagnose and recommend, you never roll back or execute changes.

Method:
1. List the repository's recent commits, focusing on those landing shortly
   before the alert started.
2. Inspect the diffs of the most plausible suspects. Reason about how each
   change could produce the observed symptom in the affected service.
3. Call `report_correlation` exactly once with your verdict. Set confidence
   honestly — if nothing in the history plausibly explains the alert, say so
   with suspected_commit "none" and confidence "none".

Prefer the smallest set of tool calls that lets you reach a well-grounded
conclusion. Ground every claim in a diff you actually inspected."""

# --- Tool schemas exposed to Claude -----------------------------------------

_TOOLS = [
    {
        "name": "list_recent_commits",
        "description": (
            "List recent commits in the repository under investigation, newest "
            "first. Use this first to see what landed before the alert."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "max_count": {
                    "type": "integer",
                    "description": "How many commits to return (default 20).",
                }
            },
        },
    },
    {
        "name": "get_commit_diff",
        "description": (
            "Show the unified diff and metadata for a single commit. Use this to "
            "inspect a suspect commit's actual changes."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "sha": {
                    "type": "string",
                    "description": "The commit SHA to inspect (full or abbreviated).",
                }
            },
            "required": ["sha"],
        },
    },
    {
        "name": "report_correlation",
        "description": (
            "Report your final verdict on which commit (if any) likely caused the "
            "alert. Call this exactly once, at the end of your investigation."
        ),
        "strict": True,
        "input_schema": {
            "type": "object",
            "properties": {
                "suspected_commit": {
                    "type": "string",
                    "description": "SHA of the most likely culprit, or 'none'.",
                },
                "confidence": {
                    "type": "string",
                    "enum": ["high", "medium", "low", "none"],
                },
                "reasoning": {
                    "type": "string",
                    "description": "Evidence-grounded explanation of the correlation.",
                },
                "recommended_action": {
                    "type": "string",
                    "description": "Suggested next step for the on-call engineer.",
                },
            },
            "required": [
                "suspected_commit",
                "confidence",
                "reasoning",
                "recommended_action",
            ],
            "additionalProperties": False,
        },
    },
]


@dataclass
class CommitCorrelation:
    """Structured result of the commit-correlation stage."""

    suspected_commit: str
    confidence: str
    reasoning: str
    recommended_action: str

    def to_dict(self) -> dict:
        return asdict(self)


def _execute_tool(repo_path: str, name: str, tool_input: dict) -> str:
    """Run a git tool and return its result as a JSON string for the model."""
    if name == "list_recent_commits":
        max_count = int(tool_input.get("max_count", 20))
        return json.dumps(list_recent_commits(repo_path, max_count=max_count))
    if name == "get_commit_diff":
        return json.dumps(get_commit_diff(repo_path, tool_input["sha"]))
    raise GitError(f"unknown tool: {name}")


def analyze_commits(
    *,
    repo_path: str,
    alert_name: str,
    service: str,
    severity: str,
    started_at: datetime,
    summary: str | None = None,
) -> CommitCorrelation:
    """Investigate `repo_path` and correlate a commit with the alert.

    Runs a bounded read-only tool-use loop. Returns a CommitCorrelation; if the
    model ends without reporting, returns a low-signal 'none' verdict rather
    than raising, so the orchestrator can continue.
    """
    client = get_anthropic_client()
    model = get_settings().claude_model_reasoning

    alert_block = (
        f"A '{severity}' alert named '{alert_name}' is firing for service "
        f"'{service}', started at {started_at.isoformat()}."
    )
    if summary:
        alert_block += f"\nAlert summary: {summary}"
    alert_block += (
        "\n\nInvestigate the repository and determine whether a recent commit "
        "likely caused this. Report your verdict with report_correlation."
    )

    messages: list[dict] = [{"role": "user", "content": alert_block}]

    for _ in range(MAX_ITERATIONS):
        response = client.messages.create(
            model=model,
            max_tokens=16000,
            thinking={"type": "adaptive"},
            system=SYSTEM_PROMPT,
            tools=_TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            # Model stopped without calling a tool (e.g. end_turn). No verdict.
            log.warning("commit analyzer ended with stop_reason=%s", response.stop_reason)
            break

        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        verdict: CommitCorrelation | None = None
        for block in response.content:
            if block.type != "tool_use":
                continue

            if block.name == "report_correlation":
                verdict = CommitCorrelation(**block.input)
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": "Verdict recorded.",
                    }
                )
                continue

            try:
                result = _execute_tool(repo_path, block.name, block.input)
                is_error = False
            except GitError as exc:
                result = f"Error: {exc}"
                is_error = True
                log.warning("git tool %s failed: %s", block.name, exc)

            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": result,
                    "is_error": is_error,
                }
            )

        messages.append({"role": "user", "content": tool_results})

        if verdict is not None:
            log.info(
                "commit correlation: suspect=%s confidence=%s",
                verdict.suspected_commit,
                verdict.confidence,
            )
            return verdict

    # Fell through the loop without a verdict — return a safe non-finding.
    return CommitCorrelation(
        suspected_commit="none",
        confidence="none",
        reasoning="Investigation did not converge on a verdict within the tool-call budget.",
        recommended_action="Manually review recent deploys for the affected service.",
    )
