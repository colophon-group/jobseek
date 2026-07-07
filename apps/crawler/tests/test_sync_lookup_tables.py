from __future__ import annotations

import polars as pl
from structlog.testing import capture_logs

import src.sync as sync


class FakeConn:
    def __init__(self, fetch_results: dict[str, list[dict]]) -> None:
        self.fetch_results = fetch_results
        self.executed: list[str] = []

    async def fetch(self, sql: str, *args) -> list[dict]:
        del args
        key = " ".join(sql.split())
        if key not in self.fetch_results:
            raise AssertionError(f"unexpected fetch: {key}")
        return self.fetch_results[key]

    async def execute(self, sql: str, *args) -> str:
        del args
        self.executed.append(" ".join(sql.split()))
        return "OK"


def _df() -> pl.DataFrame:
    return pl.DataFrame({"slug": ["one"]})


async def _noop(*args, **kwargs) -> None:
    del args, kwargs


def _patch_non_identity_sync(monkeypatch) -> None:
    monkeypatch.setattr(sync, "sync_occupation_domains", _noop)
    monkeypatch.setattr(sync, "sync_occupations", _noop)
    monkeypatch.setattr(sync, "sync_seniority", _noop)
    monkeypatch.setattr(sync, "sync_technologies", _noop)
    monkeypatch.setattr(sync, "sync_industries", _noop)
    monkeypatch.setattr(sync, "_populate_locations_if_empty", _noop)
    monkeypatch.setattr(sync, "_populate_currency_rates_if_empty", _noop)


async def test_lookup_table_sync_skips_exclusive_ddl_when_identities_match(monkeypatch):
    _patch_non_identity_sync(monkeypatch)
    rows_by_query = {
        "SELECT id, slug FROM occupation_domain": [{"id": 1, "slug": "domain"}],
        "SELECT id, slug FROM occupation": [{"id": 2, "slug": "occupation"}],
        "SELECT id, slug FROM seniority": [{"id": 3, "slug": "seniority"}],
    }
    supa_conn = FakeConn(rows_by_query)
    local_conn = FakeConn(rows_by_query)

    with capture_logs() as logs:
        await sync.sync_lookup_tables_local(
            supa_conn,
            local_conn,
            occupation_domains=_df(),
            occupations=_df(),
            seniority_df=_df(),
            technologies=_df(),
            industries=_df(),
            dry_run=False,
        )

    assert not any("ALTER TABLE job_posting" in sql for sql in local_conn.executed)
    assert not any(sql.startswith("DELETE FROM") for sql in local_conn.executed)
    assert any(log["event"] == "sync.lookup_tables_local.identity_up_to_date" for log in logs)
    assert not any(log["event"] == "sync.lookup_tables_local.mirrored" for log in logs)


async def test_lookup_table_sync_mirrors_when_identities_drift(monkeypatch):
    _patch_non_identity_sync(monkeypatch)
    mirrored: list[tuple[str, str, list[int], list[str]]] = []

    async def fake_mirror_table(conn, table: str, sql: str, ids: list[int], slugs: list[str]):
        del conn
        mirrored.append((table, sql, ids, slugs))

    monkeypatch.setattr(sync, "_mirror_table", fake_mirror_table)
    supa_conn = FakeConn(
        {
            "SELECT id, slug FROM occupation_domain": [{"id": 1, "slug": "domain"}],
            "SELECT id, slug FROM occupation": [{"id": 2, "slug": "occupation"}],
            "SELECT id, slug FROM seniority": [{"id": 3, "slug": "seniority"}],
        }
    )
    local_conn = FakeConn(
        {
            "SELECT id, slug FROM occupation_domain": [{"id": 10, "slug": "domain"}],
            "SELECT id, slug FROM occupation": [{"id": 20, "slug": "occupation"}],
            "SELECT id, slug FROM seniority": [{"id": 30, "slug": "seniority"}],
        }
    )

    await sync.sync_lookup_tables_local(
        supa_conn,
        local_conn,
        occupation_domains=_df(),
        occupations=_df(),
        seniority_df=_df(),
        technologies=_df(),
        industries=_df(),
        dry_run=False,
    )

    assert any(
        "ALTER TABLE job_posting DROP CONSTRAINT IF EXISTS job_posting_occupation_id_fkey" in sql
        for sql in local_conn.executed
    )
    assert any(
        "ALTER TABLE job_posting DROP CONSTRAINT IF EXISTS job_posting_seniority_id_fkey" in sql
        for sql in local_conn.executed
    )
    assert any(sql == "DELETE FROM occupation" for sql in local_conn.executed)
    assert any(
        "ALTER TABLE job_posting ADD CONSTRAINT job_posting_occupation_id_fkey" in sql
        for sql in local_conn.executed
    )
    assert any(
        "ALTER TABLE job_posting ADD CONSTRAINT job_posting_seniority_id_fkey" in sql
        for sql in local_conn.executed
    )
    assert mirrored == [
        ("occupation_domain", sync._MIRROR_OCCUPATION_DOMAINS, [1], ["domain"]),
        ("occupation", sync._MIRROR_OCCUPATIONS, [2], ["occupation"]),
        ("seniority", sync._MIRROR_SENIORITY, [3], ["seniority"]),
    ]
