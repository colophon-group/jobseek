"""Freeze real MokaHR detail parsing and transport, using synthetic encryption."""

from __future__ import annotations

import asyncio
import json
import runpy
from dataclasses import asdict
from html import escape
from pathlib import Path

import httpx

from src.core.scrapers import mokahr

HERE = Path(__file__).parent
tests = HERE.parents[2] / "tests"
fixture = runpy.run_path(str(tests / "test_mokahr_scraper.py"))["TestParseDetail"]()
encrypt = runpy.run_path(str(tests / "test_mokahr.py"))["_aes_encrypt"]
ROOT = "https://app.mokahr.com/social-recruitment/zte/47588"
FALLBACK = ROOT.replace("social-", "campus-")
URL = ROOT + "#/job/0c44abe6"
API = "https://app.mokahr.com/api/outer/ats-apply/website/job"
IV, KEY = "0123456789abcdef", "fedcba9876543210"


def spa(**overrides):
    data = {
        "aesIv": IV,
        "jobsGroupedByLocation": [
            {"cityId": key, "label": val} for key, val in fixture._city_map().items()
        ],
        **overrides,
    }
    return '<input id="init-data" value="' + escape(json.dumps(data), quote=True) + '">'


def envelope(payload):
    return {"data": encrypt(json.dumps(payload).encode(), KEY, IV), "necromancer": KEY}


def response(body, status=200, headers=None):
    return {
        "body": json.dumps(body) if isinstance(body, (dict, list)) else body,
        "status": status,
        "headers": headers or {},
    }


out = {"projection": [], "http": []}
for name, raw in [
    ("complete", fixture._detail()),
    ("empty", {}),
    ("empty-fields", fixture._detail(title="", jobDescription="", commitment="")),
    ("opened-date", fixture._detail(publishedAt=None)),
    ("string-department", fixture._detail(department="Engineering", zhineng="Research")),
    ("no-experience", fixture._detail(minExperience=None, maxExperience=None)),
    ("zero-salary", fixture._detail(minSalary=0, maxSalary=0)),
    (
        "locations-fallback",
        fixture._detail(locations=[{"provinceName": "湖北", "country": "中国"}]),
    ),
    *[
        (f"employment-{value}", fixture._detail(commitment=value))
        for value in [
            "全职",
            "兼职",
            "实习",
            "fullTime",
            "partTime",
            "intern",
            "contract",
            "unknown",
            None,
        ]
    ],
]:
    out["projection"].append(
        {
            "name": name,
            "raw": raw,
            "cities": fixture._city_map(),
            "expected": asdict(mokahr._parse_detail(raw, fixture._city_map())),
        }
    )

cases = []


def case(name, pages=None, url=URL, config=None, **flags):
    cases.append(
        (
            name,
            url,
            config or {},
            pages
            or {
                ROOT: response(spa()),
                API: response(envelope({"data": fixture._detail()})),
            },
            flags,
        )
    )


case("complete")
for reserved in [False, True]:
    target = ROOT + "?redirected=1"
    case(
        "bootstrap-redirect-reserved" if reserved else "bootstrap-redirect",
        {
            ROOT: response("", 302, {"Location": target}),
            target: response(spa(), headers={"TDM-Reservation": "1"} if reserved else {}),
            API: response(envelope({"data": fixture._detail()})),
        },
        native_reserved=reserved,
    )
case(
    "detail-redirect-refused",
    {ROOT: response(spa()), API: response("", 307, {"Location": API + "?redirected=1"})},
)
case("custom-locale", config={"locale": "en-US"})
case(
    "campus-fallback",
    {
        ROOT: response("gone", 404),
        FALLBACK: response(spa()),
        API: response(envelope({"data": fixture._detail()})),
    },
)
case("missing-bootstrap", {ROOT: response("shell"), FALLBACK: response("shell")})
case("empty-iv", {ROOT: response(spa(aesIv="")), FALLBACK: response(spa(aesIv=""))})
case(
    "malformed-bootstrap",
    {ROOT: response('<input id="init-data" value="bad">'), FALLBACK: response("shell")},
)
for status in [201, 204, 403, 404, 500]:
    case(f"detail-status-{status}", {ROOT: response(spa()), API: response("", status)})
case("detail-bad-json", {ROOT: response(spa()), API: response("bad")})
case("detail-missing-crypto", {ROOT: response(spa()), API: response({})})
case(
    "detail-invalid-crypto",
    {ROOT: response(spa()), API: response({"data": "bad", "necromancer": KEY})},
)
case("detail-missing-data", {ROOT: response(spa()), API: response(envelope({"success": True}))})
case("detail-empty-data", {ROOT: response(spa()), API: response(envelope({"data": {}}))})
case(
    "detail-error-envelope-with-data",
    {
        ROOT: response(spa()),
        API: response(envelope({"success": False, "code": 1, "data": fixture._detail()})),
    },
)
case("unparseable-source", url="https://app.mokahr.com/not-a-job")
case(
    "bootstrap-reserved",
    {ROOT: response(spa(), headers={"TDM-Reservation": "1"})},
    native_reserved=True,
)
case(
    "detail-reserved",
    {
        ROOT: response(spa()),
        API: response(envelope({"data": fixture._detail()}), headers={"TDM-Reservation": "1"}),
    },
    native_reserved=True,
)
case(
    "foreign-id",
    {ROOT: response(spa()), API: response(envelope({"data": fixture._detail(id="other")}))},
    native_empty=True,
)


async def collect():
    for name, url, config, pages, flags in cases:
        requests = []

        def handler(request, *, requests=requests, pages=pages, flags=flags):
            source = str(request.url)
            requests.append(
                {
                    "method": request.method,
                    "url": source,
                    "body": json.loads(request.content) if request.content else None,
                }
            )
            if source not in pages:
                # Reserved native paths deliberately stop before these legacy requests.
                if flags.get("native_reserved"):
                    return httpx.Response(404, text="gone")
                raise AssertionError(f"undeclared fixture resource: {source}")
            row = pages[source]
            return httpx.Response(row["status"], text=row["body"], headers=row["headers"])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            expected = asdict(await mokahr.scrape(url, config, client))
        out["http"].append(
            {
                "name": name,
                "url": url,
                "config": config,
                "pages": pages,
                "requests": requests,
                "expected": expected,
                **flags,
            }
        )


asyncio.run(collect())
(HERE / "python_mokahr_detail.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n"
)
print(json.dumps({key: len(value) for key, value in out.items()}))
