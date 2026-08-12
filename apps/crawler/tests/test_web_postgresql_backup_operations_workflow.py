from __future__ import annotations

import re
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
    assert 'deploy_sha="$DISPATCH_SHA"' in authorize
    assert "deploy_paths=(" not in authorize
    assert "workflow_dispatch:" in deploy
    assert "  push:" not in deploy
    assert "fetch-depth: 0" in deploy
    assert 'echo "deploy_sha=$CHECKOUT_SHA"' in deploy
    assert "DEPLOY_SHA: ${{ steps.deployment-revision.outputs.deploy_sha }}" in deploy
    for output in (
        "data_backup_sha256",
        "image_protector_sha256",
        "operations_sha256",
        "retirement_migration_sha256",
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
    lock = operate.index("exec 8>/run/jobseek-backup-deployment.lock")
    remote_umask = operate.index("umask 077;", operate.index('ssh "${ssh_options[@]}"'))
    attest = operate.index("operations_hash=", lock)
    execute = operate.index("exec /usr/local/sbin/jobseek-web-postgresql-operations", attest)
    assert remote_umask < lock < attest < execute
    assert "flock -w 60 8" in operate[lock:attest]
    assert "export JOBSEEK_BACKUP_DEPLOYMENT_LOCK_FD=8" in operate[attest:execute]
    installer_lock = installer.index("exec 8>/run/jobseek-backup-deployment.lock")
    assert installer.index("umask 077") < installer_lock
    assert "/usr/local/sbin/jobseek-web-postgresql-operations" in installer
    assert '"/var/lib/jobseek-backup/${SERVICE}-deployed-sha"' in installer
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
    assert "apps/web/drizzle/0086_drop_supabase_job_posting.sql" in transport


def test_deploy_revision_is_the_exact_explicitly_dispatched_sha() -> None:
    deploy = DEPLOY_WORKFLOW_PATH.read_text(encoding="utf-8")
    operate = _workflow()

    assert 'test "$(git rev-parse HEAD)" = "$CHECKOUT_SHA"' in deploy
    assert 'echo "deploy_sha=$CHECKOUT_SHA"' in deploy
    assert 'test "$(git rev-parse HEAD)" = "$DISPATCH_SHA"' in operate
    assert 'deploy_sha="$DISPATCH_SHA"' in operate
    assert "git log -1 --format=%H" not in deploy
    assert "git log -1 --format=%H" not in operate


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
