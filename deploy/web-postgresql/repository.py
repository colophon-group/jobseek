#!/usr/bin/env python3
"""Encrypted full web-schema backups and isolated restore proof on the backup host."""

from __future__ import annotations

import argparse
import contextlib
import fcntl
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

import database

ROOT = Path("/run/jobseek-web-postgresql-backup")
STATUS = Path("/var/lib/jobseek-backup/status")
URL = Path("/etc/jobseek-backup/web-postgresql.database-url")
ENV = Path("/etc/jobseek-backup/web-postgresql.env")
TAG = "jobseek-web-postgresql-v4"
HOST = "jobseek-web-postgresql"


def repository_environment() -> dict:
    metadata = ENV.lstat()
    if (
        not ENV.is_file()
        or ENV.is_symlink()
        or metadata.st_uid != 0
        or metadata.st_mode & 0o777 != 0o600
    ):
        raise database.DatabaseError("unsafe backup environment file")
    env = os.environ.copy()
    allowed = {"RESTIC_REPOSITORY", "RESTIC_PASSWORD", "RESTIC_SFTP_COMMAND"}
    for line in ENV.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        if key not in allowed:
            continue
        if value.startswith(('"', "'")):
            parsed = shlex.split(value)
            if len(parsed) != 1:
                raise database.DatabaseError("invalid quoted backup setting")
            value = parsed[0]
        env[key] = value
    if any(not env.get(key) for key in allowed):
        raise database.DatabaseError("required encrypted repository setting is missing")
    return env


def restic(*arguments: str, timeout: int = 3600) -> str:
    env = repository_environment()
    command = [
        "restic",
        "--cache-dir",
        str(ROOT / "restic-cache"),
        "--retry-lock=2m",
        "-o",
        "sftp.command=" + env["RESTIC_SFTP_COMMAND"],
        *arguments,
    ]
    p = subprocess.run(
        command, env=env, capture_output=True, text=True, timeout=timeout
    )
    if p.returncode:
        raise database.DatabaseError("encrypted repository operation failed")
    return p.stdout


def status(name: str, value: dict):
    STATUS.mkdir(mode=0o700, parents=True, exist_ok=True)
    database.atomic_json(STATUS / name, value)


def clean_packets():
    packets = []
    for path in ROOT.iterdir():
        if not re.fullmatch(r"packet-[0-9]+", path.name):
            continue
        metadata = path.lstat()
        if path.is_symlink() or not path.is_dir() or metadata.st_uid != os.getuid():
            raise database.DatabaseError("unsafe backup staging packet")
        packets.append(path)
    for index, path in enumerate(
        sorted(packets, key=lambda p: p.stat().st_mtime, reverse=True)
    ):
        if index >= 2 or time.time() - path.stat().st_mtime > 48 * 3600:
            shutil.rmtree(path)


