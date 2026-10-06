"""Freeze actual Inline HTTP requests, fallback order and failure contracts offline."""

from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import asdict
from pathlib import Path

import httpx

import src.core.monitors.inline as inline
from src.core.monitor import MonitorResult
from src.shared.html_normalize import _normalize_description_html_python
from src.shared.http import DEFAULT_ACCEPT

HERE = Path(__file__).parent
ROOT = "https://example.com/careers"
HTML = "<h2>Senior Software Engineer</h2><p>Go in Zurich.</p>"
base = {
    "scraper_type": "skip",
    "steps": [{"tag": "h2", "field": "title"}, {"tag": "p", "field": "description", "html": True}],
}
original_fetch = inline.fetch_text_page_with_retry


async def no_sleep(_delay):
    return None


async def bounded_fetch(*args, **kwargs):
    return await original_fetch(*args, **kwargs, sleep=no_sleep)


inline.fetch_text_page_with_retry = bounded_fetch


def page(body=HTML, status=200, **headers):
    return {"body": body, "status": status, "headers": headers}


async def run(name, overrides, pages):
    metadata = {**copy.deepcopy(base), **overrides}
    requests = []
    counts = {}

    async def handle(request):
        url = str(request.url)
        requests.append(
            {
                "url": url,
                "cookie": request.headers.get("cookie", ""),
                "accept": request.headers.get("accept", ""),
            }
        )
        index = counts.get(url, 0)
        counts[url] = index + 1
        response = pages[url]
        if isinstance(response, list):
            response = response[min(index, len(response) - 1)]
        return httpx.Response(
            response["status"], text=response["body"], headers=response["headers"], request=request
        )

    result = {"error": False, "jobs": [], "truncated": False}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handle),
        headers={"Accept": DEFAULT_ACCEPT},
    ) as client:
        try:
            found = await inline.discover({"board_url": ROOT, "metadata": metadata}, client)
            if isinstance(found, MonitorResult):
                result["truncated"] = found.truncated
                found = list((found.jobs_by_url or {}).values())
            keys = {
                "url",
                "title",
                "description",
                "locations",
                "employment_type",
                "job_location_type",
                "date_posted",
                "metadata",
                "extras",
            }
            for job in found:
                fields = {k: v for k, v in asdict(job).items() if k in keys}
                fields["description"] = _normalize_description_html_python(fields["description"])
                result["jobs"].append(fields)
        except Exception:
            result["error"] = True
    return {
        "name": name,
        "board_url": ROOT,
        "metadata": metadata,
        "pages": pages,
        "requests": requests,
        "expected": result,
    }


async def main():
    fixtures = [
        ("one-page", {}, {ROOT: page()}),
        (
            "same-origin-redirect-cookie",
            {},
            {
                ROOT: page("", 302, Location="/ready", **{"Set-Cookie": "session=owned; Path=/"}),
                "https://example.com/ready": page(),
            },
        ),
        (
            "foreign-public-redirect",
            {},
            {
                ROOT: page("", 302, Location="https://other.example/jobs"),
                "https://other.example/jobs": page(),
            },
        ),
        ("permanent403", {}, {ROOT: page("blocked", 403)}),
        ("transient403", {"transient_403": True}, {ROOT: [page("blocked", 403), page()]}),
        ("transient429", {}, {ROOT: [page("busy", 429), page()]}),
        ("transient530", {}, {ROOT: [page("busy", 530), page()]}),
        ("empty-200-retry", {}, {ROOT: [page(""), page()]}),
        ("empty-exhausted", {}, {ROOT: page("")}),
        ("404-failure", {}, {ROOT: page("missing", 404)}),
        ("410-failure", {}, {ROOT: page("missing", 410)}),
        ("bounded-attempts", {"transport_attempts": 2}, {ROOT: page("busy", 500)}),
        (
            "contains-fallback",
            {
                "fetch_urls": [ROOT, "https://example.com/alternate"],
                "fetch_contains": "Senior Software Engineer",
            },
            {ROOT: page("no marker"), "https://example.com/alternate": page()},
        ),
        (
            "404-fallback",
            {"fetch_urls": [ROOT, "https://example.com/alternate"]},
            {ROOT: page("missing", 404), "https://example.com/alternate": page()},
        ),
        (
            "json-wrapper",
            {"fetch_json_path": "[0].content.rendered"},
            {ROOT: page(json.dumps([{"content": {"rendered": HTML}}]))},
        ),
        (
            "json-empty-failure",
            {"fetch_json_path": "[0].content.rendered"},
            {ROOT: page('[{"content":{"rendered":""}}]')},
        ),
        (
            "json-malformed-fallback",
            {"fetch_json_path": "html", "fetch_urls": [ROOT, "https://example.com/alternate"]},
            {
                ROOT: page("invalid JSON"),
                "https://example.com/alternate": page(json.dumps({"html": HTML})),
            },
        ),
        (
            "public-header",
            {"fetch_urls": [{"url": ROOT, "headers": {"Accept": "text/html"}}]},
            {ROOT: page()},
        ),
        (
            "public-header-same-origin-redirect",
            {"fetch_urls": [{"url": ROOT, "headers": {"Accept": "text/html"}}]},
            {
                ROOT: page("", 302, Location="/ready", **{"Set-Cookie": "private=never; Path=/"}),
                "https://example.com/ready": page(),
            },
        ),
        (
            "public-header-foreign-refusal",
            {"fetch_urls": [{"url": ROOT, "headers": {"Accept": "text/html"}}]},
            {ROOT: page("", 302, Location="https://other.example/jobs")},
        ),
    ]
    cases = [await run(*f) for f in fixtures]
    (HERE / "python_inline_transport.json").write_text(
        json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n"
    )
    print(json.dumps({"actual_python_cases": len(cases)}))


asyncio.run(main())
