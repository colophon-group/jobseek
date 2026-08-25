"""Bounded pre-diff migration for Swiss Medical Network identities."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.smartrecruiters import _canonical_source_url
from src.processing.board import (
    _SMN_SMARTRECRUITERS_BOARD_SLUG,
    _SMN_SMARTRECRUITERS_BOARD_URL,
    _SMN_SMARTRECRUITERS_CONFIG_FINGERPRINT,
    _SMN_SMARTRECRUITERS_IDENTITY_MIGRATION,
    _SMN_SMARTRECRUITERS_IDENTITY_MIGRATION_VERSION,
    _SMN_SMARTRECRUITERS_MIGRATION_MAX_ROWS,
    _migrate_smartrecruiters_provider_identities,
)
from src.queries.monitor import (
    _DEACTIVATE_SMARTRECRUITERS_IDENTITY_ALIASES,
    _FIND_SMARTRECRUITERS_IDENTITY_COLLISIONS,
    _LOCK_SMARTRECRUITERS_IDENTITY_MIGRATION_BOARD,
    _LOCK_SMARTRECRUITERS_IDENTITY_MIGRATION_POSTINGS,
    _UPDATE_SMARTRECRUITERS_IDENTITY_SURVIVOR,
    _WRITE_SMARTRECRUITERS_IDENTITY_MIGRATION_RECEIPT,
)

_TENANT = "SwissMedicalNetwork1"
_JOB_A = "095623dd-81c4-41fc-8c8c-e2612fa22ca0"
_JOB_B = "819700b3-a847-46f1-9b08-cf23a9591f68"
_CANONICAL_A = _canonical_source_url(_TENANT, (_JOB_A, "geo", "47.1391567,7.2443098"))
_CANONICAL_B = _canonical_source_url(_TENANT, (_JOB_B, "geo", "47.2055637,7.5302145"))
_ALIAS_A_DE = "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000144497156"
_ALIAS_A_FR = "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000144497769"
_ALIAS_B = "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000131823739"


def _metadata(**overrides) -> dict:
    metadata = {
        "identity_migration": _SMN_SMARTRECRUITERS_IDENTITY_MIGRATION,
        "_monitor_config_fingerprint": _SMN_SMARTRECRUITERS_CONFIG_FINGERPRINT,
    }
    metadata.update(overrides)
    return metadata


def _jobs() -> dict[str, DiscoveredJob]:
    return {
        _CANONICAL_A: DiscoveredJob(
            url=_CANONICAL_A,
            source_aliases=[_ALIAS_A_DE, _ALIAS_A_FR],
        ),
        _CANONICAL_B: DiscoveredJob(
            url=_CANONICAL_B,
            source_aliases=[_ALIAS_B],
        ),
    }


def _row(row_id: str, source_url: str, *, active: bool = True) -> dict:
    return {
        "id": row_id,
        "source_url": source_url,
        "is_active": active,
        "first_seen_at": "2026-08-20T00:00:00+00:00",
    }


def _receipt(**overrides) -> dict:
    receipt = {
        "id": _SMN_SMARTRECRUITERS_IDENTITY_MIGRATION,
        "version": _SMN_SMARTRECRUITERS_IDENTITY_MIGRATION_VERSION,
        "config_fingerprint": _SMN_SMARTRECRUITERS_CONFIG_FINGERPRINT,
        "completed_at": "2026-08-25T12:00:00+00:00",
        "canonical_count": 2,
        "preserved_count": 2,
        "deactivated_count": 1,
    }
    receipt.update(overrides)
    return receipt


async def _run(conn: AsyncMock, **overrides) -> tuple[int, MagicMock]:
    kwargs = {
        "board_id": "board-id",
        "company_id": "company-id",
        "board_slug": _SMN_SMARTRECRUITERS_BOARD_SLUG,
        "board_url": _SMN_SMARTRECRUITERS_BOARD_URL,
        "crawler_type": "smartrecruiters",
        "monitor_start_ts": "2026-08-25T12:00:00+00:00",
        "metadata": _metadata(),
        "jobs_by_url": _jobs(),
        "truncated": False,
        "extraction_filtered": 0,
        "security_filtered": 0,
        "processing_filtered": 0,
        "board_log": MagicMock(),
    }
    kwargs.update(overrides)
    result = await _migrate_smartrecruiters_provider_identities(conn, **kwargs)
    return result, kwargs["board_log"]


def test_sql_contract_binds_every_write_to_exact_company_and_board():
    board_sql = " ".join(_LOCK_SMARTRECRUITERS_IDENTITY_MIGRATION_BOARD.split()).lower()
    postings_sql = " ".join(_LOCK_SMARTRECRUITERS_IDENTITY_MIGRATION_POSTINGS.split()).lower()
    collision_sql = " ".join(_FIND_SMARTRECRUITERS_IDENTITY_COLLISIONS.split()).lower()
    survivor_sql = " ".join(_UPDATE_SMARTRECRUITERS_IDENTITY_SURVIVOR.split()).lower()
    aliases_sql = " ".join(_DEACTIVATE_SMARTRECRUITERS_IDENTITY_ALIASES.split()).lower()
    receipt_sql = " ".join(_WRITE_SMARTRECRUITERS_IDENTITY_MIGRATION_RECEIPT.split()).lower()

    assert "company.slug = 'swiss-medical-network'" in board_sql
    assert "jb.board_slug = 'swiss-medical-network-smartrecruiters'" in board_sql
    assert "jb.company_id = $2" in board_sql
    assert "board_id = $1" in postings_sql and "company_id = $2" in postings_sql
    assert "limit $4" in postings_sql and "for update" in postings_sql
    assert "company_id <> $2 or board_id <> $3" in collision_sql
    assert "board_id = $4" in survivor_sql and "company_id = $5" in survivor_sql
    assert "board_id = $2" in aliases_sql and "company_id = $3" in aliases_sql
    assert "source_url = any($4::text[])" in aliases_sql
    assert "company.slug = 'swiss-medical-network'" in receipt_sql
    assert "board.metadata -> '_identity_migration_receipt' is null" in receipt_sql


async def test_migration_preserves_one_existing_id_per_canonical_and_retires_alias():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"existing_receipt": None},
        None,
    ]
    conn.fetch.return_value = [
        _row("00000000-0000-0000-0000-000000000001", _ALIAS_A_DE),
        _row("00000000-0000-0000-0000-000000000002", _ALIAS_A_FR),
        _row("00000000-0000-0000-0000-000000000003", _ALIAS_B),
    ]
    conn.execute.side_effect = ["UPDATE 1", "UPDATE 1", "UPDATE 1", "UPDATE 1"]

    deactivated, log = await _run(conn)

    assert deactivated == 1
    survivor_calls = [
        call
        for call in conn.execute.await_args_list
        if call.args[0] == _UPDATE_SMARTRECRUITERS_IDENTITY_SURVIVOR
    ]
    assert [(call.args[1], call.args[2]) for call in survivor_calls] == [
        ("00000000-0000-0000-0000-000000000001", _CANONICAL_A),
        ("00000000-0000-0000-0000-000000000003", _CANONICAL_B),
    ]
    deactivate_call = next(
        call
        for call in conn.execute.await_args_list
        if call.args[0] == _DEACTIVATE_SMARTRECRUITERS_IDENTITY_ALIASES
    )
    assert deactivate_call.args[1] == ["00000000-0000-0000-0000-000000000002"]
    assert set(deactivate_call.args[4]) == {
        _CANONICAL_A,
        _CANONICAL_B,
        _ALIAS_A_DE,
        _ALIAS_A_FR,
        _ALIAS_B,
    }
    receipt_call = next(
        call
        for call in conn.execute.await_args_list
        if call.args[0] == _WRITE_SMARTRECRUITERS_IDENTITY_MIGRATION_RECEIPT
    )
    receipt = json.loads(receipt_call.args[3])
    assert receipt | {"completed_at": "ignored"} == _receipt(completed_at="ignored")
    log.info.assert_called_once_with(
        "batch.monitor.smartrecruiters_identity_migration_staged",
        canonical=2,
        preserved=2,
        deactivated=1,
    )


async def test_existing_canonical_row_is_preferred_over_legacy_alias():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{"existing_receipt": None}, None]
    conn.fetch.return_value = [
        _row("00000000-0000-0000-0000-000000000010", _CANONICAL_A),
        _row("00000000-0000-0000-0000-000000000011", _ALIAS_A_DE),
    ]
    conn.execute.side_effect = ["UPDATE 1", "UPDATE 1", "UPDATE 1"]

    one_job = {_CANONICAL_A: _jobs()[_CANONICAL_A]}
    deactivated, _ = await _run(conn, jobs_by_url=one_job)

    assert deactivated == 1
    survivor = next(
        call
        for call in conn.execute.await_args_list
        if call.args[0] == _UPDATE_SMARTRECRUITERS_IDENTITY_SURVIVOR
    )
    assert survivor.args[1] == "00000000-0000-0000-0000-000000000010"


async def test_unclaimed_active_legacy_row_is_not_immediately_retired():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{"existing_receipt": None}, None]
    stale_unclaimed = "https://jobs.smartrecruiters.com/SwissMedicalNetwork1/744000100000099"
    conn.fetch.return_value = [
        _row("00000000-0000-0000-0000-000000000001", _ALIAS_A_DE),
        _row("00000000-0000-0000-0000-000000000002", _ALIAS_A_FR),
        _row("00000000-0000-0000-0000-000000000099", stale_unclaimed),
    ]
    conn.execute.side_effect = ["UPDATE 1", "UPDATE 1", "UPDATE 1"]

    one_job = {_CANONICAL_A: _jobs()[_CANONICAL_A]}
    deactivated, _ = await _run(conn, jobs_by_url=one_job)

    assert deactivated == 1
    deactivate_call = next(
        call
        for call in conn.execute.await_args_list
        if call.args[0] == _DEACTIVATE_SMARTRECRUITERS_IDENTITY_ALIASES
    )
    assert deactivate_call.args[1] == ["00000000-0000-0000-0000-000000000002"]
    assert stale_unclaimed not in deactivate_call.args[4]


async def test_identity_without_existing_survivor_is_left_for_canonical_diff_insert():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [{"existing_receipt": None}, None]
    conn.fetch.return_value = []
    conn.execute.return_value = "UPDATE 1"

    one_job = {_CANONICAL_A: _jobs()[_CANONICAL_A]}
    deactivated, _ = await _run(conn, jobs_by_url=one_job)

    assert deactivated == 0
    assert not any(
        call.args[0] == _UPDATE_SMARTRECRUITERS_IDENTITY_SURVIVOR
        for call in conn.execute.await_args_list
    )
    receipt_call = next(
        call
        for call in conn.execute.await_args_list
        if call.args[0] == _WRITE_SMARTRECRUITERS_IDENTITY_MIGRATION_RECEIPT
    )
    receipt = json.loads(receipt_call.args[3])
    assert receipt["preserved_count"] == 0


@pytest.mark.parametrize(
    "overrides",
    [
        {"board_slug": "copied-board"},
        {"board_url": "https://evil.example/jobs"},
        {"crawler_type": "dom"},
        {"metadata": _metadata(_monitor_config_fingerprint="wrong")},
        {"truncated": True},
        {"extraction_filtered": 1},
        {"security_filtered": 1},
        {"processing_filtered": 1},
    ],
)
async def test_copied_contract_or_partial_cycle_never_reaches_database(overrides):
    conn = AsyncMock()

    with pytest.raises(ValueError):
        await _run(conn, **overrides)

    conn.fetchrow.assert_not_awaited()


async def test_unrelated_migration_marker_is_an_inert_noop():
    conn = AsyncMock()

    result, _ = await _run(conn, metadata=_metadata(identity_migration="other"))

    assert result == 0
    conn.fetchrow.assert_not_awaited()


async def test_unknown_active_source_fails_before_any_write():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"existing_receipt": None}
    conn.fetch.return_value = [
        _row("00000000-0000-0000-0000-000000000020", "https://evil.example/job/1")
    ]

    with pytest.raises(ValueError, match="unknown active source URL"):
        await _run(conn)

    conn.execute.assert_not_awaited()


async def test_cross_company_canonical_collision_fails_before_any_write():
    conn = AsyncMock()
    conn.fetchrow.side_effect = [
        {"existing_receipt": None},
        {"id": "foreign", "company_id": "other", "board_id": "other"},
    ]
    conn.fetch.return_value = []

    with pytest.raises(ValueError, match="owned outside its board"):
        await _run(conn)

    conn.execute.assert_not_awaited()


async def test_exact_receipt_is_a_permanent_noop():
    conn = AsyncMock()

    result, _ = await _run(
        conn,
        metadata=_metadata(_identity_migration_receipt=_receipt()),
    )

    assert result == 0
    conn.fetchrow.assert_not_awaited()


async def test_exact_database_receipt_is_an_idempotent_noop_under_board_lock():
    conn = AsyncMock()
    conn.fetchrow.return_value = {"existing_receipt": _receipt()}

    result, _ = await _run(conn)

    assert result == 0
    conn.fetch.assert_not_awaited()
    conn.execute.assert_not_awaited()


async def test_mismatched_receipt_and_over_cap_alias_map_fail_closed():
    conn = AsyncMock()
    with pytest.raises(ValueError, match="receipt mismatch"):
        await _run(
            conn,
            metadata=_metadata(_identity_migration_receipt=_receipt(version=2)),
        )

    over_cap_aliases = [
        f"https://jobs.smartrecruiters.com/SwissMedicalNetwork1/{740000000000000 + index}"
        for index in range(_SMN_SMARTRECRUITERS_MIGRATION_MAX_ROWS + 1)
    ]
    with pytest.raises(ValueError, match="omitted bounded source aliases"):
        await _run(
            AsyncMock(),
            jobs_by_url={
                _CANONICAL_A: DiscoveredJob(url=_CANONICAL_A, source_aliases=over_cap_aliases)
            },
        )
