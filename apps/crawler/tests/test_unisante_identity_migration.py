"""Safety contracts for Unisanté's provider-reference identity migration."""

from __future__ import annotations

import csv
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.processing.board import (
    _UNISANTE_CANONICAL_URL_PATTERN,
    _UNISANTE_DETAIL_URL_PATTERN,
    _UNISANTE_IDENTITY_MIGRATION,
    _UNISANTE_IDENTITY_MIGRATION_MAX_ROWS,
    _UNISANTE_IDENTITY_MIGRATION_VERSION,
    _migrate_unisante_provider_identities,
)
from src.queries.monitor import _MIGRATE_UNISANTE_PROVIDER_IDENTITIES

_DATA = Path(__file__).parents[1] / "data"


def _job(reference: str) -> tuple[str, SimpleNamespace]:
    canonical = f"https://emploi.unisante.ch/index.php/offres?reference={reference}"
    detail = f"https://emploi.unisante.ch/index.php/offre/{reference}-role"
    return canonical, SimpleNamespace(
        metadata={"provider_reference": reference, "detail_url": detail}
    )


def _jobs(*references: str) -> dict[str, SimpleNamespace]:
    return dict(_job(reference) for reference in references)


def _row(**overrides) -> dict:
    row = {
        "existing_receipt": None,
        "active": 8,
        "legacy": 8,
        "canonical": 0,
        "unknown": 0,
        "discovered": 10,
        "valid": 10,
        "conflicts": 0,
        "candidates": 7,
        "existing_canonicals": 0,
        "updated": 7,
        "retired": 1,
        "may_migrate": True,
        "receipt_written": True,
    }
    row.update(overrides)
    return row


def _receipt(**overrides) -> dict:
    receipt = {
        "id": _UNISANTE_IDENTITY_MIGRATION,
        "version": _UNISANTE_IDENTITY_MIGRATION_VERSION,
        "completed_at": "2026-08-25T18:00:00+00:00",
        "updated_count": 7,
        "retired_count": 0,
    }
    receipt.update(overrides)
    return receipt


async def _run(conn: AsyncMock, **overrides) -> tuple[int, MagicMock]:
    kwargs = {
        "board_id": "board-id",
        "company_id": "company-id",
        "board_slug": "unisante-emploi",
        "board_url": "https://emploi.unisante.ch/index.php/offres",
        "crawler_type": "unisante",
        "metadata": {"identity_migration": _UNISANTE_IDENTITY_MIGRATION},
        "jobs_by_url": _jobs(
            "8", "191", "285", "1120", "1393", "1394", "1396", "1401", "1403", "1405"
        ),
        "truncated": False,
        "extraction_filtered": 0,
        "security_filtered": 0,
        "board_log": MagicMock(),
    }
    kwargs.update(overrides)
    return await _migrate_unisante_provider_identities(conn, **kwargs), kwargs["board_log"]


def test_committed_board_enables_only_reviewed_migration_contract() -> None:
    with (_DATA / "boards.csv").open(newline="", encoding="utf-8") as handle:
        row = next(
            item for item in csv.DictReader(handle) if item["board_slug"] == "unisante-emploi"
        )
    assert row["company_slug"] == "unisante"
    assert row["board_url"] == "https://emploi.unisante.ch/index.php/offres"
    assert row["monitor_type"] == "unisante"
    assert json.loads(row["monitor_config"]) == {"identity_migration": _UNISANTE_IDENTITY_MIGRATION}
    assert row["scraper_type"] == "skip"


def test_identity_url_contracts_are_exact_and_reference_keyed() -> None:
    import re

    canonical = "https://emploi.unisante.ch/index.php/offres?reference=1405"
    assert re.fullmatch(_UNISANTE_CANONICAL_URL_PATTERN, canonical).group(1) == "1405"
    assert re.fullmatch(
        _UNISANTE_DETAIL_URL_PATTERN,
        "https://emploi.unisante.ch/index.php/offre/1405-assistante-de-direction",
    )
    for invalid in (
        "https://evil.example/index.php/offres?reference=1405",
        "https://emploi.unisante.ch/index.php/offres?reference=0",
        "https://emploi.unisante.ch/index.php/offres?reference=1405&extra=1",
        "https://emploi.unisante.ch/offres?reference=1405",
    ):
        assert re.fullmatch(_UNISANTE_CANONICAL_URL_PATTERN, invalid) is None


