"""Freeze Comeet and Jobvite HTTP contracts from the actual Python monitors."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, comeet, jobvite
from src.shared.html_normalize import normalize_description_html

HERE = Path(__file__).parent


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
                if provider in {"comeet", "jobvite"}
                else "application/json",
                **raw["headers"],
            },
            request=request,
        )

    expected = {"error": False, "gone": False, "truncated": False, "jobs": [], "urls": []}
    module = {"comeet": comeet, "jobvite": jobvite}[provider]
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
    board = "https://www.comeet.com/jobs/acme/C6.001"
    row = {
        "url_active_page": "https://example.com/jobs/123",
        "name": "Engineer",
        "location": {"city": "London", "country": "UK", "is_remote": True},
        "employment_type": "FULL_TIME",
        "time_updated": "2026-10-06T00:00:00Z",
        "uid": "123",
        "department": "R&D",
        "details": [
            {"name": "Requirements & Skills", "value": "<p>Build</p>", "order": 2},
            {"name": "Responsibilities", "value": "<p>Own it</p>", "order": 1},
        ],
    }
    embedded = (
        "<script>COMPANY_POSITIONS_DATA = "
        + json.dumps([row, False, {}])
        + "; COMPANY_DATA = {};</script>"
    )
    for name, value in [
        ("complete", response(embedded)),
        ("empty", response("COMPANY_POSITIONS_DATA = [];")),
        ("missing-assignment", response("<p>Missing</p>")),
        ("malformed-assignment", response("COMPANY_POSITIONS_DATA = [;")),
        ("hosted-404-failure", response("", 404)),
        ("hosted-503-failure", response("", 503)),
    ]:
        cases.append(
            await collect(
                "comeet",
                name,
                board,
                board,
                {board: value},
                {"company": "acme", "board_id": "C6.001"},
            )
        )
    api = "https://www.comeet.co/careers-api/2.0/company/C6.001/positions?token=public+fixture&details=true"
    md = {"company_id": "C6.001", "token": "public fixture"}
    extra = {
        **row,
        "url_active_page": " ",
        "url_comeet_hosted_page": "https://www.comeet.com/jobs/acme/C6.001/123",
        "location": {"name": " Paris "},
        "details": None,
        "custom_fields": {
            "details": [
                {"name": "What you’ll do", "value": "<b>Own</b>", "order": True},
                {"name": "Who You Are", "value": "<p>Ship</p>", "order": "-1"},
            ]
        },
        "workplace_type": "Hybrid",
    }
    for name, value in [
        ("api-complete", response([row, extra, {}, None])),
        ("api-wrapped", response({"positions": [row]})),
        ("api-empty", response([])),
        ("api-gone", response({}, 404)),
        ("api-410-failure", response({}, 410)),
        ("api-malformed", response("{broken")),
        ("api-201-success", response([row], 201)),
        ("api-503-failure", response({}, 503)),
        ("api-no-positions", response({})),
    ]:
        cases.append(
            await collect("comeet", name, "https://example.com/careers", api, {api: value}, md)
        )
    base = "https://jobs.jobvite.com/acme"
    prefix = '<html ng-app="jv.careersite.desktop.app"><script>careersiteName: "acme"</script>'

    def page(links, range=None):
        text = prefix + "".join('<a href="' + link + '">Open</a>' for link in links)
        if range:
            text += '<div class="jv-pagination-text">' + range + "</div>"
        return text + "</html>"

    detail = "https://jobs.jobvite.com/acme/job/ABC123"
    good = response(
        page(["/acme/job/ABC123?tracking=yes", "https://jobs.jobvite.com/other/job/XXX123"])
    )
    for name, value in [
        ("complete", good),
        ("empty", response(page([]))),
        ("bad-identity", response(page([]).replace('"acme"', '"other"'))),
        ("missing-marker", response("<p>Careers</p>")),
        ("retry-202", [response("", 202), good]),
        ("retry-403", [response("", 403), good]),
        ("retry-empty-200", [response(""), good]),
        ("gone-404", response("", 404)),
        ("gone-410", response("", 410)),
        (
            "invalid-redirect",
            response(
                "",
                302,
                {"Location": "https://www.jobvite.com/support/job-seeker-support?invalid=1"},
            ),
        ),
        ("challenge", response(page([]) + "<title>Just a moment</title>")),
    ]:
        cases.append(await collect("jobvite", name, base, base, {base: value}))
    cases.append(
        await collect(
            "jobvite",
            "anchor-canonicalization",
            base,
            base,
            {
                base: response(
                    page(
                        [
                            "/acme//job/BAD123",
                            "/acme/job/ABC123",
                            "/acme/jobs/",
                            "/other/search?c=bad&p=0",
                        ]
                    )
                )
            },
        )
    )
    cases.append(
        await collect(
            "jobvite",
            "category-nonzero-first",
            base,
            base,
            {base: response(page(["/acme/search?c=Engineering&p=1"]))},
        )
    )
    child = base + "/jobs/positions"
    for name, value in [
        ("linked-listing", response(page(["/acme/job/ABC123"]))),
        ("linked-listing-404-empty", response("", 404)),
        ("linked-listing-503-failure", response("", 503)),
    ]:
        cases.append(
            await collect(
                "jobvite", name, base, base, {base: response(page([child])), child: value}
            )
        )
    first = base + "/search?c=Engineering&p=0"
    second = base + "/search?c=Engineering&p=1"
    for name, p1, p2 in [
        (
            "category-complete",
            response(page([detail, second], "1 - 1 of 2")),
            response(page(["/acme/job/DEF456"], "2 - 2 of 2")),
        ),
        (
            "category-total-drift",
            response(page([detail, second], "1 - 1 of 2")),
            response(page(["/acme/job/DEF456"], "2 - 2 of 3")),
        ),
        (
            "category-repeat",
            response(page([detail, second], "1 - 1 of 2")),
            response(page([detail], "2 - 2 of 2")),
        ),
        (
            "category-missing-next",
            response(page([detail], "1 - 1 of 2")),
            response(page([], "2 - 2 of 2")),
        ),
        (
            "category-missing-range",
            response(page([detail, second])),
            response(page([], "2 - 2 of 2")),
        ),
        ("category-404-failure", response(page([detail, second], "1 - 1 of 2")), response("", 404)),
    ]:
        cases.append(
            await collect(
                "jobvite", name, base, base, {base: response(page([first])), first: p1, second: p2}
            )
        )
    (HERE / "python_third_provider_http.json").write_text(
        json.dumps({"http": cases}, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Frozen {len(cases)} actual Python HTTP cases")


if __name__ == "__main__":
    with patch("src.shared.http_retry.asyncio.sleep", no_wait):
        asyncio.run(main())
