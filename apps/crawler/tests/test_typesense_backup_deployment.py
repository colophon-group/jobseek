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


def test_failed_typesense_service_uses_reachable_direct_smoke_before_marker() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    rotation = (ROOT / "deploy/backups/typesense/credential-rotation.sh").read_text(
        encoding="utf-8"
    )

    install_script = installer.index('"$REPO_ROOT/scripts/jobseek-data-backup.py"')
    reload_units = installer.index("systemctl daemon-reload", install_script)
    smoke = installer.index("typesense_rotation_smoke_and_commit", reload_units)
    final_health = installer.index(
        'systemctl is-failed --quiet "jobseek-${SERVICE}-backup.service"', smoke
    )
    marker = installer.index('>"/var/lib/jobseek-backup/${SERVICE}-deployed-sha.tmp"', smoke)

    assert install_script < reload_units < smoke < final_health < marker
    assert "typesense_rotation_repair_failed_backup" not in installer
    assert "typesense_rotation_repair_failed_backup" not in rotation
    smoke_function = rotation[rotation.index("typesense_rotation_smoke_and_commit()") :]
    assert "typesense_rotation_disable_fail_safe" in smoke_function
    assert '"$typesense_rotation_flock_command" -u' in smoke_function
    assert '"$typesense_rotation_flock_command" -w' in smoke_function
    assert "typesense_rotation_validate_fresh_status" in smoke_function
    assert "typesense_rotation_restore_timer_state" in smoke_function


def test_typesense_rollout_is_manual_revision_bound_and_freshly_smoked() -> None:
    workflow = (ROOT / ".github/workflows/deploy-data-backups.yml").read_text(encoding="utf-8")
    host_workflow = (ROOT / ".github/workflows/deploy-typesense-host.yml").read_text(
        encoding="utf-8"
    )
    backup_installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    receiver = (ROOT / "deploy/backups/install-host-from-stdin.sh").read_text(encoding="utf-8")
    host_installer = (ROOT / "deploy/typesense-host/install-host.sh").read_text(encoding="utf-8")
    runbook = (ROOT / "docs/16-hetzner-maintenance.md").read_text(encoding="utf-8")

    assert "workflow_dispatch:" in workflow
    assert "  push:" not in workflow
    assert 'test "$DISPATCH_REF" = refs/heads/main' in workflow
    assert 'test "$DISPATCH_REF" = refs/heads/main' in host_workflow
    assert "command_timeout: 40m" in host_workflow
    assert 'echo "deploy_sha=$CHECKOUT_SHA"' in workflow
    assert "backup-contract-pending" in host_installer
    assert "Typesense last-success backup evidence is missing or stale" in host_installer
    assert 'status.get("success") is not True' not in host_installer
    assert "acquire_typesense_backup_locks" in host_installer
    assert "/run/jobseek-backup-deployment.lock" in host_installer
    assert "/run/jobseek-data-backup-typesense.lock" in host_installer
    assert "quiesce_typesense_backup" in host_installer
    assert "recover_typesense_container" in host_installer
    assert "wait_for_typesense || true" not in host_installer
    assert "rollback_typesense_transaction" in host_installer
    assert "sync -f" in host_installer
    assert 'container.get("RestartCount")' in host_installer
    assert 'state.get("OOMKilled") is False' in host_installer
    assert "write_cloudflared_deployed_revision" in host_installer
    assert "cloudflared-deployed-sha" in host_workflow
    assert runbook.count("gh run watch") >= 3
    assert 'test "$(cat /var/lib/jobseek-typesense-host/deployed-sha)"' in backup_installer
    assert 'test "$(cat /var/lib/jobseek-typesense-host/backup-contract-pending)"' in (
        backup_installer
    )
    assert 'if [[ "$SERVICE" == "typesense" ]]; then\n  typesense_rotation_smoke_and_commit' in (
        backup_installer
    )
    assert 'installer_args=(--start-timer "$service")' in receiver
    assert "rm -f /var/lib/jobseek-typesense-host/backup-contract-pending" in backup_installer


