"""Freeze original Python URL collision selection across monitor types."""

from __future__ import annotations

import asyncio
import copy
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from src.core.monitor import (
    _apply_url_allowlist,
    _apply_url_filter,
    _apply_url_transform,
    _normalize_discovered,
    _url_transform_collision_config,
    postprocess_monitor_stream,
)
from src.core.monitors import DiscoveredJob

CASES = []
ROOT = "https://example.com/"
BASE = {
    "find": r"^https://example\.com/(?:en|de)/jobs/(\d+)(?:-[^/?#]+)?$",
    "replace": r"https://example.com/jobs/\1",
    "collision_policy": "prefer_source_pattern",
    "collision_preferred_source_patterns": [r"/en/", r"/de/"],
    "collision_canonical_identity_regex": r"^https://example\.com/jobs/(\d+)$",
    "collision_identity_metadata_key": "id",
    "collision_stream_buffer_limit": 500,
}


def job(url="en/jobs/123-a", title="English", **kw):
    return {
        "URL": ROOT + url,
        "Title": title,
        "Description": "<p>Work.</p>",
        "Metadata": {"id": "123"},
        **kw,
    }


def case(name, transform=None, jobs=None, *, provider="rss", extra=None, native_refused=False):
    config = {
        "url_transform": copy.deepcopy(BASE if transform is None else transform),
        **(extra or {}),
    }
    jobs = copy.deepcopy([job()] if jobs is None else jobs)
    collision = None
    try:
        collision = _url_transform_collision_config(config)
        decoded = []
        url_only = all(j.get("URLOnly") for j in jobs)
        for j in jobs:
            if url_only:
                continue
            decoded.append(
                DiscoveredJob(
                    url=j["URL"],
                    title=j.get("Title"),
                    description=j.get("Description"),
                    locations=j.get("Locations"),
                    metadata=j.get("Metadata"),
                    source_identity=j.get("SourceIdentity"),
                )
            )
        raw = {j["URL"] for j in jobs} if url_only else decoded
        if provider == "rss":

            async def batches():
                for i in range(0, len(jobs), 200):
                    yield (
                        {j["URL"] for j in jobs[i : i + 200]} if url_only else decoded[i : i + 200]
                    )

            async def collect():
                return [r async for r in postprocess_monitor_stream(batches(), config)]

            results = asyncio.run(collect())
        else:
            result = _normalize_discovered(
                raw, reject_conflicting_duplicate_urls=collision is not None
            )
            result = _apply_url_filter(result, config)
            result = _apply_url_allowlist(result, config)
            results = [_apply_url_transform(result, config)]
        expected = []
        for result in results:
            for url in sorted(result.urls):
                j = result.jobs_by_url.get(url) if result.jobs_by_url is not None else None
                if j is None:
                    expected.append({"URL": url, "URLOnly": True})
                else:
                    expected.append(
                        {
                            "URL": j.url,
                            "Title": j.title,
                            "Description": j.description,
                            "Locations": j.locations,
                            "Metadata": j.metadata,
                            "SourceIdentity": j.source_identity or "",
                        }
                    )
        error = False
    except (ValueError, TypeError):
        expected, error = [], True
    CASES.append(
        {
            "name": name,
            "provider": provider,
            "config": config,
            "jobs": jobs,
            "expected": expected,
            "error": error,
            "native_refused": native_refused,
        }
    )


for provider in ["rss", "api_sniffer", "dom"]:
    case(
        provider + "-source-preference",
        jobs=[job("de/jobs/123-b", "German"), job()],
        provider=provider,
    )
    case(
        provider + "-reverse-order", jobs=[job(), job("de/jobs/123-b", "German")], provider=provider
    )
