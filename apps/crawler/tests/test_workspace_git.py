"""Tests for workspace git wrappers (mocked subprocess)."""

from __future__ import annotations

import json
import subprocess
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

from src.workspace.errors import GitHubApiError, WorkspaceError
from src.workspace.git import (
    _run,
    authenticate_managed_worktree,
    check_existing_prs,
    check_gh_auth,
    create_draft_pr,
    create_worktree,
    current_branch,
    delete_branch_at_expected_oid,
    delete_local_branch_at_expected_oid,
    delete_remote_branch_at_expected_oid,
    ensure_clone,
    find_open_pr_for_branch,
    pr_provenance,
    push_branch_at_expected_oid,
    remove_authenticated_worktree,
    sync_branch_with_main,
    validate_pr_attachment,
    verify_recorded_pr,
)

TEST_OID = "a" * 40


def _pr_details(**overrides) -> dict:
    details = {
        "number": 42,
        "state": "OPEN",
        "isDraft": True,
        "headRefName": "add-company/acme",
        "headRefOid": TEST_OID,
        "headRepository": {"name": "jobseek"},
        "headRepositoryOwner": {"login": "colophon-group"},
        "baseRefName": "main",
        "author": {"login": "resolver"},
        "closingIssuesReferences": [
            {
                "number": 7,
                "repository": {
                    "name": "jobseek",
                    "owner": {"login": "colophon-group"},
                },
            }
        ],
        "isCrossRepository": False,
    }
    details.update(overrides)
    return details


def _managed_repo_fixture(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    worktrees = tmp_path / "worktrees"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)
    subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )
    (repo / "tracked.txt").write_text("base\n")
    subprocess.run(["git", "-C", str(repo), "add", "tracked.txt"], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "base"], check=True)
    monkeypatch.setattr("src.workspace.git._MANAGED_REPO", repo)
    monkeypatch.setattr("src.workspace.git._WORKTREES_DIR", worktrees)
    return repo, worktrees


class TestFreshWorktreeCreation:
    def test_preexisting_unregistered_directory_is_preserved(self, tmp_path, monkeypatch):
        _, worktrees = _managed_repo_fixture(tmp_path, monkeypatch)
        target = worktrees / "acme"
        target.mkdir(parents=True)
        marker = target / "owned.txt"
        marker.write_text("outside ownership\n")

        with pytest.raises(WorkspaceError, match="not registered|already exists"):
            create_worktree("add-company/acme", target, "HEAD")

        assert marker.read_text() == "outside ownership\n"

    def test_preexisting_local_ref_is_preserved(self, tmp_path, monkeypatch):
        repo, worktrees = _managed_repo_fixture(tmp_path, monkeypatch)
        branch = "add-company/acme"
        subprocess.run(["git", "-C", str(repo), "branch", branch], check=True)
        before = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

        with pytest.raises(WorkspaceError, match="Local branch already exists"):
            create_worktree(branch, worktrees / "acme", "HEAD")

        after = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", branch],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert after == before

    def test_symlink_at_target_is_preserved(self, tmp_path, monkeypatch):
        _, worktrees = _managed_repo_fixture(tmp_path, monkeypatch)
        outside = tmp_path / "outside"
        outside.mkdir()
        target = worktrees / "acme"
        worktrees.mkdir()
        target.symlink_to(outside, target_is_directory=True)

        with pytest.raises(WorkspaceError, match="not a real directory"):
            create_worktree("add-company/acme", target, "HEAD")

        assert target.is_symlink()


class TestEnsureCloneInstalledMode:
    def test_fresh_clone_uses_managed_path_without_external_state(self, tmp_path, monkeypatch):
        managed = tmp_path / "managed" / "repo"
        repo_url = (tmp_path / "jobseek-fixture.git").as_uri()
        monkeypatch.setattr("src.workspace.git._MANAGED_REPO", managed)
        monkeypatch.setenv("WS_REPO_URL", repo_url)

        with patch("src.workspace.git.subprocess.run") as clone:
            result = ensure_clone()

        assert result == managed
        clone.assert_called_once_with(
            ["git", "clone", repo_url, str(managed)],
            check=True,
            capture_output=True,
            text=True,
        )
        assert (managed.parent / "repo.lock").is_file()

    def test_existing_clone_fetches_and_resets_inside_managed_path(self, tmp_path, monkeypatch):
        managed = tmp_path / "managed" / "repo"
        (managed / "apps" / "crawler" / "data").mkdir(parents=True)
        monkeypatch.setattr("src.workspace.git._MANAGED_REPO", managed)
        completed = subprocess.CompletedProcess([], 0, "", "")

        with (
            patch("src.workspace.git._run", return_value=completed) as run,
            patch("src.workspace.git.get_main_branch_remote", return_value="main"),
            patch("src.workspace.git.subprocess.run") as clone,
        ):
            result = ensure_clone()

        assert result == managed
        run.assert_any_call(["git", "fetch", "origin"], cwd=managed)
        run.assert_any_call(["git", "checkout", "main"], cwd=managed, check=False)
        run.assert_any_call(["git", "reset", "--hard", "origin/main"], cwd=managed)
        clone.assert_not_called()


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

    def test_run_reports_missing_github_cli_at_command_boundary(self):
        with (
            patch("src.workspace.git._repo_cwd", return_value=None),
            patch(
                "src.workspace.git.subprocess.run",
                side_effect=FileNotFoundError("gh"),
            ),
            pytest.raises(GitHubApiError, match="executable not found: gh") as exc_info,
        ):
            _run(["gh", "api", "repos/example/project"])

        assert exc_info.value.returncode == 127
        assert exc_info.value.cmd[0] == "gh"

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


