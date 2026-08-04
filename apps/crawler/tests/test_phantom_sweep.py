"""Tests for the classified, bounded phantom-posting repair (#6158)."""

from __future__ import annotations

import asyncio
from contextlib import AbstractAsyncContextManager
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from src.cli import parse_args
from src.phantom_sweep import (
    _COUNT_ELIGIBLE,
    _FETCH_TERMINAL_BOARDS,
    _LOCK_KEY,
    _SWEEP_CHUNK,
    _TRY_LOCK,
    _UNLOCK,
    PhantomSweepAlreadyRunning,
    PhantomSweepSafetyError,
    classify_terminal_boards,
    load_configured_board_urls,
    refresh_derived_surfaces,
    sweep_phantom_postings,
)


def _board(
    *,
    board_id: str = "00000000-0000-0000-0000-000000000001",
    slug: str = "retired-board",
    url: str = "https://jobs.example.test/retired",
    status: str = "disabled",
    enabled: bool = False,
    confirmations: int = 0,
    gone_at: object | None = None,
    active: int = 3,
) -> dict:
    return {
        "board_id": board_id,
        "board_slug": slug,
        "board_url": url,
        "board_status": status,
        "is_enabled": enabled,
        "gone_confirmation_count": confirmations,
        "gone_at": gone_at,
        "active_postings": active,
    }


class _Context(AbstractAsyncContextManager):
    def __init__(self, value=None):
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, *_args):
        return False


class _Pool:
    def __init__(self, conn):
        self.conn = conn

    def acquire(self):
        return _Context(self.conn)


class _Conn:
    def __init__(
        self,
        *,
        terminal_rows: list[dict],
        counts: list[int],
        batches: list[list | BaseException],
    ):
        self.terminal_rows = terminal_rows
        self.counts = iter(counts)
        self.batches = iter(batches)
        self.fetch_calls: list[tuple] = []
        self.fetchval_calls: list[tuple] = []
        self.lock_available = True
        self.transaction_count = 0

    async def fetch(self, sql, *args):
        self.fetch_calls.append((sql, *args))
        if sql == _FETCH_TERMINAL_BOARDS:
            return self.terminal_rows
        if sql == _SWEEP_CHUNK:
            result = next(self.batches)
            if isinstance(result, BaseException):
                raise result
            return result
        raise AssertionError(sql)

    async def fetchval(self, sql, *args):
        self.fetchval_calls.append((sql, *args))
        if sql == _COUNT_ELIGIBLE:
            return next(self.counts)
        if sql == _TRY_LOCK:
            return self.lock_available
        if sql == _UNLOCK:
            return True
        raise AssertionError(sql)

    def transaction(self):
        self.transaction_count += 1
        return _Context()


def test_csv_loader_requires_and_reads_exact_urls(tmp_path: Path) -> None:
    csv_path = tmp_path / "boards.csv"
    csv_path.write_text(
        "company_slug,board_slug,board_url\nacme,one,https://jobs.test/acme\n",
        encoding="utf-8",
    )
    assert load_configured_board_urls(csv_path) == frozenset({"https://jobs.test/acme"})

    csv_path.write_text("company_slug,board_slug\nacme,one\n", encoding="utf-8")
    with pytest.raises(PhantomSweepSafetyError, match="missing board_url"):
        load_configured_board_urls(csv_path)


def test_cli_exposes_bounded_dry_run(monkeypatch) -> None:
    monkeypatch.setattr(
        "sys.argv",
        ["crawler", "sweep-phantoms", "--dry-run", "--chunk-size", "25", "--max-chunks", "3"],
    )
    args = parse_args()
    assert args.command == "sweep-phantoms"
    assert args.dry_run is True
    assert args.chunk_size == 25
    assert args.max_chunks == 3


def test_classification_fails_closed_for_configured_disabled_board() -> None:
    configured = _board(url="https://jobs.test/live", active=99)
    removed = _board(board_id="00000000-0000-0000-0000-000000000002", active=5)
    confirmed = _board(
        board_id="00000000-0000-0000-0000-000000000003",
        status="gone",
        enabled=True,
        confirmations=2,
        gone_at=object(),
        active=2,
    )
    pending = _board(
        board_id="00000000-0000-0000-0000-000000000004",
        status="gone",
        enabled=True,
        confirmations=1,
        gone_at=object(),
        active=7,
    )

    eligible, blocked = classify_terminal_boards(
        [configured, removed, confirmed, pending],
        frozenset({"https://jobs.test/live"}),
    )

    assert [row.reason for row in eligible] == [
        "removed_from_configuration",
        "provider_gone_confirmed",
    ]
    assert [row.reason for row in blocked] == ["configured_disabled_requires_recovery"]


