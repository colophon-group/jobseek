"""Check the fixed, source-scoped PostgreSQL reader before host installation."""

from __future__ import annotations

import fcntl
import hashlib
import importlib.util
import json
import os
import signal
import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.export_cursor_fence import _CDC_CUTOFF_SQL, CDC_WRITER_BARRIER_ID

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "deploy/networking/justup-feed-readonly-role.py"
SPEC = importlib.util.spec_from_file_location("justup_feed_readonly_role", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
role = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(role)


def audit_evidence(*, taxonomy: int = 0) -> dict[str, object]:
    return {
        "login": True,
        "inherit": True,
        "superuser": False,
        "createdb": False,
        "createrole": False,
        "replication": False,
        "bypassrls": False,
        "connectionLimit": 2,
        "databaseConnect": True,
        "databaseCreate": False,
        "schemaCreate": False,
        "scramPassword": True,
        "taxonomyCount": taxonomy,
        "memberships": [],
        "schemaUsage": ["justup_feed", "public"],
        "writableSchemas": [],
        "settings": sorted(role.ROLE_SETTINGS),
        "readableTables": [role.VIEW],
        "readableColumns": sorted(
            {
                f"public.{table}.{column}"
                for table, columns in (
                    role.CORE_COLUMNS | (role.TAXONOMY_COLUMNS if taxonomy == 4 else {})
                ).items()
                for column in columns
            }
            | {f"{role.VIEW}.{column}" for column in ("posting_id", "locale", "hash", "html")}
        ),
        "writableTables": [],
        "accessibleSequences": [],
        "callableRoutines": [
            {
                "schema": "justup_feed",
                "name": "capture_cdc_snapshot_cutoff",
                "owner": role.OWNER,
                "securityDefiner": True,
            }
        ],
    }


def boundary_evidence() -> dict[str, object]:
    return {
        "ownerLogin": False,
        "ownerSuperuser": False,
        "ownerCreateRole": False,
        "ownerCreateDb": False,
        "ownerReplication": False,
        "ownerBypassRls": False,
        "ownerMemberships": ["pg_read_all_stats"],
        "ownerInboundMemberships": 0,
        "schemaOwner": role.OWNER,
        "schemaAcl": [f"{role.OWNER}:CREATE", f"{role.OWNER}:USAGE", f"{role.ROLE}:USAGE"],
        "functionCount": 1,
        "functionOwner": role.OWNER,
        "functionSecurityDefiner": True,
        "functionVolatility": "v",
        "functionParallel": "u",
        "functionLeakproof": False,
        "functionSource": role.CUTOFF_FUNCTION_BODY,
        "functionSettings": ["search_path=pg_catalog, pg_temp"],
        "functionAcl": [f"{role.OWNER}:EXECUTE", f"{role.ROLE}:EXECUTE"],
        "viewOwner": "crawler",
        "viewSecurityBarrier": True,
        "viewDefinition": role.VIEW_DEFINITION,
        "viewAcl": [
            "crawler:DELETE",
            "crawler:INSERT",
            "crawler:REFERENCES",
            "crawler:SELECT",
            "crawler:TRIGGER",
            "crawler:TRUNCATE",
            "crawler:UPDATE",
            f"{role.ROLE}:SELECT",
        ],
    }


def test_install_sql_limits_role_to_source_columns_and_cutoff_function() -> None:
    sql = role.install_sql("A" * 64)
    assert "CONNECTION LIMIT 2" in sql
    assert "GRANT SELECT (" in sql
    assert "ON TABLE public.company TO justup_feed_readonly" in sql
    assert "GRANT pg_read_all_stats TO justup_feed_cutoff_owner" in sql
    assert "GRANT pg_read_all_stats TO justup_feed_readonly" not in sql
    assert "WITH (security_barrier=true)" in sql
    assert "GRANT SELECT ON TABLE justup_feed.uploaded_description" in sql
    assert "GRANT SELECT ON TABLE public.descriptions" not in sql
    assert "PASSWORD 'SCRAM-SHA-256$4096:" in sql
    assert "A" * 64 not in sql
    assert "GRANT SELECT ON ALL TABLES" not in sql
    assert "GRANT INSERT" not in sql
    with pytest.raises(role.RoleError):
        role.install_sql("A' ; DROP TABLE job_posting; --")


def test_protected_password_matches_only_its_scram_verifier() -> None:
    verifier = role.scram_verifier("A" * 64, b"0" * 16)
    assert role.password_matches_scram("A" * 64, verifier)
    assert not role.password_matches_scram("B" * 64, verifier)
    assert not role.password_matches_scram("A" * 64, "not-a-scram-verifier")


def test_privileged_cutoff_is_identical_to_crawler_writer_barrier() -> None:
    assert role.CDC_WRITER_BARRIER_ID == CDC_WRITER_BARRIER_ID
    assert role.CUTOFF_QUERY.replace("pg_catalog.", "") == _CDC_CUTOFF_SQL.replace(
        "$1::bigint", f"{CDC_WRITER_BARRIER_ID}::bigint"
    )
    assert "SET search_path = pg_catalog, pg_temp" in role.install_sql("A" * 64)


@pytest.mark.parametrize("taxonomy", [0, 4])
def test_audit_accepts_only_exact_reader_contract(taxonomy: int) -> None:
    result = role.validate_audit(audit_evidence(taxonomy=taxonomy), boundary_evidence())
    assert result["role"] == "justup_feed_readonly"
    assert len(result["readableColumns"]) == 30 + sum(
        len(columns) for columns in role.TAXONOMY_COLUMNS.values()
    ) * (taxonomy == 4)


@pytest.mark.parametrize(
    ("field", "unexpected"),
    [
        ("memberships", ["pg_read_all_stats"]),
        ("readableTables", [role.VIEW, "public.job_board"]),
        ("readableColumns", ["public.company.extras"]),
        ("writableTables", ["public.job_posting"]),
        ("schemaUsage", ["justup_feed", "public", "private"]),
        ("accessibleSequences", ["public.secret_sequence"]),
        ("connectionLimit", -1),
        ("databaseCreate", True),
        ("scramPassword", False),
    ],
)
def test_audit_rejects_broader_or_unbounded_role(field: str, unexpected: object) -> None:
    evidence = audit_evidence()
    evidence[field] = unexpected
    with pytest.raises(role.RoleError):
        role.validate_audit(evidence, boundary_evidence())


def test_audit_rejects_partial_taxonomy_schema() -> None:
    with pytest.raises(role.RoleError, match="partial"):
        role.validate_audit(audit_evidence(taxonomy=2), boundary_evidence())


@pytest.mark.parametrize(
    "field",
    [
        "ownerMemberships",
        "functionSource",
        "functionAcl",
        "viewDefinition",
        "viewAcl",
        "viewSecurityBarrier",
    ],
)
def test_audit_rejects_privileged_boundary_drift(field: str) -> None:
    boundary = boundary_evidence()
    boundary[field] = None
    with pytest.raises(role.RoleError):
        role.validate_audit(audit_evidence(), boundary)


def test_audit_rejects_callable_external_security_definer() -> None:
    evidence = audit_evidence()
    evidence["callableRoutines"].append(
        {
            "schema": "public",
            "name": "private_data",
            "owner": "crawler",
            "securityDefiner": True,
        }
    )
    with pytest.raises(role.RoleError, match="unexpected privileged routine"):
        role.validate_audit(evidence, boundary_evidence())


def test_psql_hides_password_even_when_database_error_echoes_sql(monkeypatch) -> None:
    secret = "S" * 64

    def failed_run(args, **kwargs):
        assert tuple(args) == role.PSQL
        assert secret not in " ".join(args)
        assert secret not in kwargs["input"]
        return SimpleNamespace(returncode=1, stdout="", stderr=f"ERROR: {kwargs['input']}")

    monkeypatch.setattr(subprocess, "run", failed_run)
    with pytest.raises(role.RoleError) as failure:
        role.psql(role.install_sql(secret))
    assert secret not in str(failure.value)


def test_stage_verifier_requires_exact_digest_checked_payloads(tmp_path: Path) -> None:
    stage = tmp_path / "stage"
    installer = stage / "deploy/networking/justup-feed-readonly-role.py"
    conformance = stage / "scripts/jobseek-ingress-conformance.py"
    for directory in (stage, installer.parent.parent, installer.parent, conformance.parent):
        directory.mkdir(exist_ok=True, mode=0o700)
    installer.write_bytes(b"fixed installer")
    conformance.write_bytes(b"fixed ingress audit")
    installer.chmod(0o600)
    conformance.chmod(0o600)
    installer_hash = hashlib.sha256(installer.read_bytes()).hexdigest()
    conformance_hash = hashlib.sha256(conformance.read_bytes()).hexdigest()
    role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())

    conformance.write_bytes(b"tampered")
    with pytest.raises(role.RoleError, match="digest"):
        role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())
    conformance.write_bytes(b"fixed ingress audit")
    extra = stage / "extra"
    extra.write_bytes(b"unexpected")
    with pytest.raises(role.RoleError, match="missing or extra"):
        role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())
    extra.unlink()
    conformance.unlink()
    with pytest.raises(role.RoleError, match="missing or extra"):
        role.verify_stage(stage, installer_hash, conformance_hash, uid=os.getuid())