class TestPullRequestProvenance:
    def _validate(self, details: dict) -> None:
        validate_pr_attachment(
            details,
            pr_number=42,
            branch="add-company/acme",
            base_ref="main",
            issue=7,
            slug="acme",
            authorized_actor="resolver",
        )

    def test_accepts_exact_same_repo_draft(self):
        self._validate(_pr_details())

    @pytest.mark.parametrize(
        ("details", "message"),
        [
            (
                _pr_details(
                    isCrossRepository=True,
                    headRepository={"name": "jobseek-fork"},
                    headRepositoryOwner={"login": "attacker"},
                ),
                "not owned",
            ),
            (_pr_details(headRefName="add-company/acme-lookalike"), "expected"),
            (_pr_details(baseRefName="release"), "targets"),
            (
                _pr_details(
                    closingIssuesReferences=[
                        {
                            "number": 8,
                            "repository": {
                                "name": "jobseek",
                                "owner": {"login": "colophon-group"},
                            },
                        }
                    ]
                ),
                "issue #7",
            ),
            (_pr_details(author={"login": "someone-else"}), "authenticated resolver actor"),
        ],
        ids=["fork", "lookalike-branch", "wrong-base", "wrong-issue", "wrong-author"],
    )
    def test_rejects_untrusted_resume_shapes(self, details, message):
        with pytest.raises(WorkspaceError, match=message):
            self._validate(details)

    @pytest.mark.parametrize("remote_oid", ["b" * 40, None], ids=["changed", "deleted"])
    def test_recorded_pr_rejects_changed_or_deleted_remote_ref(self, remote_oid):
        details = _pr_details()
        recorded = pr_provenance(details, issue=7, slug="acme")
        with (
            patch("src.workspace.git.get_pr_details_strict", return_value=details),
            patch("src.workspace.git.remote_branch_oid_strict", return_value=remote_oid),
            pytest.raises(WorkspaceError, match="changed or disappeared"),
        ):
            verify_recorded_pr(
                recorded,
                pr_number=42,
                branch="add-company/acme",
                issue=7,
                slug="acme",
            )

    @pytest.mark.parametrize("remote_oid", ["b" * 40, None], ids=["changed", "deleted"])
    def test_conditional_branch_delete_rejects_changed_or_deleted_ref(self, remote_oid):
        with (
            patch("src.workspace.git.remote_branch_oid_strict", return_value=remote_oid),
            patch("src.workspace.git._run") as run,
            pytest.raises(WorkspaceError, match="changed|disappeared"),
        ):
            delete_branch_at_expected_oid("add-company/acme", TEST_OID)
        run.assert_not_called()

    def test_conditional_branch_delete_uses_exact_force_with_lease(self):
        empty = subprocess.CompletedProcess([], 0, "", "")
        calls = [TEST_OID, None]
        with (
            patch("src.workspace.git.remote_branch_oid_strict", side_effect=calls),
            patch("src.workspace.git._run", return_value=empty) as run,
        ):
            delete_branch_at_expected_oid("add-company/acme", TEST_OID)

        assert any(
            call.args[0]
            == [
                "git",
                "push",
                f"--force-with-lease=refs/heads/add-company/acme:{TEST_OID}",
                "origin",
                ":refs/heads/add-company/acme",
            ]
            for call in run.call_args_list
        )

    def test_ambiguous_delete_retry_accepts_absence_only_after_journaled_attempt(self):
        with (
            patch("src.workspace.git.remote_branch_oid_strict", return_value=None),
            patch("src.workspace.git._run") as run,
        ):
            delete_remote_branch_at_expected_oid(
                "add-company/acme",
                TEST_OID,
                absent_is_success=True,
            )
        run.assert_not_called()

    def test_exact_push_leases_old_ref_and_publishes_captured_oid(self):
        old_oid = "b" * 40
        empty = subprocess.CompletedProcess([], 0, "", "")
        with (
            patch(
                "src.workspace.git.remote_branch_oid_strict",
                side_effect=[old_oid, TEST_OID],
            ),
            patch("src.workspace.git._run", return_value=empty) as run,
        ):
            push_branch_at_expected_oid("add-company/acme", TEST_OID, old_oid)

        run.assert_called_once_with(
            [
                "git",
                "push",
                "-u",
                f"--force-with-lease=refs/heads/add-company/acme:{old_oid}",
                "origin",
                f"{TEST_OID}:refs/heads/add-company/acme",
            ],
            retries=2,
        )

    def test_exact_push_rejects_pre_push_ref_swap(self):
        with (
            patch("src.workspace.git.remote_branch_oid_strict", return_value="c" * 40),
            patch("src.workspace.git._run") as run,
            pytest.raises(WorkspaceError, match="changed"),
        ):
            push_branch_at_expected_oid("add-company/acme", TEST_OID, "b" * 40)
        run.assert_not_called()


