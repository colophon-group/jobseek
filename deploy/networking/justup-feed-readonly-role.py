#!/usr/bin/env python3
"""Install and audit the fixed PostgreSQL reader used by the Justup feed.

Run only as the protected operator on the PostgreSQL host. The password comes
from a root-owned file and is sent to psql on stdin, never in an argument or
operator output. HBA admission is managed separately by the ingress baseline.
"""

from __future__ import annotations

import argparse
import base64
import binascii
import fcntl
import hashlib
import hmac
import json
import os
import re
import secrets
import signal
import stat
import subprocess
import sys
import time
from contextlib import contextmanager, suppress
from pathlib import Path

ROLE = "justup_feed_readonly"
OWNER = "justup_feed_cutoff_owner"
VIEW = "justup_feed.uploaded_description"
INGRESS_PENDING = (
    Path("/var/lib/jobseek-ingress/pending"),
    Path("/var/lib/jobseek-ingress/postgresql/pending"),
)
ROLLBACK_UNITS = (
    "jobseek-ingress-rollback.timer",
    "jobseek-ingress-rollback.service",
    "jobseek-postgresql-network-rollback.timer",
    "jobseek-postgresql-network-rollback.service",
)
BACKUP_STATUS = Path("/var/lib/jobseek-backup/status/postgresql.json")
RESTORE_STATUS = Path("/var/lib/jobseek-backup/status/postgresql-restore-drill.json")
RESTORE_DRILL = Path("/usr/local/sbin/jobseek-postgresql-restore-drill")
BACKUP_LOCK = Path("/run/jobseek-data-backup-postgresql.lock")
BACKUP_MAX_AGE_SECONDS = 36 * 60 * 60
RESTORE_MAX_AGE_SECONDS = 30 * 60
RESTORE_DRILL_TIMEOUT_SECONDS = 4 * 60 * 60
RESTORE_STOP_GRACE_SECONDS = 60
CORE_COLUMNS = {
    "company": {"id", "name", "slug", "website"},
    "job_posting": {
        "id",
        "company_id",
        "titles",
        "source_url",
        "is_active",
        "tdm_reserved",
        "first_seen_at",
        "last_seen_at",
        "updated_at",
        "locales",
        "location_ids",
        "description_r2_hash",
        "location_types",
        "employment_type",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "experience_min",
        "experience_max",
        "occupation_id",
        "seniority_id",
    },
}
TAXONOMY_COLUMNS = {
    "location_name": {"location_id", "name", "is_display", "locale"},
    "location": {"id", "type"},
    "occupation_name": {"occupation_id", "name", "locale", "is_display"},
    "seniority_name": {"seniority_id", "name", "locale", "is_display"},
}
TAXONOMY_TABLES = set(TAXONOMY_COLUMNS)
PASSWORD_RE = re.compile(r"[A-Za-z0-9_-]{43,128}\Z")
SCRAM_ITERATIONS = 4096
ROLE_SETTINGS = {
    "default_transaction_read_only=on",
    "statement_timeout=60s",
    "idle_in_transaction_session_timeout=120s",
    "lock_timeout=5s",
    "search_path=pg_catalog, public, pg_temp",
}
CDC_WRITER_BARRIER_ID = 18933879273374539
CUTOFF_QUERY = """
WITH captured AS MATERIALIZED (
    SELECT pg_catalog.clock_timestamp() AS captured_at
),
initial_writers AS MATERIALIZED (
    SELECT 1 AS present, locks.pid
    FROM captured
    JOIN pg_catalog.pg_locks AS locks ON true
    WHERE locks.locktype = 'advisory'
      AND locks.database = (
          SELECT oid FROM pg_catalog.pg_database WHERE datname = pg_catalog.current_database()
      )
      AND locks.classid = ((18933879273374539::bigint >> 32) & 4294967295)::oid
      AND locks.objid = (18933879273374539::bigint & 4294967295)::oid
      AND locks.objsubid = 1
      AND locks.mode = 'ShareLock'
      AND locks.granted
),
scan_gate AS MATERIALIZED (
    -- This dependency guarantees that the initial lock set is captured before
    -- activity is inspected. The no-op gate is replaced with pg_sleep only by
    -- the isolated concurrency test that commits a writer between the scans.
    SELECT true AS ready
    FROM (SELECT count(*) FROM initial_writers) AS captured_locks
),
gated_writers AS MATERIALIZED (
    SELECT initial_writers.present,
           initial_writers.pid
    FROM initial_writers
    CROSS JOIN scan_gate
),
writers AS MATERIALIZED (
    SELECT gated_writers.present,
           gated_writers.pid,
           activity.xact_start
    FROM gated_writers
    LEFT JOIN pg_catalog.pg_stat_activity AS activity ON activity.pid = gated_writers.pid
),
classified AS MATERIALIZED (
    SELECT writers.present,
           writers.xact_start,
           CASE
               WHEN writers.xact_start IS NOT NULL THEN 'active'
               WHEN writers.pid IS NULL THEN 'unknown'
               WHEN EXISTS (
                   SELECT 1
                   FROM pg_catalog.pg_locks AS recheck
                   WHERE recheck.locktype = 'advisory'
                     AND recheck.database = (
                         SELECT oid
                         FROM pg_catalog.pg_database
                         WHERE datname = pg_catalog.current_database()
                     )
                     AND recheck.classid =
                         ((18933879273374539::bigint >> 32) & 4294967295)::oid
                     AND recheck.objid =
                         (18933879273374539::bigint & 4294967295)::oid
                     AND recheck.objsubid = 1
                     AND recheck.mode = 'ShareLock'
                     AND recheck.granted
                     AND recheck.pid = writers.pid
               ) THEN 'unknown'
               ELSE 'released'
           END AS writer_state
    FROM writers
)
SELECT captured.captured_at,
       LEAST(
           captured.captured_at,
           COALESCE(
               min(classified.xact_start) FILTER (
                   WHERE classified.writer_state = 'active'
               ),
               captured.captured_at
           )
       ) AS cutoff,
       count(classified.present) FILTER (
           WHERE classified.writer_state IN ('active', 'unknown')
       )::int AS active_writers,
       count(classified.present) FILTER (
           WHERE classified.writer_state = 'released'
       )::int AS released_writers,
       count(classified.present) FILTER (
           WHERE classified.writer_state = 'unknown'
       )::int AS unknown_writers
FROM captured
LEFT JOIN classified ON true
GROUP BY captured.captured_at
"""
CUTOFF_FUNCTION_BODY = "\n" + CUTOFF_QUERY.strip() + ";\n"
VIEW_DEFINITION = (
    "SELECT posting_id, locale, hash, html FROM descriptions WHERE r2_uploaded IS TRUE;"
)

