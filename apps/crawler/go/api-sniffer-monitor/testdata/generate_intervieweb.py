"""Freeze Intervieweb URL identity, counters and pagination protocol."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors import intervieweb

base = "https://fixture.intervieweb.it/en/career"
urls = []
for value in (
    "/jobs/engineer/EN/?tracking=1#top",
    "//fixture.intervieweb.it/jobs/one/fr",
    "/jobs/café/en/",
    "/jobs/data%2Fgo/en/",
    "/jobs/bad%ZZ/en/",
    "/jobs/role/en/;ignored",
    "https://fixture.intervieweb.it:443/jobs/one/en",
    "https://other.intervieweb.it/jobs/one/en",
    "http://fixture.intervieweb.it/jobs/one/en",
    "https://user@fixture.intervieweb.it/jobs/one/en",
    "/jobs/one/english",
    "/jobs/one/en/extra",
):
    urls.append(dict(raw=value, canonical=intervieweb._canonical_job_url(value, base)))

pages = []
for name, body in (
    ("single", '<a href="/jobs/one/en">One</a>'),
    ("empty", "No jobs"),
    ("counter", "Page 1 of 3 and Page 2 of 2"),
    ("unicode-counter", "Page ١ of ٢"),
    ("zero", "Page 1 of 0"),
    ("over-cap", "Page 1 of 1001"),
):
    try:
        value = intervieweb._page_count(body)
        error = False
    except ValueError:
        value, error = None, True
    pages.append(
        dict(
            name=name,
            body=body,
            pages=value,
            error=error,
            urls=sorted(intervieweb._parse_job_urls(body, base)),
        )
    )

protocol = []
for name, endpoint, order, section in (
    ("valid", "/app.php?module=newcareer&token=synthetic", "name", "jobs"),
    ("unicode-section", "/app.php?module=newcareer", "date", "café"),
    ("cross-origin", "https://other.example/app.php?module=newcareer", "name", "jobs"),
    ("duplicate-module", "/app.php?module=newcareer&module=newcareer", "name", "jobs"),
    ("bad-module", "/app.php?module=admin", "name", "jobs"),
    ("bad-order", "/app.php?module=newcareer", "salary", "jobs"),
    ("missing-section", "/app.php?module=newcareer", "name", None),
):
    body = (
        f'<input id="url-for-announces" value="{endpoint}">'
        f'<a data-order="{order}" class="active">Order</a>'
    )
    if section is not None:
        body += json.dumps({"section": section}, ensure_ascii=False)
    try:
        value = intervieweb._pagination_protocol(body, base)
        error = False
    except ValueError:
        value, error = None, True
    protocol.append(dict(name=name, body=body, value=value, error=error))

Path(__file__).with_name("python_intervieweb.json").write_text(
    json.dumps(dict(urls=urls, pages=pages, protocol=protocol), indent=2) + "\n"
)