def test_atomic_sql_preserves_one_alias_in_place_and_retires_conflicts() -> None:
    sql = " ".join(_MIGRATE_UNISANTE_PROVIDER_IDENTITIES.split()).lower()

    assert "from company where id = $2 and slug = 'unisante'" in sql
    assert "from unnest($3::text[], $4::text[])" in sql
    assert "for update of posting" in sql
    assert "row_number() over" in sql
    assert "set source_url = candidate.canonical_url" in sql
    assert "not exists ( select 1 from canonical_existing" in sql
    assert "set is_active = false" in sql
    assert "updated_in_place" in sql
    assert "retired_aliases" in sql
    assert "canonical_conflicts.conflict_count = 0" in sql
    assert "active_state.unknown_count = 0" in sql
    assert "'_identity_migration_receipt'" in sql
    assert "aliases_to_retire" in sql
    assert "updated_count + transition_state.retired_count" in sql
    assert "transition_state.legacy_count" in sql


async def test_absent_canonical_rewrites_legacy_rows_in_place() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = _row()

    retired, log = await _run(conn)

    assert retired == 1
    args = conn.fetchrow.await_args.args
    assert args[0] == _MIGRATE_UNISANTE_PROVIDER_IDENTITIES
    assert args[1:3] == ("board-id", "company-id")
    assert len(args[3]) == len(args[4]) == 10
    assert args[5] == _UNISANTE_IDENTITY_MIGRATION_MAX_ROWS
    assert json.loads(args[6]) == {
        "id": _UNISANTE_IDENTITY_MIGRATION,
        "version": _UNISANTE_IDENTITY_MIGRATION_VERSION,
    }
    log.info.assert_called_once_with(
        "batch.monitor.unisante_identity_migration_completed",
        updated=7,
        retired=1,
    )


async def test_existing_canonical_keeps_canonical_and_retires_aliases() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(
        canonical=7,
        existing_canonicals=7,
        updated=0,
        retired=8,
    )

    retired, _ = await _run(conn)

    assert retired == 8


async def test_valid_receipt_makes_replay_a_noop_before_sql() -> None:
    conn = AsyncMock()

    retired, _ = await _run(
        conn,
        metadata={
            "identity_migration": _UNISANTE_IDENTITY_MIGRATION,
            "_identity_migration_receipt": _receipt(),
        },
    )

    assert retired == 0
    conn.fetchrow.assert_not_awaited()


@pytest.mark.parametrize(
    "overrides",
    [
        {"board_slug": "copied-board"},
        {"board_url": "https://evil.example/offres"},
        {"crawler_type": "dom"},
        {"truncated": True},
        {"extraction_filtered": 1},
        {"security_filtered": 1},
        {"jobs_by_url": {}},
    ],
)
async def test_wrong_contract_or_partial_batch_fails_before_sql(overrides: dict) -> None:
    conn = AsyncMock()

    with pytest.raises(RuntimeError):
        await _run(conn, **overrides)

    conn.fetchrow.assert_not_awaited()


@pytest.mark.parametrize(
    "row",
    [
        _row(unknown=1, may_migrate=False, receipt_written=False),
        _row(conflicts=1, may_migrate=False, receipt_written=False),
        _row(valid=9, may_migrate=False, receipt_written=False),
        _row(updated=6, retired=1, receipt_written=False),
    ],
)
async def test_database_safety_gate_blocks_partial_or_conflicting_transition(row: dict) -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = row

    with pytest.raises(RuntimeError, match="safety gate blocked"):
        await _run(conn)
