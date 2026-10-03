"""Freeze token selection and encoded requests from existing Python monitors."""

from __future__ import annotations

import json
from pathlib import Path

import httpx

from src.core.monitors import ashby, lever

cases = []
for kind, monitor, board_url in (
    ("ashby", ashby, "https://jobs.ashbyhq.com/inferred"),
    ("lever", lever, "https://jobs.eu.lever.co/inferred"),
):
    for token in (None, "", "explicit", "binance.us", "Jasper AI", "Tools for Humanity"):
        metadata = {"token": token, "scraper_type": "skip"}
        selected = metadata.get("token") or monitor._token_from_url(board_url)
        region = lever._region_from_url(board_url) if kind == "lever" else None
        endpoint = (
            monitor._api_url(selected, region) if kind == "lever" else monitor._api_url(selected)
        )
        params = {"limit": 100, "skip": 0} if kind == "lever" else {"includeCompensation": "true"}
        request = httpx.Request("GET", endpoint, params=params)
        cases.append(
            {
                "kind": kind,
                "board_url": board_url,
                "metadata": metadata,
                "token": selected,
                "endpoint": str(request.url),
            }
        )
    for metadata in (
        {"scraper_type": "skip", "board_token": "ignored"},
        {"token": "explicit", "scraper_type": "skip", "region": "us"}
        if kind == "lever"
        else {"token": "explicit", "scraper_type": "skip", "org": "ignored"},
    ):
        selected = metadata.get("token") or monitor._token_from_url(board_url)
        region = metadata.get("region") or lever._region_from_url(board_url)
        endpoint = (
            monitor._api_url(selected, region) if kind == "lever" else monitor._api_url(selected)
        )
        params = {"limit": 100, "skip": 0} if kind == "lever" else {"includeCompensation": "true"}
        cases.append(
            {
                "kind": kind,
                "board_url": board_url,
                "metadata": metadata,
                "token": selected,
                "endpoint": str(httpx.Request("GET", endpoint, params=params).url),
            }
        )

Path(__file__).with_name("python_rich_profile_tokens.json").write_text(
    json.dumps({"cases": cases}, indent=2) + "\n"
)
