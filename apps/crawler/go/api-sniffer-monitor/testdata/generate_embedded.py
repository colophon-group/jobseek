"""Freeze pure reference scraper output; no HTTP, credentials or database."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.core.scrapers.embedded import parse_html as embedded
from src.core.scrapers.nextdata import parse_html as nextdata

job = {
    "title": "Software Engineer",
    "description": "<p>Build things.</p>",
    "locations": [{"name": "Zurich"}, {"name": "Paris"}],
    "id": 1234567890123456789,
    "remote": True,
    "qualifications": "<p>Python required.</p>",
    "responsibilities": ["Build", "Review"],
}
fields = {
    "title": "title",
    "description": "description",
    "locations": "locations[].name",
    "metadata.uid": "id",
    "job_location_type": {"path": "remote", "map": {"True": "remote"}},
    "qualifications": "qualifications",
    "responsibilities": "responsibilities",
}
payload = json.dumps({"job": job}, ensure_ascii=False)
script = f'<script id="__NEXT_DATA__">{payload}</script>'
cases = [
    ("nextdata", script, {"path": "job", "fields": fields}, True),
    (
        "script-attributes",
        f"<script type='application/json' id='job'>{payload}</script>",
        {"script_id": "job", "path": "job", "fields": fields},
        False,
    ),
    (
        "last-script",
        '<script id="job">{}</script>' + f'<script id="job">{payload}</script>',
        {"script_id": "job", "path": "job", "fields": fields},
        False,
    ),
    (
        "last-variable",
        f"Client.pageData = {{}};Client.pageData = {payload};Client.pageData = broken;",
        {"variable": "Client.pageData", "path": "job", "fields": fields},
        False,
    ),
    (
        "variable-string-brackets",
        'const POSITION_DATA={"title":"Engineer {x}",'
        '"description":"bracket ] and escaped \\"quote\\""};',
        {"variable": "POSITION_DATA", "fields": {"title": "title", "description": "description"}},
        False,
    ),
    (
        "pattern-unicode-offset",
        f"前置文字 new\u00a0US.Opportunity.CandidateOpportunityDetail({payload});",
        {
            "pattern": r"new\s+US\.Opportunity\.CandidateOpportunityDetail\s*\(",
            "path": "job",
            "fields": fields,
        },
        False,
    ),
    (
        "trailing-comma",
        '<script id="job">{"title":"Engineer","description":"body",}</script>',
        {"script_id": "job", "fields": {"title": "title", "description": "description"}},
        False,
    ),
    (
        "concat-fields",
        script,
        {
            "path": "job",
            "fields": {
                "title": "title",
                "description": ["=Intro", "description", "=Missing", "missing"],
                "metadata.sibling": {"lookup_from": "job", "key_from": "title"},
            },
        },
        True,
    ),
    (
        "reactrouter",
        "window.__staticRouterHydrationData = JSON.parse(" + json.dumps(payload) + ");",
        {"source": "reactrouter", "path": "job", "fields": fields},
        True,
    ),
    (
        "rsc-merged",
        "self.__next_f.push([1,"
        + json.dumps('1:["$","$L1",null,' + payload + ']\n2:{"other":"yes"}\n')
        + "])",
        {"source": "rsc", "path": "job", "fields": fields},
        False,
    ),
    (
        "rsc-last-key",
        "self.__next_f.push([1,"
        + json.dumps('1:{"job":{"title":"Old"}}\n2:' + payload + "\n")
        + "])",
        {"source": "rsc", "path": "job", "fields": fields},
        False,
    ),
    ("missing-path", script, {"path": "missing", "fields": fields}, True),
    ("malformed", '<script id="__NEXT_DATA__">{</script>', {"path": "job", "fields": fields}, True),
]
out = []
for name, html, config, use_nextdata in cases:
    result = (nextdata if use_nextdata else embedded)(html, config)
    expected = {k: v for k, v in asdict(result).items() if v is not None}
    out.append(
        {
            "name": name,
            "html": html,
            "config": config,
            "nextdata": use_nextdata,
            "expected": expected,
        }
    )
Path(__file__).with_name("python_embedded.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n"
)
