"""Freeze grouped provider HTTP requests and output from the actual Python monitors."""

from __future__ import annotations

import asyncio
import functools
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, bamboohr, recruiter_co_kr, softgarden, ukg
from src.shared.html_normalize import normalize_description_html

HERE = Path(__file__).parent
UKG = "https://recruiting.ultipro.com/ABC123/JobBoard/11111111-1111-1111-1111-111111111111"
KR = "https://api-recruiter.recruiter.co.kr"
ROW = {
    "Id": "22222222-2222-2222-2222-222222222222",
    "Title": "Engineer",
    "BriefDescription": "<p>Build</p>",
    "FullTime": True,
}


def response(value, status=200, headers=None):
    return {
        "status": status,
        "body": value if isinstance(value, str) else json.dumps(value),
        "headers": headers or {},
    }


async def no_wait(*args):
    return None


async def collect(provider, name, board, endpoint, pages, metadata=None):
    requests = []
    counts = {}

    def transport(request):
        key = str(request.url).rstrip("/") if provider == "softgarden" else str(request.url)
        raw = pages[key]
        index = counts.get(key, 0)
        counts[key] = index + 1
        if isinstance(raw, list):
            raw = raw[min(index, len(raw) - 1)]
        requests.append(
            {
                "method": request.method,
                "url": key,
                "body": json.loads(request.content) if request.content else None,
                "prefix": request.headers.get("prefix", ""),
            }
        )
        return httpx.Response(
            raw["status"],
            text=raw["body"],
            headers={
                "Content-Type": "text/html; charset=utf-8"
                if provider == "softgarden"
                else "application/json",
                **raw["headers"],
            },
            request=request,
        )

    expected = {"error": False, "gone": False, "truncated": False, "jobs": [], "urls": []}
    module = {
        "softgarden": softgarden,
        "ukg": ukg,
        "bamboohr": bamboohr,
        "recruiter_co_kr": recruiter_co_kr,
    }[provider]
    config = {"board_url": board, "metadata": metadata or {}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), follow_redirects=False
    ) as client:
        try:
            result = await module.discover(config, client)
            if isinstance(result, MonitorResult):
                expected["truncated"] = result.truncated
                jobs = list((result.jobs_by_url or {}).values())
                expected["urls"] = sorted(result.urls)
            elif isinstance(result, set):
                jobs = []
                expected["urls"] = sorted(result)
            else:
                jobs = result
                expected["urls"] = sorted(job.url for job in jobs)
            for job in jobs:
                fields = asdict(job)
                for key in ("localizations", "base_salary", "source_identity"):
                    fields.pop(key, None)
                fields["description"] = normalize_description_html(fields["description"])
                expected["jobs"].append(fields)
            expected["jobs"].sort(key=lambda job: job["url"])
        except Exception as error:
            expected["error"] = True
            expected["gone"] = isinstance(error, BoardGoneError)
    return {
        "provider": provider,
        "name": name,
        "board_url": board,
        "endpoint": endpoint,
        "metadata": metadata or {},
        "pages": pages,
        "requests": requests,
        "expected": expected,
    }


async def main():
    cases = []
    sg = "https://acme.softgarden.io"
    for name, value, md in [
        ("complete", response("var complete_job_id_list=[123,456];"), {}),
        (
            "custom-german",
            response("var complete_job_id_list=[123];"),
            {"job_url_pattern": "{base}/job/{id}?l=de"},
        ),
        ("empty", response("var complete_job_id_list=[];"), {}),
        ("missing-marker", response("<html>No marker</html>"), {}),
        ("any-success", response("var complete_job_id_list=[123];", 201), {}),
        ("failure", response("", 503), {}),
        ("generic-404-failure", response("", 404), {}),
    ]:
        cases.append(await collect("softgarden", name, sg, sg, {sg: value}, md))
    search = UKG + "/JobBoardView/LoadSearchResults"
    full = response({"totalCount": 1, "opportunities": [ROW]})
    for name, value in [
        ("complete", full),
        ("empty", response({"totalCount": 0, "opportunities": []})),
        ("bad-total", response({"totalCount": True, "opportunities": [ROW]})),
        ("retry-202", [response({}, 202), full]),
        ("partial", response({"totalCount": 2, "opportunities": [ROW]})),
        ("duplicate", response({"totalCount": 2, "opportunities": [ROW, ROW]})),
    ]:
        cases.append(await collect("ukg", name, UKG, search, {search: value}))
    listing = "https://acme.bamboohr.com/careers/list"
    full = response(
        {"meta": {"totalCount": 1}, "result": [{"id": 123, "jobOpeningName": "Engineer"}]}
    )
    for name, value in [
        ("complete", full),
        ("empty", response({"meta": {"totalCount": 0}, "result": []})),
        ("retry-json", [response("{"), full]),
        (
            "retirement-redirect",
            response("", 302, {"Location": "https://www.bamboohr.com/careers/"}),
        ),
        ("filter-match", full),
        ("filter-no-match", full),
    ]:
        md = {"description_include_regex": "(?i)ExecuJet"} if name.startswith("filter-") else {}
        pages = {listing: value}
        if md:
            pages["https://acme.bamboohr.com/careers/123/detail"] = response(
                {
                    "result": {
                        "jobOpening": {
                            "description": "<p>ExecuJet&#39;s FBO</p>"
                            if name == "filter-match"
                            else "<p>Other division</p>"
                        }
                    }
                }
            )
        cases.append(
            await collect("bamboohr", name, "https://acme.bamboohr.com/careers", listing, pages, md)
        )
    listing = KR + "/position/v1/jobflex"
    detail = KR + "/position/v2/jobflex/123"
    full = response(
        {"pagination": {"totalPages": 1}, "list": [{"positionSn": 123, "title": "Engineer"}]}
    )
    hydrated = response(
        {
            "title": "Engineer",
            "jobDescription": "<p>Build</p>",
            "startDateTime": "2026-10-06T00:00:00",
        }
    )
    for name, listing_response, detail_response in [
        ("complete", full, hydrated),
        ("detail-404-fallback", full, response({}, 404)),
        ("list-retry", [response({}, 429), full], hydrated),
        ("tenant-gone", response({"code": "NotFoundCompanyException"}, 400), hydrated),
        ("detail-bad-json", full, response("{")),
        ("detail-retry", full, [response({}, 500), hydrated]),
    ]:
        cases.append(
            await collect(
                "recruiter_co_kr",
                name,
                "https://acme.recruiter.co.kr/career/home",
                listing,
                {listing: listing_response, detail: detail_response},
            )
        )
    return cases


with (
    patch.object(
        ukg,
        "fetch_json_page_with_retry",
        functools.partial(ukg.fetch_json_page_with_retry, sleep=no_wait),
    ),
    patch.object(
        bamboohr,
        "fetch_json_page_with_retry",
        functools.partial(bamboohr.fetch_json_page_with_retry, sleep=no_wait),
    ),
    patch.object(recruiter_co_kr.asyncio, "sleep", no_wait),
):
    cases = asyncio.run(main())
(HERE / "python_secondary_provider_http.json").write_text(
    json.dumps({"http": cases}, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps({"http_reference_cases": len(cases)}))
