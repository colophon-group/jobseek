"""The replay trace observes a scheduled request without changing its result."""

from __future__ import annotations

import base64
import json
import stat

import httpx
import pytest

from src.processing import workday_capture

BOARD_ID = "bcd90676-101c-4e58-b427-98edd4e09b7d"
METADATA = {
    "company": "elevancehealth",
    "wd_instance": "wd1",
    "site": "ANT",
    "all_sites": False,
}


@pytest.mark.asyncio
async def test_capture_observes_one_response_without_another_request(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDAY_REPLAY_CAPTURE_BOARD_ID", BOARD_ID)
    monkeypatch.setattr(workday_capture, "_TRACE_DIR", tmp_path)
    capture = workday_capture.start_workday_capture(BOARD_ID, "workday", METADATA)
    assert capture is not None

    requests: list[httpx.Request] = []
    body = b'{"total":1,"jobPostings":[{"externalPath":"/job/one"}]}'

    def respond(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            content=body,
            headers={"content-type": "application/json", "set-cookie": "secret=private"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        client.event_hooks["request"].append(capture.capture_request)
        client.event_hooks["response"].append(capture.capture_response)
        response = await client.post(capture.api_url, json={"limit": 20, "offset": 0})
        assert response.json()["total"] == 1
    capture.finish(monitor_succeeded=True)

    assert len(requests) == 1
    result = tmp_path / f"jobseek-workday-{BOARD_ID}.jsonl"
    records = [json.loads(line) for line in result.read_text().splitlines()]
    assert len(records) == 3
    assert records[0]["schema"] == "jobseek.workday-replay/v1"
    assert records[1]["status"] == 200
    assert base64.b64decode(records[1]["response_body_b64"]) == body
    assert json.loads(base64.b64decode(records[1]["request_body_b64"])) == {
        "limit": 20,
        "offset": 0,
    }
    assert records[2]["complete"] is True
    assert records[2]["requests"] == records[2]["responses"] == 1
    assert "secret" not in result.read_text()
    assert stat.S_IMODE(result.stat().st_mode) == 0o600
    assert workday_capture.start_workday_capture(BOARD_ID, "workday", METADATA) is None


@pytest.mark.asyncio
async def test_failed_cycle_remains_partial(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDAY_REPLAY_CAPTURE_BOARD_ID", BOARD_ID)
    monkeypatch.setattr(workday_capture, "_TRACE_DIR", tmp_path)
    capture = workday_capture.start_workday_capture(BOARD_ID, "workday", METADATA)
    assert capture is not None
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(429, content=b"retry"))
    ) as client:
        client.event_hooks["request"].append(capture.capture_request)
        client.event_hooks["response"].append(capture.capture_response)
        response = await client.post(capture.api_url, json={"limit": 20, "offset": 0})
        assert response.status_code == 429
    capture.finish(monitor_succeeded=False)
    assert capture.path.exists()
    assert not capture.path.with_suffix("").exists()


@pytest.mark.asyncio
async def test_oversized_response_preserves_live_result_but_rejects_replay(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDAY_REPLAY_CAPTURE_BOARD_ID", BOARD_ID)
    monkeypatch.setattr(workday_capture, "_TRACE_DIR", tmp_path)
    capture = workday_capture.start_workday_capture(BOARD_ID, "workday", METADATA)
    assert capture is not None
    body = b"x" * (workday_capture._MAX_RESPONSE_BYTES + 1)
    requests = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        client.event_hooks["request"].append(capture.capture_request)
        client.event_hooks["response"].append(capture.capture_response)
        response = await client.post(capture.api_url, json={"limit": 20, "offset": 0})
        assert response.content == body
    capture.finish(monitor_succeeded=True)
    assert requests == 1
    assert capture.path.exists()
    assert not capture.path.with_suffix("").exists()


@pytest.mark.asyncio
async def test_transport_failure_makes_trace_incomplete(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDAY_REPLAY_CAPTURE_BOARD_ID", BOARD_ID)
    monkeypatch.setattr(workday_capture, "_TRACE_DIR", tmp_path)
    capture = workday_capture.start_workday_capture(BOARD_ID, "workday", METADATA)
    assert capture is not None
    attempts = 0

    def respond(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise httpx.ConnectError("offline", request=request)
        return httpx.Response(200, content=b'{"total":0,"jobPostings":[]}')

    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        client.event_hooks["request"].append(capture.capture_request)
        client.event_hooks["response"].append(capture.capture_response)
        with pytest.raises(httpx.ConnectError):
            await client.post(capture.api_url, json={"limit": 20, "offset": 0})
        response = await client.post(capture.api_url, json={"limit": 20, "offset": 0})
        assert response.status_code == 200
    capture.finish(monitor_succeeded=True)
    assert attempts == 2
    assert capture.requests == 2 and capture.responses == 1
    assert capture.path.exists()
    assert not capture.path.with_suffix("").exists()


def test_capture_only_selected_direct_workday_board(tmp_path, monkeypatch):
    monkeypatch.setenv("WORKDAY_REPLAY_CAPTURE_BOARD_ID", BOARD_ID)
    monkeypatch.setattr(workday_capture, "_TRACE_DIR", tmp_path)
    assert workday_capture.start_workday_capture("other-board", "workday", METADATA) is None
    assert workday_capture.start_workday_capture(BOARD_ID, "dom", METADATA) is None
    assert (
        workday_capture.start_workday_capture(BOARD_ID, "workday", {**METADATA, "proxy": True})
        is None
    )
    assert list(tmp_path.iterdir()) == []
