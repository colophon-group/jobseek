"""Disposable PostgreSQL proof for the Justup reader's actual SQL boundary."""

from __future__ import annotations

import asyncio
import hashlib
import importlib.util
import json
import os
import shutil
import socket
import struct
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID

import asyncpg
import pytest

from src.export_cursor_fence import CDC_WRITER_BARRIER_ID, capture_justup_feed_cutoff
from src.justup_feed import __main__ as feed

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "justup_feed_reader_postgres", ROOT / "deploy/networking/justup-feed-readonly-role.py"
)
assert SPEC is not None and SPEC.loader is not None
role = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(role)

COMPANY = "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa"
POSTING = "11111111-1111-4111-8111-111111111111"
BODY = "<p>Synthetic public job.</p>"
PASSWORD = "A" * 64


def binaries() -> Path:
    configured = os.environ.get("JOBSEEK_TEST_PG_BIN")
    candidate = Path(configured) if configured else Path(shutil.which("initdb") or "").parent
    if not all((candidate / name).is_file() for name in ("initdb", "pg_ctl", "psql", "createdb")):
        if configured:
            pytest.fail("Required PostgreSQL 16 binaries are unavailable")
        pytest.skip("Disposable PostgreSQL binaries are unavailable")
    if configured:
        version = command([str(candidate / "pg_ctl"), "--version"])
        assert version.returncode == 0 and " 16." in version.stdout
    return candidate


def command(
    args: list[str], *, sql: str | None = None, env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        args,
        input=sql,
        capture_output=True,
        text=True,
        timeout=45,
        env={**os.environ, "LC_ALL": "C", "LANG": "C", **(env or {})},
        check=False,
    )


@contextmanager
def database() -> Iterator[tuple[list[str], int, Path, Path]]:
    pg_bin = binaries()
    with tempfile.TemporaryDirectory(prefix="feedpg-", dir="/tmp") as temporary:
        root = Path(temporary)
        data = root / "data"
        sock = root / "sock"
        sock.mkdir()
        assert (
            command(
                [str(pg_bin / "initdb"), "-D", str(data), "-U", "crawler", "--no-instructions"]
            ).returncode
            == 0
        )
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        started = command(
            [
                str(pg_bin / "pg_ctl"),
                "-D",
                str(data),
                "-l",
                str(root / "postgres.log"),
                "-o",
                f"-k {sock} -h 127.0.0.1 -p {port}",
                "-w",
                "start",
            ]
        )
        assert started.returncode == 0, started.stderr
        try:
            assert (
                command(
                    [
                        str(pg_bin / "createdb"),
                        "-h",
                        str(sock),
                        "-p",
                        str(port),
                        "-U",
                        "crawler",
                        "crawler",
                    ]
                ).returncode
                == 0
            )
            cli = [
                str(pg_bin / "psql"),
                "-h",
                str(sock),
                "-p",
                str(port),
                "-U",
                "crawler",
                "-d",
                "crawler",
                "-XAt",
                "-v",
                "ON_ERROR_STOP=1",
            ]
            yield cli, port, sock, data
        finally:
            stopped = command(
                [str(pg_bin / "pg_ctl"), "-D", str(data), "-m", "immediate", "-w", "stop"]
            )
            assert stopped.returncode == 0, stopped.stderr


