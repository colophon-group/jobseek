"""Stable Teamtailor identity and bounded ECOM alias-retirement contracts."""

from __future__ import annotations

import csv
import importlib
import json
import re
import sys
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.cli import parse_args
from src.core.monitor import MonitorResult, _apply_url_allowlist, _apply_url_transform
from src.ecom_teamtailor_cutover import (
    _migration_sql,
    apply_ecom_teamtailor_cutover,
    ecom_teamtailor_cutover_state,
    rollback_ecom_teamtailor_cutover,
)
from src.processing.board import (
    _ECOM_CANONICAL_URL_PATTERN,
    _ECOM_IDENTITY_MIGRATION,
    _ECOM_IDENTITY_MIGRATION_CONTRACT,
    _ECOM_IDENTITY_MIGRATION_MAX_ROWS,
    _ECOM_IDENTITY_MIGRATION_VERSION,
    _ECOM_LEGACY_URL_PATTERN,
    _retire_canonicalized_provider_identities,
)
from src.queries.monitor import _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES
from src.sync import _monitor_config_fingerprint

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"
_BOARD_SLUG, _BOARD_URL, _CRAWLER_TYPE, _FINGERPRINT = _ECOM_IDENTITY_MIGRATION_CONTRACT


def _board() -> tuple[dict[str, str], dict]:
    with _BOARDS.open(newline="") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == _BOARD_SLUG)
    return row, json.loads(row["monitor_config"])


def _metadata(**overrides) -> dict:
    metadata = {
        "identity_migration": _ECOM_IDENTITY_MIGRATION,
        "_monitor_config_fingerprint": _FINGERPRINT,
        "recent_discovered_counts": [44, 44, 44],
    }
    metadata.update(overrides)
    return metadata


def _canonical_urls(count: int = 44) -> set[str]:
    ids = [7_769_137, *(8_000_000 + index for index in range(count - 1))]
    return {f"https://ecomtradinggroup.teamtailor.com/jobs/{job_id}" for job_id in ids}


def _row(**overrides) -> dict:
    row = {
        "active": 89,
        "legacy": 45,
        "canonical": 44,
        "unknown": 0,
        "discovered": 44,
        "validated": 44,
        "retired": 45,
        "receipt_written": True,
        "existing_receipt": None,
    }
    row.update(overrides)
    return row


async def _run(conn: AsyncMock, **overrides) -> tuple[int, MagicMock]:
    kwargs = {
        "board_id": "ecom-board-id",
        "company_id": "ecom-company-id",
        "board_slug": _BOARD_SLUG,
        "board_url": _BOARD_URL,
        "crawler_type": _CRAWLER_TYPE,
        "monitor_start_ts": "2026-08-26T12:00:00+00:00",
        "metadata": _metadata(),
        "discovered": 44,
        "canonical_urls": _canonical_urls(),
        "truncated": False,
        "extraction_filtered": 0,
        "security_filtered": 0,
        "processing_filtered": 0,
        "all_canonical": True,
        "board_log": MagicMock(),
    }
    kwargs.update(overrides)
    return await _retire_canonicalized_provider_identities(conn, **kwargs), kwargs["board_log"]


def test_ecom_board_uses_exact_stable_provider_identity_and_migration_contract() -> None:
    row, metadata = _board()

    assert row["company_slug"] == "ecom-agroindustrial"
    assert (row["board_url"], row["monitor_type"]) == (_BOARD_URL, _CRAWLER_TYPE)
    assert metadata["identity_migration"] == _ECOM_IDENTITY_MIGRATION
    assert (
        _monitor_config_fingerprint(row["board_url"], row["monitor_type"], metadata) == _FINGERPRINT
    )

    old_title = "https://careers.ecomtrading.com/jobs/7769137-accounts-and-finance-executive"
    current_title = "https://ecomtradinggroup.teamtailor.com/jobs/7769137-assistant-finance-manager"
    expected = {"https://ecomtradinggroup.teamtailor.com/jobs/7769137"}
    for source in (old_title, current_title):
        filtered = _apply_url_allowlist(
            MonitorResult(urls={source}),
            {"url_allowlist": metadata["url_allowlist"]},
        )
        transformed = _apply_url_transform(filtered, metadata)
        assert transformed.urls == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://careers.ecomtrading.com/jobs/7769137-accounts-and-finance-executive",
        "https://ecomtradinggroup.teamtailor.com/jobs/7769137-assistant-finance-manager",
        "https://careers.ecomtrading.com/jobs/7769137",
    ],
)
def test_legacy_contract_covers_only_ecom_title_alias_namespace(url: str) -> None:
    assert re.fullmatch(_ECOM_LEGACY_URL_PATTERN, url)


