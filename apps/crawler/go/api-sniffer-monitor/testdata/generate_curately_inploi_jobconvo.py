"""Freeze original provider fields and requests before the grouped Go port."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core import enum_normalize  # noqa: E402
from src.core.job_content import enrich_description  # noqa: E402
from src.core.monitor import MonitorResult  # noqa: E402
from src.core.monitors import curately, inploi, jobconvo  # noqa: E402
from src.core.scrapers import jobconvo as jobconvo_detail  # noqa: E402

ROOT = Path(__file__).parent
UUID = "11111111-2222-3333-4444-555555555555"
PAGE = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
BOARD = f"https://jobs.jobconvo.com/pt-br/careers/Example/{PAGE}/"
CURATELY = dict(
    jobId=1,
    clientId=6,
    jobTitle=" Engineer ",
    publicJobDescr="<p>Build & test</p>",
    workCity=" Boston ",
    workState=" MA ",
    workZipcode=" 02110 ",
    status=1,
    jobType=1,
    jobHours=2,
    workType=3,
    createDate="2026-10-09",
    payrateMin=45,
    payrateMax=55.5,
    clientName="Example",
    estStartDate="2026-11-01",
)
INPLOI = dict(
    id=123,
    title=" Engineer ",
    town=" London ",
    city="London",
    country=" UK ",
    location_type="Onsite (5 Days per Week)",
    contract_type="Permanent",
    pay_min="35,000",
    pay_max="45,000",
    pay_currency="GBP",
    pay_type="annual",
    external_ref="ref",
    company_name="Example",
    category="Engineering",
    custom_data=dict(open_date="2026-10-09", expiry_date="2027-01-01"),
)
DETAIL = dict(
    id=UUID,
    title=" Engineer ",
    description="<p>Build</p>",
    requirements="<p>Care</p>",
    benefits="<p>Learn</p>",
    city=" São Paulo ",
    state="São Paulo",
    country=" Brazil ",
    employment="Full time",
    type_work_location=1,
    pub_date="2026-10-09",
    company_language="PT-BR",
    salary=dict(currency="BRL", min=100, max=None, unit="month"),
    company="Example",
    deadline="2027-01-01",
    level="Senior",
    status=0,
)


def frozen(value):
    return {k: v for k, v in asdict(value).items() if v is not None} if value is not None else None


def core():
    cases = []
    for provider, row, call in [
        (
            "curately",
            CURATELY,
            lambda r: curately._parse_job(
                r, short_name="example", currency="usd", salary_unit="hour", language="en"
            ),
        ),
        ("inploi", INPLOI, lambda r: inploi._parse_job(r, "https://careers.example.com/search")),
        ("jobconvo", DETAIL, jobconvo_detail._parse_detail),
    ]:
        variants = [("rich", {})]
        keys = list(row)
        for key in keys:
            variants += [(f"{key}-null", {key: None}), (f"{key}-blank", {key: ""})]
        if provider == "curately":
            variants += [
                ("inactive", dict(status=0)),
                ("unknown-status", dict(status=99)),
                ("remote", dict(workCity=None, workState=None, workZipcode=None, workType=1)),
                ("bool-id", dict(jobId=True)),
                ("string-id", dict(jobId="0123")),
                ("string-status", dict(status="1")),
                ("string-work", dict(workType="1")),
                ("numeric-date", dict(createDate=123)),
                ("bool-pay", dict(payrateMin=True)),
            ]
        elif provider == "inploi":
            variants += [
                ("remote", dict(town="Remote", location_type="location")),
                ("masked", dict(pay_mask=True)),
                ("hidden", dict(pay_display=False)),
                ("single-pay", dict(pay_min=None, pay_max=None, pay="22.5")),
                ("bool-pay", dict(pay_min=True)),
                ("bad-location", dict(location_type=1)),
                ("bool-id", dict(id=True)),
            ]
        else:
            variants += [
                ("string-remote", dict(type_work_location="2")),
                ("bool-work", dict(type_work_location=True)),
                ("unknown-work", dict(type_work_location=99)),
                ("invalid-language", dict(company_language="English")),
            ]
        for name, change in variants:
            sample = copy.deepcopy(row)
            sample.update(change)
            case = dict(provider=provider, mode=name, row=sample)
            try:
                case["expected"] = frozen(call(sample))
            except (ValueError, TypeError, AttributeError):
                case["error"] = True
            cases.append(case)
    ROOT.joinpath("python_curately_inploi_jobconvo_fields.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    ROOT.joinpath("provider_location_types.json").write_text(
        json.dumps(
            enum_normalize._JOB_LOCATION_TYPE_MAP, ensure_ascii=False, sort_keys=True, indent=2
        )
        + "\n"
    )
    return len(cases)


async def inventory(provider, mode):
    boards = dict(
        curately=dict(
            board_url="https://careers.curately.ai/jobs/example",
            metadata=dict(client_id=6, currency="USD", salary_unit="hour", language="en"),
        ),
        inploi=dict(
            board_url="https://careers.example.com/search",
            metadata=dict(api_key="pk_synthetic_example", segment_id="123", page_size=2),
        ),
        jobconvo=dict(board_url=BOARD, metadata=dict(locale="pt-br", career_page=PAGE)),
    )
    board = boards[provider]
    if provider == "curately" and mode == "bootstrap":
        del board["metadata"]["client_id"]
    if provider == "inploi" and mode == "detect":
        del board["metadata"]["api_key"]
    count = 0
    exchanges = []

    def serve(request):
        nonlocal count
        count += 1
        status = 200
        if provider == "curately":
            if "getByShortName" in request.url.path:
                payload = dict(Success=True, Status=200, shortName="example", clientId=6)
            else:
                offset = json.loads(request.content)["next"]
                total = 3 if mode in {"paged", "semantic-zero", "snapshot-change"} else 1
                rows = [dict(CURATELY, jobId=i + 1) for i in range(offset, min(offset + 2, total))]
                if mode == "empty":
                    total, rows = 0, []
                elif mode == "duplicate":
                    total, rows = 2, [dict(CURATELY), dict(CURATELY)]
                elif mode == "wrong-client":
                    rows[0]["clientId"] = 7
                elif mode == "invalid-row":
                    rows = [None]
                elif mode == "inactive":
                    rows[0]["status"] = 0
                elif mode == "gap":
                    total, rows = 2, []
                elif mode == "invalid-title":
                    rows[0]["jobTitle"] = " "
                elif mode == "semantic-zero" and offset > 0 and count == 2:
                    total, rows = 0, []
                elif mode == "snapshot-change" and offset > 0 and count == 2:
                    total = 4
                payload = dict(Success=True, Status=200, TotalSize=total, List=rows)
                if mode == "boolean-total":
                    payload["TotalSize"] = True
                if mode == "bad-envelope":
                    payload["Success"] = False
        elif provider == "inploi":
            if request.url.host == "careers.example.com":
                payload = 'inploi pk_synthetic_example "segment_ids","segment","123"'
            else:
                page = int(request.url.params["page"])
                size = int(request.url.params["per_page"])
                total = 3 if mode in {"paged", "changed-total", "detect"} else 1
                rows = [
                    dict(INPLOI, id=i + 1)
                    for i in range((page - 1) * size, min(page * size, total))
                ]
                if mode == "empty":
                    total, rows = 0, []
                elif mode == "duplicate":
                    total, rows = 2, [dict(INPLOI), dict(INPLOI)]
                elif mode == "mixed-invalid":
                    total, rows = 2, [dict(INPLOI), None]
                elif mode == "invalid-row":
                    rows = [None]
                elif mode == "invalid-title":
                    rows[0]["title"] = " "
                elif mode == "gap":
                    rows = []
                elif mode == "changed-total" and page == 2:
                    total = 4
                payload = dict(
                    data=rows,
                    pagination=dict(
                        total=total, current_page=page, last_page=max(1, (total + size - 1) // size)
                    ),
                )
                if mode == "boolean-total":
                    payload["pagination"]["total"] = True
                if mode == "wrong-page":
                    payload["pagination"]["current_page"] = page + 1
        else:
            page = int(request.url.params.get("page", "1"))
            link_id = UUID if page == 1 else "22222222-2222-3333-4444-555555555555"
            href = f"https://jobs.jobconvo.com/job/engineer/{link_id}/?career_page={PAGE}"
            rows = f'<tr class="joblist"><td><a href="{href}">Engineer</a></td></tr>'
            links = (
                '<li><a href="?page=2">2</a></li>'
                if mode in {"paged", "bad-tail", "escape"}
                else ""
            )
            if mode == "empty":
                rows = ""
            if mode == "missing-table":
                rows = ""
            if mode == "wrong-career":
                rows = rows.replace(PAGE, "00000000-0000-0000-0000-000000000000")
            if mode == "duplicate":
                rows *= 2
            if mode == "escape":
                links = '<li><a href="https://evil.example/?page=2">2</a></li>'
            if mode == "bad-tail" and page == 2:
                status = 404
            payload = (
                f'<table id="tbl">{rows}</table><ul class="pagination">'
                f'<li class="active">{page}</li>{links}</ul>'
            )
            if mode == "missing-table":
                payload = payload.replace('id="tbl"', 'id="other"')
            if mode == "missing-pagination":
                payload = payload.split("<ul")[0]
            if mode == "wrong-page":
                payload = payload.replace(f'active">{page}', f'active">{page + 1}')
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
                    for k in ("accept", "content-type", "x-publishable-key")
                    if k in request.headers
                },
                body=body,
                status=status,
            )
        )
        return httpx.Response(
            status,
            text=body,
            headers={
                "content-type": "text/html" if isinstance(payload, str) else "application/json"
            },
        )

    async def no_sleep(_):
        pass

    case = dict(provider=provider, mode=mode, board=board, exchanges=exchanges)
    with patch.object(asyncio, "sleep", no_sleep):
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            try:
                result = await dict(curately=curately, inploi=inploi, jobconvo=jobconvo)[
                    provider
                ].discover(board, client)
                jobs = (
                    list((result.jobs_by_url or {}).values())
                    if isinstance(result, MonitorResult)
                    else result
                )
                case.update(
                    jobs=sorted(jobs) if provider == "jobconvo" else [frozen(j) for j in jobs],
                    truncated=isinstance(result, MonitorResult) and result.truncated,
                )
            except Exception as exc:
                case.update(error=True, original_error=type(exc).__name__)
    return case


async def main():
    modes = dict(
        curately=[
            "rich",
            "bootstrap",
            "empty",
            "paged",
            "semantic-zero",
            "snapshot-change",
            "duplicate",
            "gap",
            "wrong-client",
            "invalid-row",
            "invalid-title",
            "inactive",
            "boolean-total",
            "bad-envelope",
        ],
        inploi=[
            "rich",
            "detect",
            "empty",
            "paged",
            "changed-total",
            "duplicate",
            "mixed-invalid",
            "invalid-row",
            "invalid-title",
            "gap",
            "boolean-total",
            "wrong-page",
        ],
        jobconvo=[
            "rich",
            "empty",
            "paged",
            "duplicate",
            "missing-table",
            "missing-pagination",
            "wrong-page",
            "wrong-career",
            "escape",
            "bad-tail",
        ],
    )
    cases = [await inventory(p, m) for p, variants in modes.items() for m in variants]
    ROOT.joinpath("python_curately_inploi_jobconvo_inventory.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    details = []
    for mode in [
        "rich",
        "locale-en",
        "wrong-id",
        "wrong-shape",
        "invalid-json",
        "404",
        "410",
        "503",
        "302",
        "salary-text",
        "salary-unknown",
        "salary-range",
    ]:
        exchanges = []
        source = f"https://jobs.jobconvo.com/job/engineer/{UUID}/"
        config = dict(locale="en") if mode == "locale-en" else dict(locale="pt-br")

        def serve_detail(request, mode=mode, exchanges=exchanges):
            payload = dict(DETAIL)
            if mode == "salary-text":
                payload["salary"] = "R$ 100 per month"
            if mode == "salary-unknown":
                payload["salary"] = "negotiable"
            if mode == "salary-range":
                payload["salary"] = "$100,000–$120,000 per year"
            if mode == "wrong-id":
                payload["id"] = PAGE
            if mode == "wrong-shape":
                payload = []
            body = (
                "{"
                if mode == "invalid-json"
                else json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
            )
            status = int(mode) if mode.isdigit() else 200
            exchanges.append(
                dict(
                    method=request.method,
                    url=str(request.url),
                    accept=request.headers.get("accept"),
                    status=status,
                    body=body,
                )
            )
            return httpx.Response(
                status,
                text=body,
                headers={
                    "content-type": "application/json",
                    "location": "https://foreign.example/",
                },
            )

        case = dict(mode=mode, source=source, config=config, exchanges=exchanges)
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve_detail)) as client:
            try:
                result = await jobconvo_detail.scrape(source, config, client)
                case["expected"] = frozen(result)
                enrich_description(result)
                case["processed_description"] = result.description
            except (ValueError, httpx.HTTPError) as exc:
                case.update(error=True, original_error=type(exc).__name__)
        details.append(case)
    ROOT.joinpath("python_jobconvo_detail_requests.json").write_text(
        json.dumps(details, ensure_ascii=False, indent=2) + "\n"
    )
    print(
        f"Frozen {core()} original field cases, {len(cases)} complete inventories "
        f"and {len(details)} detail request cases"
    )


if __name__ == "__main__":
    asyncio.run(main())
