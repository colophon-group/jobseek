"""Freeze the original Johdi, JobDiva and HeadHunter request/field contracts."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from dataclasses import asdict
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitor import MonitorResult  # noqa: E402
from src.core.monitors import headhunter, jobdiva, johdi  # noqa: E402
from src.core.scrapers import headhunter as headhunter_detail  # noqa: E402
from src.core.scrapers import johdi as johdi_detail  # noqa: E402
from src.shared.http_retry import PaginationFetchError  # noqa: E402

ROOT = Path(__file__).parent
TENANT = "synthetic_public_tenant_12345"
KEY = "synthetic_company_key_12345"
HH = dict(
    id="101",
    employer=dict(id="42", name=" Example "),
    name=" Senior  Engineer ",
    description="<p>Build &amp; maintain Go services in Zurich.</p>",
    address=dict(city=" Zurich ", street=" Example ", building="1"),
    employment_form=dict(id="FULL"),
    work_format=[dict(id="HYBRID"), dict(id="REMOTE")],
    published_at="2026-10-10T10:00:00+0300",
    salary_range=dict(currency="RUR", **{"from": 1000, "to": 2000}, mode=dict(id="MONTH")),
    key_skills=[dict(name=" Go ")],
    professional_roles=[dict(name=" Engineer ")],
    languages=[dict(name=" English ")],
    experience=dict(name="3–6 years"),
    department=dict(name="Platform"),
    schedule=dict(id="remote", name="Remote"),
)


def frozen(job):
    return {k: v for k, v in asdict(job).items() if v is not None}


async def inventory(provider, mode):
    boards = dict(
        johdi=dict(
            board_url="https://employer.example/careers",
            metadata=dict(company_key=KEY, flow="web", locale="fr"),
        ),
        jobdiva=dict(
            board_url=f"https://www2.jobdiva.com/portal/?a={TENANT}&compid=0#/",
            metadata=dict(token=TENANT),
        ),
        headhunter=dict(
            board_url="https://hh.ru/employer/42", metadata=dict(employer_id="42", proxy=True)
        ),
    )
    board = boards[provider]
    exchanges = []
    snapshot = 0

    def handler(request):
        nonlocal snapshot
        status = 200
        content_type = "application/json"
        value = None
        if provider == "johdi":
            if request.url.host == "employer.example":
                content_type = "text/html"
                text = (
                    f'<div id="ats-offers" data-company-hash-key="{KEY}" '
                    'data-flow="web" data-locale="fr"></div>'
                )
                if mode == "wrong-widget":
                    text = text.replace(KEY, "different_company_key_12345")
                if mode == "duplicate-widget":
                    text += text
            else:
                value = [dict(id=1), dict(id="23")]
                if mode == "empty":
                    value = []
                if mode == "duplicate":
                    value = [dict(id=1), dict(id=1)]
                if mode == "invalid-id":
                    value = [dict(id=True)]
                if mode == "wrong-shape":
                    value = dict(data=[])
                if mode == "status":
                    status, value = 403, {}
        elif provider == "jobdiva":
            if request.url.path.endswith("/auth/a"):
                snapshot += 1
                value = dict(token="synthetic_fresh_token", portalID=1, a=TENANT, compid=-1)
                if mode == "missing-token":
                    value.pop("token")
            elif request.url.path.endswith("/searchjobsportal"):
                total = 201
                rows = [dict(id=i) for i in range(1, 201)]
                if mode == "empty":
                    total, rows = 0, []
                if mode == "changing":
                    rows = [dict(id=i + snapshot * 1000) for i in range(1, 201)]
                if mode == "short-first":
                    rows = rows[:1]
                if mode == "invalid-id":
                    rows[0]["id"] = True
                if mode == "duplicate":
                    rows[1]["id"] = 1
                if mode == "invalid-total":
                    total = True
                value = dict(total=total, data=rows)
            else:
                value = dict(data=[dict(id=201)])
                if mode == "changing":
                    value = dict(data=[dict(id=201 + snapshot * 1000)])
                if mode == "shift":
                    value = dict(data=[dict(id=1)])
                if mode == "status":
                    status, value = 403, {}
        else:
            if request.url.host == "api.hh.ru":
                if mode.startswith("public"):
                    status, value = 403, {}
                else:
                    page = int(request.url.params["page"])
                    total = (
                        101 if mode in {"paged", "changing", "duplicate-page", "short-page"} else 1
                    )
                    rows = [copy.deepcopy(HH)]
                    if total == 101:
                        rows = [
                            {**copy.deepcopy(HH), "id": str(i)}
                            for i in range(page * 100 + 1, min(total, (page + 1) * 100) + 1)
                        ]
                    if mode == "changing" and page:
                        total = 102
                    if mode == "duplicate-page" and page:
                        rows[0]["id"] = "1"
                    if mode == "short-page" and page:
                        rows = []
                    if mode == "empty":
                        total, rows = 0, []
                    if mode == "foreign-employer":
                        rows[0]["employer"]["id"] = "43"
                    if mode == "wrong-page":
                        page += 1
                    value = dict(
                        items=rows, found=total, page=page, pages=(total + 99) // 100, per_page=100
                    )
            else:
                vacancy = dict(
                    vacancyId="101",
                    company=dict(id="42"),
                    name=" Engineer ",
                    address=dict(displayName=" Moscow "),
                    publicationTime={"$": "2026-10-10"},
                )
                total = 1
                if mode == "public-partial":
                    total = 2
                if mode == "public-foreign":
                    vacancy["company"]["id"] = "43"
                text = (
                    '<template id="HH-Lux-InitialState">'
                    + json.dumps(
                        dict(vacancySearchResult=dict(vacancies=[vacancy], totalResults=total))
                    )
                    + "</template>"
                )
                content_type = "text/html"
        if value is not None:
            text = json.dumps(value, ensure_ascii=False)
        exchanges.append(
            dict(
                method=request.method,
                url=str(request.url),
                request_body=request.content.decode(),
                headers={
                    k: v
                    for k, v in request.headers.items()
                    if k
                    in {
                        "accept",
                        "user-agent",
                        "content-type",
                        "authorization",
                        "portalid",
                        "a",
                        "compid",
                        "token",
                        "referer",
                    }
                },
                status=status,
                body=text,
                content_type=content_type,
            )
        )
        return httpx.Response(
            status, text=text, headers={"Content-Type": content_type}, request=request
        )

    result = dict(provider=provider, mode=mode, board=board)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            output = await {"johdi": johdi, "jobdiva": jobdiva, "headhunter": headhunter}[
                provider
            ].discover(board, client)
            truncated = isinstance(output, MonitorResult) and output.truncated
            if isinstance(output, MonitorResult):
                output = (
                    list(output.jobs_by_url.values())
                    if output.jobs_by_url is not None
                    else output.urls
                )
            result["jobs"] = (
                sorted((frozen(j) for j in output), key=lambda j: j["url"])
                if provider == "headhunter"
                else sorted(output)
            )
            result["truncated"] = truncated
        except (ValueError, TypeError, httpx.HTTPError, RuntimeError, PaginationFetchError):
            result["error"] = True
    result["exchanges"] = exchanges
    return result


async def main():
    cases = []
    for provider, modes in {
        "johdi": [
            "complete",
            "empty",
            "wrong-widget",
            "duplicate-widget",
            "duplicate",
            "invalid-id",
            "wrong-shape",
            "status",
        ],
        "jobdiva": [
            "complete",
            "empty",
            "changing",
            "short-first",
            "invalid-id",
            "duplicate",
            "invalid-total",
            "missing-token",
            "shift",
            "status",
        ],
        "headhunter": [
            "complete",
            "paged",
            "empty",
            "changing",
            "duplicate-page",
            "short-page",
            "foreign-employer",
            "wrong-page",
            "public",
            "public-partial",
            "public-foreign",
        ],
    }.items():
        for mode in modes:
            cases.append(await inventory(provider, mode))
    ROOT.joinpath("python_remaining_http_inventory.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    fields = []
    for mode, change in [
        ("complete", {}),
        (
            "legacy-salary",
            dict(salary_range=None, salary=dict(currency="USD", **{"from": 1, "to": None})),
        ),
        (
            "shift-salary",
            dict(salary_range=dict(currency="RUR", **{"from": 100}, mode=dict(id="SHIFT"))),
        ),
        (
            "bool-salary",
            dict(
                salary_range=dict(currency="RUR", **{"from": True, "to": 20}, mode=dict(id="HOUR"))
            ),
        ),
        ("foreign-employer", dict(employer=dict(id="43"))),
        ("remote", dict(work_format=[], address=None, area=dict(name="Moscow"))),
    ]:
        row = copy.deepcopy(HH)
        row.update(change)
        parsed = headhunter._parse_job(row, employer_id="42")
        fields.append(dict(mode=mode, row=row, expected=frozen(parsed) if parsed else None))
    ROOT.joinpath("python_remaining_http_fields.json").write_text(
        json.dumps(fields, ensure_ascii=False, indent=2) + "\n"
    )
    detail_cases = []
    johdi_row = dict(
        id=23,
        title=" Engineer ",
        introduction=" <p>Join us.</p> ",
        description=" <p>Build systems.</p> ",
        work_place=" Lausanne ",
        city="Lausanne",
        canton=" VD ",
        country_code=" ch ",
        contract_type="Permanent",
        publication_date=" 2026-10-10 ",
        ref="Example",
        subtitle=" Senior ",
        sector="IT",
        activity_from=80,
        activity_to=100,
        expiration_date="2027-01-01",
        apply_link="https://employer.example/apply",
    )
    for mode, change in [
        ("complete", {}),
        ("blank-description", dict(introduction=None, description="")),
        ("numeric-title", dict(title=123)),
        ("numeric-location", dict(city=123)),
        ("fallback-apply", dict(apply_link=None, postulation_url="https://employer.example/apply")),
        (
            "null-fields",
            dict(
                contract_type=None,
                publication_date=None,
                work_place=None,
                city=None,
                canton=None,
                country_code=None,
            ),
        ),
        ("blank-type", dict(contract_type=" ")),
    ]:
        row = copy.deepcopy(johdi_row)
        row.update(change)
        case = dict(provider="johdi", mode=mode, row=row)
        try:
            case["expected"] = frozen(johdi_detail._parse_detail(row, "fr-CH"))
        except (ValueError, TypeError):
            case["error"] = True
        detail_cases.append(case)
    for case in fields:
        detail_cases.append(
            dict(
                provider="headhunter",
                mode=case["mode"],
                row=case["row"],
                expected=frozen(headhunter_detail.parse_payload(case["row"])),
            )
        )
    ROOT.joinpath("python_remaining_http_detail_fields.json").write_text(
        json.dumps(detail_cases, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps(dict(inventories=len(cases), field_cases=len(fields))))


if __name__ == "__main__":
    asyncio.run(main())