def reap_restore_resources():
    # The held deployment/service locks prove no other protected restore is
    # active. A killed parent can leave only these networkless owned drills.
    listed = subprocess.run(
        [
            "docker",
            "ps",
            "--all",
            "--filter",
            "label=jobseek.backup.service=web-postgresql-full-restore",
            "--format",
            "{{.ID}} {{.Names}}",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if listed.returncode:
        raise database.DatabaseError("cannot inspect isolated restore resources")
    resources = []
    for line in listed.stdout.splitlines():
        fields = line.split()
        if (
            len(fields) != 2
            or not re.fullmatch(r"[0-9a-f]{12,64}", fields[0])
            or not re.fullmatch(r"jobseek-web-postgresql-drill-[0-9]+", fields[1])
        ):
            raise database.DatabaseError(
                "unexpected resource uses the restore ownership label"
            )
        resources.append(fields[0])
    for resource in resources:
        if subprocess.run(
            ["docker", "rm", "--force", resource], capture_output=True, timeout=60
        ).returncode:
            raise database.DatabaseError("orphaned isolated restore cleanup failed")
    for path in ROOT.iterdir():
        if re.fullmatch(r"restore-[a-z0-9_]{8}", path.name):
            metadata = path.lstat()
            if path.is_symlink() or not path.is_dir() or metadata.st_uid != os.getuid():
                raise database.DatabaseError("unsafe isolated restore directory")
            shutil.rmtree(path)


def backup(url_file: Path) -> dict:
    previous_path = STATUS / "web-postgresql.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else {}
    started = time.time()
    record = {
        "service": "web-postgresql",
        "schema_version": 4,
        "success": False,
        "attempt_unix": int(started),
        "last_success_unix": previous.get("last_success_unix", 0),
        "last_success_at": previous.get("last_success_at"),
        "last_success_details": previous.get("last_success_details", {}),
    }
    url = database.private_file(url_file)
    packet = ROOT / ("packet-" + str(int(started)))
    client = database.Client(url, ROOT)
    try:
        manifest = database.dump(client, packet)
        restic("backup", "--host", HOST, "--tag", TAG, "--json", str(packet))
        # Retention is scoped to this packet generation. Physical pruning is
        # separate so a six-hour backup cannot monopolize the shared repository.
        restic(
            "forget",
            "--host",
            HOST,
            "--tag",
            TAG,
            "--group-by",
            "host,tags",
            "--keep-daily",
            "30",
            "--keep-weekly",
            "12",
            "--keep-monthly",
            "12",
        )
        snapshots = json.loads(
            restic(
                "snapshots",
                "--host",
                HOST,
                "--tag",
                TAG,
                "--group-by",
                "host,tags",
                "--latest",
                "1",
                "--json",
            )
        )
        if len(snapshots) != 1:
            raise database.DatabaseError("new full backup snapshot is ambiguous")
        snapshot = snapshots[0]
        if not re.fullmatch(r"[0-9a-f]{64}", snapshot["id"]):
            raise database.DatabaseError("invalid encrypted backup snapshot identity")
        details = {
            "packet_version": 4,
            "source_kind": "supabase"
            if "supabase" in urlsplit(url).hostname
            else "hetzner",
            "archive_bytes": manifest["archive_bytes"],
            "archive_sha256": manifest["archive_sha256"],
            "table_count": len(manifest["catalog"]["tables"]),
            "row_count": sum(
                x["rows"] for x in manifest["fingerprints"]["tables"].values()
            ),
            "repository_snapshot_id": snapshot["id"],
        }
        record.update(
            success=True,
            last_success_unix=int(time.time()),
            last_success_details=details,
        )
        from datetime import datetime, timezone

        record["last_success_at"] = datetime.now(timezone.utc).isoformat()
        shutil.rmtree(packet)
    except BaseException:
        record["error"] = "complete_web_database_backup_failed"
        raise
    finally:
        client.close()
        record["duration_seconds"] = round(time.time() - started, 3)
        status("web-postgresql.json", record)
        text = (
            "# TYPE jobseek_backup_last_attempt_unixtime gauge\n"
            f'jobseek_backup_last_attempt_unixtime{{service="web-postgresql"}} {record["attempt_unix"]}\n'
            "# TYPE jobseek_backup_last_attempt_success gauge\n"
            f'jobseek_backup_last_attempt_success{{service="web-postgresql"}} {int(record["success"])}\n'
            "# TYPE jobseek_backup_last_success_unixtime gauge\n"
            f'jobseek_backup_last_success_unixtime{{service="web-postgresql"}} {record["last_success_unix"]}\n'
            "# TYPE jobseek_backup_last_duration_seconds gauge\n"
            f'jobseek_backup_last_duration_seconds{{service="web-postgresql"}} {record["duration_seconds"]}\n'
        )
        temporary = STATUS / ".web-postgresql.prom.tmp"
        temporary.write_text(text)
        temporary.chmod(0o644)
        os.replace(temporary, STATUS / "web-postgresql.prom")
    return record


def restore_latest() -> dict:
    latest = json.loads((STATUS / "web-postgresql.json").read_text())
    if (
        latest.get("success") is not True
        or time.time() - latest["last_success_unix"] > 9 * 3600
    ):
        raise database.DatabaseError(
            "fresh complete backup required before restore proof"
        )
    details = latest["last_success_details"]
    if details.get("packet_version") != 4:
        raise database.DatabaseError("complete packet generation is required")
    snapshot_id = details["repository_snapshot_id"]
    if not re.fullmatch(r"[0-9a-f]{64}", snapshot_id):
        raise database.DatabaseError("invalid restore snapshot identity")
    directory = Path(tempfile.mkdtemp(prefix="restore-", dir=ROOT))
    container = "jobseek-web-postgresql-drill-" + str(os.getpid())
    created = False
    try:
        restic("restore", snapshot_id, "--target", str(directory))
        manifests = list(directory.rglob("manifest.json"))
        if len(manifests) != 1:
            raise database.DatabaseError("restored packet is ambiguous")
        packet = manifests[0].parent
        manifest = database.validate_packet(packet)
        if manifest["archive_sha256"] != details["archive_sha256"]:
            raise database.DatabaseError(
                "encrypted snapshot differs from the backed-up packet"
            )
        command = [
            "docker",
            "run",
            "--detach",
            "--name",
            container,
            "--network",
            "none",
            "--label",
            "jobseek.backup.service=web-postgresql-full-restore",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--user",
            "70:70",
            "--memory=1g",
            "--memory-swap=1g",
            "--cpus=1",
            "--pids-limit=128",
            "--tmpfs",
            "/var/lib/postgresql/data:rw,nosuid,uid=70,gid=70,mode=0700,size=768m",
            "--tmpfs",
            "/var/run/postgresql:rw,nosuid,uid=70,gid=70,size=16m",
            "--tmpfs",
            "/tmp:rw,noexec,nosuid,size=64m",
            "--mount",
            f"type=bind,src={ROOT},dst={ROOT},readonly",
            "-e",
            "POSTGRES_HOST_AUTH_METHOD=trust",
            database.IMAGE,
        ]
        p = subprocess.run(command, capture_output=True, text=True, timeout=60)
        if p.returncode:
            raise database.DatabaseError("isolated restore database did not start")
        created = True
        client = database.Client(
            "postgresql://postgres@localhost/postgres?sslmode=disable",
            ROOT,
            container=container,
        )
        try:
            for _ in range(60):
                try:
                    client.sql("SELECT 1;")
                    break
                except database.DatabaseError:
                    time.sleep(1)
            else:
                raise database.DatabaseError(
                    "isolated restore database did not become ready"
                )
            proof = database.restore(client, packet)
        finally:
            client.close()
        proof.update(
            {
                "packet_version": 4,
                "source_kind": details["source_kind"],
                "repository_snapshot_id": snapshot_id,
                "finished_unix": int(time.time()),
            }
        )
        status("web-postgresql-full-restore.json", proof)
        return proof
    finally:
        if created:
            removed = subprocess.run(
                ["docker", "rm", "--force", container], capture_output=True, timeout=60
            )
            if removed.returncode:
                raise database.DatabaseError(
                    "isolated restore container cleanup failed"
                )
        shutil.rmtree(directory)


def recover_repository():
    # Ordinary unlock removes only Restic's stale locks. Never use --remove-all.
    # Hold both consumer locks and reject any local live Restic process first.
    process = subprocess.run(["pgrep", "-x", "restic"], capture_output=True, timeout=10)
    if process.returncode != 1:
        raise database.DatabaseError(
            "repository recovery requires all local Restic processes quiet"
        )
    restic("unlock", timeout=180)
    print(json.dumps({"success": True, "stale_repository_locks_reconciled": True}))


def activate(url_file: Path):
    url = database.private_file(url_file)
    if urlsplit(url).hostname != "web-postgres.jseek.co":
        raise database.DatabaseError(
            "backup activation requires the Hetzner web database"
        )
    status(
        "web-postgresql-complete-runtime.json",
        {"state": "activating", "packet_version": 4},
    )
    # The old timer is quiesced before replacing either its credential or unit.
    for argv in [
        ["systemctl", "disable", "--now", "jobseek-web-postgresql-backup.timer"],
        ["systemctl", "stop", "jobseek-web-postgresql-backup.service"],
    ]:
        if subprocess.run(argv, capture_output=True, timeout=120).returncode:
            raise database.DatabaseError("legacy web backup could not be quiesced")
    rollback = URL.with_name("web-postgresql.supabase-rollback-url")
    if not rollback.exists():
        shutil.copyfile(URL, rollback)
        rollback.chmod(0o600)
    candidate = URL.with_name(".web-postgresql.database-url.candidate")
    candidate.write_text(url)
    candidate.chmod(0o600)
    os.replace(candidate, URL)
    release = Path(__file__).resolve().parent
    unit = Path("/etc/systemd/system/jobseek-web-postgresql-backup.service")
    unit.write_text(f"""[Unit]
Description=Jobseek complete web PostgreSQL encrypted backup
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target
[Service]
Type=oneshot
User=root
Group=root
ExecStart=/usr/bin/python3 {release}/repository.py backup
TimeoutStartSec=2h
RuntimeDirectory=jobseek-web-postgresql-backup
RuntimeDirectoryMode=0700
RuntimeDirectoryPreserve=yes
UMask=0077
NoNewPrivileges=true
PrivateTmp=true
PrivateDevices=true
ProtectHome=true
ProtectSystem=strict
ReadWritePaths=/var/lib/jobseek-backup /run
RestrictAddressFamilies=AF_UNIX AF_INET AF_INET6
[Install]
WantedBy=multi-user.target
""")
    unit.chmod(0o644)
    subprocess.run(["systemctl", "daemon-reload"], check=True, capture_output=True)
    # Under the held service lock, prove the new credential and encrypted restore
    # before scheduling it. Failure leaves the new timer disabled, never stale-green.
    backup(URL)
    proof = restore_latest()
    subprocess.run(
        ["systemctl", "reset-failed", "jobseek-web-postgresql-backup.service"],
        check=True,
        capture_output=True,
    )
    subprocess.run(
        ["systemctl", "enable", "--now", "jobseek-web-postgresql-backup.timer"],
        check=True,
        capture_output=True,
    )
    status(
        "web-postgresql-complete-runtime.json",
        {
            "state": "active",
            "packet_version": 4,
            "archive_sha256": proof["archive_sha256"],
        },
    )
    print(
        json.dumps(
            {
                "success": True,
                "full_backup_activated": True,
                "table_count": proof["table_count"],
                "row_count": proof["row_count"],
            }
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action", choices=["backup", "restore", "recover-repository", "activate"]
    )
    parser.add_argument("--url-file", type=Path, default=URL)
    args = parser.parse_args()
    if os.getuid() != 0:
        raise database.DatabaseError("protected root backup runtime required")
    os.umask(0o077)
    ROOT.mkdir(mode=0o700, parents=True, exist_ok=True)
    with contextlib.ExitStack() as stack:
        for name in [
            "jobseek-backup-deployment",
            "jobseek-data-backup-web-postgresql",
            *(
                ["jobseek-data-backup-typesense"]
                if args.action == "recover-repository"
                else []
            ),
        ]:
            lock = stack.enter_context(Path("/run/" + name + ".lock").open("w"))
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        clean_packets()
        if args.action != "recover-repository":
            reap_restore_resources()
        if args.action == "backup":
            result = backup(args.url_file)
            details = result["last_success_details"]
            print(
                json.dumps(
                    {
                        k: details[k]
                        for k in [
                            "packet_version",
                            "table_count",
                            "row_count",
                            "archive_bytes",
                        ]
                    }
                )
            )
        elif args.action == "restore":
            result = restore_latest()
            print(
                json.dumps(
                    {
                        k: result[k]
                        for k in [
                            "success",
                            "table_count",
                            "row_count",
                            "archive_sha256",
                        ]
                    }
                )
            )
        elif args.action == "activate":
            activate(args.url_file)
        else:
            recover_repository()


if __name__ == "__main__":
    try:
        main()
    except (database.DatabaseError, OSError, ValueError, subprocess.SubprocessError):
        print(
            json.dumps(
                {"success": False, "error": "complete_web_backup_operation_failed"}
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
