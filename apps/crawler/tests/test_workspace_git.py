"""Tests for workspace git wrappers (mocked subprocess)."""

from __future__ import annotations

import json
from unittest.mock import patch

from src.workspace.git import (
    check_existing_prs,
    check_gh_auth,
    current_branch,
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
