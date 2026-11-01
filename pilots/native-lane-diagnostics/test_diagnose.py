from __future__ import annotations

import copy
import json
import unittest

from diagnose import digest, fingerprints, publication_probe, summarize


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
            "descriptions": [{k: v for k, v in self.descriptions[0].items() if k != "id"}],
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
        arms = [{"output": {"canonical_diagnostics": evidence}} for evidence in (first, second)]
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
            fingerprints(self.postings, self.descriptions, {"another-source": "dom_rendered"})

    def test_probe_identifies_exact_pre_upload_hash_without_exposing_identity(self):
        current = copy.deepcopy(self.postings)
        current[0].update(description_r2_hash="uploaded-hash", to_be_enriched=True)
        prior = [{k: v for k, v in current[0].items() if k != "id"}]
        prior[0].update(description_r2_hash=None, to_be_enriched=False)
        descriptions = [{k: v for k, v in self.descriptions[0].items() if k != "id"}]
        target = digest({"postings": prior, "descriptions": descriptions})
        matches = publication_probe(current, self.descriptions, self.profiles, target)
        self.assertEqual(matches, [{"profile": "dom_rendered", "kind": "pre_upload", "rows": 1}])
        self.assertEqual(publication_probe(current, self.descriptions, self.profiles, "0" * 64), [])
        current_payload = {
            "postings": [{k: v for k, v in current[0].items() if k != "id"}],
            "descriptions": descriptions,
        }
        self.assertEqual(
            publication_probe(current, self.descriptions, self.profiles, digest(current_payload)),
            [],
        )

    def test_probe_identifies_partial_small_profile_upload_window(self):
        current = [
            {
                "source_url": f"https://fixture.invalid/{i}",
                "description_r2_hash": "uploaded",
                "to_be_enriched": True,
            }
            for i in range(3)
        ]
        current.insert(
            0,
            {
                "source_url": "https://fixture.invalid/prefix",
                "description_r2_hash": "uploaded",
                "to_be_enriched": True,
            },
        )
        profiles = {row["source_url"]: "dom_rendered" for row in current}
        profiles[current[0]["source_url"]] = "api_rich"
        prior = copy.deepcopy(current)
        for index in (1, 3):
            prior[index].update(description_r2_hash=None, to_be_enriched=False)
        target = digest({"postings": prior, "descriptions": []})
        self.assertEqual(
            publication_probe(current, [], profiles, target),
            [{"profile": "dom_rendered", "kind": "pre_upload", "rows": 2}],
        )


if __name__ == "__main__":
    unittest.main()
