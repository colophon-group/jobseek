"""Freeze Dayforce identities, bootstrap, pages and fields from actual Python."""

from __future__ import annotations

import asyncio
import copy
import json
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import dayforce
from src.shared import browser
from src.shared import dayforce as shared

board = shared.DayforceBoard("fixture", "Careers")
site = shared.DayforceSite(7, "en-US", ("en-US", "fr-CA"), False)
cases = []


def record(kind, name, value, fn, **extra):
    try:
        result = fn(value)
        if hasattr(result, "__dataclass_fields__"):
            result = asdict(result)
        error = False
    except (ValueError, TypeError):
        result, error = None, True
    cases.append(dict(kind=kind, name=name, value=value, expected=result, error=error, **extra))


for name, source in [
    ("listing", board.listing_url()),
    ("localized", board.localized_listing_url("fr-CA")),
    ("detail", board.job_url("en-US", 14)),
    ("trailing-slash", board.listing_url() + "/"),
    ("normalized-case", "https://jobs.dayforcehcm.com/FIXTURE/Careers"),
    ("foreign-host", "https://evil.example/fixture/Careers"),
    ("userinfo", "https://user@jobs.dayforcehcm.com/fixture/Careers"),
    ("bad-port", "https://jobs.dayforcehcm.com:444/fixture/Careers"),
    ("http", "http://jobs.dayforcehcm.com/fixture/Careers"),
    ("query", board.listing_url() + "?x=1"),
    ("fragment", board.listing_url() + "#x"),
    ("reserved-tenant", "https://jobs.dayforcehcm.com/api/Careers"),
    ("zero-detail-id", board.job_url("en-US", 0)),
    ("overflow-detail-id", board.job_url("en-US", 9223372036854775808)),
    ("empty-segment", "https://jobs.dayforcehcm.com/fixture//Careers"),
]:
    record("board", name, source, shared.dayforce_board_from_url)

bootstrap = {
    "query": {"clientNamespace": board.tenant, "careerSiteXRefCode": board.portal},
    "props": {
        "pageProps": {
            "dehydratedState": {
                "queries": [
                    {
                        "queryKey": ["site-info"],
                        "state": {
                            "data": {
                                "clientNamespace": board.tenant,
                                "jobBoardCode": board.portal.lower(),
                                "jobBoardId": 7,
                                "cultureCode": "en-US",
                                "isoCultureCodes": ["en-US", "fr-CA"],
                                "isDisabled": None,
                            }
                        },
                    }
                ]
            }
        }
    },
}


def page(data):
    return '<script id="__NEXT_DATA__" type="application/json">' + json.dumps(data) + "</script>"


def site_data(data):
    return data["props"]["pageProps"]["dehydratedState"]["queries"][0]["state"]["data"]


record("site", "valid", page(bootstrap), lambda p: shared.extract_dayforce_site(p, board))
for name, key, value in [
    ("disabled", "isDisabled", True),
    ("bad-disabled", "isDisabled", "true"),
    ("foreign-tenant", "clientNamespace", "other"),
    ("foreign-portal", "jobBoardCode", "Other"),
    ("invalid-id", "jobBoardId", True),
    ("string-id", "jobBoardId", "0007"),
    ("unsupported-culture", "cultureCode", "de-DE"),
    ("bad-culture", "cultureCode", "en"),
    ("duplicate-cultures", "isoCultureCodes", ["en-US", "EN-us"]),
    ("empty-cultures", "isoCultureCodes", []),
]:
    data = copy.deepcopy(bootstrap)
    site_data(data)[key] = value
    record("site", name, page(data), lambda p: shared.extract_dayforce_site(p, board))
data = copy.deepcopy(bootstrap)
data["query"]["clientNamespace"] = "other"
record("site", "foreign-query", page(data), lambda p: shared.extract_dayforce_site(p, board))
data = copy.deepcopy(bootstrap)
data["props"]["pageProps"]["dehydratedState"]["queries"] *= 2
record("site", "ambiguous-site", page(data), lambda p: shared.extract_dayforce_site(p, board))
record("site", "missing-script", "<p>Missing</p>", lambda p: shared.extract_dayforce_site(p, board))
record(
    "site",
    "invalid-json",
    '<script id="__NEXT_DATA__">{</script>',
    lambda p: shared.extract_dayforce_site(p, board),
)

