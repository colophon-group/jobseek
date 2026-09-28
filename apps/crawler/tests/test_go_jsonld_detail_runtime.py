"""JSON-LD route, failure classification and bounded native process ownership."""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.job_content import JobContent
from src.processing.scrape import (
    _is_budget_eligible_failure,
    _is_permanent_gone,
    _runtime_for_scrape,
)
from src.runtime import jsonld_go_detail
from src.runtime.jsonld_go_detail import GoJsonLdDetailRuntime, percentage_selected
from src.shared.tdm import TDMReservedError

URL = "https://example.com/jobs/1"


def test_route_preserves_provided_owner_and_transport_obligations(monkeypatch):
    monkeypatch.delenv("JSONLD_GO_DETAIL_PERCENT", raising=False)
    assert (
        _runtime_for_scrape("board", "json-ld", None, None, url=URL).implementation
        == "go-jsonld-detail"
    )
    assert percentage_selected("board", URL + "#JobEntry", None)
    provided = object()
    assert _runtime_for_scrape("board", "json-ld", None, provided, url=URL) is provided
    for config in [
        {"render": True},
        {"proxy": True},
        {"skip_ssl": True},
        {"workday_fallback": {}},
        {"unknown": True},
        {"transport_attempts": True},
        {"defaults_by_url": {42: {}}},
    ]:
        assert not percentage_selected("board", URL, config)
    for value in ["0", "01", "-1", "101"]:
        monkeypatch.setenv("JSONLD_GO_DETAIL_PERCENT", value)
        assert not percentage_selected("board", URL, None)


def fake_binary(tmp_path, payload, exit_code=0):
    path = tmp_path / "jsonld-fake"
    path.write_text(
        "#!/usr/bin/env python3\nimport json,sys\n"
        "assert sys.argv[1:]==[]\nrequest=json.load(sys.stdin)\n"
        + f"assert request['url']=={URL!r}\nprint(json.dumps({payload!r}))\nsys.exit({exit_code})\n"
    )
    path.chmod(0o755)
    return str(path)


def payload(**changes):
    return {
        "requests": 1,
        "responses": 1,
        "bytes": 200,
        "status": 200,
        "final_url": URL,
        "content": dataclasses.asdict(JobContent(title="Go")),
        **changes,
    }


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "status,permanent,budget",
    [
        (403, False, False),
        (404, True, False),
        (410, True, False),
        (422, False, True),
        (429, False, False),
        (503, False, False),
    ],
)
async def test_status_keeps_database_delisting_authority(tmp_path, status, permanent, budget):
    http = AsyncMock()
    runtime = GoJsonLdDetailRuntime(
        fake_binary(tmp_path, payload(status=status, error="status", error_kind="status"), 1)
    )
    with pytest.raises(httpx.HTTPStatusError) as exc:
        await runtime.scrape(URL, "json-ld", None, http)
    assert _is_permanent_gone(exc.value) is permanent
    assert _is_budget_eligible_failure(exc.value) is budget
    assert http.mock_calls == []


@pytest.mark.asyncio
async def test_success_keeps_all_rich_fields(tmp_path):
    content = JobContent(
        title="Go",
        description="<p>Build</p>",
        locations=["Paris"],
        employment_type="FULL_TIME",
        base_salary={"currency": "EUR", "min": 10},
        extras={"skills": ["Go"]},
    )
    runtime = GoJsonLdDetailRuntime(
        fake_binary(tmp_path, payload(content=dataclasses.asdict(content)))
    )
    assert await runtime.scrape(URL, "json-ld", None, None, pw=object()) == content


@pytest.mark.asyncio
async def test_policy_is_typed_and_no_python_fetch(tmp_path):
    runtime = GoJsonLdDetailRuntime(
        fake_binary(
            tmp_path,
            payload(
                error="tdm",
                error_kind="tdm",
                tdm_source="meta",
                tdm_policy="https://example.com/license",
            ),
            1,
        )
    )
    with pytest.raises(TDMReservedError) as exc:
        await runtime.scrape(URL, "json-ld", None, None)
    assert exc.value.source == "meta"
    assert exc.value.policy_url == "https://example.com/license"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"requests": 337},
        {"responses": 2},
        {"bytes": 17 << 20},
        {"content": {"title": "Go"}},
        {"final_url": "http://user:pass@example.com/"},
        {"error_kind": "gone"},
    ],
)
async def test_child_contract_rejects_invalid_payload(tmp_path, changes):
    runtime = GoJsonLdDetailRuntime(fake_binary(tmp_path, payload(**changes)))
    with pytest.raises(ValueError):
        await runtime.scrape(URL, "json-ld", None, None)


@pytest.mark.asyncio
async def test_offline_parser_mode_sends_html_and_applies_exact_defaults(tmp_path):
    path = tmp_path / "parser-fake"
    expected = dataclasses.asdict(JobContent(title="Go", locations=["London"]))
    path.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nassert sys.argv[1:]==['--parse']\n"
        "request=json.load(sys.stdin)\nassert request['html']=='retained html'\n"
        "assert request['config']['defaults_by_url']\n" + f"print(json.dumps({expected!r}))\n"
    )
    path.chmod(0o755)
    content = await jsonld_go_detail.parse_rendered_html(
        URL,
        {"defaults_by_url": {URL: {"locations": ["London"]}}},
        "retained html",
        binary=str(path),
    )
    assert dataclasses.asdict(content) == expected


@pytest.mark.asyncio
async def test_child_overflow_and_cancellation_reap(tmp_path, monkeypatch):
    children = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(jsonld_go_detail, "_MAX_OUTPUT", 100)
    path = tmp_path / "child"
    path.write_text(
        "#!/usr/bin/env python3\nimport sys,time\nsys.stdin.read()\n"
        "print('x'*200,flush=True)\ntime.sleep(60)\n"
    )
    path.chmod(0o755)
    with pytest.raises(ValueError, match="output exceeds"):
        await asyncio.wait_for(jsonld_go_detail.run_child(str(path), {}), 10)
    assert children[-1].returncode is not None
    path.write_text("#!/usr/bin/env python3\nimport sys,time\nsys.stdin.read()\ntime.sleep(60)\n")
    task = asyncio.create_task(
        jsonld_go_detail.run_child(str(path), {"html": "x" * (1 << 20)}, parse=True)
    )
    async with asyncio.timeout(5):
        while len(children) < 2:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert children[-1].returncode is not None