case("lexical-tie", jobs=[job("en/jobs/123-b", "B"), job("en/jobs/123-a", "A")])
case(
    "metadata-preference",
    transform={
        **BASE,
        "collision_preferred_metadata_key": "locale",
        "collision_preferred_metadata_patterns": [r"^de$", r"^en$"],
    },
    jobs=[
        job(Metadata={"id": "123", "locale": "en"}),
        job("de/jobs/123-b", "Deutsch", Metadata={"id": "123", "locale": "de"}),
    ],
)
case("identity-missing", jobs=[job(Metadata={})])
case("identity-mismatch", jobs=[job(Metadata={"id": "456"})])
case("identity-integer", jobs=[job(Metadata={"id": 123})])
case("identity-float", jobs=[job(Metadata={"id": 123.0})])
case("identity-bool", jobs=[job(Metadata={"id": True})])
case(
    "identity-source-pattern",
    transform={
        k: v
        for k, v in {**BASE, "collision_source_identity_regex": BASE["find"]}.items()
        if k != "collision_identity_metadata_key"
    },
    jobs=[job(Metadata=None)],
)
case(
    "same-explicit-identity",
    jobs=[
        job(SourceIdentity="provider:tenant:123"),
        job("de/jobs/123-b", "Deutsch", SourceIdentity="provider:tenant:123"),
    ],
)
case(
    "conflicting-explicit-identity",
    jobs=[
        job(SourceIdentity="provider:tenant:123"),
        job("de/jobs/123-b", "Deutsch", SourceIdentity="provider:tenant:456"),
    ],
)
case(
    "inherit-explicit-identity",
    jobs=[job(), job("de/jobs/123-b", "Deutsch", SourceIdentity="provider:tenant:123")],
)
case("duplicate-exact", jobs=[job(), job()])
case("duplicate-conflicting", jobs=[job(), job(title="Changed")])
case(
    "url-only",
    jobs=[
        {"URL": ROOT + "de/jobs/123-b", "URLOnly": True},
        {"URL": ROOT + "en/jobs/123-a", "URLOnly": True},
    ],
)
case(
    "identity-marker",
    transform={
        **BASE,
        "find": r"^https://example\.com/.*$",
        "replace": "https://example.com/jobs/{identity}",
    },
    jobs=[job()],
)
case(
    "identity-marker-non-string",
    transform={
        **BASE,
        "find": r"^https://example\.com/.*$",
        "replace": "https://example.com/jobs/{identity}",
    },
    jobs=[job(Metadata={"id": 123})],
)
case(
    "filter-before-identity",
    jobs=[job(Metadata={}), job("de/jobs/123-b", "Deutsch")],
    extra={"url_filter": r"/de/"},
)
case(
    "rss-cross-batch-preference",
    jobs=[job("de/jobs/123-b", "Deutsch")]
    + [job(f"en/jobs/{i}-a", str(i), Metadata={"id": str(i)}) for i in range(1000, 1200)]
    + [job()],
)
case(
    "rss-buffer-limit",
    transform={**BASE, "collision_stream_buffer_limit": 1},
    jobs=[job(), job("de/jobs/123-b", "Deutsch")],
)
case(
    "non-stream-buffer-not-used",
    transform={**BASE, "collision_stream_buffer_limit": 1},
    jobs=[job(), job("de/jobs/123-b", "Deutsch")],
    provider="api_sniffer",
)
for name, changes in [
    ("bad-policy", {"collision_policy": "last"}),
    ("missing-source-preference", {"collision_preferred_source_patterns": []}),
    ("bad-canonical-groups", {"collision_canonical_identity_regex": ".*"}),
    ("two-identities", {"collision_source_identity_regex": BASE["find"]}),
    ("bad-limit", {"collision_stream_buffer_limit": True}),
    ("unpaired-preference", {"collision_preferred_metadata_key": "locale"}),
    ("bad-regex", {"find": "["}),
]:
    case(name, transform={**BASE, **changes}, native_refused=True)

out = Path(__file__).with_name("python_feed_collision.json")
out.write_text(
    json.dumps(
        {"reference": "original src.core.monitor collision dispatcher", "cases": CASES},
        ensure_ascii=False,
        indent=2,
    )
    + "\n"
)
print(json.dumps({"cases": len(CASES), "errors": sum(c["error"] for c in CASES)}))
