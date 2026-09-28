"""Freeze the canonical Python parser's existing regressions, with no origin I/O."""

from __future__ import annotations

import dataclasses
import json
from contextlib import suppress
from pathlib import Path

import pytest

from src.core import jsonld
from src.core.scrapers import jsonld as adapter

cases = []
active = ""
inside = False
original_html = jsonld.parse_html
original_rendered = jsonld.parse_rendered_html
original_selected = adapter._selected_description
descriptions = []


def record(raw, config=None, url="", rendered=False):
    global inside
    if inside:
        return original_html(raw, config)
    inside = True
    config = config or {}
    request = {"url": url, "html": raw, "config": config}
    if not rendered:
        request["config"] = {k: v for k, v in config.items() if k != "defaults_by_url"}
    case = {"name": active, "request": request}
    try:
        result = original_rendered(url, config, raw) if rendered else original_html(raw, config)
        case["expected"] = dataclasses.asdict(result)
        return result
    except Exception as exc:
        case["expected_error"] = type(exc).__name__
        raise
    finally:
        inside = False
        cases.append(case)


def selected(raw, selector):
    case = {"name": active, "html": raw, "selector": selector}
    try:
        result = original_selected(raw, selector)
        case["expected"] = result
        return result
    except ValueError:
        case["expected_error"] = True
        raise
    finally:
        descriptions.append(case)


class Recorder:
    def pytest_sessionstart(self, session):
        adapter._selected_description = selected
        jsonld.parse_html = adapter.parse_html = lambda html, config=None: record(html, config)
        jsonld.parse_rendered_html = adapter.parse_rendered_html = lambda url, config, html: record(
            html, config, url, True
        )

    def pytest_runtest_setup(self, item):
        global active
        active = item.nodeid


try:
    result = pytest.main(["tests/test_jsonld_scraper.py", "-q"], plugins=[Recorder()])
    assert result == 0, result
finally:
    adapter._selected_description = original_selected
    jsonld.parse_html = adapter.parse_html = original_html
    jsonld.parse_rendered_html = adapter.parse_rendered_html = original_rendered


def add(name, posting, config=None, before="", after=""):
    global active
    active = name
    raw = (
        before
        + '<script type="application/ld+json">'
        + json.dumps({"@type": "JobPosting", **posting}, ensure_ascii=False)
        + "</script>"
        + after
    )
    record(raw, config)


for unit in [
    "YEAR",
    "annual",
    "yr",
    "monthly",
    "mo",
    "week",
    "two_weeks",
    "daily",
    "hr",
    "hourly",
    "unknown",
    None,
]:
    for value in [
        50000,
        0,
        True,
        123.4,
        {"minValue": 10, "maxValue": 50},
        {"minValue": 15, "unitText": "HOUR"},
    ]:
        add(
            f"salary-{unit}-{value}",
            {
                "title": "Salary",
                "baseSalary": {"currency": "EUR", "value": value, "unitText": unit},
            },
        )
for value in [
    "  full time ",
    ["FULL_TIME", "PART_TIME"],
    ["Full Time", "Intern"],
    ["TEMPORARY", "CONTRACTOR"],
    ["unknown", 1, "part-time"],
    ["  "],
    None,
]:
    add(f"employment-{value}", {"employmentType": value})
for value in [[0, False, " ", "raw", {"b": 1, "a": [None, True, "x"]}], " raw ", [], 0, None]:
    add(
        f"extras-{value}",
        {"skills": value, "responsibilities": value, "educationRequirements": value},
    )
for location in [
    {
        "name": "Acme",
        "address": {
            "addressLocality": " Zürich ",
            "addressRegion": "ZH",
            "addressCountry": {"name": "Switzerland"},
        },
    },
    {"name": "None", "address": "Berlin"},
    [{"address": {"addressCountry": "DE"}}, {"address": {"addressCountry": "DE"}}],
    None,
]:
    for cfg in [
        {},
        {"ignore_locations": True},
        {"ignore_address_region": True},
        {"ignore_locations": 1},
        {"ignore_locations": []},
    ]:
        add(
            f"locations-{location}-{cfg}",
            {"title": "Job", "hiringOrganization": {"name": "acme"}, "jobLocation": location},
            cfg,
        )
for content in ["<b>R&amp;amp;D</b>", "&amp;#xa;&amp;#9;&amp;#13; &amp;amp;", ""]:
    add(f"description-{content}", {"description": content})
for raw in [
    (
        '<script type="text/plain" type="application/ld+json">{"@type'
        '":"JobPosting","title":"last attr"}</script>'
    ),
    (
        '<script type="application/ld+json" type="text/plain">{"@type'
        '":"JobPosting","title":"ignored"}</script>'
    ),
    '<script type="APPLICATION/LD+JSON">{"@type":"JobPosting","title":"ignored"}</script>',
    (
        '<meta name="job-title" content="one" content="two"<'
        '>meta name="job-description" content="&lt;p&gt;Hi&lt;/p&gt;">'
    ),
    (
        '<script type="application/ld+json">{"@graph":[{"@type":["Thi'
        'ng","JobPosting"],"Title":"graph","Skills":[true,1.0]}]}</sc'
        "ript>"
    ),
    (
        '<script type="application/ld+json">{"@type":"JobPosting","da'
        'tePosted":"2026-09-28" "hiringOrganization":{"name":"Acme"},'
        '"description":"pay \\$10\nnow"}</script>'
    ),
]:
    active = "source-edge"
    record(raw)
for config in [
    {
        "defaults_by_url": {
            "https://example.com/job": {
                "title": "Missing",
                "locations": ["Paris"],
                "base_salary": {"min": 1},
            }
        }
    },
    {"defaults_by_url": []},
    {"defaults_by_url": {"https://example.com/job": {"unknown": "bad"}}},
    {"defaults_by_url": {"https://elsewhere/job": {"unknown": "unused"}}},
]:
    active = "exact-url-default"
    with suppress(ValueError):
        record(
            '<script type="application/ld+json">{"@type":"JobPosting","extras":{}}</script>',
            config,
            "https://example.com/job",
            True,
        )
unique = {}
for case in cases:
    # Non-string Python dict keys cannot cross the JSON boundary; the bridge
    # rejects those before serialization rather than changing them into strings.
    if json.loads(json.dumps(case["request"])) == case["request"]:
        unique.setdefault(json.dumps(case["request"], sort_keys=True), case)
output = Path(__file__).with_name("python_cases.json")
output.write_text(json.dumps(list(unique.values()), indent=2, ensure_ascii=False) + "\n")
print(f"Froze {len(unique)} distinct JSON-LD parser cases")

for fragment in [
    "<p>Build &amp; test 'now'.</p>",
    '<p title="a &amp; b">text<br>next&nbsp;line</p>',
    "<ul><li>one<li>two</ul>",
    "text<!--comment--><hr>",
    '<div data-x="a"b">nested</div>',
]:
    active = "selected-description-edge"
    selected(
        '<section class="vacancy_description">' + fragment + "</section>", ".vacancy_description"
    )
Path(__file__).with_name("python_descriptions.json").write_text(
    json.dumps(
        [c for c in descriptions if isinstance(c["selector"], str)], ensure_ascii=False, indent=2
    )
    + "\n"
)