class TestAuthenticatedWorktreeRemoval:
    def test_rejects_symlink_victim_without_following_it(self, tmp_path, monkeypatch):
        root = tmp_path / "worktrees"
        victim = tmp_path / "victim"
        root.mkdir()
        victim.mkdir()
        (victim / "keep.txt").write_text("owned elsewhere")
        target = root / "acme"
        target.symlink_to(victim, target_is_directory=True)
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: root)

        with (
            patch(
                "src.workspace.git._registered_worktrees_strict",
                return_value={
                    target: {
                        "head": TEST_OID,
                        "branch": "refs/heads/add-company/acme",
                        "locked": False,
                    }
                },
            ),
            pytest.raises(WorkspaceError, match="not a real directory"),
        ):
            authenticate_managed_worktree(target, "add-company/acme", TEST_OID)
        assert (victim / "keep.txt").read_text() == "owned elsewhere"

    def test_rejects_unrelated_registered_branch_and_commit(self, tmp_path, monkeypatch):
        root = tmp_path / "worktrees"
        target = root / "acme"
        target.mkdir(parents=True)
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: root)
        with (
            patch(
                "src.workspace.git._registered_worktrees_strict",
                return_value={
                    target: {
                        "head": "c" * 40,
                        "branch": "refs/heads/add-company/other",
                        "locked": False,
                    }
                },
            ),
            pytest.raises(WorkspaceError, match="unexpected branch"),
        ):
            authenticate_managed_worktree(target, "add-company/acme", TEST_OID)

    def test_quarantines_exact_inode_and_prunes_registration(self, tmp_path, monkeypatch):
        root = tmp_path / "worktrees"
        target = root / "acme"
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("delete me")
        identity = target.stat()
        registration = {
            target: {
                "head": TEST_OID,
                "branch": "refs/heads/add-company/acme",
                "locked": False,
            }
        }
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: root)
        with (
            patch(
                "src.workspace.git._registered_worktrees_strict",
                side_effect=[registration, {}],
            ),
            patch("src.workspace.git._remove_worktree_admin_strict"),
            patch("src.workspace.git._run"),
        ):
            remove_authenticated_worktree(
                target,
                "add-company/acme",
                TEST_OID,
                expected_dev=identity.st_dev,
                expected_ino=identity.st_ino,
            )
        assert not target.exists()
        assert list(root.iterdir()) == []

    def test_retry_preserves_replacement_after_quarantine_crash(self, tmp_path, monkeypatch):
        root = tmp_path / "worktrees"
        target = root / "acme"
        target.mkdir(parents=True)
        (target / "owned.txt").write_text("original")
        identity = target.stat()
        registration = {
            target: {
                "head": TEST_OID,
                "branch": "refs/heads/add-company/acme",
                "locked": False,
            }
        }
        monkeypatch.setattr("src.workspace.git.worktrees_dir", lambda: root)
        with (
            patch("src.workspace.git._registered_worktrees_strict", return_value=registration),
            patch(
                "src.workspace.safe_cleanup.safe_rmtree_child",
                side_effect=RuntimeError("crash-after-quarantine"),
            ),
            pytest.raises(RuntimeError, match="crash-after-quarantine"),
        ):
            remove_authenticated_worktree(
                target,
                "add-company/acme",
                TEST_OID,
                expected_dev=identity.st_dev,
                expected_ino=identity.st_ino,
            )

        target.mkdir()
        (target / "keep.txt").write_text("replacement")
        with (
            patch("src.workspace.git._registered_worktrees_strict", return_value=registration),
            pytest.raises(WorkspaceError, match="both exist"),
        ):
            remove_authenticated_worktree(
                target,
                "add-company/acme",
                TEST_OID,
                expected_dev=identity.st_dev,
                expected_ino=identity.st_ino,
                absent_is_success=True,
            )
        assert (target / "keep.txt").read_text() == "replacement"

    def test_removes_only_matching_git_admin_directory(self, tmp_path, monkeypatch):
        from src.workspace.git import _remove_worktree_admin_strict

        repo = tmp_path / "repo"
        target = tmp_path / "worktrees" / "acme"
        quarantine = tmp_path / "worktrees" / ".quarantine"
        repo.mkdir()
        target.mkdir(parents=True)
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        admin_root = repo / ".git" / "worktrees"
        owned = admin_root / "acme"
        unrelated = admin_root / "other"
        owned.mkdir(parents=True)
        unrelated.mkdir()
        (owned / "gitdir").write_text(f"{target / '.git'}\n")
        (owned / "HEAD").write_text("ref: refs/heads/add-company/acme\n")
        (unrelated / "gitdir").write_text(f"{tmp_path / 'worktrees' / 'other' / '.git'}\n")
        (unrelated / "HEAD").write_text("ref: refs/heads/add-company/other\n")
        (unrelated / "keep.txt").write_text("preserve")
        monkeypatch.setattr("src.workspace.git._MANAGED_REPO", repo)

        _remove_worktree_admin_strict(
            target,
            quarantine,
            branch="add-company/acme",
            missing_ok=False,
        )
        assert not owned.exists()
        assert (unrelated / "keep.txt").read_text() == "preserve"


