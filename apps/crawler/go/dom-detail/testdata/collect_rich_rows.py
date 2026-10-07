"""Freeze real Python DOM rich-row semantics for the next grouped migration."""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path

from src.core.monitors.dom import _extract_rich_rows_static, _validated_rich_rows

BASE = "https://example.com/careers"
ROW = (
    '<article class="job"><a href="/jobs/123"><h2>Senior Engineer 東京</h2>'
    '</a><span class="city">Zurich</span><span class="country">'
    'Switzerland</span><span class="team">Platform</span>'
    '<div class="description"><p>Build &amp; learn.</p></div></article>'
)
OPTIONS = {"row_selector": ".job", "link_selector": "a"}
CASES = []


def case(name, changes=None, source=ROW, matcher=None, allow_empty=False):
    options = OPTIONS | (changes or {})
    try:
        config = _validated_rich_rows(options)
        jobs = _extract_rich_rows_static(
            source,
            BASE,
            config,
            re.compile(matcher) if matcher else None,
            allow_empty=allow_empty,
        )
        result = {"jobs": [dataclasses.asdict(job) for job in jobs], "error": False}
    except ValueError:
        result = {"jobs": [], "error": True}
    CASES.append(
        {
            "name": name,
            "options": options,
            "source": source,
            "base_url": BASE,
            "include": matcher,
            "allow_empty": allow_empty,
            **result,
        }
    )


case("basic")
case("same-duplicate", source=ROW + ROW)
case("conflicting-duplicate", source=ROW + ROW.replace("Senior", "Lead"))
case(
    "prefer-longer-title",
    {"duplicate_url_policy": "prefer_longer_title"},
    source=ROW + ROW.replace("Senior", "Principal Senior"),
)
case("metadata", {"metadata_selectors": {"team": ".team"}})
case("missing-metadata", {"metadata_selectors": {"team": ".absent"}})
case("description", {"description_selector": ".description"})
case("missing-description", {"description_selector": ".absent"})
case(
    "adjacent-description",
    {"description_next_selector": ".next"},
    source=ROW + '<aside class="next"><p>Adjacent &amp; useful.</p></aside>',
)
case("missing-adjacent", {"description_next_selector": ".next"})
case(
    "section",
    {"section_start": {"selector": "#start"}, "section_end": {"selector": "#end"}},
    source='<h1 id="start">Open</h1>' + ROW + '<h1 id="end">Other</h1>' + ROW,
)
case(
    "missing-section",
    {"section_start": {"selector": "#start"}, "section_end": {"selector": "#end"}},
)
case(
    "ambiguous-section",
    {"section_start": {"selector": "#start"}, "section_end": {"selector": "#end"}},
    source='<h1 id="start">Open</h1><h2 id="start">Again</h2>' + ROW + '<h1 id="end">Other</h1>',
)
case(
    "active-lifecycle",
    {
        "active_urls": ["https://example.com/jobs/123"],
        "inactive_urls": ["https://example.com/jobs/456"],
    },
    source=ROW.replace("/jobs/123", "/jobs/123?tracking=1#apply")
    + ROW.replace("/jobs/123", "/jobs/456"),
)
case(
    "unclassified-lifecycle",
    {
        "active_urls": ["https://example.com/jobs/789"],
        "inactive_urls": ["https://example.com/jobs/456"],
    },
)
case(
    "template",
    {"url_template": "https://example.com/jobs/{value}", "link_attr": "data-id"},
    source=ROW.replace('href="/jobs/123"', 'data-id="123"'),
)
case("cross-origin-template", {"url_template": "https://other.example/jobs/{value}"})
case("locations-joined", {"location_selectors": [".city", ".country"]})
case(
    "locations-first",
    {"location_selectors": [".absent", ".city", ".country"], "location_selector_mode": "first"},
)
case(
    "locations-semicolon",
    {"location_selectors": [".city"], "location_separator": ";"},
    source=ROW.replace(">Zurich<", ">Zurich<b>Geneva</b><"),
)
case(
    "locations-pattern", {"location_selectors": [".city"], "location_value_patterns": ["^Zurich$"]}
)
case(
    "locations-pattern-reject",
    {"location_selectors": [".city"], "location_value_patterns": ["^Geneva$"]},
)
case("missing-location", {"location_selectors": [".absent"]})
case(
    "missing-location-allowed", {"location_selectors": [".absent"], "allow_missing_locations": True}
)
case("default-location", {"default_locations": ["Remote", "Switzerland"]})
case("title-selector", {"title_selector": "h2"})
case("title-regex", {"title_regex": "Senior (.*)"})
case("title-regex-reject", {"title_regex": "Principal (.*)"})
case("title-replacements", {"title_replacements": {"Senior": "Lead", "東京": "Tokyo"}})
case("required-selector", {"row_required_selector": ".team"})
case("required-selector-filter", {"row_required_selector": ".absent"})
case("row-text-pattern", {"row_text_pattern": "Engineer"})
case("row-text-pattern-filter", {"row_text_pattern": "Designer"})
case("total-exact", {"total_selector": "#total"}, source='<p id="total">1</p>' + ROW)
case("total-mismatch", {"total_selector": "#total"}, source='<p id="total">2</p>' + ROW)
case("total-malformed", {"total_selector": "#total"}, source='<p id="total">1 job</p>' + ROW)
case("explicit-empty", {"total_selector": "#total"}, source='<p id="total">0</p>', allow_empty=True)
case("missing-rows", source="<p>Nothing</p>")
case("include", matcher="/jobs/123")
case("include-excludes-all", matcher="/jobs/456")
case("unknown-key", {"unimplemented": True})
case("missing-title", source=ROW.replace("Senior Engineer 東京", ""))
case("missing-link", source=ROW.replace('href="/jobs/123"', ""))

for key in ["link_attr", "location_selector_mode", "location_separator", "duplicate_url_policy"]:
    case("null-" + key, {key: None})
case("ordered-title-replacements", {"title_replacements": {"Senior": "Lead", "Lead": "Staff"}})

case("has-direct-child", {"row_selector": "article:has(> a[href^='/jobs/'])"})
case(
    "has-direct-child-excludes-grandchild",
    {"row_selector": "article:has(> a[href^='/jobs/'])"},
    source=ROW
    + ROW.replace("<a ", "<div><a ")
    .replace("</a>", "</a></div>")
    .replace("/jobs/123", "/jobs/456"),
)

case(
    "unicode-total-digits",
    {"total_selector": ".total"},
    source='<span class="total">1١</span>'
    + "".join(ROW.replace("/jobs/123", f"/jobs/{i}") for i in range(11)),
)
case(
    "unicode-total-first-digit-refused",
    {"total_selector": ".total"},
    source='<span class="total">١</span>' + ROW,
)

Path(__file__).with_name("python_rich_rows.json").write_text(
    json.dumps({"cases": CASES}, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(CASES)} actual Python rich-row cases")
