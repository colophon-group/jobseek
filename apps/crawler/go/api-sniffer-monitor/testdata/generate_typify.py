"""Freeze Typify live partitions, public fields and authoritative counts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitors import typify

pages = []
for name, board, body in (
    (
        "nl",
        "https://example.com/jobs",
        "window.typify={language:'nl'};<input class='cb-function' data-id='1'>"
        "<input class='cb-function' data-id='2'>",
    ),
    (
        "de-default",
        "https://www.dominosjobs.de/jobs",
        "window.typify={};<input class='cb-function' data-id='1'>",
    ),
    (
        "fr",
        "https://example.com/jobs",
        "window.typify={language:'fr'};<input class='cb-function' data-id='1'>",
    ),
    (
        "duplicate",
        "https://example.com/jobs",
        "window.typify={};<input class='cb-function' data-id='1'>"
        "<input class='cb-function' data-id='1'>",
    ),
    (
        "unicode-id",
        "https://example.com/jobs",
        "window.typify={};<input class='cb-function' data-id='٢'>",
    ),
    ("no-partitions", "https://example.com/jobs", "window.typify={}"),
    ("no-marker", "https://example.com/jobs", "<input class='cb-function' data-id='1'>"),
):
    parsed = typify._page_config(body, board)
    pages.append(dict(name=name, board=board, body=body, value=asdict(parsed) if parsed else None))

counts = []
for name, payload in (
    ("empty", dict(pagination=dict(total=0, total_pages=0), results=[])),
    ("one", dict(pagination=dict(total=1, total_pages=1), results=[dict(title="Engineer")])),
    ("numeric-string", dict(pagination=dict(total="1", total_pages="1"), results=[{}])),
    ("unicode-count", dict(pagination=dict(total="١", total_pages="١"), results=[{}])),
    ("over-cap", dict(pagination=dict(total=2, total_pages=2), results=[{}, {}])),
    ("gap", dict(pagination=dict(total=2, total_pages=1), results=[{}])),
    ("bool-total", dict(pagination=dict(total=True, total_pages=1), results=[{}])),
    ("float-total", dict(pagination=dict(total=1.0, total_pages=1), results=[{}])),
    ("negative", dict(pagination=dict(total=-1, total_pages=0), results=[])),
):
    try:
        total, rows = typify._partition_rows(payload)
        error = None
    except ValueError as exc:
        total, rows, error = None, None, type(exc).__name__
    counts.append(dict(name=name, payload=payload, total=total, rows=rows, error=error))

jobs = []
for row in (
    dict(title="  Engineer  ", url="/vacancy/one", location=dict(label=" Rotterdam ")),
    dict(title="Developer", url="/vacancy/café"),
    dict(title="Developer", url="/vacancy/data%2Fgo"),
    dict(title="Developer", url="/vacancy/bad%ZZ"),
    dict(title="Foreign", url="https://foreign.example/one"),
    dict(title="", url="/one"),
    dict(title="Engineer", url=None),
    dict(title="Engineer", url="/one", location=dict(label=3)),
):
    parsed = typify._parse_job(row, "https://example.com/jobs")
    value = (
        None
        if parsed is None
        else {key: getattr(parsed, key) for key in ("url", "title", "locations")}
    )
    jobs.append(dict(row=row, value=value))

Path(__file__).with_name("python_typify.json").write_text(
    json.dumps(dict(pages=pages, counts=counts, jobs=jobs), indent=2) + "\n"
)
