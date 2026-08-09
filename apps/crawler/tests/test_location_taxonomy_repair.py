from __future__ import annotations

import argparse
import importlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import cli
from src.location_taxonomy_repair import (
    LocationRow,
    LocationTaxonomyRepairError,
    LocationTaxonomyRepairSummary,
    _validate_exact_equality,
    _validate_local_before,
    _validate_source,
)


def _row(
    location_id: int,
    slug: str | None,
    lat: float | None = None,
    lng: float | None = None,
) -> LocationRow:
    return LocationRow(location_id, slug, lat, lng)


def test_source_requires_exact_cardinality_unique_nonblank_slugs_and_paired_coordinates() -> None:
    valid = (_row(1, "alpha", 1.0, 2.0), _row(2, "beta"))
    _validate_source(valid, expected_rows=2)

    with pytest.raises(LocationTaxonomyRepairError, match="row count is 2, expected 3"):
        _validate_source(valid, expected_rows=3)
    with pytest.raises(LocationTaxonomyRepairError, match="IDs are not unique"):
        _validate_source((valid[0], valid[0]), expected_rows=2)
    with pytest.raises(LocationTaxonomyRepairError, match="blank slugs"):
        _validate_source((_row(1, "alpha"), _row(2, "  ")), expected_rows=2)
    with pytest.raises(LocationTaxonomyRepairError, match="duplicate slugs"):
        _validate_source((_row(1, "alpha"), _row(2, "alpha")), expected_rows=2)
    with pytest.raises(LocationTaxonomyRepairError, match="partial coordinate pairs"):
        _validate_source((_row(1, "alpha", 1.0, None),), expected_rows=1)


def test_local_preflight_requires_exact_ids_and_rejects_only_populated_conflicts() -> None:
    source = (_row(1, "alpha", 1.0, 2.0), _row(2, "beta"))
    missing_or_matching = (_row(1, " ", 1.0, None), _row(2, "beta"))
    _validate_local_before(source, missing_or_matching)

    with pytest.raises(LocationTaxonomyRepairError, match="missing_local=1, extra_local=1"):
        _validate_local_before(source, (_row(1, None), _row(3, None)))
    with pytest.raises(
        LocationTaxonomyRepairError,
        match=r"slug=1, lat=1, lng=1",
    ):
        _validate_local_before(
            source,
            (_row(1, "wrong", 9.0, 8.0), _row(2, "beta")),
        )


def test_postflight_is_exact_and_reports_only_a_bounded_mismatch_count() -> None:
    source = (_row(1, "alpha", 1.0, 2.0), _row(2, "beta"))
    _validate_exact_equality(source, source)

    with pytest.raises(LocationTaxonomyRepairError, match="failed for 1 rows") as error:
        _validate_exact_equality(source, (_row(1, "alpha", 1.0, 2.0), _row(2, "other")))
    assert "alpha" not in str(error.value)
    assert "other" not in str(error.value)


def test_migration_adds_a_not_valid_guard_without_requiring_a_fresh_taxonomy() -> None:
    migration = importlib.import_module("src.migrations.versions.0017_guard_location_slug")
    sql = migration._ADD_NONBLANK_LOCATION_SLUG_GUARD

    assert "to_regclass('public.location') IS NOT NULL" in sql
    assert "CHECK (slug IS NOT NULL AND btrim(slug) <> '') NOT VALID" in sql
    assert "VALIDATE CONSTRAINT" not in sql
    assert migration.revision == "0017"
    assert migration.down_revision == "0016"

    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op = original_op
    assert execute.call_count == 2
    downgrade_sql = execute.call_args_list[1].args[0]
    assert "DROP CONSTRAINT IF EXISTS chk_location_slug_nonblank" in downgrade_sql


def test_cli_exposes_only_the_fixed_cardinality_repair_command(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "repair-location-taxonomy-source"])
    args = cli.parse_args()
    assert args.command == "repair-location-taxonomy-source"
    assert vars(args) == {"command": "repair-location-taxonomy-source"}


async def test_cli_uses_only_local_and_provider_neutral_web_pools(monkeypatch, capsys) -> None:
    source_pool = object()
    local_pool = object()
    summary = LocationTaxonomyRepairSummary(
        expected_rows=37_526,
        source_rows=37_526,
        local_rows=37_526,
        source_coordinate_pairs=36_400,
        missing_slugs_before=37_526,
        missing_coordinate_values_before=2_252,
        updated_rows=37_526,
        source_local_equal=True,
        constraint_validated=True,
    )
    repair = AsyncMock(return_value=summary)
    mirror_pool = AsyncMock(side_effect=AssertionError("legacy mirror must not open"))
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(command="repair-location-taxonomy-source"),
    )
    monkeypatch.setattr(cli, "create_local_pool", AsyncMock(return_value=local_pool))
    monkeypatch.setattr(cli, "create_web_pool", AsyncMock(return_value=source_pool))
    monkeypatch.setattr("src.db.create_pool", mirror_pool)
    monkeypatch.setattr(cli, "close_all_pools", AsyncMock())

    with patch("src.location_taxonomy_repair.repair_location_taxonomy_source", new=repair):
        await cli.run()

    repair.assert_awaited_once_with(source_pool, local_pool)
    mirror_pool.assert_not_awaited()
    assert '"source_local_equal": true' in capsys.readouterr().out
