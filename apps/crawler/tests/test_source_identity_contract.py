"""Static and pure contract tests for durable posting identity."""

from __future__ import annotations

import importlib

from src.exporter import PostingSchema
from src.queries.monitor import (
    _DIFF_BATCH_DURABLE,
    _INSERT_RICH_JOB_DURABLE,
    _INSERT_RICH_JOB_ENRICH_DURABLE,
    _INSERT_URL_ONLY_JOBS_DURABLE,
    _VALIDATE_DURABLE_DISCOVERIES,
)


def test_durable_sql_matches_identity_but_updates_outbound_url() -> None:
    compact = " ".join(_DIFF_BATCH_DURABLE.split())
    assert "d.source_identity = posting.source_identity" in compact
    assert "SET source_url = d.source_url" in compact
    assert "INSERT INTO job_posting_source_alias" in compact
    assert "locked.source_url <> d.source_url" in compact
    assert "RETURNING job_posting.source_identity" in compact
    assert "$4::uuid AS discovering_company_id" in compact


def test_collision_preflight_is_owner_and_alias_aware() -> None:
    compact = " ".join(_VALIDATE_DURABLE_DISCOVERIES.split())
    assert "cross_owner_identity" in compact
    assert "outbound_url_owned_by_other_identity" in compact
    assert "outbound_alias_owned_by_other_identity" in compact
    assert "posting.company_id <> $4::uuid" in compact


def test_every_explicit_insert_conflicts_on_identity_not_navigation_url() -> None:
    for statement in (
        _INSERT_RICH_JOB_DURABLE,
        _INSERT_RICH_JOB_ENRICH_DURABLE,
        _INSERT_URL_ONLY_JOBS_DURABLE,
    ):
        assert "source_identity" in statement
        assert "ON CONFLICT (source_identity) DO NOTHING" in statement
        assert "RETURNING id, company_id, source_identity, source_url" in statement


def test_outbound_url_is_mutable_in_downstream_cdc() -> None:
    assert "source_url" in PostingSchema.column_names()
    assert "source_url" in PostingSchema.upsert_columns
    assert "source_identity" not in PostingSchema.column_names()


def test_migration_is_bounded_receipt_backed_and_rollback_guarded() -> None:
    migration = importlib.import_module("src.migrations.versions.0023_add_durable_source_identity")
    upgrade = migration._CREATE_IDENTITY_CONTRACT
    downgrade = migration._DOWNGRADE_GUARD

    assert migration.revision == "0023"
    assert migration.down_revision == "0022"
    assert migration._MAX_BACKFILL_ROWS == 5_000_000
    assert migration._BACKFILL_BATCH_ROWS == 50_000
    assert "posting_identity_migration_receipt" in upgrade
    assert "expected_rows = backfilled_rows" in upgrade
    assert "LIMIT 50000" in upgrade
    assert "source_identity = posting.source_url" in upgrade
    assert "ALTER COLUMN source_identity SET NOT NULL" in upgrade
    assert "BEFORE INSERT OR UPDATE OF source_url" in upgrade
    assert "OLD.source_identity IS NOT DISTINCT FROM OLD.source_url" in upgrade
    assert "source_identity IS DISTINCT FROM source_url" in downgrade
    assert "EXISTS (SELECT 1 FROM job_posting_source_alias)" in downgrade
    assert "rollback refused" in downgrade


def test_mutable_outbound_url_participates_in_commit_safe_cdc_trigger() -> None:
    migration = importlib.import_module("src.migrations.versions.0023_add_durable_source_identity")
    cdc = " ".join(migration._INSTALL_MUTABLE_URL_CDC.split())
    assert "NEW.source_url" in cdc
    assert "OLD.source_url" in cdc
    assert "BEFORE UPDATE OF source_url" in cdc
