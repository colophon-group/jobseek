"""The opt-in DOM capture must remain local, bounded, and one-run only."""

from __future__ import annotations

import base64
import json
import re
import stat
from pathlib import Path

import pytest

from src.core.monitors import dom, dom_capture


class FakePage:
    def __init__(self) -> None:
        self.main_frame = object()
        self.url = "https://jobs.booking.com/booking/jobs"
        self.listeners: list = []
        self.navigations = 0

    def on(self, event: str, callback) -> None:
        assert event == "response"
        self.listeners.append(callback)

    def remove_listener(self, event: str, callback) -> None:
        assert event == "response"
        self.listeners.remove(callback)

    async def evaluate(self, expression: str, selector: str) -> list[str]:
        assert "document.querySelectorAll" in expression
        assert selector == "a[href]"
        return ["https://jobs.booking.com/booking/jobs/123", "https://jobs.booking.com/other"]


class FakeRequest:
    method = "GET"

    def is_navigation_request(self) -> bool:
        return True


class FakeResponse:
    def __init__(self, page: FakePage, body: bytes) -> None:
        self.frame = page.main_frame
        self.request = FakeRequest()
        self.url = page.url
        self.status = 200
        self.headers = {"content-length": str(len(body))}
        self._body = body
        self.body_reads = 0

    async def body(self) -> bytes:
        self.body_reads += 1
        return self._body


@pytest.mark.asyncio
async def test_booking_capture_is_opt_in_and_writes_one_private_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = FakePage()
    path = tmp_path / "booking.json"
    monkeypatch.setattr(dom_capture, "_PATH", path)
    assert dom_capture.start_booking_capture(page.url, page) is None
    monkeypatch.setenv("BOOKING_DOM_REPLAY_CAPTURE", "1")
    assert dom_capture.start_booking_capture("https://other.example/jobs", page) is None

    capture = dom_capture.start_booking_capture(page.url, page)
    assert capture is not None
    response = FakeResponse(page, b"<html>main response</html>")
    capture.on_response(response)
    await capture.finish(
        page=page,
        html='<html><a href="/booking/jobs/123">Job</a></html>',
        links=["https://jobs.booking.com/booking/jobs/123"],
        urls={"https://jobs.booking.com/booking/jobs/123"},
    )
    assert response.body_reads == 1
    record = json.loads(path.read_text())
    assert record["schema"] == "jobseek.dom-replay/v1"
    assert base64.b64decode(record["main_body_b64"]) == b"<html>main response</html>"
    assert record["matched_urls"] == ["https://jobs.booking.com/booking/jobs/123"]
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert dom_capture.start_booking_capture(page.url, page) is None


@pytest.mark.asyncio
async def test_booking_capture_rejects_oversize_response(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = FakePage()
    path = tmp_path / "booking.json"
    monkeypatch.setattr(dom_capture, "_PATH", path)
    monkeypatch.setenv("BOOKING_DOM_REPLAY_CAPTURE", "1")
    monkeypatch.setattr(dom_capture, "_MAX_MAIN_BODY", 4)
    capture = dom_capture.start_booking_capture(page.url, page)
    assert capture is not None
    capture.on_response(FakeResponse(page, b"12345"))
    await capture.finish(page=page, html="html", links=[], urls=set())
    assert not path.exists()


@pytest.mark.asyncio
async def test_rendered_path_captures_the_existing_navigation_only(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    page = FakePage()
    path = tmp_path / "booking.json"
    monkeypatch.setattr(dom_capture, "_PATH", path)
    monkeypatch.setenv("BOOKING_DOM_REPLAY_CAPTURE", "1")

    async def navigate_once(page: FakePage, board_url: str, config: dict) -> None:
        assert board_url == page.url
        page.navigations += 1
        response = FakeResponse(page, b"<html>main</html>")
        for listener in page.listeners:
            listener(response)

    async def no_actions(page: FakePage, actions: list) -> None:
        assert actions == []

    async def content(page: FakePage) -> str:
        return "<html>rendered</html>"

    monkeypatch.setattr(dom, "_navigate_board_root", navigate_once)
    monkeypatch.setattr(dom, "run_actions", no_actions)
    monkeypatch.setattr(dom, "safe_content", content)
    monkeypatch.setattr(dom, "_raise_if_bot_challenge", lambda url, html: None)
    urls = await dom._extract_links_rendered(
        page,
        {"_board_url": page.url, "render": True},
        re.compile(r"jobs\.booking\.com/booking/jobs/\d+"),
    )
    assert urls == {"https://jobs.booking.com/booking/jobs/123"}
    assert page.navigations == 1
    assert page.listeners == []
    assert json.loads(path.read_text())["matched_urls"] == sorted(urls)
