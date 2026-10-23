"""Freeze synthetic mutable-document identities from the original Python code."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from src.shared.response_fingerprint import (
    build_response_fingerprint_url,
    response_fingerprint_validators,
)

SOURCE = "https://careers.example.com/vacancy.pdf?lang=en&blank=&encoded=%2F#section"
HEADERS = {
    "etag": '"strong-opaque-validator-001"',
    "last-modified": "Thu, 22 Oct 2026 10:30:00 GMT",
    "content-length": "1024",
    "content-type": "application/pdf; version=1.7",
}


def capture(name: str, source: str, changes: dict[str, str | None]) -> dict:
    headers = {k: v for k, v in (HEADERS | changes).items() if v is not None}
    response = httpx.Response(200, headers=headers, request=httpx.Request("HEAD", source))
    row = {"name": name, "source": source, "headers": headers}
    try:
        validators = response_fingerprint_validators(
            response, expected_content_type="application/pdf", source_url=source
        )
        row.update(expected_url=build_response_fingerprint_url(source, validators), error=False)
    except ValueError:
        row["error"] = True
    return row


cases = [
    capture(name, SOURCE, changes)
    for name, changes in [
        ("complete", {}),
        ("identity-encoding", {"content-encoding": "identity"}),
        ("weak", {"etag": 'W/"strong-opaque-validator-001"'}),
        ("encoded", {"content-encoding": "gzip"}),
        ("wrong-type", {"content-type": "text/html"}),
        ("missing-etag", {"etag": None}),
        ("missing-date", {"last-modified": None}),
        ("naive-date", {"last-modified": "Thu Oct 22 10:30:00 2026"}),
        ("zero", {"content-length": "0"}),
        ("oversized", {"content-length": str((20 << 20) + 1)}),
    ]
]
cases += [
    capture("no-query", "https://careers.example.com/vacancy.pdf", {}),
    capture("empty-query", "https://careers.example.com/vacancy.pdf?#section", {}),
]
Path(__file__).with_name("python_response_fingerprints.json").write_text(
    json.dumps(cases, indent=2) + "\n", encoding="utf-8"
)
