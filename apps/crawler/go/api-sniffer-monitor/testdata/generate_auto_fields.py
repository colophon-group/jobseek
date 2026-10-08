"""Freeze unambiguous automatic API mappings through the original Python helper."""

from __future__ import annotations

import json
from pathlib import Path

from src.shared.api_sniff import auto_map_fields

cases = []


def add(name, items):
    cases.append(dict(name=name, items=items, expected=auto_map_fields(items)))


add("empty", [])
add(
    "plain",
    [
        dict(
            title="Engineer",
            description="Build",
            location="Zürich",
            employmentType="FULL_TIME",
            publishedAt="2026-10-08",
            remote=True,
            department="Platform",
            url="https://jobs.example/one",
        )
    ],
)
add("url-only", [dict(identifier="one", url="https://jobs.example/one")])
add("adp-schedule-overrides-type", [dict(type="Normal", workLevelCode="FT")])
add(
    "adp-address",
    [
        dict(
            requisitionLocations=[
                dict(
                    nameCode=dict(shortName="4121-Hotel"),
                    address=dict(
                        cityName="Salt Lake City",
                        countrySubdivisionLevel1=dict(codeValue="UT"),
                        country=dict(longName="US"),
                    ),
                )
            ]
        )
    ],
)
add(
    "adp-name-short",
    [dict(requisitionLocations=[dict(nameCode=dict(shortName="Short", longName="Long"))])],
)
add("adp-name-long", [dict(requisitionLocations=[dict(nameCode=dict(longName="Long"))])])
add("location-list-strings", [dict(locations=["Geneva", "Paris"])])
add("location-list-name", [dict(locations=[dict(code=1, displayName="Paris", name="First")])])
add("location-list-fallback", [dict(locations=[dict(code=1, geographic="Paris", label2="Later")])])
add("location-object-name", [dict(location=dict(code=1, city="Sheffield", state="UK"))])
add("location-object-fallback", [dict(location=dict(empty="", first="Paris", second="London"))])
add("location-list-empty-string", [dict(locations=[dict(first="", second="London")])])
add("location-empty", [dict(locations=[])])
add("location-null", [dict(location=None)])
add("department-object-name", [dict(department=dict(name=None, title="Platform"))])
add("department-object-title", [dict(department=dict(title="Platform", label="Later"))])
add(
    "department-object-fallback",
    [dict(department=dict(code=12, alternate="Platform", later="Other"))],
)
add("department-no-text", [dict(department=dict(code=12))])
add(
    "key-from-later-item",
    [dict(url="https://jobs.example/one"), dict(title="Engineer", url="https://jobs.example/two")],
)
add("sixth-item-ignored", [dict(identifier=i) for i in range(5)] + [dict(title="Ignored")])
add(
    "first-present-location",
    [dict(id=1), dict(location="Paris"), dict(location=dict(name="London"))],
)
for key in [
    "job_title",
    "jobOpeningName",
    "Title__c",
    "bodyHtml",
    "position_description_html",
    "Job_Posting_Description__c",
    "employment_status_label",
    "publication_date__c",
    "Modality__c",
    "department_label",
]:
    add("alias-" + key, [{key: "value"}])
Path(__file__).with_name("python_auto_fields.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(f"froze {len(cases)} actual Python auto-mapping cases")
