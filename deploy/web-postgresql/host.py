#!/usr/bin/env python3
"""Root-owned PostgreSQL 17 host deployment and complete backup operations."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import ipaddress
import json
import os
import re
import signal
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import database

CONFIG = Path("/etc/jobseek-web-postgresql")
STATE = Path("/var/lib/jobseek-web-postgresql")
RUNTIME = Path("/run/jobseek-web-postgresql")
CONTAINER = "jobseek-web-postgresql"
UNIT = "jobseek-web-postgresql.service"
DATA = Path("/mnt/jobseek-postings-postgresql/web-postgresql")
DOMAIN = "web-postgres.jseek.co"
PORT = 5433


def run(
    argv: list[str],
    *,
    input_text: str | None = None,
    env: dict | None = None,
    timeout: int = 600,
) -> str:
    p = subprocess.run(
        argv, input=input_text, env=env, capture_output=True, text=True, timeout=timeout
    )
    if p.returncode:
        # Output may contain SQL or provider URLs: retain it only in root state.
        STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
        error = STATE / "operation-error.private.log"
        error.write_text(p.stderr)
        error.chmod(0o600)
        raise database.DatabaseError("protected host command failed")
    return p.stdout.strip()


def write(path: Path, value: str, *, mode: int = 0o600, owner: int | None = None):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_name("." + path.name + ".candidate")
    with tmp.open("w") as stream:
        os.chmod(tmp, mode)
        stream.write(value)
    if owner is not None:
        os.chown(tmp, owner, owner)
    os.replace(tmp, path)


def config() -> dict:
    return json.loads(database.private_file(CONFIG / "deployment.json"))


def owner_url(cfg: dict, *, name: str = "jobseek_web") -> str:
    password = cfg["owner_password"]
    if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", password):
        raise database.DatabaseError("invalid protected owner password")
    return f"postgresql://jobseek_web_owner:{password}@{DOMAIN}:{PORT}/{name}?sslmode=verify-full"


def admin(sql: str) -> str:
    return run(
        [
            "docker",
            "exec",
            "--interactive",
            "--user",
            "70:70",
            CONTAINER,
            "psql",
            "-X",
            "-w",
            "-q",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            "-U",
            "postgres",
            "-p",
            str(PORT),
            "-d",
            "postgres",
        ],
        input_text=sql,
    )


def cloudflare(cfg: dict, method: str, endpoint: str, data=None):
    request = urllib.request.Request(
        "https://api.cloudflare.com/client/v4/" + endpoint,
        method=method,
        headers={
            "Authorization": "Bearer " + cfg["cloudflare_token"],
            "Content-Type": "application/json",
        },
        data=json.dumps(data).encode() if data is not None else None,
    )
    with urllib.request.urlopen(request, timeout=30) as response:
        body = json.load(response)
    if body.get("success") is not True:
        raise database.DatabaseError("protected DNS operation failed")
    return body["result"]


def prepare_dns(cfg: dict):
    address = str(ipaddress.IPv4Address(cfg["public_ipv4"]))
    zones = cloudflare(cfg, "GET", "zones?name=jseek.co")
    if len(zones) != 1:
        raise database.DatabaseError("database DNS zone is ambiguous")
    zone = zones[0]["id"]
    records = cloudflare(cfg, "GET", f"zones/{zone}/dns_records?name={DOMAIN}")
    if records:
        if (
            len(records) != 1
            or records[0]["type"] != "A"
            or records[0]["content"] != address
            or records[0]["proxied"]
        ):
            raise database.DatabaseError(
                "refusing to replace an existing database DNS record"
            )
    else:
        cloudflare(
            cfg,
            "POST",
            f"zones/{zone}/dns_records",
            {
                "type": "A",
                "name": DOMAIN,
                "content": address,
                "proxied": False,
                "ttl": 60,
            },
        )
    write(
        CONFIG / "cloudflare.ini",
        "dns_cloudflare_api_token = " + cfg["cloudflare_token"] + "\n",
    )
    run(
        [
            "certbot",
            "certonly",
            "--non-interactive",
            "--agree-tos",
            "--register-unsafely-without-email",
            "--dns-cloudflare",
            "--dns-cloudflare-credentials",
            str(CONFIG / "cloudflare.ini"),
            "--dns-cloudflare-propagation-seconds",
            "30",
            "--cert-name",
            DOMAIN,
            "-d",
            DOMAIN,
        ],
        timeout=300,
    )
    renew_tls()


def renew_tls():
    tls = CONFIG / "tls"
    tls.mkdir(mode=0o700, exist_ok=True)
    os.chown(tls, 70, 70)
    source = Path("/etc/letsencrypt/live") / DOMAIN
    for src, dst in [("fullchain.pem", "server.crt"), ("privkey.pem", "server.key")]:
        write(tls / dst, (source / src).read_text(), mode=0o400, owner=70)


def prepare():
    cfg = json.loads(sys.stdin.read())
    expected = {
        "owner_password",
        "auditor_password",
        "worker_password",
        "cloudflare_token",
        "public_ipv4",
        "crawler_private_ipv4",
        "crawler_public_ipv4",
        "source_url",
        "revision",
    }
    if set(cfg) != expected or not re.fullmatch(r"[0-9a-f]{40}", cfg["revision"]):
        raise database.DatabaseError("invalid deployment payload")
    for key in ["owner_password", "auditor_password", "worker_password"]:
        if not re.fullmatch(r"[A-Za-z0-9_-]{43,128}", cfg[key]):
            raise database.DatabaseError("invalid protected database password")
    private = ipaddress.IPv4Address(cfg["crawler_private_ipv4"])
    crawler_public = ipaddress.IPv4Address(cfg["crawler_public_ipv4"])
    if not private.is_private:
        raise database.DatabaseError("worker address must be private")
    source = database.connection_environment(cfg["source_url"])
    if not source["PGHOST"].endswith((".supabase.com", ".supabase.co")):
        raise database.DatabaseError("prepare requires the retiring Supabase source")
    mount = DATA.parent
    if not mount.is_dir() or mount.stat().st_dev == Path("/").stat().st_dev:
        raise database.DatabaseError("dedicated PostgreSQL data volume is not mounted")
    if shutil.disk_usage(mount).free < 5 * 1024**3:
        raise database.DatabaseError(
            "database volume has insufficient recovery headroom"
        )
    available = (
        int(
            next(
                x.split()[1]
                for x in Path("/proc/meminfo").read_text().splitlines()
                if x.startswith("MemAvailable:")
            )
        )
        * 1024
    )
    if available < 2 * 1024**3:
        raise database.DatabaseError("insufficient host memory headroom")
    if (CONFIG / "deployment.json").exists():
        previous = config()
        for key in [
            "owner_password",
            "auditor_password",
            "worker_password",
            "public_ipv4",
            "crawler_private_ipv4",
            "crawler_public_ipv4",
        ]:
            if previous[key] != cfg[key]:
                raise database.DatabaseError(
                    "existing database identity cannot be rotated by prepare"
                )
    else:
        with socket.socket() as probe:
            try:
                probe.bind(("0.0.0.0", PORT))
            except OSError:
                raise database.DatabaseError(
                    "database port is already occupied"
                ) from None
    CONFIG.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    database.atomic_json(CONFIG / "deployment.json", cfg)
    run(["apt-get", "update"], timeout=300)
    env = os.environ.copy()
    env["DEBIAN_FRONTEND"] = "noninteractive"
    run(
        [
            "apt-get",
            "install",
            "--yes",
            "--no-install-recommends",
            "certbot",
            "python3-certbot-dns-cloudflare",
        ],
        env=env,
    )
    prepare_dns(cfg)
    DATA.mkdir(mode=0o700, exist_ok=True)
    os.chown(DATA, 70, 70)
    if not (CONFIG / "admin-password").exists():
        import secrets

        write(
            CONFIG / "admin-password",
            secrets.token_urlsafe(48) + "\n",
            mode=0o400,
            owner=70,
        )
    write(
        CONFIG / "pg_hba.conf",
        f"""local all postgres trust
