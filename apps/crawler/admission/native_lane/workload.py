"""Deterministic, non-production input for the ordinary native lane comparison."""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

_MODE = os.environ.get("NATIVE_LANE_INPUT_MODE", "generated")
if _MODE not in ("generated", "captured"):
    raise ValueError("unknown native comparison input mode")
CAPTURED = _MODE == "captured"
CORPUS = Path(__file__).with_name("corpus")
_CAPTURE = json.loads((CORPUS / "manifest.json").read_text()) if CAPTURED else None
_GROUPS = (
    sorted(
        {
            (
                urlsplit(row["source_url"]).hostname,
                row["scraper_type"],
                json.dumps(row["scraper_config"], sort_keys=True),
            )
            for row in _CAPTURE["cases"]
        }
    )
    if _CAPTURE
    else []
)
PROFILES = (
    tuple(f"captured_{index}" for index in range(len(_GROUPS)))
    if CAPTURED
    else ("api_rich", "dom_direct", "dom_rendered", "jsonld_direct")
)
PROFILE_COUNTS = {"api_rich": 500, "dom_direct": 12, "dom_rendered": 12, "jsonld_direct": 12}
ORIGINS = (
    tuple(sorted({group[0] for group in _GROUPS}))
    if CAPTURED
    else tuple(f"origin-{index}.lane.bench.test" for index in range(4))
)


def canonical(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


def jobs() -> list[dict[str, Any]]:
    if _CAPTURE is not None:
        rows = []
        for record in _CAPTURE["cases"]:
            parsed = urlsplit(record["source_url"])
            group = (
                parsed.hostname,
                record["scraper_type"],
                json.dumps(record["scraper_config"], sort_keys=True),
            )
            rows.append(
                {
                    **record,
                    "profile": PROFILES[_GROUPS.index(group)],
                    "origin": parsed.hostname,
                    "path": parsed.path + ("?" + parsed.query if parsed.query else ""),
                    "url": record["source_url"],
                    "title": record["expected"]["title"],
                    "preseeded": True,
                    "delay_ms": 0,
                }
            )
        return rows
    rows = []
    for origin, profile in zip(ORIGINS, PROFILES, strict=True):
        for index in range(PROFILE_COUNTS[profile]):
            size = ("normal", "slow", "large")[index % 3]
            description = (
                "<p>Build reliable Go services. Salary $100,000-$120,000 per year. "
                "Five years of experience. Full-time remote work.</p>"
            )
            if size == "large":
                description += (
                    "<p>Design and maintain scalable systems with Go and Python.</p>" * 128
                )
            rows.append(
                {
                    "profile": profile,
                    "origin": origin,
                    "path": f"/jobs/{profile}/{index}",
                    "url": f"https://{origin}/jobs/{profile}/{index}",
                    "title": f"Senior Software Engineer {profile} {index}",
                    "description": description,
                    "size": size,
                    "delay_ms": 350 if size == "slow" else 0,
                    "padding_bytes": 262144 if size == "large" else 0,
                }
            )
    return rows


def manifest() -> dict[str, Any]:
    if _CAPTURE is not None:
        return _CAPTURE
    return {"schema": "jobseek.native-lane-workload/v1", "jobs": jobs()}


def workload_sha256() -> str:
    return hashlib.sha256(canonical(manifest())).hexdigest()


def jsonld(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "@context": "https://schema.org",
        "@type": "JobPosting",
        "title": row["title"],
        "description": row["description"],
        "employmentType": "FULL_TIME",
        "jobLocationType": "TELECOMMUTE",
        "applicantLocationRequirements": {"@type": "Country", "name": "United States"},
        "datePosted": "2026-10-01",
    }


def detail_body(row: dict[str, Any]) -> bytes:
    if CAPTURED:
        filename = row["html_file"]
        if Path(filename).name != filename:
            raise ValueError("captured response path escapes corpus")
        with gzip.open(CORPUS / filename, "rb") as stream:
            body = stream.read((8 << 20) + 1)
        if (
            len(body) > 8 << 20
            or len(body) != row["html_bytes"]
            or hashlib.sha256(body).hexdigest() != row["html_sha256"]
        ):
            raise ValueError("captured response identity differs")
        return body
    payload = canonical(jsonld(row)).decode().replace("</", "<\\/")
    if row["profile"] == "dom_rendered":
        # The raw response deliberately has no JobPosting JSON-LD script.
        # Both browser routes must execute this script to obtain the same data.
        data = json.dumps(payload)
        content = (
            "<script>const s=document.createElement('script');"
            "s.type='application/ld+json';s.textContent=" + data + ";"
            "document.head.appendChild(s);</script>"
        )
    else:
        content = '<script type="application/ld+json">' + payload + "</script>"
    return (
        "<!doctype html><html><head>" + content + "</head><body><main>Careers</main>"
        '<div hidden="hidden">' + "x" * row["padding_bytes"] + "</div></body></html>"
    ).encode()


def listing_body(profile: str) -> bytes:
    selected = [row for row in jobs() if row["profile"] == profile]
    if profile == "api_rich":
        return canonical(
            {
                "jobs": [
                    {
                        "url": row["url"],
                        "name": row["title"],
                        "body": row["description"],
                        "city": "United States",
                        "employment": "Full-time",
                        "workplace": "remote",
                        "skills": ["Go", "Python"],
                    }
                    for row in selected
                ]
            }
        )
    links = "".join(f'<a href="{row["path"]}">{row["title"]}</a>' for row in selected)
    if profile == "dom_rendered":
        links = "<script>document.body.innerHTML=" + json.dumps(links) + ";</script>"
    return ("<!doctype html><html><head></head><body>" + links + "</body></html>").encode()


def boards() -> list[dict[str, Any]]:
    if CAPTURED:
        return [
            {
                "slug": profile,
                "url": next(row["url"] for row in jobs() if row["profile"] == profile),
                "domain": group[0],
                "crawler_type": "dom",
                "monitor_needs_browser": False,
                "scraper_needs_browser": False,
                "monitor_seeded": False,
                "metadata": {
                    "scraper_type": group[1],
                    "scraper_config": json.loads(group[2]),
                    "rescrape_policy": "never",
                },
            }
            for profile, group in zip(PROFILES, _GROUPS, strict=True)
        ]
    result = []
    for origin, profile in zip(ORIGINS, PROFILES, strict=True):
        rendered = profile == "dom_rendered"
        metadata: dict[str, Any] = {
            "scraper_type": "json-ld",
            "scraper_config": {"render": rendered, "skip_ssl": True, "ignore_locations": True},
            "rescrape_policy": "never",
            "skip_ssl": True,
        }
        if rendered:
            del metadata["skip_ssl"]
            del metadata["scraper_config"]["skip_ssl"]
            metadata["render"] = True
            metadata["wait"] = "load"
        if profile == "api_rich":
            metadata.update(
                api_url=f"https://{origin}/listing",
                json_path="jobs",
                url_field="url",
                fields={
                    "title": "name",
                    "description": "body",
                    "locations": "city",
                    "employment_type": "employment",
                    "job_location_type": "workplace",
                    "skills": "skills",
                },
            )
        elif profile != "jsonld_direct":
            metadata["url_filter"] = {"include": f"/jobs/{profile}/[0-9]+$"}
        result.append(
            {
                "slug": profile,
                "url": f"https://{origin}/listing",
                "domain": origin,
                "crawler_type": "api_sniffer" if profile == "api_rich" else "dom",
                "monitor_needs_browser": rendered,
                "scraper_needs_browser": rendered,
                "monitor_seeded": profile != "jsonld_direct",
                "metadata": metadata,
            }
        )
    return result
