"""Native JOIN detail routing, result contract, and child lifecycle."""

from __future__ import annotations

import asyncio
import dataclasses

import pytest

from src.core.job_content import JobContent
from src.processing.scrape import _runtime_for_scrape
from src.runtime import join_go_detail
from src.runtime.join_go_detail import GoJoinDetailRuntime, percentage_selected
from src.shared.tdm import TDMReservedError

URL = "https://join.com/companies/acme/1-engineer"
CONFIG = {"path": "props.pageProps.initialState.job", "fields": {"title": "title"}}


def test_detail_route_preserves_fallback_and_provided_owner(monkeypatch):
    monkeypatch.delenv("JOIN_GO_DETAIL_PERCENT", raising=False)
    for config in [CONFIG, None]:
        assert (
            _runtime_for_scrape("board", "nextdata", config, None, url=URL).implementation
            == "go-join-nextdata-detail"
        )
    assert _runtime_for_scrape("board", "jsonld", None, None, url=URL) is None
    assert (
        _runtime_for_scrape("board", "nextdata", CONFIG, None, url="https://other.example/job/1")
        is None
    )
    provided = object()
    assert _runtime_for_scrape("board", "nextdata", CONFIG, provided, url=URL) is provided
    for config in [
        {**CONFIG, "render": True},
        {**CONFIG, "proxy": True},
        {**CONFIG, "fields": {"title": "other"}},
    ]:
        assert not percentage_selected("board", URL, config)
    for value in ["0", "01", "-1", "101"]:
        monkeypatch.setenv("JOIN_GO_DETAIL_PERCENT", value)
        assert not percentage_selected("board", URL, CONFIG)


def fake_binary(tmp_path, payload, exit_code=0):
    path = tmp_path / "join-fake"
    path.write_text(
        "#!/usr/bin/env python3\nimport json,sys\nassert sys.argv[1:]==['--detail']\n"
        "request=json.load(sys.stdin)\n"
        f"assert request['url']=={URL!r}\nprint(json.dumps({payload!r}))\nsys.exit({exit_code})\n"
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
@pytest.mark.parametrize("status", [200, 404, 410, 429, 503])
async def test_detail_empty_non200_is_not_tombstone_or_retry(tmp_path, status):
    content = JobContent(title="Go") if status == 200 else JobContent()
    runtime = GoJoinDetailRuntime(
        fake_binary(tmp_path, payload(status=status, content=dataclasses.asdict(content)))
    )
    assert await runtime.scrape(URL, "nextdata", CONFIG, None, pw=object()) == content


@pytest.mark.asyncio
async def test_no_fields_zero_requests(tmp_path):
    runtime = GoJoinDetailRuntime(
        fake_binary(
            tmp_path,
            payload(
                requests=0,
                responses=0,
                bytes=0,
                status=0,
                final_url="",
                content=dataclasses.asdict(JobContent()),
            ),
        )
    )
    assert await runtime.scrape(URL, "nextdata", None, None) == JobContent()


@pytest.mark.asyncio
@pytest.mark.parametrize("source", ["header", "meta"])
async def test_detail_typed_policy_source_and_companion(tmp_path, source):
    runtime = GoJoinDetailRuntime(
        fake_binary(
            tmp_path,
            payload(
                error="tdm-reservation=1",
                error_kind="tdm",
                tdm_source=source,
                tdm_policy="https://policy.example/",
                content=dataclasses.asdict(JobContent()),
            ),
            exit_code=1,
        )
    )
    with pytest.raises(TDMReservedError) as exc:
        await runtime.scrape(URL, "nextdata", CONFIG, None)
    assert (exc.value.source, exc.value.policy_url) == (source, "https://policy.example/")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "changes",
    [
        {"requests": 22},
        {"responses": 2},
        {"bytes": 17 << 20},
        {"content": {"title": "Go"}},
        {"final_url": "http://join.com/unsafe"},
    ],
)
async def test_invalid_child_contract_fails_without_python_fetch(tmp_path, changes):
    runtime = GoJoinDetailRuntime(fake_binary(tmp_path, payload(**changes)))
    with pytest.raises(ValueError):
        await runtime.scrape(URL, "nextdata", CONFIG, None)


@pytest.mark.asyncio
async def test_output_overflow_and_cancellation_reap(tmp_path, monkeypatch):
    children = []
    create = asyncio.create_subprocess_exec

    async def capture(*args, **kwargs):
        proc = await create(*args, **kwargs)
        children.append(proc)
        return proc

    monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
    monkeypatch.setattr(join_go_detail, "_MAX_OUTPUT", 100)
    path = tmp_path / "child"
    path.write_text(
        "#!/usr/bin/env python3\nimport time,sys\nsys.stdin.read()\n"
        "print('x'*200,flush=True)\ntime.sleep(60)\n"
    )
    path.chmod(0o755)
    runtime = GoJoinDetailRuntime(str(path))
    with pytest.raises(ValueError, match="output exceeds"):
        await asyncio.wait_for(runtime.scrape(URL, "nextdata", CONFIG, None), 10)
    assert children[-1].returncode is not None
    path.write_text("#!/usr/bin/env python3\nimport time,sys\nsys.stdin.read()\ntime.sleep(60)\n")
    task = asyncio.create_task(runtime.scrape(URL, "nextdata", CONFIG, None))
    async with asyncio.timeout(5):
        while len(children) < 2:
            await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await asyncio.wait_for(task, 10)
    assert children[-1].returncode is not None
