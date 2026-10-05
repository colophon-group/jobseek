"""Freeze the existing Python extractor's results; offline verification only."""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.shared.nextdata import extract_field

cases = []


def case(name, item, spec, root=None):
    record = {"name": name, "root": root if root is not None else item, "item": item, "spec": spec}
    try:
        record["expected"] = extract_field(item, spec, root=record["root"])
    except (ValueError, TypeError):
        record["error"] = True
    cases.append(record)


for value in [
    None,
    True,
    False,
    0,
    -2,
    1.0,
    1.25,
    1000000.0,
    1e-5,
    "",
    " Zürich ",
    ["a", None, 3, True],
    {"z": 1, "a": "O'Reilly"},
]:
    case(f"scalar-{len(cases)}", {"x": value}, "x")
case(
    "projection",
    {"locations": [{"name": "Zurich"}, {"name": None}, {"name": "Bern"}]},
    "locations[].name",
)
case(
    "filter",
    {"rows": [{"value": 2, "label": "low"}, {"value": 10, "label": "high"}]},
    "rows[?value > `5`].label",
)
case("functions", {"rows": [{"label": "A"}, {"label": "B"}]}, "join(', ', rows[].label)")
case("not-null", {"name": None, "other": "fallback"}, "not_null(name, other)")
case("to-string", {"value": 1.0}, "to_string(value)")
case("literal", {}, "=<h2>Requirements</h2>")
case("missing", {}, "missing.nested")
case("heading-missing", {"description": "Body"}, ["=<h2>Missing</h2>", "missing", "description"])
case("heading-present", {"description": "Body"}, ["=<h2>About</h2>", "description", "=Tail"])
case("constants-only", {}, ["=Hello", "=world"])
case("no-orphan-tail", {}, ["missing", "=Tail"])
case("empty-list", {"x": [None, "", "  "]}, ["=Heading", "x"])
case("plain-lines", {"x": "Intro\n\n- One\n- Two\nEnd\n"}, ["x"])
case("html-unchanged", {"x": "<p>Intro</p>\n- One"}, ["x"])
case(
    "separator",
    {"city": "Zurich", "country": "Switzerland"},
    {"concat": ["city", "country"], "separator": ", "},
)
case(
    "each",
    {"rows": [{"title": "First", "body": None}, {"title": "Second", "body": "Body"}, "Tail"]},
    [{"each": "rows", "wrap": "<h3>{title}</h3>{body}"}],
)
case("bool-map", {"remote": True}, {"path": "remote", "map": {"True": "remote", "False": "onsite"}})
case(
    "array-map",
    {"values": [True, None, 1, "missing"]},
    {"path": "values", "map": {"True": "remote", "None": None, "1": 2}},
)
case("scalar-map-null", {"value": 1}, {"path": "value", "map": {"1": None}})
case(
    "html-unescape",
    {"x": ["&lt;p&gt;Body&lt;/p&gt;", "A&amp;B"]},
    {"path": "x", "html_unescape": True},
)
case(
    "unescape-precedes-timestamp",
    {"x": "&amp;"},
    {"path": "x", "html_unescape": True, "timestamp_unit": "seconds"},
)
for unit, value in [
    ("seconds", 0),
    ("seconds", -0.25),
    ("seconds", 1700000000.125),
    ("milliseconds", 1700000000125),
    ("milliseconds", [0, 1000]),
]:
    case(f"timestamp-{len(cases)}", {"x": value}, {"path": "x", "timestamp_unit": unit})
case("timestamp-invalid", {"x": "invalid"}, {"path": "x", "timestamp_unit": "seconds"})
case(
    "pattern-order",
    {"attrs": {"other": "skip", "department_2": ["B", None], "department_1": "A"}},
    {"path": "attrs", "key_pattern": "^department_"},
)
case(
    "pattern-scalar",
    {"attrs": {"department_1": "A"}},
    {"path": "attrs", "key_pattern": "^department_"},
)
case("pattern-non-object", {"attrs": ["invalid"]}, {"path": "attrs", "key_pattern": "^department_"})
case(
    "lookup",
    {"department": 12},
    {"lookup_from": "lookups.departments", "key_from": "department"},
    {"lookups": {"departments": {"12": "Engineering"}}},
)
case(
    "lookup-missing",
    {"department": 12},
    {"lookup_from": "lookups.departments", "key_from": "department"},
    {},
)
case("invalid-spec", {}, 42)

Path(__file__).with_name("python_fields.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(cases)} Python field cases")
