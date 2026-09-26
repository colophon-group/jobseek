"""Same-byte Workable detail projection and exclusive route contract."""

from __future__ import annotations

import json
import stat
import subprocess
from dataclasses import asdict
from pathlib import Path

import httpx
import pytest

from src.core.job_content import JobContent
from src.core.scrapers.workable import _parse_detail, _parse_markdown_detail, scrape
from src.processing.scrape import _runtime_for_scrape
from src.runtime.workable_go_detail import GoWorkableDetailRuntime

MODULE = Path(__file__).resolve().parents[1] / "go" / "workable-monitor"
SOURCE = "https://apply.workable.com/acme/j/ABC123/"
API = "https://apply.workable.com/api/v2/accounts/acme/jobs/ABC123"


def project(format: str, body: bytes) -> JobContent:
    result = subprocess.run(
        ["go", "run", "./cmd/detail-project", "--format", format],
        input=body,
        capture_output=True,
        cwd=MODULE,
        check=True,
    )
    return JobContent(**json.loads(result.stdout))


def fake_binary(tmp_path: Path, payload: dict) -> str:
    path = tmp_path / "workable-detail-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        "assert sys.argv[1:] == ['--url', 'https://apply.workable.com/acme/j/ABC123/']\n"
        f"print(json.dumps({payload!r}))\n"
    )
    path.chmod(0o755)
    return str(path)


def test_exact_json_response_projects_like_python():
    detail = {
        "title": "Engineer",
        "description": "<p>Build</p>",
        "requirements": "<p>Go</p>",
        "benefits": "<p>Leave</p>",
        "locations": [
            {"city": "Zurich", "country": "Switzerland"},
            {"city": "Zurich", "country": "Switzerland"},
            "Remote",
        ],
        "workplace": "on_site",
        "type": "full",
        "published": "2026-09-25",
        "department": ["Engineering", "Platform"],
    }
    body = json.dumps(detail).encode()
    assert asdict(project("json", body)) == asdict(_parse_detail(json.loads(body)))


def test_sparse_json_response_keeps_empty_scalar_values():
    body = b'{"title":"","published":"","remote":"yes"}'
    assert asdict(project("json", body)) == asdict(_parse_detail(json.loads(body)))


def test_exact_markdown_response_projects_like_python():
    body = b"""# Engineer

> Acme \xc2\xb7 Zurich, Switzerland (Remote) \xc2\xb7 Full-time \xc2\xb7 Posted 2026-09-25

**Workplace:** remote
**Department:** Platform

## Description

<script>alert('x')</script>

## Requirements

-   Go

## Apply
"""
    assert asdict(project("markdown", body)) == asdict(_parse_markdown_detail(body.decode()))


def test_detail_route_is_explicit(monkeypatch):
    monkeypatch.setenv("WORKABLE_GO_DETAIL_BOARD_IDS", " a, b ")
    assert _runtime_for_scrape("b", "workable", None, None).implementation == "go-workable-detail"
    assert _runtime_for_scrape("c", "workable", None, None) is None
    assert _runtime_for_scrape("b", "json-ld", None, None) is None


@pytest.mark.asyncio
async def test_go_detail_content_and_empty_response(tmp_path: Path):
    common = {"requests": 1, "responses": 1, "bytes": 50, "final_url": API}
    success = GoWorkableDetailRuntime(
        fake_binary(tmp_path, {**common, "status": 200, "content": {"title": "Engineer"}})
    )
    assert (await success.scrape(SOURCE, "workable", None, None)).title == "Engineer"
    blank = GoWorkableDetailRuntime(fake_binary(tmp_path, {**common, "status": 404}))
    assert (await blank.scrape(SOURCE, "workable", None, None)) == JobContent()
    with pytest.raises(ValueError, match="canonical direct"):
        await success.scrape(SOURCE, "workable", {"proxy": True}, None)


@pytest.mark.asyncio
async def test_natural_detail_capture_uses_existing_response(tmp_path: Path, monkeypatch):
    from src.core.scrapers import workable_detail_capture

    monkeypatch.setattr(workable_detail_capture, "_CAPTURE_DIR", tmp_path)
    monkeypatch.setenv("WORKABLE_DETAIL_CAPTURE_JOBS", "acme/ABC123")
    body = b'{"title":"Engineer"}'
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert (await scrape(SOURCE, {}, client)).title == "Engineer"
    artifact = tmp_path / "jobseek-workable-detail-acme-ABC123-api.json"
    assert requests == 1
    assert artifact.read_bytes() == body
    assert stat.S_IMODE(artifact.stat().st_mode) == 0o600
