"""Tests for the read-only git inspection helpers.

These build a throwaway git repo in a temp dir, so they need `git` on PATH but
not the `anthropic` SDK.
"""
from __future__ import annotations

import subprocess

import pytest

from tools.git_tools import GitError, get_commit_diff, list_recent_commits


def _git(repo, *args):
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)


@pytest.fixture
def repo(tmp_path):
    """A tiny repo with three commits, oldest to newest."""
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@incidentlens.dev")
    _git(tmp_path, "config", "user.name", "Test Runner")

    (tmp_path / "app.py").write_text("def handler():\n    return 200\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "Initial handler")

    (tmp_path / "app.py").write_text("def handler():\n    return 200  # ok\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "Add comment")

    (tmp_path / "app.py").write_text("def handler():\n    raise RuntimeError('boom')\n")
    _git(tmp_path, "add", "app.py")
    _git(tmp_path, "commit", "-m", "Refactor error handling")

    return tmp_path


def test_list_recent_commits_newest_first(repo):
    commits = list_recent_commits(str(repo))
    assert len(commits) == 3
    assert commits[0]["subject"] == "Refactor error handling"
    assert commits[-1]["subject"] == "Initial handler"
    for c in commits:
        assert len(c["sha"]) == 40
        assert c["short_sha"] == c["sha"][:10]
        assert c["author"] == "Test Runner"


def test_list_recent_commits_respects_max_count(repo):
    commits = list_recent_commits(str(repo), max_count=1)
    assert len(commits) == 1
    assert commits[0]["subject"] == "Refactor error handling"


def test_get_commit_diff_returns_change(repo):
    newest = list_recent_commits(str(repo), max_count=1)[0]
    diff = get_commit_diff(str(repo), newest["sha"])
    assert diff["subject"] == "Refactor error handling"
    assert "RuntimeError('boom')" in diff["diff"]
    assert diff["truncated"] is False


def test_get_commit_diff_truncates(repo):
    newest = list_recent_commits(str(repo), max_count=1)[0]
    diff = get_commit_diff(str(repo), newest["sha"], max_chars=10)
    assert diff["truncated"] is True
    assert len(diff["diff"]) == 10


def test_bad_repo_path_raises():
    with pytest.raises(GitError):
        list_recent_commits("/path/that/does/not/exist")