def test_ecom_canonical_contract_is_title_free_numeric_provider_identity() -> None:
    assert re.fullmatch(
        _ECOM_CANONICAL_URL_PATTERN,
        "https://ecomtradinggroup.teamtailor.com/jobs/7769137",
    )
    for invalid in (
        "https://ecomtradinggroup.teamtailor.com/jobs/7769137-title",
        "https://careers.ecomtrading.com/jobs/7769137",
        "https://evil.example/jobs/7769137",
        "https://ecomtradinggroup.teamtailor.com/jobs/not-numeric",
        "https://ecomtradinggroup.teamtailor.com/jobs/7769137?source=bad",
    ):
        assert re.fullmatch(_ECOM_CANONICAL_URL_PATTERN, invalid) is None


async def test_healthy_cycle_retires_known_aliases_after_canonical_collision_resolves() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = _row()

    retired, board_log = await _run(conn)

    assert retired == 45
    args = conn.fetchrow.await_args.args
    assert args[:5] == (
        _RETIRE_CANONICALIZED_PROVIDER_IDENTITIES,
        "ecom-board-id",
        "ecom-company-id",
        "2026-08-26T12:00:00+00:00",
        _ECOM_IDENTITY_MIGRATION_MAX_ROWS,
    )
    assert set(args[5]) == _canonical_urls()
    assert args[6:8] == (
        _ECOM_LEGACY_URL_PATTERN,
        _ECOM_CANONICAL_URL_PATTERN,
    )
    assert json.loads(args[8]) == {
        "id": _ECOM_IDENTITY_MIGRATION,
        "version": _ECOM_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
    }
    assert args[9] == "ecom-agroindustrial"
    board_log.info.assert_called_once_with(
        "batch.monitor.identity_migration_completed",
        migration=_ECOM_IDENTITY_MIGRATION,
        retired=45,
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"board_slug": "another-board"},
        {"board_url": "https://evil.example/jobs"},
        {"crawler_type": "dom"},
        {"metadata": _metadata(_monitor_config_fingerprint="wrong")},
        {"metadata": _metadata(identity_migration="ecom-teamtailor-stable-id-v2")},
    ],
)
async def test_copied_ecom_marker_or_wrong_contract_never_enters_sql(overrides) -> None:
    conn = AsyncMock()

    retired, _ = await _run(conn, **overrides)

    assert retired == 0
    conn.fetchrow.assert_not_awaited()


async def test_ecom_receipt_is_bounded_and_makes_replay_a_permanent_noop() -> None:
    conn = AsyncMock()
    receipt = {
        "id": _ECOM_IDENTITY_MIGRATION,
        "version": _ECOM_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _FINGERPRINT,
        "completed_at": "2026-08-26T12:00:00+00:00",
        "retired_count": 45,
        "rollback_rows": [],
    }
    metadata = _metadata(_identity_migration_receipt=receipt)

    first, _ = await _run(conn, metadata=metadata)
    second, _ = await _run(conn, metadata=metadata)

    assert (first, second) == (0, 0)
    conn.fetchrow.assert_not_awaited()


async def test_ecom_unknown_active_source_or_over_cap_alias_set_fails_closed() -> None:
    conn = AsyncMock()
    conn.fetchrow.return_value = _row(
        active=90,
        unknown=1,
        retired=0,
        receipt_written=False,
    )

    retired, board_log = await _run(conn)

    assert retired == 0
    assert board_log.warning.call_args.args[0] == "batch.monitor.identity_migration_blocked"
    assert board_log.warning.call_args.kwargs["cap"] == _ECOM_IDENTITY_MIGRATION_MAX_ROWS


