"""Freeze shared item filtering through the deployed Python parser."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors import api_sniffer

cases = []


def add(name, config, items, total=None):
    try:
        options = api_sniffer._validated_item_filter({"item_filter": config})
        rows, scoped_total = api_sniffer._apply_item_filter(items, options, total)
        error = False
    except Exception:
        rows, scoped_total, error = None, None, True
    cases.append(
        dict(
            name=name,
            config=config,
            items=items,
            total=total,
            rows=rows,
            scoped_total=scoped_total,
            error=error,
        )
    )


rows = [
    {"id": "1", "scope": "main"},
    {"id": "2", "scope": ["other", "main"]},
    {"id": "3", "scope": "other"},
    {"id": "4", "scope": 1},
]
add("include-scalars-and-lists", {"include": {"scope": ["main"]}}, rows, 10)
add("exclude-scalars-and-lists", {"exclude": {"scope": ["main"]}}, rows, 10)
add(
    "nested-path",
    {"include": {"owner.name": ["Acme"]}},
    [{"owner": {"name": "Acme"}}, {"owner": {"name": "Other"}}],
    2,
)
add("all-paths-required", {"include": {"scope": ["main"], "id": ["2"]}}, rows, 4)
add("excluded-after-include", {"include": {"scope": ["main"]}, "exclude": {"id": ["2"]}}, rows, 4)
add("regex-search", {"exclude_regex": {"scope": ["(?i)MAIN"]}}, rows, 4)
add("required-full-match", {"require_regex": {"id": "1|12"}}, [{"id": "12"}], 1)
add("required-no-partial", {"require_regex": {"id": "[0-9]+"}}, [{"id": "x12"}], 1)
add("required-invalid-bool", {"require_regex": {"id": "[0-9]+"}}, [{"id": True}], 1)
add("required-invalid-float", {"require_regex": {"id": "[0-9]+"}}, [{"id": 12.0}], 1)
add(
    "required-large-integer",
    {"require_regex": {"id": "[0-9]+"}},
    [{"id": 123456789012345678901234567890}],
    1,
)
add("required-missing", {"require_regex": {"id": "[0-9]+"}}, [{}], 1)
add(
    "require-after-scope",
    {"include": {"scope": ["main"]}, "require_regex": {"id": "[0-9]+"}},
    [{"id": "1", "scope": "main"}, {"id": "bad", "scope": "other"}],
    2,
)
add(
    "dedupe-first-order",
    {"dedupe_by": ["id"]},
    [{"id": "1", "title": "First"}, {"id": "2"}, {"id": "1", "title": "Second"}],
    3,
)
add("dedupe-integer-string", {"dedupe_by": ["id"]}, [{"id": 1}, {"id": "1"}], 2)
add(
    "dedupe-invalid-preserved",
    {"dedupe_by": ["id"]},
    [{"id": True}, {"id": 1.0}, {"id": ""}, {}, {"id": None}],
    5,
)
add(
    "dedupe-composite",
    {"dedupe_by": ["id", "scope"]},
    [{"id": "1", "scope": "a"}, {"id": "1", "scope": "b"}, {"id": "1", "scope": "a"}],
    3,
)
pref = {
    "dedupe_by": ["id"],
    "dedupe_preference": {
        "path": "locale",
        "preferred_values": ["en", "de"],
        "fallback_by": ["locale", "url"],
    },
}
add(
    "preferred-order",
    pref,
    [
        {"id": "1", "locale": "de", "url": "b"},
        {"id": "2", "locale": "fr", "url": "c"},
        {"id": "1", "locale": "en", "url": "a"},
    ],
    3,
)
add(
    "preference-lexical-tie",
    pref,
    [{"id": "1", "locale": "en", "url": "b"}, {"id": "1", "locale": "en", "url": "a"}],
    2,
)
add("preference-invalid-fallback", pref, [{"id": "1", "locale": "en"}], 1)
add("preference-invalid-id-preserved", pref, [{"locale": "en"}], 1)
add("gap-remains-after-filter", {"include": {"scope": ["main"]}}, rows, 100)
add("total-clamps-zero", {"include": {"scope": ["main"]}}, rows, 1)
for name, config in [
    ("invalid-empty", {}),
    ("invalid-key", {"unknown": True}),
    ("invalid-empty-include", {"include": {}}),
    ("invalid-values", {"include": {"scope": []}}),
    ("invalid-regex", {"exclude_regex": {"scope": ["("]}}),
    ("invalid-preference-alone", {"dedupe_preference": pref["dedupe_preference"]}),
]:
    add(name, config, rows, 4)
Path(__file__).with_name("python_item_filters.json").write_text(json.dumps(cases, indent=2) + "\n")
