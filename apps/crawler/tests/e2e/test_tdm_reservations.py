"""Real-DB proof that reservations cover retained and concurrently inserted copies."""

from __future__ import annotations

import asyncio
import os
import uuid

import asyncpg
import pytest

from src.processing.tdm import record_reservation
from src.shared.tdm import TDMReservedError

pytestmark = pytest.mark.skipif(
    os.getenv("REQUIRE_POSTGRES_E2E") != "true", reason="requires isolated migrated PostgreSQL"
)


@pytest.fixture
async def fixture_db():
    conn = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    company = uuid.uuid4()
    boards = [uuid.uuid4(), uuid.uuid4()]
    for board in boards:
        await conn.execute(
            "INSERT INTO job_board (id, company_id, board_url) VALUES ($1,$2,$3)",
            board,
            company,
            f"https://tdm.invalid/{board}",
        )
    try:
        yield conn, company, boards
    finally:
        await conn.execute(
            "DELETE FROM descriptions WHERE posting_id IN "
            "(SELECT id FROM job_posting WHERE board_id = ANY($1::uuid[]))",
            boards,
        )
        await conn.execute("DELETE FROM job_posting WHERE board_id = ANY($1::uuid[])", boards)
        await conn.execute("DELETE FROM job_board WHERE id = ANY($1::uuid[])", boards)
        await conn.close()


async def insert(conn, company, board):
    posting = uuid.uuid4()
    await conn.execute(
        "INSERT INTO job_posting (id, company_id, board_id, source_url, titles) "
        "VALUES ($1,$2,$3,$4,ARRAY['Original title'])",
        posting,
        company,
        board,
        f"https://tdm.invalid/posting/{posting}",
    )
    return posting


def reservation():
    return TDMReservedError("https://tdm.invalid/jobs", source="header")


async def test_board_reservation_covers_existing_and_future_without_delisting(fixture_db):
    conn, company, boards = fixture_db
    old = await insert(conn, company, boards[0])
    sibling = await insert(conn, company, boards[1])
    before = await conn.fetchval("SELECT updated_at FROM job_posting WHERE id=$1", old)
    await record_reservation(conn, board_id=boards[0], error=reservation())
    new = await insert(conn, company, boards[0])
    rows = await conn.fetch(
        "SELECT * FROM job_posting WHERE id=ANY($1::uuid[])", [old, new, sibling]
    )
    by_id = {row["id"]: row for row in rows}
    assert by_id[old]["tdm_reserved"] and by_id[new]["tdm_reserved"]
    assert not by_id[sibling]["tdm_reserved"]
    assert by_id[old]["updated_at"] > before
    assert all(row["is_active"] and row["titles"] == ["Original title"] for row in rows)
    # An ordinary content update or attempted posting-only reset cannot reopen a reserved board.
    await conn.execute(
        "UPDATE job_posting SET tdm_reserved=false, titles=ARRAY['Refreshed'] WHERE id=$1", old
    )
    assert await conn.fetchval("SELECT tdm_reserved FROM job_posting WHERE id=$1", old)


async def test_detail_reservation_leaves_sibling_posting_eligible(fixture_db):
    conn, company, boards = fixture_db
    one = await insert(conn, company, boards[0])
    two = await insert(conn, company, boards[0])
    await record_reservation(conn, posting_id=one, error=reservation())
    assert await conn.fetchval("SELECT tdm_reserved FROM job_posting WHERE id=$1", one)
    assert not await conn.fetchval("SELECT tdm_reserved FROM job_posting WHERE id=$1", two)
    assert not await conn.fetchval("SELECT tdm_reserved FROM job_board WHERE id=$1", boards[0])


async def test_inflight_insert_cannot_escape_board_reservation(fixture_db):
    conn, company, boards = fixture_db
    writer = await asyncpg.connect(os.environ["LOCAL_DATABASE_URL"])
    tx = writer.transaction()
    await tx.start()
    task = None
    try:
        posting = await insert(writer, company, boards[0])
        task = asyncio.create_task(
            record_reservation(conn, board_id=boards[0], error=reservation())
        )
        await asyncio.sleep(0.05)
        assert not task.done()  # parent lock orders the reservation after this insert
        await tx.commit()
        await asyncio.wait_for(task, 5)
        assert await conn.fetchval("SELECT tdm_reserved FROM job_posting WHERE id=$1", posting)
    finally:
        await writer.close()
        if task is not None and not task.done():
            task.cancel()


async def test_real_ashby_entrypoint_restricts_retained_copy(fixture_db, monkeypatch):
    import json
    from unittest.mock import AsyncMock, MagicMock

    import httpx

    from src.labeller.prepare import load_posting
    from src.processing.board import DeadlineExtender, _process_one_board_streaming
    from src.runtime.extraction import PythonMonitorRuntime

    conn, company, boards = fixture_db
    await conn.execute(
        "UPDATE job_board SET board_url=$2, crawler_type='ashby' WHERE id=$1",
        boards[0],
        f"https://jobs.ashbyhq.com/tdm-{boards[0]}",
    )
    posting = await insert(conn, company, boards[0])
    original = "<p>Previously stored description</p>"
    await conn.execute(
        "INSERT INTO descriptions (posting_id, locale, html, hash) VALUES ($1,'en',$2,42)",
        posting,
        original,
    )
    pool = await asyncpg.create_pool(os.environ["LOCAL_DATABASE_URL"], min_size=1, max_size=2)
    for name in (
        "_get_currency_rates",
        "_get_technology_ids",
        "_get_occupation_ids",
        "_get_seniority_ids",
    ):
        monkeypatch.setattr(f"src.batch.{name}", AsyncMock(return_value={}))
    monkeypatch.setattr("src.batch._get_location_resolver", AsyncMock(return_value=MagicMock()))
    requests = []

    def response(request):
        requests.append(str(request.url))
        return httpx.Response(200, headers={"TDM-Reservation": "1"}, json={"jobs": []})

    try:
        assert await load_posting(pool, str(posting)) is not None
        board = await conn.fetchrow("SELECT * FROM job_board WHERE id=$1", boards[0])
        async with httpx.AsyncClient(transport=httpx.MockTransport(response)) as http:
            result = await _process_one_board_streaming(
                board,
                pool,
                http,
                DeadlineExtender(),
                monitor_runtime=PythonMonitorRuntime(),
            )
            assert result.status == "tdm_reserved"
            assert len(requests) == 1 and requests[0].startswith("https://api.ashbyhq.com/")
            evidence = await conn.fetchval(
                "SELECT tdm_reservation FROM job_board WHERE id=$1", boards[0]
            )
            assert json.loads(evidence)["url"] == requests[0]
            assert await load_posting(pool, str(posting)) is None
            # The stale Redis-shaped board snapshot is still refused on the next run.
            again = await _process_one_board_streaming(
                board,
                pool,
                http,
                DeadlineExtender(),
                monitor_runtime=PythonMonitorRuntime(),
            )
            assert again.status == "tdm_reserved" and len(requests) == 1
        assert await conn.fetchval("SELECT is_active FROM job_posting WHERE id=$1", posting)
        assert (
            await conn.fetchval("SELECT html FROM descriptions WHERE posting_id=$1", posting)
            == original
        )
    finally:
        await pool.close()