@pytest.mark.asyncio
async def test_dry_run_reports_without_lock_or_mutation() -> None:
    conn = _Conn(terminal_rows=[_board(active=8)], counts=[8], batches=[])
    summary = await sweep_phantom_postings(
        _Pool(conn),
        dry_run=True,
        configured_urls=frozenset({"https://unrelated.test"}),
    )

    assert summary.candidate_postings == 8
    assert summary.updated_postings == 0
    assert summary.remaining_postings == 8
    assert all(call[0] not in {_TRY_LOCK, _SWEEP_CHUNK} for call in conn.fetchval_calls)
    assert conn.transaction_count == 0


@pytest.mark.asyncio
async def test_configured_disabled_rows_abort_before_count_or_lock() -> None:
    conn = _Conn(
        terminal_rows=[_board(url="https://jobs.test/configured", active=2)],
        counts=[],
        batches=[],
    )

    with pytest.raises(PhantomSweepSafetyError, match="recover/classify"):
        await sweep_phantom_postings(
            _Pool(conn),
            configured_urls=frozenset({"https://jobs.test/configured"}),
        )
    assert conn.fetchval_calls == []


@pytest.mark.asyncio
async def test_chunked_sweep_commits_and_reports_resumable_remainder() -> None:
    conn = _Conn(
        terminal_rows=[_board(active=5)],
        counts=[5, 1],
        batches=[
            [{"posting_id": "1"}, {"posting_id": "2"}],
            [{"posting_id": "3"}, {"posting_id": "4"}],
        ],
    )

    summary = await sweep_phantom_postings(
        _Pool(conn),
        chunk_size=2,
        max_chunks=2,
        configured_urls=frozenset({"https://unrelated.test"}),
    )

    assert summary.candidate_postings == 5
    assert summary.updated_postings == 4
    assert summary.remaining_postings == 1
    assert summary.chunks_committed == 2
    assert summary.complete is False
    assert conn.transaction_count == 2
    assert (_UNLOCK, _LOCK_KEY) in conn.fetchval_calls


@pytest.mark.asyncio
async def test_lock_contention_changes_nothing() -> None:
    conn = _Conn(terminal_rows=[_board(active=1)], counts=[1], batches=[])
    conn.lock_available = False
    with pytest.raises(PhantomSweepAlreadyRunning):
        await sweep_phantom_postings(
            _Pool(conn),
            configured_urls=frozenset({"https://unrelated.test"}),
        )
    assert conn.transaction_count == 0


@pytest.mark.asyncio
async def test_interruption_releases_session_lock_after_prior_chunk() -> None:
    conn = _Conn(
        terminal_rows=[_board(active=3)],
        counts=[3],
        batches=[[{"posting_id": "1"}], asyncio.CancelledError()],
    )

    with pytest.raises(asyncio.CancelledError):
        await sweep_phantom_postings(
            _Pool(conn),
            chunk_size=1,
            max_chunks=3,
            configured_urls=frozenset({"https://unrelated.test"}),
        )

    assert conn.transaction_count == 2
    assert (_UNLOCK, _LOCK_KEY) in conn.fetchval_calls


def test_sql_rechecks_terminal_state_and_fences_cdc() -> None:
    assert "jb.board_status = 'disabled' AND jb.is_enabled = false" in _SWEEP_CHUNK
    assert "jb.gone_confirmation_count >= 2" in _SWEEP_CHUNK
    assert "FOR UPDATE OF jp SKIP LOCKED" in _SWEEP_CHUNK
    assert "updated_at = clock_timestamp()" in _SWEEP_CHUNK
    assert "ORDER BY jp.id" in _SWEEP_CHUNK


@pytest.mark.asyncio
async def test_refresh_invalidates_cache_and_recomputes_typesense_counts(monkeypatch) -> None:
    redis = AsyncMock()
    client = object()
    refresh = AsyncMock()
    monkeypatch.setattr("src.redis_queue.get_redis", lambda: redis)
    monkeypatch.setattr("src.typesense_client.get_typesense_client", lambda: client)
    monkeypatch.setattr("src.sync.refresh_typesense_counts", refresh)

    conn = object()
    await refresh_derived_surfaces(_Pool(conn))

    redis.delete.assert_awaited_once_with("cache:platform-stats")
    refresh.assert_awaited_once_with(conn, client)
