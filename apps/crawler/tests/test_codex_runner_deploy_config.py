"""Behavioral shell tests for Codex runner runtime-config deployment gates."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "scripts" / "deploy-codex-runner-host.sh"
GOVERNOR_SERVICE = ROOT / "deploy" / "systemd" / "jobseek-codex-governor.service"
GOVERNOR_ENV_EXAMPLE = ROOT / "deploy" / "systemd" / "jobseek-codex-governor.env.example"
LEAK_MARKER = "not-a-real-password-7f93"

_REQUIRE_CONFIG = r"""
set -euo pipefail
export JOBSEEK_CODEX_CONFIG_DIR="$1"
source "$2"
as_runner() { "$@"; }
require_runtime_config
"""

_UPDATE_REPO = r"""
set -euo pipefail
source "$1"
REPO_DIR="$2"
REPO_URL="$3"
BRANCH=main
EXPECTED_SHA="$4"
as_runner() { "$@"; }
update_repo
"""


def _git(*args: str, cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _commit_and_push(seed: Path, content: str, message: str) -> str:
    (seed / "tracked.txt").write_text(content, encoding="utf-8")
    _git("add", "tracked.txt", cwd=seed)
    _git("commit", "-m", message, cwd=seed)
    _git("push", "origin", "main", cwd=seed)
    return _git("rev-parse", "HEAD", cwd=seed).stdout.strip()


def _deployment_fixture(tmp_path: Path) -> tuple[Path, Path, str]:
    origin = tmp_path / "origin.git"
    seed = tmp_path / "seed"
    repo = tmp_path / "repo"
    _git("init", "--bare", str(origin), cwd=tmp_path)
    _git("init", str(seed), cwd=tmp_path)
    _git("config", "user.name", "Codex deploy test", cwd=seed)
    _git("config", "user.email", "codex-deploy-test@example.invalid", cwd=seed)
    _git("checkout", "-b", "main", cwd=seed)
    _git("remote", "add", "origin", str(origin), cwd=seed)
    first = _commit_and_push(seed, "first\n", "first")
    _git("clone", "--branch", "main", str(origin), str(repo), cwd=tmp_path)
    return origin, seed, first


def _update_repo(repo: Path, origin: Path, expected_sha: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            _UPDATE_REPO,
            "bash",
            str(DEPLOY),
            str(repo),
            str(origin),
            expected_sha,
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def test_governor_lock_and_home_write_scope_cover_managed_worktree_reconciliation() -> None:
    service = GOVERNOR_SERVICE.read_text()
    deploy = DEPLOY.read_text()

    assert "ExecStart=/usr/bin/flock -n /srv/jobseek-codex/state/codex-runner.lock" in service
    assert "ReadWritePaths=/srv/jobseek-codex /home/codex-runner" in service
    assert 'flock -w "${LOCK_TIMEOUT_S}" 9' in deploy
    assert '"${REPO_DIR}/scripts/codex-worktree-reconcile.py" --apply' in deploy


def test_update_repo_detaches_deployment_checkout_from_mutable_main_ref(tmp_path: Path) -> None:
    origin, seed, first = _deployment_fixture(tmp_path)
    repo = tmp_path / "repo"

    first_deploy = _update_repo(repo, origin, first)
    assert first_deploy.returncode == 0, first_deploy.stderr
    assert _git("rev-parse", "HEAD", cwd=repo).stdout.strip() == first
    symbolic = subprocess.run(
        ["git", "symbolic-ref", "-q", "HEAD"],
        cwd=repo,
        capture_output=True,
        text=True,
        check=False,
    )
    assert symbolic.returncode != 0

    second = _commit_and_push(seed, "second\n", "second")
    _git("fetch", "origin", "main", cwd=repo)
    _git("update-ref", "refs/heads/main", second, cwd=repo)
    assert _git("status", "--porcelain", cwd=repo).stdout == ""

    second_deploy = _update_repo(repo, origin, second)
    assert second_deploy.returncode == 0, second_deploy.stderr
    assert _git("rev-parse", "HEAD", cwd=repo).stdout.strip() == second
    assert _git("status", "--porcelain", cwd=repo).stdout == ""


def test_update_repo_still_rejects_genuine_tracked_changes(tmp_path: Path) -> None:
    origin, _seed, first = _deployment_fixture(tmp_path)
    repo = tmp_path / "repo"

    deployed = _update_repo(repo, origin, first)
    assert deployed.returncode == 0, deployed.stderr
    (repo / "tracked.txt").write_text("operator edit\n", encoding="utf-8")

    blocked = _update_repo(repo, origin, first)
    assert blocked.returncode != 0
    assert "has tracked local changes; refusing to overwrite" in blocked.stderr


def test_governor_example_bounds_all_retained_codex_sessions() -> None:
    example = GOVERNOR_ENV_EXAMPLE.read_text()

    assert "JOBSEEK_CODEX_MAX_RETAINED_SESSION_FILES=500" in example
    assert "JOBSEEK_CODEX_MAX_RETAINED_SESSION_GIB=2" in example
    assert "JOBSEEK_CODEX_MAX_UNLINKED_SESSION_AGE_DAYS=7" in example


def _validate(tmp_path: Path, content: str) -> subprocess.CompletedProcess[str]:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "governor.env").write_text("# test governor config\n", encoding="utf-8")
    (config_dir / "labeller.env").write_text(content, encoding="utf-8")
    return subprocess.run(
        ["bash", "-c", _REQUIRE_CONFIG, "bash", str(config_dir), str(DEPLOY)],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.parametrize(
    "content",
    [
        f"LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n",
        f"# DSN only\n\nLOCAL_DATABASE_URL='postgresql://reader:{LEAK_MARKER}@db/crawler'\n",
        f'  # comment\nLOCAL_DATABASE_URL="postgres://reader:{LEAK_MARKER}@db/crawler"\n',
    ],
)
def test_require_runtime_config_accepts_one_nonempty_postgresql_dsn(
    tmp_path: Path, content: str
) -> None:
    result = _validate(tmp_path, content)

    assert result.returncode == 0, result.stderr
    assert LEAK_MARKER not in result.stdout
    assert LEAK_MARKER not in result.stderr


@pytest.mark.parametrize(
    "content",
    [
        "",
        "# no assignment\n",
        "LOCAL_DATABASE_URL=\n",
        "LOCAL_DATABASE_URL=   \n",
        "LOCAL_DATABASE_URL=''\n",
        'LOCAL_DATABASE_URL="   "\n',
        f'LOCAL_DATABASE_URL="postgresql://reader:{LEAK_MARKER}@db/crawler\n',
        "LOCAL_DATABASE_URL=https://db.invalid/crawler\n",
        "LOCAL_DATABASE_URL=postgresql://\n",
        "LOCAL_DATABASE_URL=postgresql://db name/crawler\n",
        "OTHER_KEY=value\n",
        f"export LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n",
        f" LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n",
        (
            f"LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n"
            "CRAWLER_DB_POOL_MAX=2\n"
        ),
        (
            f"LOCAL_DATABASE_URL=postgresql://reader:{LEAK_MARKER}@db/crawler\n"
            "LOCAL_DATABASE_URL=postgresql://reader:second-password@db/crawler\n"
        ),
    ],
)
def test_require_runtime_config_rejects_empty_duplicate_or_extra_assignments(
    tmp_path: Path, content: str
) -> None:
    result = _validate(tmp_path, content)

    assert result.returncode != 0
    assert LEAK_MARKER not in result.stdout
    assert LEAK_MARKER not in result.stderr
    assert "invalid labeller.env" in result.stderr
