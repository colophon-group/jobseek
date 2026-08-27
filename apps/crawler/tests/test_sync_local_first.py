from __future__ import annotations

import inspect
import sys
from unittest.mock import AsyncMock

import polars as pl
import pytest

import src.deadletters as deadletters
import src.sync as sync
from src.cli import parse_args


class _Transaction:
    def __init__(self, events: list[str], label: str) -> None:
        self.events = events
        self.label = label

    async def __aenter__(self):
        self.events.append(f"{self.label}_tx_enter")
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        del traceback
        self.events.append(f"{self.label}_{'rollback' if exc_type else 'commit'}")
        return False


class _Connection:
    def __init__(self, events: list[str], label: str) -> None:
        self.events = events
        self.label = label

    def transaction(self) -> _Transaction:
        return _Transaction(self.events, self.label)

    async def execute(self, sql: str, *args):
        del sql, args
        return "OK"


class _Acquire:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    async def __aenter__(self) -> _Connection:
        return self.conn

    async def __aexit__(self, exc_type, exc, traceback) -> bool:
        del exc_type, exc, traceback
        return False


class _Pool:
    def __init__(self, conn: _Connection) -> None:
        self.conn = conn

    def acquire(self) -> _Acquire:
        return _Acquire(self.conn)


def _patch_inputs(monkeypatch) -> tuple[pl.DataFrame, pl.DataFrame]:
    companies = pl.DataFrame(
        {
            "slug": ["acme"],
            "name": ["Acme"],
            "website": ["https://acme.test"],
            "logo_url": [""],
            "icon_url": [""],
            "logo_type": [""],
        }
    )
    boards = pl.DataFrame(
        {
            "company_slug": ["acme"],
            "board_slug": ["acme-jobs"],
            "board_url": ["https://acme.test/jobs"],
            "monitor_type": ["greenhouse"],
            "monitor_config": ["{}"],
            "scraper_type": [""],
            "scraper_config": [""],
        }
    )
    monkeypatch.setattr(sync, "_load_occupation_domains", lambda: pl.DataFrame())
    monkeypatch.setattr(sync, "_load_occupations", lambda: pl.DataFrame())
    monkeypatch.setattr(sync, "_load_seniority", lambda: pl.DataFrame())
    monkeypatch.setattr(sync, "_load_technologies", lambda: pl.DataFrame())
    monkeypatch.setattr(sync, "_load_industries", lambda: pl.DataFrame())
    monkeypatch.setattr(sync, "_load_company_descriptions", lambda: pl.DataFrame())
    monkeypatch.setattr(sync, "_load_companies", lambda: companies)
    monkeypatch.setattr(sync, "_load_boards", lambda: boards)
    monkeypatch.setattr(sync, "setup_logging", lambda *_args: None)
    monkeypatch.setattr(sync, "close_all_pools", AsyncMock())
    monkeypatch.setattr(sync, "close_redis", AsyncMock())
    monkeypatch.setattr(deadletters, "classify_deadletters", AsyncMock(return_value=[]))
    monkeypatch.setattr(deadletters, "lifecycle_counts", lambda _entries: {})
    return companies, boards


def _patch_local_writes(monkeypatch, events: list[str]) -> None:
    async def record(label: str, *args, **kwargs) -> None:
        del args, kwargs
        events.append(label)

    monkeypatch.setattr(
        sync,
        "sync_lookup_tables_local",
        lambda *args, **kwargs: record("local_lookups", *args, **kwargs),
    )
    monkeypatch.setattr(
        sync,
        "sync_companies",
        lambda *args, **kwargs: record("local_companies", *args, **kwargs),
    )
    monkeypatch.setattr(
        sync,
        "sync_company_descriptions",
        lambda *args, **kwargs: record("local_descriptions", *args, **kwargs),
    )

    async def boards(*args, **kwargs) -> sync.BoardSyncEffects:
        await record("local_boards", *args, **kwargs)
        return sync.BoardSyncEffects()

    monkeypatch.setattr(sync, "sync_boards", boards)
    monkeypatch.setattr(
        sync,
        "resolve_pending_misses",
        lambda *args, **kwargs: record("local_misses", *args, **kwargs),
    )


