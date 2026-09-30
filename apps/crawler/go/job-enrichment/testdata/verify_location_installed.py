"""Check installed public location routing offline, with a private scratch index."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, "/app")
os.environ["JOB_ENRICHMENT_ENGINE"] = "go"
from src.core.location_resolve import LocationResolver  # noqa: E402

corpus = json.loads(Path(__file__).with_name("python_location.json").read_text())
checked = 0
for signature, fixture in corpus["fixtures"].items():
    resolver = LocationResolver()
    resolver._init_db()
    db = resolver._db
    assert db is not None and resolver._go is not None
    try:
        for table, rows, marks in [
            ("entry", fixture["entries"], "?,?,?,?,?"),
            ("name_index", fixture["names"], "?,?"),
            ("display_name", fixture["display"], "?,?"),
        ]:
            db.executemany(f"INSERT INTO {table} VALUES ({marks})", rows)
        db.executescript("CREATE INDEX idx_name ON name_index(name)")
        db.commit()
        resolver._loaded = True
        for case in corpus["cases"]:
            if case["fixture"] != signature:
                continue
            resolver._tracking = case["tracking"]
            resolver._negative = set(case["negative"])
            resolver._misses = set(case["misses_before"])
            values = resolver.resolve(case["raw"], case["fallback"], case["language"])
            assert [
                {"location_id": v.location_id, "location_type": v.location_type} for v in values
            ] == case["expected"], "location output drift"
            assert sorted(resolver._misses) == case["lookup_misses"], "lookup miss drift"
            assert [
                {"raw_value": a, "sample_value": b} for a, b in resolver.drain_location_misses()
            ] == case["location_misses"], "location miss drift"
            checked += 1
        assert resolver._go.client.proc is not None
        resolver._go.client.close()
        assert resolver.resolve(["Zurich"])[0].location_id == 2657896
    finally:
        db.close()
        resolver._go.close()
print(f"Installed public Go location resolver matches {checked} frozen cases and restarts")
