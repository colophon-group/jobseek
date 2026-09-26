"""Compare the dark Go Typesense projection with the current Python exporter.

Run from apps/crawler: uv run python go/typesense-exporter/replay_python.py
This uses frozen rows and never connects to Postgres or Typesense.
"""

from __future__ import annotations

import json
import subprocess
import uuid
from datetime import UTC, datetime
from pathlib import Path

from src.exporter import TaxonomyMaps, _build_typesense_docs

GO_DIR = Path(__file__).parent
NOW = datetime(2025, 6, 15, 12, tzinfo=UTC)


def row(**overrides: object) -> dict:
    value: dict = {
        "id": uuid.UUID("80000000-0000-0000-0000-000000000001"),
        "company_id": uuid.UUID("00000000-0000-0000-0000-000000000002"),
        "company_name": "TestCo",
        "company_slug": "testco",
        "company_icon": "https://example.com/icon.svg",
        "titles": ["Senior Engineer"],
        "is_active": True,
        "location_ids": [10, 11],
        "location_types": ["onsite", "hybrid"],
        "occupation_id": 100,
        "seniority_id": 1,
        "technology_ids": [50],
        "employment_type": "full-time",
        "experience_min": 1.5,
        "experience_max": 2.5,
        "locales": [],
        "first_seen_at": NOW,
        "last_seen_at": NOW,
        "salary_eur": 115000,
        "salary_min": 120000,
        "salary_max": 150000,
        "salary_currency": "USD",
        "salary_period": "year",
        "source_url": "https://example.com/job",
        "description_r2_hash": 0,
    }
    value.update(overrides)
    return value


def main() -> None:
    maps = TaxonomyMaps(
        location_names={10: {"en": "Zurich"}, 11: {"de": "Winterthur"}},
        location_types={10: "city", 11: "city"},
        location_ancestors={10: [10, 20, 30], 11: [11, 20, 30]},
        occupation_names={100: "Software Engineer"},
        occupation_ancestors={100: [100, 200]},
        seniority_names={1: "Senior"},
        technology_names={50: "Python"},
    )
    go_maps = {
        "location_names": maps.location_names,
        "location_fallback_names": {
            id: next(iter(names.values())) for id, names in maps.location_names.items()
        },
        "location_types": maps.location_types,
        "location_ancestors": maps.location_ancestors,
        "occupation_names": maps.occupation_names,
        "occupation_ancestors": maps.occupation_ancestors,
        "seniority_names": maps.seniority_names,
        "technology_names": maps.technology_names,
    }
    cases = [
        row(),
        row(is_active=False, titles=["  "], description_r2_hash=None),
        row(
            location_ids=[],
            location_types=[],
            occupation_id=None,
            seniority_id=None,
            technology_ids=[],
            experience_min=None,
            experience_max=None,
            locales=["en"],
            first_seen_at=None,
            last_seen_at=None,
            salary_eur=None,
            salary_min=None,
            salary_max=None,
            salary_currency=None,
            salary_period=None,
            source_url=None,
        ),
    ]
    for index, posting in enumerate(cases):
        expected = _build_typesense_docs([posting], maps)[0]
        encoded = json.dumps(
            {"row": posting, "maps": go_maps},
            default=lambda value: value.isoformat() if isinstance(value, datetime) else str(value),
        ).encode()
        result = subprocess.run(
            ["go", "run", "."],
            input=encoded,
            cwd=GO_DIR,
            check=True,
            capture_output=True,
        )
        actual = json.loads(result.stdout)
        if actual != expected:
            keys = sorted(set(actual) | set(expected))
            differences = {
                key: (expected.get(key), actual.get(key))
                for key in keys
                if expected.get(key) != actual.get(key)
            }
            raise AssertionError(f"case {index}: {differences}")
        print(f"case {index}: {len(actual)} fields match")


if __name__ == "__main__":
    main()
