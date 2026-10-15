"""Freeze original offline DOM read contracts; run with both Go gates disabled."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.scrapers.dom import scrape


async def main() -> None:
    cases = []
    source = "https://jobs.company.example/positions/42"
    target = "https://department.company.example/jobs/full-role"
    base = {
        "steps": [
            {"tag": "h1", "field": "title"},
            {"tag": "div", "attr": "class=summary", "field": "description", "html": True},
        ],
        "defaults_by_url": {source: {"locations": ["Zurich"], "language": "en"}},
        "linked_description": {
            "selector": "a.full-description",
            "allowed_host_suffixes": [".company.example"],
            "min_chars": 50,
            "scope": ".job-description",
            "steps": [{"tag": "p", "field": "description", "html": True, "to_end": True}],
        },
    }
    for mode in [
        "complete-linked",
        "complete-original",
        "alternate-linked",
        "foreign",
        "duplicate-link",
        "short-linked",
        "redirect-foreign",
        "redirect-allowed",
        "reserved",
        "status-before-policy",
    ]:
        config = json.loads(json.dumps(base))
        read = source
        if mode == "alternate-linked":
            config["fetch_url_transform"] = {
                "find": "^https://jobs.company.example/",
                "replace": "https://read.company.example/",
            }
            read = "https://read.company.example/positions/42"
        href = target if mode != "foreign" else "https://outside.example/full"
        pointer = (
            '<h1>Research Professor</h1><div class="summary">'
            f'<a class="full-description" href="{href}">Learn more</a></div>'
        )
        if mode == "complete-original":
            config["steps"][1] = {"tag": "p", "field": "description", "html": True}
            pointer = (
                '<h1>Research Professor</h1><div class="summary"><p>'
                + "Complete original role. " * 10
                + "</p></div>"
            )
        if mode == "duplicate-link":
            pointer += f'<a class="full-description" href="{target}">Duplicate</a>'
        linked = (
            '<h1>Keep original title</h1><div class="job-description">'
            "<p>Lead a multidisciplinary research program and mentor faculty trainees.</p>"
            "<p>Publish original scholarship and collaborate across departments.</p></div>"
        )
        if mode == "short-linked":
            linked = '<div class="job-description"><p>Short</p></div>'
        exchanges = [{"url": read, "status": 200, "headers": {}, "body": pointer}]
        if mode in {"redirect-foreign", "redirect-allowed"}:
            next_url = (
                "https://outside.example/full"
                if mode == "redirect-foreign"
                else "https://other.company.example/full"
            )
            exchanges.append(
                {"url": target, "status": 302, "headers": {"Location": next_url}, "body": ""}
            )
            if mode == "redirect-allowed":
                exchanges.append({"url": next_url, "status": 200, "headers": {}, "body": linked})
        else:
            exchanges.append(
                {
                    "url": target,
                    "status": 410 if mode == "status-before-policy" else 200,
                    "headers": {"TDM-Reservation": "1"}
                    if mode in {"reserved", "status-before-policy"}
                    else {},
                    "body": linked,
                }
            )
        requested = []

        def handler(
            request: httpx.Request, requested=requested, exchanges=exchanges
        ) -> httpx.Response:
            requested.append(str(request.url))
            response = next(x for x in exchanges if x["url"] == str(request.url))
            return httpx.Response(
                response["status"],
                headers=response["headers"],
                text=response["body"],
                request=request,
            )

        expected = None
        error = None
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                expected = asdict(await scrape(source, config, client))
            except Exception as exc:
                error = type(exc).__name__
        cases.append(
            {
                "name": mode,
                "source": source,
                "config": config,
                "exchanges": exchanges,
                "requested": requested,
                "expected": expected,
                "error": error,
            }
        )
    Path(__file__).with_name("python_read_variants.json").write_text(
        json.dumps(cases, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
