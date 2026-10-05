"""Freeze actual NextData rich projection and URL-template semantics."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors.nextdata import _extract_rich

cases = [
    (
        "rich",
        [
            {
                "id": 90071992547409931234,
                "title": "Senior Engineer",
                "body": "<p>Build.</p>",
                "city": ["Zurich", "Paris"],
                "remote": True,
            }
        ],
        "https://example.com/jobs/{id}",
        [],
        {
            "title": "title",
            "description": "body",
            "locations": "city",
            "job_location_type": {"path": "remote", "map": {"True": "remote"}},
            "metadata.id": "id",
        },
    ),
    (
        "slugs",
        [{"id": 1, "title": "Développeur & C++", "city": "Zürich"}],
        "https://example.com/jobs/{id}-{slug}",
        ["title", "city"],
        {"title": "title", "locations": "city"},
    ),
    (
        "scalar-templates",
        [{"id": True, "title": 42}, {"id": 1.5, "title": False}],
        "https://example.com/jobs/{id}",
        [],
        {"title": "title"},
    ),
    (
        "missing-template",
        [{"title": "No ID"}, None, 42],
        "https://example.com/jobs/{id}",
        [],
        {"title": "title"},
    ),
    (
        "metadata-unknown-target",
        [{"id": 1, "language": "sv", "department": "Engineering"}],
        "https://example.com/jobs/{id}",
        [],
        {"language": "language", "department": "department"},
    ),
    (
        "duplicates-preserved",
        [{"id": 1, "title": "First"}, {"id": 1, "title": "Last"}],
        "https://example.com/jobs/{id}",
        [],
        {"title": "title"},
    ),
    (
        "field-concat",
        [{"id": 1, "city": "Zurich", "country": "Switzerland", "body": "&lt;p&gt;Build&lt;/p&gt;"}],
        "https://example.com/jobs/{id}",
        [],
        {
            "locations": {"concat": ["city", "country"], "separator": ", "},
            "description": {"path": "body", "html_unescape": True},
        },
    ),
]
output = [
    {
        "name": name,
        "items": items,
        "template": template,
        "slug_fields": slugs,
        "fields": fields,
        "expected": [asdict(job) for job in _extract_rich(items, template, slugs, fields)],
    }
    for name, items, template, slugs, fields in cases
]
Path(__file__).with_name("python_nextdata_inventory.json").write_text(
    json.dumps({"cases": output}, indent=2, ensure_ascii=False) + "\n"
)