def test_revision_0022_is_exact_bounded_receipt_backed_and_reversible() -> None:
    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )

    assert (migration.revision, migration.down_revision) == ("0022", "0021")
    assert migration._MIGRATION_ID == _ECOM_IDENTITY_MIGRATION
    assert migration._CONFIG_FINGERPRINT == _FINGERPRINT
    assert migration._LEGACY_PATTERN == _ECOM_LEGACY_URL_PATTERN
    assert migration._CANONICAL_PATTERN == _ECOM_CANONICAL_URL_PATTERN
    assert migration._MAX_ROWS == _ECOM_IDENTITY_MIGRATION_MAX_ROWS

    forward = migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES
    rollback = migration._ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES
    for sql in (forward, rollback):
        assert "company.slug = 'ecom-agroindustrial'" in sql
        assert "board.board_slug = 'ecom-agroindustrial-global'" in sql
        assert "_identity_migration_receipt" in sql
        assert "rollback_rows" in sql
    assert "unknown active source identities" in forward
    assert "foreign canonical URL ownership" in forward
    assert "canonical/legacy row collisions" in forward
    assert "candidate_count > 100" in forward
    assert "row_number() OVER" in forward
    assert "SET source_url = 'https://ecomtradinggroup.teamtailor.com/jobs/'" in forward
    assert "metadata - '_identity_migration_receipt'" in rollback
    assert "occupied legacy identities" in rollback

    execute = MagicMock()
    original_op = migration.op
    migration.op = MagicMock(execute=execute)
    try:
        migration.upgrade()
        migration.downgrade()
    finally:
        migration.op = original_op
    assert execute.call_args_list[0].args == (forward,)
    assert execute.call_args_list[1].args == (rollback,)


def test_revision_0022_sql_has_no_accidental_sqlalchemy_bind_parameters() -> None:
    from sqlalchemy import text

    migration = importlib.import_module(
        "src.migrations.versions.0022_migrate_ecom_teamtailor_identities"
    )

    assert not text(migration._MIGRATE_ECOM_TEAMTAILOR_IDENTITIES)._bindparams
    assert not text(migration._ROLLBACK_ECOM_TEAMTAILOR_IDENTITIES)._bindparams


@pytest.mark.parametrize(
    "command",
    [
        "repair-ecom-teamtailor-cutover",
        "rollback-ecom-teamtailor-cutover",
        "ecom-teamtailor-cutover-state",
    ],
)
def test_ecom_cutover_commands_have_no_unbounded_arguments(monkeypatch, command) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", command])
    assert vars(parse_args()) == {"command": command}


async def test_ecom_cutover_hooks_reuse_exact_revision_sql() -> None:
    connection = AsyncMock()
    connection.execute.return_value = "DO"

    assert await apply_ecom_teamtailor_cutover(connection) == "DO"
    assert await rollback_ecom_teamtailor_cutover(connection) == "DO"

    assert connection.execute.await_args_list[0].args == (_migration_sql(),)
    assert connection.execute.await_args_list[1].args == (_migration_sql(rollback=True),)


@pytest.mark.parametrize(
    ("rows", "expected"),
    [
        ([], "absent"),
        ([{"receipt": None}], "pending"),
        (
            [
                {
                    "receipt": {
                        "id": _ECOM_IDENTITY_MIGRATION,
                        "version": _ECOM_IDENTITY_MIGRATION_VERSION,
                        "config_fingerprint": _FINGERPRINT,
                        "rollback_rows": [],
                    }
                }
            ],
            "complete",
        ),
    ],
)
async def test_ecom_cutover_state_assigns_rollback_to_only_pending_deploy(rows, expected) -> None:
    connection = AsyncMock()
    connection.fetch.return_value = rows

    assert await ecom_teamtailor_cutover_state(connection) == expected


async def test_ecom_cutover_state_rejects_ambiguous_or_mismatched_receipts() -> None:
    connection = AsyncMock()
    connection.fetch.return_value = [{"receipt": None}, {"receipt": None}]
    with pytest.raises(RuntimeError, match="ambiguous"):
        await ecom_teamtailor_cutover_state(connection)

    connection.fetch.return_value = [{"receipt": {"id": "other"}}]
    with pytest.raises(RuntimeError, match="mismatched"):
        await ecom_teamtailor_cutover_state(connection)
