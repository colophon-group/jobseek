"""Contract checks for the bounded Jobs.cz identity repair."""

from __future__ import annotations

import importlib
from unittest.mock import MagicMock


def test_jobs_cz_tracking_migration_is_scoped_bounded_and_cdc_visible(monkeypatch) -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0030_collapse_jobs_cz_tracking_urls"
    )
    execute = MagicMock()
    monkeypatch.setattr(migration.op, "execute", execute)

    migration.upgrade()

    sql = execute.call_args.args[0]
    assert migration.revision == "0030"
    assert migration.down_revision == "0029"
    assert migration._MAX_CANDIDATES == 10_000
    assert "candidate_count > 10000" in sql
    assert "fedex-czechia-local" in sql
    assert "ferring-pharmaceuticals-careers-cz" in sql
    assert "^https://www[.]jobs[.]cz/rpd/[0-9]+/[?][^#]+$" in sql
    assert "first_value(posting.id)" in sql
    assert "cardinality(posting.titles) > 0" in sql
    assert "posting.description_r2_hash IS NOT NULL" in sql
    assert "SET source_url = mapping.canonical_url" in sql
    assert "SET is_active = false" in sql
    assert "updated_at = clock_timestamp()" in sql
    assert "left active duplicates" in sql