class TestExactLocalBranchDeletion:
    def test_repointed_branch_is_preserved(self, tmp_path, monkeypatch):
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init", "-q", str(repo)], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "Test"], check=True)
        subprocess.run(
            ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
            check=True,
        )
        (repo / "one.txt").write_text("one")
        subprocess.run(["git", "-C", str(repo), "add", "one.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "one"], check=True)
        first = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(["git", "-C", str(repo), "branch", "add-company/acme"], check=True)
        (repo / "two.txt").write_text("two")
        subprocess.run(["git", "-C", str(repo), "add", "two.txt"], check=True)
        subprocess.run(["git", "-C", str(repo), "commit", "-qm", "two"], check=True)
        second = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        subprocess.run(
            ["git", "-C", str(repo), "branch", "-f", "add-company/acme", second],
            check=True,
        )
        monkeypatch.setattr("src.workspace.git._MANAGED_REPO", repo)

        with pytest.raises(WorkspaceError, match="changed"):
            delete_local_branch_at_expected_oid("add-company/acme", first)
        current = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "add-company/acme"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()
        assert current == second


class TestCompanyLifecycleLock:
    def test_same_thread_lock_is_explicitly_reentrant(self, tmp_path, monkeypatch):
        from src.workspace.filelock import company_lifecycle_lock

        monkeypatch.setattr("src.workspace.filelock._LIFECYCLE_LOCKS_DIR", tmp_path)
        with company_lifecycle_lock("acme"), company_lifecycle_lock("acme"):
            pass

    def test_same_slug_attempts_are_serialized(self, tmp_path, monkeypatch):
        from src.workspace.filelock import company_lifecycle_lock

        monkeypatch.setattr("src.workspace.filelock._LIFECYCLE_LOCKS_DIR", tmp_path)
        first_acquired = Event()
        release_first = Event()
        second_attempting = Event()
        second_acquired = Event()

        def first():
            with company_lifecycle_lock("acme"):
                first_acquired.set()
                assert release_first.wait(timeout=2)

        def second():
            assert first_acquired.wait(timeout=2)
            second_attempting.set()
            with company_lifecycle_lock("acme"):
                second_acquired.set()

        with ThreadPoolExecutor(max_workers=2) as pool:
            first_future = pool.submit(first)
            second_future = pool.submit(second)
            assert second_attempting.wait(timeout=2)
            assert not second_acquired.wait(timeout=0.1)
            release_first.set()
            first_future.result(timeout=2)
            second_future.result(timeout=2)

        assert second_acquired.is_set()

    def test_stale_sidecar_does_not_block_recovery(self, tmp_path, monkeypatch):
        import hashlib

        from src.workspace.filelock import company_lifecycle_lock

        monkeypatch.setattr("src.workspace.filelock._LIFECYCLE_LOCKS_DIR", tmp_path)
        digest = hashlib.sha256(b"acme").hexdigest()
        stale = tmp_path / f"company-{digest}.lock"
        stale.parent.mkdir(parents=True, exist_ok=True)
        stale.write_text("stale-owner\n")

        with company_lifecycle_lock("acme"):
            assert stale.exists()
