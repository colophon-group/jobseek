from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]


def test_typesense_credential_rotation_is_probed_atomic_and_rollback_safe() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    rotation = (ROOT / "deploy/backups/typesense/credential-rotation.sh").read_text(
        encoding="utf-8"
    )

    stage = rotation.index('typesense_rotation_candidate_env="$typesense_rotation_candidate_root')
    assert '"http://127.0.0.1:8108/stats.json"' in rotation
    authorize = rotation.index("typesense_rotation_authorize_candidate", stage)
    arm = rotation.index("typesense_rotation_pending=1", authorize)
    quiesce = rotation.index("typesense_rotation_disable_fail_safe", arm)
    unlock = rotation.index('"$typesense_rotation_flock_command" -u', quiesce)
    smoke = rotation.index('"$typesense_rotation_backup_command" typesense', unlock)
    fresh_status = rotation.index("typesense_rotation_validate_fresh_status", smoke)
    reacquire = rotation.index('"$typesense_rotation_flock_command" -w', fresh_status)
    commit = rotation.index('mv -f "$typesense_rotation_candidate_env"', reacquire)
    restore_timer = rotation.index("typesense_rotation_restore_timer_state", commit)

    assert (
        stage
        < authorize
        < arm
        < quiesce
        < unlock
        < smoke
        < fresh_status
        < reacquire
        < commit
        < restore_timer
    )
    assert "typesense_rotation_rollback" in installer
    assert "typesense_rotation_atomic_restore" in rotation
    assert "credential rollback failed hard" in rotation
    assert "|| true" not in rotation
    assert 'source "$REPO_ROOT/deploy/backups/typesense/credential-rotation.sh"' in installer
    prepare_call = installer.index("typesense_rotation_prepare")
    assert prepare_call < installer.index(
        '"$REPO_ROOT/scripts/jobseek-data-backup.py"', prepare_call
    )
    smoke_call = installer.index("typesense_rotation_smoke_and_commit")
    timer_gate = installer.index(
        'systemctl is-active --quiet "jobseek-${SERVICE}-backup.timer"', smoke_call
    )
    marker = installer.index('>"/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp"')
    finalize = installer.index("typesense_rotation_finalize", marker)
    assert smoke_call < timer_gate < marker < finalize


def test_backup_deploy_requires_an_enabled_healthy_timer() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    receiver = (ROOT / "deploy/backups/install-host-from-stdin.sh").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy-data-backups.yml").read_text(encoding="utf-8")

    assert 'systemctl is-enabled --quiet "jobseek-${SERVICE}-backup.timer"' in installer
    assert 'systemctl is-active --quiet "jobseek-${SERVICE}-backup.timer"' in installer
    assert 'systemctl is-failed --quiet "jobseek-${SERVICE}-backup.service"' in installer
    assert "Typesense backup evidence is failed or stale" in installer
    assert 'if [[ "$service" != "web-postgresql" ]]' in receiver
    assert 'installer_args=(--start-timer "$service")' in receiver
    assert 'bash deploy/backups/install-host.sh "${installer_args[@]}"' in receiver
    assert (
        'systemctl is-enabled "jobseek-${JOBSEEK_BACKUP_SERVICE}-backup.timer" || true'
        not in workflow
    )
    assert (
        'systemctl is-active "jobseek-${JOBSEEK_BACKUP_SERVICE}-backup.timer" || true'
        not in workflow
    )


def test_failed_or_stale_typesense_backup_is_repaired_before_deploy_marker() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    rotation = (ROOT / "deploy/backups/typesense/credential-rotation.sh").read_text(
        encoding="utf-8"
    )

    install_script = installer.index('"$REPO_ROOT/scripts/jobseek-data-backup.py"')
    reload_units = installer.index("systemctl daemon-reload", install_script)
    repair_condition = installer.index("typesense_backup_requires_repair; then", reload_units)
    repair = installer.index("typesense_rotation_repair_failed_backup", repair_condition)
    final_health = installer.index(
        'systemctl is-failed --quiet "jobseek-${SERVICE}-backup.service"', repair
    )
    marker = installer.index('>"/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp"', repair)

    assert install_script < reload_units < repair_condition < repair < final_health < marker
    assert "typesense_backup_status_is_fresh" in installer
    assert "typesense_backup_timer_expected_enabled &&" in installer
    assert '[[ "$TIMER_ACTION" != disable ]]' in installer
    assert "0 <= age <= 36 * 60 * 60" in installer

    repair_function = rotation[
        rotation.index("typesense_rotation_repair_failed_backup()") : rotation.index(
            "typesense_rotation_smoke_and_commit()"
        )
    ]
    assert '"$typesense_rotation_systemctl_command" start \\' in repair_function
    assert "typesense_rotation_disable_fail_safe" in repair_function
    assert '"$typesense_rotation_flock_command" -u' in repair_function
    assert '"$typesense_rotation_flock_command" -w' in repair_function
    assert "typesense_rotation_validate_fresh_status" in repair_function
    assert "typesense_rotation_restore_timer_state" in repair_function


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
