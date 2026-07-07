"""Read-only git inspection helpers for the commit analyzer.

Every function shells out to `git` (no writes, no checkout, no fetch) against a
target repository. These back the tools the commit analyzer exposes to Claude,
so keep their return values compact and JSON-friendly — they flow straight into
the model's context window.

This module intentionally has no `anthropic` dependency so it can be unit
tested without the SDK installed.
"""
from __future__ import annotations

import subprocess
from datetime import datetime

# Unit-separated pretty format: sha, author name, ISO date, subject.
_LOG_FORMAT = "%H%x1f%an%x1f%aI%x1f%s"
_FIELD_SEP = "\x1f"


class GitError(RuntimeError):
    """Raised when a git command fails or the repo path is unusable."""


def _run_git(repo_path: str, *args: str, timeout: int = 15) -> str:
    """Run a git command in `repo_path` and return stdout.

    Raises GitError on non-zero exit or missing git/repo so callers can turn
    the failure into a tool_result with is_error=True.
    """
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise GitError(f"git {' '.join(args)} timed out after {timeout}s") from exc
    except OSError as exc:
        # Missing git on PATH, or an invalid/nonexistent repo path (the latter
        # surfaces as NotADirectoryError on Windows, FileNotFoundError on POSIX).
        raise GitError(f"could not run git in '{repo_path}': {exc}") from exc

    if proc.returncode != 0:
        raise GitError(
            f"git {' '.join(args)} failed ({proc.returncode}): {proc.stderr.strip()}"
        )
    return proc.stdout


def list_recent_commits(
    repo_path: str,
    max_count: int = 20,
    since: datetime | None = None,
) -> list[dict]:
    """Return recent commits, newest first.

    Each entry: {sha, short_sha, author, date, subject}. `since` (if given)
    limits to commits at or after that instant — useful for scoping the search
    to the window leading up to an alert.
    """
    args = ["log", f"--max-count={max_count}", f"--pretty=format:{_LOG_FORMAT}"]
    if since is not None:
        args.append(f"--since={since.isoformat()}")

    out = _run_git(repo_path, *args)
    commits: list[dict] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        sha, author, date, subject = line.split(_FIELD_SEP)
        commits.append(
            {
                "sha": sha,
                "short_sha": sha[:10],
                "author": author,
                "date": date,
                "subject": subject,
            }
        )
    return commits


def get_commit_diff(repo_path: str, sha: str, max_chars: int = 8000) -> dict:
    """Return metadata + a (possibly truncated) unified diff for one commit.

    The diff is capped at `max_chars` so a single large refactor can't blow out
    the model's context; truncation is flagged in the returned dict.
    """
    # --stat first so the summary survives even when the full diff is truncated.
    stat = _run_git(repo_path, "show", "--stat", "--pretty=format:%an%x1f%aI%x1f%s", sha)
    header, _, _ = stat.partition("\n")
    try:
        author, date, subject = header.split(_FIELD_SEP)
    except ValueError:  # unexpected format; surface raw header
        author = date = subject = header

    diff = _run_git(repo_path, "show", "--pretty=format:", sha)
    truncated = len(diff) > max_chars
    if truncated:
        diff = diff[:max_chars]

    return {
        "sha": sha,
        "author": author,
        "date": date,
        "subject": subject,
        "diff": diff,
        "truncated": truncated,
    }
