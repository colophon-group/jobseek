"""Freeze complete original PeopleSoft requests, fields and atomic failures."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx
import structlog

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors import peoplesoft

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())
board = "https://fixture.example/psc/site/EMPLOYEE/HRMS/c/HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL"


def row(
    id="42",
    title="Nurse",
    location="Oceanside",
    posted="10/1/2026",
    department="Clinical",
    family="Health",
):
    values = [
        ("HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID", id),
        ("SCH_JOB_TITLE", title),
        ("LOCATION", location),
        ("SCH_OPENED", posted),
        ("HRS_APP_JBSCH_I_HRS_DEPT_DESCR", department),
        ("JOB_FAMILY_LABEL", family),
    ]
    return (
        '<li id="HRS_AGNT_RSLT_I$0_row_'
        + id
        + '">'
        + "".join('<span id="' + k + '$0">' + v + "</span>" for k, v in values if v is not None)
        + "</li>"
    )


def page(rows, total, form=True):
    more = (
        '<div class="ps_box-more" onclick="action(\\\'HRS_AGNT_RSLT_I$hdown$0\\\')"></div><form>'
        '<input name="ICSID" value="synthetic-session">'
        '<input name="duplicate" value="first">'
        '<input name="duplicate" value="second">'
        '<input name="blank">'
        '<input name="ICAction" value="old">'
        '<input name="unchecked" type="checkbox">'
        '<input name="checked" type="checkbox" checked value="yes">'
        '<input name="disabled" disabled>'
        '<input name="submit" type="submit">'
        '<input name="unicode" value="café + x"></form>'
        if form
        else ""
    )
    return (
        "<html><body>HRS_AGNT_RSLT_I Search Results List <b>"
        + str(total)
        + "</b> jobs found<ul>"
        + "".join(rows)
        + "</ul>"
        + more
        + "</body></html>"
    )


cases = []


async def capture(name, pages):
    c = {"name": name, "board": board, "exchanges": []}
    used = 0

    async def handler(request):
        nonlocal used
        if request.url.path.startswith("/psp/"):
            response = httpx.Response(
                200,
                text="<html>anonymous</html>",
                headers={"Set-Cookie": "anonymous=synthetic; Path=/"},
                request=request,
            )
        else:
            if used >= len(pages):
                raise ValueError("Unexpected original extra request")
            source = pages[used]
            used += 1
            response = httpx.Response(200, text=source, request=request)
        c["exchanges"].append(
            {
                "method": request.method,
                "url": str(request.url),
                "request_body": request.content.decode(),
                "body": response.text,
                "cookie": request.headers.get("cookie"),
                "content_type": request.headers.get("content-type"),
            }
        )
        return response

    try:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await peoplesoft.discover({"board_url": board}, client)
        c["jobs"] = [{k: v for k, v in asdict(j).items() if v is not None} for j in jobs]
    except (ValueError, httpx.HTTPError):
        c["error"] = True
    cases.append(c)


async def main():
    for name, changes in [
        ("normal", {}),
        ("nested-text", {"title": "  Registered <b>Nurse</b>  "}),
        ("missing-title", {"title": None}),
        ("missing-location", {"location": None}),
        ("bad-id", {"id": "0"}),
        ("date-single", {"posted": "1/2/2026"}),
        ("date-leap", {"posted": "2/29/2024"}),
        ("date-impossible", {"posted": "2/29/2026"}),
        ("date-short-year", {"posted": "1/2/26"}),
        ("missing-date", {"posted": None}),
        ("missing-metadata", {"department": None, "family": None}),
    ]:
        await capture(name, [page([row(**changes)], 1)])
    await capture("complete-zero", [page([], 0, False)])
    await capture("duplicate-id", [page([row(), row()], 2)])
    await capture("missing-grid", ["<html><b>0</b> jobs found</html>"])
    await capture("missing-count", ["<html>HRS_AGNT_RSLT_I Search Results List</html>"])
    await capture(
        "complete-cumulative",
        [page([row()], 2), page([row(), row(id="43", title="Engineer")], 2, False)],
    )
    await capture("missing-continuation", [page([row()], 2, False)])
    await capture("no-growth", [page([row()], 2), page([row()], 2)])
    await capture("too-many-rows", [page([row(), row(id="43")], 1)])
    await capture("over-cap", [page([], 50001)])


asyncio.run(main())
Path(__file__).with_name("python_last_peoplesoft.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(f"Original PeopleSoft full contracts frozen: {len(cases)}")
