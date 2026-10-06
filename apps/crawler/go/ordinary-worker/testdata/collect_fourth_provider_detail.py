"""Freeze actual Python Paycom and Rippling detail HTTP contracts."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx
from collect_fourth_provider_http import PORTAL, ROOT, SERVICE, TOKEN, bootstrap, no_wait, response

from src.core.salary_extract import _parse_salary_text_python
from src.core.scrapers import paycom, rippling

HERE = Path(__file__).parent
PAYURL = "https://www.paycomonline.net/v4/ats/web.php/portal/" + TOKEN + "/jobs/123"
DETAIL = SERVICE + "/api/ats/job-postings/123"
RIPURL = "https://ats.rippling.com/acme/jobs/abc-123"
RIPDETAIL = ROOT + "/abc-123"


async def collect(provider, name, url, pages, config=None):
    requests = []
    counts = {}

    def transport(request):
        key = str(request.url)
        raw = pages[key]
        i = counts.get(key, 0)
        counts[key] = i + 1
        if isinstance(raw, list):
            raw = raw[min(i, len(raw) - 1)]
        requests.append(
            {
                "method": request.method,
                "url": key,
                "body": json.loads(request.content) if request.content else None,
                "headers": {
                    k: request.headers[k]
                    for k in (
                        "authorization",
                        "locale",
                        "translation-highlights",
                        "portal-host-referrer",
                    )
                    if k in request.headers
                },
            }
        )
        return httpx.Response(
            raw["status"],
            text=raw["body"],
            headers={"Content-Type": "text/html; charset=utf-8", **raw["headers"]},
            request=request,
        )

    expected = {"error": False, "content": None, "empty": False}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), follow_redirects=False
    ) as client:
        try:
            content = await {"paycom": paycom, "rippling": rippling}[provider].scrape(
                url, config or {}, client
            )
            expected["content"] = asdict(content)
            expected["empty"] = all(value is None for value in expected["content"].values())
        except Exception:
            expected["error"] = True
    return {
        "provider": provider,
        "name": name,
        "url": url,
        "config": config or {},
        "pages": pages,
        "requests": requests,
        "expected": expected,
    }


async def main():
    cases = []
    row = {
        "name": "Engineer",
        "description": {"company": "<p>Company</p>", "role": "<p>Build</p>"},
        "workLocations": ["Zurich, On-site", "Remote US"],
        "employmentType": {"label": "SALARIED_FT", "id": "salary"},
        "createdOn": "2026-10-06T00:00:00Z",
        "department": {"name": "Platform", "base_department": "Engineering"},
        "companyName": "Acme",
        "payRangeDetails": [
            {
                "rangeStart": 100000,
                "rangeEnd": 140000,
                "currency": "CHF",
                "frequency": "YEARLY_ANNUALLY",
            }
        ],
    }
    for name, value in [
        ("complete", response(row)),
        ("empty", response({})),
        (
            "unknown-salary-frequency",
            response(
                {
                    **row,
                    "payRangeDetails": [
                        {
                            "rangeStart": 10,
                            "rangeEnd": 20,
                            "currency": "USD",
                            "frequency": "unknown",
                        }
                    ],
                }
            ),
        ),
        (
            "hourly",
            response(
                {
                    **row,
                    "workLocations": ["New York, Remote"],
                    "employmentType": {"id": "HOURLY_PT"},
                    "payRangeDetails": [
                        {
                            "rangeStart": 20.5,
                            "rangeEnd": 32.5,
                            "currency": "USD",
                            "frequency": "per-hour-wage",
                        }
                    ],
                }
            ),
        ),
        (
            "description-role-only",
            response(
                {**row, "description": {"role": "<p>Build</p>"}, "workLocations": ["Berlin Hybrid"]}
            ),
        ),
        ("404-empty", response({}, 404)),
        ("503-empty", response({}, 503)),
        ("201-empty", response(row, 201)),
        ("malformed-json", response("{")),
        ("description-invalid-failure", response({**row, "description": "wrong"})),
    ]:
        cases.append(await collect("rippling", name, RIPURL, {RIPDETAIL: value}))
    cases.append(
        await collect(
            "rippling",
            "slug-override",
            RIPURL,
            {RIPDETAIL.replace("/board/acme/", "/board/override/"): response(row)},
            {"slug": "override"},
        )
    )
    cases.append(
        await collect(
            "rippling",
            "regional-localized-source",
            "https://ats.us1.rippling.com/en-US/acme/jobs/abc-123",
            {RIPDETAIL: response(row)},
        )
    )
    job = {
        "jobId": 123,
        "jobTitle": " Engineer ",
        "description": "<p>Build</p>",
        "qualifications": "<p>Go</p>",
        "location": "Zurich",
        "secondaryLocations": ["ZURICH", "London", " london "],
        "positionType": "Full-Time",
        "remoteType": "Hybrid",
        "googleJobJson": json.dumps(
            {"datePosted": "2026-10-06", "title": "Fallback", "employmentType": "CONTRACT"}
        ),
        "salaryRange": "CHF 100000-140000 per year",
        "clientCode": "ACME",
        "jobCategory": "Platform",
        "jobShift": "Day",
        "educationLevel": "Bachelors",
        "travelPercentage": 0,
        "isHotJob": False,
    }
    for name, value, config in [
        ("complete", response({"jobPosting": job}), {}),
        (
            "default-locations",
            response({"jobPosting": {**job, "location": None, "secondaryLocations": None}}),
            {"defaults": {"locations": [" Hawarden, IA ", "Hawarden, IA"]}},
        ),
        (
            "google-fallback",
            response({"jobPosting": {**job, "jobTitle": None, "positionType": None}}),
            {},
        ),
        ("bad-google-json", response({"jobPosting": {**job, "googleJobJson": "{"}}), {}),
        ("missing-job", response({}), {}),
        ("salary-hourly", response({"jobPosting": {**job, "salaryRange": "$20-$30 per hour"}}), {}),
        ("detail-404-empty", response({}, 404), {}),
        ("detail-410-empty", response({}, 410), {}),
        ("detail-503-failure", response({}, 503), {}),
        ("detail-201-failure", response({"jobPosting": job}, 201), {}),
        ("detail-malformed-json", response("{"), {}),
    ]:
        cases.append(
            await collect(
                "paycom", name, PAYURL, {PORTAL: response(bootstrap()), DETAIL: value}, config
            )
        )
    cases.append(
        await collect(
            "paycom",
            "detail-retry-503",
            PAYURL,
            {
                PORTAL: response(bootstrap()),
                DETAIL: [response({}, 503), response({"jobPosting": job})],
            },
        )
    )
    cases.append(
        await collect("paycom", "bootstrap-404-failure", PAYURL, {PORTAL: response("", 404)})
    )
    cases.append(
        await collect(
            "paycom",
            "bootstrap-untrusted-service",
            PAYURL,
            {PORTAL: response(bootstrap("https://example.com"))},
        )
    )
    cases.append(
        await collect("paycom", "invalid-defaults", PAYURL, {}, {"defaults": {"locations": []}})
    )
    (HERE / "python_fourth_provider_detail.json").write_text(
        json.dumps({"http": cases}, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Frozen {len(cases)} actual Python detail HTTP cases")


if __name__ == "__main__":
    with (
        patch("src.shared.http_retry.asyncio.sleep", no_wait),
        patch("src.core.scrapers.paycom.parse_salary_text", _parse_salary_text_python),
    ):
        asyncio.run(main())