PSQL = (
    "docker",
    "exec",
    "-i",
    "postgres",
    "psql",
    "-U",
    "crawler",
    "-d",
    "crawler",
    "-X",
    "-qAt",
    "-v",
    "ON_ERROR_STOP=1",
)


class RoleError(Exception):
    """Safe operator-facing failure without credential or SQL content."""


def protected_status(path: Path, *, uid: int = 0) -> dict[str, object]:
    """Read one root-owned, bounded status file without following links."""
    if not path.is_absolute() or path.name in ("", ".", ".."):
        raise RoleError("Backup evidence path is invalid.")
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parent = os.fstat(parent_fd)
            if parent.st_uid != uid or stat.S_IMODE(parent.st_mode) & 0o022:
                raise RoleError("Backup evidence directory is not protected.")
            fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent_fd)
            try:
                info = os.fstat(fd)
                if (
                    not stat.S_ISREG(info.st_mode)
                    or info.st_uid != uid
                    or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600
                    or info.st_size > 8192
                ):
                    raise RoleError("Backup evidence file is not protected.")
                raw = os.read(fd, 8193)
                if len(raw) != info.st_size:
                    raise RoleError("Backup evidence changed while being read.")
            finally:
                os.close(fd)
        finally:
            os.close(parent_fd)
    except OSError as exc:
        raise RoleError("Backup evidence cannot be read.") from exc
    if len(raw) > 8192:
        raise RoleError("Backup evidence is too large.")
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RoleError("Backup evidence is invalid JSON.") from exc
    if not isinstance(value, dict):
        raise RoleError("Backup evidence is invalid.")
    return value


def fresh_backup_identity(backup: dict[str, object], checked_at: float) -> tuple[int, str, int]:
    if (
        backup.get("schema_version") != 1
        or backup.get("service") != "postgresql"
        or backup.get("success") is not True
    ):
        raise RoleError("PostgreSQL backup evidence is not successful.")
    last_success = backup.get("last_success_unix")
    attempt = backup.get("attempt_unix")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in (last_success, attempt)
    ):
        raise RoleError("PostgreSQL backup timestamp is invalid.")
    if attempt > last_success or not 0 <= checked_at - last_success <= BACKUP_MAX_AGE_SECONDS:
        raise RoleError("PostgreSQL backup evidence is stale.")
    details = backup.get("last_success_details")
    if not isinstance(details, dict):
        raise RoleError("PostgreSQL backup identity is missing.")
    label = details.get("backup_label")
    stop = details.get("repository_latest_stop_unix")
    if (
        not isinstance(label, str)
        or re.fullmatch(r"[A-Za-z0-9_-]{4,128}", label) is None
        or not isinstance(stop, int)
        or isinstance(stop, bool)
        or not 0 < stop <= last_success
    ):
        raise RoleError("PostgreSQL backup identity is invalid.")
    return last_success, label, stop


def assert_backup_restore_fresh(
    *,
    backup_path: Path = BACKUP_STATUS,
    restore_path: Path = RESTORE_STATUS,
    now: float | None = None,
    uid: int = 0,
) -> None:
    """Fail closed on stale or unbound backup and completed restore proof."""
    checked_at = time.time() if now is None else now
    backup = protected_status(backup_path, uid=uid)
    restore = protected_status(restore_path, uid=uid)
    last_success, label, stop = fresh_backup_identity(backup, checked_at)
    if (
        restore.get("schema_version") != 1
        or restore.get("service") != "postgresql"
        or restore.get("success") is not True
        or restore.get("source") != "jobseek-postgresql-restore-drill"
    ):
        raise RoleError("Backup or restore evidence is not successful.")
    restored_at = restore.get("completed_unix")
    started_at = restore.get("started_unix")
    restored_backup = restore.get("backup_last_success_unix")
    restored_label = restore.get("backup_label")
    restored_stop = restore.get("backup_stop_unix")
    if any(
        not isinstance(value, int) or isinstance(value, bool) or value <= 0
        for value in (started_at, restored_at, restored_backup, restored_stop)
    ):
        raise RoleError("Backup or restore timestamp is invalid.")
    if (
        restored_backup != last_success
        or restored_label != label
        or restored_stop != stop
        or not last_success <= started_at <= restored_at
        or not 0 <= checked_at - restored_at <= RESTORE_MAX_AGE_SECONDS
    ):
        raise RoleError("Backup or restore evidence is stale or mismatched.")


