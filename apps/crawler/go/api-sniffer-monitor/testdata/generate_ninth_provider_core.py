"""Freeze five providers through their actual Python parsers."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import beehire, computrabajo, hirehive, welcometothejungle, ycombinator

keys = (
    "url",
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "base_salary",
    "language",
    "extras",
    "metadata",
    "localizations",
)
cases = []


def add(provider, name, row, source, parser, metadata=None):
    try:
        job = parser()
        expected = {key: asdict(job)[key] for key in keys} if job is not None else None
        error = False
    except Exception:
        expected, error = None, True
    cases.append(
        dict(
            provider=provider,
            name=name,
            row=row,
            source=source,
            metadata=metadata or {},
            expected=expected,
            error=error,
        )
    )


bee = dict(
    id="b1",
    inviteKey="b1",
    title={"1": " Ingénieur ", "0": " Engineer "},
    language=1,
    fullDescription={"1": "<p>Construire</p>", "0": "<p>Build</p>"},
    location=dict(city="Geneva", state="Geneva", country="Switzerland"),
    details=dict(contract=dict(type="contractType_permanent", remote="hybrid")),
    jobCategories=[dict(label="Platform")],
    created="2026-10-08",
)
for name, changes in [
    ("localized", {}),
    ("plain", dict(title=" Engineer ", fullDescription="<p>Build</p>")),
    ("first-language", dict(language=99, title={"4": " Engenheiro ", "1": " Ingénieur "})),
    ("fallback-description", dict(fullDescription=None, description={"0": "<p>Build</p>"})),
    ("worldwide", dict(location=dict(isWorldwide=True))),
    ("named-location", dict(location=dict(name=" Zürich ", isWorldwide=True))),
    ("no-title", dict(title=None)),
    ("no-url", dict(inviteKey=None)),
    ("invite-link", dict(inviteLink="/invite/direct")),
    ("empty-description-map", dict(fullDescription={}, description={"0": "fallback"})),
]:
    row = {**copy.deepcopy(bee), **changes}
    add(
        "beehire",
        name,
        row,
        "https://app.beehire.com/career/tenant",
        lambda row=row: beehire._parse_job(row),
    )

hive = dict(
    id=12,
    hosted_url="https://tenant.hirehive.com/engineer-12/",
    title="Engineer",
    description=dict(html="<p>Build</p>", text="fallback"),
    location=" Zürich ",
    type=dict(type="Full time"),
    language=dict(code="de_CH"),
    published_date="2026-10-08",
    compensation_tiers=[
        dict(min_value=100000, max_value=130000, currency_code="CHF", interval="annually")
    ],
    category=dict(name="Platform"),
    experience=dict(type="senior"),
)
for name, changes in [
    ("rich", {}),
    ("structured-location", dict(location=None, state_code="ZH", country=dict(name="CH"))),
    ("text-description", dict(description=dict(html="", text="Build"))),
    ("empty-language", dict(language=dict(code=""))),
    ("invalid-language", dict(language=dict(code="deutsch"))),
    ("salary-fallback", dict(compensation_tiers=[], salary="CHF 100000 - 130000 per year")),
    (
        "zero-tier",
        dict(
            compensation_tiers=[
                dict(min_value=0, max_value=None, interval="hour", currency_code="CHF")
            ]
        ),
    ),
    ("no-url", dict(hosted_url=None)),
]:
    row = {**copy.deepcopy(hive), **changes}
    add(
        "hirehive",
        name,
        row,
        "https://tenant.hirehive.com/",
        lambda row=row: hirehive._parse_job(row, default_job_location_type="hybrid"),
        dict(defaults=dict(job_location_type="hybrid")),
    )

jungle = dict(
    slug="engineer-12",
    name="Engineer",
    status="published",
    archived_at="2020-01-01",
    description="<p>Build</p>",
    contract_type="CDI",
    remote="partial",
    language="fr",
    published_at="2026-10-08",
    salary_min=50000,
    salary_max=70000,
    salary_currency="EUR",
    salary_period="yearly",
    reference="REF",
    start_date="2026-11-01",
    profession=dict(name=dict(fr="Ingénierie", en="Engineering")),
    profile="<p>Skills</p>",
    key_missions=["Build", "Ship"],
    skills=[dict(name=dict(fr="Go", en="Golang"))],
    offices=[dict(local_address="Paris"), dict(city="Paris"), dict(address="Lyon")],
)
for name, changes in [
    ("republished", {}),
    ("archived", dict(status="archived")),
    ("expired", dict(status="expired")),
    ("closed", dict(status="closed")),
    ("locale-fallback", dict(language=None)),
    ("single-office", dict(offices=None, office=dict(city="Paris"))),
    ("unknown-remote", dict(remote="unknown")),
    ("no-salary", dict(salary_min=None, salary_max=None)),
    ("missing-name", dict(name=None)),
    ("missing-slug", dict(slug=None)),
]:
    row = {**copy.deepcopy(jungle), **changes}
    add(
        "welcometothejungle",
        name,
        row,
        "https://www.welcometothejungle.com/fr/companies/tenant/jobs",
        lambda row=row: welcometothejungle._parse_job(row, locale="fr", public_slug="tenant"),
    )

listings = []


def listing(provider, name, source, body, page, parser):
    try:
        urls, total = parser()
        expected, error = dict(urls=sorted(urls), total=total), False
    except Exception:
        expected, error = None, True
    listings.append(
        dict(
            provider=provider,
            name=name,
            source=source,
            body=body,
            page=page,
            expected=expected,
            error=error,
        )
    )


employer = "https://hn.computrabajo.com/empresas/ofertas-de-trabajo-de-tenant-0123456789abcdef"
panda = "https://tenant.pandape.infojobs.com.br/"
for source, variant in [(employer, "employer"), (panda, "pandape")]:

    def page_body(total, page, numbers, variant=variant, source=source):
        if variant == "employer":
            marker = (
                f'<link rel="canonical" href="{source}">'
                f'<meta name="title" content="{total} ofertas de trabajo">'
            )
            links = "".join(
                (
                    '<a class="js-o-link" href="/ofertas-de-trabajo/'
                    f'oferta-de-trabajo-de-engineer-{number:032x}">Job</a>'
                )
                for number in numbers
            )
        else:
            marker = (
                '<section id="VacancySection"></section>'
                f'<div class="color-title font-3xl">{total} vagas de emprego</div>'
                '<input id="hdn_PageSize" value="20">'
                f'<input id="hdn_PageNumber" value="{page}">'
                f'<input id="hdn_isLast" value="{str(page * 20 >= total).lower()}">'
            )
            links = "".join(
                f'<a class="card-vacancy" href="/Detail/{number}">Job</a>' for number in numbers
            )
        return marker + links

    for name, total, page, numbers in [
        ("zero", 0, 1, []),
        ("one", 1, 1, [1]),
        ("full-page", 21, 1, list(range(1, 21))),
        ("last-page", 21, 2, [21]),
        ("duplicate", 2, 1, [1, 1]),
        ("missing-job", 2, 1, [1]),
    ]:
        body = page_body(total, page, numbers)
        parse = (
            computrabajo._parse_listing
            if variant == "employer"
            else computrabajo._parse_pandape_listing
        )
        listing(
            "computrabajo",
            variant + "-" + name,
            source,
            body,
            page,
            lambda body=body, page=page, parse=parse, source=source: parse(
                body, board_url=source, requested_page=page
            ),
        )
    body = (
        page_body(1, 1, [1])
        .replace("ofertas de trabajo", "unknown")
        .replace("vagas de emprego", "unknown")
    )
    listing(
        "computrabajo",
        variant + "-missing-total",
        source,
        body,
        1,
        lambda body=body, parse=parse, source=source: parse(
            body, board_url=source, requested_page=1
        ),
    )

yc = "https://www.ycombinator.com/companies/tenant/jobs"
for name, body in [
    (
        "scoped",
        (
            '<a href="/companies/tenant/jobs/Ab1-engineer">Job</a>'
            '<a href="/companies/other/jobs/Bb2-other">Other</a>'
        ),
    ),
    ("duplicate", '"/companies/tenant/jobs/Ab1-engineer" "/companies/tenant/jobs/Ab1-engineer"'),
    ("single-quote", "'/companies/tenant/jobs/Ab1-engineer'"),
    ("empty", "<html/>"),
]:
    listing(
        "ycombinator",
        name,
        yc,
        body,
        1,
        lambda body=body: (ycombinator._extract_job_urls(body, "tenant"), 0),
    )

Path(__file__).with_name("python_ninth_provider_core.json").write_text(
    json.dumps(dict(cases=cases, listings=listings), ensure_ascii=False, indent=2) + "\n"
)
print(f"froze {len(cases)} rich fields and {len(listings)} listing cases through Python")
