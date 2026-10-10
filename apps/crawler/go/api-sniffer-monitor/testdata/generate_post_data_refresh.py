"""Freeze original POST-token refresh with synthetic values and no network."""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

import structlog

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT))
from src.core.monitors import api_sniffer  # noqa: E402

structlog.configure(logger_factory=structlog.ReturnLoggerFactory())


async def main():
    cases = [
        (
            "ordered-form",
            "action=jobs&nonce=old&page=1",
            {"nonce": r"nonce=([a-z]+)"},
            "nonce=fresh",
        ),
        (
            "repeated-fields",
            "z=2&nonce=old&a=1&nonce=other",
            {"nonce": r"nonce=([a-z]+)"},
            "nonce=fresh",
        ),
        (
            "nested-json",
            '{"outer":{"nonce":"old"},"page":1}',
            {"outer.nonce": r"token=([a-z]+)"},
            "token=fresh",
        ),
        ("unicode-token", "nonce=old&page=1", {"nonce": r"nonce=([^\s]+)"}, "nonce=新しい値"),
        ("missing-token", "nonce=old", {"nonce": r"nonce=([a-z]+)"}, "none"),
        ("empty-token", "nonce=old", {"nonce": r"nonce=([a-z]*)"}, "nonce="),
        ("oversized-token", "nonce=old", {"nonce": r"nonce=([a-z]+)"}, "nonce=" + "x" * 16385),
        ("no-group", "nonce=old", {"nonce": r"nonce=[a-z]+"}, "nonce=fresh"),
        ("two-groups", "nonce=old", {"nonce": r"(nonce)=([a-z]+)"}, "nonce=fresh"),
        ("missing-body-field", "action=jobs", {"nonce": r"nonce=([a-z]+)"}, "nonce=fresh"),
        ("unchanged-value", "nonce=fresh", {"nonce": r"nonce=([a-z]+)"}, "nonce=fresh"),
        (
            "two-fields",
            "z=2&nonce=old&token=old",
            {"nonce": r"nonce=([a-z]+)", "token": r"token=([a-z]+)"},
            "nonce=fresh token=current",
        ),
    ]
    output = []
    for name, body, fields, source in cases:
        calls = []

        async def fetch(_client, url, calls=calls, source=source, **_options):
            calls.append(url)
            return source

        api_sniffer.fetch_text_page_with_retry = fetch
        config = {"fields": fields}
        row = {"name": name, "body": body, "refresh": config, "source": source}
        try:
            row["expected_body"] = await api_sniffer._refresh_post_data(
                None, "https://example.com/careers", body, config
            )
            row["error"] = False
        except (ValueError, KeyError, TypeError):
            row["error"] = True
        row["source_requests"] = calls
        output.append(row)
    Path(__file__).with_name("python_post_data_refresh.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
