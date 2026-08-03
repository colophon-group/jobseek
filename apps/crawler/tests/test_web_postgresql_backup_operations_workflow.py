from __future__ import annotations

import hashlib
import re
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
WORKFLOW_PATH = ROOT / ".github/workflows/operate-web-postgresql-backup.yml"
DEPLOY_WORKFLOW_PATH = ROOT / ".github/workflows/deploy-data-backups.yml"
RUNBOOK_PATH = ROOT / "docs/19-data-backup-recovery.md"


def _workflow() -> str:
    return WORKFLOW_PATH.read_text(encoding="utf-8")


def _job(workflow: str, name: str) -> str:
    match = re.search(
        rf"^  {re.escape(name)}:\n(?P<body>.*?)(?=^  [a-z][a-z0-9_]*:\n|\Z)",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing workflow job {name}"
    return match.group("body")


def _deploy_trigger_paths(workflow: str) -> tuple[str, ...]:
    match = re.search(
        r"^    paths:\n(?P<body>(?:      - ['\"].+['\"]\n)+)",
        workflow,
        flags=re.MULTILINE,
    )
    assert match is not None, "missing deploy trigger path list"
    return tuple(
        line.split("- ", 1)[1].strip().strip("'\"") for line in match.group("body").splitlines()
    )


def _authorization_deploy_paths(workflow: str) -> tuple[str, ...]:
    match = re.search(
        r"^          deploy_paths=\(\n(?P<body>.*?)^          \)$",
        workflow,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, "missing authorization deploy path list"
    return tuple(line.strip()[1:-1] for line in match.group("body").splitlines() if line.strip())


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", *arguments],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _latest_deploy_sha(repository: Path, dispatch_sha: str, paths: tuple[str, ...]) -> str:
    return _git(repository, "log", "-1", "--format=%H", dispatch_sha, "--", *paths)


def test_operations_require_owner_review_main_and_exact_confirmation() -> None:
    workflow = _workflow()
    preauthorize = _job(workflow, "preauthorize")
    authorize = _job(workflow, "authorize")
    operate = _job(workflow, "operate")

    assert "workflow_dispatch:" in workflow
    assert "schedule:" not in workflow
    assert "push:" not in workflow
    assert "environment:" not in preauthorize
    assert "needs: preauthorize" in authorize
    assert "environment: production-backup-operations" in authorize
    for job in (preauthorize, authorize, operate):
        assert 'test "$DISPATCH_ACTOR" = viktor-shcherb' in job
        assert 'test "$DISPATCH_TRIGGERING_ACTOR" = viktor-shcherb' in job
    assert 'test "$DISPATCH_REF" = refs/heads/main' in preauthorize
    assert 'test "$DISPATCH_REF" = refs/heads/main' in authorize
    assert "github.triggering_actor == 'viktor-shcherb'" in authorize
    assert "github.triggering_actor == 'viktor-shcherb'" in operate
    for mode, confirmation in (
        ("verify", "VERIFY-WEB-POSTGRESQL"),
        ("backup", "RUN-WEB-POSTGRESQL-BACKUP"),
        ("restore", "RUN-WEB-POSTGRESQL-RESTORE-DRILL"),
        ("enable-timer", "ENABLE-WEB-POSTGRESQL-TIMER"),
    ):
        assert f"- {mode}" in workflow
        assert f"expected={confirmation}" in authorize


def test_secret_bearing_job_uses_strict_native_openssh_after_authorization() -> None:
    workflow = _workflow()
    operate = _job(workflow, "operate")

    assert len(workflow.splitlines()) < 260
    assert "group: hetzner-data-backup-production-sync" in workflow
    assert "cancel-in-progress: false" in workflow
    assert "needs: [preauthorize, authorize]" in operate
    assert "environment: production" in operate
    assert "secrets.HETZNER_TYPESENSE_HOST" in operate
    assert "secrets.HETZNER_SSH_KEY" in operate
    assert "secrets.HETZNER_TYPESENSE_KNOWN_HOSTS" in operate
    assert "StrictHostKeyChecking=yes" in operate
    assert "UserKnownHostsFile=$ssh_root/known_hosts" in operate
    assert "GlobalKnownHostsFile=/dev/null" in operate
    assert "PasswordAuthentication=no" in operate
    assert 'ssh-keygen -F "$TARGET_HOST"' in operate
    assert "timeout --foreground --signal=TERM --kill-after=30s 165m" in operate
    assert "2h45m" not in operate
    assert "appleboy/ssh-action" not in workflow
    assert "ssh-keyscan" not in workflow
    assert "secrets.DATABASE_URL" not in workflow
    assert "secrets.RESTIC" not in workflow

    uses = re.findall(r"^\s+- uses: ([^\s#]+)", workflow, flags=re.MULTILINE)
    assert uses == ["actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"]


def test_dispatch_is_bound_to_reviewed_revision_and_installed_helper() -> None:
    workflow = _workflow()
    authorize = _job(workflow, "authorize")
    operate = _job(workflow, "operate")
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    deploy = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")

    assert 'test "$(git rev-parse HEAD)" = "$DISPATCH_SHA"' in authorize
    assert "fetch-depth: 0" in authorize
    assert 'git log -1 --format=%H "$DISPATCH_SHA" -- "${deploy_paths[@]}"' in authorize
    assert 'git merge-base --is-ancestor "$deploy_sha" "$DISPATCH_SHA"' in authorize
    assert _authorization_deploy_paths(workflow) == _deploy_trigger_paths(deploy)
    assert _authorization_deploy_paths(deploy) == _deploy_trigger_paths(deploy)
    assert "workflow_dispatch:" not in deploy
    assert "fetch-depth: 0" in deploy
    assert 'git log -1 --format=%H "$CHECKOUT_SHA" -- "${deploy_paths[@]}"' in deploy
    assert "DEPLOY_SHA: ${{ steps.deployment-revision.outputs.deploy_sha }}" in deploy
    for output in (
        "data_backup_sha256",
        "operations_sha256",
        "restore_drill_sha256",
        "service_sha256",
        "timer_sha256",
    ):
        assert f"{output}:" in authorize
        assert f"--expected-{output.replace('_', '-')}" in operate
    assert "/usr/local/sbin/jobseek-web-postgresql-operations" in operate
    assert "test ! -L /usr/local/sbin/jobseek-web-postgresql-operations" in operate
    assert "stat -c '%U:%G:%a' /usr/local/sbin/jobseek-web-postgresql-operations" in operate
    assert "test \\\"\\${operations_hash%% *}\\\" = '$EXPECTED_OPERATIONS_SHA256'" in operate
    assert "exec /usr/local/sbin/jobseek-web-postgresql-operations" in operate
    assert "/usr/local/sbin/jobseek-web-postgresql-operations" in installer
    assert '"/var/lib/jobseek-backup/${SERVICE}-deployed-sha"' in installer
    assert "'.github/workflows/operate-web-postgresql-backup.yml'" in deploy
    assert "py_compile deploy/backups/web-postgresql/operations.py" in deploy


def test_backup_deploy_uses_strict_native_transport_and_stdin_credentials() -> None:
    deploy = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")
    transport = (ROOT / "deploy/backups/deploy-remote.sh").read_text(encoding="utf-8")
    receiver = (ROOT / "deploy/backups/install-host-from-stdin.sh").read_text(encoding="utf-8")

    assert "appleboy/" not in deploy
    assert "timeout-minutes: 360" in deploy
    assert "secrets.HETZNER_BACKUP_KNOWN_HOSTS" in deploy
    assert 'bash deploy/backups/deploy-remote.sh "$BACKUP_SERVICE" "$DEPLOY_SHA"' in deploy
    assert "StrictHostKeyChecking=yes" in transport
    assert "timeout --foreground --signal=TERM --kill-after=30s 330m" in transport
    assert " 6h" not in transport
    assert "UserKnownHostsFile=$ssh_root/known_hosts" in transport
    assert "ssh-keyscan" not in transport
    assert "install-host-from-stdin.sh" in transport
    assert "JOBSEEK_WEB_DATABASE_URL" not in " ".join(
        line for line in transport.splitlines() if "ssh " in line
    )
    assert "JOBSEEK_TYPESENSE_BACKUP_KEY_FILE" in receiver
    assert "JOBSEEK_WEB_DATABASE_URL_FILE" in receiver
    assert "exec 8>/run/jobseek-backup-deployment.lock" in (
        ROOT / "deploy/backups/install-host.sh"
    ).read_text(encoding="utf-8")


def test_deploy_revision_ignores_unrelated_commits_but_tracks_relevant_changes(
    tmp_path: Path,
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Backup Test")
    _git(repository, "config", "user.email", "backup-test@example.invalid")
    artifact = repository / "deploy/backups/web-postgresql/operations.py"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("version one\n", encoding="utf-8")
    _git(repository, "add", ".")
    _git(repository, "commit", "-qm", "Add backup artifact")
    deployed_sha = _git(repository, "rev-parse", "HEAD")
    deployed_artifact_hash = hashlib.sha256(artifact.read_bytes()).hexdigest()

    (repository / "README.md").write_text("unrelated\n", encoding="utf-8")
    _git(repository, "add", "README.md")
    _git(repository, "commit", "-qm", "Unrelated main change")
    unrelated_dispatch_sha = _git(repository, "rev-parse", "HEAD")
    deploy_paths = _deploy_trigger_paths(DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8"))

    assert _latest_deploy_sha(repository, unrelated_dispatch_sha, deploy_paths) == deployed_sha

    artifact.write_text("version two\n", encoding="utf-8")
    _git(repository, "add", str(artifact.relative_to(repository)))
    _git(repository, "commit", "-qm", "Change backup artifact")
    relevant_dispatch_sha = _git(repository, "rev-parse", "HEAD")

    assert _latest_deploy_sha(repository, relevant_dispatch_sha, deploy_paths) == (
        relevant_dispatch_sha
    )
    assert relevant_dispatch_sha != deployed_sha
    assert hashlib.sha256(artifact.read_bytes()).hexdigest() != deployed_artifact_hash


def test_only_the_web_installer_leg_can_advance_the_web_revision_marker() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    operations = (ROOT / "deploy/backups/web-postgresql/operations.py").read_text(encoding="utf-8")
    marker_block_match = re.search(
        r'^if \[\[ -n "\$\{JOBSEEK_BACKUP_DEPLOY_SHA:-\}" \]\]; then\n'
        r"(?P<body>.*?)^fi$",
        installer,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert marker_block_match is not None
    marker_block = marker_block_match.group("body")

    assert marker_block_match.start() > installer.index('if [[ "$TIMER_ACTION" != "disable" ]]')
    assert marker_block.count("${SERVICE}-deployed-sha.tmp") == 4
    assert marker_block.count('${SERVICE}-deployed-sha"') == 1
    assert 'chown root:root "/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp"' in (marker_block)
    assert "web-postgresql-deployed-sha" not in installer
    assert (
        'DEPLOYED_SHA_PATH = Path("/var/lib/jobseek-backup/web-postgresql-deployed-sha")'
        in operations
    )


def test_runbook_documents_the_protected_activation_sequence() -> None:
    runbook = RUNBOOK_PATH.read_text(encoding="utf-8")

    assert ".github/workflows/operate-web-postgresql-backup.yml" in runbook
    assert "production-backup-operations" in runbook
    assert "HETZNER_TYPESENSE_KNOWN_HOSTS" in runbook
    assert "/var/lib/jobseek-backup/web-postgresql-deployed-sha" in runbook
    assert "Run the modes in that order for first activation" in runbook
    for confirmation in (
        "VERIFY-WEB-POSTGRESQL",
        "RUN-WEB-POSTGRESQL-BACKUP",
        "RUN-WEB-POSTGRESQL-RESTORE-DRILL",
        "ENABLE-WEB-POSTGRESQL-TIMER",
    ):
        assert confirmation in runbook
