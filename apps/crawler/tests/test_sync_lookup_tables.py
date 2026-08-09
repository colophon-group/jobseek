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


async def test_legacy_identity_drift_fails_without_writes() -> None:
    local_conn = AsyncMock()
    mirror_conn = AsyncMock()

    async def local_fetch(sql: str):
        if "FROM company" in sql:
            return [{"id": "local-id", "slug": "acme"}]
        return []

    async def mirror_fetch(sql: str):
        if "FROM company" in sql:
            return [{"id": "mirror-id", "slug": "acme"}]
        return []

    local_conn.fetch = AsyncMock(side_effect=local_fetch)
    mirror_conn.fetch = AsyncMock(side_effect=mirror_fetch)

    try:
        await sync._assert_legacy_identity_alignment(local_conn, mirror_conn)
    except RuntimeError as exc:
        assert "legacy mirror identity drift" in str(exc)
        assert "company" in str(exc)
    else:
        raise AssertionError("identity drift must fail closed")

    local_conn.execute.assert_not_awaited()
    mirror_conn.execute.assert_not_awaited()


async def test_legacy_preflight_allows_slug_stable_board_url_change() -> None:
    local_conn = AsyncMock()
    mirror_conn = AsyncMock()

    async def local_fetch(sql: str):
        if "FROM job_board" in sql:
            return [
                {
                    "id": "stable-id",
                    "board_slug": "acme-jobs",
                    "board_url": "https://new.acme.test/jobs",
                }
            ]
        return []

    async def mirror_fetch(sql: str):
        if "FROM job_board" in sql:
            return [
                {
                    "id": "stable-id",
                    "board_slug": "acme-jobs",
                    "board_url": "https://old.acme.test/jobs",
                }
            ]
        return []

    local_conn.fetch = AsyncMock(side_effect=local_fetch)
    mirror_conn.fetch = AsyncMock(side_effect=mirror_fetch)

    await sync._assert_legacy_identity_alignment(local_conn, mirror_conn)


async def test_legacy_sequence_alignment_uses_highest_mirror_identity() -> None:
    mirror_conn = AsyncMock()

    await sync._mirror_table(
        mirror_conn,
        "occupation",
        sync._MIRROR_OCCUPATIONS,
        [4, 7],
        ["one", "two"],
    )

    sequence_sql = mirror_conn.execute.await_args_list[1].args[0]
    assert "SELECT MAX(id) FROM occupation" in sequence_sql
    assert mirror_conn.execute.await_args_list[1].args[1:] == ()
