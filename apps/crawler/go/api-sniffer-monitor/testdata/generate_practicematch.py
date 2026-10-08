"""Freeze actual PracticeMatch parsers, forms and complete traversal outcomes."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import patch

from src.core.monitors import practicematch as monitor

landing = '<input id="facilityID" value="42"><input id="facilityLandingURL" value="A &amp; B">'


def link(n):
    return (
        f'<a href="https://www.practicematch.com/physicians/job-details.cfm/{n}/'
        'role?tracking=1">Job</a>'
    )


parsers = []
for name, body in [
    ("empty", ""),
    ("hidden", landing),
    ("initial", landing + link(123)),
    ("duplicate", landing + link(123) * 2),
    ("two-hosts", link(1) + link(2).replace("www.practicematch.com", "practicematch.com")),
    ("relative-ignored", '<a href="/physicians/job-details.cfm/1/">Job</a>'),
    ("foreign", link(1).replace("www.practicematch.com", "evil.example")),
    (
        "upper",
        link(1)
        .replace("physicians", "PHYSICIANS")
        .replace("www.practicematch.com", "WWW.PRACTICEMATCH.COM"),
    ),
    ("unicode-id", link("１２３")),
    ("encoded-id", link("%31")),
    ("invalid-percent-suffix", link(1).replace("/role", "/role%zz")),
    ("empty-field-overrides", landing + '<input id="facilityID" value="">'),
]:
    hidden, urls = monitor._parse_landing_html(body)
    parsers.append({"name": name, "body": body, "hidden": hidden, "urls": sorted(urls)})

forms = []
for hidden in (
    {"facilityID": "42"},
    {
        "facilityID": "a/b",
        "facilityLandingURL": "A & B",
        "contactID": "",
        "oppIDs": "11,12",
        "hasMap": "1",
    },
):
    for profession in ("1", "-1"):
        for page in (1, 3):
            forms.append(
                {
                    "hidden": hidden,
                    "profession": profession,
                    "page": page,
                    "body": monitor._form(hidden, profession_id=profession, page=page).decode(),
                }
            )


async def inventory(mode: str) -> dict:
    board = "https://employer.practicematch.com/employer/fixture/"
    metadata = {"proxy": True}
    if mode == "cap":
        metadata["max_pages"] = 1
    calls = []

    async def fetch(client, url, **kwargs):
        method = kwargs.get("method", "GET")
        body = kwargs.get("content", b"").decode()
        calls.append(
            {"method": method, "url": url, "body": body, "headers": kwargs.get("headers", {})}
        )
        if method == "GET":
            return (
                "<p>No facility</p>"
                if mode == "missing-facility"
                else landing + (link(1) if mode not in ("empty", "cap") else "")
            )
        from urllib.parse import parse_qs

        form = parse_qs(body)
        profession, page = form["professionID"][0], int(form["pageNum"][0])
        if mode == "late-failure" and profession == "-1":
            raise ValueError("fixture later resource failure")
        rows = ""
        if mode == "cap":
            rows = link(2 if profession == "1" else 3)
        elif mode in ("complete", "late-failure"):
            rows = (
                link(2)
                if profession == "1" and page == 2
                else link(3)
                if profession == "-1" and page == 1
                else ""
            )
        elif mode == "repeat":
            rows = link(1)
        return json.dumps({"OPPLISTINGSHTML": rows})

    record = {"name": mode, "board": board, "metadata": metadata, "calls": calls}
    try:
        with patch.object(monitor, "fetch_text_page_with_retry", fetch):
            result = await monitor.discover({"board_url": board, "metadata": metadata}, None)
        record.update(
            urls=sorted(result.urls if hasattr(result, "urls") else result),
            truncated=bool(getattr(result, "truncated", False)),
            failed=False,
        )
    except Exception:
        record.update(urls=[], truncated=False, failed=True)
    return record


async def main():
    cases = [
        await inventory(mode)
        for mode in ("complete", "empty", "repeat", "cap", "missing-facility", "late-failure")
    ]
    Path(__file__).with_name("python_practicematch.json").write_text(
        json.dumps(
            {"parsers": parsers, "forms": forms, "inventories": cases}, indent=2, ensure_ascii=False
        )
        + "\n"
    )


asyncio.run(main())
