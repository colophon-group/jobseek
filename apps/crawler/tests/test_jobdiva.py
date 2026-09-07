"""Tests for the JobDiva candidate portal monitor."""

from __future__ import annotations

from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import jobdiva

TENANT = "tenant-key-1234567890"
BOARD_URL = f"https://www2.jobdiva.com/portal/?a={TENANT}&compid=0#/"


def _row(job_id: int) -> dict:
    return {"id": job_id, "title": f"Job {job_id}"}


@pytest.mark.asyncio
async def test_discover_drains_native_range_endpoint():
    calls: list[tuple[int, int, int]] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/a"):
            assert request.headers["a"] == TENANT
            return httpx.Response(
                200,
                json={"token": "fresh", "portalID": 198, "a": TENANT, "compid": -1},
            )
        if request.url.path.endswith("/job/searchjobsportal"):
            body = parse_qs(request.content.decode(), keep_blank_values=True)
            assert body["from"] == ["1"]
            assert body["to"] == ["200"]
            return httpx.Response(
                200,
                json={
                    "total": 401,
                    "data": [_row(value) for value in range(1, 201)],
                },
            )
        if request.url.path.endswith("/job/getmore"):
            start = int(request.url.params["from"])
            to = int(request.url.params["to"])
            count = int(request.url.params["count"])
            calls.append((start, to, count))
            last = min(start + count - 1, 401)
            return httpx.Response(
                200,
                json={
                    # The getmore payload's hint is not the snapshot contract;
                    # the initiating search count is authoritative for a pass.
                    "total": 999,
                    "data": [_row(value) for value in range(start, last + 1)],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jobdiva.discover({"board_url": BOARD_URL}, client)

    assert isinstance(result, set)
    assert len(result) == 401
    assert calls == [
        (201, 0, 200),
        (401, 0, 201),
        (201, 0, 200),
        (401, 0, 201),
    ]
    assert f"https://www2.jobdiva.com/portal/?a={TENANT}&compid=0#/jobs/401" in result


@pytest.mark.asyncio
async def test_discover_marks_duplicate_page_as_truncated():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/auth/a"):
            return httpx.Response(
                200,
                json={"token": "fresh", "portalID": 198, "a": TENANT, "compid": -1},
            )
        if request.url.path.endswith("/job/searchjobsportal"):
            return httpx.Response(
                200,
                json={"total": 201, "data": [_row(value) for value in range(1, 201)]},
            )
        return httpx.Response(200, json={"total": 201, "data": [_row(200)]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jobdiva.discover({"board_url": BOARD_URL}, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 200


@pytest.mark.asyncio
async def test_discover_retries_changed_snapshot_until_two_passes_match():
    searches = 0
    active_end = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal searches, active_end
        if request.url.path.endswith("/auth/a"):
            return httpx.Response(
                200,
                json={"token": "fresh", "portalID": 198, "a": TENANT, "compid": -1},
            )
        if request.url.path.endswith("/job/searchjobsportal"):
            searches += 1
            active_end = 401 if searches == 1 else 402
            return httpx.Response(
                200,
                json={
                    "total": active_end,
                    "data": [_row(value) for value in range(1, 201)],
                },
            )
        if request.url.path.endswith("/job/getmore"):
            start = int(request.url.params["from"])
            end = min(start + int(request.url.params["count"]) - 1, active_end)
            return httpx.Response(
                200,
                json={
                    "total": active_end,
                    "data": [_row(value) for value in range(start, end + 1)],
                },
            )
        raise AssertionError(f"Unexpected request: {request.method} {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await jobdiva.discover({"board_url": BOARD_URL}, client)

    assert isinstance(result, set)
    assert len(result) == 402
    assert searches == 3


def test_tenant_rejects_unbounded_or_missing_values():
    with pytest.raises(ValueError, match="valid JobDiva tenant"):
        jobdiva._tenant_from_board({"board_url": "https://www2.jobdiva.com/portal/"})
    with pytest.raises(ValueError, match="valid JobDiva tenant"):
        jobdiva._tenant_from_board({"board_url": "https://www2.jobdiva.com/portal/?a=too-short"})
