"""Freeze actual Recruiterbox, Paycor and VeryEast parser and HTTP contracts."""

from __future__ import annotations

import ast
import asyncio
import json
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core import enum_normalize
from src.core.scrapers import paycor, recruiterbox, veryeast
from src.shared import http_retry

root = Path(__file__).resolve().parents[3]


def original(name, symbol):
    tree = ast.parse((root / "tests" / name).read_text())
    nodes = [
        n
        for n in tree.body
        if isinstance(n, ast.FunctionDef)
        and n.name == symbol
        or isinstance(n, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == symbol for t in n.targets)
    ]
    assert len(nodes) == 1
    ns = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), name, "exec"), ns)
    return ns[symbol]


bodies = {
    "paycor": original("test_paycor.py", "DETAIL_HTML"),
    "recruiterbox": original("test_recruiterbox_scraper.py", "_detail")(),
    "veryeast": original("test_veryeast_scraper.py", "HTML"),
}
sources = {
    "paycor": "https://recruitingbypaycor.com/career/JobIntroduction.action?id=abc",
    "recruiterbox": "https://acme.hire.trakstar.com/jobs/abc123/",
    "veryeast": "https://job.veryeast.cn/8018193/2836817",
}
modules = {"paycor": paycor, "recruiterbox": recruiterbox, "veryeast": veryeast}
cases = []
for provider, body in bodies.items():
    variants = [
        ("complete", body),
        ("empty", ""),
        ("challenge", "<h1>Please sign in</h1>"),
        (
            "nested-title",
            body.replace("Systems Engineer", "Hello <b>World</b> Again")
            .replace("Field Sales Representative</div>", "Hello <b>World</b> Again</div>")
            .replace("Movement Coach 健身教练", "Hello <b>World</b> Again"),
        ),
        ("entities", body.replace("<p>", '<p data-x="A &amp; B">A &amp; B&nbsp;')),
    ]
    if provider == "paycor":
        variants += [
            (key, body.replace(key, "unused"))
            for key in (
                "gnewtonJobPosition",
                "gnewtonJobLocationInfo",
                "gnewtonJobID",
                "gnewtonJobOpening",
                "gnewtonJobDescriptionText",
            )
        ]
    if provider == "recruiterbox":
        variants += [
            ("no-location", body.replace("meta-job-location-", "unused-")),
            ("no-opening", body.replace("opening-info", "unused")),
            ("no-description", body.replace("jobdesciption", "unused")),
        ]
        variants += [
            (word, body.replace("Fully remote", word))
            for word in (
                "Hybrid",
                "On-site",
                "Onsite (5 Days per Week)",
                "Remote Hybrid",
                "remote wherever",
                "unknown",
            )
        ]
    if provider == "veryeast":
        variants += [
            ("no-description", body.replace('class="describe"', 'class="unused"')),
            ("no-fields", body.replace("<li>", "<p>").replace("</li>", "</p>")),
            (
                "extra-sections",
                body.replace(
                    "</div>\n</div>",
                    "<h4>Ignore heading</h4><section>Final section</section></div>\n</div>",
                ),
            ),
        ]
        variants += [
            (word, body.replace("全职", word))
            for word in (
                "实习",
                "实习生",
                "Internship",
                "合同制",
                "兼职",
                "unknown",
                "Contract temporary",
                "",
            )
        ]
    for name, html in variants:
        cases.append(
            {
                "provider": provider,
                "name": name,
                "body": html,
                "content": asdict(modules[provider].parse_html(html)),
            }
        )


async def main():
    requests = []

    async def no_sleep(_):
        pass

    http_retry.asyncio.sleep = no_sleep
    for provider, module in modules.items():
        for statuses in (
            [200],
            [201],
            [204],
            [400],
            [404],
            [410],
            [403],
            [429, 200],
            [503, 200],
            [500, 500, 500],
            [0, 200],
        ):
            calls = []

            def handle(request, calls=calls, statuses=statuses, provider=provider):
                i = len(calls)
                calls.append({"method": request.method, "url": str(request.url)})
                status = statuses[min(i, len(statuses) - 1)]
                if status == 0:
                    raise httpx.ConnectError("fixture", request=request)
                return httpx.Response(status, text=bodies[provider], request=request)

            content = None
            failed = False
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                try:
                    content = asdict(await module.scrape(sources[provider], {}, client))
                except Exception:
                    failed = True
            requests.append(
                {
                    "provider": provider,
                    "name": "-".join(map(str, statuses)),
                    "source": sources[provider],
                    "statuses": statuses,
                    "body": bodies[provider],
                    "requests": calls,
                    "content": content,
                    "failed": failed,
                }
            )
    result = {"cases": cases, "requests": requests, "sources": sources}
    (Path(__file__).parent / "python_final_static_detail.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    )
    rules = {
        "location_types": enum_normalize._JOB_LOCATION_TYPE_MAP,
        "intern_signals": sorted(enum_normalize._INTERNSHIP_LEVEL_SIGNALS),
    }
    (Path(__file__).parents[1] / "final_static_detail_rules.json").write_text(
        json.dumps(rules, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
    )


asyncio.run(main())
