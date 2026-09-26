"""The selected Workday origin uses Go exclusively and preserves failure policy."""

from __future__ import annotations

import pytest

from src.processing.board import _monitor_runtime_for_board
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
    "suspect_streak": 0,
    "recent_discovered_counts": [301, 301, 301, 301, 300],
    "_monitor_config_fingerprint": (
        "2b4ae021c5e69c4c9544ad046330ee9116eed11cb1b5e7064fc800d5013c1e4b"
    ),
    "_confirmed_drop_candidate": None,
}


def fake_binary(tmp_path, payload: dict, *, exit_code: int = 0, expected_args=None):
    path = tmp_path / "workday-live-fake"
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, sys\n"
        + (f"assert sys.argv[1:] == {expected_args!r}\n" if expected_args is not None else "")
        + f"print(json.dumps({payload!r}))\n"
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
    with pytest.raises(ValueError, match="unchanged direct, single-site"):
        async for _ in runtime.stream(BOARD_URL, "workday", {**CONFIG, "proxy": True}, None):
            pass


@pytest.mark.asyncio
async def test_second_single_site_uses_its_own_identity_and_url_boundary(tmp_path):
    board_id = "7df42688-8abb-49eb-b897-e15dcb09d2a4"
    url = "https://freseniusglobal.wd3.myworkdayjobs.com/FK_Careers"
    job = "https://freseniusglobal.wd3.myworkdayjobs.com/FK_Careers/job_1"
    config = {
        "company": "freseniusglobal",
        "wd_instance": "wd3",
        "site": "FK_Careers",
        "all_sites": False,
        "scraper_type": "workday",
    }
    binary = fake_binary(
        tmp_path,
        {"urls": [job], "requests": 1, "responses": 1, "transport_errors": 0, "bytes": 42},
        expected_args=[
            "--company",
            "freseniusglobal",
            "--instance",
            "wd3",
            "--site",
            "FK_Careers",
        ],
    )
    runtime = GoWorkdayMonitorRuntime(binary, board_id=board_id)
    results = [result async for result in runtime.stream(url, "workday", config, None)]
    assert results[0].urls == {job}

    with pytest.raises(ValueError, match="unchanged direct, single-site"):
        async for _ in runtime.stream(
            url,
            "workday",
            {**config, "site": "Other_Careers"},
            None,
        ):
            pass


def test_multiple_selected_boards_are_exclusive(monkeypatch):
    other = "7df42688-8abb-49eb-b897-e15dcb09d2a4"
    monkeypatch.setenv("WORKDAY_GO_BOARD_ID", "bcd90676-101c-4e58-b427-98edd4e09b7d")
    monkeypatch.setenv("WORKDAY_GO_BOARD_IDS", f" {other},")
    assert _monitor_runtime_for_board(other, None).implementation == "go-workday"
    assert _monitor_runtime_for_board("not-selected", None).implementation == "python"


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