def test_install_requires_no_pending_ingress_rollback(tmp_path: Path, monkeypatch) -> None:
    pending = tmp_path / "pending"
    monkeypatch.setattr(role, "INGRESS_PENDING", (pending,))

    def inactive(args, **_kwargs):
        if args[0] == "systemctl":
            return SimpleNamespace(returncode=0, stdout="inactive\n")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(subprocess, "run", inactive)
    role.assert_ingress_stable()
    pending.touch()
    with pytest.raises(role.RoleError, match="pending"):
        role.assert_ingress_stable()
    pending.unlink()

    def active_timer(args, **_kwargs):
        if args[0] == "systemctl" and args[-1] == "jobseek-ingress-rollback.timer":
            return SimpleNamespace(returncode=0, stdout="active\n")
        return inactive(args)

    monkeypatch.setattr(subprocess, "run", active_timer)
    with pytest.raises(role.RoleError, match="not quiescent"):
        role.assert_ingress_stable()

    def rollback_container(args, **_kwargs):
        if args[0] == "docker":
            return SimpleNamespace(returncode=0, stdout="postgres-rollback-pre-ingress\n")
        return inactive(args)

    monkeypatch.setattr(subprocess, "run", rollback_container)
    with pytest.raises(role.RoleError, match="rollback container"):
        role.assert_ingress_stable()