def test_typesense_backup_requires_persistent_staging_and_bounded_memory_policy() -> None:
    backup_installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    host_installer = (ROOT / "deploy/typesense-host/install-host.sh").read_text(encoding="utf-8")
    service = (ROOT / "deploy/systemd/jobseek-typesense-backup.service").read_text(encoding="utf-8")
    workflow = (ROOT / ".github/workflows/deploy-typesense-host.yml").read_text(encoding="utf-8")

    verifier = (ROOT / "scripts/verify-typesense-snapshot-mount.py").read_text(encoding="utf-8")
    for artifact in (backup_installer, host_installer, service):
        assert "/mnt/jobseek-typesense-backup" in artifact
        assert "8589934592" in artifact
    assert "/mnt/jobseek-typesense-backup" in verifier
    assert "21474836480" in service
    assert "4294967296" in service
    assert "UUID=" in verifier
    assert "nodev,nosuid,noexec" in verifier
    assert '--memory "$TYPESENSE_MEMORY_LIMIT"' in host_installer
    assert '--memory-reservation "$TYPESENSE_MEMORY_RESERVATION"' in host_installer
    assert '--memory-swap "$TYPESENSE_MEMORY_SWAP"' in host_installer
    assert "TYPESENSE_MEMORY_LIMIT_BYTES=3221225472" in host_installer
    assert "TYPESENSE_MEMORY_RESERVATION_BYTES=2684354560" in host_installer
    assert '"$TYPESENSE_SNAPSHOT_DIR:$TYPESENSE_SNAPSHOT_IN_CONTAINER"' in host_installer
    assert 'chown root:root "$TYPESENSE_SNAPSHOT_DIR"' not in host_installer
    assert "/usr/local/sbin/jobseek-verify-typesense-snapshot-mount" in host_installer
    assert "backup-contract-pending" in host_installer
    assert "quiesce_typesense_backup" in host_installer
    assert "jobseek.typesense-snapshot-contract" in backup_installer
    assert "RequiresMountsFor=/mnt/jobseek-typesense-backup" in service
    assert "ConditionPathIsMountPoint=/mnt/jobseek-typesense-backup" in service
    assert "/usr/local/sbin/jobseek-verify-typesense-snapshot-mount" in backup_installer
    assert "TYPESENSE_SNAPSHOT_CONTAINER_MOUNT_ROOT=/jobseek-snapshots" in service
    assert "operations/snapshot" in workflow
    assert "--request POST" in workflow
    assert "--get" in workflow
    assert "--data-urlencode 'snapshot_path=/jobseek-snapshots/direct-mount-smoke'" in workflow
    assert '--output "$snapshot_body"' in workflow
    assert "--write-out '%{http_code}'" in workflow
    assert 'SNAPSHOT_HTTP_STATUS="$snapshot_status"' in workflow
    assert 'status != "201"' in workflow
    assert 'payload.get("success") is not True' in workflow
    assert '--data \'{"snapshot_path"' not in workflow
    assert "mount -t tmpfs" in workflow
    assert "stat -c '%d'" in workflow
    assert "--memory 3g" in workflow
    assert "--memory-reservation 2560m" in workflow
    assert "--memory-swap 3g" in workflow
    assert "Environment=TYPESENSE_MEMORY_POLICY_PHASE=enforced" in service


def test_typesense_restore_drill_is_isolated_and_self_cleaning() -> None:
    drill = (ROOT / "deploy/backups/typesense/restore-drill.sh").read_text(encoding="utf-8")

    assert "typesense/typesense:27.1@sha256:" in drill
    assert "typesense/typesense:27.1}" not in drill
    assert "restore image must be a reviewed digest-pinned 27.1 artifact" in drill
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
