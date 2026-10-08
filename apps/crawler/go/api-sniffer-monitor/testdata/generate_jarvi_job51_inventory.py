"""Freeze complete original inventories; transport policy stays a separate test."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import jarvi, job51

cases = []
core = json.loads(Path(__file__).with_name("python_jarvi_job51_core.json").read_text())
jarvi_row = next(
    c["inputs"]["row"] for c in core if c["provider"] == "jarvi" and c["name"] == "rich"
)
job51_row = next(
    c["inputs"]["row"] for c in core if c["provider"] == "job51" and c["name"] == "rich"
)


def output(result):
    truncated = isinstance(result, MonitorResult) and result.truncated
    jobs = (
        list((result.jobs_by_url or {}).values()) if isinstance(result, MonitorResult) else result
    )
    return [{k: v for k, v in asdict(job).items() if v is not None} for job in jobs], truncated


async def collect_jarvi(mode):
    rows = [copy.deepcopy(jarvi_row)]
    total = 1
    if mode == "empty":
        rows = []
        total = 0
    elif mode == "invalid-row":
        rows = ["invalid", None, 42]
    elif mode == "skip-title":
        rows[0]["fieldsValues"] = []
        rows[0]["name"] = ""
    elif mode == "advertised-gap":
        total = 2
    elif mode == "string-total":
        total = "2"
    elif mode == "float-total":
        total = 2.0
    elif mode == "boolean-total":
        rows = []
        total = True
    elif mode == "huge-total":
        total = 10**25
    payload = dict(data=rows, total=total)
    if mode == "missing-data":
        payload = dict(total=1)
    board = dict(
        board_url="https://fixture.invalid/careers",
        metadata=dict(public_api_key="public_fixture_key", currency="CHF"),
    )
    requests = []

    def serve(request):
        requests.append(
            dict(
                url=str(request.url),
                body=json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
                key=request.headers.get("x-api-key"),
            )
        )
        return httpx.Response(200, json=payload)

    async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
        try:
            jobs, truncated = output(await jarvi.discover(board, client))
            cases.append(
                dict(
                    provider="jarvi",
                    mode=mode,
                    board=board,
                    exchanges=requests,
                    jobs=jobs,
                    truncated=truncated,
                )
            )
        except (ValueError, TypeError):
            cases.append(
                dict(provider="jarvi", mode=mode, board=board, exchanges=requests, error=True)
            )


async def collect_job51(mode):
    total = 23 if mode in {"paged", "changed-total", "later-short"} else 2
    if mode == "empty":
        total = 0
    board = dict(board_url="https://campus.51job.com/fixture/job.html", metadata=dict(ctmid=12345))
    requests = []

    async def fetch(endpoint, params, client):
        if endpoint == "job_list.php":
            page = params["pagenum"]
            count = min(20, max(0, total - (page - 1) * 20))
            rows = [dict(jobid=str((page - 1) * 20 + i + 1)) for i in range(count)]
            reported = total
            if mode == "duplicate-id" and rows:
                rows[-1]["jobid"] = rows[0]["jobid"]
            elif mode == "invalid-id" and rows:
                rows[0]["jobid"] = "bad"
            elif mode == "changed-total" and page == 2:
                reported = total + 1
            elif mode == "later-short" and page == 2 or mode == "first-short":
                rows = rows[:-1]
            result = dict(totalnum=str(reported), joblist=rows)
        else:
            result = copy.deepcopy(job51_row)
            result["jobid"] = params["jobid"]
            if mode == "wrong-employer":
                result["ctmid"] = "12346"
            elif mode == "wrong-detail-id":
                result["jobid"] = "999"
            elif mode == "missing-description":
                result["jobinfo"] = "<script>private</script>"
        requests.append(
            dict(
                url=job51._signed_url(endpoint, params),
                body="jsoncallback("
                + json.dumps(
                    dict(status="1", resultbody=result), ensure_ascii=False, separators=(",", ":")
                )
                + ")",
            )
        )
        return result

    async with httpx.AsyncClient() as client:
        with patch.object(job51, "_fetch_jsonp", fetch):
            try:
                jobs, truncated = output(await job51.discover(board, client))
                cases.append(
                    dict(
                        provider="job51",
                        mode=mode,
                        board=board,
                        exchanges=requests,
                        jobs=jobs,
                        truncated=truncated,
                    )
                )
            except (ValueError, TypeError):
                cases.append(
                    dict(provider="job51", mode=mode, board=board, exchanges=requests, error=True)
                )


async def main():
    for mode in [
        "rich",
        "empty",
        "invalid-row",
        "skip-title",
        "advertised-gap",
        "string-total",
        "float-total",
        "boolean-total",
        "huge-total",
        "missing-data",
    ]:
        await collect_jarvi(mode)
    for mode in [
        "rich",
        "empty",
        "paged",
        "duplicate-id",
        "invalid-id",
        "changed-total",
        "later-short",
        "first-short",
        "wrong-employer",
        "wrong-detail-id",
        "missing-description",
    ]:
        await collect_job51(mode)


asyncio.run(main())
Path(__file__).with_name("python_jarvi_job51_inventory.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(cases)} actual Python inventory cases")
