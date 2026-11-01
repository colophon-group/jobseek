from __future__ import annotations

import copy
import json
import unittest

from diagnose import digest, fingerprints, summarize


class FingerprintTests(unittest.TestCase):
    def setUp(self):
        self.postings = [
            {
                "id": "posting-secret",
                "source_url": "https://fixture.invalid/a",
                "titles": ["private title"],
                "technology_ids": [4, 7],
            }
        ]
        self.descriptions = [
            {
                "id": "posting-secret",
                "source_url": "https://fixture.invalid/a",
                "locale": "en",
                "html": "<p>private description</p>",
            }
        ]
        self.profiles = {"https://fixture.invalid/a": "dom_rendered"}

    def evidence(self, rows=None):
        return fingerprints(rows or self.postings, self.descriptions, self.profiles)

    def test_payload_hash_matches_existing_collector_and_omits_private_values(self):
        evidence = self.evidence()
        payload = {
            "postings": [{k: v for k, v in self.postings[0].items() if k != "id"}],
            "descriptions": [
                {k: v for k, v in self.descriptions[0].items() if k != "id"}
            ],
        }
        self.assertEqual(evidence["canonical_sha256"], digest(payload))
        encoded = json.dumps(evidence)
        for private in (
            "posting-secret",
            "fixture.invalid",
            "private title",
            "private description",
        ):
            self.assertNotIn(private, encoded)

    def test_array_order_change_identifies_exact_field_without_normalizing_parity(self):
        changed = copy.deepcopy(self.postings)
        changed[0]["technology_ids"].reverse()
        first, second = self.evidence(), self.evidence(changed)
        self.assertNotEqual(first["canonical_sha256"], second["canonical_sha256"])
        arms = [
            {"output": {"canonical_diagnostics": evidence}}
            for evidence in (first, second)
        ]
        fields = summarize(arms)["differing_fields"]
        self.assertEqual(
            [(x["profile"], x["table"], x["column"]) for x in fields],
            [("dom_rendered", "postings", "technology_ids")],
        )

    def test_missing_diagnostic_is_explicit(self):
        result = summarize([{"output": {"canonical_diagnostics": self.evidence()}}, {}])
        self.assertEqual(result["diagnosed_arms"], 1)
        self.assertEqual(result["snapshots_match_collected"], [None, None])
        self.assertTrue(result["differing_fields"])

    def test_unknown_source_cannot_silently_drop_rows(self):
        with self.assertRaises(KeyError):
            fingerprints(
                self.postings, self.descriptions, {"another-source": "dom_rendered"}
            )


if __name__ == "__main__":
    unittest.main()
