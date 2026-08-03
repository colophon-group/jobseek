from __future__ import annotations

import hashlib
import importlib.util
import json
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts" / "jobseek-data-backup.py"
SPEC = importlib.util.spec_from_file_location("jobseek_data_backup", SCRIPT_PATH)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def completed(stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], 0, stdout=stdout, stderr="")


def test_execute_with_status_preserves_last_success_on_failure(tmp_path: Path) -> None:
    previous = {
        "last_success_at": "2026-07-21T01:00:00+00:00",
        "last_success_unix": 1_774_000_000,
    }
    (tmp_path / "postgresql.json").write_text(json.dumps(previous), encoding="utf-8")

    def fail() -> dict[str, object]:
        raise backup.BackupError("token=should-not-leak")

    with pytest.raises(backup.BackupError):
        backup.execute_with_status("postgresql", fail, status_dir=tmp_path)

    record = json.loads((tmp_path / "postgresql.json").read_text(encoding="utf-8"))
    assert record["success"] is False
    assert record["last_success_unix"] == previous["last_success_unix"]
    assert "should-not-leak" not in record["error"]
    assert "<redacted>" in record["error"]
    assert 'jobseek_backup_last_attempt_success{service="postgresql"} 0' in (
        tmp_path / "postgresql.prom"
    ).read_text(encoding="utf-8")


