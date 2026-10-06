"""Freeze the actual MokaHR parser, identity and encrypted-listing contracts."""

from __future__ import annotations

import asyncio
import json
import runpy
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.monitor import MonitorResult
from src.core.monitors import mokahr
from src.processing.board import _classify_job_url, _prepare_discovered_sources
from src.shared.html_normalize import normalize_description_html

HERE = Path(__file__).parent
ns = runpy.run_path(str(HERE.parents[2] / "tests" / "test_mokahr.py"))
raw_job = ns["_raw_job"]
encrypt = ns["_encrypted_jobs"]
spa = ns["_spa_html"]
URL = "https://app.mokahr.com/social-recruitment/zte/47588"
IV = "0123456789abcdef"
KEY = "fedcba9876543210"
out = {"options": [], "bootstrap": [], "listing": [], "projection": []}
out["url_identity"] = []
for route in ["social-recruitment", "campus-recruitment", "social_apply", "campus_apply"]:
    for host in ["app.mokahr.com", "careers.example.com"]:
        board = f"https://{host}/{route}/fixture/123"
        sources = [board + "#/job/first", board + "#/job/second"]
        result = MonitorResult(urls=set(sources))
        accepted, dropped, _ = _prepare_discovered_sources(result, board, set())
        out["url_identity"].append(
            {
                "board": board,
                "sources": sources,
                "accepted": sorted(v[1] for v in accepted),
                "dropped": dropped,
            }
        )
for fragment in ["", "#0", "#/jobs", "#/job/", "#/job/bad/id", "#/job/%61"]:
    source = URL + fragment
    out["url_identity"].append(
        {"board": URL, "source": source, "reason": _classify_job_url(source, URL)}
    )
out["url_identity"].append(
    {
        "board": "https://example.com/careers",
        "source": "https://example.com/careers#/job/first",
        "reason": _classify_job_url(
            "https://example.com/careers#/job/first", "https://example.com/careers"
        ),
    }
)


def options(name, metadata=None, url=URL):
    config = {"org_id": "zte", "site_id": 47588, **(metadata or {})}
    row = {"name": name, "url": url, "metadata": config}
    try:
        sources = mokahr._configured_partitions(
            url, config, config["org_id"], mokahr._site_id(config["site_id"], field="test")
        )
        row["expected"] = [asdict(p) for p in sources]
    except (ValueError, TypeError):
        row["error"] = True
    out["options"].append(row)


options("shared")
options("campus", url="https://app.mokahr.com/campus_apply/zte/47588#/jobs")
options("custom", url="https://careers.example.com/social_recruitment/zte/47588")
options("fallback-route", url="https://careers.example.com/campus")
options("query", url=URL + "?from=portal")
options("standard-port", url="https://app.mokahr.com:443/social-recruitment/zte/47588")
options("wrong-route", {"site_id": 12})
options("boolean-site", {"site_id": True})
options("site-string", {"site_id": "47588"})
options("zero-site", {"site_id": 0})
options(
    "bounded-site",
    {"site_id": 999999999999},
    url="https://app.mokahr.com/social-recruitment/zte/999999999999",
)
options("cleartext", url=URL.replace("https:", "http:"))
options("credentials", url=URL.replace("https://", "https://user@"))
options("port", url=URL.replace(".com/", ".com:444/"))
partition = {"board_url": "https://careers.example.com/campus-recruitment/zte/123", "site_id": 123}
options("partitions", {"partitions": [partition]})
options("duplicate-site", {"partitions": [{"board_url": URL, "site_id": 47588}]})
options("contradictory-partition", {"partitions": [{**partition, "site_id": 124}]})
options("unbounded-partitions", {"partitions": [partition] * 16})
options("partition-extra-key", {"partitions": [{**partition, "token": "unused"}]})
options("partition-object", {"partitions": {}})

p = mokahr._configured_partitions(URL, {}, "zte", 47588)[0]


def bootstrap(name, value):
    row = {"name": name, "page": value}
    import re
    from html import unescape

    match = re.search(r'id="init-data"[^>]*value="([^"]*)"', value)
    init = json.loads(unescape(match.group(1))) if match else None
    try:
        iv, cities = mokahr._validated_init_data(init, p)
        row["iv"], row["cities"] = iv, cities
    except (ValueError, RuntimeError):
        row["error"] = True
    out["bootstrap"].append(row)


bootstrap("exact", spa(IV))
bootstrap("foreign-org", spa(IV, org_id="other"))
bootstrap("foreign-site", spa(IV, site_id=47589))
bootstrap("foreign-type", spa(IV, site_type="camp"))
bootstrap("wrong-iv-length", spa(IV[:-1]))
bootstrap("non-ascii-iv", spa("é" * 16))
bootstrap("missing", "<html></html>")


