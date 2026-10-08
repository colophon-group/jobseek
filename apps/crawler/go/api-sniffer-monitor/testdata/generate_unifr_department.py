"""Freeze the actual Unifr deadline, accordion, and PDF-link contracts."""

from __future__ import annotations

import asyncio
import importlib.util
import json
from dataclasses import asdict
from datetime import date
from pathlib import Path

import httpx

from src.core.monitors import unifr

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "unifr_reference_tests", root / "tests/test_unifr_monitor.py"
)
assert spec is not None and spec.loader is not None
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def options(source):
    return dict(
        kind="accordion",
        url=source.url,
        suffix=source.page_title_suffix,
        heading=source.heading,
        expected_ids=sorted(source.expected_ids),
        excluded_central_ids=source.excluded_central_ids,
        selector=source.list_selector,
        deadline_required=sorted(source.deadline_required),
        immediately_available=sorted(source.immediately_available),
    )


async def freeze():
    cases = []
    source = unifr._ACCORDION_SOURCES["ami"]
    for name in (
        "complete",
        "expired-deadline",
        "ambiguous-deadline",
        "missing-deadline",
        "missing-currentness",
        "missing-panel",
        "duplicate-id",
        "drift",
        "zero",
        "wrong-owner",
        "pagination",
    ):
        items = [
            ("styleguide-2-1", "Researcher", "Role available immediately."),
            ("styleguide-2-2", "Student", "Apply by September 1st, 2050."),
        ]
        if name == "expired-deadline":
            items[1] = (items[1][0], items[1][1], "Apply by September 1st, 2020.")
        elif name == "ambiguous-deadline":
            items[1] = (
                items[1][0],
                items[1][1],
                "Apply by September 1st, 2050 or by October 1, 2050",
            )
        elif name == "missing-deadline":
            items[1] = (items[1][0], items[1][1], "No date")
        elif name == "missing-currentness":
            items[0] = (items[0][0], items[0][1], "A role")
        elif name == "duplicate-id":
            items[1] = items[0]
        elif name == "drift":
            items[1] = ("unexpected", "Student", "Apply by September 1st, 2050.")
        elif name == "zero":
            items = []
        body = helpers._accordion_html(
            title="Jobs " + source.page_title_suffix, heading=source.heading, items=items
        )
        if name == "missing-panel":
            body = body.replace('data-accordion-content="styleguide-2-2"', 'data-missing="x"')
        elif name == "wrong-owner":
            body = body.replace(source.page_title_suffix, "Other")
        elif name == "pagination":
            body = body.replace("</main>", '<a rel="next">Next</a></main>')
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request, body=body: httpx.Response(
                    200, text=body, headers={"content-type": "text/html"}
                )
            )
        ) as client:
            try:
                jobs = await unifr._accordion_jobs(client, source, date(2026, 8, 26))
                output, error = [asdict(j) for j in jobs], False
            except ValueError:
                output, error = None, True
        cases.append(
            dict(
                kind="accordion",
                name=name,
                options=options(source),
                body=body,
                today="2026-08-26",
                output=output,
                error=error,
            )
        )

    for name, text in (
        ("empty", "No deadline"),
        ("english", "Apply by September 1st, 2050."),
        ("implied-year", "Recruiting 2050: apply by October 3rd."),
        ("ambiguous-year", "2026 and 2050: apply by October 3rd."),
        ("german", "Bewerbungsfrist: 3. März 2050"),
        ("geosciences", "Apply before 3rd of March 2050"),
        ("invalid-date", "Apply by February 30, 2050"),
        ("ambiguous-date", "Apply by March 1, 2050 or by April 1, 2050"),
        ("same-date", "Apply by March 1, 2050 or before 1st of March 2050"),
        ("unicode-boundary", "préby March 1, 2050"),
    ):
        try:
            value = unifr._deadline_from_text(text)
            output, error = value.isoformat() if value else "", False
        except ValueError:
            output, error = None, True
        cases.append(dict(kind="deadline", name=name, text=text, output=output, error=error))

    for name, source_name, hrefs in (
        ("law", "law", ["/ius/de/assets/public/documents/offres-emploi/one.pdf"]),
        ("regional-rewrite", "regional-school-service", ["assets/public/files/one.pdf"]),
        ("duplicate", "law", ["/ius/de/assets/public/documents/offres-emploi/one.pdf"] * 2),
        ("foreign", "law", ["https://example.com/one.pdf"]),
        ("query", "law", ["/ius/de/assets/public/documents/offres-emploi/one.pdf?x=1"]),
        ("fragment", "law", ["/ius/de/assets/public/documents/offres-emploi/one.pdf#x"]),
        ("path", "law", ["/elsewhere/one.pdf"]),
        ("zero", "law", []),
    ):
        source = unifr._LINK_SOURCES[source_name]
        links = "".join(f'<a href="{url}">Job</a>' for url in hrefs)
        body = (
            f"<html><head><title>Jobs {source.page_title_suffix}</title></head>"
            f"<body><main><h1>{source.heading}</h1>{links}</main></body></html>"
        )
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request, body=body: httpx.Response(
                    200, text=body, headers={"content-type": "text/html"}
                )
            )
        ) as client:
            try:
                output, error = sorted(await unifr._link_inventory(client, source)), False
            except ValueError:
                output, error = None, True
        cases.append(
            dict(
                kind="links",
                name=name,
                body=body,
                error=error,
                output=output,
                options=dict(
                    kind="links",
                    url=source.url,
                    suffix=source.page_title_suffix,
                    heading=source.heading,
                    path_prefix=source.path_prefix,
                    rewrite_from=source.rewrite_from or "",
                    rewrite_to=source.rewrite_to or "",
                ),
            )
        )
    return cases


Path(__file__).with_name("python_unifr_department.json").write_text(
    json.dumps(asyncio.run(freeze()), indent=2, ensure_ascii=False) + "\n"
)