hostssl all jobseek_web_owner 0.0.0.0/0 scram-sha-256
hostssl all jobseek_migration_auditor 0.0.0.0/0 scram-sha-256
hostssl all jobseek_narrowed_worker {private}/32 scram-sha-256
hostssl all jobseek_narrowed_worker {crawler_public}/32 scram-sha-256
host all all 0.0.0.0/0 reject
host all all ::/0 reject
""",
        mode=0o444,
    )
    write(
        CONFIG / "postgresql.conf",
        f"""listen_addresses='0.0.0.0'
port={PORT}
ssl=on
ssl_cert_file='/run/web-tls/server.crt'
ssl_key_file='/run/web-tls/server.key'
ssl_min_protocol_version='TLSv1.2'
hba_file='/run/web-hba.conf'
password_encryption='scram-sha-256'
max_connections=120
shared_buffers='128MB'
effective_cache_size='512MB'
work_mem='4MB'
maintenance_work_mem='64MB'
max_wal_size='1GB'
min_wal_size='80MB'
log_min_messages=warning
log_min_error_statement=panic
log_statement=none
timezone='UTC'
""",
        mode=0o444,
    )
    run(["docker", "pull", database.IMAGE], timeout=300)
    release = Path(__file__).resolve().parent
    write(
        Path("/usr/local/sbin/jobseek-web-postgresql-launch"),
        f"""#!/bin/sh
