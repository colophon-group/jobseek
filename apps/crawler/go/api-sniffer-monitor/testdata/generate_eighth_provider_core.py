"""Freeze synthetic inputs through the actual public-provider Python parsers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import cvwarehouse, earcu, woowa

keys = (
    "url",
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "language",
    "extras",
    "metadata",
    "source_identity",
)
xml = """<positions><position><DescriptionURL>/jobs/vacancy/12</DescriptionURL>
<JobTitle> Engineer </JobTitle><Description><![CDATA[<p>Build &amp; ship</p>]]></Description>
<Locations><Location> Zürich </Location><Location> </Location></Locations>
<LastPublishedDate>2026-10-08</LastPublishedDate><VacancyRef>REF</VacancyRef>
<JobFunction>Platform</JobFunction><Brand>Example</Brand>
<DisplaySalaryDescription>CHF 100000</DisplaySalaryDescription></position></positions>"""
card = """<div data-item-collection
 data-filter-workschedule='["Full time", false, 3.0, {"kind":"temp"}]'
 data-filter-worktype="Stagiair" data-filter-attribute='["Example"]'>
<a data-jobid="12" href="/?job=12&amp;lang=nl-BE&amp;section=12345678-1234-1234&amp;q=title">Job</a>
<span class="workType"><i class="lni-laptop"></i>Hybrid</span></div>
<section data-jobdetail-job-id="12"><h2 class="job-title">Engineer</h2>
<div class="additional-data"><span class="location">Brussels</span></div>
<div class="jobDescriptionText"><p>Build &amp; ship</p></div></section>"""
listing = {"recruitNumber": "R-1", "recruitName": " Listing title ", "description": "Seoul"}
detail = {
    "recruitNumber": "R-1",
    "recruitName": ' Engineer\'s & "role" ',
    "recruitContents": "<p>근무 지역: 서울 / 부산 역할 Build</p>",
    "employmentType": {"recruitItemCode": "BA002003"},
    "recruitOpenDate": "2026-10-08T12:00:00",
    "recruitEndDate": "2999-01-01",
    "recruitCorporationNumber": 0,
    "careerType": {"code": "new"},
    "jobGroup": "Platform",
    "desiredBranches": [{"recruitItemName": "Branch", "recruitItemRemark": "addr=Gangnam"}],
    "applicantCheckList": [{"recruitItemName": " Check's ", "recruitItemRemark": " A&B "}],
}
cases = []


def add(provider, name, input_value, source, parse):
    try:
        jobs = parse()
        expected = [{key: asdict(job)[key] for key in keys} for job in jobs]
        error = False
    except Exception:
        expected, error = None, True
    cases.append(
        {
            "provider": provider,
            "name": name,
            "input": input_value,
            "source": source,
            "expected": expected,
            "error": error,
        }
    )


feed = "https://jobs.example.com/jobs/allvacancies/"
for name, body in (
    ("rich", xml),
    ("empty", "<positions/><!--footer-->"),
    ("unsafe-url", xml.replace("/jobs/vacancy/12", "https://other.example/vacancy/12")),
    ("missing-title", xml.replace(" Engineer ", " ")),
    ("duplicate", xml.replace("</positions>", xml.split("<positions>")[1])),
    ("wrong-root", "<html/>"),
    ("entities", '<!DOCTYPE positions [<!ENTITY private "private">]><positions/>'),
):
    add("earcu", name, body, feed, lambda body=body: earcu._parse_feed(body, feed))

page = "https://tenant.cvw.io/?section=12345678-1234-1234&lang=nl-BE"
for name, body in (
    ("rich", card),
    ("empty", "<html/>"),
    ("duplicate", card + card),
    ("missing-description", card.replace("<p>Build &amp; ship</p>", "")),
    ("unsafe-job", card.replace("/?job=12", "https://other.example/?job=12")),
):
    add(
        "cvwarehouse",
        name,
        body,
        page,
        lambda body=body: cvwarehouse._parse_locale_page(body, page),
    )

for host, variant in woowa._VARIANTS.items():
    for name, change in (
        ("rich", {}),
        ("fallback-title", {"recruitName": None}),
        ("sentinel-date", {"recruitOpenDate": "9999-12-31"}),
        ("bad-date", {"recruitEndDate": "2026-02-30"}),
        ("missing-description", {"recruitContents": ""}),
    ):
        value = {**detail, **change}
        add(
            "woowa",
            f"{variant.name}-{name}",
            {"listing": listing, "detail": value},
            f"https://{host}/",
            lambda value=value, variant=variant: [woowa._parse_job(variant, listing, value)],
        )

Path(__file__).with_name("python_eighth_provider_core.json").write_text(
    json.dumps({"cases": cases}, ensure_ascii=False, indent=2) + "\n"
)
print(f"froze {len(cases)} actual Python field cases")