def setup_schema(cli: list[str], data: Path, *, taxonomy: bool) -> None:
    ddl = """
    CREATE TABLE company(id uuid, name text, slug text, website text, extras jsonb);
    CREATE TABLE job_posting(
      id uuid, company_id uuid, titles text[], source_url text, is_active boolean,
      tdm_reserved boolean, first_seen_at timestamptz, last_seen_at timestamptz,
      updated_at timestamptz, locales text[], location_ids integer[],
      description_r2_hash bigint, location_types text[], employment_type text,
      salary_min integer, salary_max integer, salary_currency text, salary_period text,
      experience_min numeric, experience_max numeric, occupation_id integer,
      seniority_id integer, enrichment jsonb);
    CREATE TABLE descriptions(
      posting_id uuid, locale text, hash bigint, html text, r2_uploaded boolean,
      private_note text);
    CREATE TABLE job_board(config text);
    INSERT INTO company VALUES
      ('aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa','Synthetic Company','synthetic',
       'https://example.org','{"private":true}');
    INSERT INTO job_posting(
      id,company_id,titles,source_url,is_active,tdm_reserved,first_seen_at,
      updated_at,locales,location_ids,location_types,employment_type,occupation_id,seniority_id,
      enrichment)
    VALUES (
      '11111111-1111-4111-8111-111111111111',
      'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',ARRAY['Engineer'],
      'https://example.org/jobs/1',true,false,now()-interval '1 day',
      now()-interval '1 day',ARRAY['en','fr'],ARRAY[1],ARRAY['remote'],
      'full_time',1,1,'{"private":true}');
    """
    if taxonomy:
        ddl += """
        CREATE TABLE location_name(
          location_id integer,name text,is_display boolean,locale text,private_note text);
        CREATE TABLE location(id integer,type text,private_note text);
        CREATE TABLE occupation_name(
          occupation_id integer,name text,locale text,is_display boolean,private_note text);
        CREATE TABLE seniority_name(
          seniority_id integer,name text,locale text,is_display boolean,private_note text);
        INSERT INTO location_name VALUES (1,'Zurich',true,'en','hidden');
        INSERT INTO location VALUES (1,'city','hidden');
        INSERT INTO occupation_name VALUES (1,'Engineer','en',true,'hidden');
        INSERT INTO seniority_name VALUES (1,'Senior','en',true,'hidden');
        """
    assert command(cli, sql=ddl).returncode == 0

    # The applied rule must precede role creation. The second database proves
    # that a crawler-only local reject would leave another socket path open.
    (data / "pg_hba.conf").write_text(
        "local all justup_feed_readonly reject\n"
        "local all all trust\n"
        "host crawler justup_feed_readonly 127.0.0.1/32 scram-sha-256\n"
        "host all all 127.0.0.1/32 trust\n"
    )
    assert command(cli, sql="SELECT pg_reload_conf();").returncode == 0


