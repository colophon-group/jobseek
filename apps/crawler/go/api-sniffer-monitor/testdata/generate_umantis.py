"""Freeze actual Python Umantis listing and pagination components."""

from __future__ import annotations

import asyncio
import json
import re
from dataclasses import asdict
from html import escape
from pathlib import Path

import httpx

from src.core.monitors import umantis

BASE = "https://recruitingapp-3040.umantis.com"
EMPLOYER = "Université de Neuchâtel"
FIELD = "column_value_1184173"


def row(identifier="1", locale="2", title="Engineer", owner=EMPLOYER):
    return (
        '<tr><td><a class="HSTableLinkSubTitle" '
        f'href="/Vacancies/{identifier}/Description/{locale}">{title}</a></td>'
        '<td><li><i class="icon-department"></i>'
        '<span class="column-value">Neuchâtel <b>CH</b></span></li>'
        '<li><span class="visually-hidden">Employment Type:</span>'
        '<span class="column-value">Full time</span></li>'
        f'<span class="column-value" id="{FIELD}">{owner}</span></td></tr>'
    )


rows = []
for name, body, strict in (
    ("nested-fields", row(), False),
    ("strict-owner", row(owner="Université <b>de Neuchâtel</b>"), True),
    ("forged-prefix", row(owner=EMPLOYER + " <b>Other Employer</b>"), True),
    ("missing-owner", row().replace(f'id="{FIELD}"', 'id="other"'), True),
    (
        "duplicate-owner",
        row().replace(
            "</td></tr>", f'<span class="column-value" id="{FIELD}">{EMPLOYER}</span></td></tr>'
        ),
        True,
    ),
    ("malformed-owner", row(owner=EMPLOYER + "<b>Other</span></b>"), True),
    ("locale-alias", row(locale="3") + row(locale="1", title="Ingénieur"), False),
    ("conflicting-locale", row() + row(title="Different"), False),
    ("duplicate-locale", row() + row(), False),
    (
        "bare-link",
        '<a class="HSTableLinkSubTitle" href="/Vacancies/9/Description/1">Bare job</a>',
        False,
    ),
    ("foreign-link", row().replace("/Vacancies/1/", "https://foreign.example/Vacancies/1/"), False),
    ("invalid-id", row(identifier="0"), False),
    ("blank-title", row(title=" "), False),
    ("empty", "No rows", False),
):
    try:
        parsed = umantis._parse_parsed_jobs_from_html(
            body,
            BASE,
            expected_employer=EMPLOYER if strict else None,
            employer_field_id=FIELD if strict else None,
        )
        parsed = list(umantis._deduplicate_vacancies(parsed).values())
        values = [
            dict(
                id=p.vacancy_id,
                language=p.language_id,
                url=p.job.url,
                title=p.job.title,
                location=(p.job.locations or [""])[0],
                employment_type=p.job.employment_type or "",
            )
            for p in parsed
        ]
        error = False
    except ValueError:
        values, error = None, True
    rows.append(dict(name=name, body=body, strict=strict, rows=values, error=error))

navigation = []
base_nav = dict(
    TableNr="1184173",
    TableTotalLines=3,
    TableFrom=1,
    TableTo=2,
    TableCurrentPage=1,
    NextLink=dict(
        EnhancedUrl="/Jobs/3?CompanyID=32&tc1184173=p2&_search_token1184173=123", FieldIsActive=1
    ),
)
for name, update in (
    ("valid", {}),
    ("numeric-strings", {"TableTotalLines": "3"}),
    ("boolean-count", {"TableTotalLines": True}),
    ("fractional-count", {"TableTotalLines": 3.0}),
    ("over-cap", {"TableTotalLines": 50001}),
    ("missing-page", {"TableCurrentPage": None}),
    ("bad-table", {"TableNr": "0"}),
    ("float-active", {"NextLink": dict(base_nav["NextLink"], FieldIsActive=1.0)}),
    ("boolean-active", {"NextLink": dict(base_nav["NextLink"], FieldIsActive=True)}),
):
    payload = dict(base_nav, **update)
    body = (
        '<table-navigation initial-data-string="'
        + escape(json.dumps(payload), quote=True)
        + '"></table-navigation>'
    )
    try:
        parsed = umantis._extract_navigation(body)
        value, error = asdict(parsed), False
    except ValueError:
        value, error = None, True
    navigation.append(dict(name=name, body=body, value=value, error=error))

paths = []
for listing in (
    BASE + "/Jobs/All",
    BASE + "/Jobs/3?lang=fre&CompanyID=32&Reset=G&DesignID=10012",
    BASE + "/Jobs/All?x=one&x=two&blank=&tc11=p1&reset=1",
):
    paths.append(dict(listing=listing, expected=umantis._pagination_url(listing, "11", 2)))

