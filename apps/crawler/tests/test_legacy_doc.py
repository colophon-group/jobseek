from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.core.scrapers.dom import scrape
from src.core.scrapers.legacy_doc import _OLE_MAGIC, parse_bytes


class _AntiwordProcess:
    returncode = 0

    def __init__(self, output: bytes) -> None:
        self.output = output

    async def communicate(self):
        return self.output, b""

    def kill(self) -> None:
        self.returncode = -9


async def test_extracts_legacy_word_job_with_bounded_antiword(monkeypatch):
    source_paths: list[Path] = []

    async def fake_subprocess(*args, **kwargs):
        _ = kwargs
        assert args[:5] == ("antiword", "-m", "UTF-8.txt", "-w", "0")
        source_path = Path(args[5])
        assert source_path.read_bytes() == _OLE_MAGIC + b"word-data"
        source_paths.append(source_path)
        return _AntiwordProcess(
            b"POSITION TITLE:\tCare Coordinator\n\nPOSITION SUMMARY: Support clients & families.\n"
        )

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subprocess)
    result = await parse_bytes(
        _OLE_MAGIC + b"word-data",
        "https://example.com/Care-Coordinator.doc",
        {
            "title_source": "text",
            "title_pattern": r"(?im)^POSITION TITLE:\s*(.+)$",
            "defaults": {"locations": ["Marshall, MN"]},
        },
    )

    assert result.title == "Care Coordinator"
    assert result.locations == ["Marshall, MN"]
    assert result.description == (
        "<p>POSITION TITLE: Care Coordinator</p>\n"
        "<p>POSITION SUMMARY: Support clients &amp; families.</p>"
    )
    assert source_paths and not source_paths[0].exists()


async def test_dom_document_fallback_dispatches_legacy_word(monkeypatch):
    async def fake_subprocess(*args, **kwargs):
        _ = args, kwargs
        return _AntiwordProcess(b"Program Manager\n\nLead the care coordination program.")

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subprocess)
    content = _OLE_MAGIC + b"word-data"
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, content=content, request=request)
        )
    ) as client:
        result = await scrape(
            "https://example.com/Program-Manager.doc",
            {
                "steps": [{"tag": "h1", "field": "title"}],
                "document_fallback": {"doc": {"title_source": "text"}},
            },
            client,
        )

    assert result.title == "Program Manager"
    assert "Lead the care coordination program." in (result.description or "")


@pytest.mark.parametrize(
    "config",
    [
        {"unknown": True},
        {"title_source": "heading"},
        {"title_pattern": "("},
        {"location_pattern": 1},
    ],
)
async def test_rejects_invalid_legacy_word_config(config):
    with pytest.raises(ValueError, match="legacy Word"):
        await parse_bytes(_OLE_MAGIC + b"word-data", "https://example.com/role.doc", config)


async def test_rejects_non_ole_document_before_launch(monkeypatch):
    async def fail_subprocess(*args, **kwargs):
        raise AssertionError((args, kwargs))

    monkeypatch.setattr("asyncio.create_subprocess_exec", fail_subprocess)
    with pytest.raises(ValueError, match="invalid OLE"):
        await parse_bytes(b"not-a-word-document", "https://example.com/role.doc", {})
