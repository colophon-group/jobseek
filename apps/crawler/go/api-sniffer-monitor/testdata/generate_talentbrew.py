"""Freeze TalentBrew listing counters, scoped links and AJAX parameters."""

from __future__ import annotations

import json
from pathlib import Path

from src.core.monitors import talentbrew

base = "https://example.com/search-jobs"
cases = []
for name, body in [
    (
        "scoped",
        (
            '<div id="search-results" data-total-job-results="2'
            '" data-total-pages="1" data-current-page="1" data-'
            'records-per-page="10"></div><ul id="search-results'
            '-list"><li><a href="/job/one">One</a></li><li><a h'
            'ref="/job/two">Two</a></li></ul><a data-job-id="3"'
            ' href="/job/ignored">Outside</a>'
        ),
    ),
    ("fallback", '<a data-job-id="1" href="/job/one">One</a><a href="/job/ignored">Other</a>'),
    (
        "empty-scoped-no-fallback",
        '<div id="search-results-list"></div><a data-job-id="1" href="/job/one">One</a>',
    ),
    (
        "unicode",
        (
            '<div id="search-results-list"><a href="/job/café">'
            'One</a><a href="/job/café">Copy</a></div>'
        ),
    ),
    (
        "unicode-counters",
        '<div id="search-results" data-total-results="١٢" data-total-pages="２"'
        ' data-current-page="+1"'
        ' data-records-per-page="1_0"></div>',
    ),
    (
        "invalid-underscores",
        '<div id="search-results" data-total-results="_12" data-total-pages="1__2" '
        'data-current-page="2_"></div>',
    ),
    ("total-alias", '<div id="search-results" data-total-results="3"></div>'),
    (
        "zero-total-fallback",
        (
            '<div id="search-results" data-total-job-results="0'
            '" data-total-results="0" data-total-pages="0"></di'
            "v>"
        ),
    ),
    ("zero-total-primary", '<div id="search-results" data-total-job-results="0"></div>'),
    (
        "ajax",
        (
            '<div id="search-results" data-total-job-results="2'
            '345" data-ajax-url="/search-results" data-keywords'
            '="Go Engineer" data-organization-ids="123" data-di'
            'stance="0"></div>'
        ),
    ),
    (
        "bad-counters",
        '<div id="search-results" data-total-results="many" data-total-pages="many"></div>',
    ),
]:
    parsed = talentbrew._parse_page(body, base)
    cases.append(
        dict(
            name=name,
            body=body,
            urls=sorted(parsed.urls),
            total=parsed.total_jobs,
            pages=parsed.total_pages,
            current=parsed.current_page,
            per_page=parsed.records_per_page,
            ajax_url=parsed.ajax_url,
            attributes=parsed.search_attrs,
            ajax_params=talentbrew._ajax_params(parsed, base + "?fl=a,b&fl=c", 2, 1000),
        )
    )
Path(__file__).with_name("python_talentbrew.json").write_text(json.dumps(cases, indent=2) + "\n")
