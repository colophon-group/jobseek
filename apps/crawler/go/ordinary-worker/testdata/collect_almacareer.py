"""Freeze actual AlmaCareer helpers with synthetic public widget identifiers."""

from __future__ import annotations

import asyncio
import json
import runpy
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitors import almacareer as alma
from src.shared.html_normalize import normalize_description_html

HERE = Path(__file__).parent
ns = runpy.run_path(str(HERE.parents[2] / "tests" / "test_almacareer.py"))
raw = ns["TestParseJob"]()._raw
WIDGET = "11111111-2222-3333-4444-555555555555"
KEY = "0" * 64
out = {"host": [], "widget": [], "chunks": [], "groups": [], "projection": []}
for name, url, metadata in [
    ("cz", "https://acme.jobs.cz/jobs", {}),
    ("sk", "https://acme.topjobs.sk/", {}),
    ("www", "https://www.acme.jobs.cz/", {}),
    ("metadata-host", "https://careers.example.com/", {"host": "ACME.jobs.cz"}),
    ("metadata-slug", "https://careers.example.com/", {"slug": "acme", "country": "sk"}),
    ("custom-host", "https://careers.example.com/", {"host": "careers.example.com"}),
    ("ignored-slug", "https://api.jobs.cz/", {}),
    ("unknown", "https://example.com/", {}),
]:
    out["host"].append(
        {
            "name": name,
            "url": url,
            "metadata": metadata,
            "expected": alma._host_from_board(url, metadata),
        }
    )


def widget(name, source, inline=False):
    parser = alma._extract_inline_widget_config if inline else alma._extract_widget_config
    out["widget"].append(
        {"name": name, "source": source, "inline": inline, "expected": parser(source)}
    )


config = {"id": WIDGET, "apiKey": KEY, "detailPath": "detail-pozice"}
widget("script", json.dumps({"widgets": {"main": config}}))
widget("nested", json.dumps({"widgets": {"main": {"themes": {"nested": {}}, **config}}}))
widget("default-path", json.dumps({"widgets": {"main": {"id": WIDGET, "apiKey": KEY}}}))
widget("missing-key", json.dumps({"widgets": {"main": {"id": WIDGET}}}))
widget("missing-anchor", json.dumps(config))
widget("bounded-window", '"widgets":{"main":{' + " " * 65537 + json.dumps(config))
widget("unicode-window", '"widgets":{"main":{' + "é" * 40000 + json.dumps(config))
inline = (
    "window.__LMC_CAREER_WIDGET__.push("
    + json.dumps({"widgetId": WIDGET, "apiKey": KEY, "detailPath": "details"})
    + ");"
)
widget("inline", inline, True)
widget("inline-default-path", inline.replace(', "detailPath": "details"', ""), True)
widget("inline-recovery", "window.__LMC_CAREER_WIDGET__.push({bad});" + inline, True)
widget("inline-invalid-id", inline.replace(WIDGET, "invalid"), True)
widget("inline-invalid-key", inline.replace(KEY, "invalid"), True)
widget(
    "inline-nested",
    inline.replace('"detailPath": "details"', '"detailPath": "details", "config": {"nested": {}}'),
    True,
)

for name, source in [
    ("unique", '"react.a.react.min.js" "react.b.react.min.js" "react.a.react.min.js"'),
    ("traversal", '"../react.a.react.min.js" "react.a/react.min.js" "react.a.react.min.js?bad"'),
    ("bounded", " ".join(f'"react.chunk{n}.react.min.js"' for n in range(20))),
]:
    out["chunks"].append(
        {
            "name": name,
            "source": source,
            "expected": list(dict.fromkeys(alma._REACT_CHUNK_RE.findall(source)))[:16],
        }
    )
for name, group in [
    ("empty", None),
    (
        "tree",
        {
            "jobAds": [raw()],
            "groups": [{"jobAds": [raw(id="2")], "groups": [{"jobAds": [raw(id="3")]}]}],
        },
    ),
    ("empty-tree", {"groups": [{"jobAds": []}]}),
]:
    out["groups"].append({"name": name, "raw": group, "expected": alma._flatten_groups(group)})


def project(name, value):
    job = alma._parse_job(value, "acme.jobs.cz", "detail-pozice", "cz")
    expected = asdict(job) if job else None
    if expected:
        for key in ["localizations", "source_identity"]:
            expected.pop(key, None)
    out["projection"].append({"name": name, "raw": value, "expected": expected})


