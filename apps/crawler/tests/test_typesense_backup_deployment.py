from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_typesense_credential_rotation_is_probed_atomic_and_rollback_safe() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")

    probe = installer.index('"http://127.0.0.1:8108/stats.json"')
    script_install = installer.index('"$REPO_ROOT/scripts/jobseek-data-backup.py"', probe)
    replacement = installer.index("os.replace(temporary, path)")
    smoke = installer.index("systemctl start jobseek-typesense-backup.service")
    timer_gate = installer.index(
        'systemctl is-active --quiet "jobseek-${SERVICE}-backup.timer"', smoke
    )
    deployed_revision = installer.index("mv /var/lib/jobseek-backup/deployed-sha.tmp", smoke)
    commit = installer.index("typesense_rotation_pending=0", smoke)

    assert probe < script_install < replacement < smoke < timer_gate < deployed_revision < commit
    assert "rollback_typesense_credential" in installer
    rollback_install = (
        "install -o root -g root -m 0600 \\\n"
        '    "$typesense_previous_env" /etc/jobseek-backup/typesense.env'
    )
    assert rollback_install in installer
    assert "credential-change backup smoke did not succeed" in installer


def test_backup_deploy_requires_an_enabled_healthy_timer() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy-data-backups.yml").read_text(encoding="utf-8")

    assert 'systemctl is-enabled --quiet "jobseek-${SERVICE}-backup.timer"' in installer
    assert 'systemctl is-active --quiet "jobseek-${SERVICE}-backup.timer"' in installer
    assert 'systemctl is-failed --quiet "jobseek-${SERVICE}-backup.service"' in installer
    assert "Typesense backup evidence is failed or stale" in installer
    assert (
        'systemctl is-enabled "jobseek-${JOBSEEK_BACKUP_SERVICE}-backup.timer" || true'
        not in workflow
    )
    assert (
        'systemctl is-active "jobseek-${JOBSEEK_BACKUP_SERVICE}-backup.timer" || true'
        not in workflow
    )


def test_typesense_restore_drill_is_isolated_and_self_cleaning() -> None:
    drill = (ROOT / "deploy/backups/typesense/restore-drill.sh").read_text(encoding="utf-8")

    assert "production container name 'typesense' exists" in drill
    assert "--publish" in drill
    assert '"127.0.0.1:$PORT:8108"' in drill
    assert "--network" in drill
    assert "  bridge" in drill
    assert "exact_alias_inventory" in drill
    assert "exact_collection_counts" in drill
    assert "representative_search" in drill
    assert "ephemeral_write_read_delete" in drill
    assert 'docker rm --force "$container"' in drill
    assert 'rm -rf -- "$work_dir"' in drill
