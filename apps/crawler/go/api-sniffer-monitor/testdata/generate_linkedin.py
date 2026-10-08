"""Freeze actual LinkedIn guest-card fields, scope, requests and discovery."""

from __future__ import annotations

import asyncio
import html
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from src.core.monitors import linkedin as monitor


def card(n, slug="acme", location="Basel, Switzerland", href=None, title="Engineer"):
    href = href or f"https://ch.linkedin.com/jobs/view/engineer-at-acme-{n}?tracking=1"
    return (
        f'<li><div class="base-search-card" data-entity-urn="urn:li:jobPosting:{n}">'
        f'<a class="base-card__full-link" href="{html.escape(href)}">Job</a>'
        f'<h3 class="base-search-card__title">{title}</h3>'
        '<h4 class="base-search-card__subtitle">'
        f'<a href="https://www.linkedin.com/company/{slug}?trk=x">ACME</a></h4>'
        f'<span class="job-search-card__location">{location}</span>'
        '<time datetime="2026-10-01">Recent</time></div></li>'
    )


parsers = []
for name, body in [
    ("normal", card(123)),
    ("nested-text", card(123, title=" Hello <b>World</b> Again ")),
    ("duplicate", card(123) * 2),
    ("missing-urn", card(123).replace("urn:li:jobPosting:", "invalid:")),
    ("foreign-href", card(123, href="https://evil.test/jobs/view/123")),
    ("wrong-id", card(123, href="https://www.linkedin.com/jobs/view/wrong-999")),
    ("credentials", card(123, href="https://user@www.linkedin.com/jobs/view/title-123")),
    ("port", card(123, href="https://www.linkedin.com:443/jobs/view/title-123")),
    ("unicode-id", card("１２３")),
    ("numeric-path", card(123, href="https://www.linkedin.com/jobs/view/123/")),
    ("invalid-percent-title", card(123, href="https://www.linkedin.com/jobs/view/role%zz-123")),
    ("empty-fields", card(123, title="", location="")),
    ("blank", " "),
    ("boilerplate", "<!DOCTYPE html><!---->"),
    ("challenge", "<html>Sign in</html>"),
]:
    for numeric in (False, True):
        case = {"name": name, "body": body, "numeric": numeric}
        try:
            jobs = monitor._parse_listing_cards(body, canonical_numeric_job_urls=numeric)
            case.update(jobs=[asdict(j) for j in jobs], failed=False)
        except Exception:
            case.update(jobs=[], failed=True)
        parsers.append(case)

empty = [
    {"body": b, "empty": monitor._is_empty_listing_fragment(b)}
    for b in (
        "",
        " \n",
        "<!---->",
        "<!DOCTYPE html><!---->",
        "<!--comment-->",
        "<!--unclosed",
        "<!doctype svg>",
        "<html></html>",
        "<p>No jobs</p>",
        "&nbsp;",
        "\x1c",
    )
]


async def inventory(name, metadata, replies, board="https://www.linkedin.com/company/acme/jobs/"):
    calls = []
    responses = []

    async def fetch(client, resource, **kwargs):
        calls.append({"url": resource, "headers": kwargs.get("headers", {})})
        if len(calls) > len(replies):
            raise ValueError("unexpected fixture request")
        body = replies[len(calls) - 1]
        responses.append(body)
        if body == "__failure__":
            raise ValueError("later fixture request failed")
        return body

    async def pause(*args):
        pass

    case = {
        "name": name,
        "board_url": board,
        "metadata": metadata,
        "calls": calls,
        "responses": responses,
    }
    try:
        with (
            patch.object(monitor, "fetch_text_page_with_retry", fetch),
            patch.object(monitor.asyncio, "sleep", pause),
        ):
            result = await monitor.discover({"board_url": board, "metadata": metadata}, None)
        truncated = bool(getattr(result, "truncated", False))
        jobs = list(result.jobs_by_url.values()) if hasattr(result, "jobs_by_url") else result
        case.update(jobs=[asdict(j) for j in jobs], failed=False, truncated=truncated)
    except Exception:
        case.update(jobs=[], failed=True, truncated=False)
    return case


async def main():
    md = {"company_id": "42", "company_slug": "acme"}
    ten = "".join(card(n) for n in range(1, 11))
    cases = [
        ("short", md, [card(1)]),
        ("empty", md, ["<!DOCTYPE html><!---->"]),
        ("pagination", md, [ten, card(11)]),
        ("terminator", md, [ten, "<!---->"]),
        ("late-failure", md, [ten, "__failure__"]),
        ("repeat", md, [ten, card(10)]),
        ("nonempty-no-cards", md, ["<p>Challenge</p>"]),
        ("identity-retry", md, [card(1, slug="other"), card(1)]),
        ("identity-failure", md, [card(1, slug="other"), card(1, slug="other")]),
        ("keywords-union", {**md, "keywords": "ACME"}, [card(1), card(1) + card(2)]),
        ("keywords-empty", {**md, "keywords": "ACME"}, ["<!---->", "<!---->"]),
        ("keywords-late-failure", {**md, "keywords": "ACME"}, [card(1), "__failure__"]),
        ("multi-id", {"company_ids": ["42", "43"]}, [card(1)]),
        ("numeric", {**md, "canonical_numeric_job_urls": True}, [card(1)]),
        ("country-owned", {**md, "source_ownership_excluded_country_codes": ["CHN"]}, [card(1)]),
        ("country-excluded", {**md, "source_ownership_excluded_country_codes": ["CHE"]}, [card(1)]),
        (
            "country-unknown",
            {**md, "source_ownership_excluded_country_codes": ["CHN"]},
            [card(1, location="Hybrid")],
        ),
        (
            "country-suffix",
            {**md, "source_ownership_excluded_country_codes": ["CHN"]},
            [card(1, location="Basel, City, Switzerland")],
        ),
        ("resolve-slug", {}, [card(1), "facetCurrentCompany%3D42", card(1)]),
        ("resolve-failure", {}, ["<!---->"]),
        ("both-id-fields", {"company_id": "42", "company_ids": ["43"]}, []),
        ("duplicate-company-id", {"company_ids": ["42", "42"]}, []),
        ("invalid-keywords", {**md, "keywords": " "}, []),
        ("invalid-numeric", {**md, "canonical_numeric_job_urls": 1}, []),
        ("invalid-country", {**md, "source_ownership_excluded_country_codes": ["XYZ"]}, []),
        (
            "cap",
            md,
            ["".join(card(n) for n in range(start, start + 10)) for start in range(1, 1001, 10)],
        ),
    ]
    inventories = [await inventory(name, metadata, replies) for name, metadata, replies in cases]
    inventories.append(
        await inventory(
            "url-company-filter",
            {},
            [card(1)],
            board="https://www.linkedin.com/jobs/search/?f_C=42%2C43",
        )
    )
    Path(__file__).with_name("python_linkedin.json").write_text(
        json.dumps(
            {"parsers": parsers, "empty": empty, "inventories": inventories},
            indent=2,
            ensure_ascii=False,
        )
        + "\n"
    )


asyncio.run(main())