owners = []
for name, body in (
    ("owner", f'<head><meta name="description" content="{EMPLOYER} - Careers"></head>'),
    ("body", f'<head></head><body><meta name="description" content="{EMPLOYER}"></body>'),
    ("prefix", f'<head><meta name="description" content="{EMPLOYER} Other - Careers"></head>'),
    (
        "duplicate",
        f'<head><meta name="description" content="{EMPLOYER}">'
        f'<meta name="description" content="{EMPLOYER}"></head>',
    ),
    ("unfinished-head", f'<head><meta name="description" content="{EMPLOYER}">'),
    ("unfinished-body", f'<head><meta name="description" content="{EMPLOYER}"></head><body>'),
    ("selfclosing-body", f'<head><meta name="description" content="{EMPLOYER}"></head><body/>'),
    ("nested-head", f'<head><head><meta name="description" content="{EMPLOYER}"></head></head>'),
    ("double-head", f'<head><meta name="description" content="{EMPLOYER}"></head><head></head>'),
    (
        "whole-page",
        f'<html><head><meta name="description" content="{EMPLOYER} – Careers"></head>'
        "<body>Jobs</body></html>",
    ),
):
    parser = umantis._DetailOwnerParser()
    parser.feed(body)
    parser.close()
    valid = (
        parser.head_count == 1
        and parser.structurally_complete
        and not parser.outside_head_descriptions
        and len(parser.descriptions) == 1
    )
    if valid:
        valid = umantis._normalized_identity(
            re.split(r"\s+[-–—]\s+", parser.descriptions[0], maxsplit=1)[0]
        ) == umantis._normalized_identity(EMPLOYER)
    owners.append(dict(name=name, body=body, valid=valid))

visible = []
for name, body in (
    ("visible", "<p>No jobs available</p>"),
    ("script", "<script>No jobs available</script>"),
    ("hidden", "<p hidden>No jobs available</p>"),
    ("style", "<p style='DISPLAY: none'>No jobs available</p>"),
    ("closed-details", "<details><p>No jobs available</p></details>"),
    ("open-details", "<details open><p>No jobs available</p></details>"),
    ("aria", "<p aria-hidden='true'>No jobs available</p>"),
    ("class", "<p class='visually-hidden'>No jobs available</p>"),
    ("nested", "<div hidden><div>x</div></div><p>No jobs available</p>"),
    ("selfclosing-hidden", "<div hidden/><p>No jobs available</p>"),
):
    visible.append(
        dict(
            name=name,
            body=body,
            expected="No jobs available",
            valid=umantis._has_visible_text(body, "No jobs available"),
        )
    )


def nav(total, first, last, page, next_url=None):
    payload = dict(
        TableNr="1184173",
        TableTotalLines=total,
        TableFrom=first,
        TableTo=last,
        TableCurrentPage=page,
    )
    if next_url:
        payload["NextLink"] = dict(EnhancedUrl=next_url, FieldIsActive=1)
    return (
        '<table-navigation initial-data-string="'
        + escape(json.dumps(payload), quote=True)
        + '"></table-navigation>'
    )


async def freeze_discovery():
    cases = []
    listing = BASE + "/Jobs/3?CompanyID=32"
    next_url = BASE + "/Jobs/3?CompanyID=32&tc1184173=p2&_search_token1184173=123"
    owner = f'<head><meta name="description" content="{EMPLOYER} - Careers"></head>'
    first = row() + nav(2, 1, 1, 1, next_url)
    second = row(identifier="2") + nav(2, 2, 2, 2)
    for name, strict, first_body, tail_body, tail_status, owner_body in (
        ("strict-complete", True, first, second, 200, owner),
        ("strict-missing-tail", True, first, "", 404, owner),
        ("strict-overlap", True, first, row() + nav(2, 2, 2, 2), 200, owner),
        ("strict-changing-total", True, first, row(identifier="2") + nav(3, 2, 2, 2), 200, owner),
        (
            "strict-foreign-next",
            True,
            first.replace(BASE, "https://foreign.example"),
            second,
            200,
            owner,
        ),
        ("strict-wrong-owner", True, first, second, 200, owner.replace(EMPLOYER, "Other")),
        ("strict-zero", True, nav(0, 0, 0, 1) + "<p>No jobs available</p>", "", 200, owner),
        (
            "strict-hidden-zero",
            True,
            nav(0, 0, 0, 1) + "<p hidden>No jobs available</p>",
            "",
            200,
            owner,
        ),
        ("legacy-complete-repeat", False, first, second, 200, owner),
        ("legacy-terminal", False, first, "", 404, owner),
    ):
        calls = []
        responses = {
            listing: dict(status=200, body=first_body),
            next_url: dict(status=tail_status, body=tail_body),
        }
        for identifier in ("1", "2"):
            responses[BASE + f"/Vacancies/{identifier}/Description"] = dict(
                status=200, body=owner_body
            )
        if not strict:
            responses[BASE + "/Jobs/3?CompanyID=32&tc1184173=p2"] = dict(
                status=tail_status, body=tail_body
            )
            responses[BASE + "/Jobs/3?CompanyID=32&tc1184173=p3"] = dict(status=200, body=tail_body)

        def handler(request, calls=calls, responses=responses):
            calls.append(str(request.url))
            assert str(request.url) in responses, str(request.url)
            response = responses[str(request.url)]
            return httpx.Response(response["status"], text=response["body"])

        metadata = dict(customer_id="3040", listing_path="/Jobs/3?CompanyID=32")
        if strict:
            metadata.update(
                strict_listing_contract=True,
                expected_employer=EMPLOYER,
                employer_field_id=FIELD,
                empty_state_text="No jobs available",
            )
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                jobs = await umantis.discover(dict(board_url=listing, metadata=metadata), client)
                result, error = sorted(jobs), False
            except (ValueError, httpx.HTTPStatusError):
                result, error = None, True
        cases.append(
            dict(
                name=name,
                metadata=metadata,
                listing=listing,
                responses=responses,
                urls=result,
                error=error,
                calls=calls,
            )
        )
    return cases


discovery = asyncio.run(freeze_discovery())
Path(__file__).with_name("python_umantis.json").write_text(
    json.dumps(
        dict(
            rows=rows,
            navigation=navigation,
            paths=paths,
            owners=owners,
            visible=visible,
            discovery=discovery,
        ),
        indent=2,
        ensure_ascii=False,
    )
    + "\n"
)
