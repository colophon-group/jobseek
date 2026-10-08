"""Freeze actual LinkedIn, JazzHR and Taleo Enterprise detail fields."""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from urllib.parse import quote

import httpx

from src.core.scrapers import jazzhr, linkedin, taleo
from src.runtime import dom_go_parse
from src.shared import http_retry

arguments = argparse.ArgumentParser()
arguments.add_argument("--dom-parser", type=Path, required=True)
options = arguments.parse_args()
if not options.dom_parser.is_file():
    arguments.error("the deployed DOM parser must be built before capturing expected fields")
dom_go_parse._BINARY = str(options.dom_parser.resolve())

root = Path(__file__).resolve().parents[3]


def helpers(file, names, namespace):
    tree = ast.parse((root / "tests" / file).read_text())
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    assert len(nodes) == len(names)
    exec(compile(ast.Module(body=nodes, type_ignores=[]), file, "exec"), namespace)
    return namespace


t = helpers(
    "test_taleo_scraper.py",
    {"_detail_html", "_wipo_detail_html", "_wipo_internship_detail_html"},
    {"quote": quote},
)
j = helpers("test_jazzhr.py", {"_jsonld_detail"}, {"json": json})
tree = ast.parse((root / "tests/test_linkedin.py").read_text())
node = next(
    n
    for n in tree.body
    if isinstance(n, ast.Assign)
    and any(isinstance(v, ast.Name) and v.id == "DETAIL_HTML" for v in n.targets)
)
namespace = {"COMPANY_ID": "109559449"}
exec(compile(ast.Module(body=[node], type_ignores=[]), "test_linkedin.py", "exec"), namespace)
li = namespace["DETAIL_HTML"]

