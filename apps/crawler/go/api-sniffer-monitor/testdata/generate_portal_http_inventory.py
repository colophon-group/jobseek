"""Freeze complete original inventories and their actual HTTP exchanges."""

from __future__ import annotations

import asyncio
import copy
import importlib.util
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

CRAWLER = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(CRAWLER))
from src.core.job_content import JobContent, enrich_description  # noqa: E402
from src.core.monitors import infoniqa, keka, pageup, turbohire  # noqa: E402
from src.shared.html_normalize import normalize_description_html  # noqa: E402


def fixture(name):
    spec = importlib.util.spec_from_file_location(name, CRAWLER / "tests" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


k = fixture("test_keka_monitor")
t = fixture("test_turbohire")
p = fixture("test_pageup")
i = fixture("test_infoniqa_monitor")
cases = []


async def freeze(provider, name, board, replies, *, stream=False):
    exchanges = []
    chunks = []

    async def handler(request):
        reply = replies[len(exchanges)]
        exchanges.append(
            dict(
                method=request.method,
                url=str(request.url),
                body=(await request.aread()).decode(),
                headers={
                    key: request.headers[key]
                    for key in (
                        "accept",
                        "content-type",
                        "origin",
                        "referer",
                        "x-requested-with",
                        "authorization",
                        "cookie",
                    )
                    if key in request.headers
                    and not (key == "accept" and request.headers[key] == "*/*")
                },
                response=reply,
            )
        )
        return httpx.Response(
            reply.get("status", 200), text=reply["body"], headers=reply.get("headers", {})
        )

    case = dict(provider=provider, name=name, board=board)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            module = dict(keka=keka, turbohire=turbohire, pageup=pageup, infoniqa=infoniqa)[
                provider
            ]
            if stream:
                jobs = []
                async for chunk in module.stream(board, client):
                    values = [asdict(job) for job in (chunk.jobs_by_url or {}).values()]
                    chunks.append(values)
                    jobs.extend(values)
            else:
                result = await module.discover(board, client)
                jobs = (
                    [dict(url=value) for value in sorted(result)]
                    if isinstance(result, set)
                    else [asdict(job) for job in result]
                )
            case.update(status="complete", expected=jobs)
        except Exception as error:
            case.update(status="failed", error_type=type(error).__name__)
    if case["status"] == "complete":
        prepared = []
        for job in jobs:
            content = JobContent(
                description=normalize_description_html(job.get("description")),
                extras=copy.deepcopy(job.get("extras")),
            )
            enrich_description(content)
            prepared.append(content.description)
        case["canonical_html"] = prepared
    case.update(exchanges=exchanges, chunks=chunks)
    cases.append(case)


def reply(value, *, media="application/json"):
    return dict(
        body=json.dumps(value) if not isinstance(value, str) else value,
        headers={"content-type": media},
    )


async def main():
    for name, values in [
        ("populated", [k._job()]),
        ("empty", []),
        ("duplicate", [k._job(), k._job()]),
        ("missing-title", [k._job(title=None)]),
        ("not-list", {}),
        ("malformed-json", "{"),
    ]:
        await freeze(
            "keka",
            name,
            dict(board_url=k.LISTING_URL, metadata={}),
            [reply(k._bootstrap(), media="text/html"), reply(values)],
        )
    await freeze(
        "keka",
        "identifier-changed",
        dict(board_url=k.LISTING_URL, metadata=k._config()),
        [reply(k._bootstrap(k.OTHER_IDENTIFIER), media="text/html")],
    )
    await freeze(
        "keka",
        "missing-bootstrap",
        dict(board_url=k.LISTING_URL, metadata={}),
        [reply("<html></html>", media="text/html")],
    )
    for name, rows, total, detail in [
        ("populated", [t._raw_job()], 1, t._raw_job()),
        ("empty", [], 0, None),
        ("incomplete", [t._raw_job()], 2, None),
        ("missing-public-id", [{"JobId": "x"}], 1, None),
        ("detail-mismatch", [t._raw_job()], 1, {**t._raw_job(), "JobId": "other"}),
        ("bad-detail-title", [t._raw_job()], 1, {**t._raw_job(), "JobTitle": None}),
    ]:
        replies = [reply({"access_token": "public-token"}), reply({"Total": total, "Result": rows})]
        if detail is not None:
            replies.append(reply(detail))
        await freeze("turbohire", name, dict(board_url=t.BOARD_URL, metadata={}), replies)
    await freeze(
        "turbohire", "missing-token", dict(board_url=t.BOARD_URL, metadata={}), [reply({})]
    )
    for name, jobs, total in [("populated", [(560566, "plumber", "Plumber")], 1), ("empty", [], 0)]:
        await freeze(
            "pageup",
            name,
            dict(board_url=p.LISTING_URL, metadata={}),
            [reply(p._page(jobs, total=total, page=1, page_size=500), media="text/html")],
            stream=True,
        )
    first = [(560566 + n, f"job-{n}", f"Job {n}") for n in range(500)]
    for name, last, total in [
        ("two-pages", [(561066, "final-job", "Final Job")], 501),
        ("late-snapshot-change", [(561066, "final-job", "Final Job")], 502),
        ("repeated-identity", [first[0]], 501),
    ]:
        await freeze(
            "pageup",
            name,
            dict(board_url=p.LISTING_URL, metadata={}),
            [
                reply(p._page(first, total=501, page=1, page_size=500), media="text/html"),
                reply(p._page(last, total=total, page=2, page_size=500), media="text/html"),
            ],
            stream=True,
        )
    for name, total, search_jobs, pages in [
        ("empty", 0, "", []),
        ("initial-jobs", 1, i._job(i.JOB_IDS[0]), []),
        ("two-pages", 2, "", [i._job(i.JOB_IDS[0]), i._job(i.JOB_IDS[1])]),
        ("duplicate-page", 2, i._job(i.JOB_IDS[0]), [i._job(i.JOB_IDS[0])]),
        ("incomplete", 1, "", []),
    ]:
        shell = reply(i._shell(), media="text/html")
        shell["headers"]["set-cookie"] = "JSESSIONID=test-session; Path=/hcm"
        replies = [shell, reply(i._search(total, jobs=search_jobs), media="text/html")]
        for document in pages:
            replies.extend([reply("true"), reply(document, media="text/html")])
        replies.append(reply("false"))
        await freeze(
            "infoniqa",
            name,
            dict(board_url=i.BOARD_URL, metadata={"employer_name": i.EMPLOYER}),
            replies,
        )
    shell = reply(i._shell(employer="Other Employer"), media="text/html")
    await freeze(
        "infoniqa",
        "wrong-employer",
        dict(board_url=i.BOARD_URL, metadata={"employer_name": i.EMPLOYER}),
        [shell],
    )


asyncio.run(main())
target = Path(__file__).with_name("python_portal_http_inventory.json")
target.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
print(
    json.dumps(
        {
            "cases": len(cases),
            "complete": sum(c["status"] == "complete" for c in cases),
            "target": str(target),
        }
    )
)
