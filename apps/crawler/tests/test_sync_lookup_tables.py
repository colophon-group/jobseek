from __future__ import annotations

from unittest.mock import AsyncMock

import polars as pl

import src.sync as sync


def _df() -> pl.DataFrame:
    return pl.DataFrame({"slug": ["one"]})


async def test_lookup_table_sync_uses_only_local_natural_key_upserts(monkeypatch):
    local_conn = AsyncMock()
    calls: list[tuple[str, object]] = []

    async def record(label: str, conn, frame, dry_run: bool) -> None:
        assert conn is local_conn
        assert frame is not None
        assert dry_run is False
        calls.append((label, conn))

    monkeypatch.setattr(
        sync,
        "sync_occupation_domains",
        lambda conn, frame, dry_run: record("domains", conn, frame, dry_run),
    )
    monkeypatch.setattr(
        sync,
        "sync_occupations",
        lambda conn, frame, dry_run: record("occupations", conn, frame, dry_run),
    )
    monkeypatch.setattr(
        sync,
        "sync_seniority",
        lambda conn, frame, dry_run: record("seniority", conn, frame, dry_run),
    )
    monkeypatch.setattr(
        sync,
        "sync_technologies",
        lambda conn, frame, dry_run: record("technologies", conn, frame, dry_run),
    )
    monkeypatch.setattr(
        sync,
        "sync_industries",
        lambda conn, frame, dry_run: record("industries", conn, frame, dry_run),
    )

    await sync.sync_lookup_tables_local(
        local_conn,
        occupation_domains=_df(),
        occupations=_df(),
        seniority_df=_df(),
        technologies=_df(),
        industries=_df(),
        dry_run=False,
    )

    assert [label for label, _conn in calls] == [
        "domains",
        "occupations",
        "seniority",
        "technologies",
        "industries",
    ]
    local_conn.fetch.assert_not_awaited()
    local_conn.execute.assert_not_awaited()


def test_local_lookup_sql_never_updates_existing_ids() -> None:
    for sql in (
        sync._UPSERT_OCCUPATION_DOMAINS,
        sync._UPSERT_OCCUPATIONS,
        sync._UPSERT_SENIORITY,
        sync._UPSERT_TECHNOLOGIES,
    ):
        normalized = " ".join(sql.split())
        assert "SET id" not in normalized
        assert "DELETE" not in normalized
