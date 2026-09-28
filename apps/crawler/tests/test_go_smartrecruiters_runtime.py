"""Go preserves complete ordinary and localized SmartRecruiters output."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from src.processing.board import _monitor_runtime_for_board
from src.runtime.smartrecruiters_go import (
    GoSmartRecruitersMonitorRuntime,
    eligible,
    percentage_selected,
)
from src.shared.http_retry import PaginationFetchError
from src.shared.tdm import TDMReservedError

MODULE = Path(__file__).resolve().parents[1] / "go/smartrecruiters-monitor"
URL = "https://careers.smartrecruiters.com/Acme"
CONFIG = {"token": "Acme", "scraper_type": "smartrecruiters"}
BOARD_ID = "96ca9888-ad6d-491b-951b-bcb170c6a117"


def fake_binary(tmp_path, payload, exit_code=0):
    path = tmp_path / "fake-smartrecruiters"
    path.write_text(
        "#!/usr/bin/env python3\nimport json,sys\n"
        + "request=json.load(sys.stdin)\nassert request['board_url'].startswith('https://')\n"
        + f"print(json.dumps({payload!r}))\nsys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


def test_go_module_parity_transport_and_cancellation():
    subprocess.run(["go", "test", "-race", "./..."], cwd=MODULE, check=True, capture_output=True)


def test_strict_routes_and_all_configured_identity_modes(monkeypatch):
    monkeypatch.delenv("SMARTRECRUITERS_GO_BOARD_IDS", raising=False)
    monkeypatch.delenv("SMARTRECRUITERS_GO_PERCENT", raising=False)
    assert percentage_selected(BOARD_ID, URL, CONFIG)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "python"
    for mode in [None, "job-v1", "job-location-v1"]:
        cfg = {**CONFIG, **({"canonical_identity": mode} if mode else {})}
        assert eligible(URL, "smartrecruiters", cfg) == "Acme"
    assert (
        eligible(
            URL,
            "smartrecruiters",
            {**CONFIG, "canonical_job_id_url_template": "https://career.hm.com/job/{job_id}/"},
        )
        == "Acme"
    )
    with pytest.raises(ValueError):
        eligible(URL, "smartrecruiters", {**CONFIG, "proxy": True})
    monkeypatch.setenv("SMARTRECRUITERS_GO_BOARD_IDS", BOARD_ID)
    assert _monitor_runtime_for_board(BOARD_ID, None).implementation == "go-smartrecruiters"
    monkeypatch.setenv("SMARTRECRUITERS_GO_PERCENT", "100")
    assert percentage_selected(BOARD_ID, URL, {**CONFIG, "recent_discovered_counts": [24_800]})
    assert percentage_selected(BOARD_ID, URL, CONFIG)
    monkeypatch.setenv("SMARTRECRUITERS_GO_PERCENT", "0")
    assert not percentage_selected(BOARD_ID, URL, CONFIG)


@pytest.mark.asyncio
async def test_url_only_empty_inventory_has_no_new_verified_empty_policy(tmp_path):
    payload = {
        "urls": [],
        "jobs": None,
        "truncated": False,
        "total_found": 0,
        "requests": 1,
        "responses": 1,
        "bytes": 30,
    }
    runtime = GoSmartRecruitersMonitorRuntime(fake_binary(tmp_path, payload), board_id=BOARD_ID)
    results = [r async for r in runtime.stream(URL, "smartrecruiters", CONFIG, None)]
    assert len(results) == 1 and results[0].urls == set()
    assert not results[0].verified_empty_reason


@pytest.mark.asyncio
async def test_all_localized_fields_and_source_identities_reach_writer(tmp_path):
    fixtures = json.loads((MODULE / "testdata/python_inventory.json").read_text())
    for name in [
        "canonical-bilingual-locations-fallback",
        "localized-job-id",
        "localized-template",
    ]:
        case = next(c for c in fixtures if c["name"] == name)
        expected = case["expected"]
        payload = {
            **expected,
            "total_found": sum(
                len(j["metadata"]["smartrecruiters_publication_ids"]) for j in expected["jobs"]
            ),
            "requests": 5,
            "responses": 5,
            "bytes": 1000,
        }
        runtime = GoSmartRecruitersMonitorRuntime(fake_binary(tmp_path, payload), board_id=BOARD_ID)
        results = [
            r
            async for r in runtime.stream(
                case["board_url"], "smartrecruiters", case["metadata"], None
            )
        ]
        assert results[0].urls == set(expected["urls"])
        for job in expected["jobs"]:
            actual = results[0].jobs_by_url[job["url"]]
            assert actual.source_identity == job["source_identity"]
            assert actual.localizations == job["localizations"]
            assert actual.description == job["description"]
            assert actual.base_salary == job["base_salary"]


@pytest.mark.asyncio
async def test_failures_preserve_policy_and_pagination_classes(tmp_path):
    for failure, error in [
        ({"kind": "tdm", "source": "meta", "policy": "https://policy.test/"}, TDMReservedError),
        ({"kind": "pagination", "attempts": 1, "status": 404}, PaginationFetchError),
    ]:
        payload = {
            "requests": 1,
            "responses": 1,
            "bytes": 0,
            "error": "failed",
            "failure": {
                "url": "https://api.smartrecruiters.com/v1/companies/Acme/postings",
                **failure,
            },
        }
        runtime = GoSmartRecruitersMonitorRuntime(
            fake_binary(tmp_path, payload, 1), board_id=BOARD_ID
        )
        with pytest.raises(error):
            _ = [r async for r in runtime.stream(URL, "smartrecruiters", CONFIG, None)]


@pytest.mark.asyncio
async def test_child_output_bound_and_cancellation_reap(tmp_path, monkeypatch):
    import asyncio

    from src.runtime import smartrecruiters_go as bridge

    children = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    path = tmp_path / "child"
    path.write_text(
        "#!/usr/bin/env python3\nimport sys,time\nsys.stdin.read()\n"
        "print('x'*10000,flush=True)\ntime.sleep(60)\n"
    )
    path.chmod(0o755)
    with pytest.raises(ValueError, match="output exceeds bound"):
        await asyncio.wait_for(bridge.run_go(str(path), {}, limit=100), 10)
    assert children[-1].returncode is not None
    path.write_text("#!/usr/bin/env python3\nimport sys,time\nsys.stdin.read()\ntime.sleep(60)\n")
    task = asyncio.create_task(bridge.run_go(str(path), {}))
    async with asyncio.timeout(5):
        while len(children) < 2:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert children[-1].returncode is not None


@pytest.mark.asyncio
async def test_detail_routing_fields_and_http_empty_contract(tmp_path, monkeypatch):
    import dataclasses

    from src.core.job_content import JobContent
    from src.processing.scrape import _runtime_for_scrape
    from src.runtime.smartrecruiters_go_detail import GoSmartRecruitersDetailRuntime

    monkeypatch.delenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS", raising=False)
    assert _runtime_for_scrape(BOARD_ID, "smartrecruiters", {}, None) is None
    monkeypatch.setenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS", BOARD_ID)
    assert (
        _runtime_for_scrape(BOARD_ID, "smartrecruiters", {}, None).implementation
        == "go-smartrecruiters-detail"
    )
    expected = json.loads((MODULE / "testdata/python_detail.json").read_text())[0]["expected"]
    for status in [200, 404, 429, 503]:
        payload = {
            "content": expected if status == 200 else None,
            "requests": 1,
            "responses": 1,
            "bytes": 500 if status == 200 else 0,
            "status": status,
            "final_url": "https://api.smartrecruiters.com/v1/companies/Acme/postings/123",
        }
        path = tmp_path / "detail"
        path.write_text(
            "#!/usr/bin/env python3\nimport json,sys\n"
            "request=json.load(sys.stdin)\nassert request['mode']=='detail'\n"
            + f"assert request['capture_board_id']=={BOARD_ID!r}\n"
            + f"print(json.dumps({payload!r}))\n"
        )
        path.chmod(0o755)
        content = await GoSmartRecruitersDetailRuntime(str(path), board_id=BOARD_ID).scrape(
            "https://jobs.smartrecruiters.com/Acme/123", "smartrecruiters", {}, None, pw=object()
        )
        assert dataclasses.asdict(content) == (
            expected if status == 200 else dataclasses.asdict(JobContent())
        )


def test_default_detail_family_routing_and_reversal(monkeypatch):
    from src.processing.scrape import _runtime_for_scrape
    from src.runtime.smartrecruiters_go_detail import percentage_selected as detail_selected

    url = "https://jobs.smartrecruiters.com/Acme/123-role"
    monkeypatch.delenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS", raising=False)
    monkeypatch.delenv("SMARTRECRUITERS_GO_DETAIL_PERCENT", raising=False)
    assert detail_selected(BOARD_ID, url, None)
    runtime = _runtime_for_scrape(BOARD_ID, "smartrecruiters", None, None, url=url)
    assert runtime.implementation == "go-smartrecruiters-detail"
    assert runtime.board_id == BOARD_ID
    provided = object()
    assert _runtime_for_scrape(BOARD_ID, "smartrecruiters", None, provided, url=url) is provided
    for value in ["0", "-1", "101", "1.5", "01", "invalid"]:
        monkeypatch.setenv("SMARTRECRUITERS_GO_DETAIL_PERCENT", value)
        assert _runtime_for_scrape(BOARD_ID, "smartrecruiters", None, None, url=url) is None
    monkeypatch.setenv("SMARTRECRUITERS_GO_DETAIL_BOARD_IDS", BOARD_ID)
    assert (
        _runtime_for_scrape(BOARD_ID, "smartrecruiters", None, None, url=url).implementation
        == "go-smartrecruiters-detail"
    )


def test_detail_identity_admission_matches_python_parser(monkeypatch):
    from src.core.scrapers.smartrecruiters import _parse_job_url
    from src.runtime.smartrecruiters_go_detail import eligible as detail_eligible
    from src.runtime.smartrecruiters_go_detail import percentage_selected as detail_selected

    monkeypatch.delenv("SMARTRECRUITERS_GO_DETAIL_PERCENT", raising=False)
    for path in [
        "/Acme/123-role",
        "/Acme/123-rôle/",
        "/oneclick-ui/company/Acme/publication/123",
        "/oneclick-ui/company/Acme/job/123",
    ]:
        url = "https://jobs.smartrecruiters.com" + path + "?source=normal"
        assert detail_eligible(url, "smartrecruiters", None) == _parse_job_url(url)
        assert detail_selected(BOARD_ID, url, None)
    for url in [
        "http://jobs.smartrecruiters.com/Acme/123",
        "https://jobs.smartrecruiters.com@evil.test/Acme/123",
        "https://jobs.smartrecruiters.com:443/Acme/123",
        "https://jobs.smartrecruiters.com/Acme/123/extra",
        "https://jobs.smartrecruiters.com/Acme/123%2Drole",
        "https://jobs.smartrecruiters.com/Acme/123;other",
        "https://jobs.smartrecruiters.com/Acme/123?u=https://jobs.smartrecruiters.com/oneclick-ui/company/Other/job/456",
    ]:
        assert not detail_selected(BOARD_ID, url, None)
    url = "https://jobs.smartrecruiters.com/Acme/123"
    for config in [{"proxy": True}, {"render": True}, {"enrich": {}}, {"default": {}}]:
        assert not detail_selected(BOARD_ID, url, config)
