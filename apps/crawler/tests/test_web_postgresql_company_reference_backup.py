from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SPEC = importlib.util.spec_from_file_location(
    "reference_backup", ROOT / "scripts/jobseek-data-backup.py"
)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def phase_state(phase: str) -> dict[str, object]:
    ledger = []
    if phase != "legacy":
        ledger.append(
            {
                "hash": backup.WEB_POSTGRES_REFERENCE_EXPAND_HASH,
                "created_at": backup.WEB_POSTGRES_REFERENCE_EXPAND_CREATED_AT,
            }
        )
    if phase == "reference":
        ledger.append(
            {
                "hash": backup.WEB_POSTGRES_REFERENCE_CONTRACT_HASH,
                "created_at": backup.WEB_POSTGRES_REFERENCE_CONTRACT_CREATED_AT,
            }
        )
    return {
        "ledger": ledger,
        "table": phase != "legacy",
        "bridge_function": phase == "expanded",
        "bridge_triggers": int(phase == "expanded"),
        "selection_fks": [
            {
                "table": table,
                "target": "company_reference" if phase == "reference" else "company",
                "target_schema": "public",
                "delete": "r" if phase == "reference" else "c",
                "update": "a",
                "valid": True,
                "deferred": False,
                "source_columns": 1,
                "target_columns": 1,
                "target_column": "id",
            }
            for table in ("followed_company", "watchlist_company")
        ],
    }


@pytest.mark.parametrize("phase", backup.WEB_POSTGRES_REFERENCE_PHASES)
def test_phase_requires_matching_ledger_catalogue_and_dependencies(monkeypatch, phase):
    outputs = [json.dumps(phase_state(phase))]
    if phase != "legacy":
        outputs.append("")
    if phase == "expanded":
        outputs.append("1")
    values = iter(outputs)
    monkeypatch.setattr(backup, "_web_psql", lambda *_args, **_kwargs: next(values))
    assert backup._web_postgres_reference_phase(env={}) == phase
    assert (
        ("public", "company_reference") in backup._web_postgres_tables(phase)
        if phase != "legacy"
        else ("public", "company_reference") not in backup._web_postgres_tables(phase)
    )
    bootstrap = backup._web_postgres_bootstrap_sql(phase)
    assert ("CREATE ROLE jobseek_migration_auditor NOLOGIN" in bootstrap) == (phase != "legacy")
    assert ("CREATE FUNCTION public.company_reference_from_legacy" in bootstrap) == (
        phase == "expanded"
    )
    assert "INSERT INTO public.company_reference" not in bootstrap or phase == "expanded"


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_ledger",
        "wrong_hash",
        "duplicate_ledger",
        "missing_table",
        "old_fk",
        "cascade_fk",
        "bridge_retained",
    ),
)
def test_mixed_company_reference_phases_fail_closed(monkeypatch, mutation):
    state = phase_state("reference")
    if mutation == "missing_ledger":
        state["ledger"] = []
    elif mutation == "wrong_hash":
        state["ledger"][0]["hash"] = "f" * 64
    elif mutation == "duplicate_ledger":
        state["ledger"].append(state["ledger"][0])
    elif mutation == "missing_table":
        state["table"] = False
    elif mutation == "old_fk":
        state["selection_fks"][0]["target"] = "company"
    elif mutation == "cascade_fk":
        state["selection_fks"][0]["delete"] = "c"
    else:
        state["bridge_function"] = True
    monkeypatch.setattr(backup, "_web_psql", lambda *_args, **_kwargs: json.dumps(state))
    with pytest.raises(backup.BackupError):
        backup._web_postgres_reference_phase(env={})


def test_phase_bootstrap_and_allowlist_reject_unknown_or_omitted_reference():
    with pytest.raises(backup.BackupError):
        backup._web_postgres_tables("invented")
    listing = []
    for schema, table in backup.WEB_POSTGRES_TABLES:
        listing += [
            f"1; 1259 1 TABLE {schema} {table} postgres",
            f"2; 0 1 TABLE DATA {schema} {table} postgres",
        ]
    for schema, sequence in backup.WEB_POSTGRES_SEQUENCES:
        listing += [
            f"3; 1259 2 SEQUENCE {schema} {sequence} postgres",
            f"4; 0 0 SEQUENCE SET {schema} {sequence} postgres",
        ]
    backup._validate_web_postgres_archive("\n".join(listing), "legacy")
    with pytest.raises(backup.BackupError, match="table boundary"):
        backup._validate_web_postgres_archive("\n".join(listing), "expanded")


