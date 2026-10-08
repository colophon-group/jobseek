"""Freeze actual SEEK GraphQL detail requests and validated Python outputs."""

from __future__ import annotations

import ast
import asyncio
import copy
import json
from dataclasses import asdict
from pathlib import Path
from unittest.mock import patch

from src.core.scrapers.seek import scrape

root = Path(__file__).resolve().parents[3]
module = ast.parse((root / "tests/test_seek_monitor.py").read_text())
fixture = next(
    x for x in module.body if isinstance(x, ast.FunctionDef) and x.name == "_detail_payload"
)
namespace = {"ADVERTISER_ID": "9094357"}
exec(
    compile(ast.Module(body=[fixture], type_ignores=[]), "<existing-seek-detail-fixture>", "exec"),
    namespace,
)
base = namespace["_detail_payload"]("94267983")
cases = []
for host in ("au.seek.com", "www.seek.com.au", "nz.seek.com", "www.seek.co.nz"):
    cases.append((host, "https://" + host + "/job/94267983", {}, copy.deepcopy(base)))
for name, field, value in [
    ("expired", "isExpired", True),
    ("inactive", "status", "Inactive"),
    ("string-false", "isExpired", "False"),
    ("string-true", "isExpired", "True"),
    ("bad-expiry", "isExpired", "false"),
    ("null-expiry", "isExpired", None),
    ("numeric-status", "status", 1),
    ("missing-title", "title", None),
    ("blank-title", "title", "  "),
    ("blank-content", "content", "  "),
    ("foreign-job", "id", "123"),
    ("numeric-job", "id", 94267983),
    ("numeric-advertiser", "advertiser", {"id": 9094357, "name": "United Rentals"}),
    ("missing-advertiser", "advertiser", None),
]:
    payload = copy.deepcopy(base)
    payload["data"]["jobDetails"]["job"][field] = value
    cases.append((name, "https://au.seek.com/job/94267983", {}, payload))
for name, config in [
    ("bound-advertiser", {"advertiser_id": "9094357"}),
    ("foreign-advertiser", {"advertiser_id": "123"}),
    ("numeric-option", {"advertiser_id": 9094357}),
    ("null-option", {"advertiser_id": None}),
]:
    cases.append((name, "https://au.seek.com/job/94267983", config, copy.deepcopy(base)))
for name, payload in [
    ("null-job", {"data": {"jobDetails": {"job": None}}}),
    ("missing-job", {}),
    ("empty-job", {"data": {"jobDetails": {"job": {}}}}),
]:
    cases.append((name, "https://au.seek.com/job/94267983", {}, payload))


async def main():
    results = []
    for name, source, config, payload in cases:
        requests = []

        async def fetch(
            http, method, endpoint, headers, body, requests=requests, payload=payload, **kwargs
        ):
            requests.append(
                {
                    "method": method,
                    "url": endpoint,
                    "headers": dict(headers),
                    "body": json.loads(body),
                }
            )
            return payload

        try:
            with patch("src.core.monitors.api_sniffer.http_fetch_with_retry", fetch):
                content = await scrape(source, config, None)
            expected = {k: v for k, v in asdict(content).items() if v is not None}
            error = False
        except ValueError:
            expected = None
            error = True
        results.append(
            dict(
                name=name,
                source=source,
                config=config,
                payload=payload,
                requests=requests,
                expected=expected,
                error=error,
            )
        )
    Path(__file__).with_name("python_seek_detail.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