def test_execute_with_status_records_a_success(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    instants = iter(
        (
            datetime(2026, 7, 22, 1, 0, tzinfo=UTC),
            datetime(2026, 7, 22, 1, 2, 3, tzinfo=UTC),
        )
    )
    monkeypatch.setattr(backup, "utc_now", lambda: next(instants))

    record = backup.execute_with_status(
        "postgresql",
        lambda: {"backup_label": "20260722-010000F"},
        status_dir=tmp_path,
    )

    assert record["success"] is True
    assert record["duration_seconds"] == 123
    assert record["last_success_at"] == "2026-07-22T01:02:03+00:00"
    assert record["backup_label"] == "20260722-010000F"
    assert 'jobseek_backup_last_attempt_success{service="postgresql"} 1' in (
        tmp_path / "postgresql.prom"
    ).read_text(encoding="utf-8")


def test_postgres_auto_uses_full_on_sunday(monkeypatch: pytest.MonkeyPatch) -> None:
    commands: list[list[str]] = []
    info = [
        {
            "backup": [
                {
                    "label": "20260726-010000F",
                    "type": "full",
                    "info": {"size": 1234, "repository": {"delta": 567}},
                    "timestamp": {"stop": 1_774_555_555},
                }
            ]
        }
    ]

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return completed("true\n")
        if "info" in argv:
            return completed(json.dumps(info))
        return completed()

    monkeypatch.setattr(backup, "run_checked", fake_run)
    monkeypatch.setattr(
        backup,
        "postgres_expire_archives",
        lambda _stanza: {
            "repository_capacity_bytes": 10_000,
            "repository_available_bytes": 6_000,
            "repository_available_ratio": 0.6,
        },
    )
    monkeypatch.setattr(backup, "utc_now", lambda: datetime(2026, 7, 26, tzinfo=UTC))

    result = backup.postgres_backup("auto")

    assert result["backup_type"] == "full"
    assert result["backup_database_bytes"] == 1234
    assert result["backup_repository_bytes"] == 567
    assert any("--type=full" in command for command in commands)
    backup_command = next(command for command in commands if "backup" in command)
    assert set(backup.POSTGRES_RETENTION_OPTIONS) <= set(backup_command)
    assert sum("check" in command for command in commands) == 2


def test_postgres_expiration_is_networkless_and_independent_of_live_container(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    config = tmp_path / "config"
    repository = tmp_path / "repository"
    spool = tmp_path / "spool"
    for path in (config, repository, spool):
        path.mkdir()
    monkeypatch.setenv("PGBACKREST_CONFIG_DIR", str(config))
    monkeypatch.setenv("PGBACKREST_REPOSITORY_DIR", str(repository))
    monkeypatch.setenv("PGBACKREST_SPOOL_DIR", str(spool))
    monkeypatch.setenv("PGBACKREST_ARCHIVE_DRAIN_SECONDS", "0")
    monkeypatch.setattr(backup, "_postgres_archive_uses_repository_lock", lambda _container: False)
    monkeypatch.setattr(
        backup,
        "run_checked",
        lambda argv, **_kwargs: commands.append(argv) or completed(),
    )
    monkeypatch.setattr(
        backup.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=400, free=600),
    )

    result = backup.postgres_expire_archives("jobseek")

    assert result == {
        "repository_capacity_bytes": 1_000,
        "repository_available_bytes": 600,
        "repository_available_ratio": 0.6,
    }
    command = commands[0]
    assert command[:4] == ["docker", "run", "--rm", "--network"]
    assert "none" in command
    assert "--read-only" in command
    assert "--entrypoint" in command
    assert "pgbackrest" in command
    assert set(backup.POSTGRES_RETENTION_OPTIONS) <= set(command)
    assert command[-1] == "expire"
    assert "exec" not in command


def test_postgres_expiration_holds_and_restores_archive_sentinel(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    sentinel = spool / "archive-enabled"
    sentinel.touch()
    observed: list[tuple[bool, bool]] = []

    monkeypatch.setenv("PGBACKREST_CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("PGBACKREST_REPOSITORY_DIR", str(tmp_path / "repository"))
    monkeypatch.setenv("PGBACKREST_SPOOL_DIR", str(spool))
    monkeypatch.setenv("PGBACKREST_ARCHIVE_DRAIN_SECONDS", "0")
    monkeypatch.setattr(backup, "_postgres_archive_uses_repository_lock", lambda _container: False)
    monkeypatch.setattr(
        backup,
        "run_checked",
        lambda _argv, **_kwargs: (
            observed.append(
                (sentinel.exists(), (spool / "archive-enabled.retention-hold").exists())
            )
            or completed()
        ),
    )
    monkeypatch.setattr(
        backup.shutil,
        "disk_usage",
        lambda _path: SimpleNamespace(total=1_000, used=400, free=600),
    )

    backup.postgres_expire_archives("jobseek")

    assert observed == [(False, True)]
    assert sentinel.is_file()
    assert not (spool / "archive-enabled.retention-hold").exists()


def test_postgres_archive_hold_refuses_active_worker_timeout(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    sentinel = spool / "archive-enabled"
    sentinel.touch()
    monkeypatch.setenv("PGBACKREST_ARCHIVE_DRAIN_SECONDS", "0")
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda *_args, **_kwargs: completed("pgbackrest archive-push segment\n"),
    )

    with (
        pytest.raises(backup.BackupError, match="timed out draining"),
        backup.postgres_archive_hold(spool, "postgres"),
    ):
        pytest.fail("archive hold must not yield while a worker is active")

    assert sentinel.is_file()
    assert not (spool / "archive-enabled.retention-hold").exists()


def test_postgres_archive_hold_drains_worker_when_archive_is_already_disabled(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    monkeypatch.setenv("PGBACKREST_ARCHIVE_DRAIN_SECONDS", "0")
    monkeypatch.setattr(
        backup.subprocess,
        "run",
        lambda *_args, **_kwargs: completed("pgbackrest archive-push segment\n"),
    )

    with (
        pytest.raises(backup.BackupError, match="timed out draining"),
        backup.postgres_archive_hold(spool, "postgres"),
    ):
        pytest.fail("archive hold must drain a worker after emergency disable")

    assert not (spool / "archive-enabled").exists()
    assert not (spool / "archive-enabled.retention-hold").exists()


def test_postgres_archive_hold_uses_crash_safe_repository_lock(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    spool = tmp_path / "spool"
    spool.mkdir()
    sentinel = spool / "archive-enabled"
    sentinel.touch()
    monkeypatch.setenv("PGBACKREST_ARCHIVE_DRAIN_SECONDS", "0")
    monkeypatch.setattr(backup, "_postgres_archive_uses_repository_lock", lambda _container: True)

    with backup.postgres_archive_hold(spool, "postgres"):
        assert sentinel.is_file()
        assert not (spool / "archive-enabled.retention-hold").exists()
        contender = (spool / "repository.lock").open("r", encoding="utf-8")
        with contender, pytest.raises(BlockingIOError):
            backup.fcntl.flock(contender, backup.fcntl.LOCK_SH | backup.fcntl.LOCK_NB)


def test_typesense_requires_root_only_configuration(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("TYPESENSE_API_KEY", raising=False)
    with pytest.raises(backup.BackupError, match="TYPESENSE_API_KEY is missing"):
        backup.typesense_backup()


def test_web_postgresql_requires_a_database_credential(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("WEB_DATABASE_URL", raising=False)
    monkeypatch.delenv("CREDENTIALS_DIRECTORY", raising=False)
    with pytest.raises(backup.BackupError, match="credential is missing"):
        backup._web_database_url()


def test_web_postgresql_boundary_rejects_an_external_foreign_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backup,
        "_web_psql",
        lambda *_args, **_kwargs: "external_fk|public.saved_job -> public.job_posting",
    )
    with pytest.raises(backup.BackupError, match="saved_job -> public.job_posting"):
        backup._validate_web_postgres_boundary(env={})


def test_web_postgresql_boundary_is_explicitly_product_only() -> None:
    selected = {f"{schema}.{table}" for schema, table in backup.WEB_POSTGRES_TABLES}
    assert "drizzle.__drizzle_migrations" in selected
    assert not any("job_posting" in table for table in selected)
    assert not any("murmur" in table for table in selected)
    assert "public.subscription" not in selected
    assert "public.enrich_batch" not in selected
    assert backup._web_postgres_bootstrap_sql() == 'CREATE SCHEMA IF NOT EXISTS "drizzle";\n'


def test_web_postgresql_boundary_rejects_a_missing_sequence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(("", 'missing_sequence|"drizzle"."__drizzle_migrations_id_seq"'))
    monkeypatch.setattr(backup, "_web_psql", lambda *_args, **_kwargs: next(outputs))
    with pytest.raises(backup.BackupError, match="missing_sequence"):
        backup._validate_web_postgres_boundary(env={})


def test_web_postgresql_boundary_requires_exact_saved_job_contract(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outputs = iter(("", "", "contract_ledger"))
    monkeypatch.setattr(backup, "_web_psql", lambda *_args, **_kwargs: next(outputs))

    with pytest.raises(backup.BackupError, match="exact 0085 catalog"):
        backup._validate_web_postgres_boundary(env={})


def test_web_postgresql_contract_checks_ledger_and_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries: list[str] = []

    def capture(sql: str, **_kwargs: object) -> str:
        queries.append(sql)
        return ""

    monkeypatch.setattr(backup, "_web_psql", capture)
    backup._validate_web_postgres_contract(env={})

    assert str(backup.WEB_POSTGRES_CONTRACT_CREATED_AT) in queries[0]
    assert backup.WEB_POSTGRES_CONTRACT_HASH in queries[0]
    assert backup._quote_literal(backup.WEB_POSTGRES_SAVED_JOB_TEXT_CHECK_DEFINITION) in queries[0]
    assert "expected_column.column_name <> 'job_posting_id'" not in queries[0]
    assert "saved_job_snapshot_text_nonblank_check" in queries[0]
    assert "saved_job_required_snapshot_check" in queries[0]
    assert "saved_job_snapshot_from_mirror_before_insert" in queries[0]
    assert "saved_job_posting_fk" in queries[0]
    assert "application_interview_saved_job_id_fkey" in queries[0]


def test_web_postgresql_contract_hash_matches_migration() -> None:
    migration = ROOT / "apps/web/drizzle/0085_saved_job_snapshot_contract.sql"

    assert hashlib.sha256(migration.read_bytes()).hexdigest() == (backup.WEB_POSTGRES_CONTRACT_HASH)


def test_web_postgresql_backup_dumps_only_the_allowlist_and_cleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    fingerprint = "\n".join(
        f"{schema}.{table}|1|{'a' * 32}" for schema, table in backup.WEB_POSTGRES_TABLES
    )
    sequence_fingerprint = "\n".join(
        f"{schema}.{sequence}|42|t" for schema, sequence in backup.WEB_POSTGRES_SEQUENCES
    )
    monkeypatch.setenv("WEB_DATABASE_URL", "postgresql://test.invalid/web")
    monkeypatch.setenv("RESTIC_REPOSITORY", "sftp:relative-repository")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", "/root-only/password")
    monkeypatch.setenv("RESTIC_SFTP_COMMAND", "ssh -i /root-only/key -p 23")
    monkeypatch.setenv("WEB_POSTGRES_STAGING_ROOT", str(tmp_path / "web-postgresql"))
    monkeypatch.setattr(backup, "_validate_web_postgres_boundary", lambda **_: None)
    monkeypatch.setattr(
        backup,
        "utc_now",
        lambda: datetime(2026, 8, 3, 12, 0, tzinfo=UTC),
    )

    def fake_psql(sql: str, **_: object) -> str:
        if sql == "SHOW server_version":
            return "17.6"
        if "last_value::bigint" in sql:
            return sequence_fingerprint
        return fingerprint

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if "pg_dump" in argv:
            mount = argv[argv.index("--volume") + 1]
            host_path = Path(mount.split(":/backup:", 1)[0])
            (host_path / "web-postgresql.dump").write_bytes(b"portable-logical-dump")
        if "pg_restore" in argv and "--list" in argv:
            toc = []
            for schema, table in backup.WEB_POSTGRES_TABLES:
                toc.append(f"1; 1259 1 TABLE {schema} {table} postgres")
                toc.append(f"2; 0 1 TABLE DATA {schema} {table} postgres")
            for schema, sequence in backup.WEB_POSTGRES_SEQUENCES:
                toc.append(f"3; 1259 2 SEQUENCE {schema} {sequence} postgres")
                toc.append(f"4; 0 0 SEQUENCE SET {schema} {sequence} postgres")
            return completed("\n".join(toc))
        if "snapshots" in argv:
            return completed(
                json.dumps(
                    [
                        {
                            "id": "abcdef0123456789",
                            "short_id": "abcdef01",
                            "time": "2026-08-03T12:00:05Z",
                        }
                    ]
                )
            )
        return completed()

    monkeypatch.setattr(backup, "_web_psql", fake_psql)
    monkeypatch.setattr(backup, "run_checked", fake_run)

    result = backup.web_postgresql_backup()

    assert result["table_count"] == len(backup.WEB_POSTGRES_TABLES)
    assert result["row_count"] == len(backup.WEB_POSTGRES_TABLES)
    assert result["repository_snapshot_id"] == "abcdef01"
    assert not (tmp_path / "web-postgresql" / "staging" / "20260803T120000Z").exists()
    dump_command = next(command for command in commands if "pg_dump" in command)
    selected = [
        dump_command[index + 1]
        for index, argument in enumerate(dump_command)
        if argument == "--table"
    ]
    assert selected == [
        backup._qualified_table(schema, table) for schema, table in backup.WEB_POSTGRES_TABLES
    ] + [
        backup._qualified_table(schema, sequence)
        for schema, sequence in backup.WEB_POSTGRES_SEQUENCES
    ]
    assert all("job_posting" not in table for table in selected)
    assert "--serializable-deferrable" in dump_command
    assert any("backup" in command for command in commands)
    assert any("forget" in command and "--prune" in command for command in commands)
    assert any("check" in command for command in commands)


def test_web_postgresql_restore_verifies_checksum_and_fingerprints(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    dump_path = tmp_path / "web-postgresql.dump"
    dump_path.write_bytes(b"restored-logical-dump")
    bootstrap_path = tmp_path / "bootstrap.sql"
    bootstrap_path.write_text(backup._web_postgres_bootstrap_sql(), encoding="utf-8")
    fingerprints = {
        f"{schema}.{table}": {"rows": 1, "digest": "b" * 32}
        for schema, table in backup.WEB_POSTGRES_TABLES
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "archive": dump_path.name,
                "archive_bytes": dump_path.stat().st_size,
                "archive_sha256": backup._sha256_file(dump_path),
                "bootstrap": bootstrap_path.name,
                "bootstrap_sha256": backup._sha256_file(bootstrap_path),
                "tables": [f"{schema}.{table}" for schema, table in backup.WEB_POSTGRES_TABLES],
                "sequences": [
                    f"{schema}.{sequence}" for schema, sequence in backup.WEB_POSTGRES_SEQUENCES
                ],
                "fingerprints": fingerprints,
                "sequence_fingerprints": {
                    f"{schema}.{sequence}": {"last_value": 42, "is_called": True}
                    for schema, sequence in backup.WEB_POSTGRES_SEQUENCES
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("WEB_DATABASE_URL", "postgresql://test.invalid/restored")
    monkeypatch.setattr(backup, "_validate_web_postgres_boundary", lambda **_: None)
    monkeypatch.setattr(
        backup,
        "_web_postgres_fingerprints",
        lambda **_: fingerprints,
    )
    monkeypatch.setattr(
        backup,
        "_web_postgres_sequence_fingerprints",
        lambda **_: {
            f"{schema}.{sequence}": {"last_value": 42, "is_called": True}
            for schema, sequence in backup.WEB_POSTGRES_SEQUENCES
        },
    )

    result = backup.verify_web_postgresql_restore(manifest_path, dump_path, bootstrap_path)

    assert result["table_count"] == len(backup.WEB_POSTGRES_TABLES)
    assert result["row_count"] == len(backup.WEB_POSTGRES_TABLES)


def test_web_postgresql_service_keeps_database_url_in_systemd_credential() -> None:
    service = (ROOT / "deploy/systemd/jobseek-web-postgresql-backup.service").read_text(
        encoding="utf-8"
    )
    workflow = (ROOT / ".github/workflows/deploy-data-backups.yml").read_text(encoding="utf-8")
    restore = (ROOT / "deploy/backups/web-postgresql/restore-drill.sh").read_text(encoding="utf-8")

    assert "LoadCredential=web-database-url:" in service
    assert "EnvironmentFile=/etc/jobseek-backup/web-postgresql.env" in service
    assert "typesense.env" not in service
    assert "postgres:17-alpine@sha256:" in service
    assert "RuntimeDirectory=jobseek-backup/web-postgresql" in service
    assert "RuntimeDirectoryPreserve=yes" in service
    assert "WEB_POSTGRES_STAGING_ROOT=/run/jobseek-backup/web-postgresql" in service
    assert "service: [postgresql, typesense, web-postgresql]" in workflow
    assert "max-parallel: 1" in workflow
    assert 'elif [[ "$JOBSEEK_BACKUP_SERVICE" != "web-postgresql" ]]' in workflow
    assert "--publish 127.0.0.1::5432" in restore
    assert "WEB_POSTGRES_DRILL_ROOT:-/run/jobseek-backup/web-postgresql/drills" in restore
    assert "--file /restore/bootstrap.sql" in restore
    assert "saved_job" in restore
    for required_snapshot_field in (
        "posting_title",
        "posting_source_url",
        "posting_first_seen_at",
        "posting_is_active",
        "company_id",
        "company_name",
        "company_slug",
    ):
        assert required_snapshot_field in restore
    assert "murmur" not in restore.lower()


def test_backup_installer_allows_only_the_staged_web_timer_to_remain_disabled() -> None:
    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")

    assert 'systemctl is-enabled --quiet "jobseek-${SERVICE}-backup.timer"' in installer
    assert '[[ "$SERVICE" == "typesense" ]] &&' in installer


def test_restic_command_injects_the_restricted_sftp_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RESTIC_SFTP_COMMAND", "ssh -i /root-only/key -p 23")
    assert backup._restic_command("check") == [
        "restic",
        "-o",
        "sftp.command=ssh -i /root-only/key -p 23",
        "check",
    ]


def test_typesense_backup_snapshots_uploads_validates_and_cleans(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    commands: list[list[str]] = []
    staging_parent = tmp_path / "typesense"
    monkeypatch.setenv("TYPESENSE_API_KEY", "test-key")
    monkeypatch.setenv("RESTIC_REPOSITORY", "sftp:relative-repository")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", "/root-only/password")
    monkeypatch.setenv("RESTIC_SFTP_COMMAND", "ssh -i /root-only/key -p 23")
    monkeypatch.setenv("TYPESENSE_SNAPSHOT_HOST_ROOT", str(staging_parent))
    monkeypatch.setattr(backup, "_snapshot_request", lambda *_: None)
    inventory = {
        "aliases": {alias: f"{alias}_v1" for alias in backup._TYPESENSE_ALIASES},
        "collection_documents": {
            alias: index for index, alias in enumerate(backup._TYPESENSE_ALIASES)
        },
    }
    monkeypatch.setattr(backup, "_typesense_inventory", lambda *_: inventory)
    monkeypatch.setattr(
        backup,
        "utc_now",
        lambda: datetime(2026, 7, 22, 2, 0, tzinfo=UTC),
    )

    def fake_run(argv: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        commands.append(argv)
        if argv[:2] == ["docker", "inspect"]:
            return completed("true\n")
        if argv[:2] == ["docker", "cp"]:
            destination = Path(argv[-1])
            destination.mkdir(parents=True, exist_ok=True)
            (destination / "state.bin").write_bytes(b"consistent-snapshot")
        if "snapshots" in argv:
            return completed(
                json.dumps(
                    [
                        {
                            "id": "1234567890abcdef",
                            "short_id": "12345678",
                            "time": "2026-07-22T02:00:05Z",
                        }
                    ]
                )
            )
        return completed()

    monkeypatch.setattr(backup, "run_checked", fake_run)

    result = backup.typesense_backup()

    assert result["snapshot_bytes"] == len(b"consistent-snapshot")
    assert result["repository_snapshot_id"] == "12345678"
    assert result["repository_snapshot_count"] == 1
    assert result["retention"] == {"keep_daily": 14, "keep_weekly": 4}
    assert result["aliases"] == inventory["aliases"]
    assert result["collection_documents"] == inventory["collection_documents"]
    assert not (staging_parent / "staging" / "20260722T020000Z").exists()
    assert any("backup" in command for command in commands)
    assert any("forget" in command and "--prune" in command for command in commands)
    assert any("check" in command for command in commands)


def test_typesense_inventory_requires_all_aliases_and_records_counts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    aliases = {alias: f"{alias}_v7" for alias in backup._TYPESENSE_ALIASES}

    def fake_get(_url: str, _key: str, path: str) -> dict[str, object]:
        if path == "/aliases":
            return {
                "aliases": [
                    {"name": alias, "collection_name": target} for alias, target in aliases.items()
                ]
            }
        alias = path.removeprefix("/collections/")
        return {"num_documents": len(alias)}

    monkeypatch.setattr(backup, "_typesense_json_get", fake_get)

    result = backup._typesense_inventory("http://127.0.0.1:8108", "key")

    assert result["aliases"] == aliases
    assert result["collection_documents"] == {
        alias: len(alias) for alias in backup._TYPESENSE_ALIASES
    }


def test_typesense_inventory_rejects_a_missing_alias(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        backup,
        "_typesense_json_get",
        lambda *_: {
            "aliases": [
                {"name": alias, "collection_name": f"{alias}_v1"}
                for alias in backup._TYPESENSE_ALIASES[:-1]
            ]
        },
    )

    with pytest.raises(backup.BackupError, match="incomplete"):
        backup._typesense_inventory("http://127.0.0.1:8108", "key")


def test_typesense_backup_rejects_inventory_changes_during_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    first = {
        "aliases": {alias: f"{alias}_v1" for alias in backup._TYPESENSE_ALIASES},
        "collection_documents": {alias: 1 for alias in backup._TYPESENSE_ALIASES},
    }
    second = {
        **first,
        "collection_documents": {
            **first["collection_documents"],
            "watchlist": 2,
        },
    }
    inventories = iter((first, second))
    monkeypatch.setenv("TYPESENSE_API_KEY", "test-key")
    monkeypatch.setenv("RESTIC_REPOSITORY", "sftp:relative-repository")
    monkeypatch.setenv("RESTIC_PASSWORD_FILE", "/root-only/password")
    monkeypatch.setenv("RESTIC_SFTP_COMMAND", "ssh -i /root-only/key -p 23")
    monkeypatch.setenv("TYPESENSE_SNAPSHOT_HOST_ROOT", str(tmp_path))
    monkeypatch.setattr(backup, "_snapshot_request", lambda *_: None)
    monkeypatch.setattr(backup, "_typesense_inventory", lambda *_: next(inventories))
    monkeypatch.setattr(
        backup,
        "run_checked",
        lambda *_args, **_kwargs: completed("true\n"),
    )

    with pytest.raises(backup.BackupError, match="inventory changed"):
        backup.typesense_backup()


def test_redact_removes_common_secret_shapes() -> None:
    value = backup.redact(
        "api_key=abc password: def Authorization: Bearer ghi "
        "postgresql://user:database-secret@example.invalid/web"
    )
    assert "abc" not in value
    assert "def" not in value
    assert "ghi" not in value
    assert "database-secret" not in value
