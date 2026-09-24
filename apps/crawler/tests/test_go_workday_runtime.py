"""The selected Workday origin uses Go exclusively and preserves failure policy."""

from __future__ import annotations

import pytest

from src.runtime.workday_go import GoWorkdayMonitorRuntime
from src.shared.http import mark_external_response, track_request_hosts
from src.shared.tdm import TDMReservedError

BOARD_URL = "https://elevancehealth.wd1.myworkdayjobs.com/en-US/ANT"
CONFIG = {
    "company": "elevancehealth",
    "wd_instance": "wd1",
    "site": "ANT",
    "all_sites": False,
    "scraper_type": "workday",
}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0):
    path = tmp_path / "workday-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        f"print(json.dumps({payload!r}))\n"
        f"sys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


@pytest.mark.asyncio
async def test_selected_origin_returns_go_urls_without_python_fetch(tmp_path):
    urls = [
        "https://elevancehealth.wd1.myworkdayjobs.com/ANT/job_1",
        "https://elevancehealth.wd1.myworkdayjobs.com/ANT/job_2",
    ]
    binary = fake_binary(
        tmp_path,
        {
            "urls": urls,
            "advertised": 2,
            "requests": 1,
            "responses": 1,
            "transport_errors": 0,
            "bytes": 80,
        },
    )
    runtime = GoWorkdayMonitorRuntime(binary)
    results = [result async for result in runtime.stream(BOARD_URL, "workday", CONFIG, None)]
    assert len(results) == 1
    assert results[0].urls == set(urls)


@pytest.mark.asyncio
async def test_go_workday_preserves_tdm_skip(tmp_path):
    binary = fake_binary(
        tmp_path,
        {
            "error": "tdm-reservation=1",
            "tdm_policy": "https://example.org/policy",
            "requests": 1,
            "responses": 1,
            "transport_errors": 0,
            "bytes": 20,
        },
        exit_code=1,
    )
    runtime = GoWorkdayMonitorRuntime(binary)
    with pytest.raises(TDMReservedError) as exc:
        async for _ in runtime.stream(BOARD_URL, "workday", CONFIG, None):
            pass
    assert exc.value.policy_url == "https://example.org/policy"


@pytest.mark.asyncio
async def test_go_workday_rejects_config_expansion_before_request(tmp_path):
    binary = fake_binary(
        tmp_path, {"urls": [], "requests": 0, "responses": 0, "transport_errors": 0, "bytes": 0}
    )
    runtime = GoWorkdayMonitorRuntime(binary)
    with pytest.raises(ValueError, match="unchanged Elevance"):
        async for _ in runtime.stream(BOARD_URL, "workday", {**CONFIG, "proxy": True}, None):
            pass


@pytest.mark.asyncio
async def test_go_workday_rejects_external_output(tmp_path):
    binary = fake_binary(
        tmp_path,
        {
            "urls": ["https://other.example/job"],
            "requests": 1,
            "responses": 1,
            "transport_errors": 0,
            "bytes": 10,
        },
    )
    runtime = GoWorkdayMonitorRuntime(binary)
    with pytest.raises(ValueError, match="outside the selected origin"):
        async for _ in runtime.stream(BOARD_URL, "workday", CONFIG, None):
            pass


def test_go_response_status_feeds_existing_host_circuit():
    with track_request_hosts() as tracker:
        mark_external_response(
            "https://elevancehealth.wd1.myworkdayjobs.com/wday/cxs/elevancehealth/ANT/jobs",
            429,
        )
        assert tracker.transient_failure_host == "elevancehealth.wd1.myworkdayjobs.com"
