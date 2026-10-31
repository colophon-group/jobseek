import importlib.util
from pathlib import Path
import unittest
from unittest.mock import patch
import urllib.error
import re

spec = importlib.util.spec_from_file_location("migration", Path(__file__).with_name("migrate-secrets-to-doppler.py"))
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class TransferTests(unittest.TestCase):
    def test_allowlist_excludes_bootstrap_and_inherited_unselected_secrets(self):
        selected = migration.select_values("repository", {"KEY": "fixture", "DOPPLER_IMPORT_TOKEN": "bootstrap", "github_token": "runner", "UNRELATED": "other"}, {"repository": ["KEY"]})
        self.assertEqual(selected, {"KEY": "fixture"})

    def test_missing_secrets_and_unknown_sources_fail_before_network(self):
        for source, values in [("arbitrary", {"KEY": "fixture"}), ("repository", {}), ("repository", {"KEY": ""})]:
            with self.assertRaises(migration.MigrationError):
                migration.select_values(source, values, {"repository": ["KEY"]})

    def test_bootstrap_cannot_be_added_to_allowlist(self):
        for name in ["DOPPLER_IMPORT_TOKEN", "github_token"]:
            with self.assertRaises(migration.MigrationError):
                migration.select_values("repository", {name: "fixture"}, {"repository": [name]})

    def test_round_trip_preserves_multiline_values(self):
        store = {"DOPPLER_PROJECT": migration.PROJECT, "DOPPLER_CONFIG": "import_repository"}
        calls = []

        def transport(token, config, values=None):
            calls.append(values)
            self.assertEqual(config, "import_repository")
            if values is not None:
                store.update(values)
            return store.copy()

        value = "first line\nsecond line\n"
        self.assertEqual(migration.migrate("repository", "dp.st.fixture", {"KEY": value}, {"repository": ["KEY"]}, transport), 1)
        self.assertEqual(store["KEY"], value)
        self.assertEqual(len(calls), 3)

    def test_wrong_scope_and_conflicts_do_not_write(self):
        for current in [{"DOPPLER_PROJECT": "other"}, {"DOPPLER_PROJECT": migration.PROJECT, "DOPPLER_CONFIG": "import_repository", "KEY": "existing"}]:
            def transport(token, config, values=None):
                self.assertIsNone(values)
                return current

            with self.assertRaises(migration.MigrationError):
                migration.migrate("repository", "dp.st.fixture", {"KEY": "incoming"}, {"repository": ["KEY"]}, transport)

    def test_mismatched_verification_fails(self):
        def transport(token, config, values=None):
            return {"DOPPLER_PROJECT": migration.PROJECT, "DOPPLER_CONFIG": "import_repository"}

        with self.assertRaises(migration.MigrationError):
            migration.migrate("repository", "dp.st.fixture", {"KEY": "incoming"}, {"repository": ["KEY"]}, transport)

    def test_redirects_are_refused(self):
        with self.assertRaises(migration.MigrationError):
            migration.NoRedirect().redirect_request(None, None, 302, "redirect", {}, "https://other.invalid")

    def test_provider_failures_do_not_disclose_payloads(self):
        with patch.object(migration.urllib.request, "build_opener") as opener:
            opener.return_value.open.side_effect = urllib.error.URLError("PRIVATE_FIXTURE_SENTINEL")
            with self.assertRaises(migration.MigrationError) as error:
                migration.request("dp.st.PRIVATE_FIXTURE_SENTINEL", "import_repository", {"KEY": "PRIVATE_FIXTURE_SENTINEL"})
            self.assertNotIn("PRIVATE_FIXTURE_SENTINEL", str(error.exception))

    def test_workflow_never_serializes_the_entire_secret_context(self):
        workflow = (Path(__file__).parent.parent / ".github/workflows/migrate-secrets-to-doppler.yml").read_text()
        self.assertIsNone(re.search(r"tojson\s*\(\s*secrets\s*\)", workflow, re.I))
        self.assertNotIn("pull_request:", workflow)
        self.assertNotIn("schedule:", workflow)


if __name__ == "__main__":
    unittest.main()
