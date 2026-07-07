from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import structlog

from src.core import monitor as monitor_module
from src.core.monitors import get_save_raw


class _Response:
    def __init__(self, *, status_code: int = 200, text: str = "", payload: Any = None) -> None:
        self.status_code = status_code
        self.text = text
        self._payload = payload if payload is not None else {}

    def json(self) -> Any:
        return self._payload


class _Client:
    def __init__(self, response: _Response) -> None:
        self.response = response
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def get(self, url: str, **kwargs: Any) -> _Response:
        self.calls.append((url, kwargs))
        return self.response


@pytest.mark.asyncio
async def test_save_raw_unknown_monitor_type_is_ignored(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def fake_save_raw(
        artifact_dir: Path,
        board_url: str,
        metadata: dict[str, Any],
        client: object,
    ) -> None:
        raise AssertionError("unregistered saver should not be called")

    monkeypatch.setattr(
        monitor_module,
        "get_save_raw",
        lambda name: fake_save_raw if name == "known" else None,
    )

    await monitor_module._save_raw(
        tmp_path,
        "https://example.com/jobs",
        "unknown",
        {"token": "acme"},
        object(),
    )


@pytest.mark.asyncio
async def test_save_raw_dispatches_registered_hook(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    seen: list[tuple[Path, str, dict[str, Any], object]] = []

    async def fake_save_raw(
        artifact_dir: Path,
        board_url: str,
        metadata: dict[str, Any],
        client: object,
    ) -> None:
        seen.append((artifact_dir, board_url, metadata, client))

    monkeypatch.setattr(monitor_module, "get_save_raw", lambda name: fake_save_raw)
    client = object()

    await monitor_module._save_raw(
        tmp_path,
        "https://example.com/jobs",
        "custom",
        {"token": "acme"},
        client,
    )

    assert seen == [(tmp_path, "https://example.com/jobs", {"token": "acme"}, client)]


@pytest.mark.asyncio
async def test_save_raw_logs_handler_failures(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    async def broken_save_raw(
        artifact_dir: Path,
        board_url: str,
        metadata: dict[str, Any],
        client: object,
    ) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(monitor_module, "get_save_raw", lambda name: broken_save_raw)

    with structlog.testing.capture_logs() as logs:
        await monitor_module._save_raw(
            tmp_path,
            "https://example.com/jobs",
            "broken",
            {},
            object(),
        )

    assert any(
        event["event"] == "monitor.save_raw_failed"
        and event["monitor_type"] == "broken"
        and event["board_url"] == "https://example.com/jobs"
        for event in logs
    )


@pytest.mark.parametrize(
    "monitor_type",
    [
        "api_sniffer",
        "ashby",
        "breezy",
        "dom",
        "greenhouse",
        "hireology",
        "lever",
        "nextdata",
        "personio",
        "recruitee",
        "rippling",
        "rss",
        "sitemap",
        "talentbrew",
    ],
)
def test_raw_artifact_savers_are_registered(monitor_type: str) -> None:
    assert get_save_raw(monitor_type) is not None


@pytest.mark.asyncio
async def test_greenhouse_raw_saver_uses_monitor_api_url(tmp_path: Path) -> None:
    save_raw = get_save_raw("greenhouse")
    assert save_raw is not None
    client = _Client(_Response(payload={"jobs": [{"title": "Engineer"}]}))

    await save_raw(tmp_path, "https://boards.greenhouse.io/acme", {}, client)

    assert client.calls[0][0] == "https://boards-api.greenhouse.io/v1/boards/acme/jobs"
    assert client.calls[0][1]["params"] == {"content": "true"}
    assert json.loads((tmp_path / "response.json").read_text()) == {"jobs": [{"title": "Engineer"}]}


@pytest.mark.asyncio
async def test_sitemap_raw_saver_uses_sitemap_headers(tmp_path: Path) -> None:
    save_raw = get_save_raw("sitemap")
    assert save_raw is not None
    client = _Client(_Response(text="<urlset />"))

    await save_raw(
        tmp_path,
        "https://example.com/jobs",
        {"sitemap_url": "https://example.com/sitemap.xml"},
        client,
    )

    assert client.calls[0][0] == "https://example.com/sitemap.xml"
    assert "jobseek-crawler" in client.calls[0][1]["headers"]["User-Agent"]
    assert (tmp_path / "sitemap.xml").read_text() == "<urlset />"
