"""Tests for workspace git wrappers (mocked subprocess)."""

from __future__ import annotations

import json
import subprocess
from unittest.mock import patch

import pytest

from src.workspace.errors import GitHubApiError, WorkspaceError
from src.workspace.git import (
    _run,
    check_existing_prs,
    check_gh_auth,
    create_draft_pr,
    current_branch,
    find_open_pr_for_branch,
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

    def test_find_open_pr_for_branch(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.returncode = 0
            mock.return_value.stdout = json.dumps([{"number": 42}])
            assert find_open_pr_for_branch("add-company/stripe") == 42
            assert "--head" in mock.call_args.args[0]
            assert "add-company/stripe" in mock.call_args.args[0]

    def test_find_open_pr_for_branch_returns_none(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.returncode = 0
            mock.return_value.stdout = "[]"
            assert find_open_pr_for_branch("add-company/stripe") is None

    def test_find_open_pr_for_branch_fails_closed_on_invalid_response(self):
        with patch("src.workspace.git._run") as mock:
            mock.return_value.stdout = "not json"
            try:
                find_open_pr_for_branch("add-company/stripe")
            except GitHubApiError as exc:
                assert "Could not parse" in exc.stderr
            else:
                raise AssertionError("invalid PR lookup output must fail closed")

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

    def test_sync_branch_with_main_merges_latest_main_without_rewriting_history(self, tmp_path):
        completed = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch("src.workspace.git._repo_cwd", return_value=tmp_path),
            patch("src.workspace.git.get_main_branch_remote", return_value="main"),
            patch("src.workspace.git._run", return_value=completed) as run,
        ):
            sync_branch_with_main("add-company/acme")

        run.assert_any_call(["git", "fetch", "origin"], cwd=tmp_path)
        run.assert_any_call(["git", "checkout", "add-company/acme"], cwd=tmp_path)
        run.assert_any_call(
            ["git", "merge", "--no-edit", "origin/main"],
            cwd=tmp_path,
            check=False,
        )
        assert not any("rebase" in call.args[0] for call in run.call_args_list)

    def test_sync_branch_with_main_commits_resolved_csv_conflicts(self, tmp_path):
        ok = subprocess.CompletedProcess([], 0, "", "")
        conflict = subprocess.CompletedProcess([], 1, "", "conflict")
        with (
            patch("src.workspace.git._repo_cwd", return_value=tmp_path),
            patch("src.workspace.git.get_main_branch_remote", return_value="main"),
            patch("src.workspace.git._run", side_effect=[ok, ok, conflict, ok]) as run,
            patch("src.workspace.git._resolve_csv_conflicts", return_value=True) as resolve,
        ):
            sync_branch_with_main("add-company/acme")

        resolve.assert_called_once_with(tmp_path)
        run.assert_called_with(["git", "commit", "--no-edit"], cwd=tmp_path, check=False)

    def test_sync_branch_with_main_aborts_non_csv_conflicts(self, tmp_path):
        ok = subprocess.CompletedProcess([], 0, "", "")
        conflict = subprocess.CompletedProcess([], 1, "", "conflict")
        with (
            patch("src.workspace.git._repo_cwd", return_value=tmp_path),
            patch("src.workspace.git.get_main_branch_remote", return_value="main"),
            patch("src.workspace.git._run", side_effect=[ok, ok, conflict, ok]) as run,
            patch("src.workspace.git._resolve_csv_conflicts", return_value=False),
            pytest.raises(WorkspaceError, match="manual resolution"),
        ):
            sync_branch_with_main("add-company/acme")

        run.assert_called_with(["git", "merge", "--abort"], cwd=tmp_path, check=False)