row = dict(
    clientNamespace=board.tenant,
    jobBoardId=7,
    jobPostingId=14,
    jobReqId=123,
    jobTitle=" Engineer & Builder ",
    jobDescription="Summary&nbsp;\n\nHelp customers.\nExplain benefits.",
    hasVirtualLocation=False,
    postingStartTimestampUTC="2026-08-03T10:00:00+00:00",
    postingExpiryTimestampUTC=" 2026-09-03T10:00:00Z ",
    isEvergreen=False,
    postingLocations=[dict(formattedAddress=" Zurich, CH ")],
)
for name, change in [
    ("plain", {}),
    (
        "html",
        {
            "jobDescription": (
                '<section><h2>Role</h2><p title="x">Build&nbsp; &amp; ship</p>'
                "<script>bad()</script></section>"
            )
        },
    ),
    ("empty-description", {"jobDescription": " "}),
    ("crlf", {"jobDescription": "First\r\nsecond\r\n\r\nthird &lt;quoted&gt;"}),
    ("virtual", {"hasVirtualLocation": True}),
    (
        "unicode-location",
        {
            "postingLocations": [
                dict(formattedAddress="Straße, DE"),
                dict(formattedAddress="STRASSE, DE"),
            ]
        },
    ),
    (
        "virtual-duplicate",
        {
            "hasVirtualLocation": True,
            "postingLocations": [dict(formattedAddress="VIRTUAL"), dict(formattedAddress="Zurich")],
        },
    ),
    ("missing-title", {"jobTitle": None}),
    ("blank-title", {"jobTitle": " "}),
    ("boolean-id", {"jobPostingId": True}),
    ("overflow-id", {"jobPostingId": 9223372036854775808}),
    ("unicode-id", {"jobPostingId": "１４"}),
    ("leading-zero-id", {"jobPostingId": "0014"}),
    ("date-only", {"postingStartTimestampUTC": "2026-01-03"}),
    ("bad-date", {"postingStartTimestampUTC": "nope"}),
    ("boolean-virtual", {"hasVirtualLocation": "true", "isEvergreen": "true", "jobReqId": False}),
]:
    record("field", name, {**row, **change}, lambda r: dayforce._parse_job(r, board, site))
record("field", "non-object", None, lambda r: dayforce._parse_job(r, board, site))

payload = dict(maxCount=1, offset=0, count=1, jobPostings=[row])
for name, change in [
    ("valid", {}),
    ("empty", dict(maxCount=0, count=0, jobPostings=[])),
    ("bad-total", dict(maxCount=True)),
    ("wrong-offset", dict(offset=5)),
    ("bad-count", dict(count=2)),
    ("non-list", dict(jobPostings={})),
    ("overflow-total", dict(maxCount=0)),
    ("foreign-board", dict(jobPostings=[{**row, "jobBoardId": 8}])),
    ("foreign-tenant", dict(jobPostings=[{**row, "clientNamespace": "other"}])),
    ("non-object-row", dict(jobPostings=[None])),
]:
    record("page", name, {**payload, **change}, lambda p: dayforce._page_rows(p, board, site, 0))
for name, overlap in [
    ("default", None),
    ("zero", 0),
    ("five", 5),
    ("ten", 10),
    ("bool", True),
    ("full-page", 25),
    ("negative", -1),
    ("string", "5"),
]:
    config = {} if overlap is None else {"offset_overlap": overlap}
    record("overlap", name, config, dayforce._offset_overlap)
for offset in (0, 20, 25):
    record("body", str(offset), offset, lambda n: json.loads(dayforce._search_body(board, site, n)))