def write_restore_status(path: Path, value: dict[str, object], *, uid: int = 0) -> None:
    """Atomically replace the root-only drill result, including a failed attempt."""
    payload = (json.dumps(value, sort_keys=True, separators=(",", ":")) + "\n").encode()
    if len(payload) > 8192:
        raise RoleError("Restore evidence is too large.")
    temporary = f".{path.name}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    try:
        parent_fd = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            parent = os.fstat(parent_fd)
            if parent.st_uid != uid or stat.S_IMODE(parent.st_mode) & 0o022:
                raise RoleError("Backup evidence directory is not protected.")
            fd = os.open(
                temporary,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                0o600,
                dir_fd=parent_fd,
            )
            try:
                remaining = memoryview(payload)
                while remaining:
                    written = os.write(fd, remaining)
                    if written <= 0:
                        raise RoleError("Restore evidence write was incomplete.")
                    remaining = remaining[written:]
                os.fsync(fd)
            finally:
                os.close(fd)
            try:
                existing = os.stat(path.name, dir_fd=parent_fd, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None and (
                not stat.S_ISREG(existing.st_mode)
                or existing.st_uid != uid
                or existing.st_nlink != 1
                or stat.S_IMODE(existing.st_mode) != 0o600
            ):
                raise RoleError("Prior restore evidence is not protected.")
            os.replace(temporary, path.name, src_dir_fd=parent_fd, dst_dir_fd=parent_fd)
            os.fsync(parent_fd)
        finally:
            with suppress(FileNotFoundError):
                os.unlink(temporary, dir_fd=parent_fd)
            os.close(parent_fd)
    except OSError as exc:
        raise RoleError("Restore evidence cannot be written.") from exc


@contextmanager
def backup_operation_lock(path: Path = BACKUP_LOCK, *, uid: int = 0):
    """Serialize a drill with the installed backup and retention routine."""
    if not path.is_absolute():
        raise RoleError("PostgreSQL backup lock path is invalid.")
    try:
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_uid != uid
                or info.st_nlink != 1
                or stat.S_IMODE(info.st_mode) & 0o022
            ):
                raise RoleError("PostgreSQL backup lock is not protected.")
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RoleError("PostgreSQL backup operation is already running.") from exc
            yield fd
        finally:
            os.close(fd)
    except OSError as exc:
        raise RoleError("PostgreSQL backup lock cannot be acquired.") from exc


def run_restore_drill(drill_path: Path, label: str, lock_fd: int) -> None:
    """Stop the whole drill process group gracefully on timeout or interruption."""

    def interrupted(_signum, _frame):
        raise RoleError("PostgreSQL restore drill was interrupted.")

    old_term = signal.signal(signal.SIGTERM, interrupted)
    old_int = signal.signal(signal.SIGINT, interrupted)
    try:
        try:
            process = subprocess.Popen(
                (str(drill_path), label),
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                pass_fds=(lock_fd,),
            )
        except OSError as exc:
            raise RoleError("PostgreSQL restore drill could not start.") from exc
        try:
            code = process.wait(timeout=RESTORE_DRILL_TIMEOUT_SECONDS)
        except (subprocess.TimeoutExpired, RoleError) as exc:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            signal.signal(signal.SIGINT, signal.SIG_IGN)
            with suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=RESTORE_STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                with suppress(ProcessLookupError):
                    os.killpg(process.pid, signal.SIGKILL)
                with suppress(subprocess.TimeoutExpired):
                    process.wait(timeout=10)
            raise RoleError("PostgreSQL restore drill stopped without proof.") from exc
    finally:
        signal.signal(signal.SIGTERM, old_term)
        signal.signal(signal.SIGINT, old_int)
    if code != 0:
        raise RoleError("PostgreSQL restore drill failed; no feed role was installed.")


def attest_restore(
    *,
    backup_path: Path = BACKUP_STATUS,
    restore_path: Path = RESTORE_STATUS,
    drill_path: Path = RESTORE_DRILL,
    lock_path: Path = BACKUP_LOCK,
    uid: int = 0,
) -> dict[str, object]:
    """Run the installed isolated drill and bind success to the latest backup."""
    with backup_operation_lock(lock_path, uid=uid) as lock_fd:
        first_backup, first_label, first_stop = fresh_backup_identity(
            protected_status(backup_path, uid=uid), time.time()
        )
        receipt: dict[str, object] = {
            "schema_version": 1,
            "service": "postgresql",
            "source": "jobseek-postgresql-restore-drill",
            "success": False,
            "backup_last_success_unix": first_backup,
            "backup_label": first_label,
            "backup_stop_unix": first_stop,
            "started_unix": int(time.time()),
        }
        write_restore_status(restore_path, receipt, uid=uid)
        try:
            drill = drill_path.lstat()
            drill_parent = drill_path.parent.lstat()
        except OSError as exc:
            raise RoleError("PostgreSQL restore drill is unavailable.") from exc
        if (
            not drill_path.is_absolute()
            or not stat.S_ISDIR(drill_parent.st_mode)
            or drill_parent.st_uid != uid
            or stat.S_IMODE(drill_parent.st_mode) & 0o022
            or not stat.S_ISREG(drill.st_mode)
            or drill.st_uid != uid
            or drill.st_nlink != 1
            or stat.S_IMODE(drill.st_mode) & 0o022
            or not stat.S_IMODE(drill.st_mode) & 0o100
        ):
            raise RoleError("PostgreSQL restore drill is not protected.")
        run_restore_drill(drill_path, first_label, lock_fd)
        last_identity = fresh_backup_identity(protected_status(backup_path, uid=uid), time.time())
        if last_identity != (first_backup, first_label, first_stop):
            raise RoleError("PostgreSQL backup changed during the restore drill.")
        receipt.update(success=True, completed_unix=int(time.time()))
        write_restore_status(restore_path, receipt, uid=uid)
        return receipt


