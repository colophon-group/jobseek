#!/usr/bin/env python3
"""Retirement safety contracts, including a real PostgreSQL 17 transfer."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import uuid
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/web-postgresql"))
import database
import host
import repository


class SafetyTests(unittest.TestCase):
    def test_database_locale_values_cannot_inject_sql(self):
        locale = {
            "encoding": "UTF8",
            "provider": "i",
            "collate": "en_US.UTF-8",
            "ctype": "en_US.UTF-8",
            "locale": "en-US",
            "icu_rules": None,
        }
        self.assertIn(
            "LOCALE_PROVIDER icu",
            database.create_database_sql("fixture", "owner", locale),
        )
        for key, value in [
            ("locale", "en-US'; DROP DATABASE postgres;--"),
            ("provider", "unknown"),
            ("icu_rules", "custom"),
        ]:
            with self.subTest(key=key), self.assertRaises(database.DatabaseError):
                database.create_database_sql("fixture", "owner", {**locale, key: value})

    def test_locale_alignment_refuses_user_objects_or_sessions(self):
        locale = {
            "encoding": "UTF8",
            "provider": "i",
            "collate": "en_US.UTF-8",
            "ctype": "en_US.UTF-8",
            "locale": "en-US",
            "icu_rules": None,
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.object(host, "STATE", Path(directory)),
        ):
            for identity, objects in [
                ({"owned": False, "sessions": 0}, "0"),
                ({"owned": True, "sessions": 1}, "0"),
                ({"owned": True, "sessions": 0}, "1"),
            ]:
                with patch.object(
                    host, "admin", side_effect=[json.dumps(identity), objects, ""]
                ) as admin:
                    with self.assertRaises(database.DatabaseError):
                        host.prepare_empty_database_locale("jobseek_web", locale)
                    self.assertFalse(
                        any("DROP DATABASE" in c.args[0] for c in admin.call_args_list)
                    )
                    if objects == "1":
                        self.assertIn(
                            "ALLOW_CONNECTIONS false", admin.call_args_list[1].args[0]
                        )
                        self.assertIn(
                            "ALLOW_CONNECTIONS true", admin.call_args_list[-1].args[0]
                        )

    def test_no_remote_plaintext_or_transaction_pooler(self):
        for url in [
            "postgresql://u:p@example.org/db?sslmode=disable",
            "postgresql://u:p@example.org:6543/db",
            "https://example.org/db",
        ]:
            with self.subTest(url=url), self.assertRaises(database.DatabaseError):
                database.connection_environment(url)

    def test_public_destination_verifies_certificate(self):
        settings = database.connection_environment(
            "postgresql://u:p@web-postgres.jseek.co:5433/db"
        )
        self.assertEqual(settings["PGSSLMODE"], "verify-full")
        self.assertEqual(settings["PGSSLROOTCERT"], "system")
        self.assertEqual(settings["PGOPTIONS"], "-c timezone=UTC")

    def test_no_credential_in_client_arguments(self):
        with tempfile.TemporaryDirectory() as directory:
            client = database.Client(
                "postgresql://u:secret-fixture@example.org/db", Path(directory)
            )
            try:
                args = client.command("pg_dump", "--format=custom")
                self.assertNotIn("secret-fixture", " ".join(args))
                self.assertNotIn("postgresql://", " ".join(args))
                self.assertEqual(client.env_file.stat().st_mode & 0o777, 0o600)
            finally:
                client.close()
            self.assertFalse(client.env_file.exists())

    def test_identifiers_are_confined(self):
        self.assertEqual(
            database.identifier("ai_filter_decision"), '"ai_filter_decision"'
        )
        for value in ["public.user", "x; DROP DATABASE postgres", "../x", 'x"', ""]:
            with self.assertRaises(database.DatabaseError):
                database.identifier(value)

    def test_private_credential_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "credential"
            file.write_text("fixture")
            file.chmod(0o644)
            with self.assertRaises(database.DatabaseError):
                database.private_file(file)
            file.chmod(0o600)
            self.assertEqual(database.private_file(file), "fixture")
            link = Path(directory) / "link"
            link.symlink_to(file)
            with self.assertRaises(database.DatabaseError):
                database.private_file(link)

    def test_workflow_authorization_and_cutover_precondition(self):
        workflow = (ROOT / ".github/workflows/operate-web-postgresql.yml").read_text()
        for boundary in [
            "refs/heads/main",
            "TRIGGERING_ACTOR",
            "viktor-shcherb",
            "restore-source",
            "cancel-in-progress: false",
            "WEB-POSTGRESQL-${ACTION^^}",
            "production-backup-operations",
            "deploy/ai-filter-refresh/pause-remote.sh",
            "hetzner-ingress-production",
            "verify-server",
            "Prove external certificate verification and SCRAM login",
            "deploy/web-postgresql/ingress.py rollback",
            "prepare-worker-access",
        ]:
            self.assertIn(boundary, workflow)
        host = (ROOT / "deploy/web-postgresql/host.py").read_text()
        self.assertIn("source_fence(src, remove=True)", host)
        self.assertIn("successful complete restore rehearsal is required", host)
        self.assertNotIn("GRANT SELECT ON ALL TABLES", host)

    def test_runner_worker_login_is_proven_before_source_pause(self):
        workflow = (ROOT / ".github/workflows/operate-web-postgresql.yml").read_text()
        proof = workflow.index(
            "name: Require the deployment runner can use the restricted worker login"
        )
        pause = workflow.index("name: Pause and verify the standalone matching process")
        self.assertLess(proof, pause)
        probe = (ROOT / "deploy/web-postgresql/verify-worker-access.sh").read_text()
        for contract in [
            "PGUSER=jobseek_narrowed_worker",
            "PGSSLMODE=verify-full",
            "default_transaction_read_only=on",
            "rolconnlimit=6",
            "rolsuper OR rolcreatedb OR rolcreaterole",
            'test "$result" = t',
        ]:
            self.assertIn(contract, probe)
        self.assertIn(
            "hostssl all jobseek_narrowed_worker 0.0.0.0/0 scram-sha-256", host.HBA
        )
        self.assertNotIn("host all all 0.0.0.0/0 trust", host.HBA)

    def test_repository_recovery_does_not_force_live_locks(self):
        with (
            patch.object(repository.subprocess, "run", return_value=Mock(returncode=0)),
            patch.object(repository, "restic") as unlock,
        ):
            with self.assertRaises(database.DatabaseError):
                repository.recover_repository()
            unlock.assert_not_called()
        with (
            patch.object(repository.subprocess, "run", return_value=Mock(returncode=1)),
            patch.object(repository, "restic") as unlock,
        ):
            repository.recover_repository()
            unlock.assert_called_once_with("unlock", timeout=180)

    def test_grouped_restic_receipt_binds_the_new_packet(self):
        packet = Path("/run/fixture/packet-1")
        snapshot = {
            "id": "a" * 64,
            "hostname": repository.HOST,
            "tags": [repository.TAG],
            "paths": [str(packet)],
        }
        group = {
            "group_key": {
                "hostname": repository.HOST,
                "paths": None,
                "tags": [repository.TAG],
            },
            "snapshots": [snapshot],
        }
        self.assertEqual(
            repository.backup_snapshot(json.dumps([group]), packet), snapshot
        )
        for invalid in [[], [snapshot], [group, group], [{**group, "snapshots": []}]]:
            with (
                self.subTest(invalid=invalid),
                self.assertRaises(database.DatabaseError),
            ):
                repository.backup_snapshot(json.dumps(invalid), packet)
        prior = {**snapshot, "id": "b" * 64, "paths": ["/run/fixture/packet-0"]}
        with self.assertRaises(database.DatabaseError):
            repository.backup_snapshot(
                json.dumps([{**group, "snapshots": [prior, snapshot]}]), packet
            )
        for field, value in [
            ("id", "short"),
            ("paths", ["/old-packet"]),
            ("hostname", "other"),
            ("tags", ["old-generation"]),
        ]:
            invalid = {**group, "snapshots": [{**snapshot, field: value}]}
            with self.subTest(field=field), self.assertRaises(database.DatabaseError):
                repository.backup_snapshot(json.dumps([invalid]), packet)

    def test_backup_snapshot_query_filters_the_exact_packet(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "status").mkdir()
            packet = root / "packet-1000"
            packet.mkdir()
            manifest = {
                "archive_bytes": 1,
                "archive_sha256": "c" * 64,
                "catalog": {"tables": ["fixture"]},
                "fingerprints": {"tables": {"fixture": {"rows": 1}}},
            }

            def restic(*args):
                if args[0] != "snapshots":
                    return ""
                self.assertIn("--path", args)
                self.assertEqual(args[args.index("--path") + 1], str(packet))
                return json.dumps(
                    [
                        {
                            "group_key": {
                                "hostname": repository.HOST,
                                "paths": None,
                                "tags": [repository.TAG],
                            },
                            "snapshots": [
                                {
                                    "id": "a" * 64,
                                    "hostname": repository.HOST,
                                    "tags": [repository.TAG],
                                    "paths": [str(packet)],
                                }
                            ],
                        }
                    ]
                )

            with (
                patch.object(repository, "ROOT", root),
                patch.object(repository, "STATUS", root / "status"),
                patch.object(repository, "restic", side_effect=restic),
                patch.object(repository.time, "time", return_value=1000),
                patch.object(
                    database,
                    "private_file",
                    return_value="postgresql://u:p@supabase.example/db",
                ),
                patch.object(database, "Client"),
                patch.object(database, "dump", return_value=manifest),
            ):
                result = repository.backup(root / "credential")
            self.assertTrue(result["success"])
            self.assertEqual(
                result["last_success_details"]["repository_snapshot_id"], "a" * 64
            )

    def test_failed_backup_packets_cannot_grow_without_bound(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for n in range(4):
                path = root / f"packet-{n}"
                path.mkdir()
                os.utime(path, (1000 + n, 1000 + n))
            with (
                patch.object(repository, "ROOT", root),
                patch.object(repository.time, "time", return_value=1100),
            ):
                repository.clean_packets()
            self.assertEqual(
                sorted(p.name for p in root.iterdir()), ["packet-2", "packet-3"]
            )

    def test_repository_uses_the_existing_protected_password_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            password = root / "password"
            password.write_text("fixture-secret")
            password.chmod(0o600)
            config = root / "repository.env"
            config.write_text(
                f'RESTIC_REPOSITORY=sftp:fixture\nRESTIC_PASSWORD_FILE={password}\nRESTIC_SFTP_COMMAND="ssh fixture"\n'
            )
            config.chmod(0o600)
            with (
                patch.object(repository, "ENV", config),
                patch.dict(os.environ, {"RESTIC_PASSWORD": "ambient-value"}),
            ):
                environment = repository.repository_environment()
                self.assertEqual(environment["RESTIC_PASSWORD_FILE"], str(password))
                self.assertNotIn("RESTIC_PASSWORD", environment)
                password.chmod(0o644)
                with self.assertRaises(database.DatabaseError):
                    repository.repository_environment()

    def test_fence_commit_followed_by_transport_failure_is_unfenced(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            locale = {
                "encoding": "UTF8",
                "provider": "c",
                "collate": "C",
                "ctype": "C",
                "locale": None,
                "icu_rules": None,
            }
            (root / "rehearsal.json").write_text(
                json.dumps({"success": True, "database_locale": locale})
            )

            @contextmanager
            def snapshot():
                yield lambda query: locale if "'encoding'" in query else 0, "fixture"

            source = Mock()
            source.snapshot = snapshot
            destination = Mock()
            destination.sql.side_effect = lambda query: (
                json.dumps(locale) if "'encoding'" in query else "0"
            )

            def fence(_client, *, remove=False):
                if not remove:
                    raise database.DatabaseError("transport failed after fence commit")

            with (
                patch.object(host, "STATE", root),
                patch.object(host, "CONFIG", root),
                patch.object(host, "RUNTIME", root),
                patch.object(
                    host,
                    "__file__",
                    "/opt/jobseek-web-postgresql/releases/"
                    + "a" * 40
                    + "/deploy/web-postgresql/host.py",
                ),
                patch.object(host, "config", return_value={}),
                patch.object(database, "private_file", return_value="fixture"),
                patch.object(
                    database, "Client", side_effect=[source, source, destination]
                ),
                patch.object(database, "source_fence", side_effect=fence) as guarded,
            ):
                with self.assertRaises(database.DatabaseError):
                    host.copy_database(rehearsal=False)
                self.assertEqual(guarded.call_count, 2)
                guarded.assert_called_with(source, remove=True)
            self.assertFalse(
                json.loads((root / "source-fence.json").read_text())["fenced"]
            )


@unittest.skipUnless(
    os.environ.get("WEB_DB_TEST_URL"), "explicit disposable PostgreSQL fixture required"
)
class TransferIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="jobseek-web-db-test-")
        self.root = Path(self.directory.name)
        self.native = (
            Path(os.environ["WEB_DB_NATIVE_BIN"])
            if os.environ.get("WEB_DB_NATIVE_BIN")
            else None
        )
        self.admin = database.Client(
            os.environ["WEB_DB_TEST_URL"], self.root, native_bin=self.native
        )
        self.prefix = "web_db_fixture_" + uuid.uuid4().hex[:10]
        self.names = [self.prefix + suffix for suffix in ["_source", "_target"]]
        self.clients = []
        self.extra_roles = []
        for name in self.names:
            self.admin.sql(
                f"CREATE DATABASE {database.identifier(name)} TEMPLATE template0 ENCODING 'UTF8';"
            )
            parsed = urlsplit(os.environ["WEB_DB_TEST_URL"])
            url = urlunsplit(parsed._replace(path="/" + name))
            self.clients.append(database.Client(url, self.root, native_bin=self.native))
        self.source, self.target = self.clients
        for role in database.POLICY_ROLES:
            self.admin.sql(
                f"DO $$ BEGIN IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname='{role}') "
                f"THEN CREATE ROLE {role} NOLOGIN; END IF; END $$;"
            )
        self.source.sql("""CREATE SCHEMA drizzle;
          CREATE TABLE drizzle.__drizzle_migrations(id bigserial PRIMARY KEY,hash text,created_at bigint);
          INSERT INTO drizzle.__drizzle_migrations(hash,created_at) VALUES ('fixture',123);
          CREATE TABLE public.watchlist(id uuid PRIMARY KEY,title text);
          INSERT INTO public.watchlist VALUES ('00000000-0000-0000-0000-000000000001','fixture');
          CREATE TABLE public.ai_filter_decision(id bigserial PRIMARY KEY,
            watchlist_id uuid REFERENCES public.watchlist(id),expires_at timestamptz);
          INSERT INTO public.ai_filter_decision(watchlist_id,expires_at)
            VALUES ('00000000-0000-0000-0000-000000000001','2026-12-01T10:00:00Z');
          CREATE TABLE public.stripe_subscription(id text PRIMARY KEY,entitled boolean);
          INSERT INTO public.stripe_subscription VALUES ('fixture',true);
          CREATE TABLE public.notification_delivery(id bigserial PRIMARY KEY,state text);
          INSERT INTO public.notification_delivery(state) VALUES ('submitted');
          CREATE TABLE public.future_application_table(id bigserial PRIMARY KEY,value text);
          INSERT INTO public.future_application_table(value) VALUES ('complete backup');
          ALTER TABLE public.ai_filter_decision ENABLE ROW LEVEL SECURITY;
          CREATE POLICY worker_read ON public.ai_filter_decision FOR SELECT TO jobseek_narrowed_worker USING(true);
          GRANT USAGE ON SCHEMA public TO jobseek_narrowed_worker;
          GRANT SELECT,INSERT,UPDATE ON public.ai_filter_decision TO jobseek_narrowed_worker;
          GRANT USAGE ON SEQUENCE public.ai_filter_decision_id_seq TO jobseek_narrowed_worker;
          GRANT USAGE ON SCHEMA drizzle TO jobseek_migration_auditor;
          GRANT SELECT ON drizzle.__drizzle_migrations TO jobseek_migration_auditor;
          CREATE TABLE public.utc_budget(month_start timestamptz
            CHECK(month_start=date_trunc('month',month_start)));
          INSERT INTO public.utc_budget VALUES ('2026-10-01T00:00:00Z');""")

    def tearDown(self):
        for client in self.clients:
            client.close()
        for name in self.names:
            self.admin.sql(f"DROP DATABASE {database.identifier(name)} WITH (FORCE);")
        for role in self.extra_roles:
            self.admin.sql(f"DROP ROLE {database.identifier(role)};")
        self.admin.close()
        self.directory.cleanup()

    def test_icu_locale_is_preserved_and_mismatch_refused_before_restore(self):
        locale = {
            "encoding": "UTF8",
            "provider": "i",
            "collate": "en_US.UTF-8",
            "ctype": "en_US.UTF-8",
            "locale": "en-US",
            "icu_rules": None,
        }
        name = self.prefix + "_icu"
        owner = self.prefix + "_owner"
        self.admin.sql(f"CREATE ROLE {database.identifier(owner)} NOLOGIN;")
        self.extra_roles.append(owner)
        self.admin.sql(database.create_database_sql(name, owner, locale))
        self.names.append(name)
        parsed = urlsplit(os.environ["WEB_DB_TEST_URL"])
        client = database.Client(
            urlunsplit(parsed._replace(path="/" + name)),
            self.root,
            native_bin=self.native,
        )
        self.clients.append(client)
        client.sql(
            "CREATE SCHEMA drizzle; CREATE TABLE drizzle.__drizzle_migrations(id int); "
            "CREATE TABLE public.words(value text); INSERT INTO words VALUES ('z'),('Å'),('ä'),('a');"
        )
        packet = self.root / "icu-packet"
        manifest = database.dump(client, packet)
        self.assertEqual(manifest["catalog"]["database_locale"], locale)
        with self.assertRaisesRegex(database.DatabaseError, "locale differs"):
            database.restore(self.target, packet)
        self.assertEqual(
            self.target.sql("SELECT count(*) FROM pg_tables WHERE schemaname='public'"),
            "0",
        )
        self.admin.sql(f"DROP DATABASE {database.identifier(self.names[1])};")
        self.admin.sql(database.create_database_sql(self.names[1], owner, locale))
        proof = database.restore(self.target, packet)
        self.assertEqual(proof["database_locale"], locale)
        ordering = "SELECT json_agg(value ORDER BY value) FROM words"
        self.assertEqual(client.sql(ordering), self.target.sql(ordering))

    def test_complete_roundtrip_preserves_new_tables_constraints_roles_and_sequence(
        self,
    ):
        manifest = database.dump(self.source, self.root / "packet")
        self.assertIn(
            "public.future_application_table", manifest["fingerprints"]["tables"]
        )
        self.assertIn("public.stripe_subscription", manifest["fingerprints"]["tables"])
        self.assertIn("public.ai_filter_decision", manifest["fingerprints"]["tables"])
        proof = database.restore(self.target, self.root / "packet")
        self.assertTrue(proof["success"])
        self.assertEqual(proof["table_count"], 7)
        with self.assertRaises(database.DatabaseError):
            self.target.sql(
                "INSERT INTO ai_filter_decision(watchlist_id) VALUES ('00000000-0000-0000-0000-000000000002');"
            )
        with self.assertRaises(database.DatabaseError):
            database.restore(self.target, self.root / "packet")
        self.assertEqual(
            self.target.sql("SELECT count(*) FROM stripe_subscription;"), "1"
        )

    def test_destination_owner_needs_no_superuser_or_role_creation_privilege(self):
        role = self.prefix + "_owner"
        self.admin.sql(
            f"CREATE ROLE {database.identifier(role)} LOGIN NOSUPERUSER NOCREATEDB "
            "NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD 'fixture-password';"
        )
        client = None
        try:
            self.admin.sql(
                f"ALTER DATABASE {database.identifier(self.names[1])} OWNER TO {database.identifier(role)};"
            )
            parsed = urlsplit(os.environ["WEB_DB_TEST_URL"])
            connection = parsed._replace(
                netloc=f"{role}:fixture-password@{parsed.hostname}:{parsed.port or 5432}",
                path="/" + self.names[1],
            )
            client = database.Client(
                urlunsplit(connection), self.root, native_bin=self.native
            )
            database.dump(self.source, self.root / "packet")
            self.assertTrue(database.restore(client, self.root / "packet")["success"])
        finally:
            if client:
                client.close()
            self.admin.sql(
                f"DROP DATABASE {database.identifier(self.names[1])} WITH (FORCE);"
            )
            self.admin.sql(f"DROP ROLE {database.identifier(role)};")
            self.admin.sql(f"CREATE DATABASE {database.identifier(self.names[1])};")

    def test_source_fence_is_not_carried_to_destination(self):
        database.source_fence(self.source)
        try:
            for statement in [
                "INSERT INTO watchlist VALUES ('00000000-0000-0000-0000-000000000002','blocked');",
                "UPDATE watchlist SET title='blocked';",
                "DELETE FROM ai_filter_decision;",
                "TRUNCATE ai_filter_decision;",
            ]:
                with (
                    self.subTest(statement=statement),
                    self.assertRaises(database.DatabaseError),
                ):
                    self.source.sql(statement)
            database.dump(self.source, self.root / "packet")
            database.restore(self.target, self.root / "packet")
            self.target.sql("UPDATE watchlist SET title='destination writable';")
            self.assertEqual(
                self.target.sql("SELECT count(*) FROM ai_filter_decision;"), "1"
            )
        finally:
            database.source_fence(self.source, remove=True)
        self.source.sql("UPDATE watchlist SET title='source recovered';")

    def test_tampered_archive_rejected_before_destination_mutation(self):
        database.dump(self.source, self.root / "packet")
        archive = self.root / "packet/database.dump"
        with archive.open("ab") as stream:
            stream.write(b"tampered")
        with self.assertRaises(database.DatabaseError):
            database.restore(self.target, self.root / "packet")
        self.assertEqual(
            self.target.sql(
                "SELECT count(*) FROM pg_tables WHERE schemaname='public';"
            ),
            "0",
        )

    def test_external_foreign_key_refused(self):
        self.source.sql(
            "CREATE SCHEMA outside; CREATE TABLE outside.parent(id int PRIMARY KEY); "
            "CREATE TABLE public.child(id int REFERENCES outside.parent(id));"
        )
        with self.assertRaises(database.DatabaseError):
            database.dump(self.source, self.root / "packet")

    def test_live_sequence_advance_does_not_break_consistent_backup(self):
        original = self.source.run

        def run(program, *arguments, **kwargs):
            value = original(program, *arguments, **kwargs)
            if program == "pg_dump":
                self.source.sql("SELECT nextval('public.ai_filter_decision_id_seq');")
            return value

        self.source.run = run
        manifest = database.dump(self.source, self.root / "packet")
        self.assertNotEqual(
            manifest["fingerprints"]["sequences"], manifest["sequence_upper_bounds"]
        )
        self.assertTrue(database.restore(self.target, self.root / "packet")["success"])


if __name__ == "__main__":
    unittest.main()
