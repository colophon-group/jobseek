"""Freeze original root-array selection and ConnX field/URL projection."""

from __future__ import annotations

import csv
import dataclasses
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.core.monitors.api_sniffer import _extract_rich  # noqa: E402
from src.shared.api_sniff import extract_items  # noqa: E402

output = []
for row in csv.DictReader((ROOT / "data/boards.csv").open()):
    if row["board_slug"] not in {"cox-careers-au", "fresenius-kabi-australia-new-zealand"}:
        continue
    metadata = json.loads(row["monitor_config"])
    assert metadata["json_path"] == "$"
    metadata["browser"] = False
    metadata["scraper_type"] = "skip"
    sample = {
        "guid": "00112233-4455-6677-8899-aabbccddeeff",
        "name": "Senior Engineer",
        "location": "Sydney",
        "summary": "<p>Build systems.</p>",
        "detail": "<p>Work with researchers.</p>",
        "department": "Engineering",
        "employmentType": "Full-time",
        "code": "R-101",
        "dateAdvertised": "2026-10-10",
        "advertisingCloseDate": "2026-11-10",
        "availableDate": "2026-10-12",
    }
    second = {**sample, "guid": "11223344-5566-7788-99aa-bbccddeeff00", "name": "Research Engineer"}
    for label, payload in [
        ("one", [sample]),
        ("two", [sample, second]),
        ("empty", []),
        ("nonobjects-filtered", [None, "unrelated", sample]),
    ]:
        jobs = _extract_rich(
            extract_items(payload, "$"),
            metadata["fields"],
            metadata.get("url_field"),
            metadata["url_template"],
            row["board_url"],
        )
        expected = []
        for job in jobs:
            fields = dataclasses.asdict(job)
            assert fields.pop("language") is None
            # SDK job containers allocate an empty map for absent original extras.
            fields["extras"] = fields["extras"] or {}
            expected.append(fields)
        output.append(
            {
                "name": row["board_slug"] + "/" + label,
                "board_url": row["board_url"],
                "metadata": metadata,
                "expected": expected,
                "truncated": False,
                "url_only": False,
                "responses": [{"page": 0, "status": 200, "data": payload}],
            }
        )
assert len(output) == 8
Path(__file__).with_name("python_api_root_inventory.json").write_text(
    json.dumps(output, indent=2, ensure_ascii=False) + "\n"
)
print("Frozen eight original API root inventory/field contracts")