def read_password(path: Path) -> str:
    if not path.is_absolute() or path.is_symlink() or path.parent.is_symlink():
        raise RoleError("Password file must be an absolute regular path.")
    try:
        parent = path.parent.stat()
    except OSError as exc:
        raise RoleError("Password directory cannot be inspected.") from exc
    if parent.st_uid != 0 or not stat.S_ISDIR(parent.st_mode) or parent.st_mode & 0o022:
        raise RoleError("Password directory must be root-owned and not writable by others.")
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except OSError as exc:
        raise RoleError("Password file cannot be opened.") from exc
    try:
        info = os.fstat(fd)
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != 0
            or info.st_nlink != 1
            or stat.S_IMODE(info.st_mode) != 0o600
        ):
            raise RoleError("Password file must be a root-owned regular file with mode 0600.")
        raw = b""
        while len(raw) < 257:
            chunk = os.read(fd, 257 - len(raw))
            if not chunk:
                break
            raw += chunk
    finally:
        os.close(fd)
    if len(raw) > 256:
        raise RoleError("Password file is too large.")
    if raw.endswith(b"\n"):
        raw = raw[:-1]
    try:
        password = raw.decode("ascii")
    except UnicodeDecodeError as exc:
        raise RoleError("Password must be base64url text.") from exc
    if PASSWORD_RE.fullmatch(password) is None:
        raise RoleError("Password must be 43 to 128 base64url characters.")
    return password


def verify_stage(stage: Path, installer_sha256: str, conformance_sha256: str, uid: int = 0) -> None:
    if not stage.is_absolute() or stage.is_symlink() or not stage.is_dir():
        raise RoleError("Feed role stage is not a protected directory.")
    expected = {
        Path("deploy"),
        Path("deploy/networking"),
        Path("scripts"),
        Path("deploy/networking/justup-feed-readonly-role.py"),
        Path("scripts/jobseek-ingress-conformance.py"),
    }
    actual = {item.relative_to(stage) for item in stage.rglob("*")}
    if actual != expected:
        raise RoleError("Feed role stage has missing or extra entries.")
    for path in [stage, *(stage / item for item in expected)]:
        info = path.lstat()
        if info.st_uid != uid or info.st_gid != os.getgid() or path.is_symlink():
            raise RoleError("Feed role stage ownership or type is unsafe.")
        if path.is_dir() and stat.S_IMODE(info.st_mode) != 0o700:
            raise RoleError("Feed role stage directory mode is unsafe.")
        if path.is_file() and stat.S_IMODE(info.st_mode) != 0o600:
            raise RoleError("Feed role stage file mode is unsafe.")
    for relative, expected_sha256 in (
        (Path("deploy/networking/justup-feed-readonly-role.py"), installer_sha256),
        (Path("scripts/jobseek-ingress-conformance.py"), conformance_sha256),
    ):
        if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
            raise RoleError("Feed role stage digest is invalid.")
        digest = hashlib.sha256((stage / relative).read_bytes()).hexdigest()
        if digest != expected_sha256:
            raise RoleError("Feed role stage digest does not match.")


