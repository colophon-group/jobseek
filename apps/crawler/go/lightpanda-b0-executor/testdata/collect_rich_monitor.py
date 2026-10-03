"""Freeze standard Greenhouse rich records from the actual Python writer."""

from __future__ import annotations

import copy
import json
import os
from pathlib import Path

os.environ["JOB_ENRICHMENT_ENGINE"] = "python"

from src.core.monitors.greenhouse import _parse_job
from src.processing import board

occ = {"software-engineer": 41, "data-engineer": 52}
sen = {"senior": 7, "junior": 3, "intern": 9}
tech = {"python": 4, "go": 2, "postgresql": 4}
rates = {"CHF": 1.05, "USD": 0.9}
location_inputs = []


def resolve(resolver, locations, kind, language):
    location_inputs.append([locations, kind, language])
    return ([2], ["hybrid"]) if locations else (None, None)


board._resolve_locations_sync = resolve
base = {
    "absolute_url": "https://job-boards.greenhouse.io/fixture/jobs/1",
    "title": "Senior Software Engineer",
    "content": "<p>Python and Go. Salary CHF 100000-120000 yearly. 5+ years of experience.</p>",
    "location": {"name": "Zurich"},
    "language": "en",
}
cases = []


def add(name, changes, rich_overrides=None):
    raw = copy.deepcopy(base)
    raw.update(changes)
    job = _parse_job(raw)
    assert job is not None
    for key, value in (rich_overrides or {}).items():
        setattr(job, key, value)
    content = {
        "title": job.title,
        "description": job.description,
        "locations": job.locations,
        "language": job.language,
    }
    for key in rich_overrides or {}:
        content[key] = getattr(job, key)
    location_inputs.clear()
    records, _ = board._build_rich_new_records(
        [(job.url, job)],
        company_id="00000000-0000-0000-0000-000000000001",
        board_id="00000000-0000-0000-0000-000000000002",
        identity_by_url={job.url: job.url},
        uses_durable_lane=False,
        loc_resolver=None,
        rates=rates,
        tech_id_map=tech,
        occ_ids=occ,
        sen_ids=sen,
    )
    record = records[0]
    # Detail ContentFields positional order, captured from the rich insert
    # contract (employment, titles, locales, locations, tech, salary, etc.).
    fields = [record[2], *record[4:8], record[15], *record[8:15], *record[16:18]]
    description = board._coerce_text(job.description)
    staged = None
    if description:
        staged = {
            "html": description,
            "locale": board._coerce_text(job.language) or "en",
            "hash": board.content_hash(description),
        }
    cases.append(
        {
            "name": name,
            "content": content,
            "expected": {
                "fields": fields,
                "description": staged,
                "location_inputs": copy.deepcopy(location_inputs[0]),
            },
        }
    )


add("rich", {})
add("missing_title_is_valid", {"title": None})
add("garbage_title_is_not_detail_failure", {"title": "Access Denied"})
add("blank_title", {"title": "  \n  "})
add("decoded_title", {"title": "Senior Software Engineer &amp; Platform"})
add("missing_content_and_language_defaults_en", {"content": None, "language": None})
add("empty_content", {"content": "", "language": ""})
add("whitespace_content", {"content": " \n \t ", "language": None})
add("removed_content", {"content": "<script>removed</script>", "language": None})
add("missing_locations", {"location": None})
add("deduplicated_locations", {"offices": [{"name": "Zurich"}, {"name": "London"}]})
add("stray_toggle", {"content": "<p>Python</p><a>Read more</a>"})
add("false_language_detected", {"language": False})
add("false_language_without_description", {"language": False, "content": None})
add("list_language_coerced", {"language": ["en", "fr", "en"]})
add("description_trimmed_before_hash", {"content": "\n<p>Café 東京</p>\n", "language": "fr"})
add("nullable_derived_fields", {"content": "<p>Join our team.</p>"})
add(
    "german_language_detected",
    {
        "content": (
            "<p>Wir suchen einen erfahrenen Softwareentwickler für unser Team. "
            "Sie entwickeln neue Anwendungen und arbeiten mit unseren Kunden zusammen.</p>"
        ),
        "language": None,
    },
)

add("rich_internship_remote", {}, {"employment_type": "Internship", "job_location_type": "remote"})
add("rich_fulltime_hybrid", {}, {"employment_type": "Full-time", "job_location_type": "hybrid"})
add(
    "rich_coerced_fields",
    {},
    {"employment_type": ["Internship", "Full-time"], "job_location_type": ["remote", "hybrid"]},
)
add("rich_empty_fields", {}, {"employment_type": " \n ", "job_location_type": None})

output = {
    "schema_version": 1,
    "oracle": (
        "Python src.processing.board._build_rich_new_records and rich description staging; "
        "standard Greenhouse token/skip plus rich employment/location-type inputs"
    ),
    "occupations": occ,
    "seniorities": sen,
    "technologies": tech,
    "rates": rates,
    "cases": cases,
}
Path("go/lightpanda-b0-executor/testdata/python_rich_monitor.json").write_text(
    json.dumps(output, ensure_ascii=False, indent=2) + "\n"
)
