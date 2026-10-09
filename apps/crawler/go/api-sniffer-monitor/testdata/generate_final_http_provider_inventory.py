"""Freeze complete inventories from the original four provider monitors."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitor import MonitorResult  # noqa: E402
from src.core.monitors import fenbi, nowhiring, paynet, wecruit  # noqa: E402
from src.shared.http_retry import PaginationFetchError  # noqa: E402

core = json.loads(Path(__file__).with_name("python_final_http_provider_core.json").read_text())
cases = []


def sample(provider, name, key):
    return copy.deepcopy(
        next(c["inputs"][key] for c in core if c["provider"] == provider and c["name"] == name)
    )


async def collect(provider, mode):
    module = dict(paynet=paynet, nowhiring=nowhiring, fenbi=fenbi, wecruit=wecruit)[provider]
    suite = "1234567890abcdef12345678"
    boards = dict(
        paynet=dict(
            board_url="https://www.pay-netonline.com/PayNet/Applicant/Postings.aspx?Co=Example",
            metadata={},
        ),
        nowhiring=dict(board_url="https://nowhiring.com/example/", metadata=dict(slug="example")),
        fenbi=dict(board_url="https://www.fenbi.com/page/joinus", metadata=dict(kind="fulltime")),
        wecruit=dict(
            board_url=f"https://wecruit.hotjob.cn/SU{suite}/pb/index.html#/",
            metadata=dict(
                api_origin="https://wecruit.hotjob.cn", suite_key=suite, recruit_types=[1]
            ),
        ),
    )
    board = boards[provider]
    exchanges = []
    listing_count = 0

    def serve(request):
        nonlocal listing_count
        path = request.url.path
        form = parse_qs(request.content.decode()) if provider == "wecruit" else {}
        if provider == "paynet":
            row = sample("paynet", "rich", "row")
            payload = [row]
            if mode == "empty":
                payload = []
            elif mode == "duplicate":
                payload.append(copy.deepcopy(row))
            elif mode == "mixed-invalid":
                payload.append(None)
            elif mode == "all-invalid":
                payload = [None]
            elif mode == "wrong-shape":
                payload = {}
        elif provider == "nowhiring":
            if "/career-live-sites/" in path:
                payload = dict(
                    jobSearchCriteria=[
                        dict(fieldName="billingAccountId", fieldValue="123"),
                        dict(fieldName="brandId", fieldValue="brand"),
                    ]
                )
                if mode == "missing-scope":
                    payload = {}
            elif path == "/api/jobs/search":
                start = int(json.loads(request.content)["start"])
                payload = dict(list=[dict(id="job-1")], total=1)
                if mode == "empty":
                    payload = dict(list=[], total=0)
                elif mode == "duplicate":
                    payload = dict(list=[dict(id="job-1"), dict(id="job-1")], total=2)
                elif mode == "gap":
                    payload = dict(list=[] if start else [dict(id="job-1")], total=2)
                elif mode == "paged":
                    payload = dict(list=[dict(id=f"job-{start + 1}")], total=3)
                elif mode == "invalid-id":
                    payload = dict(list=[dict(id="path/escape")], total=1)
                elif mode == "boolean-total":
                    payload["total"] = True
                elif mode == "float-total":
                    payload["total"] = 1.0
            else:
                payload = sample("nowhiring", "rich", "row")
                payload["id"] = path.rsplit("/", 1)[-1]
                if mode == "invalid-detail":
                    payload["jobTitle"] = " "
        elif provider == "fenbi":
            if path.startswith("/page/"):
                payload = '<script src="https://nodestatic.fbstatic.cn/weblts_spa_online/page/main-ABC123.js"></script>'
                if mode == "missing-bundle":
                    payload = "<html></html>"
                elif mode == "duplicate-bundle":
                    payload *= 2
            else:
                row = sample("fenbi", "fulltime", "payload")["fulltime"][0]
                rows = [row]
                if mode == "empty":
                    rows = []
                elif mode == "duplicate":
                    rows.append(copy.deepcopy(row))
                elif mode == "invalid-detail":
                    row["title"] = " "
                payload = (
                    "this.joinUsArr="
                    + json.dumps(
                        dict(fulltime=rows, parttime=rows),
                        ensure_ascii=False,
                        separators=(",", ":"),
                    )
                    + ";"
                )
                if mode == "duplicate-marker":
                    payload *= 2
        else:
            if "/listPositionDetail/" in path:
                payload = sample("wecruit", "rich", "detail")
                payload.update(postId=form["postId"][0], recruitType=1)
                if mode == "wrong-detail-id":
                    payload["postId"] = "0" * 24
                elif mode == "wrong-detail-lane":
                    payload["recruitType"] = 2
                elif mode == "invalid-detail":
                    payload["workContent"] = payload["serviceCondition"] = ""
                payload = dict(state=200, data=payload)
            else:
                listing_count += 1
                page = int(form["currentPage"][0])
                count = 3 if mode in {"paged", "changed-total"} else 1
                size = 2 if count == 3 else 50
                rows = [
                    dict(
                        postId=f"{i + 1:024x}",
                        recruitType=1,
                        currentSuiteKey=suite,
                        postName="Fallback",
                        publishDate="2026-10-09",
                    )
                    for i in range((page - 1) * size, min(count, page * size))
                ]
                if mode == "empty":
                    count, rows = 0, []
                elif mode == "duplicate":
                    rows.append(copy.deepcopy(rows[0]))
                    count = 2
                elif mode == "wrong-suite":
                    rows[0]["currentSuiteKey"] = "0" * 24
                elif mode == "wrong-lane":
                    rows[0]["recruitType"] = 2
                elif mode == "missing-row":
                    rows = []
                if mode == "changed-total" and page == 2 and listing_count == 2:
                    count = 4
                pagination = dict(
                    pageData=rows,
                    totalPage=(count + size - 1) // size,
                    pageSize=size,
                    currentPage=page,
                    dataCount=count,
                )
                if mode == "empty":
                    pagination.update(pageSize=0, currentPage=0)
                if mode == "float-zero":
                    count = 0
                    pagination = dict(
                        pageData=[], totalPage=0.0, pageSize=0.0, currentPage=False, dataCount=False
                    )
                payload = dict(state="200", data=dict(pageForm=pagination, positonNum=count))
        body = (
            payload
            if isinstance(payload, str)
            else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        )
        exchanges.append(
            dict(
                url=str(request.url),
                method=request.method,
                request_body=request.content.decode(),
                headers={
                    k: request.headers[k]
                    for k in ("accept", "referer", "content-type")
                    if k in request.headers
                },
                body=body,
            )
        )
        return httpx.Response(
            200,
            text=body,
            headers={
                "content-type": "text/html; charset=utf-8"
                if provider == "fenbi"
                else "application/json"
            },
        )

    async def no_sleep(_):
        return None

    case = dict(provider=provider, mode=mode, board=board, exchanges=exchanges)
    with patch.object(wecruit.asyncio, "sleep", no_sleep):
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            try:
                result = await module.discover(board, client)
                jobs = (
                    list((result.jobs_by_url or {}).values())
                    if isinstance(result, MonitorResult)
                    else result
                )
                case.update(
                    jobs=[{k: v for k, v in asdict(job).items() if v is not None} for job in jobs],
                    truncated=isinstance(result, MonitorResult) and result.truncated,
                )
            except (ValueError, TypeError, httpx.HTTPError, PaginationFetchError) as exc:
                case.update(error=True, original_error=type(exc).__name__)
    cases.append(case)


async def main():
    modes = dict(
        paynet=["rich", "empty", "duplicate", "mixed-invalid", "all-invalid", "wrong-shape"],
        nowhiring=[
            "rich",
            "empty",
            "duplicate",
            "gap",
            "paged",
            "invalid-id",
            "boolean-total",
            "float-total",
            "missing-scope",
            "invalid-detail",
        ],
        fenbi=[
            "rich",
            "empty",
            "duplicate",
            "invalid-detail",
            "missing-bundle",
            "duplicate-bundle",
            "duplicate-marker",
        ],
        wecruit=[
            "rich",
            "empty",
            "paged",
            "changed-total",
            "duplicate",
            "wrong-suite",
            "wrong-lane",
            "missing-row",
            "wrong-detail-id",
            "wrong-detail-lane",
            "invalid-detail",
            "float-zero",
        ],
    )
    for provider, provider_modes in modes.items():
        for mode in provider_modes:
            await collect(provider, mode)


if __name__ == "__main__":
    asyncio.run(main())
    Path(__file__).with_name("python_final_http_provider_inventory.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(f"Frozen {len(cases)} complete original provider inventories")
