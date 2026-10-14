"""Freeze actual Onlyfy, PhuketAll and Tupu360 parsers and identities."""

from __future__ import annotations

import json
import runpy
from dataclasses import asdict
from pathlib import Path

from src.core.scrapers import onlyfy, phuketall, tupu360

root = Path(__file__).resolve().parents[3]
fixtures = {
    name: runpy.run_path(str(root / "tests" / filename))
    for name, filename in {
        "onlyfy": "test_onlyfy.py",
        "phuketall": "test_phuketall_scraper.py",
        "tupu360": "test_tupu360_scraper.py",
    }.items()
}
cases = []
for provider, module in (("onlyfy", onlyfy), ("phuketall", phuketall), ("tupu360", tupu360)):
    bodies = [
        (name, body)
        for name, body in fixtures[provider].items()
        if isinstance(body, str) and "<html" in body
    ]
    for name, body in bodies:
        variants = [
            ("complete", body),
            ("empty", ""),
            ("nested", body.replace("Medical Nurse", "Hello <b>World</b> Again")),
            ("entities", body.replace("<p>", "<p>A &amp; B&nbsp;")),
        ]
        if provider == "onlyfy":
            variants += [
                (
                    "duplicate",
                    body.replace(
                        "</body>",
                        '<p class="text-element-body_text">'
                        "Flexible working hours and mobile work.</p></body>",
                    ),
                )
            ]
        if provider == "tupu360":
            variants += [
                (key, body.replace(key, "unused"))
                for key in (
                    "positionName",
                    "positionInfoInp",
                    "positionId",
                    "position-description",
                    "工作地点",
                    "发布时间",
                )
            ]
        if provider == "phuketall":
            variants += [
                (key, body.replace(key, "unused"))
                for key in ("title-feedbox", "col-md-12", "detailsbox", "ภูเก็ต")
            ]
        for variant, html in variants:
            for config in [{}, {"language": "de"}] if provider == "onlyfy" else [{}]:
                cases.append(
                    {
                        "provider": provider,
                        "name": f"{name}/{variant}/{config}",
                        "body": html,
                        "config": config,
                        "content": asdict(module.parse_html(html, config)),
                    }
                )

onlyfy_urls = []
for source in (
    "https://example.onlyfy.jobs/de/job/abc123",
    "https://example.onlyfy.jobs/job/abc%2Fx",
    "https://example.onlyfy.jobs/job/a+b:c",
    "https://example.onlyfy.jobs/job/é",
    "https://example.onlyfy.jobs/job/a b",
    "/job/abc",
    "https://example.onlyfy.jobs/candidate/widget/test/job/abc",
    "https://example.onlyfy.jobs/job/show/abc",
    "https://example.onlyfy.jobs/job",
    "https://example.onlyfy.jobs/en",
):
    for language in (None, "de", "de CH"):
        case = {"source": source, "language": language}
        try:
            case.update(
                candidate=onlyfy._candidate_url(source, language),
                listing=onlyfy._listing_url(source, language),
                handle=onlyfy._job_handle_and_locale(source, language)[0],
                accepted=True,
            )
        except ValueError:
            case["accepted"] = False
        onlyfy_urls.append(case)

dates = [
    {"source": value, "expected": phuketall._parse_date(value)}
    for value in (
        "21 ส.ค. 69",
        "1 ม.ค. 2569",
        "29 ก.พ. 2567",
        "29 ก.พ. 2569",
        "21 Aug 26",
        "21 AUG 26",
        "1 January 2026",
        "31 Feb 26",
        "",
        "٠١ ม.ค. ٢٥٦٩",
        "21 ส.ค. 2026",
        "21 Aug 69",
        "21 Aug 68",
    )
]
identities = []
base = "https://www.phuketall.com/jobs/057289-213282-phuket/213282.html"
for source in (
    base,
    base.replace("/jobs/", "/en/jobs/"),
    base + "?x=1",
    base + "#x",
    base.replace("www.phuketall.com", "www.phuketall.com.attacker.test"),
    base.replace("https", "http"),
    base.replace(".com/", ".com:443/"),
):
    identities.append(
        {
            "provider": "phuketall",
            "source": source,
            "expected": phuketall._trusted_detail_identity(source),
        }
    )
for source in (
    fixtures["tupu360"]["URL"],
    fixtures["tupu360"]["CUSTOM_HOST_URL"],
    fixtures["tupu360"]["URL"] + "&positionId=",
    fixtures["tupu360"]["URL"] + "&bad=%zz",
    fixtures["tupu360"]["URL"] + "&positionId=deadbeef",
    fixtures["tupu360"]["URL"].replace("https", "http"),
    fixtures["tupu360"]["URL"].replace(".com/", ".com:443/"),
):
    identities.append(
        {"provider": "tupu360", "source": source, "expected": tupu360._position_id(source)}
    )

listing = fixtures["onlyfy"]["_rsc_listing_html"]()
out = {
    "cases": cases,
    "onlyfy_urls": onlyfy_urls,
    "dates": dates,
    "identities": identities,
    "onlyfy_listing": {
        "body": listing,
        "handle": "abc123",
        "expected": onlyfy._location_from_listing(listing, "abc123"),
    },
}
target = Path(__file__).with_name("python_last_static_detail.json")
target.write_text(json.dumps(out, ensure_ascii=False, sort_keys=True, indent=2) + "\n")
print(
    f"wrote {len(cases)} actual parser cases, {len(onlyfy_urls)} URL cases, "
    f"{len(dates)} dates and {len(identities)} identities"
)
