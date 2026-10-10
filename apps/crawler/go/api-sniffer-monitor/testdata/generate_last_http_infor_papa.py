"""Freeze original Infor and Papa Johns monitor contracts."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors import infor, papa_johns

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
board = "https://fixture.cloud.infor.com:1443/fixture/CandidateSelfService/lm?context.dataarea=fixture&context.session.key.JobBoard=PUBLIC&context.session.key.HROrganization=1"
base = {
    "JobRequisition": "42",
    "JobPosting": "7",
    "__Description_translation___": " Registered Nurse ",
    "LocationOfJob": "US:NY:Oceanside",
    "PostingDateRange.Begin": "20261001",
    "WorkType": " Full Time ",
    "Category": "Clinical",
    "SubCategory": "Nursing",
}
cases = []


async def capture(name, payload, cookies=True):
    c = {"name": name, "board": board, "body": payload, "exchanges": []}

    async def handler(request):
        headers = {"Content-Type": "application/json"}
        if "CandidateSelfService" in request.url.path:
            if cookies:
                headers["Set-Cookie"] = "SSO.CSRF=synthetic-csrf; Path=/"
            response = httpx.Response(
                200, headers=headers, text="<html>Anonymous context</html>", request=request
            )
        else:
            response = httpx.Response(200, headers=headers, json=payload, request=request)
        c["exchanges"].append(
            {
                "method": request.method,
                "url": str(request.url),
                "headers": {
                    k: v
                    for k, v in request.headers.items()
                    if k in {"accept", "cookie", "sso.csrf"}
                },
                "response_headers": dict(response.headers),
                "body": response.text,
            }
        )
        return response

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await infor.discover({"board_url": board, "metadata": {}}, client)
        c["jobs"] = [{k: v for k, v in asdict(j).items() if v is not None} for j in jobs]
    except (ValueError, httpx.HTTPError):
        c["error"] = True
    cases.append(c)


async def main():
    for name, changes in [
        ("normal", {}),
        ("numeric-id", {"JobRequisition": 42}),
        ("float-id", {"JobRequisition": 42.5}),
        ("boolean-id", {"JobRequisition": True}),
        ("false-id", {"JobRequisition": False}),
        ("missing-title", {"__Description_translation___": None}),
        ("container-title", {"__Description_translation___": {"a": "b"}}),
        ("empty-location", {"LocationOfJob": " :: "}),
        ("date-single", {"PostingDateRange.Begin": "202611"}),
        ("date-backtracking", {"PostingDateRange.Begin": "2026110"}),
        ("date-impossible", {"PostingDateRange.Begin": "20260229"}),
        ("date-numeric", {"PostingDateRange.Begin": 20261001}),
        ("metadata-non-string", {"WorkType": 42}),
    ]:
        await capture(
            name,
            {
                "JobPostingListWebServices_ListOperationResponseArray": [
                    {"JobPostingListWebServices_ListOperationResponse": {**base, **changes}}
                ]
            },
        )
    await capture("complete-zero", {"JobPostingListWebServices_ListOperationResponseArray": []})
    await capture("missing-array", {})
    await capture(
        "missing-wrapper", {"JobPostingListWebServices_ListOperationResponseArray": [base]}
    )
    await capture(
        "duplicate-identity",
        {
            "JobPostingListWebServices_ListOperationResponseArray": [
                {"JobPostingListWebServices_ListOperationResponse": base}
            ]
            * 2
        },
    )
    await capture(
        "missing-csrf", {"JobPostingListWebServices_ListOperationResponseArray": []}, False
    )


asyncio.run(main())
Path(__file__).with_name("python_last_infor.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
papa = []
for name, source in [
    ("one", "<p>Found 1 jobs at Papa Johns</p><a href='/job/42/nurse/'>Role</a>"),
    ("zero", "<p>Found 0 jobs at Papa Johns</p>"),
    ("missing-count", "<a href='/job/42/nurse/'>Role</a>"),
    ("empty-with-count", "<p>Found 1 jobs at Papa Johns</p>"),
    (
        "pages",
        "<p>Found 1,234 jobs at Papa Johns</p><a href='/job/42/nurse'>Role</a>"
        "<a href='/jobs/?page_jobs=99'>Next</a>",
    ),
    (
        "port",
        "<p>Found 1 jobs at Papa Johns</p><a href='https://jobs.papajohns.com:443/job/42/nurse/'>Role</a>",
    ),
    ("query-reject", "<p>Found 1 jobs at Papa Johns</p><a href='/job/42/nurse/?x=1'>Role</a>"),
    (
        "cross-origin",
        "<p>Found 1 jobs at Papa Johns</p><a href='https://foreign.example/job/42/nurse/'>Role</a>",
    ),
    (
        "duplicate",
        "<p>Found 1 jobs at Papa Johns</p><a href='/job/42/nurse'>Role</a>"
        "<a href='/job/42/nurse/'>Role</a>",
    ),
    (
        "noscript",
        "<p>Found 1 jobs at Papa Johns</p><noscript><a href='/job/42/nurse/'>Role</a></noscript>",
    ),
]:
    c = {"name": name, "source": source}
    try:
        p = papa_johns._parse_listing(source)
        c.update(urls=sorted(p.urls), total=p.total_jobs, pages=p.total_pages)
    except ValueError:
        c["error"] = True
    papa.append(c)
Path(__file__).with_name("python_last_papa.json").write_text(
    json.dumps(papa, ensure_ascii=False, indent=2) + "\n"
)
print(f"Original contracts frozen: Infor {len(cases)}, Papa Johns {len(papa)}")
