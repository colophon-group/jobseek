"""Freeze actual Inline cursor/section helpers and synthetic identities offline."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors.inline import (
    _generate_identity_url,
    _generate_url,
    _scope_to_section,
    _walk_bounded_item,
)
from src.shared.extract import flatten, walk_steps

HERE = Path(__file__).parent
steps = [{"tag": "h2", "field": "title"}]
cases = [
    ("two-titles", "<h2>Engineer</h2><h2>Designer</h2>", steps, None, None, None, False),
    ("missing-title", "<p>No roles</p>", steps, None, None, None, False),
    ("hidden", "<h2 hidden>Hidden</h2><h2>Engineer</h2>", steps, None, None, None, False),
    ("include-hidden", "<h2 hidden>Hidden</h2><h2>Engineer</h2>", steps, None, None, None, True),
    (
        "bounded",
        "<p>Item</p><h2>Engineer</h2><p>Item</p><h2>Designer</h2>",
        steps,
        {"tag": "p", "text": "Item"},
        None,
        None,
        False,
    ),
    (
        "bounded-missing-title",
        "<p>Item</p><p>No title</p><p>Item</p><h2>Engineer</h2>",
        steps,
        {"tag": "p", "text": "Item"},
        None,
        None,
        False,
    ),
    (
        "section",
        "<h1>Jobs</h1><h2>Engineer</h2><h1>End</h1><h2>Outside</h2>",
        steps,
        None,
        {"tag": "h1", "text": "Jobs"},
        {"tag": "h1", "text": "End"},
        False,
    ),
    ("missing-section", "<h2>Engineer</h2>", steps, None, {"text": "Jobs"}, {"text": "End"}, False),
    (
        "duplicate-section",
        "<h1>Jobs</h1><h1>Jobs</h1><h2>Engineer</h2><h1>End</h1>",
        steps,
        None,
        {"text": "Jobs"},
        {"text": "End"},
        False,
    ),
    (
        "missing-end",
        "<h1>Jobs</h1><h2>Engineer</h2>",
        steps,
        None,
        {"text": "Jobs"},
        {"text": "End"},
        False,
    ),
    (
        "regex-attribute",
        '<p class="item">Straße</p><h2>Engineer</h2>',
        steps,
        {"attr": "class=item", "text": "STRASSE", "match_regex": "Stra.e"},
        None,
        None,
        False,
    ),
    ("cap", "<h2>Engineer</h2>" * 501, steps, None, None, None, False),
]
output = []
for name, source, rules, boundary, start, end, hidden in cases:
    rows, failure, truncated = [], False, False
    try:
        elements = _scope_to_section(flatten(source, include_hidden=hidden), start, end)
        cursor = processed = 0
        while cursor < len(elements) and processed < 500:
            fields, new_cursor = (
                _walk_bounded_item(elements, rules, cursor, boundary)
                if boundary
                else walk_steps(elements, rules, start=cursor)
            )
            if new_cursor <= cursor:
                break
            if not fields.get("title"):
                if boundary is None:
                    break
                cursor = new_cursor
                continue
            cursor = new_cursor
            processed += 1
            rows.append(fields)
        truncated = processed >= 500 and cursor < len(elements)
    except Exception:
        failure = True
    output.append(
        {
            "name": name,
            "html": source,
            "steps": rules,
            "item": boundary,
            "start": start,
            "end": end,
            "hidden": hidden,
            "rows": rows,
            "error": failure,
            "truncated": truncated,
        }
    )

identities = []
for titles, stable, board in [
    (
        ["Engineer", "Engineer", "ENGINEER"],
        None,
        "https://example.com/jobs?z=one&lang=en&z=two&empty=#roles",
    ),
    (["École & C++", "İstanbul", "工程师"], None, "https://example.com/jobs"),
    (["Engineer", "Designer"], ["Straße", "STRASSE"], "https://example.com/jobs"),
    (
        ["Engineer", "Designer"],
        ["Provider 42", "Provider 43"],
        "https://example.com/jobs?_jid=old&locale=en",
    ),
    (["A" * 80], None, "https://example.com/jobs?name=a~b"),
]:
    seen, urls, failure = {}, [], False
    try:
        for i, title in enumerate(titles):
            urls.append(
                _generate_url(board, title, seen, stable_identity=stable[i] if stable else None)
            )
    except Exception:
        failure = True
    identities.append(
        {"board_url": board, "titles": titles, "stable": stable, "urls": urls, "error": failure}
    )
direct = [
    {"identity": v, "url": _generate_identity_url("https://example.com/jobs?lang=en#roles", v)}
    for v in ["abc", "item 42", "~&/?", "Straße"]
]
(HERE / "python_inline_document.json").write_text(
    json.dumps(
        {"cases": output, "identities": identities, "direct": direct}, indent=2, ensure_ascii=False
    )
    + "\n"
)