def listing(name, envelope):
    row = {"name": name, "envelope": envelope}
    try:
        jobs, total = mokahr._validated_list_payload(envelope, IV, p)
        row["jobs"], row["total"] = jobs, total
    except ValueError:
        row["error"] = True
    out["listing"].append(row)


listing("valid", encrypt([raw_job("a")], KEY, IV))
listing("empty", encrypt([], KEY, IV))
listing("foreign-org", encrypt([raw_job("a")], KEY, IV, org_id="other"))
listing("unsuccessful", encrypt([raw_job("a")], KEY, IV, success=False))
listing("invalid-code", encrypt([raw_job("a")], KEY, IV, code=1))
listing("boolean-total", encrypt([raw_job("a")], KEY, IV, total=True))
listing("negative-total", encrypt([], KEY, IV, total=-1))
listing("missing-crypto", {})
listing("invalid-base64", {"data": "bad!", "necromancer": KEY})
listing("wrong-key", {**encrypt([raw_job("a")], KEY, IV), "necromancer": "too-short"})


def project(name, raw, cities=None):
    cities = cities or {440300: "深圳市", 110000: "北京市"}
    result = mokahr._parse_job(raw, "zte", 47588, cities)
    expected = asdict(result)
    for key in ["language", "localizations", "source_identity"]:
        expected.pop(key, None)
    out["projection"].append({"name": name, "raw": raw, "cities": cities, "expected": expected})


project("full", raw_job("a"))
project(
    "mixed-locations",
    raw_job(
        "a",
        locations=[
            {"cityId": 110105, "country": "中国"},
            "Paris",
            "Paris",
            {"provinceName": "浙江", "country": "中国"},
            None,
        ],
    ),
)
project(
    "name-precedence",
    raw_job("a", locations=[{"cityId": 440300, "cityName": "Explicit", "country": "China"}]),
)
project("missing-fields", {"id": "a", "orgId": "zte", "title": "Engineer"})
project("string-metadata", raw_job("a", department="Research", education="", zhineng="Engineering"))
project("experience-open", raw_job("a", maxExperience=None))
project("salary-open", raw_job("a", minSalary=0, maxSalary=2.5))
project(
    "boolean-numbers",
    raw_job("a", minExperience=True, maxExperience=False, minSalary=True, maxSalary=False),
)
for unit in range(13):
    project(f"salary-unit-{unit}", raw_job("a", salaryUnit=unit))
API = "https://app.mokahr.com/api/outer/ats-apply/website/jobs/v2"
http_cases = []


def response(body, status=200):
    return {
        "body": json.dumps(body, ensure_ascii=False) if isinstance(body, dict) else body,
        "status": status,
        "headers": {},
    }


def add_http(name, rows=None, pages=None, metadata=None, url=URL):
    base_pages = {URL: response(spa(IV)), API: response(encrypt(rows or [raw_job("a")], KEY, IV))}
    http_cases.append(
        (
            name,
            url,
            {"org_id": "zte", "site_id": 47588, "scraper_type": "skip", **(metadata or {})},
            pages or base_pages,
        )
    )


