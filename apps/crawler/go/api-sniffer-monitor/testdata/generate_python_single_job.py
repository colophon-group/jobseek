"""Freeze the original bounded browser detail response selector."""

from __future__ import annotations

import json
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.core.scrapers.api_sniffer import (  # noqa: E402
    _DATE_FIELDS,
    _DESCRIPTION_FIELDS,
    _EMPLOYMENT_TYPE_FIELDS,
    _LOCATION_FIELDS,
    _WORKPLACE_TYPE_FIELDS,
    _extract_from_object,
    _find_single_job,
)


def job(name: str, **extra: object) -> dict:
    return {"title": name, "description": "A complete original job description. " * 3, **extra}


def owns(root: object, selected: dict) -> bool:
    if root is selected:
        return True
    if isinstance(root, dict):
        return any(owns(value, selected) for value in root.values())
    if isinstance(root, list):
        return any(owns(value, selected) for value in root)
    return False


cases = []


def add(name: str, bodies: list, path: str | None = None) -> None:
    bodies = json.loads(json.dumps(bodies))
    selected = _find_single_job([SimpleNamespace(body=b) for b in bodies], json_path=path)
    owner = -1 if selected is None else next(i for i, b in enumerate(bodies) if owns(b, selected))
    cases.append(
        {"name": name, "bodies": bodies, "path": path or "", "expected": selected, "owner": owner}
    )


add("empty", [])
add("non-object-responses", [None, False, "text", 42, [job("ignored")]])
add("title-required", [{"description": "Long description " * 10, "location": "Zurich"}])
add("root", [job("Root")])
add("response-order-tie", [job("First"), job("Second")])
add("more-complete-response", [job("First"), job("Second", location=None)])
for title in (
    "title",
    "name",
    "jobTitle",
    "job_title",
    "positionTitle",
    "label",
    "heading",
    "role",
    "jobName",
    "jobOpeningName",
    "Title__c",
    "JOB_TITLE",
):
    add("title-alias-" + title, [{title: "Selected", "description": "x" * 51}])
for description in _DESCRIPTION_FIELDS:
    add(
        "description-alias-" + description,
        [job("First"), {"title": "Second", description: "x" * 51, "location": "Geneva"}],
    )
for location in _LOCATION_FIELDS:
    add("location-alias-" + location, [job("First"), job("Second", **{location: None})])
for length in (50, 51):
    add(
        f"unicode-description-threshold-{length}",
        [{"title": "First"}, {"title": "Second", "description": "λ" * length}],
    )
for outer in ("data", "result", "job", "posting", "position", "details", "funnel"):
    add("one-level-" + outer, [{outer: job("Nested")}])
    for inner in ("jobPosting", "job", "posting", "position", "result"):
        add("two-level-" + outer + "-" + inner, [{outer: {inner: job("Nested")}}])
add(
    "explicit-path-preference",
    [job("Root", envelope={"chosen": {"title": "Explicit"}})],
    "envelope.chosen",
)
add(
    "explicit-path-without-title",
    [job("Root", envelope={"chosen": {"description": "x" * 100}})],
    "envelope.chosen",
)
add("explicit-path-missing", [job("Root")], "missing.job")
add("explicit-path-response-order", [{"data": job("First")}, {"data": job("Second")}], "data")
add("rpc-envelope", [{"actions": [{"returnValue": {"nested": job("Aura")}}]}])
add("envelope-order-tie", [{"first": job("First"), "last": job("Last")}])
for size in (0, 10, 11, 50, 51):
    add(f"array-traversal-bound-{size}", [{"envelope": [job(str(i)) for i in range(size)]}])
    add(
        f"listing-penalty-{size}",
        [{"title": "Root", "children": [job(str(i)) for i in range(size)]}],
    )
for depth in (7, 8, 9):
    nested = job("Deep")
    for _ in range(depth):
        nested = {"envelope": nested}
    add(f"depth-bound-{depth}", [nested])
add(
    "visited-bound",
    [{"candidate": job("Beyond budget"), **{str(i): {"one": {"two": None}} for i in range(200)}}],
)
add("enough-object-keys", [job("First"), job("Second", a=None, b=None, c=None)])

target = Path(__file__).with_name("python_single_job.json")
target.write_text(json.dumps(cases, indent=2, ensure_ascii=False) + "\n")
print(f"wrote {len(cases)} original response-selection cases")

projections = []


def project(name: str, obj: dict, fields: dict | None = None) -> None:
    obj = json.loads(json.dumps(obj))
    config = {} if fields is None else {"fields": fields}
    try:
        expected = asdict(_extract_from_object(obj, config))
        failed = False
    except (TypeError, ValueError, KeyError):
        expected, failed = None, True
    projections.append(
        {"name": name, "object": obj, "fields": fields, "expected": expected, "failed": failed}
    )


project("empty-object", {})
project("plain-heuristic", job("Original"))
for title in ("title", "name", "jobTitle", "job_title", "Title__c", "ROLE"):
    for value in ("Original", None, 42):
        project(f"title-{title}-{value}", {title: value})
for description in _DESCRIPTION_FIELDS:
    for length in (10, 11):
        project(
            f"description-{description}-{length}", {"title": "Original", description: "λ" * length}
        )
for location in _LOCATION_FIELDS:
    for value in (
        "Zurich",
        ["Zurich", "Geneva"],
        [{"city": "Zurich"}, {"label": "Geneva"}],
        [],
        None,
    ):
        project(f"location-{location}-{len(projections)}", {"title": "Original", location: value})
for value in (
    [{"city": "Zurich"}, "Geneva"],
    [{"city": "Zurich"}, "city"],
    [{"city": "Zurich"}, ["Geneva"]],
    [{"city": "Zurich"}, None],
    [{"city": "Zurich"}, 42],
    ["Zurich", {"city": "Geneva"}],
):
    project(f"mixed-location-{len(projections)}", {"title": "Original", "locations": value})
project("location-precedence", {"title": "Original", "location": None, "locations": ["Ignored"]})
for name, keys in (
    ("employment_type", _EMPLOYMENT_TYPE_FIELDS),
    ("date_posted", _DATE_FIELDS),
    ("job_location_type", _WORKPLACE_TYPE_FIELDS),
):
    for key in keys:
        for value in ("Original", None, True, False):
            project(f"{name}-{key}-{value}", {"title": "Original", key: value})
project(
    "mapped-scalars",
    {"name": "Original", "body": "<p>Complete</p>", "city": "Zurich"},
    {"title": "name", "description": "body", "locations": "city"},
)
project(
    "mapped-metadata",
    {"team": "Engineering", "state": "PUBLISHED"},
    {"metadata.team": "team", "source_status": "state"},
)
project(
    "mapped-extras",
    {"tasks": [None, "", " ", "Build"], "expires": "2026-12-01"},
    {"responsibilities": "tasks", "valid_through": "expires"},
)
project(
    "mapped-concatenation", {"a": "<h2>Role</h2>", "b": "<p>Build</p>"}, {"description": ["a", "b"]}
)
project(
    "mapped-value-translation",
    {"type": "INTERN"},
    {"employment_type": {"path": "type", "map": {"INTERN": "Internship"}}},
)
project("mapped-empty-does-not-fallback", job("Not inherited"), {"title": "missing"})
project("mapped-empty-uses-heuristic", job("Original"), {})
target.with_name("python_single_job_projection.json").write_text(
    json.dumps(projections, indent=2, ensure_ascii=False) + "\n"
)
print(f"wrote {len(projections)} original detail-projection cases")