project("full", raw())
project("missing-id", raw(id=None))
project("missing-title", raw(title=""))
project("minimal", {"id": "x", "title": "Engineer"})
project("numeric-id", raw(id=1001))
project("large-id", raw(id=900719925474099312345))
project(
    "five-location-parts",
    raw(
        locations=[
            {
                "cityPart": "Part",
                "city": "City",
                "district": "District",
                "region": "Region",
                "country": "Country",
            }
        ]
    ),
)
project(
    "duplicate-location-parts",
    raw(locations=[{"cityPart": "City", "city": "City", "country": "Country"}] * 2),
)
project("empty-locations", raw(locations=None))
for period in ["měsíc", "mesiac", "hodina", "rok", "month", "hour", "year", "unknown"]:
    project(
        "salary-" + period,
        raw(salary={"min": "2.5", "max": None, "period": period, "currency": "EUR"}),
    )
project("no-currency", raw(salary={"min": 1}))
project("bad-salary-number", raw(salary={"min": "bad", "max": "bad", "currency": "EUR"}))
for employment in [
    "201300001",
    "201300002",
    "201300003",
    "201300004",
    "201300005",
    "201300006",
    "201300007",
    "unknown",
]:
    project(
        "employment-" + employment,
        raw(parameters={"employmentTypesObjects": [{"id": employment, "label": "Locale label"}]}),
    )
project("date-short", raw(validFrom="short"))
project("date-numeric", raw(validFrom=1))
HOST = "acme.jobs.cz"
ROOT = "https://" + HOST + "/"
SCRIPT = ROOT + "assets/js/script.min.js"
LOADER = ROOT + "assets/js/react.min.js"
CHUNK = ROOT + "assets/js/react.a.react.min.js"
GRAPHQL = alma.GRAPHQL_URL
helpers = ns["TestDiscover"]
script = helpers._build_script(WIDGET, KEY, "detail-pozice")
listing = helpers._listing_response
detail = helpers._detail_response
http_cases = []


def response(body, status=200, headers=None):
    return {
        "body": json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else body,
        "status": status,
        "headers": headers or {},
    }


def add_http(name, metadata=None, pages=None, native_reserved=False):
    defaults = {
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    }
    http_cases.append((name, metadata or {}, pages or defaults, native_reserved))


add_http("complete")
add_http("configured", {"widget_id": WIDGET, "api_key": KEY})
add_http("partial-id", {"widget_id": "11111111-2222-3333-4444-555555555556"})
add_http(
    "configured-detail-path", {"widget_id": WIDGET, "api_key": KEY, "detail_path": "custom/detail"}
)
add_http(
    "chunk-bootstrap",
    pages={
        SCRIPT: response("legacy"),
        LOADER: response('"react.a.react.min.js"'),
        CHUNK: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http(
    "inline-bootstrap",
    pages={
        SCRIPT: response("legacy"),
        LOADER: response("gone", 404),
        ROOT: response(inline),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http(
    "gone-bundle-inline",
    pages={
        SCRIPT: response("gone", 404),
        ROOT: response(inline),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http("script-gone", pages={SCRIPT: response("gone", 404), ROOT: response("gone", 404)})
add_http("script-permanent-403", pages={SCRIPT: response("forbidden", 403)})
add_http(
    "script-retry-503",
    pages={
        SCRIPT: [response("busy", 503), response(script)],
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http(
    "script-content-retry",
    pages={
        SCRIPT: [response("bad"), response(script)],
        LOADER: response("gone", 404),
        ROOT: response("none"),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http(
    "script-parse-exhausted",
    pages={SCRIPT: response("bad"), LOADER: response("gone", 404), ROOT: response("none")},
)
add_http(
    "pagination",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()], last_page=2)),
        "LIST:2": response(listing([raw(id="1002")], last_page=2)),
        "DETAIL:1001": response(detail("1001", "<p>One</p>")),
        "DETAIL:1002": response(detail("1002", "<p>Two</p>")),
    },
)
add_http(
    "later-listing-failure",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()], last_page=2)),
        "LIST:2": response("gone", 404),
    },
)
add_http(
    "duplicate-first-wins",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw(), raw(title="Other")])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http("empty", pages={SCRIPT: response(script), "LIST:1": response(listing([]))})
add_http(
    "missing-title", pages={SCRIPT: response(script), "LIST:1": response(listing([raw(title="")]))}
)
add_http(
    "null-listing",
    pages={SCRIPT: response(script), "LIST:1": response({"data": {"widget": {"jobAdList": None}}})},
)
add_http(
    "partial-listing-data",
    pages={
        SCRIPT: response(script),
        "LIST:1": response({**listing([raw()]), "errors": [{"message": "optional resolver"}]}),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http(
    "graphql-errors-no-data",
    pages={SCRIPT: response(script), "LIST:1": response({"errors": [{"message": "schema drift"}]})},
)
add_http("malformed-listing", pages={SCRIPT: response(script), "LIST:1": response("bad")})
add_http(
    "listing-retry-401",
    pages={
        SCRIPT: response(script),
        "LIST:1": [response("busy", 401), response(listing([raw()]))],
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http(
    "listing-empty-retry",
    pages={
        SCRIPT: response(script),
        "LIST:1": [response(""), response(listing([raw()]))],
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
)
add_http(
    "detail-failure-teaser",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response("gone", 404),
    },
)
add_http(
    "detail-retry-403",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": [response("busy", 403), response(detail("1001", "<p>Full HTML</p>"))],
    },
)
add_http(
    "detail-exhaustion-teaser",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response("busy", 429),
    },
)
add_http(
    "detail-malformed-teaser",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response("bad"),
    },
)
add_http(
    "detail-empty-teaser",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", " ")),
    },
)
add_http(
    "partial-detail-data",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(
            {**detail("1001", "<p>Full HTML</p>"), "errors": [{"message": "optional resolver"}]}
        ),
    },
)
add_http(
    "listing-header-reserved",
    pages={SCRIPT: response(script), "LIST:1": response("bad", headers={"TDM-Reservation": "1"})},
    native_reserved=True,
)
add_http(
    "detail-header-reserved",
    pages={
        SCRIPT: response(script),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response("bad", headers={"TDM-Reservation": "1"}),
    },
    native_reserved=True,
)
add_http(
    "script-native-header-boundary",
    pages={
        SCRIPT: response(script, headers={"TDM-Reservation": "1"}),
        "LIST:1": response(listing([raw()])),
        "DETAIL:1001": response(detail("1001", "<p>Full HTML</p>")),
    },
    native_reserved=True,
)


