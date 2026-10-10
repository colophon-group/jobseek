"""Freeze static inline listing contracts from the original Python extractor."""

from __future__ import annotations

import html
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors.dom import (
    _extract_onclick_links_static,
    _extract_script_json_links,
    _validated_script_json_links,
)

base = "https://jobs.example/jobs/"
config = {"variable": "jobs", "url_field": "slug", "url_template": base + "{value}/"}
rich = {
    "function": "grid",
    "argument_index": 2,
    "url_field": "link",
    "url_template": "{value}",
    "title_field": "title",
    "locations_field": "locations",
}
row = {
    "link": base + "one/",
    "title": "  R&amp;D Engineer  ",
    "locations": [" Geneva ", "Geneva", " Z&uuml;rich "],
}
cases = []


def add(name, source, options=None, include=""):
    c = {
        "name": name,
        "source": source,
        "base": base,
        "config": options or config,
        "include": include,
    }
    try:
        result = _extract_script_json_links(
            source,
            base,
            _validated_script_json_links(c["config"]),
            re.compile(include) if include else None,
        )
        c["jobs"] = (
            sorted([{"url": x} for x in result], key=lambda x: x["url"])
            if isinstance(result, set)
            else [{"url": x.url, "title": x.title, "locations": x.locations} for x in result]
        )
    except (ValueError, TypeError):
        c["error"] = True
    cases.append(c)


for name, payload in [
    ("two", '[{"slug":"one"},{"slug":"two"}]'),
    ("unicode", '[{"slug":"Genève"}]'),
    ("zero", "[]"),
    ("duplicate", '[{"slug":"one"},{"slug":"one"}]'),
    ("not-array", "{}"),
    ("non-object", "[42]"),
    ("missing", "[{}]"),
    ("numeric", '[{"slug":42}]'),
    ("null", '[{"slug":null}]'),
    ("nul", json.dumps([{"slug": "a\x00b"}])),
    ("oversized", json.dumps([{"slug": "x" * 513}])),
]:
    add(name, "const jobs=" + payload + "; trailing();")
add("multiple-assignments", "const jobs=[]; let jobs=[];")
add("missing-assignment", "const other=[];")
add("whitespace", '\x1cconst\u00a0jobs\u2003=\x1f[{"slug":"one"}];')
add("filter-reject", 'const jobs=[{"slug":"one"}];', include="/other/")
add("filter-match", 'const jobs=[{"slug":"one"}];', include="/one/")
for name, value in [("cross-origin", "https://other.example/jobs/one"), ("relative", "/jobs/one")]:
    add(
        name,
        "const jobs=" + json.dumps([{"slug": value}]) + ";",
        {**config, "url_template": "{value}"},
    )
add("nested-args", 'grid({nested:[1,2]}, "a,b)", ' + json.dumps([row]) + ", trailing);", rich)
add("comments", "grid(/* first */1, // next\n2, /* inventory */" + json.dumps([row]) + ");", rich)
add(
    "html-escape",
    html.escape("grid(1,2," + json.dumps([row]) + ");"),
    {**rich, "html_unescape": True},
)
add("function-identity", "notgrid(1,2,[]); grid(1,2," + json.dumps([row]) + ");", rich)
add("duplicate-function", "grid(1,2,[]); grid(1,2,[]);", rich)
add("malformed-prefix", "grid([},2,[]);", rich)
add("missing-argument", "grid(1,2);", rich)
for name, changed in [
    ("missing-title", {"title": None}),
    ("blank-title", {"title": "  "}),
    ("missing-locations", {"locations": None}),
    ("empty-locations", {"locations": []}),
    ("location-cap", {"locations": ["Geneva"] * 33}),
    ("blank-location", {"locations": [" "]}),
    ("scalar-location", {"locations": " Geneva "}),
    ("numeric-location", {"locations": [1]}),
    ("entity-empty-title", {"title": "&nbsp;"}),
]:
    add(name, "grid(1,2," + json.dumps([{**row, **changed}]) + ");", rich)
for name, changed in [
    ("unknown-option", {"unknown": True}),
    ("both-source", {"function": "grid", "argument_index": 0}),
    ("missing-template", {"url_template": None}),
    ("bad-name", {"variable": "invalid.name"}),
    ("unpaired-rich", {"title_field": "title"}),
    ("integer-boolean", {"html_unescape": 1}),
]:
    add(name, "const jobs=[];", {**config, **changed})
add("boolean-argument", "grid([]);", {**rich, "argument_index": True})
add("argument-cap", "grid([]);", {**rich, "argument_index": 16})
Path(__file__).with_name("python_inline_listings.json").write_text(
    json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
)
print(f"{len(cases)} original Python contracts frozen")


clicks = []


def click(name, source, include=""):
    c = {"name": name, "source": source, "base": base, "selector": "tr.item", "include": include}
    try:
        c["urls"] = sorted(
            _extract_onclick_links_static(
                source, base, c["selector"], re.compile(include) if include else None
            )
        )
    except ValueError:
        c["error"] = True
    clicks.append(c)


def table(action):
    return (
        '<table><tr class="item" onclick="'
        + html.escape(action, quote=True)
        + '"><td>Role</td></tr></table>'
    )


for name, action in [
    ("relative", "window.location = 'one/';"),
    ("href", "window.location.href='two/';"),
    ("javascript", " JaVaScRiPt: WINDOW.LOCATION = 'one/?a=1&amp;b=2'; "),
    ("unicode", "window.location='/jobs/Genève';"),
    ("malformed", "window.open('/jobs/one');"),
    ("cross-origin", "window.location='https://other.example/role';"),
    ("quote-mismatch", "window.location='one/\";"),
    ("multiple-actions", "window.location='one/'; alert(1);"),
    ("overlong", "window.location='" + "x" * 2049 + "';"),
]:
    click(name, table(action))
click(
    "duplicate",
    table("window.location='one/';").replace("</table>", "") + table("window.location='one/';"),
)
click("missing-action", '<table><tr class="item"><td>Role</td></tr></table>')
click("empty", "<table></table>")
click("filter-match", table("window.location='one/';"), "/one/")
click("filter-reject", table("window.location='one/';"), "/other/")
Path(__file__).with_name("python_onclick_listings.json").write_text(
    json.dumps(clicks, ensure_ascii=False, indent=2) + "\n"
)
print(f"{len(clicks)} original Python onclick contracts frozen")
