"""Freeze actual shared Python rich filtering and provider-boundary semantics."""

from __future__ import annotations

import asyncio
import copy
import dataclasses
import json
from pathlib import Path

import httpx

from src.core.monitor import (
    MonitorResult,
    _apply_job_filter,
    _apply_url_allowlist,
    _apply_url_filter,
    _apply_url_transform,
    postprocess_monitor_stream,
)
from src.core.monitors import DiscoveredJob, rss

BASE = "https://example.com/jobs/"
CASES = []


def job(suffix="123", title="Engineer", **fields):
    return {"URL": BASE + suffix, "Title": title, "Description": "<p>Build.</p>", **fields}


def case(name, config, jobs, *, native_refused=False):
    by_url = {}
    for data in jobs:
        if not data.get("URLOnly"):
            by_url[data["URL"]] = DiscoveredJob(
                url=data["URL"],
                title=data.get("Title"),
                description=data.get("Description"),
                locations=data.get("Locations"),
                metadata=data.get("Metadata"),
            )
    result = MonitorResult(urls={data["URL"] for data in jobs}, jobs_by_url=by_url)
    try:
        result = _apply_url_filter(result, config)
        result = _apply_job_filter(result, config)
        result = _apply_url_allowlist(result, config)
        result = _apply_url_transform(result, config)
        expected = []
        for url in sorted(result.urls):
            data = result.jobs_by_url.get(url)
            if data is None:
                expected.append({"URL": url, "URLOnly": True})
            else:
                raw = dataclasses.asdict(data)
                expected.append(
                    {
                        "URL": raw["url"],
                        "Title": raw["title"],
                        "Description": raw["description"],
                        "Locations": raw["locations"],
                        "Metadata": raw["metadata"],
                    }
                )
        outcome = {"expected": expected, "security_rejected": result.security_filtered_count}
        error = False
    except ValueError:
        outcome = {"expected": [], "security_rejected": 0}
        error = True
    CASES.append(
        {
            "name": name,
            "config": config,
            "jobs": copy.deepcopy(jobs),
            "error": error,
            "native_refused": native_refused,
            **outcome,
        }
    )