def install_reader(cli: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    role.PSQL = tuple(cli)
    monkeypatch.setattr(role, "read_password", lambda _path: PASSWORD)
    # This disposable database proves the SQL boundary; protected backup and
    # restore evidence is exercised separately by the host-gate tests.
    monkeypatch.setattr(role, "assert_backup_restore_fresh", lambda: None)
    installed = role.install(Path("/protected/synthetic-password"))
    assert installed["role"] == role.ROLE
    assert role.install(Path("/protected/synthetic-password")) == installed


@pytest.mark.parametrize("taxonomy", [False, True])
def test_reader_denials_and_snapshot_sql(
    taxonomy: bool, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    with database() as (cli, port, sock, data):
        setup_schema(cli, data, taxonomy=taxonomy)
        digest = hashlib.sha256(BODY.encode()).digest()
        body_hash = struct.unpack(">q", digest[:8])[0]
        insert = (
            "INSERT INTO descriptions VALUES "
            f"('{POSTING}','en',{body_hash},'{BODY}',true,'hidden'),"
            f"('{POSTING}','fr',{body_hash},'<p>Not uploaded.</p>',false,'hidden');"
        )
        assert command(cli, sql=insert).returncode == 0
        install_reader(cli, monkeypatch)

        feed_cli = [
            cli[0],
            "-h",
            "127.0.0.1",
            "-p",
            str(port),
            "-U",
            role.ROLE,
            "-d",
            "crawler",
            "-XAt",
            "-v",
            "ON_ERROR_STOP=1",
        ]
        env = {"PGPASSWORD": PASSWORD}
        assert command(feed_cli, sql="SELECT name FROM public.company;", env=env).returncode == 0
        for denied in (
            "SELECT extras FROM public.company;",
            "SELECT enrichment FROM public.job_posting;",
            "SELECT html FROM public.descriptions;",
            "SELECT config FROM public.job_board;",
            "UPDATE public.job_posting SET is_active=false;",
        ):
            assert command(feed_cli, sql=denied, env=env).returncode != 0
        view = command(
            feed_cli, sql="SELECT locale FROM justup_feed.uploaded_description;", env=env
        )
        assert view.returncode == 0 and view.stdout.strip() == "en"
        for database_name in ("crawler", "postgres"):
            socket_cli = [
                cli[0],
                "-h",
                str(sock),
                "-p",
                str(port),
                "-U",
                role.ROLE,
                "-d",
                database_name,
                "-XAt",
                "-v",
                "ON_ERROR_STOP=1",
            ]
            assert command(socket_cli, sql="SELECT 1;").returncode != 0

        selector = tmp_path / "selector.json"
        selector.write_text(json.dumps({"schemaVersion": 1, "companyIds": [COMPANY]}))
        spool = tmp_path / "spool"
        spool.mkdir(mode=0o700)
        monkeypatch.setenv(
            "LOCAL_DATABASE_URL",
            f"postgresql://{role.ROLE}:{PASSWORD}@127.0.0.1:{port}/crawler",
        )
        pending = asyncio.run(feed.export(spool, False, selector))
        manifest = json.loads((spool / pending["manifest"]).read_text())
        assert manifest["kind"] == "snapshot"
        assert manifest["sourceScopeRowCount"] == manifest["rows"] == 1
        part = json.loads(
            (spool / pending["manifest"]).parent.joinpath(manifest["parts"][0]["file"]).read_text()
        )
        row = part["rows"][0]
        assert row["bodyStatus"] == "ready"
        assert row["body"]["html"] == BODY
        assert row["locationNames"] == (["Zurich"] if taxonomy else [None])

        async def check_changes() -> None:
            connection = await asyncpg.connect(
                host="127.0.0.1",
                port=port,
                user=role.ROLE,
                password=PASSWORD,
                database="crawler",
            )
            try:
                query = feed.CHANGES_SQL if taxonomy else feed.CHANGES_SQL_NO_TAXONOMY
                rows = await connection.fetch(
                    query,
                    datetime.now(UTC) - timedelta(days=2),
                    UUID(feed.ZERO_UUID),
                    datetime.now(UTC) + timedelta(seconds=1),
                    feed.BODY_LIMIT,
                    101,
                    [UUID(COMPANY)],
                )
                assert len(rows) == 1
                assert rows[0]["body_html"] == BODY
            finally:
                await connection.close()

        asyncio.run(check_changes())


def test_privileged_cutoff_sees_writer_without_exposing_session_query(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with database() as (cli, port, _sock, data):
        setup_schema(cli, data, taxonomy=False)
        install_reader(cli, monkeypatch)

        async def prove() -> None:
            owner = await asyncpg.connect(
                host="127.0.0.1", port=port, user="crawler", database="crawler"
            )
            reader = await asyncpg.create_pool(
                host="127.0.0.1",
                port=port,
                user=role.ROLE,
                password=PASSWORD,
                database="crawler",
                min_size=1,
                max_size=1,
            )
            try:
                await owner.execute("BEGIN")
                await owner.execute(
                    "SELECT pg_advisory_xact_lock_shared($1::bigint)", CDC_WRITER_BARRIER_ID
                )
                writer_start = await owner.fetchval(
                    "SELECT xact_start FROM pg_stat_activity WHERE pid=pg_backend_pid()"
                )
                cutoff = await capture_justup_feed_cutoff(reader)
                assert cutoff <= writer_start
                async with reader.acquire() as connection:
                    visible_query = await connection.fetchval(
                        "SELECT query FROM pg_stat_activity WHERE pid=$1", owner.get_server_pid()
                    )
                    assert visible_query in (None, "<insufficient privilege>")
                    assert (
                        await connection.fetchval(
                            "SELECT active_writers FROM justup_feed.capture_cdc_snapshot_cutoff()"
                        )
                        >= 1
                    )
                    await connection.execute("SET default_transaction_read_only=off")
                    await connection.execute("CREATE TEMP TABLE pg_locks(pid integer)")
                    assert (
                        await connection.fetchval(
                            "SELECT cutoff FROM justup_feed.capture_cdc_snapshot_cutoff()"
                        )
                        <= writer_start
                    )
            finally:
                await owner.execute("ROLLBACK")
                await owner.close()
                await reader.close()

        asyncio.run(prove())


def test_audit_failure_keeps_login_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    with database() as (cli, _port, _sock, data):
        setup_schema(cli, data, taxonomy=False)
        role.PSQL = tuple(cli)
        assert command(cli, sql=role.install_sql(PASSWORD)).returncode == 0
        assert (
            command(
                cli, sql=f"GRANT UPDATE (config) ON public.job_board TO {role.ROLE};"
            ).returncode
            == 0
        )
        with pytest.raises(role.RoleError):
            role.audit(login=False)
        state = command(cli, sql=f"SELECT rolcanlogin FROM pg_roles WHERE rolname='{role.ROLE}';")
        assert state.returncode == 0 and state.stdout.strip() == "f"


def test_activation_rejects_missing_audit_boundary() -> None:
    with database() as (cli, _port, _sock, data):
        setup_schema(cli, data, taxonomy=False)
        role.PSQL = tuple(cli)
        assert command(cli, sql=role.install_sql(PASSWORD)).returncode == 0
        _, role_raw, boundary_raw = role.audit(login=False)
        assert command(cli, sql="DROP VIEW justup_feed.uploaded_description;").returncode == 0
        assert command(cli, sql=role.activate_sql(role_raw, boundary_raw)).returncode != 0
        state = command(cli, sql=f"SELECT rolcanlogin FROM pg_roles WHERE rolname='{role.ROLE}';")
        assert state.returncode == 0 and state.stdout.strip() == "f"
