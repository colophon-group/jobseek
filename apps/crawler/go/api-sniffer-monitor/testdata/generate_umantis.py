"""Freeze actual Python Umantis listing and pagination components."""

from __future__ import annotations

import json
from dataclasses import asdict
from html import escape
from pathlib import Path

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

Path(__file__).with_name("python_umantis.json").write_text(
    json.dumps(dict(rows=rows, navigation=navigation, paths=paths), indent=2, ensure_ascii=False)
    + "\n"
)
