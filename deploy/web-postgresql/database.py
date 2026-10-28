#!/usr/bin/env python3
"""Complete provider-neutral web database packets; never relay database output.

Runs on a protected host with the pinned PostgreSQL client, or against native
clients in the PostgreSQL 17 integration fixture. Credentials enter via files.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

IMAGE = "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
SCHEMAS = ("public", "drizzle")
POLICY_ROLES = ("jobseek_migration_auditor", "jobseek_narrowed_worker")
FENCE_SCHEMA = "jobseek_provider_cutover"
FENCE_TRIGGER = "jobseek_provider_write_fence"
IDENTIFIER = re.compile(r"^[a-z_][a-z0-9_]{0,62}$")


class DatabaseError(RuntimeError):
    """Only constant messages may leave this boundary."""


def private_file(path: Path) -> str:
    metadata = path.lstat()
    if not stat.S_ISREG(metadata.st_mode) or stat.S_IMODE(metadata.st_mode) != 0o600:
        raise DatabaseError("credential must be a regular mode-0600 file")
    if metadata.st_uid != os.getuid():
        raise DatabaseError("credential owner differs from the executing user")
    value = path.read_text().strip()
    if not value or "\n" in value or "\r" in value:
        raise DatabaseError("credential is empty or multiline")
    return value


def atomic_json(path: Path, value: object) -> None:
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        os.chmod(temporary, 0o600)
        json.dump(value, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def identifier(value: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise DatabaseError("unsupported SQL identifier")
    return f'"{value}"'


def connection_environment(url: str) -> dict[str, str]:
    parsed = urlsplit(url)
    if parsed.scheme not in ("postgres", "postgresql") or not parsed.hostname:
        raise DatabaseError("invalid PostgreSQL connection")
    if parsed.port == 6543:
        raise DatabaseError("direct or session connection required")
    options = parse_qs(parsed.query)
    legacy_source = parsed.hostname.endswith((".supabase.com", ".supabase.co"))
    mode = options.get("sslmode", ["require" if legacy_source else "verify-full"])[0]
    if mode not in ("verify-full", "require", "disable"):
        raise DatabaseError("unsupported PostgreSQL TLS mode")
    if mode == "disable" and parsed.hostname not in ("localhost", "127.0.0.1", "::1"):
        raise DatabaseError("remote PostgreSQL connection requires TLS")
    settings = {
        "PGHOST": parsed.hostname,
        "PGPORT": str(parsed.port or 5432),
        "PGUSER": unquote(parsed.username or ""),
        "PGPASSWORD": unquote(parsed.password or ""),
        "PGDATABASE": unquote(parsed.path.removeprefix("/")),
        "PGSSLMODE": mode,
        "PGCONNECT_TIMEOUT": "15",
        "PGAPPNAME": "jobseek-web-database-packet",
        "PGOPTIONS": "-c timezone=UTC",
    }
    if mode == "verify-full":
        settings["PGSSLROOTCERT"] = "system"
    return settings


class Client:
    def __init__(
        self,
        url: str,
        root: Path,
        *,
        native_bin: Path | None = None,
        container: str | None = None,
    ):
        self.settings = connection_environment(url)
        self.root = root.resolve()
        self.native_bin = native_bin
        self.container = container
        self.environment = os.environ.copy()
        self.environment.pop("PGSERVICE", None)
        self.environment.pop("PGOPTIONS", None)
        self.environment.update(self.settings)
        self.env_file: Path | None = None
        if not native_bin and not container:
            fd, name = tempfile.mkstemp(prefix=".connection-", dir=root)
            self.env_file = Path(name)
            with os.fdopen(fd, "w") as stream:
                for key, value in self.settings.items():
                    if "\n" in value or "\r" in value:
                        raise DatabaseError("connection field is multiline")
                    stream.write(f"{key}={value}\n")

    def close(self) -> None:
        if self.env_file:
            self.env_file.unlink(missing_ok=True)

    def command(self, program: str, *arguments: str) -> list[str]:
        if self.native_bin:
            return [str(self.native_bin / program), *arguments]
        if self.container:
            return [
                "docker",
                "exec",
                "--interactive",
                "--user",
                "0:0",
                *[item for key in self.settings for item in ("--env", key)],
                self.container,
                program,
                *arguments,
            ]
        return [
            "docker",
            "run",
            "--rm",
            "--interactive",
            "--network",
            "host",
            "--read-only",
            "--cap-drop=ALL",
            "--security-opt=no-new-privileges",
            "--memory=512m",
            "--pids-limit=64",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--env-file",
            str(self.env_file),
            "--mount",
            f"type=bind,src={self.root},dst={self.root}",
            IMAGE,
            program,
            *arguments,
        ]

    def run(
        self,
        program: str,
        *arguments: str,
        input_text: str | None = None,
        timeout: int = 1800,
    ) -> str:
        completed = subprocess.run(
            self.command(program, *arguments),
            env=self.environment,
            input=input_text,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        if completed.returncode:
            diagnostic = self.root / "operation-error.private.log"
            with diagnostic.open("w", encoding="utf-8") as stream:
                os.chmod(diagnostic, 0o600)
                stream.write(completed.stderr)
            raise DatabaseError(
                f"{program} failed; protected diagnostic retained by operator"
            )
        return completed.stdout

    def sql(self, sql: str) -> str:
        # SQL travels on stdin: it can contain a role password during provisioning.
        return self.run(
            "psql",
            "-X",
            "-w",
            "-q",
            "-A",
            "-t",
            "-v",
            "ON_ERROR_STOP=1",
            input_text=sql,
            timeout=300,
        ).strip()

    @contextlib.contextmanager
    def snapshot(self):
        error_file = tempfile.TemporaryFile(mode="w+")
        process = subprocess.Popen(
            self.command("psql", "-X", "-w", "-q", "-A", "-t", "-v", "ON_ERROR_STOP=1"),
            env=self.environment,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=error_file,
            text=True,
            bufsize=1,
        )
        assert process.stdin is not None and process.stdout is not None

        def query(statement: str):
            # json_agg pretty-separates records with literal newlines. Keep the
            # protocol one line without altering escaped newlines in values.
            process.stdin.write(
                "SELECT replace((" + statement + ")::text, chr(10), '');\n"
            )
            process.stdin.flush()
            line = process.stdout.readline()
            if not line:
                error_file.seek(0)
                diagnostic = self.root / "operation-error.private.log"
                with diagnostic.open("w", encoding="utf-8") as stream:
                    os.chmod(diagnostic, 0o600)
                    stream.write(error_file.read())
                raise DatabaseError("consistent database snapshot failed")
            try:
                return json.loads(line)
            except json.JSONDecodeError:
                raise DatabaseError("invalid database snapshot response") from None

        try:
            process.stdin.write(
                "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY;\n"
                "SET LOCAL statement_timeout='120s';\n"
                "SET LOCAL idle_in_transaction_session_timeout='20min';\n"
                "SET LOCAL timezone='UTC';\n"
                "SET LOCAL extra_float_digits=3;\n"
                "SET LOCAL datestyle='ISO, YMD';\n"
                "SET LOCAL intervalstyle='postgres';\n"
                "SET LOCAL bytea_output='hex';\n"
            )
            process.stdin.flush()
            snapshot_id = query("SELECT to_json(pg_export_snapshot())")
            if not re.fullmatch(r"[0-9A-Fa-f-]{1,128}", snapshot_id):
                raise DatabaseError("invalid exported snapshot identity")
            yield query, snapshot_id
            process.stdin.write("COMMIT;\n\\q\n")
            process.stdin.flush()
            if process.wait(timeout=15):
                raise DatabaseError("snapshot did not finish successfully")
        finally:
            if process.poll() is None:
                process.kill()
                process.wait(timeout=15)
            process.stdin.close()
            process.stdout.close()
            error_file.close()


def inventory(query) -> dict:
    tables = query("""SELECT coalesce(json_agg(t ORDER BY t.schema,t.name),'[]') FROM (
        SELECT n.nspname AS schema,c.relname AS name,c.relkind AS kind
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('public','drizzle') AND c.relkind IN ('r','p','v','m','f')
        ) t""")
    if not tables or any(t["kind"] != "r" for t in tables):
        raise DatabaseError("expected ordinary application tables only")
    if not any(
        t["schema"] == "drizzle" and t["name"] == "__drizzle_migrations" for t in tables
    ):
        raise DatabaseError("application migration ledger is missing")
    for table in tables:
        identifier(table["schema"])
        identifier(table["name"])
    foreign = query("""SELECT to_json(count(*)) FROM pg_constraint c
        JOIN pg_class r ON r.oid=c.conrelid JOIN pg_namespace n ON n.oid=r.relnamespace
        JOIN pg_class f ON f.oid=c.confrelid JOIN pg_namespace fn ON fn.oid=f.relnamespace
        WHERE c.contype='f' AND n.nspname IN ('public','drizzle')
          AND fn.nspname NOT IN ('public','drizzle')""")
    if foreign:
        raise DatabaseError("application foreign key crosses the backup boundary")
    roles = query("""SELECT coalesce(json_agg(r ORDER BY r),'[]') FROM (
        SELECT DISTINCT unnest(roles)::text AS r FROM pg_policies
        WHERE schemaname IN ('public','drizzle')) s""")
    if set(roles) - set(POLICY_ROLES) - {"public"}:
        raise DatabaseError("application has an unreviewed policy role")
    large_objects = query("SELECT to_json(count(*)) FROM pg_largeobject_metadata")
    if large_objects:
        raise DatabaseError("large objects need a separately reviewed backup boundary")
    return {"tables": tables, "policy_roles": roles}


def fingerprints(query, catalog: dict) -> dict:
    result = {}
    for table in catalog["tables"]:
        name = f"{identifier(table['schema'])}.{identifier(table['name'])}"
        # Two independent 64-bit multiset sums plus count; no row contents leave PG.
        result[f"{table['schema']}.{table['name']}"] = (
            query(f"""SELECT json_build_object(
            'rows',count(*),'a',coalesce(sum(('x'||substr(h,1,16))::bit(64)::bigint),0)::text,
            'b',coalesce(sum(('x'||substr(h,17,16))::bit(64)::bigint),0)::text)
            FROM (SELECT md5(t::text) AS h FROM {name} t) s""")
        )
    sequences = query("""SELECT coalesce(json_agg(s ORDER BY s.schema,s.name),'[]') FROM (
        SELECT n.nspname AS schema,c.relname AS name FROM pg_class c
        JOIN pg_namespace n ON n.oid=c.relnamespace
        WHERE n.nspname IN ('public','drizzle') AND c.relkind='S') s""")
    values = {}
    for sequence in sequences:
        name = f"{identifier(sequence['schema'])}.{identifier(sequence['name'])}"
        values[f"{sequence['schema']}.{sequence['name']}"] = query(
            f"SELECT json_build_object('last_value',last_value::text,'is_called',is_called) FROM {name}"
        )
    return {"tables": result, "sequences": values}


def role_grants(query) -> list[dict]:
    return query("""SELECT coalesce(json_agg(s ORDER BY s.role,s.schema,s.name,s.privilege),'[]') FROM (
        SELECT r.rolname AS role,n.nspname AS schema,c.relname AS name,
          c.relkind AS kind,a.privilege_type AS privilege
        FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
        CROSS JOIN LATERAL aclexplode(c.relacl) a JOIN pg_roles r ON r.oid=a.grantee
        WHERE n.nspname IN ('public','drizzle') AND c.relkind IN ('r','S')
          AND r.rolname IN ('jobseek_migration_auditor','jobseek_narrowed_worker')) s""")


def dump(client: Client, packet: Path) -> dict:
    packet.mkdir(mode=0o700, parents=False, exist_ok=False)
    archive = packet / "database.dump"
    with client.snapshot() as (query, snapshot_id):
        version = query(
            "SELECT to_json(current_setting('server_version_num')::integer)"
        )
        if not 170000 <= version < 180000:
            raise DatabaseError("source must be PostgreSQL 17")
        catalog = inventory(query)
        grants = role_grants(query)
        before = fingerprints(query, catalog)
        client.run(
            "pg_dump",
            "--format=custom",
            "--compress=6",
            "--no-owner",
            "--no-privileges",
            "--schema=public",
            "--schema=drizzle",
            "--strict-names",
            "--lock-wait-timeout=30000",
            f"--snapshot={snapshot_id}",
            f"--file={archive}",
        )
        after = fingerprints(query, catalog)
        if before["tables"] != after["tables"]:
            raise DatabaseError("consistent snapshot contents changed")
        if before["sequences"].keys() != after["sequences"].keys():
            raise DatabaseError("sequence inventory changed during backup")
        for name, lower in before["sequences"].items():
            upper = after["sequences"][name]
            if int(upper["last_value"]) < int(lower["last_value"]) or (
                lower["is_called"] and not upper["is_called"]
            ):
                raise DatabaseError(
                    "sequence reset during backup; retry under maintenance"
                )
    archive.chmod(0o600)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    manifest = {
        "packet_version": 4,
        "schemas": list(SCHEMAS),
        "catalog": catalog,
        "role_grants": grants,
        "fingerprints": before,
        "sequence_upper_bounds": after["sequences"],
        "archive_sha256": digest,
        "archive_bytes": archive.stat().st_size,
        "created_unix": int(time.time()),
    }
    atomic_json(packet / "manifest.json", manifest)
    return manifest


def validate_packet(packet: Path) -> dict:
    manifest = json.loads((packet / "manifest.json").read_text())
    if manifest.get("packet_version") != 4 or manifest.get("schemas") != list(SCHEMAS):
        raise DatabaseError("complete web database packet is required")
    archive = packet / "database.dump"
    if archive.is_symlink() or not archive.is_file():
        raise DatabaseError("database archive is not a regular file")
    if archive.stat().st_size != manifest.get("archive_bytes"):
        raise DatabaseError("database archive size changed")
    if hashlib.sha256(archive.read_bytes()).hexdigest() != manifest.get(
        "archive_sha256"
    ):
        raise DatabaseError("database archive checksum changed")
    return manifest


def restore(client: Client, packet: Path) -> dict:
    manifest = validate_packet(packet)
    # A candidate is restored once. No --clean, DROP DATABASE, or overwrite path.
    tables = json.loads(
        client.sql(
            "BEGIN READ ONLY; SELECT to_json(count(*)) FROM pg_class c "
            "JOIN pg_namespace n ON n.oid=c.relnamespace "
            "WHERE n.nspname IN ('public','drizzle') AND c.relkind IN ('r','p'); COMMIT;"
        )
    )
    if tables:
        raise DatabaseError("refusing to restore into a populated application database")
    for role in POLICY_ROLES:
        client.sql(
            f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') "
            f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$;"
        )
    # pg_trgm lives in public; selected schema dumps can reference its members.
    client.sql("CREATE EXTENSION IF NOT EXISTS pg_trgm WITH SCHEMA public;")
    listing = client.run("pg_restore", "--list", str(packet / "database.dump"))
    keep = []
    for line in listing.splitlines():
        # PostgreSQL's template database already contains the public schema.
        if re.search(r"\bSCHEMA - public\s", line):
            continue
        # A source fence must never be installed on the destination.
        if re.search(
            r"\bTRIGGER (?:public|drizzle) \S+ " + FENCE_TRIGGER + r"\s", line
        ):
            continue
        keep.append(line)
    restore_list = packet / "restore.list"
    restore_list.write_text("\n".join(keep) + "\n")
    restore_list.chmod(0o600)
    client.run(
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-privileges",
        f"--use-list={restore_list}",
        "--dbname=" + client.settings["PGDATABASE"],
        str(packet / "database.dump"),
    )
    for grant in manifest["role_grants"]:
        role, privilege, kind = grant["role"], grant["privilege"], grant["kind"]
        allowed = (
            {"SELECT"}
            if role == "jobseek_migration_auditor"
            else {"SELECT", "INSERT", "UPDATE", "USAGE"}
        )
        if (
            role not in POLICY_ROLES
            or privilege not in allowed
            or kind not in ("r", "S")
        ):
            raise DatabaseError("unreviewed source role privilege")
        schema = identifier(grant["schema"])
        name = f"{schema}.{identifier(grant['name'])}"
        client.sql(
            f"GRANT USAGE ON SCHEMA {schema} TO {role}; "
            f"GRANT {privilege} ON {'SEQUENCE' if kind == 'S' else 'TABLE'} {name} TO {role};"
        )
    with client.snapshot() as (query, _):
        actual_catalog = inventory(query)
        actual = fingerprints(query, actual_catalog)
        actual_grants = role_grants(query)
    if (
        actual_catalog != manifest["catalog"]
        or actual["tables"] != manifest["fingerprints"]["tables"]
    ):
        raise DatabaseError(
            "restored database does not match the consistent source snapshot"
        )
    if actual_grants != manifest["role_grants"]:
        raise DatabaseError("restored role privileges differ from source")
    lower = manifest["fingerprints"]["sequences"]
    upper = manifest["sequence_upper_bounds"]
    if actual["sequences"].keys() != lower.keys() or lower.keys() != upper.keys():
        raise DatabaseError("restored sequence inventory differs")
    for name, value in actual["sequences"].items():
        # Sequences are nontransactional. pg_dump observes a value between the
        # two reads, potentially ahead of the table snapshot, which is safe.
        if (
            not int(lower[name]["last_value"])
            <= int(value["last_value"])
            <= int(upper[name]["last_value"])
        ):
            raise DatabaseError("restored sequence is outside its observed bounds")
        if lower[name] == upper[name] and value != lower[name]:
            raise DatabaseError("restored sequence call state differs")
    return {
        "success": True,
        "table_count": len(actual["tables"]),
        "row_count": sum(x["rows"] for x in actual["tables"].values()),
        "archive_sha256": manifest["archive_sha256"],
        "archive_bytes": manifest["archive_bytes"],
    }


def source_fence(client: Client, *, remove: bool = False) -> None:
    with client.snapshot() as (query, _):
        catalog = inventory(query)
    statements = [
        "BEGIN; SET LOCAL lock_timeout='15s'; SET LOCAL statement_timeout='60s';"
    ]
    if not remove:
        statements += [
            f"CREATE SCHEMA IF NOT EXISTS {FENCE_SCHEMA};",
            f"REVOKE ALL ON SCHEMA {FENCE_SCHEMA} FROM PUBLIC;",
            f"CREATE OR REPLACE FUNCTION {FENCE_SCHEMA}.reject_write() RETURNS trigger "
            "LANGUAGE plpgsql SET search_path=pg_catalog AS $$ BEGIN "
            "RAISE EXCEPTION USING ERRCODE='25006',MESSAGE='Database migration in progress'; END $$;",
        ]
    # Acquire every table lock in deterministic order in the same transaction.
    # Once committed, earlier writes have drained and every later DML is fenced.
    for table in catalog["tables"]:
        name = f"{identifier(table['schema'])}.{identifier(table['name'])}"
        statements.append(f"LOCK TABLE {name} IN SHARE ROW EXCLUSIVE MODE;")
        statements.append(f"DROP TRIGGER IF EXISTS {FENCE_TRIGGER} ON {name};")
        if not remove:
            statements.append(
                f"CREATE TRIGGER {FENCE_TRIGGER} BEFORE INSERT OR UPDATE OR DELETE OR TRUNCATE "
                f"ON {name} FOR EACH STATEMENT EXECUTE FUNCTION {FENCE_SCHEMA}.reject_write();"
            )
    if remove:
        statements += [
            f"DROP FUNCTION IF EXISTS {FENCE_SCHEMA}.reject_write();",
            f"DROP SCHEMA IF EXISTS {FENCE_SCHEMA};",
        ]
    statements.append("COMMIT;")
    client.sql("\n".join(statements))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("dump", "restore", "verify", "fence"))
    parser.add_argument("--url-file", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--packet", type=Path)
    parser.add_argument("--native-bin", type=Path)
    args = parser.parse_args()
    os.umask(0o077)
    args.root.mkdir(mode=0o700, parents=True, exist_ok=True)
    client = Client(private_file(args.url_file), args.root, native_bin=args.native_bin)
    try:
        if args.action == "dump":
            if not args.packet or args.packet.parent.resolve() != args.root.resolve():
                raise DatabaseError(
                    "packet must be a new direct child of the protected root"
                )
            data = dump(client, args.packet)
            print(
                json.dumps(
                    {
                        "success": True,
                        "table_count": len(data["catalog"]["tables"]),
                        "archive_bytes": data["archive_bytes"],
                        "archive_sha256": data["archive_sha256"],
                    }
                )
            )
        elif args.action == "restore":
            if not args.packet:
                raise DatabaseError("restore packet is required")
            print(json.dumps(restore(client, args.packet)))
        elif args.action == "fence":
            if not client.settings["PGHOST"].endswith(
                (".supabase.com", ".supabase.co")
            ):
                raise DatabaseError(
                    "source fence is confined to the retiring Supabase database"
                )
            source_fence(client)
            print(json.dumps({"success": True, "source_fenced": True}))
        else:
            with client.snapshot() as (query, _):
                catalog = inventory(query)
            print(json.dumps({"success": True, "table_count": len(catalog["tables"])}))
    finally:
        client.close()


if __name__ == "__main__":
    try:
        main()
    except (DatabaseError, OSError, ValueError, subprocess.SubprocessError) as error:
        message = (
            str(error)
            if isinstance(error, DatabaseError)
            else "web_database_operation_failed"
        )
        print(json.dumps({"success": False, "error": message}), file=sys.stderr)
        raise SystemExit(1) from None