def test_reviewed_migration_fixtures_match_phase_hashes_and_real_sql_when_present():
    # These are test data, never runner-discovered runtime migrations. They keep
    # the independent backup release testable before the actual expand release.
    actual_root = Path(
        os.environ.get("WEB_POSTGRES_BACKUP_TEST_MIGRATION_ROOT", ROOT / "apps/web/drizzle")
    )
    for filename, expected in (
        ("0100_company_references.sql", backup.WEB_POSTGRES_REFERENCE_EXPAND_HASH),
        (
            "0101_company_reference_selection_contract.sql",
            backup.WEB_POSTGRES_REFERENCE_CONTRACT_HASH,
        ),
    ):
        fixture = (ROOT / "apps/crawler/tests/fixtures/company-reference" / filename).read_bytes()
        assert hashlib.sha256(fixture).hexdigest() == expected
        if (actual_root / filename).exists():
            assert (actual_root / filename).read_bytes() == fixture


@pytest.mark.parametrize(
    "mutation",
    (
        "missing_phase",
        "missing_dependencies",
        "null_dependencies",
        "unknown_dependency",
        "changed_bootstrap",
        "changed_archive",
    ),
)
def test_new_packet_never_falls_back_to_historical_contract(tmp_path, mutation):
    dump = tmp_path / "web-postgresql.dump"
    dump.write_bytes(b"checksum-bound-fixture")
    bootstrap = tmp_path / "bootstrap.sql"
    bootstrap.write_text(backup._web_postgres_bootstrap_sql("expanded", []))
    manifest = {
        "schema_version": 2,
        "company_reference_phase": "expanded",
        "dependencies": [],
        "archive": dump.name,
        "archive_bytes": dump.stat().st_size,
        "archive_sha256": backup._sha256_file(dump),
        "bootstrap": bootstrap.name,
        "bootstrap_sha256": backup._sha256_file(bootstrap),
        "tables": [
            f"{schema}.{table}" for schema, table in backup._web_postgres_tables("expanded", 2)
        ],
        "sequences": [f"{schema}.{table}" for schema, table in backup.WEB_POSTGRES_SEQUENCES],
    }
    if mutation == "missing_phase":
        del manifest["company_reference_phase"]
    elif mutation == "missing_dependencies":
        del manifest["dependencies"]
    elif mutation == "null_dependencies":
        manifest["dependencies"] = None
    elif mutation == "unknown_dependency":
        manifest["dependencies"] = ["public.unreviewed"]
    elif mutation == "changed_bootstrap":
        bootstrap.write_text("SELECT 1;")
        manifest["bootstrap_sha256"] = backup._sha256_file(bootstrap)
    else:
        dump.write_bytes(b"changed-archive")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    compatibility = tmp_path / "compatibility.sql"
    with pytest.raises(backup.BackupError):
        backup.prepare_web_postgresql_restore(manifest_path, dump, bootstrap, compatibility)
    assert not compatibility.exists()


def command(argv: list[str], *, input: str | None = None) -> str:
    result = subprocess.run(argv, input=input, text=True, capture_output=True, check=False)
    if result.returncode:
        raise RuntimeError(result.stderr)
    return result.stdout.strip()


@pytest.fixture
def local_clusters():
    if os.environ.get("WEB_POSTGRES_BACKUP_LOCAL_RESTORE") != "1":
        pytest.skip("explicit disposable local PostgreSQL restore rehearsal required")
    for executable in ("initdb", "pg_ctl", "psql", "pg_dump", "pg_restore", "pnpm"):
        assert shutil.which(executable), f"required local restore tool missing: {executable}"
    with tempfile.TemporaryDirectory(prefix="jobseek-ref-backup-", dir="/tmp") as directory:
        root = Path(directory)
        sockets = []
        try:
            for name in ("source", "restore"):
                data = root / name
                socket = root / f"{name}-socket"
                socket.mkdir()
                command(
                    [
                        "initdb",
                        "-D",
                        str(data),
                        "-U",
                        "postgres",
                        "--auth=trust",
                        "--no-locale",
                        "--encoding=UTF8",
                    ]
                )
                command(
                    [
                        "pg_ctl",
                        "-D",
                        str(data),
                        "-l",
                        str(root / f"{name}.log"),
                        "-o",
                        f"-k {socket} -c listen_addresses=''",
                        "-w",
                        "start",
                    ]
                )
                sockets.append(socket)
            yield root, sockets
        finally:
            for name in ("source", "restore"):
                if (root / name / "postmaster.pid").exists():
                    command(["pg_ctl", "-D", str(root / name), "-m", "immediate", "-w", "stop"])


