"""Record canonical Python extraction calls from existing regression tests."""

from __future__ import annotations

import dataclasses
import json
import os
from datetime import datetime
from pathlib import Path

import pytest

import src.core.scrapers.dom as scraper
import src.shared.extract as extract

os.environ["DOM_GO_PARSE_ENABLED"] = "0"
cases = []


def record(mode, fn, input_of, result_of):
    def wrapped(*args, **kwargs):
        request = {"mode": mode, **input_of(*args, **kwargs)}
        try:
            result = fn(*args, **kwargs)
        except Exception as e:
            cases.append({"request": request, "error": type(e).__name__})
            raise
        else:
            cases.append({"request": request, "expected": result_of(result)})
            return result

    return wrapped


extract.flatten = record(
    "flatten", extract.flatten, lambda html, **kw: {"html": html, **kw}, lambda r: r
)
extract.walk_steps = record(
    "walk",
    extract.walk_steps,
    lambda elements, steps, **kw: {"elements": elements, "steps": steps, **kw},
    lambda r: {"fields": r[0], "cursor": r[1]},
)
scraper.flatten = extract.flatten
scraper.walk_steps = extract.walk_steps
scraper.parse_html = record(
    "parse",
    scraper.parse_html,
    lambda html, config: {"html": html, "config": config},
    dataclasses.asdict,
)
root = Path(__file__).resolve().parents[3]
code = pytest.main(
    [str(root / "tests/test_extract.py"), str(root / "tests/test_dom_scraper.py"), "-q"]
)
# Current production date directives and canonical URL/default precedence.
formats = [
    "%A, %B %d, %Y",
    "%B %d, %Y",
    "%Y-%m-%d",
    "%b %d, %Y",
    "%d %B %Y",
    "%d %B, %Y",
    "%d %b %Y",
    "%d-%b-%Y",
    "%d-%m-%Y",
    "%d.%m.%Y",
    "%d/%m/%Y",
    "%m/%d/%Y",
]
for date in [datetime(2026, 9, 28), datetime(2024, 2, 29), datetime(2001, 1, 1)]:
    for fmt in formats:
        extract.walk_steps(
            [{"tag": "p", "attrs": {}, "text": date.strftime(fmt)}],
            [{"field": "date_posted", "date_input_format": fmt}],
        )
for url in ["https://example.test/job#selected", "https://example.test/job#%73elected"]:
    html = "<h1>Navigation</h1><h1 id='selected'>Role</h1><p>Work.</p>"
    config = {
        "steps": [{"tag": "h1", "field": "title"}, {"tag": "p", "field": "description"}],
        "defaults": {"locations": ["Board"]},
        "defaults_by_regex": [
            {"field": "title", "pattern": "^Role$", "defaults": {"locations": ["Conditional"]}}
        ],
        "defaults_by_url": {"https://example.test/job#selected": {"locations": ["Exact"]}},
    }
    elements = scraper._flatten_html(html, config)
    raw, _ = extract.walk_steps(
        elements, config["steps"], start=scraper._fragment_start(url, elements)
    )
    expected = scraper._map_to_job_content(scraper._apply_defaults(raw, config, url=url))
    cases.append(
        {
            "request": {"mode": "parse", "html": html, "config": config, "url": url},
            "expected": dataclasses.asdict(expected),
        }
    )
unique = {json.dumps(c, sort_keys=True, ensure_ascii=False): c for c in cases}
Path(__file__).with_name("python_cases.json").write_text(
    json.dumps(list(unique.values()), indent=2, ensure_ascii=False) + "\n"
)
print(f"Retained {len(unique)} distinct Python extraction cases")
raise SystemExit(code)