def backup_restore_status(tmp_path: Path, now: int) -> tuple[Path, Path]:
    status_dir = tmp_path / "status"
    status_dir.mkdir(mode=0o700)
    backup_path = status_dir / "postgresql.json"
    restore_path = status_dir / "postgresql-restore-drill.json"
    backup_success = now - 90
    for path, value in (
        (
            backup_path,
            {
                "schema_version": 1,
                "service": "postgresql",
                "success": True,
                "attempt_unix": now - 100,
                "last_success_unix": backup_success,
                "last_success_details": {
                    "backup_label": "20261102-010000F",
                    "repository_latest_stop_unix": now - 95,
                },
            },
        ),
        (
            restore_path,
            {
                "schema_version": 1,
                "service": "postgresql",
                "source": "jobseek-postgresql-restore-drill",
                "success": True,
                "started_unix": now - 45,
                "completed_unix": now - 30,
                "backup_last_success_unix": backup_success,
                "backup_label": "20261102-010000F",
                "backup_stop_unix": now - 95,
            },
        ),
    ):
        path.write_text(json.dumps(value), encoding="utf-8")
        path.chmod(0o600)
    return backup_path, restore_path


def test_install_backup_gate_requires_fresh_bound_restore(tmp_path: Path) -> None:
    now = 2_000_000_000
    backup, restore = backup_restore_status(tmp_path, now)
    role.assert_backup_restore_fresh(
        backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
    )

    with pytest.raises(role.RoleError, match="stale"):
        role.assert_backup_restore_fresh(
            backup_path=backup,
            restore_path=restore,
            now=now + role.RESTORE_MAX_AGE_SECONDS + 1,
            uid=os.getuid(),
        )

    with pytest.raises(role.RoleError, match="stale"):
        role.assert_backup_restore_fresh(
            backup_path=backup,
            restore_path=restore,
            now=now + role.BACKUP_MAX_AGE_SECONDS + 1,
            uid=os.getuid(),
        )
    restore.unlink()
    with pytest.raises(role.RoleError, match="cannot be read"):
        role.assert_backup_restore_fresh(
            backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
        )


