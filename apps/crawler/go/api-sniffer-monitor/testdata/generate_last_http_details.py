"""Freeze original Infor and PeopleSoft paired detail contracts."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors import infor as monitor  # noqa: E402
from src.core.scrapers import infor, peoplesoft  # noqa: E402

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
board = "https://fixture.cloud.infor.com:1443/fixture/CandidateSelfService/lm?context.dataarea=fixture&context.session.key.JobBoard=PUBLIC&context.session.key.HROrganization=1"
site, _, _ = monitor.parse_candidate_url(board)
source = monitor.build_job_url(site, "42", "7")
base = {
    "JobRequisition": "42",
    "JobPosting": "7",
    "__Description_translation___": " Nurse ",
    "Description": "Fallback title",
    "__PositionDescription_translation___": " <p>Clinical role.</p> ",
    "PositionDescription": "Fallback description",
    "LocationOfJob.Description": "US:NY:Oceanside",
    "PostingDateRange.Begin": "20261001",
    "Category.Description": " Health ",
    "RelationshipToOrganization.Description": " Employee ",
    "JobRequisitionLocation": "South Nassau",
}
cases = []


async def capture(name, payload):
    c = {"name": name, "board": board, "source": source, "exchanges": []}

    async def handler(request):
        if "CandidateSelfService" in request.url.path:
            r = httpx.Response(
                200,
                text="<html>Anonymous context</html>",
                headers={"Set-Cookie": "SSO.CSRF=synthetic-csrf; Path=/"},
                request=request,
            )
        else:
            r = httpx.Response(200, json=payload, request=request)
        c["exchanges"].append(
            {
                "method": request.method,
                "url": str(request.url),
                "body": r.text,
                "headers": {
                    k: v
                    for k, v in request.headers.items()
                    if k in {"accept", "cookie", "sso.csrf"}
                },
                "response_headers": dict(r.headers),
            }
        )
        return r

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            job = await infor.scrape(source, {}, client)
        c["job"] = {k: v for k, v in asdict(job).items() if v is not None}
    except (ValueError, httpx.HTTPError):
        c["error"] = True
    cases.append(c)


async def main():
    for name, changes in [
        ("normal", {}),
        ("fallback-title", {"__Description_translation___": None}),
        ("fallback-description", {"__PositionDescription_translation___": None}),
        (
            "fallback-location",
            {
                "LocationOfJob.Description": None,
                "__LocationOfJob.Description_translation___": "CH:Lausanne",
            },
        ),
        ("empty-title", {"__Description_translation___": "   "}),
        ("nonstring-title", {"__Description_translation___": 42}),
        ("nonstring-description", {"__PositionDescription_translation___": 42}),
        ("empty-all", dict.fromkeys(base)),
        ("nonstring-metadata", {"JobRequisition": 42}),
        ("date-zero", {"PostingDateRange.Begin": "00000000"}),
        ("date-impossible", {"PostingDateRange.Begin": "20260229"}),
        ("date-backtracking", {"PostingDateRange.Begin": "2026110"}),
    ]:
        await capture(name, {"Find_PostingDisplay_FormOperationResponse": {**base, **changes}})
    await capture("missing-wrapper", {})
    await capture("wrong-wrapper", {"Find_PostingDisplay_FormOperationResponse": []})


asyncio.run(main())
Path(__file__).with_name("python_last_infor_detail.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)


def detail(
    title="Remote Nurse",
    id="42",
    employment="Full-time",
    location="Oceanside",
    regular="Regular",
    salary="USD 100000-120000",
    sections=True,
):
    rows = []
    if sections:
        for i, (heading, body) in enumerate(
            [
                ("What your job will be like", "<p>Work with clinical teams.</p>"),
                ("Required Qualifications", "<p>Registered nurse license.</p>"),
                ("Salary", salary),
            ]
        ):
            rows.append(
                f'<div id="win0divHRS_SCH_PSTDSC_row${i}"><h2>'
                f'<span class="ps-text">{heading}</span></h2>'
                f'<span id="HRS_SCH_PSTDSC_DESCRLONG${i}">{body}</span></div>'
            )
    values = {
        "HRS_SCH_WRK2_HRS_JOB_OPENING_ID": id,
        "HRS_SCH_WRK2_POSTING_TITLE": title,
        "HRS_SCH_WRK_HRS_DESCRLONG": location,
        "HRS_SCH_WRK_HRS_FULL_PART_TIME": employment,
        "HRS_SCH_WRK_HRS_REG_TEMP": regular,
    }
    return (
        "<html><body>"
        + "".join(f'<span id="{k}">{v}</span>' for k, v in values.items() if v is not None)
        + "".join(rows)
        + "</body></html>"
    )


rows = []
original_employment = peoplesoft.normalize_employment_type
original_workplace = peoplesoft.normalize_job_location_type
original_salary = peoplesoft.parse_salary_text
for name, changes in [
    ("normal", {}),
    ("hybrid", {"title": "Hybrid Engineer"}),
    ("onsite", {"title": "On-site Nurse"}),
    ("unknown-workplace", {"title": "Engineer"}),
    ("parttime", {"employment": "Part Time"}),
    ("unknown-employment", {"employment": "Unknown"}),
    ("missing-employment", {"employment": None}),
    ("missing-location", {"location": None}),
    ("missing-id", {"id": None}),
    ("changed-id", {"id": "43"}),
    ("missing-title", {"title": None}),
    ("missing-sections", {"sections": False}),
    ("empty-salary", {"salary": ""}),
    ("hourly-salary", {"salary": "USD 40-50 per hour"}),
    ("missing-regular", {"regular": None}),
]:
    html = detail(**changes)
    c = {"name": name, "source": html, "expected_id": "42", "adapters": {}}

    def employment(raw, case=c):
        value = original_employment(raw)
        case["adapters"]["employment"] = {"input": raw or "", "value": value}
        return value

    def workplace(raw, case=c):
        value = original_workplace(raw)
        case["adapters"]["workplace"] = {"input": raw or "", "value": value}
        return value

    def salary(raw, case=c):
        value = original_salary(raw)
        case["adapters"]["salary"] = {"input": raw, "value": value}
        return value

    peoplesoft.normalize_employment_type = employment
    peoplesoft.normalize_job_location_type = workplace
    peoplesoft.parse_salary_text = salary
    try:
        c["job"] = {
            k: v
            for k, v in asdict(peoplesoft.parse_detail(html, expected_job_id="42")).items()
            if v is not None
        }
    except ValueError:
        c["error"] = True
    rows.append(c)
Path(__file__).with_name("python_last_peoplesoft_detail.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
)
print(f"Original paired details frozen: Infor {len(cases)}, PeopleSoft {len(rows)}")
