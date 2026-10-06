"""Freeze field adapters from actual ADP, Cornerstone and Paylocity code."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import adp, cornerstone, paylocity
from src.core.scrapers import paylocity as detail
from src.shared.adp import AdpBoard
from src.shared.cornerstone import CornerstoneBoard

ADP = AdpBoard("01234567-89ab-cdef-0123-456789abcdef", "19000101_000001", "en_US")
CSOD = CornerstoneBoard("fixture", 4, "fixture")
PAY = "https://2000recruiting.paylocity.com/Recruiting/Jobs/All/fixture"
cases = []


def record(provider, name, row, culture="en-US"):
    source = (
        ADP.listing_url()
        if provider == "adp"
        else CSOD.listing_url()
        if provider == "cornerstone"
        else PAY
    )
    job = (
        adp._parse_job(row, ADP)
        if provider == "adp"
        else cornerstone._parse_job(row, CSOD, culture)
        if provider == "cornerstone"
        else paylocity._parse_job(row, PAY)
    )
    cases.append(
        dict(
            provider=provider,
            name=name,
            source=source,
            row=row,
            culture=culture,
            expected=asdict(job) if job else None,
        )
    )


adp_row = {
    "itemID": "123_1",
    "requisitionTitle": " Engineer &amp; Builder ",
    "requisitionLocations": [
        {"nameCode": {"shortName": " New  York , US "}},
        {"nameCode": {"shortName": "NEW YORK, US"}},
    ],
    "workLevelCode": {"shortName": "Regular - Full Time"},
    "postDate": "2026-10-06",
    "clientRequisitionID": " Ref &amp; 1 ",
}
record("adp", "complete", adp_row)
for name, change in [
    ("numeric-id", {"itemID": 123}),
    ("missing-title", {"requisitionTitle": None}),
    ("escaped-blank-title", {"requisitionTitle": "&nbsp;"}),
    (
        "unicode-location",
        {
            "requisitionLocations": [
                {"nameCode": {"shortName": "Straße, DE"}},
                {"nameCode": {"shortName": "STRASSE, DE"}},
            ]
        },
    ),
]:
    record("adp", name, {**adp_row, **change})
record("adp", "empty", {})
for label in [
    "Full-Time/Part-Time",
    "Per Diem",
    "Seasonal",
    "Intern - Full Time",
    "Summer Intern",
    "apprentice",
    "temporary",
    "contract",
    "consultant",
    "volunteer",
    "freelance",
    "fulltime",
    "unknown",
]:
    record("adp", "employment-" + label, {**adp_row, "workLevelCode": {"shortName": label}})
csod_row = {
    "requisitionId": 123,
    "displayJobTitle": " Engineer ",
    "externalDescription": "<p>Build &amp; ship</p>",
    "locations": [
        {"city": "Zurich", "state": "ZH", "country": "CH"},
        {"city": "Zurich", "state": "ZH", "country": "CH"},
    ],
    "postingEffectiveDate": "10/06/2026",
}
record("cornerstone", "complete", csod_row)
record("cornerstone", "empty", {})
for name, change in [
    ("boolean-id", {"requisitionId": True}),
    ("big-id", {"requisitionId": "18446744073709551616"}),
    ("unicode-id", {"requisitionId": "１２３"}),
    ("missing-title", {"displayJobTitle": " "}),
    ("bad-date", {"postingEffectiveDate": "-"}),
    ("iso-date", {"postingEffectiveDate": "2026-1-2"}),
    ("european-date", {"postingEffectiveDate": "06/10/2026"}),
]:
    record(
        "cornerstone", name, {**csod_row, **change}, "de-DE" if name == "european-date" else "en-US"
    )
pay_row = {
    "JobId": 123,
    "JobTitle": "Engineer",
    "LocationName": "Zurich",
    "HiringDepartment": "Platform",
    "PublishedDate": "2026-10-06",
}
for name, change in [
    ("complete", {}),
    ("hybrid", {"LocationName": " Hybrid Remote "}),
    ("remote", {"IsRemote": True}),
    ("missing-id", {"JobId": None}),
    ("string-id", {"JobId": "00123"}),
    ("empty-fields", {"JobId": 2, "JobTitle": None, "LocationName": " "}),
]:
    record("paylocity", name, {**pay_row, **change})
markup = (
    '<div class="job-preview-title"><span> Engineer &amp; Builder </span></'
    'div><div class="job-listing-header">Description</div>\n<section><p titl'
    'e="x\'y"> Build &amp; ship&nbsp;today<br>Now</p></section><div class="j'
    'ob-listing-header">Job Type</div><p>Full <b>Time</b></p>'
)
for name, location in [
    ("empty", ""),
    (
        "map",
        '<div class="preview-location"><a href="https://maps.google.com?q=Zurich">Zurich</a></div>',
    ),
    ("remote", '<div class="preview-location"><span>Fully Remote</span> • <span>US</span></div>'),
    ("hybrid", '<div class="preview-location"><span>Hybrid Remote</span><span>Zurich</span></div>'),
    ("onsite", '<div class="preview-location">On-site<span>Zurich</span></div>'),
]:
    page = "" if name == "empty" else markup + location
    cases.append(
        dict(
            provider="paylocity-detail",
            name=name,
            html=page,
            expected=asdict(detail.parse_html(page)),
        )
    )
(Path(__file__).parent / "python_fifth_provider_fields.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print("actual Python field cases", len(cases))