def test_install_backup_gate_rejects_failed_or_unbound_evidence(tmp_path: Path) -> None:
    now = 2_000_000_000
    backup, restore = backup_restore_status(tmp_path, now)
    value = json.loads(restore.read_text(encoding="utf-8"))
    value["backup_label"] = "different-backup"
    restore.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(role.RoleError, match="mismatched"):
        role.assert_backup_restore_fresh(
            backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
        )
    value["backup_label"] = "20261102-010000F"
    value["success"] = False
    restore.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(role.RoleError, match="not successful"):
        role.assert_backup_restore_fresh(
            backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
        )
    restore.chmod(0o644)
    with pytest.raises(role.RoleError, match="not protected"):
        role.assert_backup_restore_fresh(
            backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
        )


@pytest.mark.parametrize(
    ("field", "replacement"),
    [
        ("source", "operator-assertion"),
        ("backup_stop_unix", 1),
        ("completed_unix", True),
        ("started_unix", 1),
    ],
)
def test_install_backup_gate_rejects_tampered_restore_receipt(
    tmp_path: Path, field: str, replacement: object
) -> None:
    now = 2_000_000_000
    backup, restore = backup_restore_status(tmp_path, now)
    value = json.loads(restore.read_text(encoding="utf-8"))
    value[field] = replacement
    restore.write_text(json.dumps(value), encoding="utf-8")
    with pytest.raises(role.RoleError):
        role.assert_backup_restore_fresh(
            backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
        )


def test_restore_receipt_symlink_cannot_replace_protected_evidence(tmp_path: Path) -> None:
    now = 2_000_000_000
    _backup, restore = backup_restore_status(tmp_path, now)
    target = tmp_path / "target"
    target.write_text("preserve", encoding="utf-8")
    restore.unlink()
    restore.symlink_to(target)
    with pytest.raises(role.RoleError, match="not protected"):
        role.write_restore_status(restore, {"success": False}, uid=os.getuid())
    assert target.read_text(encoding="utf-8") == "preserve"


def test_install_rechecks_backup_gate_before_activation(monkeypatch) -> None:
    calls: list[str] = []

    def gated() -> None:
        calls.append("gate")
        if calls.count("gate") == 2:
            raise role.RoleError("Backup or restore evidence is stale or mismatched.")

    def psql(sql: str) -> str:
        if sql.startswith("SELECT rolcanlogin"):
            return ""
        if sql.startswith("SELECT rolpassword"):
            return "verifier"
        calls.append("activate" if "ALTER ROLE justup_feed_readonly LOGIN" in sql else "sql")
        return ""

    monkeypatch.setattr(role, "read_password", lambda _path: "A" * 64)
    monkeypatch.setattr(role, "assert_backup_restore_fresh", gated)
    monkeypatch.setattr(role, "psql", psql)
    monkeypatch.setattr(role, "audit", lambda **_kwargs: ({}, "role", "boundary"))
    monkeypatch.setattr(role, "password_matches_scram", lambda *_args: True)
    with pytest.raises(role.RoleError, match="stale"):
        role.install(Path("/unused/password"))
    assert calls == ["gate", "sql", "gate", "sql"]
    assert "activate" not in calls


def test_attest_restore_binds_real_drill_to_current_backup(tmp_path: Path, monkeypatch) -> None:
    now = 2_000_000_000
    backup, restore = backup_restore_status(tmp_path, now)
    drill = tmp_path / "restore-drill"
    drill.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    drill.chmod(0o700)
    observed: list[object] = []

    def run(path: Path, label: str, lock_fd: int) -> None:
        observed.append((path, label))
        assert os.fstat(lock_fd).st_ino > 0
        assert json.loads(restore.read_text(encoding="utf-8"))["success"] is False

    monkeypatch.setattr(role, "run_restore_drill", run)
    monkeypatch.setattr(role.time, "time", lambda: now)
    result = role.attest_restore(
        backup_path=backup,
        restore_path=restore,
        drill_path=drill,
        lock_path=tmp_path / "backup.lock",
        uid=os.getuid(),
    )
    assert observed == [(drill, "20261102-010000F")]
    assert result["success"] is True
    assert result["backup_last_success_unix"] == now - 90
    assert result["backup_label"] == "20261102-010000F"
    assert json.loads(restore.read_text(encoding="utf-8")) == result
    role.assert_backup_restore_fresh(
        backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
    )
    with pytest.raises(role.RoleError, match="stale"):
        role.assert_backup_restore_fresh(
            backup_path=backup,
            restore_path=restore,
            now=now + role.RESTORE_MAX_AGE_SECONDS + 1,
            uid=os.getuid(),
        )


