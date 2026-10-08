"""Freeze the original three public API monitor inventories and requests."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import cnstaff, jobbank104, seamlesshiring

cases = []
boards = {
    "cnstaff": {"board_url": "https://tenant.cnstaff.com/recruit", "metadata": {}},
    "jobbank104": {"board_url": "https://www.104.com.tw/company/abcde", "metadata": {}},
    "seamlesshiring": {"board_url": "https://tenant.seamlesshiring.com/", "metadata": {}},
}
modules = {"cnstaff": cnstaff, "jobbank104": jobbank104, "seamlesshiring": seamlesshiring}


def row(provider, index):
    if provider == "cnstaff":
        return dict(
            job_id=str(index),
            job_name_show=" 工程師 ",
            job_detail="<div>建造<br>系統</div>",
            job_desc2="<p>有經驗</p>",
            job_address_name=" 北京 ",
            job_published_at="2026-10-08 10:00:00",
            job_end_at="2027-01-01",
            company_orgnize_name_show=" 企業 ",
            ws_system_job_type_ids_name=" 平台 ",
            g_job_type=" 技術 ",
        )
    if provider == "jobbank104":
        return dict(
            jobNo=f"a{index:04d}",
            jobUrl=f"https://www.104.com.tw/job/a{index:04d}?jobsource=company",
            jobName=" 工程師 ",
            jobAddrNoDesc=" 台北 ",
            jobDescription=" Build & deploy 'things' \"well\".\nNext\n\nParagraph <text> ",
        )
    return dict(
        id=index,
        title=" Engineer ",
        summary="<p>Build</p>",
        details="<div>Ship</div>",
        location=" Lagos ",
        job_type="Full time",
        work_style="Hybrid",
        post_date="2026-10-08",
        expiry_date="2027-01-01",
        position="Platform",
    )


def page(provider, rows, total=None, current=1, size=None, next_page=False):
    total = len(rows) if total is None else total
    if provider == "cnstaff":
        return dict(total=total, page=dict(now=current, total=(total + 14) // 15), list=rows)
    if provider == "jobbank104":
        size = size or 100
        return dict(
            data=dict(
                totalCount=total,
                totalPages=(total + size - 1) // size,
                page=current,
                pageSize=size,
                list=dict(normalJobs=rows),
            )
        )
    return dict(
        data=dict(
            jobs=dict(total=total, data=rows, next_page_url="opaque-signal" if next_page else None)
        )
    )


async def add(provider, name, pages, statuses=None):
    requests = []
    calls = 0

    async def handler(request):
        nonlocal calls
        requests.append(
            dict(
                method=request.method,
                url=str(request.url),
                headers={
                    key: value
                    for key, value in request.headers.items()
                    if key in {"accept", "referer", "x-requested-with"}
                },
            )
        )
        value = pages[min(calls, len(pages) - 1)]
        calls += 1
        status = statuses[min(calls - 1, len(statuses) - 1)] if statuses else 200
        if status == 0:
            raise httpx.ConnectError("original test transport failure", request=request)
        return httpx.Response(status, json=value, request=request)

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await modules[provider].discover(copy.deepcopy(boards[provider]), client)
        jobs = (
            list((result.jobs_by_url or {}).values())
            if isinstance(result, MonitorResult)
            else result
        )
        expected = dict(
            jobs=[asdict(job) for job in jobs], truncated=getattr(result, "truncated", False)
        )
        error = False
    except Exception:
        expected, error = None, True
    cases.append(
        dict(
            provider=provider,
            name=name,
            board=boards[provider],
            pages=pages,
            requests=requests,
            expected=expected,
            error=error,
            statuses=statuses,
        )
    )


async def main():
    for provider in modules:
        await add(provider, "populated", [page(provider, [row(provider, 1)])])
        await add(provider, "empty-authoritative", [page(provider, [])])
        await add(provider, "non-object", [[]])
        await add(provider, "bool-total", [page(provider, [], total=True)])
        await add(provider, "float-total", [page(provider, [], total=1.0)])
        await add(provider, "advertised-gap", [page(provider, [], total=1)])
        await add(
            provider, "duplicate-identity", [page(provider, [row(provider, 1), row(provider, 1)])]
        )
        await add(provider, "malformed-row", [page(provider, [None])])
        await add(provider, "missing-id", [page(provider, [{}])])
        if provider == "cnstaff":
            await add(
                provider,
                "two-pages",
                [
                    page(provider, [row(provider, i) for i in range(1, 16)], total=16),
                    page(provider, [row(provider, 16)], total=16, current=2),
                ],
            )
            await add(
                provider,
                "changed-count",
                [
                    page(provider, [row(provider, i) for i in range(1, 16)], total=16),
                    page(provider, [row(provider, 16), row(provider, 17)], total=17, current=2),
                ],
            )
            for key, value in [
                ("job_detail", ""),
                ("job_desc2", None),
                ("job_id", 1),
                ("job_published_at", "0000-00-00"),
                ("job_end_at", "nonsense"),
                ("job_name_show", None),
            ]:
                changed = row(provider, 1)
                changed[key] = value
                await add(provider, f"field-{key}", [page(provider, [changed])])
        elif provider == "jobbank104":
            await add(
                provider,
                "two-pages",
                [
                    page(provider, [row(provider, 1), row(provider, 2)], total=3, size=2),
                    page(provider, [row(provider, 3)], total=3, size=2, current=2),
                ],
            )
            await add(provider, "wrong-page", [page(provider, [row(provider, 1)], current=2)])
            for key, value in [
                ("jobNo", "bad"),
                ("jobUrl", "https://evil.example/job/a0001"),
                ("jobName", ""),
                ("jobDescription", ""),
                ("jobAddrNoDesc", None),
            ]:
                changed = row(provider, 1)
                changed[key] = value
                await add(provider, f"field-{key}", [page(provider, [changed])])
        else:
            await add(
                provider,
                "two-pages",
                [
                    page(provider, [row(provider, 1)], total=2, next_page=True),
                    page(provider, [row(provider, 2)], total=2),
                ],
            )
            await add(
                provider,
                "changed-count",
                [
                    page(provider, [row(provider, 1)], total=2, next_page=True),
                    page(provider, [row(provider, 2)], total=3),
                ],
            )
            await add(provider, "no-progress", [page(provider, [], total=2, next_page=True)])
            for key, value in [
                ("summary", False),
                ("details", 0),
                ("summary", [" ordered ", 2]),
                ("location", None),
                ("work_style", "unknown"),
                ("id", 0),
            ]:
                changed = row(provider, 1)
                changed[key] = value
                await add(
                    provider, f"field-{key}-{type(value).__name__}", [page(provider, [changed])]
                )
    for provider in modules:
        for status in [201, 202, 401, 403, 404, 410, 408, 429, 503, 999, 0]:
            await add(
                provider,
                f"http-{status}-then-success",
                [page(provider, [row(provider, 1)])],
                [status, 200],
            )
        await add(provider, "http-503-exhausted", [page(provider, [row(provider, 1)])], [503])
    output = Path(__file__).with_name("python_small_provider_core.json")
    output.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
    print(f"froze {len(cases)} original Python inventory cases")


if __name__ == "__main__":
    asyncio.run(main())