(Path(__file__).parent / "python_dayforce.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print("actual Python Dayforce cases", len(cases))

# Exercise actual discovery with only the browser I/O replaced by frozen pages.
# This proves pagination/output semantics, not Lightpanda transport authority.
pagination_cases = []


def search_page(ids, total, offset=0):
    return dict(
        maxCount=total,
        offset=offset,
        count=len(ids),
        jobPostings=[{**row, "jobPostingId": value} for value in ids],
    )


async def freeze_pagination(name, pages, overlap=0):
    requests = []
    counts = {}

    async def fetch(method, source, headers, body):
        assert method == "POST" and source == board.search_url()
        payload = json.loads(body)
        offset = payload["paginationStart"]
        requests.append(offset)
        choices = pages[str(offset)]
        if not isinstance(choices, list):
            choices = [choices]
        n = counts.get(offset, 0)
        counts[offset] = n + 1
        return copy.deepcopy(choices[min(n, len(choices) - 1)])

    @asynccontextmanager
    async def open_page(*args, **kwargs):
        class Page:
            async def goto(self, *args, **kwargs):
                return None

        yield Page()

    async def content(_page):
        return page(bootstrap)

    async def headers(_page, _board):
        return {
            "accept": "application/json",
            "content-type": "application/json",
            "x-csrf-token": "a" * 64,
        }

    def http_response(request):
        assert request.method == "GET" and str(request.url) == board.listing_url()
        return httpx.Response(200, text=page(bootstrap), request=request)

    expected = dict(error=False, truncated=False, jobs=[])
    with (
        patch.object(browser, "open_page", open_page),
        patch.object(browser, "safe_content", content),
        patch.object(dayforce, "make_browser_fetcher", lambda _page: fetch),
        patch.object(dayforce, "_navigate_and_capture_headers", headers),
    ):
        async with httpx.AsyncClient(transport=httpx.MockTransport(http_response)) as client:
            try:
                result = await dayforce.discover(
                    dict(board_url=board.listing_url(), metadata={"offset_overlap": overlap}),
                    client,
                    pw=object(),
                )
                if isinstance(result, MonitorResult):
                    expected["truncated"] = result.truncated
                    jobs = list((result.jobs_by_url or {}).values())
                else:
                    jobs = result
                expected["jobs"] = [asdict(job) for job in jobs]
            except Exception:
                expected["error"] = True
    pagination_cases.append(
        dict(name=name, pages=pages, overlap=overlap, requests=requests, expected=expected)
    )


async def collect_pagination():
    await freeze_pagination("one-page", {"0": search_page([1], 1)})
    await freeze_pagination("empty", {"0": search_page([], 0)})
    await freeze_pagination(
        "two-pages", {"0": search_page(range(1, 26), 30), "25": search_page(range(26, 31), 30, 25)}
    )
    await freeze_pagination(
        "overlap-five",
        {"0": search_page(range(1, 26), 30), "20": search_page(range(21, 31), 30, 20)},
        5,
    )
    await freeze_pagination(
        "overlap-missing",
        {"0": search_page(range(1, 26), 30), "20": search_page(range(20, 30), 30, 20)},
        5,
    )
    await freeze_pagination(
        "count-grow", {"0": search_page(range(1, 26), 26), "25": search_page([26, 27], 27, 25)}
    )
    await freeze_pagination(
        "count-shrink", {"0": search_page(range(1, 26), 30), "25": search_page([26], 26, 25)}
    )
    await freeze_pagination(
        "premature-empty-retry",
        {
            "0": search_page(range(1, 26), 30),
            "25": [search_page([], 30, 25), search_page(range(26, 31), 30, 25)],
        },
    )
    await freeze_pagination(
        "premature-empty-twice", {"0": search_page(range(1, 26), 30), "25": search_page([], 30, 25)}
    )
    await freeze_pagination("premature-partial", {"0": search_page([1, 2], 30)})
    await freeze_pagination("within-page-duplicate", {"0": search_page([1, 1], 2)})
    await freeze_pagination("invalid-with-valid", {"0": search_page([1, True], 2)})
    await freeze_pagination("all-invalid", {"0": search_page([True], 1)})
    foreign = search_page([26], 26, 25)
    foreign["jobPostings"][0]["clientNamespace"] = "other"
    await freeze_pagination(
        "foreign-second-page", {"0": search_page(range(1, 26), 26), "25": foreign}
    )
    (Path(__file__).parent / "python_dayforce_pagination.json").write_text(
        json.dumps(pagination_cases, ensure_ascii=False, indent=2) + "\n"
    )
    print("actual Python Dayforce pagination cases", len(pagination_cases))


asyncio.run(collect_pagination())


async def collect_bootstrap():
    cases = []
    for name, pages in [
        ("valid", {board.listing_url(): dict(status=200, body=page(bootstrap))}),
        ("missing", {board.listing_url(): dict(status=404, body="missing")}),
        ("gone", {board.listing_url(): dict(status=410, body="gone")}),
        (
            "foreign-redirect",
            {
                board.listing_url(): dict(
                    status=302, body="", location="https://evil.example/fixture/Careers"
                )
            },
        ),
        (
            "localized",
            {
                board.listing_url(): dict(status=302, body="", location="/en-US/fixture/Careers"),
                board.localized_listing_url("en-US"): dict(status=200, body=page(bootstrap)),
            },
        ),
        (
            "wrong-culture",
            {
                board.listing_url(): dict(status=302, body="", location="/fr-CA/fixture/Careers"),
                board.localized_listing_url("fr-CA"): dict(status=200, body=page(bootstrap)),
            },
        ),
        (
            "second-redirect",
            {
                board.listing_url(): dict(status=302, body="", location="/en-US/fixture/Careers"),
                board.localized_listing_url("en-US"): dict(
                    status=302, body="", location="/fr-CA/fixture/Careers"
                ),
            },
        ),
    ]:
        requests = []

        def handler(request, pages=pages, requests=requests):
            requests.append(str(request.url))
            raw = pages[str(request.url)]
            return httpx.Response(
                raw["status"],
                text=raw["body"],
                headers={"Location": raw["location"]} if "location" in raw else {},
                request=request,
            )

        expected = dict(error=False, gone=False, site=None)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                _html, parsed = await dayforce._bootstrap(board, client)
                expected["site"] = asdict(parsed)
            except dayforce.BoardGoneError:
                expected["gone"] = True
            except Exception:
                expected["error"] = True
        cases.append(dict(name=name, pages=pages, requests=requests, expected=expected))
    (Path(__file__).parent / "python_dayforce_bootstrap.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )
    print("actual Python Dayforce bootstrap cases", len(cases))


asyncio.run(collect_bootstrap())