case("provider-accepted", {"url_allowlist": r"https://example\.com/jobs/\d+"}, [job()])
case(
    "provider-rejected", {"url_allowlist": r"https://example\.com/jobs/\d+"}, [job(), job("other")]
)
case("provider-all-rejected", {"url_allowlist": r"https://elsewhere\.com/.*"}, [job()])
case(
    "provider-fullmatch-alternative",
    {"url_allowlist": r"https://example\.com/jobs/(a|ab)"},
    [job("ab")],
)
case(
    "provider-fullmatch-unicode",
    {"url_allowlist": r"https://example\.com/jobs/\w+"},
    [job("工程師")],
)
case(
    "allowlist-before-transform",
    {
        "url_allowlist": r"https://example\.com/jobs/\d+",
        "url_transform": {"find": "example.com", "replace": "careers.example.com"},
    },
    [job()],
)
case(
    "url-filter-before-boundary",
    {"url_filter": "123", "url_allowlist": r"https://example\.com/jobs/123"},
    [job(), job("outside")],
)
case(
    "job-filter-before-boundary",
    {
        "job_filter": {"field": "title", "exclude": "Intern"},
        "url_allowlist": r"https://example\.com/jobs/123",
    },
    [job(), job("outside", "Intern")],
)
case(
    "title-filter",
    {"job_filter": {"field": "title", "include": "Engineer"}},
    [job(), job("2", "Designer")],
)
case("description-filter", {"job_filter": {"field": "description", "include": "Build"}}, [job()])
case(
    "location-filter",
    {"job_filter": {"field": "locations", "include": "Zurich\nTokyo"}},
    [job(Locations=["Zurich", "Tokyo"])],
)
case("default-text-filter", {"job_filter": "Platform"}, [job(Metadata={"team": "Platform"})])
case(
    "metadata-spaces-unicode",
    {"job_filter": {"field": "metadata", "include": r'"a": "東京", "z": "&<>"'}},
    [job(Metadata={"z": "&<>", "a": "東京"})],
)
case(
    "metadata-float-kind",
    {"job_filter": {"field": "metadata", "include": r'"a": 1\.0, "b": 1e\+20, "c": 1e-07, "d": 2'}},
    [job(Metadata={"a": 1.0, "b": 1e20, "c": 1e-7, "d": 2})],
)
case(
    "metadata-line-separator",
    {"job_filter": {"field": "metadata", "include": "東京\u2028世界"}},
    [job(Metadata={"a": "東京\u2028世界"})],
)
case(
    "metadata-key-string",
    {"job_filter": {"field": "metadata.team", "include": "Platform"}},
    [job(Metadata={"team": "Platform"})],
)
case(
    "metadata-key-array",
    {"job_filter": {"field": "metadata.places", "include": r'\["Zurich", "東京"\]'}},
    [job(Metadata={"places": ["Zurich", "東京"]})],
)
case(
    "metadata-dotted-literal-key",
    {"job_filter": {"field": "metadata.x.y", "include": "keep"}},
    [job(Metadata={"x.y": "keep"})],
)
case(
    "metadata-missing-key", {"job_filter": {"field": "metadata.absent", "include": "keep"}}, [job()]
)
case(
    "raw-duplicate-last-content",
    {"job_filter": {"field": "title", "include": "Engineer"}},
    [job(), job(title="Designer")],
)
case(
    "required-classification",
    {
        "job_filter": {
            "field": "title",
            "include": "Engineer",
            "exclude": "Intern",
            "require_classification": True,
        }
    },
    [job(), job("2", "Intern")],
)
case(
    "both-classifications-refused",
    {"job_filter": {"include": "Build", "exclude": "Build", "require_classification": True}},
    [job()],
)
case(
    "neither-classification-refused",
    {"job_filter": {"include": "Keep", "exclude": "Reject", "require_classification": True}},
    [job()],
)
case(
    "hybrid-url-only-preserved",
    {
        "job_filter": {
            "field": "title",
            "include": "Engineer",
            "exclude": "Intern",
            "require_classification": True,
        }
    },
    [job(), {"URL": BASE + "unclassified", "URLOnly": True}],
)
case("empty-allowlist-not-owned", {"url_allowlist": ""}, [job()], native_refused=True)
case("invalid-allowlist-not-owned", {"url_allowlist": "["}, [job()], native_refused=True)


async def stream_cases():
    cases = []
    for mode in ["accepted", "late-classification", "boundary-tail", "boundary-first-batch"]:
        jobs = [job(str(n)) for n in range(201)]
        config = {
            "preset": "generic",
            "feed_url": "https://example.com/feed",
            "scraper_type": "skip",
            "url_allowlist": r"https://example\.com/jobs/\d+",
            "job_filter": {
                "field": "title",
                "include": "Engineer",
                "exclude": "Intern",
                "require_classification": True,
            },
        }
        if mode == "late-classification":
            jobs[-1]["Title"] = "Other"
        if mode in {"boundary-tail", "boundary-first-batch"}:
            jobs[-1 if mode == "boundary-tail" else 7]["URL"] = "https://foreign.example/jobs/200"
        body = (
            "<rss><channel>"
            + "".join(
                f"<item><link>{data['URL']}</link><title>{data['Title']}</title>"
                "<description><![CDATA[<p>Build.</p>]]></description></item>"
                for data in jobs
            )
            + "</channel></rss>"
        )

        def handler(request, body=body):
            return httpx.Response(200, text=body)

        batches, rejected, error = [], 0, False
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            try:
                async for result in postprocess_monitor_stream(
                    rss.discover_stream(
                        {"board_url": "https://example.com/careers", "metadata": config}, client
                    ),
                    config,
                ):
                    batches.append(len(result.urls))
                    rejected += result.security_filtered_count
            except ValueError:
                error = True
        cases.append(
            {
                "name": mode,
                "config": config,
                "jobs": jobs,
                "batch_sizes": batches,
                "security_rejected": rejected,
                "error": error,
            }
        )
    return cases


STREAM_CASES = asyncio.run(stream_cases())
Path(__file__).with_name("python_feed_policy.json").write_text(
    json.dumps({"cases": CASES, "stream_cases": STREAM_CASES}, ensure_ascii=False, indent=2) + "\n"
)
print(f"Frozen {len(CASES)} shared-policy and {len(STREAM_CASES)} actual Python RSS stream cases")
