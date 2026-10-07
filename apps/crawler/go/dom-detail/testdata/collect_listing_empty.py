from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors.dom import _validate_explicit_empty_state, _without_board_self_urls

cases = [
    (
        "nested-whitespace",
        '<div class="empty"> NO <span> vacancies </span> TODAY </div>',
        ".empty",
        "no vacancies",
        [],
    ),
    (
        "first-marker-only",
        '<div class="empty">Other</div><div class="empty">No vacancies</div>',
        ".empty",
        "No vacancies",
        [],
    ),
    ("casefold", '<div class="empty">Straße</div>', ".empty", "STRASSE", []),
    ("selector-only", '<p class="empty">Nothing here</p>', ".empty", None, []),
    ("missing-marker", "<p>Nothing here</p>", ".empty", None, []),
    ("wrong-text", '<p class="empty">Open jobs</p>', ".empty", "No vacancies", []),
    (
        "nonempty-fast-path",
        "<p>Open jobs</p>",
        ".empty",
        "No vacancies",
        ["https://example.com/jobs/42"],
    ),
    (
        "self-link-is-not-job",
        "<p>Missing</p>",
        ".empty",
        "No vacancies",
        ["https://example.com/careers#jobs"],
    ),
    (
        "self-link-with-marker",
        '<p class="empty">No vacancies</p>',
        ".empty",
        "No vacancies",
        ["https://example.com/careers/"],
    ),
    (
        "hidden-text-is-legacy-evidence",
        '<p class="empty" hidden>No vacancies</p>',
        ".empty",
        "No vacancies",
        [],
    ),
    ("mixed-text", '<p class="empty">No<b>vacancies</b></p>', ".empty", "No vacancies", []),
    ("unicode-spaces", '<p class="empty">No\u00a0vacancies</p>', ".empty", "No vacancies", []),
]
out = []
for name, source, selector, text, urls in cases:
    try:
        _validate_explicit_empty_state(
            source, selector, text, set(urls), "https://example.com/careers"
        )
        accepted = True
    except ValueError:
        accepted = False
    out.append(
        dict(
            name=name,
            source=source,
            selector=selector,
            text=text,
            job_count=len(_without_board_self_urls(set(urls), "https://example.com/careers")),
            accepted=accepted,
        )
    )
Path(__file__).with_name("python_listing_empty.json").write_text(
    json.dumps(out, ensure_ascii=False, indent=2) + "\n"
)
