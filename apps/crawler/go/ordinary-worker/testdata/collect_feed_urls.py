"""Freeze the existing processor's feed filters, URL rewrites and assignments."""

from __future__ import annotations

import copy
import json
from dataclasses import asdict
from pathlib import Path

from src.core.monitor import MonitorResult, _apply_url_filter, _apply_url_transform
from src.core.monitors import DiscoveredJob
from src.processing.scrape import _effective_board_enrich

CASES = [
    (
        "sitemap-iframe-suffix",
        {"url_transform": {"find": "$", "replace": "?in_iframe=1"}},
        ["https://example.com/jobs/123/job"],
    ),
    (
        "sitemap-job-suffix",
        {"url_transform": {"find": "/job$", "replace": "/job?in_iframe=1"}},
        ["https://example.com/jobs/123/job", "https://example.com/job/123"],
    ),
    (
        "sitemap-language-query",
        {"url_transform": {"find": r"\?lang=.*$", "replace": ""}},
        ["https://example.com/jobs/123?lang=en", "https://example.com/jobs/123?lang=de"],
    ),
    (
        "sitemap-https-upgrade",
        {"url_transform": {"find": "^http://", "replace": "https://"}},
        [
            "http://nttdata.jobs/vacancies/123/engineer",
            "https://nttdata.jobs/vacancies/456/engineer",
        ],
    ),
    (
        "sitemap-cms-origin",
        {
            "url_transform": {
                "find": r"https://website-cms\.internal\.aldi\.cn",
                "replace": "https://www.aldi.com.cn",
            }
        },
        ["https://website-cms.internal.aldi.cn/joinus/123"],
    ),
    (
        "filter-before-transform",
        {
            "url_filter": {"include": "/jobs/", "exclude": "intern"},
            "url_transform": {"find": "$", "replace": "?in_iframe=1"},
        },
        [
            "https://example.com/jobs/123",
            "https://example.com/jobs/intern",
            "https://example.com/contact",
        ],
    ),
    (
        "python-unicode-filter",
        {"url_filter": r"/jobs/\w+$"},
        [
            "https://example.com/jobs/工程師",
            "https://example.com/jobs/é",
            "https://example.com/jobs/-",
        ],
    ),
    (
        "python-negative-lookahead",
        {"url_filter": r"/jobs/(?!domains|p\d)[a-z]"},
        [
            "https://example.com/jobs/engineer",
            "https://example.com/jobs/domains",
            "https://example.com/jobs/p1",
        ],
    ),
    (
        "rss-successfactors-division",
        {"url_filter": "/VIVO/job/"},
        ["https://example.com/VIVO/job/123", "https://example.com/Other/job/456"],
    ),
    (
        "rss-exclude-country",
        {"url_filter": {"exclude": "/Cesko/"}},
        ["https://example.com/Cesko/job/123", "https://example.com/Germany/job/456"],
    ),
    (
        "python-capture-replacement",
        {
            "url_transform": {
                "find": r"^https://example\.com/jobs/(\d+)-[^/]+$",
                "replace": r"https://example.com/jobs/\1",
            }
        },
        ["https://example.com/jobs/123-engineer"],
    ),
    (
        "literal-dollar-replacement",
        {"url_transform": {"find": "$", "replace": "?value=$1"}},
        ["https://example.com/jobs/123"],
    ),
    (
        "empty-include-null-exclude",
        {"url_filter": {"include": "", "exclude": None}},
        ["https://example.com/jobs/123"],
    ),
    (
        "legacy-url-is-bookkeeping",
        {"url": "https://unused.example.com/googlefeed.xml"},
        ["https://example.com/jobs/123"],
    ),
]

cases = []
for name, metadata, urls in CASES:
    jobs = [
        DiscoveredJob(url=u, title=f"Job {i}", description="<p>Feed content.</p>")
        for i, u in enumerate(urls)
    ]
    result = MonitorResult(urls=set(urls), jobs_by_url={j.url: copy.deepcopy(j) for j in jobs})
    result = _apply_url_transform(_apply_url_filter(result, metadata), metadata)
    cases.append(
        {
            "name": name,
            "metadata": metadata,
            "jobs": [asdict(j) for j in jobs],
            "expected": [asdict(j) for _, j in sorted(result.jobs_by_url.items())],
        }
    )

assignments = []
for provider in ("personio", "rss"):
    for scraper in ("skip", "json-ld", "dom", "embedded"):
        for options in (None, {}, {"steps": []}, {"enrich": []}):
            metadata = {"scraper_type": scraper}
            if options is not None:
                metadata["scraper_config"] = options
            assignments.append(
                {
                    "provider": provider,
                    "metadata": metadata,
                    "enrich": _effective_board_enrich(metadata, provider),
                }
            )

Path(__file__).with_name("python_feed_urls.json").write_text(
    json.dumps({"cases": cases, "assignments": assignments}, ensure_ascii=False, indent=2) + "\n"
)