async def test_default_sync_never_opens_legacy_mirror_and_commits_before_redis(
    monkeypatch,
) -> None:
    events: list[str] = []
    _patch_inputs(monkeypatch)
    _patch_local_writes(monkeypatch, events)
    local_conn = _Connection(events, "local")
    monkeypatch.setattr(sync, "create_local_pool", AsyncMock(return_value=_Pool(local_conn)))
    monkeypatch.setattr(sync, "get_typesense_client", lambda: None)

    async def forbidden_mirror_pool():
        raise AssertionError("default sync must not open DATABASE_URL")

    monkeypatch.setattr(sync, "create_pool", forbidden_mirror_pool)

    async def apply_redis(_effects: sync.BoardSyncEffects) -> None:
        assert "local_commit" in events
        events.append("redis")

    monkeypatch.setattr(sync, "apply_board_redis_effects", apply_redis)
    monkeypatch.setattr(sync.settings, "database_url", "postgresql://configured-but-not-opted-in")

    await sync.run_sync()

    assert events.index("local_commit") < events.index("redis")
    assert events[:2] == ["local_tx_enter", "local_lookups"]


async def test_explicit_legacy_mode_without_credential_fails_before_local_writes(
    monkeypatch,
) -> None:
    _patch_inputs(monkeypatch)
    create_local_pool = AsyncMock()
    monkeypatch.setattr(sync, "create_local_pool", create_local_pool)
    monkeypatch.setattr(sync.settings, "database_url", "")

    with pytest.raises(RuntimeError, match="--legacy-mirror requires DATABASE_URL"):
        await sync.run_sync(legacy_mirror=True)

    create_local_pool.assert_not_awaited()


async def test_local_failure_rolls_back_and_blocks_every_external_effect(monkeypatch) -> None:
    events: list[str] = []
    _patch_inputs(monkeypatch)
    _patch_local_writes(monkeypatch, events)
    local_conn = _Connection(events, "local")
    monkeypatch.setattr(sync, "create_local_pool", AsyncMock(return_value=_Pool(local_conn)))
    monkeypatch.setattr(sync, "get_typesense_client", lambda: None)

    async def fail_company_write(*args, **kwargs) -> None:
        del args, kwargs
        raise RuntimeError("local write failed")

    apply_redis = AsyncMock()
    create_mirror_pool = AsyncMock()
    monkeypatch.setattr(sync, "sync_companies", fail_company_write)
    monkeypatch.setattr(sync, "apply_board_redis_effects", apply_redis)
    monkeypatch.setattr(sync, "create_pool", create_mirror_pool)

    with pytest.raises(RuntimeError, match="local write failed"):
        await sync.run_sync()

    assert "local_rollback" in events
    assert "local_commit" not in events
    apply_redis.assert_not_awaited()
    create_mirror_pool.assert_not_awaited()


