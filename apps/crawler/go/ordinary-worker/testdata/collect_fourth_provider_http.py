"""Freeze Paycom and Rippling HTTP contracts from the actual Python monitors."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, paycom, rippling
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
            headers={
                "Content-Type": "text/html; charset=utf-8"
                if provider == "paycom"
                else "application/json",
                **raw["headers"],
            },
            request=request,
        )

    expected = {
        "error": False,
        "gone": False,
        "truncated": False,
        "jobs": [],
        "urls": [],
        "hybrid": False,
    }
    module = {"paycom": paycom, "rippling": rippling}[provider]
    config = {"board_url": board, "metadata": metadata or {}}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(transport), follow_redirects=False
    ) as client:
        try:
            result = await module.discover(config, client)
            if isinstance(result, MonitorResult):
                expected["truncated"] = result.truncated
                expected["hybrid"] = result.hybrid
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


TOKEN = "11111111111111111111111111111111"
PORTAL = "https://www.paycomonline.net/v4/ats/web.php/portal/" + TOKEN + "/career-page"
SERVICE = "https://api.paycomonline.net"
SEARCH = SERVICE + "/api/ats/job-posting-previews/search"
ROOT = "https://api.rippling.com/platform/api/ats/v1/board/acme/jobs"
BOARD = "https://ats.rippling.com/acme/jobs"


def bootstrap(service=SERVICE):
    return (
        "<script>var configsFromHost = "
        + json.dumps(
            {
                "sessionJWT": "public.fixture.signature",
                "libConfig": json.dumps(
                    {
                        "atsPortalMantleServiceUrl": service,
                        "locale": "en-US",
                        "translationHighlights": True,
                    }
                ),
            }
        )
        + ";</script>"
    )


ROW = {
    "jobId": 123,
    "jobTitle": " Engineer ",
    "description": "<p> Build </p>",
    "locations": "Remote, US",
    "remoteType": None,
    "positionType": "Full Time",
    "postedOn": "2026-10-06",
    "isHotJob": False,
}


def paypage(total, rows):
    return response({"jobPostingPreviewsCount": total, "jobPostingPreviews": rows})


async def main():
    cases = []
    for name, value in [
        ("complete", response([{"uuid": "abc-123"}, {"uuid": "def-456"}])),
        ("empty", response([])),
        ("duplicates", response([{"uuid": "abc-123"}, {"uuid": "abc-123"}])),
        ("non-list-empty", response({"jobs": []})),
        ("bad-row", response([None])),
        ("bad-json", response("{")),
        ("any-2xx-success", response([{"uuid": "abc-123"}], 201)),
        ("404-failure", response({}, 404)),
        ("503-failure", response({}, 503)),
    ]:
        cases.append(await collect("rippling", name, BOARD, ROOT, {ROOT: value}))
    configured = "https://api.rippling.com/platform/api/ats/v1/board/configured/jobs"
    cases.append(
        await collect(
            "rippling",
            "configured-slug",
            BOARD,
            configured,
            {configured: response([{"uuid": "abc-123"}])},
            {"slug": "configured"},
        )
    )
    for name, pages in [
        ("complete", {PORTAL: response(bootstrap()), SEARCH: paypage(1, [ROW])}),
        ("empty", {PORTAL: response(bootstrap()), SEARCH: paypage(0, [])}),
        (
            "invalid-row-truncated",
            {PORTAL: response(bootstrap()), SEARCH: paypage(2, [ROW, {"jobId": False}])},
        ),
        (
            "all-invalid-failure",
            {PORTAL: response(bootstrap()), SEARCH: paypage(1, [{"jobId": False}])},
        ),
        ("duplicates-truncated", {PORTAL: response(bootstrap()), SEARCH: paypage(2, [ROW, ROW])}),
        (
            "two-pages",
            {
                PORTAL: response(bootstrap()),
                SEARCH: [
                    paypage(2, [ROW]),
                    paypage(
                        2,
                        [{**ROW, "jobId": "00456", "locations": "Zurich", "remoteType": "Hybrid"}],
                    ),
                ],
            },
        ),
        (
            "total-drift-failure",
            {
                PORTAL: response(bootstrap()),
                SEARCH: [paypage(2, [ROW]), paypage(3, [{**ROW, "jobId": 456}])],
            },
        ),
        (
            "premature-empty-retry",
            {
                PORTAL: response(bootstrap()),
                SEARCH: [paypage(2, [ROW]), paypage(2, []), paypage(2, [{**ROW, "jobId": 456}])],
            },
        ),
        (
            "premature-empty-failure",
            {PORTAL: response(bootstrap()), SEARCH: [paypage(2, [ROW]), paypage(2, [])]},
        ),
        ("bad-total", {PORTAL: response(bootstrap()), SEARCH: paypage(True, [ROW])}),
        (
            "retry-bootstrap-429",
            {PORTAL: [response("", 429), response(bootstrap())], SEARCH: paypage(1, [ROW])},
        ),
        (
            "retry-api-503",
            {PORTAL: response(bootstrap()), SEARCH: [response({}, 503), paypage(1, [ROW])]},
        ),
        ("api-404-failure", {PORTAL: response(bootstrap()), SEARCH: response({}, 404)}),
        ("bootstrap-gone-404", {PORTAL: response("", 404)}),
        ("bootstrap-gone-410", {PORTAL: response("", 410)}),
        (
            "bootstrap-missing-marker-gone",
            {PORTAL: response("Job board does not exist or is unavailable")},
        ),
        ("bootstrap-malformed", {PORTAL: response("var configsFromHost = {broken")}),
        ("bootstrap-untrusted-service", {PORTAL: response(bootstrap("https://example.com"))}),
        ("bootstrap-201-failure", {PORTAL: response(bootstrap(), 201)}),
    ]:
        cases.append(await collect("paycom", name, PORTAL, PORTAL, pages))
    (HERE / "python_fourth_provider_http.json").write_text(
        json.dumps({"http": cases}, indent=2, ensure_ascii=False) + "\n"
    )
    print(f"Frozen {len(cases)} actual Python monitor HTTP cases")


if __name__ == "__main__":
    with patch("src.shared.http_retry.asyncio.sleep", no_wait):
        asyncio.run(main())