@pytest.mark.parametrize(
    "phase,packet_version",
    [
        ("legacy", 1),
        ("legacy", 2),
        ("expanded", 2),
        ("reference", 2),
        ("legacy", 3),
        ("expanded", 3),
        ("reference", 3),
    ],
)
def test_actual_selective_archive_restores_reference_phase_and_smoke(
    monkeypatch, local_clusters, phase, packet_version
):
    """Real pg_dump/pg_restore, no Docker socket, no production connection.

    This exercises the installed client's major version; the protected host
    drill remains the required PostgreSQL17/encrypted-repository proof.
    """
    root, (source, target) = local_clusters

    def psql(sql: str, socket: Path = source) -> str:
        return command(
            [
                "psql",
                "-h",
                str(socket),
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-X",
                "-q",
                "-t",
                "-A",
                "-F",
                "|",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input=sql,
        )

    # Actual web schema; selection FK declarations are rewound only in this
    # disposable fixture when running after the final schema contract lands.
    schema_sql = command(
        [
            "pnpm",
            "--dir",
            str(ROOT / "apps/web"),
            "exec",
            "tsx",
            "-e",
            """
      import { generateDrizzleJson, generateMigration } from 'drizzle-kit/api';
      import * as schema from './src/db/schema';
      const legacy = { ...schema }; delete legacy.companyReference;
      generateMigration(generateDrizzleJson({}), generateDrizzleJson(legacy))
        .then(statements => process.stdout.write(statements.join(';\\n')));
    """,
        ]
    )
    # Final schema names reference the absent owner table; reconstruct the
    # pre-expansion selection constraints explicitly for the historical fixture.
    schema_sql = schema_sql.replace(
        'REFERENCES "public"."company_reference"', 'REFERENCES "public"."company"'
    ).replace("ON DELETE restrict", "ON DELETE cascade")
    psql(schema_sql)
    for name, definition in backup.WEB_POSTGRES_DEPENDENCY_SQL.items():
        if "jobseek_" in name:
            psql(definition)
    notification_migration = (
        ROOT / "apps/web/drizzle/0088_notification_policy_foundation.sql"
    ).read_text()
    for trigger in re.findall(r"CREATE TRIGGER[\s\S]+?;", notification_migration):
        psql(trigger)
    psql(
        "CREATE SCHEMA drizzle; CREATE TABLE drizzle.__drizzle_migrations "
        "(id serial PRIMARY KEY, hash text NOT NULL, created_at bigint NOT NULL); "
        "INSERT INTO drizzle.__drizzle_migrations(hash,created_at) VALUES "
        f"('{backup.WEB_POSTGRES_CONTRACT_HASH}',"
        f"{backup.WEB_POSTGRES_CONTRACT_CREATED_AT}); "
        "CREATE ROLE jobseek_migration_auditor NOLOGIN; "
        "INSERT INTO company(id,name,slug) VALUES ('00000000-0000-0000-0000-000000000"
        "201','Legacy Selected','legacy-selected'); "
        "INSERT INTO \"user\"(id,name,email) VALUES ('backup-fixture','Backup Fixture',"
        "'backup@invalid.example'); "
        "INSERT INTO watchlist(id,user_id,slug,title) VALUES ('00000000-0000-0000-000"
        "0-000000000206','backup-fixture','backup-fixture','Backup Fixture'); "
        "INSERT INTO watchlist_company(watchlist_id,company_id) VALUES ('00000000-000"
        "0-0000-0000-000000000206','00000000-0000-0000-0000-000000000201'); "
        "INSERT INTO followed_company(user_id,company_id) VALUES ('backup-fixture','0"
        "0000000-0000-0000-0000-000000000201');"
    )
    if packet_version == 3 and phase == "legacy":
        journal = json.loads((ROOT / "apps/web/drizzle/meta/_journal.json").read_text())
        prerequisite = next(
            row for row in journal["entries"] if row["tag"] == "0099_product_news_consent"
        )
        prerequisite_hash = backup._sha256_file(
            ROOT / "apps/web/drizzle/0099_product_news_consent.sql"
        )
        psql(
            "INSERT INTO drizzle.__drizzle_migrations(hash,created_at) "
            f"VALUES ('{prerequisite_hash}',{prerequisite['when']})"
        )
    if packet_version == 3:
        psql(
            "INSERT INTO company_description(company_id,locale,description) "
            "VALUES ('00000000-0000-0000-0000-000000000201','en','Retained description')"
        )
    migration_root = ROOT / "apps/crawler/tests/fixtures/company-reference"
    for name, timestamp, expected in (
        (
            "0100_company_references.sql",
            backup.WEB_POSTGRES_REFERENCE_EXPAND_CREATED_AT,
            backup.WEB_POSTGRES_REFERENCE_EXPAND_HASH,
        ),
        (
            "0101_company_reference_selection_contract.sql",
            backup.WEB_POSTGRES_REFERENCE_CONTRACT_CREATED_AT,
            backup.WEB_POSTGRES_REFERENCE_CONTRACT_HASH,
        ),
    ):
        if phase == "legacy" or (phase == "expanded" and name.startswith("0101")):
            break
        migration = (migration_root / name).read_text()
        assert hashlib.sha256(migration.encode()).hexdigest() == expected
        psql(
            "BEGIN; "
            + migration
            + "; INSERT INTO drizzle.__drizzle_migrations(hash,created_at) VALUES "
            f"('{expected}',{timestamp}); COMMIT;"
        )
    if phase != "legacy":
        psql(
            "INSERT INTO company_reference(id,name,slug,source,verified_at) VALUES "
            "('00000000-0000-0000-0000-000000000110','Retained Canonical','retained-canon"
            "ical','typesense','2026-10-03T10:00:00Z');"
        )
    if phase == "reference":
        psql(
            "INSERT INTO followed_company(user_id,company_id) VALUES "
            "('backup-fixture','00000000-0000-0000-0000-000000000110');"
        )
    monkeypatch.setattr(backup, "_web_psql", lambda sql, **_: psql(sql))
    assert backup._web_postgres_reference_phase(env={}) == phase
    backup._validate_web_postgres_boundary(env={}, phase=phase, packet_version=packet_version)
    dependencies = backup._web_postgres_dependencies(
        env={}, phase=phase, packet_version=packet_version
    )
    assert dependencies == sorted(backup.WEB_POSTGRES_DEPENDENCY_SQL)
    before = backup._web_postgres_fingerprints(env={}, phase=phase, packet_version=packet_version)
    sequences = backup._web_postgres_sequence_fingerprints(env={})
    dump = root / "web-postgresql.dump"
    argv = [
        "pg_dump",
        "-h",
        str(source),
        "-U",
        "postgres",
        "-d",
        "postgres",
        "-Fc",
        "--no-owner",
        "--no-privileges",
        "--strict-names",
        "-f",
        str(dump),
    ]
    for schema, table in (
        *backup._web_postgres_tables(phase, packet_version),
        *backup.WEB_POSTGRES_SEQUENCES,
    ):
        argv += ["--table", backup._qualified_table(schema, table)]
    command(argv)
    backup._validate_web_postgres_archive(
        command(["pg_restore", "--list", str(dump)]), phase, packet_version
    )
    bootstrap = root / "bootstrap.sql"
    bootstrap.write_text(
        backup._web_postgres_bootstrap_sql(phase, None if packet_version == 1 else dependencies)
    )
    manifest = {
        "schema_version": 1,
        "archive": dump.name,
        "archive_bytes": dump.stat().st_size,
        "archive_sha256": backup._sha256_file(dump),
        "bootstrap": bootstrap.name,
        "bootstrap_sha256": backup._sha256_file(bootstrap),
        "tables": [
            f"{schema}.{table}"
            for schema, table in backup._web_postgres_tables(phase, packet_version)
        ],
        "sequences": [f"{schema}.{table}" for schema, table in backup.WEB_POSTGRES_SEQUENCES],
        "fingerprints": before,
        "sequence_fingerprints": sequences,
    }
    if packet_version in (2, 3):
        manifest["schema_version"] = packet_version
        manifest["company_reference_phase"] = phase
        manifest["dependencies"] = dependencies
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    compatibility = root / "compatibility.sql"
    backup.prepare_web_postgresql_restore(manifest_path, dump, bootstrap, compatibility)
    psql(bootstrap.read_text(), target)
    psql(compatibility.read_text(), target)
    command(
        [
            "pg_restore",
            "-h",
            str(target),
            "-U",
            "postgres",
            "-d",
            "postgres",
            "--exit-on-error",
            "--no-owner",
            "--no-privileges",
            str(dump),
        ]
    )
    restore_script = (ROOT / "deploy/backups/web-postgresql/restore-drill.sh").read_text()
    access = restore_script.split("-- BEGIN COMPANY_REFERENCE_RESTORE_ACCESS\n", 1)[1].split(
        "-- END COMPANY_REFERENCE_RESTORE_ACCESS", 1
    )[0]
    psql(access, target)
    monkeypatch.setattr(backup, "_web_psql", lambda sql, **_: psql(sql, target))
    assert backup._web_postgres_reference_phase(env={}) == phase
    assert (
        backup._web_postgres_fingerprints(env={}, phase=phase, packet_version=packet_version)
        == before
    )
    assert backup._web_postgres_sequence_fingerprints(env={}) == sequences
    monkeypatch.setattr(backup, "_web_postgres_env", lambda: {})
    verified = backup.verify_web_postgresql_restore(manifest_path, dump, bootstrap)
    assert verified["company_reference_phase"] == phase
    if packet_version in (1, 2):
        # Historical rows/bootstrap remain exact. Relabelling its incomplete
        # table boundary as a v3 packet must never certify the new inventory.
        historical_manifest = manifest.copy()
        manifest.update(
            {"schema_version": 3, "company_reference_phase": phase, "dependencies": dependencies}
        )
        manifest_path.write_text(json.dumps(manifest))
        with pytest.raises(backup.BackupError, match="table boundary"):
            backup.prepare_web_postgresql_restore(manifest_path, dump, bootstrap, compatibility)
        manifest = historical_manifest
        manifest_path.write_text(json.dumps(manifest))
    else:
        assert (
            psql(
                "SELECT description FROM company_description "
                "WHERE company_id='00000000-0000-0000-0000-000000000201'",
                target,
            )
            == "Retained description"
        )
    if phase != "legacy":
        assert (
            psql(
                "SET ROLE jobseek_migration_auditor; SELECT count(*) FROM company_reference", target
            )
            == "2"
        )
        with pytest.raises(RuntimeError, match="permission denied"):
            psql(
                "SET ROLE jobseek_migration_auditor; INSERT INTO company_reference(id,name,sl"
                "ug,source) VALUES ('00000000-0000-0000-0000-000000000111','Denied','denied',"
                "'legacy_seed')",
                target,
            )
    # Extract and execute the actual restore-drill transaction rather than a
    # replica of its SQL. Every fixture is rolled back; retained snapshots match.
    smoke = restore_script.split("' restore-smoke \"$COMPANY_REFERENCE_PHASE\" <<'SQL'\n", 1)[
        1
    ].split("\nSQL", 1)[0]
    command(
        [
            "psql",
            "-h",
            str(target),
            "-U",
            "postgres",
            "-d",
            "postgres",
            "-X",
            "-q",
            "-v",
            "ON_ERROR_STOP=1",
            "-v",
            f"company_reference_phase={phase}",
        ],
        input=smoke,
    )
    assert (
        backup._web_postgres_fingerprints(env={}, phase=phase, packet_version=packet_version)
        == before
    )
    if phase != "legacy":
        assert psql(
            "SELECT source || '|' || verified_at::text FROM company_reference WHERE id='0"
            "0000000-0000-0000-0000-000000000110'",
            target,
        ).startswith("typesense|2026-10-03")
    if phase == "reference":
        assert (
            psql(
                "SELECT count(*) FROM company WHERE id='00000000-0000-0000-0000-000000000110'",
                target,
            )
            == "0"
        )
    if phase != "legacy":
        # A valid ledger and unchanged rows cannot hide a weakened provenance
        # check; backup/restore readiness independently inspects exact DDL.
        psql(
            "ALTER TABLE company_reference DROP CONSTRAINT company_reference_verification_check; "
            "ALTER TABLE company_reference ADD CONSTRAINT "
            "company_reference_verification_check CHECK(true)",
            target,
        )
        with pytest.raises(backup.BackupError, match="reference_checks"):
            backup._web_postgres_reference_phase(env={})

    if packet_version == 3 and phase == "legacy":
        command(["pnpm", "--dir", str(ROOT / "apps/web"), "build:company-reference:rehearsal"])
        bundle = ROOT / "deploy/backups/web-postgresql/company-reference-bundle"
        allowlist = json.loads((bundle / "manifest.json").read_text())
        script = """
          import { createRequire } from 'node:module';
          import { pathToFileURL } from 'node:url';
          const require=createRequire(process.env.REHEARSAL_APP_PACKAGE);
          const postgres=require('postgres');
          const moduleUrl=pathToFileURL(process.env.REHEARSAL_BUNDLE).href;
          const {rehearseCompanyReference}=await import(moduleUrl);
          const sql=postgres({host:process.env.REHEARSAL_SOCKET,database:'postgres',
            username:'postgres',max:1,prepare:false});
          try {
            const target=JSON.parse(process.env.REHEARSAL_TARGET);
            let refused=false;
            try { await rehearseCompanyReference(sql,{...target,createdAt:target.createdAt+1}); }
            catch { refused=true; }
            if (!refused) throw new Error('Incorrect journal timestamp was accepted');
            console.log(JSON.stringify(await rehearseCompanyReference(sql,target)));
          }
          finally { await sql.end(); }
        """
        completed = subprocess.run(
            ["node", "--input-type=module", "-e", script],
            cwd=bundle,
            env={
                **os.environ,
                "REHEARSAL_APP_PACKAGE": str(ROOT / "apps/web/package.json"),
                "REHEARSAL_BUNDLE": str(bundle / "rehearse.mjs"),
                "REHEARSAL_SOCKET": str(target),
                "REHEARSAL_TARGET": json.dumps(allowlist["migrations"][0]),
            },
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        proof = json.loads(completed.stdout)
        assert proof["preflight"] == proof["postflight"] == "passed"
        assert proof["dependencies"]["foreignKeyCount"] == 6
        assert proof["referenceRows"] == 1
        assert set(proof["preserved"]) == {
            table
            for schema, table in backup._web_postgres_tables("legacy", 3)
            if schema == "public"
        }
        after_rehearsal = backup._web_postgres_fingerprints(
            env={}, phase="legacy", packet_version=3
        )
        assert {
            key: value for key, value in after_rehearsal.items() if not key.startswith("drizzle.")
        } == {key: value for key, value in before.items() if not key.startswith("drizzle.")}
        assert (
            after_rehearsal["drizzle.__drizzle_migrations"]["rows"]
            == before["drizzle.__drizzle_migrations"]["rows"] + 1
        )


@pytest.mark.parametrize(
    ("expression", "error"),
    [
        *[
            (expression, "unreviewed custom function")
            for expression in ("default", "check", "policy", "index", "operator")
        ],
        *[
            (expression, "unreviewed custom expression")
            for expression in ("builtin_operator", "collation", "opclass")
        ],
        ("domain", "unreviewed custom type"),
        ("function_signature", "trigger function differs"),
    ],
)
def test_actual_custom_expression_dependency_blocks_backup_before_dump_or_upload(
    monkeypatch, local_clusters, expression, error
):
    _, (source, _) = local_clusters

    def psql(sql: str) -> str:
        return command(
            [
                "psql",
                "-h",
                str(source),
                "-U",
                "postgres",
                "-d",
                "postgres",
                "-X",
                "-q",
                "-t",
                "-A",
                "-v",
                "ON_ERROR_STOP=1",
            ],
            input=sql,
        )

    psql(
        'CREATE TABLE public."user" '
        "(id text PRIMARY KEY DEFAULT gen_random_uuid()::text, name text); "
        'ALTER TABLE public."user" ADD CHECK (length(name)>0); '
        'ALTER TABLE public."user" ADD CHECK ((NULL::public."user") IS NULL); '
        'CREATE INDEX backup_builtin_index ON public."user" (lower(name)); '
        'CREATE POLICY backup_builtin_policy ON public."user" USING (length(name)>0); '
        "CREATE FUNCTION public.backup_review_default() RETURNS text "
        "LANGUAGE SQL IMMUTABLE AS $$ SELECT 'fixture'::text $$; "
        "CREATE TYPE public.backup_excluded_status AS ENUM ('fixture'); "
        "CREATE TABLE public.backup_excluded (id text DEFAULT public.backup_review_default(), "
        'user_id text REFERENCES public."user"(id), status public.backup_excluded_status);'
    )
    monkeypatch.setattr(backup, "_web_psql", lambda sql, **_: psql(sql))
    # Builtins are portable; a function used only by an excluded table is not
    # part of the selected archive and must not expand its support boundary.
    assert backup._web_postgres_dependencies(env={}, phase="legacy") == []
    if expression == "default":
        psql('ALTER TABLE public."user" ALTER COLUMN id SET DEFAULT public.backup_review_default()')
    elif expression in ("check", "policy"):
        psql(
            "CREATE FUNCTION public.backup_review_check(text) RETURNS boolean "
            "LANGUAGE SQL IMMUTABLE AS $$ SELECT length($1)>0 $$"
        )
        if expression == "check":
            psql('ALTER TABLE public."user" ADD CHECK (public.backup_review_check(name))')
        else:
            psql(
                'CREATE POLICY backup_custom_policy ON public."user" '
                "USING (public.backup_review_check(name))"
            )
    elif expression == "index":
        psql(
            "CREATE FUNCTION public.backup_review_index(text) RETURNS text "
            "LANGUAGE SQL IMMUTABLE AS $$ SELECT lower($1) $$"
        )
        psql('CREATE INDEX backup_custom_index ON public."user" (public.backup_review_index(name))')
    elif expression == "operator":
        psql(
            "CREATE FUNCTION public.backup_review_operator(text,text) RETURNS boolean "
            "LANGUAGE SQL IMMUTABLE AS $$ SELECT $1=$2 $$; "
            "CREATE OPERATOR public.===# (FUNCTION=public.backup_review_operator, "
            "LEFTARG=text, RIGHTARG=text); "
            'ALTER TABLE public."user" ADD CHECK (name OPERATOR(public.===#) name)'
        )
    elif expression == "builtin_operator":
        psql(
            "CREATE OPERATOR public.===# "
            "(FUNCTION=pg_catalog.texteq, LEFTARG=text, RIGHTARG=text); "
            'ALTER TABLE public."user" ADD CHECK (name OPERATOR(public.===#) name)'
        )
    elif expression == "collation":
        psql(
            'CREATE COLLATION public.backup_collation FROM pg_catalog."C"; '
            'CREATE INDEX backup_collation_index ON public."user" '
            "(name COLLATE public.backup_collation)"
        )
    elif expression == "opclass":
        psql(
            "CREATE OPERATOR CLASS public.backup_opclass FOR TYPE text USING btree AS "
            "OPERATOR 1 < (text,text), OPERATOR 2 <= (text,text), OPERATOR 3 = (text,text), "
            "OPERATOR 4 >= (text,text), OPERATOR 5 > (text,text), "
            "FUNCTION 1 pg_catalog.bttextcmp(text,text); "
            'CREATE INDEX backup_opclass_index ON public."user" (name public.backup_opclass)'
        )
    elif expression == "domain":
        psql(
            "CREATE DOMAIN public.backup_domain AS text CHECK (length(VALUE)>0); "
            'ALTER TABLE public."user" ADD CHECK (name::public.backup_domain IS NOT NULL)'
        )
    else:
        definition = backup.WEB_POSTGRES_DEPENDENCY_SQL[
            "public.jobseek_notifications_pause_state_changed_at"
        ].replace("RETURNS trigger", "RETURNS text")
        psql(
            "SET check_function_bodies=off; "
            + definition
            + 'ALTER TABLE public."user" ALTER COLUMN id SET DEFAULT '
            "public.jobseek_notifications_pause_state_changed_at()"
        )

    monkeypatch.setenv("RESTIC_REPOSITORY", "fixture-repository")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", "/fixture/password")
    monkeypatch.setenv("RESTIC_SFTP_COMMAND", "fixture-transport")
    monkeypatch.setattr(backup, "_require_web_postgres_helper_image", lambda: None)
    monkeypatch.setattr(backup, "_web_postgres_env", lambda: {})
    monkeypatch.setattr(backup, "_web_postgres_reference_phase", lambda **_: "legacy")
    monkeypatch.setattr(backup, "_validate_web_postgres_boundary", lambda **_: None)
    commands = []
    monkeypatch.setattr(backup, "run_checked", lambda argv, **_: commands.append(argv))
    with pytest.raises(backup.BackupError, match=error):
        backup.web_postgresql_backup()
    assert commands == [], "Dependency failure must precede pg_dump and Restic upload"
