"""Freeze actual Python HTTP detail binding and output with an in-memory fetcher."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from src.core.scrapers.api_sniffer import _scrape_http

job = {
    "title": "Software Engineer",
    "description": "<p>Build things.</p>",
    "locations": "Zurich",
    "id": 1234567890123456789,
    "remote": True,
    "skills": ["Python", " ", "", None, False, 0],
    "expiry": "2026-12-01",
    "salary": {"currency": "CHF", "value": 100000},
    "department": "Engineering",
    "salary_text": "CHF 100000 - 120000 per year",
}
fields = {
    "title": "title",
    "description": "description",
    "locations": "locations",
    "metadata.uid": "id",
    "job_location_type": {"path": "remote", "map": {"True": "remote"}},
    "skills": "skills",
    "valid_through": "expiry",
    "base_salary": "salary",
    "department": "department",
    "metadata.sibling": {"lookup_from": "job", "key_from": "title"},
}
cases = [
    ("escaped-fallback", "https://careers.example.net/jobs/A%2FB/?ignored=9", {}),
    (
        "query-capture",
        "https://careers.example.net/jobs?jobId=123",
        {"url_pattern": r"[?&]jobId=(?P<id>[^&]+)"},
    ),
    (
        "fragment-capture",
        "https://careers.example.net/#/jobs/456",
        {"url_pattern": r"#/jobs/(?P<id>[0-9]+)"},
    ),
    (
        "tenant-capture",
        "https://fixture.bamboohr.com/careers/789",
        {
            "url_pattern": r"^https://(?P<tenant>[a-z0-9-]+)\.bamboohr\.com/careers/(?P<id>\d+)(?:/|$)",
            "api_url": "https://{tenant}.bamboohr.com/api/jobs/{id}",
        },
    ),
    (
        "post-binding",
        "https://careers.example.net/42",
        {
            "method": "POST",
            "post_body": '{"id":"{id}"}',
            "request_headers": {"Host": "ignored", "X-Board": "fixture"},
        },
    ),
    (
        "auth-scalars",
        "https://careers.example.net/42",
        {
            "auth_request": {
                "api_url": "https://auth.example.net/session",
                "json_path": "result",
                "header_fields": {
                    "Authorization": "token",
                    "X-Boolean": "flag",
                    "X-Number": "number",
                },
            }
        },
    ),
    (
        "salary-string",
        "https://careers.example.net/42",
        {"fields": {**fields, "base_salary": "salary_text"}},
    ),
    ("missing-object", "https://careers.example.net/42", {"json_path": "missing"}),
]


async def run():
    out = []
    for name, source, overrides in cases:
        config = {
            "api_url": "https://api.example.net/jobs/{id}",
            "json_path": "job",
            "fields": fields,
            **overrides,
        }
        requests = []

        async def fetch(http, method, endpoint, headers, body, requests=requests, **kwargs):
            requests.append(
                {"method": method, "url": endpoint, "headers": dict(headers), "body": body or ""}
            )
            if endpoint == "https://auth.example.net/session":
                return {
                    "result": {
                        "token": "Bearer fixture",
                        "flag": True,
                        "number": 1234567890123456789,
                    }
                }
            return {"job": job}

        with patch("src.core.monitors.api_sniffer.http_fetch_with_retry", fetch):
            result = await _scrape_http(source, config, None)
        expected = {k: v for k, v in asdict(result).items() if v is not None}
        out.append(
            {
                "name": name,
                "source": source,
                "config": config,
                "payload": {"job": job},
                "auth_payload": {
                    "result": {
                        "token": "Bearer fixture",
                        "flag": True,
                        "number": 1234567890123456789,
                    }
                },
                "requests": requests,
                "expected": expected,
            }
        )
    Path(__file__).with_name("python_http_detail.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(run())
