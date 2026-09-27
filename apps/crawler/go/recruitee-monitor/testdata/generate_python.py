"""Freeze native parser cases from the authoritative Python field parser."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path

from src.core.enum_normalize import _JOB_LOCATION_TYPE_MAP, _SALARY_UNIT_MAP
from src.core.monitors.recruitee import _parse_job

PROVIDER = "recruitee"
FIELDS = (
    "url",
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "base_salary",
    "metadata",
)
base = {
    "status": "published",
    "careers_url": "https://example.com/job",
    "url": "https://example.com/job",
    "title": "Engineer",
}
cases = [("empty", [])]


def add(name, **fields):
    cases.append((name, [{**base, **fields}]))


add(
    "full",
    description="<p>Build</p>",
    requirements="<p>Ship</p>",
    remote=True,
    hybrid=True,
    on_site=True,
    employment_type_code="fulltime",
    published_at="2026-09-27",
    locations=[{"city": "Zurich", "country": "CH"}, {"city": "Zurich", "country": "CH"}],
    department="Engineering",
    tags=["Go"],
    category_code="TECH",
    id=123,
    key_responsibilities="<p>Deliver</p>",
    key_responsibilities_header="Tasks",
    skills_knowledge_expertise="<p>Go</p>",
    benefits="<p>Time off</p>",
    benefits_header="Benefits",
    location={"city": "Zurich", "province": "ZH"},
    workplace_type="hybrid",
    employment_type="full_time",
    deadline_at="2026-10-01",
    job={"department": {"name": "Tech"}, "division": {"name": "Cloud"}, "requisition_id": "R1"},
)
for value in [None, "", False, 0, 1, [], {}, "text", ["x"]]:
    for field in (
        ["salary", "description", "locations", "tags"]
        if PROVIDER == "recruitee"
        else ["description", "location", "job", "workplace_type"]
    ):
        # Empty mapping/list iteration differs on malformed containers; those remain fail-closed.
        if PROVIDER == "recruitee" and field == "locations" and value in ({}, ""):
            continue
        add(f"{field}-{value!r}", **{field: value})
for unit in [None, "", *_SALARY_UNIT_MAP, "UNKNOWN", "\x1chr\x1f"]:
    add(
        f"salary-{unit!r}",
        salary={"min": 0, "max": 12.5, "currency": "EUR", "period": unit},
        compensation_minimum=0,
        compensation_maximum=12.5,
        compensation_currency="EUR",
        compensation_frequency=unit,
    )
if PROVIDER == "pinpoint":
    for value in [*_JOB_LOCATION_TYPE_MAP, "HYBRID (one day)", "\x1cremote\x1f", "unknown"]:
        add(f"workplace-{value}", workplace_type=value)
    for value in [True, False, None, 0, 1, "Heading"]:
        add(f"header-{value!r}", benefits="Text", benefits_header=value)
    for value in [False, None, 0, True]:
        add(f"visible-{value!r}", compensation_minimum=1, compensation_visible=value)
    add("nontext-location-name", location={"name": 7, "city": "Paris"})
    add("nontext-frequency", compensation_minimum=1, compensation_frequency=5)
else:
    add("draft", status="draft")
    add("remote-priority", remote=False, hybrid=True, on_site=True)
    add("onsite", on_site=True)
    add("flat-location", location="Paris")
    add("bad-period", salary={"min": 1, "period": 5})
rows = []
for name, items in cases:
    body = {"offers" if PROVIDER == "recruitee" else "data": items}
    row = {"name": name, "body": body}
    try:
        jobs = []
        for item in items:
            if PROVIDER == "recruitee" and item.get("status") != "published":
                continue
            job = _parse_job(item)
            if job:
                jobs.append({k: dataclasses.asdict(job)[k] for k in FIELDS})
        row["expected"] = {"jobs": jobs, "truncated": False}
    except (TypeError, AttributeError, ValueError):
        row["error"] = True
    rows.append(row)
Path(__file__).with_name("python_parser.json").write_text(
    json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
)
print(PROVIDER, len(rows))