def test_crawler_sync_cli_cannot_select_legacy_mirror(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", ["crawler", "sync"])
    assert parse_args().command == "sync"

    monkeypatch.setattr(sys, "argv", ["crawler", "sync", "--legacy-mirror"])
    with pytest.raises(SystemExit):
        parse_args()


def test_watchlist_sync_has_no_web_job_posting_fallback() -> None:
    source = inspect.getsource(sync.sync_watchlists_typesense)

    assert "JOIN job_posting" not in source
    assert "local_conn or web_conn" not in source


async def test_legacy_mirror_failure_is_loud_and_blocks_redis(monkeypatch) -> None:
    events: list[str] = []
    _patch_inputs(monkeypatch)
    _patch_local_writes(monkeypatch, events)
    local_conn = _Connection(events, "local")
    mirror_conn = _Connection(events, "mirror")
    monkeypatch.setattr(sync, "create_local_pool", AsyncMock(return_value=_Pool(local_conn)))
    monkeypatch.setattr(sync, "create_pool", AsyncMock(return_value=_Pool(mirror_conn)))
    monkeypatch.setattr(sync, "get_typesense_client", lambda: None)
    monkeypatch.setattr(sync.settings, "database_url", "postgresql://configured")

    async def fail_mirror(*args, **kwargs) -> None:
        del args, kwargs
        assert "local_commit" in events
        events.append("mirror_failed")
        raise RuntimeError("mirror unavailable")

    apply_redis = AsyncMock()
    monkeypatch.setattr(sync, "sync_legacy_mirror", fail_mirror)
    monkeypatch.setattr(sync, "apply_board_redis_effects", apply_redis)

    with pytest.raises(RuntimeError, match="mirror unavailable"):
        await sync.run_sync(legacy_mirror=True)

    assert events.index("local_commit") < events.index("mirror_failed")
    apply_redis.assert_not_awaited()


async def test_legacy_board_mirror_uses_remote_schema_disable_query() -> None:
    local_conn = AsyncMock()
    mirror_conn = AsyncMock()
    local_conn.fetch.return_value = [
        {
            "id": "00000000-0000-0000-0000-000000000001",
            "company_id": "00000000-0000-0000-0000-000000000002",
            "board_slug": "acme-jobs",
            "board_url": "https://acme.test/jobs",
            "crawler_type": "greenhouse",
            "metadata": {},
        }
    ]

    await sync._mirror_boards_to_supabase(
        local_conn,
        mirror_conn,
        ["https://acme.test/jobs"],
        (
            (
                "00000000-0000-0000-0000-000000000001",
                "00000000-0000-0000-0000-000000000002",
            ),
        ),
    )

    statements = [call.args[0] for call in mirror_conn.execute.await_args_list]
    assert statements == [
        sync._MIRROR_BOARDS_SUPA,
        sync._REALIGN_BOARD_POSTING_COMPANIES_SUPA,
        sync._DISABLE_REMOVED_BOARDS,
    ]
    rehome_call = mirror_conn.execute.await_args_list[1]
    assert rehome_call.args[1] == ["00000000-0000-0000-0000-000000000001"]
    assert rehome_call.args[2] == ["00000000-0000-0000-0000-000000000002"]
    assert "quarantined_at" not in sync._DISABLE_REMOVED_BOARDS
    assert "quarantined_at" in sync._DISABLE_REMOVED_BOARDS_LOCAL


async def test_typesense_and_web_boundary_run_only_after_local_commit(monkeypatch) -> None:
    events: list[str] = []
    _patch_inputs(monkeypatch)
    _patch_local_writes(monkeypatch, events)
    local_conn = _Connection(events, "local")
    web_conn = _Connection(events, "web")
    monkeypatch.setattr(sync, "create_local_pool", AsyncMock(return_value=_Pool(local_conn)))

    async def create_web_pool() -> _Pool:
        assert "local_commit" in events
        events.append("web_pool")
        return _Pool(web_conn)

    monkeypatch.setattr(sync, "create_web_pool", create_web_pool)
    monkeypatch.setattr(sync, "get_typesense_client", lambda: object())
    monkeypatch.setattr(sync, "_snapshot_name_maps", AsyncMock(return_value={}))
    monkeypatch.setattr(sync, "_apply_taxonomy_renames", AsyncMock())

    async def apply_redis(_effects: sync.BoardSyncEffects) -> None:
        events.append("redis")

    async def sync_typesense(local, web, client) -> None:
        del client
        assert local is local_conn
        assert web is web_conn
        assert "local_commit" in events
        events.append("typesense")

    monkeypatch.setattr(sync, "apply_board_redis_effects", apply_redis)
    monkeypatch.setattr(sync, "sync_typesense", sync_typesense)

    await sync.run_sync()

    assert events.index("local_commit") < events.index("web_pool")
    assert events.index("web_pool") < events.index("redis") < events.index("typesense")
