"""Freeze synthetic public inputs through the existing provider parsers."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import deel, hibob, traffit

rows = {
    "deel": [
        {
            "id": "one",
            "title": "Engineer",
            "richtextDescription": "<p>Build</p>",
            "createdAt": "2026-10-07",
            "isCompensationVisible": True,
            "job": {
                "jobLocations": [{"location": {"name": "Zürich"}}],
                "jobEmploymentTypes": [{"employmentType": {"name": "Full time"}}],
                "jobTeams": [{"team": {"name": "Platform"}}],
                "jobDepartments": [{"department": {"name": "Engineering"}}],
                "currentCompensation": {
                    "currencyIsoCode": "CHF",
                    "minAmount": 100000,
                    "maxAmount": 120000,
                },
            },
        },
        {
            "id": 42,
            "title": False,
            "isCompensationVisible": False,
            "job": {"currentCompensation": {"minAmount": 1}},
        },
        {
            "id": "one",
            "job": {"jobEmploymentTypes": [{}, {"employmentType": {"name": "Contractor"}}]},
        },
        {"id": 0},
        {},
    ],
    "hibob": [
        {
            "id": " one ",
            "title": "Engineer",
            "description": " <p>Build</p> ",
            "responsibilities": "<p>Own it</p>",
            "requirements": "<p>Go</p>",
            "benefits": "<p>Leave</p>",
            "site": " Zürich ",
            "country": "CH",
            "employmentType": "Employee",
            "employmentTypeId": "Permanent",
            "workspaceType": "Hybrid",
            "publishedAt": "2026-10-07",
            "language": "en",
            "payTransparencyMinSalary": 0,
            "payTransparencyMaxSalary": 120000,
            "payTransparencySalaryCurrency": "CHF",
            "payTransparencySalaryPayPeriod": "Monthly",
            "departmentId": 0,
            "workspaceTypeId": "Hybrid",
        },
        {
            "id": "two",
            "country": " CH ",
            "employmentTypeId": "Contractor",
            "workspaceTypeId": "Remote",
            "payTransparencyMinSalary": 12,
        },
        {"id": "three", "site": " ", "workspaceType": "unclassifiable"},
        {"id": "four", "description": False, "payTransparencyMaxSalary": 0},
        {"id": 42},
        {"id": " "},
        {},
    ],
    "traffit": [
        {
            "url": "https://tenant.traffit.com/job/one",
            "advert": {
                "name": "Engineer",
                "language": "en",
                "values": [
                    {"field_id": "description", "value": "<p>Build</p>"},
                    {"field_id": "geolocation", "value": '{"locality":"Zürich","country":"CH"}'},
                    {"field_id": "requirements", "value": "<p>Go</p>"},
                ],
                "recruitment": {"nr_ref": "REF"},
            },
            "options": {
                "job_type": ["Full time"],
                "remote": "1",
                "branches": ["Engineering"],
                "_Salary_MIN": "100.5",
                "_Salary_MAX": "invalid",
                "_Salary_Currency": "CHF",
                "_Salary_Rate": "Hourly",
            },
            "valid_start": "2026-10-07 10:00:00",
        },
        {
            "url": "https://tenant.traffit.com/job/two",
            "options": {
                "_work_model": "Hybrid",
                "_Salary_MIN": False,
                "_Salary_MAX": True,
                "_Salary_Currency": "PLN",
                "_Salary_Rate": "unknown",
            },
        },
        {
            "url": "https://tenant.traffit.com/job/three",
            "advert": {
                "values": [
                    {"field_id": "geolocation", "value": "invalid"},
                    {"field_id": "benefits", "value": "<p>Leave</p>"},
                ]
            },
        },
        {
            "url": "https://tenant.traffit.com/job/four",
            "options": {"remote": "0", "_work_model": "Remote"},
        },
        {},
    ],
}
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
)
cases = []
for provider, values in rows.items():
    for row in values:
        if provider == "deel":
            job = deel._parse_job(row, "tenant")
        elif provider == "hibob":
            job = hibob._parse_job(row, "https://tenant.careers.hibob.com")
        else:
            job = traffit._parse_job(row)
        expected = None if job is None else {key: asdict(job)[key] for key in keys}
        cases.append({"provider": provider, "row": row, "expected": expected})
Path(__file__).with_name("python_seventh_provider_core.json").write_text(
    json.dumps(
        {"schema": "jobseek.seventh-provider-core/v1", "cases": cases}, ensure_ascii=False, indent=2
    )
    + "\n"
)
print(f"Frozen {len(cases)} actual Python field cases")