set -eu
exec docker run --rm --name {CONTAINER} --label jobseek.service=web-postgresql \\
  --network host --user 70:70 --read-only --cap-drop=ALL --security-opt=no-new-privileges \\
  --memory=1536m --memory-swap=1536m --cpus=1 --pids-limit=256 --shm-size=256m \\
  --log-driver=json-file --log-opt=max-size=20m --log-opt=max-file=3 \\
  --tmpfs /tmp:rw,noexec,nosuid,size=64m --tmpfs /var/run/postgresql:rw,nosuid,uid=70,gid=70,size=16m \\
  --mount type=bind,src={DATA},dst=/var/lib/postgresql/data \\
  --mount type=bind,src={CONFIG}/tls,dst=/run/web-tls,readonly \\
  --mount type=bind,src={CONFIG}/admin-password,dst=/run/admin-password,readonly \\
  --mount type=bind,src={CONFIG}/pg_hba.conf,dst=/run/web-hba.conf,readonly \\
  --mount type=bind,src={CONFIG}/postgresql.conf,dst=/run/web-postgresql.conf,readonly \\
  -e POSTGRES_PASSWORD_FILE=/run/admin-password -e POSTGRES_INITDB_ARGS=--auth-host=scram-sha-256 \\
  {database.IMAGE} postgres -c config_file=/run/web-postgresql.conf