def test_failed_drill_invalidates_old_restore_proof(tmp_path: Path, monkeypatch) -> None:
    now = 2_000_000_000
    backup, restore = backup_restore_status(tmp_path, now)
    drill = tmp_path / "restore-drill"
    drill.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
    drill.chmod(0o700)
    monkeypatch.setattr(role.time, "time", lambda: now)

    def fail(_path: Path, _label: str, _lock_fd: int) -> None:
        raise role.RoleError("PostgreSQL restore drill failed; no feed role was installed.")

    monkeypatch.setattr(role, "run_restore_drill", fail)
    with pytest.raises(role.RoleError, match="restore drill failed"):
        role.attest_restore(
            backup_path=backup,
            restore_path=restore,
            drill_path=drill,
            lock_path=tmp_path / "backup.lock",
            uid=os.getuid(),
        )
    assert json.loads(restore.read_text(encoding="utf-8"))["success"] is False
    with pytest.raises(role.RoleError, match="not successful"):
        role.assert_backup_restore_fresh(
            backup_path=backup, restore_path=restore, now=now, uid=os.getuid()
        )


def test_remote_install_attests_after_dispatch_before_ddl() -> None:
    source = (ROOT / "deploy/networking/run-remote.sh").read_text(encoding="utf-8")
    install = source.split('if [[ "$MODE" == install-feed-role ]]; then', 1)[1].split(
        'if [[ "$MODE" == audit ]]; then', 1
    )[0]
    attestation = install.index("jobseek-justup-feed-role attest-restore")
    conformance = install.index('python3 "$conformance" --role postgresql', attestation)
    ddl = install.index("jobseek-justup-feed-role install", conformance)
    assert attestation < conformance < ddl
    assert "remote 5h" in install


def test_restore_attestation_rejects_backup_rotated_during_drill(
    tmp_path: Path, monkeypatch
) -> None:
    now = 2_000_000_000
    backup, restore = backup_restore_status(tmp_path, now)
    drill = tmp_path / "restore-drill"
    drill.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    drill.chmod(0o700)
    monkeypatch.setattr(role.time, "time", lambda: now)

    def rotate(_path: Path, _label: str, _lock_fd: int) -> None:
        value = json.loads(backup.read_text(encoding="utf-8"))
        value["last_success_unix"] = now - 1
        backup.write_text(json.dumps(value), encoding="utf-8")

    monkeypatch.setattr(role, "run_restore_drill", rotate)
    with pytest.raises(role.RoleError, match="changed during"):
        role.attest_restore(
            backup_path=backup,
            restore_path=restore,
            drill_path=drill,
            lock_path=tmp_path / "backup.lock",
            uid=os.getuid(),
        )
    assert json.loads(restore.read_text(encoding="utf-8"))["success"] is False


