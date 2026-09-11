from __future__ import annotations

import asyncio
import json
from typing import Any

import pytest

from src.lightpanda import producer_client as client


def _response(**changes: object) -> bytes:
    value = {
        "activated": False,
        "board_slugs": [],
        "cohort": "",
        "existing_payload_sha256": "",
        "existing_state": "",
        "outcome": "legacy",
        "payload_sha256": "",
        "preparation_digest": "",
        "reason": "literal_legacy",
        "version": client.PROTOCOL,
    }
    value.update(changes)
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode("ascii")


def test_only_exact_authenticated_response_can_authorize_literal_legacy() -> None:
    assert client._decode(_response()).is_legacy
    with pytest.raises(client.ProducerClientError, match="decision"):
        client._decode(_response(reason="authority_lost"))
    with pytest.raises(client.ProducerClientError, match="decision"):
        client._decode(_response(activated=True))
    with pytest.raises(client.ProducerClientError, match="canonical"):
        client._decode(b'{"version":"jobseek.lightpanda.producer/v1"}')


def test_only_exact_sorted_go_manifest_is_accepted() -> None:
    result = client._decode(
        _response(
            outcome="manifest",
            reason="manifest",
            cohort="c1",
            board_slugs=["browser-use-careers"],
        )
    )
    assert result.cohort == "c1"
    assert result.board_slugs == ("browser-use-careers",)
    with pytest.raises(client.ProducerClientError, match="decision"):
        client._decode(
            _response(
                outcome="manifest",
                reason="manifest",
                cohort="c1",
                board_slugs=["z", "a"],
            )
        )


async def test_unavailable_producer_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def unavailable(_path: object) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(client, "_enabled", lambda: True)
    monkeypatch.setattr(client, "_identity", lambda: (1, 2))
    monkeypatch.setattr(asyncio, "open_unix_connection", unavailable)
    with pytest.raises(client.ProducerClientError, match="unavailable"):
        await client.request_task(
            operation="prepare",
            domain="jobs.example.com",
            posting_id="00000000-0000-4000-8000-000000000001",
            next_scrape_at=123,
            config={"board_id": "11111111-1111-4111-8111-111111111111"},
            browser=True,
        )


async def test_noncanonical_frame_prefix_is_rejected() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x80\x00")
    reader.feed_eof()
    with pytest.raises(client.ProducerClientError, match="prefix"):
        await client._read_frame(reader)