old = (
    '<h1 class="job_title">Engineer</h1><h3 class="job_meta">Remote - Full Time</h3>'
    "<div><p>Build things.</p></div><button>Apply Now</button>"
)
inputs = {
    "linkedin": [
        ("full", li),
        ("empty", ""),
        ("challenge", "<html>Sign in</html>"),
        (
            "nested-text",
            li.replace("Manager/Senior Manager, Regulatory Affairs", " Hello <b>World</b> Again "),
        ),
        ("remote", li.replace("Boston, MA (Hybrid)", "Boston, MA (Remote)")),
        ("remote-hybrid", li.replace("Boston, MA (Hybrid)", "Remote Hybrid")),
        (
            "empty-description",
            li.replace("<p>Lead regulatory strategy.</p><ul><li>File submissions</li></ul>", " "),
        ),
        ("no-title", li.replace("top-card-layout__title", "unused")),
        ("no-location", li.replace("topcard__flavor--bullet", "unused")),
        ("empty-criteria", li.replace("description__job-criteria-text", "unused")),
        (
            "untrusted-company",
            li.replace("https://www.linkedin.com/company/", "https://evil.test/company/"),
        ),
        (
            "uppercase-company",
            li.replace("https://www.linkedin.com/company/", "https://WWW.LINKEDIN.COM/company/"),
        ),
        (
            "duplicate-employment",
            li + '<li class="description__job-criteria-item">'
            '<h3 class="description__job-criteria-subheader">Employment type</h3>'
            '<span class="description__job-criteria-text">Part-time</span></li>',
        ),
        (
            "entity-description",
            li.replace(
                "<p>Lead regulatory strategy.</p>",
                '<p data-x="A &amp; B">A &amp; B&nbsp;<br>Next</p>',
            ),
        ),
    ],
    "jazzhr": [
        ("complete-jsonld", j["_jsonld_detail"]()),
        ("old-theme", old),
        ("department", old.replace("Remote - Full Time", "Success - Remote - Full Time")),
        ("hybrid", old.replace("Remote - Full Time", "Hybrid - Contract")),
        ("single-meta", old.replace("Remote - Full Time", "Remote")),
        ("empty-meta", old.replace("Remote - Full Time", "")),
        ("no-meta", old.replace('class="job_meta"', 'class="unused"')),
        ("missing-title", old.replace('class="job_title"', 'class="unused"')),
        ("blank", ""),
        ("challenge", "<title>Just a moment</title>"),
        (
            "jsonld-title-only",
            '<script type="application/ld+json">'
            '{"@type":"JobPosting","title":"JSON title"}</script>' + old,
        ),
        (
            "jsonld-description-only",
            '<script type="application/ld+json">{"@type":"JobPosting",'
            '"description":"<p>JSON description</p>"}</script>' + old,
        ),
        ("stop-boundary", old.replace("</button>", "</button><p>Do not include.</p>")),
        ("nested-title", old.replace("Engineer", " Hello <b>World</b> Again ")),
    ],
    "taleo": [
        ("enterprise", t["_detail_html"]()),
        ("wipo-staff", t["_wipo_detail_html"]()),
        ("wipo-intern", t["_wipo_internship_detail_html"]()),
        ("encoded-title", t["_wipo_detail_html"](title="Chief Officer %26 Controller")),
        ("unicode-title", t["_detail_html"](title="Ingénieur 日本語")),
        ("invalid-percent-title", t["_detail_html"](title="Title %zz %FF%FE")),
        (
            "unicode-escape",
            t["_detail_html"]().replace("Pilot\\'s Assistant", "Pil\\u00f6t\\x27s Assistant"),
        ),
        ("unknown-escape", t["_detail_html"]().replace("Pilot\\'s Assistant", "Pil\\qot")),
        (
            "line-continuation",
            t["_detail_html"]().replace("Pilot\\'s Assistant", "Pilot\\\r\nAssistant"),
        ),
        ("empty", ""),
        ("missing-marker", "<p>No payload</p>"),
        (
            "non-array",
            "api.fillList('requisitionDescriptionInterface','descRequisition', variable)",
        ),
        (
            "short-array",
            "api.fillList('requisitionDescriptionInterface','descRequisition',['one','two'])",
        ),
        ("non-string", "api.fillList('requisitionDescriptionInterface','descRequisition',[42])"),
        ("invalid-hex", t["_detail_html"]().replace("Pilot\\'s Assistant", "Bad\\xQ1")),
        ("unterminated", "api.fillList('requisitionDescriptionInterface','descRequisition',['one"),
        (
            "invalid-separator",
            "api.fillList('requisitionDescriptionInterface','descRequisition',['one';'two'])",
        ),
        (
            "value-cap",
            "api.fillList('requisitionDescriptionInterface','descRequisition',["
            + ",".join("'x'" for _ in range(129))
            + "])",
        ),
        ("mixed-quotes", t["_detail_html"]().replace("'252963'", '"252963"')),
        ("empty-title", t["_detail_html"](title="")),
        ("remote-marker", t["_detail_html"]().replace("hybrid", "remote")),
        ("onsite-marker", t["_detail_html"]().replace("hybrid", "on-site")),
        (
            "first-marker-invalid",
            "api.fillList('requisitionDescriptionInterface','descRequisition', variable);"
            + t["_detail_html"](),
        ),
    ],
}
cases = []
for provider, rows in inputs.items():
    parser = {"linkedin": linkedin, "jazzhr": jazzhr, "taleo": taleo}[provider].parse_html
    for name, body in rows:
        case = {"provider": provider, "name": name, "body": body}
        try:
            case.update(content=asdict(parser(body, {})), failed=False)
        except Exception:
            case.update(content=None, failed=True)
        cases.append(case)