add_http("complete")
add_http(
    "bootstrap-cookie",
    pages={
        URL: {**response(spa(IV)), "headers": {"Set-Cookie": "site=one; Path=/; Secure"}},
        API: response(encrypt([raw_job("a")], KEY, IV)),
    },
)
add_http(
    "explicit-active", [raw_job("a"), raw_job("b", status="closed"), raw_job("c", status="pause")]
)
add_http("uppercase-open", [raw_job("a", status="OPEN")])
add_http("unknown-status", [raw_job("a", status="unknown")])
add_http("missing-status", [raw_job("a", status=None)])
add_http("foreign-row", [raw_job("a", orgId="other")])
add_http("invalid-id", [raw_job("bad/id")])
add_http("missing-title", [raw_job("a", title=" ")])
add_http("duplicate-snapshot", [raw_job("a"), raw_job("a")])
add_http("page-gone", pages={URL: response("gone", 404)})
add_http("page-shutdown", pages={URL: response("<title>当前网页已关停</title>")})
add_http("foreign-bootstrap", pages={URL: response(spa(IV, org_id="other"))})
add_http("missing-bootstrap", pages={URL: response("<html></html>")})
add_http("api-gone", pages={URL: response(spa(IV)), API: response("gone", 404)})
add_http("bad-json", pages={URL: response(spa(IV)), API: response("bad")})
add_http(
    "unsuccessful-envelope",
    pages={URL: response(spa(IV)), API: response(encrypt([], KEY, IV, success=False))},
)
add_http("zero-confirmed", pages={URL: response(spa(IV)), API: response(encrypt([], KEY, IV))})
add_http(
    "zero-not-converged",
    pages={
        URL: response(spa(IV)),
        API: [response(encrypt([], KEY, IV)), response(encrypt([raw_job("a")], KEY, IV))],
    },
)
first = [raw_job(str(n)) for n in range(50)]
add_http(
    "pagination",
    pages={
        URL: response(spa(IV)),
        API: [
            response(encrypt(first, KEY, IV, total=51)),
            response(encrypt([raw_job("last")], KEY, IV, total=51)),
        ],
    },
)
add_http(
    "later-gone",
    pages={
        URL: response(spa(IV)),
        API: [response(encrypt(first, KEY, IV, total=51)), response("gone", 404)],
    },
)
add_http(
    "early-short",
    pages={URL: response(spa(IV)), API: response(encrypt([raw_job("a")], KEY, IV, total=51))},
)
add_http(
    "later-empty",
    pages={
        URL: response(spa(IV)),
        API: [
            response(encrypt(first, KEY, IV, total=51)),
            response(encrypt([], KEY, IV, total=51)),
        ],
    },
)
add_http(
    "count-restart",
    pages={
        URL: response(spa(IV)),
        API: [
            response(encrypt(first, KEY, IV, total=51)),
            response(encrypt([raw_job("last")], KEY, IV, total=52)),
            response(encrypt([raw_job("recovered")], KEY, IV, total=1)),
        ],
    },
)
add_http(
    "duplicate-restart",
    pages={
        URL: response(spa(IV)),
        API: [
            response(encrypt(first, KEY, IV, total=51)),
            response(encrypt([raw_job("0")], KEY, IV, total=51)),
            response(encrypt([raw_job("recovered")], KEY, IV, total=1)),
        ],
    },
)
add_http(
    "too-large-page",
    pages={
        URL: response(spa(IV)),
        API: response(encrypt([*first, raw_job("last")], KEY, IV, total=51)),
    },
)
PARTITION_URL = "https://careers.example.com/campus-recruitment/zte/123"
PARTITION_API = "https://careers.example.com/api/outer/ats-apply/website/jobs/v2"
add_http(
    "ordered-partition-union",
    metadata={"partitions": [{"board_url": PARTITION_URL, "site_id": 123}]},
    pages={
        URL: response(spa(IV)),
        API: response(encrypt([raw_job("a", title="Primary")], KEY, IV)),
        PARTITION_URL: response(spa(IV, site_id=123, site_type="camp")),
        PARTITION_API: response(
            encrypt([raw_job("a", title="Alias"), raw_job("b", title="Campus")], KEY, IV)
        ),
    },
)
add_http(
    "partition-failure-discards-prefix",
    metadata={"partitions": [{"board_url": PARTITION_URL, "site_id": 123}]},
    pages={
        URL: response(spa(IV)),
        API: response(encrypt([raw_job("a")], KEY, IV)),
        PARTITION_URL: response(spa(IV, site_id=123, site_type="camp")),
        PARTITION_API: response("busy", 429),
    },
)


async def collect_http():
    output = []
    for name, url, metadata, pages in http_cases:
        requests, counts = [], {}

        def handle(request, *, requests=requests, pages=pages, counts=counts):
            endpoint = str(request.url)
            requests.append(
                {
                    "method": request.method,
                    "url": endpoint,
                    "body": json.loads(request.content) if request.content else None,
                    "cookie": request.headers.get("cookie", ""),
                }
            )
            if endpoint not in pages:
                raise AssertionError("undeclared fixture request")
            index = counts.get(endpoint, 0)
            counts[endpoint] = index + 1
            r = pages[endpoint]
            if isinstance(r, list):
                r = r[min(index, len(r) - 1)]
            return httpx.Response(r["status"], text=r["body"], headers=r.get("headers", {}))

        expected = {"error": False, "gone": False, "truncated": False, "jobs": []}
        async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
            try:
                result = await mokahr.discover({"board_url": url, "metadata": metadata}, client)
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
        output.append(
            {
                "name": name,
                "board_url": url,
                "metadata": metadata,
                "pages": pages,
                "requests": requests,
                "expected": expected,
            }
        )
    return output


out["http"] = asyncio.run(collect_http())
(HERE / "python_mokahr.json").write_text(json.dumps(out, ensure_ascii=False, indent=2) + "\n")
print(json.dumps({key: len(value) for key, value in out.items()}))