async def collect_http():
    output = []
    for name, metadata, pages, native_reserved in http_cases:
        requests, counts = [], {}

        def handle(request, *, requests=requests, pages=pages, counts=counts):
            endpoint = str(request.url)
            payload = json.loads(request.content) if request.content else None
            key = endpoint
            if endpoint == GRAPHQL:
                assert payload["query"] in [alma._LISTING_QUERY, alma._DETAIL_QUERY]
                key = (
                    ("LIST:" + str(payload["variables"]["page"]))
                    if payload["query"] == alma._LISTING_QUERY
                    else ("DETAIL:" + str(payload["variables"]["jobId"]))
                )
            requests.append(
                {
                    "method": request.method,
                    "url": endpoint,
                    "key": key,
                    "body": payload,
                    "headers": {
                        k: request.headers[k]
                        for k in ["accept", "content-type", "origin", "referer", "x-api-key"]
                    }
                    if payload
                    else {},
                }
            )
            assert key in pages, "undeclared fixture request"
            index = counts.get(key, 0)
            counts[key] = index + 1
            r = pages[key]
            if isinstance(r, list):
                r = r[min(index, len(r) - 1)]
            return httpx.Response(r["status"], text=r["body"], headers=r["headers"])

        expected = {
            "error": False,
            "gone": False,
            "reserved": False,
            "truncated": False,
            "jobs": [],
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            try:
                result = await alma.discover(
                    {"board_url": ROOT + "jobs/", "metadata": metadata}, client
                )
                if isinstance(result, list):
                    jobs = result
                else:
                    jobs = list(result.jobs_by_url.values())
                    expected["truncated"] = result.truncated
                expected["jobs"] = [
                    {**asdict(j), "description": normalize_description_html(j.description)}
                    for j in jobs
                ]
            except Exception as exc:
                expected["error"] = True
                expected["gone"] = type(exc).__name__ == "BoardGoneError"
                expected["reserved"] = type(exc).__name__ == "TDMReservedError"
        output.append(
            {
                "name": name,
                "board_url": ROOT + "jobs/",
                "metadata": metadata,
                "pages": pages,
                "requests": requests,
                "expected": expected,
                "native_reserved": native_reserved,
            }
        )
    return output


out["http"] = asyncio.run(collect_http())
(HERE / "python_almacareer.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
queries = HERE.parent.parent / "api-sniffer-monitor" / "almacareer_queries.go"
queries.write_text(
    "// Generated from the legacy AlmaCareer queries by collect_almacareer.py.\n"
    "package apisniffer\n\n"
    + "\n".join(
        f"const {name} = {json.dumps(value)}"
        for name, value in [
            ("AlmaListingQuery", alma._LISTING_QUERY),
            ("AlmaDetailQuery", alma._DETAIL_QUERY),
        ]
    )
    + "\n"
)
print(json.dumps({key: len(value) for key, value in out.items()}))