identities = []
for provider, source in [
    ("linkedin", "https://www.linkedin.com/jobs/view/title-123"),
    ("linkedin", "https://ch.linkedin.com/jobs/view/title-123/?track=1"),
    ("linkedin", "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/123"),
    ("linkedin", "https://www.linkedin.com/jobs/view/１２３"),
    ("linkedin", "https://www.linkedin.com/company/acme/jobs"),
    ("jazzhr", "https://fixture.applytojob.com/apply/jobs/details/ABC_123?ref=x"),
    ("jazzhr", "https://fixture.applytojob.com:443/apply/jobs/details/ABC_123"),
    ("jazzhr", "https://user@fixture.applytojob.com/apply/jobs/details/ABC_123"),
    ("jazzhr", "https://www.applytojob.com/apply/jobs/details/ABC_123"),
    ("jazzhr", "https://fixture.applytojob.com/apply/jobs"),
    ("taleo", "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=17204&lang=en"),
    ("taleo", "https://easyjet.taleo.net:443/careersection/2/jobdetail.ftl?job=17204"),
    ("taleo", "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=İ12"),
    ("taleo", "https://phe.tbe.taleo.net/careersection/2/jobdetail.ftl?job=12"),
    ("taleo", "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=12#fragment"),
    ("taleo", "https://easyjet.taleo.net/careersection/2/jobdetail.ftl?job=bad/value"),
]:
    valid = (
        bool(linkedin._job_id_from_url(source))
        if provider == "linkedin"
        else jazzhr._tenant_from_url(source) is not None and "/apply/jobs/details/" in source
        if provider == "jazzhr"
        else taleo._detail_url(source)
    )
    endpoint = (
        "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/"
        + linkedin._job_id_from_url(source)
        if provider == "linkedin" and valid
        else source
    )
    identities.append(
        {"provider": provider, "source": source, "valid": valid, "endpoint": endpoint}
    )


async def no_sleep(_delay):
    return None


async def capture_requests():
    # Both helpers retain their original retry logic; only the wall-clock wait
    # is replaced. The response helper binds its default sleep at import time.
    http_retry.asyncio.sleep = no_sleep
    http_retry.fetch_response_with_status_retries.__kwdefaults__["sleep"] = no_sleep
    results = []
    for provider, module in {"linkedin": linkedin, "jazzhr": jazzhr, "taleo": taleo}.items():
        source = {
            "linkedin": "https://ch.linkedin.com/jobs/view/engineer-123?track=x",
            "jazzhr": "https://fixture.applytojob.com/apply/jobs/details/ABC_123",
            "taleo": "https://fixture.taleo.net/careersection/2/jobdetail.ftl?job=123",
        }[provider]
        schedules = [
            (str(status), [status])
            for status in [
                200,
                201,
                204,
                301,
                400,
                401,
                403,
                404,
                410,
                408,
                425,
                429,
                500,
                503,
                999,
            ]
        ]
        schedules += [
            ("empty", [200]),
            ("transport", [0]),
            ("recover-403", [403, 200]),
            ("recover-429", [429, 200]),
            ("recover-503", [503, 200]),
            ("alternating", [429, 503, 429, 503, 200]),
        ]
        for name, statuses in schedules:
            requests = []
            body = "" if name == "empty" else inputs[provider][0][1]

            def handler(request, requests=requests, statuses=statuses, body=body):
                index = len(requests)
                requests.append({"method": request.method, "url": str(request.url)})
                status = statuses[min(index, len(statuses) - 1)]
                if not status:
                    raise httpx.ConnectError("fixture transport", request=request)
                return httpx.Response(status, text=body, request=request)

            case = {
                "provider": provider,
                "name": name,
                "source": source,
                "statuses": statuses,
                "body": body,
            }
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                try:
                    content = await module.scrape(source, {}, client)
                    case.update(content=asdict(content), failed=False)
                except Exception:
                    case.update(content=None, failed=True)
            case["requests"] = requests
            results.append(case)
    return results


requests = asyncio.run(capture_requests())
Path(__file__).with_name("python_static_provider_detail.json").write_text(
    json.dumps({"cases": cases, "identities": identities, "requests": requests}, indent=2) + "\n"
)
print(
    f"Captured {len(cases)} parsers, {len(identities)} identities, {len(requests)} request traces"
)
