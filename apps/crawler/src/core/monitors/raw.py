"""Helpers for saving raw monitor source artifacts during workspace runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import httpx


async def save_json_response(
    artifact_dir: Path,
    client: httpx.AsyncClient,
    url: str,
    *,
    filename: str = "response.json",
    params: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
) -> None:
    resp = await client.get(
        url,
        params=params,
        headers=headers,
        follow_redirects=follow_redirects,
    )
    if resp.status_code == 200:
        (artifact_dir / filename).write_text(
            json.dumps(resp.json(), indent=2, default=str),
            encoding="utf-8",
        )


async def save_text_response(
    artifact_dir: Path,
    client: httpx.AsyncClient,
    url: str,
    *,
    filename: str,
    headers: dict[str, str] | None = None,
    follow_redirects: bool = False,
) -> None:
    resp = await client.get(url, headers=headers, follow_redirects=follow_redirects)
    if resp.status_code == 200:
        (artifact_dir / filename).write_text(resp.text, encoding="utf-8")
