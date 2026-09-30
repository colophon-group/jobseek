"""Freeze the existing location regression suite; no publisher or DB requests.

Run from apps/crawler:
 JOB_ENRICHMENT_ENGINE=python uv run --frozen python go/job-enrichment/testdata/generate_location.py
"""

from __future__ import annotations

import hashlib
import json
import os
import random
import re
from dataclasses import asdict
from pathlib import Path

import pytest

from src.core import location_resolve as legacy
from src.core.location_resolve import LocationResolver

os.environ["JOB_ENRICHMENT_ENGINE"] = "python"
root = Path("go/job-enrichment")
fixtures = {}
cases = []
original = LocationResolver.resolve


def record(self, raw_locations, job_location_type=None, posting_language=None):
    assert self._db is not None
    fixture = {
        "entries": self._db.execute(
            "SELECT id,parent_id,loc_type,population,languages FROM entry"
        ).fetchall(),
        "names": self._db.execute("SELECT name,location_id FROM name_index").fetchall(),
        "display": self._db.execute("SELECT location_id,name FROM display_name").fetchall(),
    }
    signature = hashlib.sha256(json.dumps(fixture, ensure_ascii=False).encode()).hexdigest()
    fixtures[signature] = fixture
    before = sorted(self._misses)
    misses_before = len(self._location_misses)
    result = original(self, raw_locations, job_location_type, posting_language)
    cases.append(
        {
            "fixture": signature,
            "raw": raw_locations,
            "fallback": job_location_type,
            "language": posting_language,
            "tracking": self._tracking,
            "negative": sorted(self._negative),
            "misses_before": before,
            "lookup_misses": sorted(self._misses),
            "location_misses": [
                {"raw_value": a, "sample_value": b}
                for a, b in self._location_misses[misses_before:]
            ],
            "expected": [asdict(r) for r in result],
        }
    )
    return result


rules = {
    "source_sha256": hashlib.sha256(Path("src/core/location_resolve.py").read_bytes()).hexdigest(),
    "maps": {
        name: value
        for name, value in vars(legacy).items()
        if name.startswith("_")
        and isinstance(value, dict)
        and value
        and all(isinstance(v, str) for v in value.values())
    },
    "patterns": {
        name: {"pattern": value.pattern, "ignore_case": bool(value.flags & re.I)}
        for name, value in vars(legacy).items()
        if isinstance(value, re.Pattern)
    },
    "digits": [],
}
for n in range(0x110000):
    if chr(n).isdigit():
        if rules["digits"] and rules["digits"][-1][1] == n - 1:
            rules["digits"][-1][1] = n
        else:
            rules["digits"].append([n, n])
(root / "location_rules.json").write_text(json.dumps(rules, ensure_ascii=False, indent=2) + "\n")
rng = random.Random(930)
sets = []
for size in range(1, 150):
    ids = [rng.randint(1, 9999999) for _ in range(size)]
    sets.append({"input": ids, "order": list(set(ids))})
for size in range(1, 100):
    ids = [1 + n * 8 for n in range(size)]
    sets.append({"input": ids, "order": list(set(ids))})
(root / "testdata/python_location_sets.json").write_text(
    "[\n" + ",\n".join(json.dumps(case, separators=(",", ":")) for case in sets) + "\n]\n"
)

LocationResolver.resolve = record
status = pytest.main(["-q", "tests/test_location_resolve.py"])
assert status == 0, status
(root / "testdata/python_location.json").write_text(
    json.dumps(
        {
            "source_sha256": hashlib.sha256(
                Path("src/core/location_resolve.py").read_bytes()
            ).hexdigest(),
            "fixtures": fixtures,
            "cases": cases,
        },
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(f"{len(cases)} location cases, {len(fixtures)} indexes")