def assert_ingress_stable() -> None:
    for pending in INGRESS_PENDING:
        if pending.exists() or pending.is_symlink():
            raise RoleError("Ingress rollback is still pending.")
    for unit in ROLLBACK_UNITS:
        try:
            state = subprocess.run(
                ("systemctl", "show", "-p", "ActiveState", "--value", unit),
                capture_output=True,
                text=True,
                timeout=10,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RoleError("Ingress rollback state cannot be verified.") from exc
        if state.returncode != 0 or state.stdout.strip() != "inactive":
            raise RoleError("Ingress rollback state is not quiescent.")
    try:
        rollback = subprocess.run(
            (
                "docker",
                "ps",
                "-a",
                "--filter",
                "name=^postgres-rollback-pre-ingress$",
                "--format",
                "{{.Names}}",
            ),
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RoleError("PostgreSQL rollback container cannot be inspected.") from exc
    if rollback.returncode != 0 or rollback.stdout.strip():
        raise RoleError("PostgreSQL rollback container remains present.")


def scram_verifier(password: str, salt: bytes | None = None) -> str:
    if PASSWORD_RE.fullmatch(password) is None:
        raise RoleError("Password must be 43 to 128 base64url characters.")
    salt = secrets.token_bytes(16) if salt is None else salt
    if len(salt) != 16:
        raise RoleError("SCRAM salt length is invalid.")
    salted = hashlib.pbkdf2_hmac("sha256", password.encode("ascii"), salt, SCRAM_ITERATIONS)
    client_key = hmac.digest(salted, b"Client Key", "sha256")
    stored_key = hashlib.sha256(client_key).digest()
    server_key = hmac.digest(salted, b"Server Key", "sha256")

    def encoded(value: bytes) -> str:
        return base64.b64encode(value).decode("ascii")

    return (
        f"SCRAM-SHA-256${SCRAM_ITERATIONS}:{encoded(salt)}"
        f"${encoded(stored_key)}:{encoded(server_key)}"
    )


def password_matches_scram(password: str, verifier: str) -> bool:
    match = re.fullmatch(
        r"SCRAM-SHA-256\$4096:([A-Za-z0-9+/=]+)\$[A-Za-z0-9+/=]+:[A-Za-z0-9+/=]+",
        verifier,
    )
    if match is None:
        return False
    try:
        salt = base64.b64decode(match.group(1), validate=True)
    except (ValueError, binascii.Error):
        return False
    try:
        return hmac.compare_digest(scram_verifier(password, salt), verifier)
    except RoleError:
        return False


def install_sql(password: str) -> str:
    verifier = scram_verifier(password)
    # Only the derived SCRAM verifier reaches PostgreSQL; plaintext remains
    # process-local and never appears in SQL, arguments, logs or output.
    column_grants = "\n".join(
        f"GRANT SELECT ({', '.join(sorted(columns))}) ON TABLE public.{table} TO {ROLE};"
        for table, columns in CORE_COLUMNS.items()
    )
    taxonomy_grants = "\n".join(
        f"        EXECUTE 'GRANT SELECT ({', '.join(sorted(columns))}) "
        f"ON TABLE public.{table} TO {ROLE}';"
        for table, columns in TAXONOMY_COLUMNS.items()
    )
    return f"""BEGIN;
SET LOCAL statement_timeout = '60s';
SET LOCAL password_encryption = 'scram-sha-256';
DO $$
BEGIN
    IF to_regclass('public.company') IS NULL
       OR to_regclass('public.job_posting') IS NULL
       OR to_regclass('public.descriptions') IS NULL THEN
        RAISE EXCEPTION 'Required feed tables are missing';
    END IF;
    IF num_nonnulls(
        to_regclass('public.location_name'), to_regclass('public.location'),
        to_regclass('public.occupation_name'), to_regclass('public.seniority_name')
    ) NOT IN (0, 4) THEN
        RAISE EXCEPTION 'Taxonomy display schema is partial';
    END IF;
END $$;
CREATE ROLE {OWNER} NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS;
GRANT pg_read_all_stats TO {OWNER};
CREATE ROLE {ROLE} NOLOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE
    NOREPLICATION NOBYPASSRLS CONNECTION LIMIT 2 PASSWORD '{verifier}';
GRANT CONNECT ON DATABASE crawler TO {ROLE};
GRANT USAGE ON SCHEMA public TO {ROLE};
{column_grants}
CREATE SCHEMA justup_feed AUTHORIZATION {OWNER};
REVOKE ALL ON SCHEMA justup_feed FROM PUBLIC;
GRANT USAGE ON SCHEMA justup_feed TO {ROLE};
SET ROLE {OWNER};
CREATE FUNCTION justup_feed.capture_cdc_snapshot_cutoff()
RETURNS TABLE(captured_at timestamptz, cutoff timestamptz,
              active_writers integer, released_writers integer, unknown_writers integer)
LANGUAGE sql VOLATILE PARALLEL UNSAFE SECURITY DEFINER
SET search_path = pg_catalog, pg_temp
AS $justup_feed_cutoff${CUTOFF_FUNCTION_BODY}$justup_feed_cutoff$;
RESET ROLE;
REVOKE ALL ON FUNCTION justup_feed.capture_cdc_snapshot_cutoff() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION justup_feed.capture_cdc_snapshot_cutoff() TO {ROLE};
CREATE VIEW {VIEW} WITH (security_barrier=true) AS
    SELECT posting_id, locale, hash, html FROM public.descriptions
    WHERE r2_uploaded IS TRUE;
REVOKE ALL ON TABLE {VIEW} FROM PUBLIC;
GRANT SELECT ON TABLE {VIEW} TO {ROLE};
DO $$
BEGIN
    IF num_nonnulls(
        to_regclass('public.location_name'), to_regclass('public.location'),
        to_regclass('public.occupation_name'), to_regclass('public.seniority_name')
    ) = 4 THEN
{taxonomy_grants}
    END IF;
END $$;
ALTER ROLE {ROLE} IN DATABASE crawler SET default_transaction_read_only = on;
ALTER ROLE {ROLE} IN DATABASE crawler SET statement_timeout = '60s';
ALTER ROLE {ROLE} IN DATABASE crawler SET idle_in_transaction_session_timeout = '120s';
ALTER ROLE {ROLE} IN DATABASE crawler SET lock_timeout = '5s';
ALTER ROLE {ROLE} IN DATABASE crawler SET search_path = pg_catalog, public, pg_temp;
COMMIT;
"""


AUDIT_SQL = f"""SELECT json_build_object(
    'login', r.rolcanlogin,
    'inherit', r.rolinherit,
    'superuser', r.rolsuper,
    'createdb', r.rolcreatedb,
    'createrole', r.rolcreaterole,
    'replication', r.rolreplication,
    'bypassrls', r.rolbypassrls,
    'connectionLimit', r.rolconnlimit,
    'databaseConnect', has_database_privilege(r.rolname, 'crawler', 'CONNECT'),
    'databaseCreate', has_database_privilege(r.rolname, 'crawler', 'CREATE'),
    'schemaCreate', has_schema_privilege(r.rolname, 'public', 'CREATE'),
    'scramPassword', (SELECT a.rolpassword LIKE 'SCRAM-SHA-256$%'
        FROM pg_authid AS a WHERE a.oid = r.oid),
    'taxonomyCount', num_nonnulls(
        to_regclass('public.location_name'), to_regclass('public.location'),
        to_regclass('public.occupation_name'), to_regclass('public.seniority_name')
    ),
    'memberships', (SELECT coalesce(json_agg(g.rolname ORDER BY g.rolname), '[]'::json)
        FROM pg_auth_members AS m JOIN pg_roles AS g ON g.oid = m.roleid
        WHERE m.member = r.oid),
    'settings', (SELECT to_json(s.setconfig) FROM pg_db_role_setting AS s
        WHERE s.setrole = r.oid
          AND s.setdatabase = (SELECT oid FROM pg_database WHERE datname = 'crawler')),
    'schemaUsage', (SELECT coalesce(json_agg(n.nspname ORDER BY n.nspname), '[]'::json)
        FROM pg_namespace AS n WHERE n.nspname NOT LIKE 'pg_%'
          AND n.nspname <> 'information_schema'
          AND has_schema_privilege(r.rolname, n.oid, 'USAGE')),
    'writableSchemas', (SELECT coalesce(json_agg(n.nspname ORDER BY n.nspname), '[]'::json)
        FROM pg_namespace AS n WHERE n.nspname NOT LIKE 'pg_%'
          AND n.nspname <> 'information_schema'
          AND has_schema_privilege(r.rolname, n.oid, 'CREATE')),
    'readableTables', (SELECT coalesce(json_agg(n.nspname || '.' || c.relname
        ORDER BY n.nspname, c.relname), '[]'::json)
        FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','v','m','f')
          AND has_table_privilege(r.rolname, c.oid, 'SELECT')),
    'readableColumns', (SELECT coalesce(json_agg(
        n.nspname || '.' || c.relname || '.' || a.attname
        ORDER BY n.nspname, c.relname, a.attname), '[]'::json)
        FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        JOIN pg_attribute AS a ON a.attrelid = c.oid AND a.attnum > 0 AND NOT a.attisdropped
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','v','m','f')
          AND has_column_privilege(r.rolname, c.oid, a.attnum, 'SELECT')),
    'writableTables', (SELECT coalesce(json_agg(n.nspname || '.' || c.relname
        ORDER BY n.nspname, c.relname), '[]'::json)
        FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND c.relkind IN ('r','p','v','m','f')
          AND (has_table_privilege(r.rolname, c.oid, 'INSERT')
            OR has_table_privilege(r.rolname, c.oid, 'UPDATE')
            OR has_table_privilege(r.rolname, c.oid, 'DELETE')
            OR has_table_privilege(r.rolname, c.oid, 'TRUNCATE')
            OR has_table_privilege(r.rolname, c.oid, 'REFERENCES')
            OR has_any_column_privilege(r.rolname, c.oid, 'INSERT')
            OR has_any_column_privilege(r.rolname, c.oid, 'UPDATE')
            OR has_any_column_privilege(r.rolname, c.oid, 'REFERENCES'))),
    'accessibleSequences', (SELECT coalesce(json_agg(n.nspname || '.' || c.relname
        ORDER BY n.nspname, c.relname), '[]'::json)
        FROM pg_class AS c JOIN pg_namespace AS n ON n.oid = c.relnamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND c.relkind = 'S' AND (
            has_sequence_privilege(r.rolname, c.oid, 'USAGE') OR
            has_sequence_privilege(r.rolname, c.oid, 'SELECT') OR
            has_sequence_privilege(r.rolname, c.oid, 'UPDATE'))),
    'callableRoutines', (SELECT coalesce(json_agg(json_build_object(
        'schema', n.nspname, 'name', p.proname,
        'owner', pg_get_userbyid(p.proowner), 'securityDefiner', p.prosecdef)
        ORDER BY n.nspname, p.proname, p.oid), '[]'::json)
        FROM pg_proc AS p JOIN pg_namespace AS n ON n.oid = p.pronamespace
        WHERE n.nspname NOT LIKE 'pg_%' AND n.nspname <> 'information_schema'
          AND has_schema_privilege(r.rolname, n.oid, 'USAGE')
          AND has_function_privilege(r.rolname, p.oid, 'EXECUTE'))
)::text
FROM pg_roles AS r WHERE r.rolname = '{ROLE}';
"""

BOUNDARY_AUDIT_SQL = """SELECT json_build_object(
    'ownerLogin', owner.rolcanlogin,
    'ownerSuperuser', owner.rolsuper,
    'ownerCreateRole', owner.rolcreaterole,
    'ownerCreateDb', owner.rolcreatedb,
    'ownerReplication', owner.rolreplication,
    'ownerBypassRls', owner.rolbypassrls,
    'ownerMemberships', (SELECT coalesce(json_agg(g.rolname ORDER BY g.rolname), '[]'::json)
        FROM pg_auth_members AS m JOIN pg_roles AS g ON g.oid = m.roleid
        WHERE m.member = owner.oid),
    'ownerInboundMemberships', (SELECT count(*) FROM pg_auth_members AS m
        WHERE m.roleid = owner.oid),
    'schemaOwner', pg_get_userbyid(n.nspowner),
    'schemaAcl', (SELECT coalesce(json_agg(
        (CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END)
        || ':' || a.privilege_type ORDER BY a.grantee, a.privilege_type), '[]'::json)
        FROM aclexplode(coalesce(n.nspacl, acldefault('n', n.nspowner))) AS a),
    'functionCount', (SELECT count(*) FROM pg_proc WHERE pronamespace = n.oid),
    'functionOwner', pg_get_userbyid(p.proowner),
    'functionSecurityDefiner', p.prosecdef,
    'functionVolatility', p.provolatile,
    'functionParallel', p.proparallel,
    'functionLeakproof', p.proleakproof,
    'functionSource', p.prosrc,
    'functionSettings', p.proconfig,
    'functionAcl', (SELECT coalesce(json_agg(
        (CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END)
        || ':' || a.privilege_type ORDER BY a.grantee, a.privilege_type), '[]'::json)
        FROM aclexplode(coalesce(p.proacl, acldefault('f', p.proowner))) AS a),
    'viewOwner', pg_get_userbyid(v.relowner),
    'viewSecurityBarrier', coalesce('security_barrier=true' = ANY(v.reloptions), false),
    'viewDefinition', pg_get_viewdef(v.oid, true),
    'viewAcl', (SELECT coalesce(json_agg(
        (CASE WHEN a.grantee = 0 THEN 'PUBLIC' ELSE pg_get_userbyid(a.grantee) END)
        || ':' || a.privilege_type ORDER BY a.grantee, a.privilege_type), '[]'::json)
        FROM aclexplode(coalesce(v.relacl, acldefault('r', v.relowner))) AS a)
)::text
FROM pg_roles AS owner
JOIN pg_namespace AS n ON n.nspname = 'justup_feed'
JOIN pg_proc AS p ON p.pronamespace = n.oid AND p.proname = 'capture_cdc_snapshot_cutoff'
    AND p.pronargs = 0
JOIN pg_class AS v ON v.relnamespace = n.oid AND v.relname = 'uploaded_description'
    AND v.relkind = 'v'
WHERE owner.rolname = 'justup_feed_cutoff_owner';
"""


def psql(sql: str) -> str:
    try:
        result = subprocess.run(
            PSQL, input=sql, text=True, capture_output=True, timeout=120, check=False
        )
    except (OSError, subprocess.TimeoutExpired):
        raise RoleError("Protected PostgreSQL command failed.") from None
    if result.returncode != 0:
        # psql diagnostics may include the statement containing the password.
        raise RoleError("Protected PostgreSQL command failed; inspect host-private logs.")
    return result.stdout.strip()


def validate_audit(value: object, boundary: object, *, login: bool = True) -> dict[str, object]:
    if not isinstance(value, dict) or not isinstance(boundary, dict):
        raise RoleError("Role audit returned invalid evidence.")
    expected_columns = CORE_COLUMNS | (TAXONOMY_COLUMNS if value.get("taxonomyCount") == 4 else {})
    expected_column_names = {
        f"public.{table}.{column}"
        for table, columns in expected_columns.items()
        for column in columns
    } | {
        f"justup_feed.uploaded_description.{column}"
        for column in ("posting_id", "locale", "hash", "html")
    }
    expected = {
        "login": login,
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
        "memberships": [],
        "schemaUsage": ["justup_feed", "public"],
        "writableSchemas": [],
        "readableTables": [VIEW],
        "writableTables": [],
        "accessibleSequences": [],
    }
    if value.get("taxonomyCount") not in (0, 4):
        raise RoleError("Taxonomy display schema is partial.")
    if any(value.get(key) != item for key, item in expected.items()):
        raise RoleError("Feed role has unexpected attributes or privileges.")
    if set(value.get("readableColumns") or []) != expected_column_names:
        raise RoleError("Feed role has unexpected readable columns.")
    callable_routines = value.get("callableRoutines")
    if not isinstance(callable_routines, list):
        raise RoleError("Feed role routine privileges are unknown.")
    required_cutoff = False
    for routine in callable_routines:
        if not isinstance(routine, dict) or set(routine) != {
            "schema",
            "name",
            "owner",
            "securityDefiner",
        }:
            raise RoleError("Feed role routine privileges are invalid.")
        fixed_cutoff = routine == {
            "schema": "justup_feed",
            "name": "capture_cdc_snapshot_cutoff",
            "owner": OWNER,
            "securityDefiner": True,
        }
        if fixed_cutoff:
            required_cutoff = True
        elif routine["securityDefiner"] is not False:
            raise RoleError("Feed role can execute an unexpected privileged routine.")
    if not required_cutoff:
        raise RoleError("Feed role cannot execute its fixed cutoff routine.")
    if set(value.get("settings") or []) != ROLE_SETTINGS:
        raise RoleError("Feed role defaults do not match the bounded reader contract.")
    boundary_expected = {
        "ownerLogin": False,
        "ownerSuperuser": False,
        "ownerCreateRole": False,
        "ownerCreateDb": False,
        "ownerReplication": False,
        "ownerBypassRls": False,
        "ownerMemberships": ["pg_read_all_stats"],
        "ownerInboundMemberships": 0,
        "schemaOwner": OWNER,
        "schemaAcl": [f"{OWNER}:CREATE", f"{OWNER}:USAGE", f"{ROLE}:USAGE"],
        "functionCount": 1,
        "functionOwner": OWNER,
        "functionSecurityDefiner": True,
        "functionVolatility": "v",
        "functionParallel": "u",
        "functionLeakproof": False,
        "functionSource": CUTOFF_FUNCTION_BODY,
        "functionSettings": ["search_path=pg_catalog, pg_temp"],
        "functionAcl": [f"{OWNER}:EXECUTE", f"{ROLE}:EXECUTE"],
        "viewOwner": "crawler",
        "viewSecurityBarrier": True,
    }
    if any(boundary.get(key) != item for key, item in boundary_expected.items()):
        raise RoleError("Feed function or view boundary has drifted.")
    view_acl = set(boundary.get("viewAcl") or [])
    owner_view_acl = {
        "crawler:DELETE",
        "crawler:INSERT",
        "crawler:REFERENCES",
        "crawler:SELECT",
        "crawler:TRIGGER",
        "crawler:TRUNCATE",
        "crawler:UPDATE",
    }
    if view_acl not in (
        owner_view_acl | {f"{ROLE}:SELECT"},
        owner_view_acl | {"crawler:MAINTAIN", f"{ROLE}:SELECT"},
    ):
        raise RoleError("Uploaded-description view privileges have drifted.")
    view_definition = boundary.get("viewDefinition")
    if not isinstance(view_definition, str) or (
        re.sub(r"\s+", " ", view_definition)
        .strip()
        .replace("public.", "")
        .replace("descriptions.", "")
        != VIEW_DEFINITION
    ):
        raise RoleError("Uploaded-description view definition has drifted.")
    return {
        "role": ROLE,
        "taxonomyTables": value["taxonomyCount"],
        "readableColumns": sorted(expected_column_names),
        "uploadedDescriptionView": VIEW,
        "callableRoutineCount": len(callable_routines),
    }


def audit(*, login: bool = True) -> tuple[dict[str, object], str, str]:
    role_raw = psql(AUDIT_SQL)
    boundary_raw = psql(BOUNDARY_AUDIT_SQL)
    try:
        value = json.loads(role_raw)
        boundary = json.loads(boundary_raw)
    except json.JSONDecodeError as exc:
        raise RoleError("Role audit returned invalid JSON.") from exc
    return validate_audit(value, boundary, login=login), role_raw, boundary_raw


def activate_sql(role_raw: str, boundary_raw: str) -> str:
    role_digest = hashlib.md5(role_raw.encode("utf-8")).hexdigest()
    boundary_digest = hashlib.md5(boundary_raw.encode("utf-8")).hexdigest()
    role_query = AUDIT_SQL.strip().removesuffix(";")
    boundary_query = BOUNDARY_AUDIT_SQL.strip().removesuffix(";")
    return f"""BEGIN ISOLATION LEVEL SERIALIZABLE;
DO $role_activation$
BEGIN
    IF md5(({role_query})) IS DISTINCT FROM '{role_digest}'
       OR md5(({boundary_query})) IS DISTINCT FROM '{boundary_digest}' THEN
        RAISE EXCEPTION 'Feed role boundary changed before activation';
    END IF;
END $role_activation$;
ALTER ROLE {ROLE} LOGIN;
COMMIT;
"""


def install(password_file: Path) -> dict[str, object]:
    password = read_password(password_file)
    state = psql(f"SELECT rolcanlogin FROM pg_roles WHERE rolname = '{ROLE}';")
    if state not in ("", "t", "f"):
        raise RoleError("Feed role state is invalid.")
    if state == "t":
        try:
            result = audit()[0]
            verifier = psql(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{ROLE}';")
            if not password_matches_scram(password, verifier):
                raise RoleError("Protected feed credential does not match the login.")
            return result
        except RoleError:
            psql(f"ALTER ROLE {ROLE} NOLOGIN;")
            raise
    if state == "":
        assert_backup_restore_fresh()
        psql(install_sql(password))
    _result, role_raw, boundary_raw = audit(login=False)
    verifier = psql(f"SELECT rolpassword FROM pg_authid WHERE rolname = '{ROLE}';")
    if not password_matches_scram(password, verifier):
        raise RoleError("Protected feed credential does not match the staged login.")
    try:
        assert_backup_restore_fresh()
        psql(activate_sql(role_raw, boundary_raw))
        return audit()[0]
    except RoleError:
        psql(f"ALTER ROLE {ROLE} NOLOGIN;")
        raise


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("install", "audit", "attest-restore", "verify-stage"))
    parser.add_argument("--password-file", type=Path)
    parser.add_argument("--stage", type=Path)
    parser.add_argument("--installer-sha256")
    parser.add_argument("--conformance-sha256")
    args = parser.parse_args()
    if os.geteuid() != 0:
        raise RoleError("This command requires the protected root operator.")
    if args.action == "verify-stage":
        if args.password_file is not None or args.stage is None:
            raise RoleError("Stage verification needs the protected stage only.")
        verify_stage(args.stage, args.installer_sha256 or "", args.conformance_sha256 or "")
        print('{"verified":true}')
        return 0
    if args.stage is not None or args.installer_sha256 or args.conformance_sha256:
        raise RoleError("Role operations do not accept stage verification inputs.")
    if args.action == "attest-restore":
        if args.password_file is not None:
            raise RoleError("Restore attestation never reads a password file.")
        result = attest_restore()
        print(json.dumps(result, separators=(",", ":"), sort_keys=True))
        return 0
    if args.action == "install":
        if args.password_file is None:
            raise RoleError("Install requires a protected password file.")
        assert_ingress_stable()
        result = install(args.password_file)
    elif args.password_file is not None:
        raise RoleError("Audit never reads a password file.")
    else:
        assert_ingress_stable()
        result = audit()[0]
    print(json.dumps(result, separators=(",", ":"), sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except RoleError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
