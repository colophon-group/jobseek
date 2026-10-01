"""Capture ordinary Greenhouse inventory normalization from Python's processor."""

from __future__ import annotations

import copy
import json
from pathlib import Path

from src.core.monitor import _normalize_discovered
from src.core.monitors.greenhouse import _parse_job
from src.processing.board import _canonicalize_url, _classify_job_url, _prepare_discovered_sources
from src.shared.truncation import truncated_rich_result

BOARD = "https://job-boards.greenhouse.io/fixture"
URLS = [
    "https://job-boards.greenhouse.io/fixture/jobs/1",
    "https://job-boards.greenhouse.io/fixture/",
    BOARD + "#0",
    BOARD + "?id=1",
    "https://example.com",
    "https://example.com/?id=1",
    "/relative/jobs/1",
    "https:///missing/jobs/1",
    "mailto:job@example.com",
    "ftp://example.com/jobs/1",
    "https://[bad]/jobs/1",
    "https://[::1]/jobs/1",
    "https://[v1.future]/jobs/1",
    "HTTPS://EXAMPLE.COM/jobs/1",
    "https://example.com/jobs/%zz",
    "https://example.com/jobs/1;session=x",
    "https://example.com/;session=x",
    " \t\nhttps://example.com/jobs/1\r",
    "https://example.com／bad/jobs/1",
    "https://[127.0.0.1]/jobs/1",
    "https://example.com:bad/jobs/1",
    "https://career.successfactors.eu/career?company=x&_s.crb=abc&jobAlertController_jobAlertName=name&browserTimeZone=x&career_job_req_id=1#job",
    "https://career.successfactors.eu/career?_S.CRB=keep&blank=&_s.crb=drop&dup=1&dup=2",
    "https://jobs.sapsf.com/jobs/1?_s.crb=x&a=%zz&b=%FF&c=hello+world",
    "https://careers.overwolf.com/jobs/1?src=x&t=123&id=1",
    "https://careers.overwolf.com/jobs/1?SRC=x&T=١٢٣&id=1",
    "https://careers.overwolf.com/jobs/1?t=not-numeric&src=x",
    "https://careers.overwolf.com/jobs/1?t=&blank=&src=x",
    "https://careers.overwolf.com:443/jobs/1?src=keep&t=123",
    "https://careers.overwolf.com/jobs/1?ｓｒｃ=keep&src=x",
    "https://careers.overwolf.com/jobs/1?src=x&t=²",
    "https://foo.tal.net/brand-6/xf-767829ced96c/candidate/opp/1/en-GB",
    "https://tal.net/xf-abcd/xf-123/opp/1;session=x?a=1#fragment",
    "https://foo.tal.net/xf-ABCD/opp/1",
    "https://foo.tal.net/opp/1/xf-abcd",
    "https://[::ffff:127.0.0.1]/jobs/1",
    "https://prefix[::1]/jobs/1",
    "https://[::1]suffix/jobs/1",
    "https://[fe80::1%zone]/jobs/1",
    "https://jobs.sapsf.com/jobs/1?_s.crb=x&a=%F0%9F&b=%E1%80%FF&c=%E0%80",
]


def job(url, title):
    return {
        "absolute_url": url,
        "title": title,
        "content": f"<p>{title}</p>",
        "location": {"name": "Zurich"},
        "language": "en",
    }


def capture(name, raw, *, truncated=False):
    parsed = [_parse_job(copy.deepcopy(value)) for value in raw]
    assert all(value is not None for value in parsed)
    result = truncated_rich_result(parsed) if truncated else _normalize_discovered(parsed)
    sources, reasons, jobs = _prepare_discovered_sources(result, BOARD, set())
    return {
        "name": name,
        "board_url": BOARD,
        "raw_jobs": raw,
        "truncated": truncated,
        "discovered": len(result.urls),
        "drop_reasons": reasons,
        "jobs": [
            {
                "url": url,
                "title": jobs[url].title,
                "description": jobs[url].description,
                "locations": jobs[url].locations,
                "language": jobs[url].language,
            }
            for url in sorted(row[1] for row in sources)
        ],
    }


cases = [
    capture(
        "url-sanity-and-canonicalization", [job(url, f"Job {i}") for i, url in enumerate(URLS)]
    ),
    capture("last-raw-url-wins", [job(URLS[0], "First"), job(URLS[0], "Last")]),
    capture(
        "interleaved-duplicate-dictionary-order",
        [
            job("https://foo.tal.net/xf-111/opp/1", "First"),
            job("https://foo.tal.net/xf-222/opp/1", "Middle"),
            job("https://foo.tal.net/xf-111/opp/1", "Last"),
        ],
    ),
    capture(
        "canonical-alias-last-content-wins",
        [
            job("https://foo.tal.net/xf-111/opp/1", "First"),
            job("https://foo.tal.net/xf-222/opp/1", "Last"),
        ],
    ),
    capture(
        "truncated-retains-duplicates-and-all-collected",
        [job(URLS[0], "First"), job(URLS[0], "Last"), job(URLS[1], "Navigation")],
        truncated=True,
    ),
    capture("empty", []),
]

target = Path(__file__).with_name("python_inventory.json")
target.write_text(json.dumps(cases, ensure_ascii=False, indent=2) + "\n")
Path(__file__).with_name("python_urls.json").write_text(
    json.dumps(
        [
            {
                "url": url,
                "canonical": _canonicalize_url(url),
                "reason": _classify_job_url(_canonicalize_url(url), BOARD) or "",
            }
            for url in URLS
        ],
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
