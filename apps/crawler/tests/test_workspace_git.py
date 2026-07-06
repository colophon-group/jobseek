"""Tests for workspace git wrappers (mocked subprocess)."""

from __future__ import annotations

import json
from unittest.mock import patch

from src.workspace.errors import WorkspaceError
from src.workspace.git import (
    _run,
    check_existing_prs,
    check_gh_auth,
    create_draft_pr,
    current_branch,
    sync_branch_with_main,
)


class TestGitWrappers:
    def test_current_branch(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.stdout = "main\n"
            assert current_branch() == "main"

    def test_check_gh_auth_success(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.returncode = 0
            assert check_gh_auth() is True

    def test_check_gh_auth_failure(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.returncode = 1
            assert check_gh_auth() is False

    def test_check_existing_prs_found(self):
        prs = [{"number": 42, "title": "Add stripe", "url": "https://github.com/..."}]
        with patch("src.workspace.git._run") as mock:
            mock.return_value.returncode = 0
            mock.return_value.stdout = json.dumps(prs)
            result = check_existing_prs(10)
            assert len(result) == 1
            assert result[0]["number"] == 42

    def test_check_existing_prs_none(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.returncode = 0
            mock.return_value.stdout = "[]"
            result = check_existing_prs(10)
            assert result == []

    def test_check_existing_prs_error(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.returncode = 1
            mock.return_value.stdout = ""
            result = check_existing_prs(10)
            assert result == []

    def test_create_draft_pr_parses_url(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.stdout = "https://github.com/owner/repo/pull/42\n"
            pr_number = create_draft_pr("Add stripe", "Closes #10")
            assert pr_number == 42

    def test_run_rejects_negative_retries(self):
        with patch("src.workspace.git.subprocess.run") as mock:
            try:
                _run(["git", "status"], retries=-1)
            except ValueError as exc:
                assert str(exc) == "retries must be non-negative"
            else:
                raise AssertionError("_run should reject negative retries")
            mock.assert_not_called()

    def test_sync_branch_with_main_requires_repo_root(self):
        with (
            patch("src.workspace.git._repo_cwd", return_value=None),
            patch("src.workspace.git.get_main_branch_remote") as get_main,
        ):
            try:
                sync_branch_with_main("feature")
            except WorkspaceError as exc:
                assert "inside a git repository" in str(exc)
            else:
                raise AssertionError("sync_branch_with_main should require a repo root")
            get_main.assert_not_called()