def test_attest_restore_rejects_concurrent_backup_before_drill(tmp_path: Path, monkeypatch) -> None:
    now = 2_000_000_000
    backup, restore = backup_restore_status(tmp_path, now)
    lock_path = tmp_path / "backup.lock"
    lock_path.touch(mode=0o600)
    lock_path.chmod(0o600)
    monkeypatch.setattr(role.time, "time", lambda: now)
    monkeypatch.setattr(role, "run_restore_drill", lambda *_args: pytest.fail("drill ran"))
    with lock_path.open("r+") as locked:
        fcntl.flock(locked, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with pytest.raises(role.RoleError, match="already running"):
            role.attest_restore(
                backup_path=backup,
                restore_path=restore,
                drill_path=tmp_path / "unused",
                lock_path=lock_path,
                uid=os.getuid(),
            )
    assert json.loads(restore.read_text(encoding="utf-8"))["success"] is True


def test_restore_timeout_stops_process_group(monkeypatch) -> None:
    class TimedOutProcess:
        pid = 43210
        waits = 0

        def wait(self, *, timeout: int) -> int:
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired("restore", timeout)
            return 124

    process = TimedOutProcess()
    stopped: list[tuple[int, int]] = []

    def spawn(args, **kwargs):
        assert args == ("/protected/restore", "20261102-010000F")
        assert kwargs["pass_fds"] == (3,)
        assert kwargs["start_new_session"] is True
        return process

    monkeypatch.setattr(role.subprocess, "Popen", spawn)
    monkeypatch.setattr(role.os, "killpg", lambda pid, signum: stopped.append((pid, signum)))
    with pytest.raises(role.RoleError, match="without proof"):
        role.run_restore_drill(Path("/protected/restore"), "20261102-010000F", 3)
    assert stopped == [(process.pid, role.signal.SIGTERM)]
    assert process.waits == 2


def test_isolated_restore_drill_selects_exact_backup_set(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name, source in {
        "mountpoint": "#!/bin/sh\nexit 0\n",
        "findmnt": "#!/bin/sh\necho cifs\n",
        "install": '#!/bin/sh\nfor value do target=$value; done\nmkdir -p -- "$target"\n',
        "docker": """#!/bin/sh
printf '%s\\n' "$*" >> "$TRACE"
case "$1 $2" in
  'image inspect') exit 0 ;;
  'container inspect') exit 1 ;;
  'run --rm')
    found=false
    for value do
      case "$value" in --set=*) found=$value ;; esac
    done
    if [ -n "$EXPECTED_LABEL" ]; then
      [ "$found" = "--set=$EXPECTED_LABEL" ] || exit 7
    else
      [ "$found" = false ] || exit 7
    fi
    [ "${SLOW_RESTORE:-}" = 1 ] && sleep 30
    exit 0 ;;
  'run --detach') echo synthetic-container; exit 0 ;;
  'exec '*)
    case "$*" in
      *pg_is_in_recovery*) echo f ;;
      *current_setting*) echo '16.13|f|off' ;;
    esac
    exit 0 ;;
  'rm -f') exit 0 ;;
esac
exit 0
""",
    }.items():
        path = fake_bin / name
        path.write_text(source, encoding="utf-8")
        path.chmod(0o700)
    config = tmp_path / "config"
    config.mkdir()
    (config / "pgbackrest.conf").write_text("synthetic\n", encoding="utf-8")
    repository = tmp_path / "repository"
    repository.mkdir()
    base = tmp_path / "drills"
    base.mkdir()
    trace = tmp_path / "docker.trace"
    label = "20261102-010000F"
    env = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "TRACE": str(trace),
        "EXPECTED_LABEL": label,
        "JOBSEEK_POSTGRES_BACKUP_CONFIG": str(config),
        "JOBSEEK_POSTGRES_BACKUP_REPOSITORY": str(repository),
        "JOBSEEK_POSTGRES_RESTORE_DRILL_DIR": str(base),
    }
    drill = ROOT / "deploy/backups/postgresql/restore-drill.sh"
    exact = subprocess.run(
        ("bash", str(drill), label),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert exact.returncode == 0, exact.stderr
    assert f"--set={label}" in trace.read_text(encoding="utf-8")
    assert "restore and relation integrity drill passed" in exact.stdout

    trace.unlink()
    mismatched = subprocess.run(
        ("bash", str(drill), "20261102-020000F"),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert mismatched.returncode != 0
    assert "restore and relation integrity drill passed" not in mismatched.stdout
    assert "--set=20261102-020000F" in trace.read_text(encoding="utf-8")

    trace.unlink()
    latest = subprocess.run(
        ("bash", str(drill)),
        env={**env, "EXPECTED_LABEL": ""},
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert latest.returncode == 0, latest.stderr
    assert "--set=" not in trace.read_text(encoding="utf-8")

    trace.unlink()
    unsafe = subprocess.run(
        ("bash", str(drill), "bad;label"),
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert unsafe.returncode == 2
    assert not trace.exists()

    stopped = subprocess.Popen(
        ("bash", str(drill), label),
        env={**env, "SLOW_RESTORE": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        for _ in range(100):
            if trace.exists() and "run --rm" in trace.read_text(encoding="utf-8"):
                break
            time.sleep(0.02)
        else:
            pytest.fail("restore fixture never reached the container step")
        os.killpg(stopped.pid, signal.SIGTERM)
        assert stopped.wait(timeout=5) != 0
        assert "rm -f" in trace.read_text(encoding="utf-8")
    finally:
        if stopped.poll() is None:
            os.killpg(stopped.pid, signal.SIGKILL)
            stopped.wait(timeout=5)
