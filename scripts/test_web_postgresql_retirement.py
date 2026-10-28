#!/usr/bin/env python3
"""Retirement safety contracts, including a real PostgreSQL 17 transfer."""

from __future__ import annotations

import os
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "deploy/web-postgresql"))
import database  # noqa: E402
import repository  # noqa: E402


class SafetyTests(unittest.TestCase):
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
        ]:
            self.assertIn(boundary, workflow)
        host = (ROOT / "deploy/web-postgresql/host.py").read_text()
        self.assertIn("source_fence(src, remove=True)", host)
        self.assertIn("successful complete restore rehearsal is required", host)
        self.assertNotIn("GRANT SELECT ON ALL TABLES", host)

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
        for name in self.names:
            self.admin.sql(f"CREATE DATABASE {database.identifier(name)};")
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
        self.admin.close()
        self.directory.cleanup()

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