""",
        mode=0o755,
    )
    write(
        Path("/usr/local/sbin/jobseek-web-postgresql"),
        f'#!/bin/sh\nexec python3 {release}/host.py "$@"\n',
        mode=0o755,
    )
    write(
        Path("/etc/letsencrypt/renewal-hooks/deploy/jobseek-web-postgresql"),
        "#!/bin/sh\nset -eu\n/usr/local/sbin/jobseek-web-postgresql renew-tls\n"
        "systemctl reload jobseek-web-postgresql.service\n",
        mode=0o755,
    )
    shutil.copyfile(release / UNIT, Path("/etc/systemd/system") / UNIT)
    os.chmod(Path("/etc/systemd/system") / UNIT, 0o644)
    run(["systemctl", "daemon-reload"])
    run(["systemctl", "enable", "--now", UNIT])
    for _ in range(60):
        try:
            admin("SELECT 1;")
            break
        except database.DatabaseError:
            time.sleep(1)
    else:
        raise database.DatabaseError("PostgreSQL did not become ready")
    for role, key in [
        ("jobseek_web_owner", "owner_password"),
        ("jobseek_migration_auditor", "auditor_password"),
        ("jobseek_narrowed_worker", "worker_password"),
    ]:
        # Password alphabet is validated before interpolating into protected SQL.
        admin(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') THEN "
            f"CREATE ROLE {role} LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS "
            f"PASSWORD '{cfg[key]}'; END IF; END $$;"
        )
    admin(
        "ALTER ROLE jobseek_migration_auditor SET default_transaction_read_only=on; "
        "ALTER ROLE jobseek_narrowed_worker CONNECTION LIMIT 6;"
    )
    exists = admin("SELECT count(*) FROM pg_database WHERE datname='jobseek_web';")
    if exists == "0":
        admin("CREATE DATABASE jobseek_web OWNER jobseek_web_owner TEMPLATE template0;")
    # Expose only the TLS/SCRAM endpoint after the server and roles are ready.
    run(["ufw", "allow", f"{PORT}/tcp", "comment", "jobseek-web-postgresql TLS"])
    write(CONFIG / "source.url", cfg["source_url"])
    write(CONFIG / "owner.url", owner_url(cfg))
    print(
        json.dumps(
            {"success": True, "database_prepared": True, "revision": cfg["revision"]}
        )
    )


def copy_database(*, rehearsal: bool):
    cfg = config()
    source = CONFIG / "source.url"
    if rehearsal:
        name = "jobseek_rehearsal_" + str(int(time.time()))
        admin(
            f"CREATE DATABASE {database.identifier(name)} OWNER jobseek_web_owner TEMPLATE template0;"
        )
        target_file = RUNTIME / (name + ".url")
        write(target_file, owner_url(cfg, name=name))
    else:
        name = "jobseek_web"
        target_file = CONFIG / "owner.url"
        if (STATE / "copy.json").exists():
            raise database.DatabaseError(
                "a completed production copy cannot be overwritten"
            )
        if not (STATE / "rehearsal.json").exists() or not json.loads(
            (STATE / "rehearsal.json").read_text()
        ).get("success"):
            raise database.DatabaseError(
                "successful complete restore rehearsal is required"
            )
    src = database.Client(database.private_file(source), RUNTIME)
    dst = database.Client(database.private_file(target_file), RUNTIME)
    packet = RUNTIME / ("packet-" + str(int(time.time())))
    fenced = False
    try:
        if not rehearsal:
            with src.snapshot() as (query, _):
                workers = query(
                    "SELECT to_json(count(*)) FROM pg_stat_activity "
                    "WHERE usename='jobseek_narrowed_worker'"
                )
            if workers:
                raise database.DatabaseError(
                    "pause the source Narrowed worker before copying"
                )
            occupied = dst.sql(
                "BEGIN READ ONLY; SELECT count(*) FROM pg_tables "
                "WHERE schemaname IN ('public','drizzle'); COMMIT;"
            )
            if occupied != "0":
                raise database.DatabaseError(
                    "production destination is already populated"
                )
            # The driver pauses the worker before this operation. Every source
            # DML path is fenced at the database boundary, including old deploys.
            database.source_fence(src)
            fenced = True
            database.atomic_json(
                STATE / "source-fence.json",
                {"fenced": True, "created_unix": int(time.time())},
            )
        database.dump(src, packet)
        result = database.restore(dst, packet)
        with dst.snapshot() as (query, _):
            dst_catalog = database.inventory(query)
        result.update(
            {
                "revision": cfg["revision"],
                "created_unix": int(time.time()),
                "source_fenced": fenced,
                "database": name,
                "packet": str(packet),
                "catalog_tables": len(dst_catalog["tables"]),
            }
        )
        database.atomic_json(
            STATE / ("rehearsal.json" if rehearsal else "copy.json"), result
        )
        print(
            json.dumps(
                {k: v for k, v in result.items() if k not in ("packet", "database")}
            )
        )
    except BaseException:
        # During this operation no protected consumer has been switched. If the
        # copy fails, restore source writes before handing control back.
        if fenced and not (STATE / "copy.json").exists():
            database.source_fence(src, remove=True)
            database.atomic_json(
                STATE / "source-fence.json", {"fenced": False, "copy_failed": True}
            )
        raise
    finally:
        src.close()
        dst.close()
        if rehearsal:
            admin(f"DROP DATABASE {database.identifier(name)} WITH (FORCE);")
            target_file.unlink(missing_ok=True)
            if packet.exists():
                shutil.rmtree(packet)
    if not rehearsal:
        # Hold the shared web deployment concurrency group while the authorized
        # coordinator updates all consumers. Partial external changes must not
        # automatically reopen source writes.
        for _ in range(600):
            if (STATE / "consumers-configured.json").exists():
                return
            time.sleep(1)
        raise database.DatabaseError("consumer receipt pending; source remains fenced")


def confirm_consumers():
    cfg = config()
    copied = json.loads((STATE / "copy.json").read_text())
    receipt = json.loads(sys.stdin.read())
    auditor = urllib.parse.urlsplit(owner_url(cfg))._replace(
        netloc=f"jobseek_migration_auditor:{cfg['auditor_password']}@{DOMAIN}:{PORT}"
    )
    expected = {
        "revision": copied["revision"],
        "project": "jobseek-web",
        "owner_sha256": hashlib.sha256(owner_url(cfg).encode()).hexdigest(),
        "auditor_sha256": hashlib.sha256(
            urllib.parse.urlunsplit(auditor).encode()
        ).hexdigest(),
        "consumers": [
            "vercel:production",
            "github:production",
            "github:production-migration-drift",
        ],
    }
    if receipt != expected or copied.get("success") is not True:
        raise database.DatabaseError(
            "consumer receipt does not bind the copied database"
        )
    database.atomic_json(STATE / "consumers-configured.json", expected)
    print(json.dumps({"success": True, "consumer_cutover_recorded": True}))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=[
            "prepare",
            "rehearse",
            "copy",
            "verify",
            "renew-tls",
            "confirm-consumers",
        ],
    )
    args = parser.parse_args()
    if os.getuid() != 0:
        raise database.DatabaseError(
            "host operations require the protected root runtime"
        )
    os.umask(0o077)

    def interrupted(_signum, _frame):
        raise database.DatabaseError("protected operation interrupted")

    for signum in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(signum, interrupted)
    RUNTIME.mkdir(mode=0o700, parents=True, exist_ok=True)
    STATE.mkdir(mode=0o700, parents=True, exist_ok=True)
    if args.action == "confirm-consumers":
        confirm_consumers()
        return
    with Path("/run/jobseek-web-postgresql-operation.lock").open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        if args.action == "prepare":
            prepare()
        elif args.action in ("rehearse", "copy"):
            copy_database(rehearsal=args.action == "rehearse")
        elif args.action == "renew-tls":
            renew_tls()
        else:
            cfg = config()
            client = database.Client(owner_url(cfg), RUNTIME)
            try:
                with client.snapshot() as (query, _):
                    catalog = database.inventory(query)
                print(
                    json.dumps({"success": True, "table_count": len(catalog["tables"])})
                )
            finally:
                client.close()


if __name__ == "__main__":
    try:
        main()
    except (database.DatabaseError, OSError, ValueError, subprocess.SubprocessError):
        print(
            json.dumps(
                {"success": False, "error": "web_postgresql_host_operation_failed"}
            ),
            file=sys.stderr,
        )
        raise SystemExit(1) from None
