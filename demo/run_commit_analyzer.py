"""End-to-end demo of the commit analyzer stage.

Builds a throwaway git repo with a planted regression, fires a mock alert at
it, and prints Claude's commit-correlation verdict. Requires ANTHROPIC_API_KEY.

    python -m demo.run_commit_analyzer
"""
from __future__ import annotations

import os
import subprocess
import sys
import tempfile
from datetime import UTC, datetime

from agents.commit_analyzer import analyze_commits
from config import get_settings


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


def build_demo_repo(repo: str) -> None:
    """Create a checkout service with a plausible latency regression."""
    _git(repo, "init")
    _git(repo, "config", "user.email", "demo@incidentlens.dev")
    _git(repo, "config", "user.name", "Demo Author")

    with open(os.path.join(repo, "checkout.py"), "w") as f:
        f.write(
            "def process_order(order):\n"
            "    total = sum(item.price for item in order.items)\n"
            "    return charge(total)\n"
        )
    _git(repo, "add", "checkout.py")
    _git(repo, "commit", "-m", "Initial checkout service")

    with open(os.path.join(repo, "README.md"), "w") as f:
        f.write("# Checkout service\n")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "Add README")

    # The regression: a synchronous per-item DB lookup inside the hot path.
    with open(os.path.join(repo, "checkout.py"), "w") as f:
        f.write(
            "def process_order(order):\n"
            "    total = 0\n"
            "    for item in order.items:\n"
            "        # NEW: fetch live price from DB on every item (N+1 queries)\n"
            "        price = db.query('SELECT price FROM prices WHERE sku=?', item.sku)\n"
            "        total += price\n"
            "    return charge(total)\n"
        )
    _git(repo, "add", "checkout.py")
    _git(repo, "commit", "-m", "Fetch live prices per item at checkout")


def main() -> int:
    if not get_settings().anthropic_api_key:
        print("ANTHROPIC_API_KEY is not set — cannot run the live demo.", file=sys.stderr)
        return 1

    with tempfile.TemporaryDirectory() as repo:
        build_demo_repo(repo)
        print(f"Built demo repo at {repo}\n")

        verdict = analyze_commits(
            repo_path=repo,
            alert_name="HighCheckoutLatency",
            service="checkout",
            severity="critical",
            started_at=datetime.now(UTC),
            summary="p99 latency on checkout jumped from 120ms to 2.3s.",
        )

    print("=== Commit correlation verdict ===")
    print(f"Suspected commit : {verdict.suspected_commit}")
    print(f"Confidence       : {verdict.confidence}")
    print(f"Reasoning        : {verdict.reasoning}")
    print(f"Recommended      : {verdict.recommended_action}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
