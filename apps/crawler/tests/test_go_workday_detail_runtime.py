"""Selected Go Workday details preserve the scheduler-facing content and error boundary."""

from __future__ import annotations

import base64
import json

import httpx
import pytest

from src.core.scrapers import workday
from src.processing.scrape import _runtime_for_scrape
from src.runtime.workday_go_detail import GoWorkdayDetailRuntime
from src.shared.tdm import TDMReservedError

SOURCE = "https://tenant.wd5.myworkdayjobs.com/External/job/Engineer/JR001"


def fake_binary(tmp_path, payload: dict, *, exit_code=0):
    path = tmp_path / "workday-detail-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


def test_detail_selector_is_explicit_and_respects_injected_runtime(monkeypatch):
    monkeypatch.setenv("WORKDAY_GO_DETAIL_BOARD_IDS", " first, second ")
    assert (
        _runtime_for_scrape("second", "workday", None, None).implementation == "go-workday-detail"
    )
    assert _runtime_for_scrape("other", "workday", None, None) is None
    assert _runtime_for_scrape("second", "json-ld", None, None) is None
    injected = object()
    assert _runtime_for_scrape("second", "workday", None, injected) is injected
    with pytest.raises(ValueError, match="direct verified TLS"):
        _runtime_for_scrape("second", "workday", {"proxy": True}, None)


def test_natural_capture_retains_one_exact_response_without_extra_request(tmp_path, monkeypatch):
    artifact = tmp_path / "detail.json"
    monkeypatch.setattr(workday, "_DETAIL_CAPTURE_FILE", str(artifact))
    monkeypatch.setenv("WORKDAY_DETAIL_CAPTURE_HOST", "tenant.wd5.myworkdayjobs.com")
    api_url = "https://tenant.wd5.myworkdayjobs.com/wday/cxs/tenant/External/job/Engineer/JR001"
    body = b'{"jobPostingInfo":{"title":"Engineer"}}'
    response = httpx.Response(200, content=body, headers={"content-type": "application/json"})
    workday._capture_natural_detail_response(SOURCE, api_url, response)
    captured = json.loads(artifact.read_text())
    assert base64.b64decode(captured["body_base64"]) == body
    assert captured["source_url"] == SOURCE
    assert artifact.stat().st_mode & 0o777 == 0o600
    workday._capture_natural_detail_response(
        SOURCE, api_url, httpx.Response(200, content=b"different")
    )
    assert base64.b64decode(json.loads(artifact.read_text())["body_base64"]) == body


@pytest.mark.asyncio
async def test_go_detail_content_and_gone(tmp_path):
    content = {
        "title": "Engineer",
        "description": "<p>Work</p>",
        "locations": ["Zurich"],
        "employment_type": "Full-time",
        "job_location_type": "hybrid",
        "date_posted": "2026-09-01",
        "metadata": {"jobReqId": "JR001"},
    }
    common = {"requests": 1, "responses": 1, "transport_errors": 0, "bytes": 200}
    success = GoWorkdayDetailRuntime(
        fake_binary(tmp_path, {**common, "status": 200, "content": content})
    )
    result = await success.scrape(SOURCE, "workday", None, None)
    assert result.title == "Engineer"
    assert result.description == "<p>Work</p>"
    assert result.metadata == {"jobReqId": "JR001"}

    gone = GoWorkdayDetailRuntime(fake_binary(tmp_path, {**common, "status": 403, "gone": True}))
    result = await gone.scrape(SOURCE, "workday", None, None)
    assert result.title is None


@pytest.mark.asyncio
async def test_go_detail_failure_never_falls_back_to_python(tmp_path):
    common = {"requests": 1, "responses": 1, "transport_errors": 0, "bytes": 20}
    reserved = GoWorkdayDetailRuntime(
        fake_binary(
            tmp_path,
            {**common, "error": "tdm-reservation=1", "tdm_policy": "https://example.test/policy"},
            exit_code=1,
        )
    )
    with pytest.raises(TDMReservedError):
        await reserved.scrape(SOURCE, "workday", None, None)

    invalid = GoWorkdayDetailRuntime(
        fake_binary(
            tmp_path,
            {**common, "error": "invalid", "error_kind": "invalid_payload", "status": 200},
            exit_code=1,
        )
    )
    with pytest.raises(RuntimeError, match="Go Workday detail failed"):
        await invalid.scrape(SOURCE, "workday", None, None)
