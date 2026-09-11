from __future__ import annotations

import asyncio
import json
from typing import Any, cast

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
        "lifetime_occupancy": 0,
        "lifetime_capacity": 2048,
        "lifetime_headroom": 2048,
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
    assert (result.lifetime_occupancy, result.lifetime_capacity, result.lifetime_headroom) == (
        0,
        2048,
        2048,
    )
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
            next_scrape_at=0,
            config={"board_id": "11111111-1111-4111-8111-111111111111"},
            browser=True,
            first_time=True,
        )


def test_prepare_activation_and_capacity_responses_are_strict() -> None:
    prepared = client._decode(
        _response(
            outcome="prepared",
            reason="prepared",
            preparation_digest="a" * 64,
            payload_sha256="b" * 64,
            lifetime_occupancy=1271,
            lifetime_headroom=777,
        )
    )
    activated = client._decode(
        _response(
            outcome="activated",
            reason="activated",
            activated=True,
            preparation_digest="a" * 64,
            payload_sha256="b" * 64,
            lifetime_occupancy=1272,
            lifetime_headroom=776,
        )
    )
    assert prepared.lifetime_headroom == 777
    assert activated.activated and activated.lifetime_occupancy == 1272
    with pytest.raises(client.ProducerCapacityError) as raised:
        client._decode(
            _response(
                outcome="capacity",
                reason="namespace_full",
                lifetime_occupancy=2048,
                lifetime_headroom=0,
            )
        )
    assert (
        raised.value.reason,
        raised.value.occupancy,
        raised.value.capacity,
        raised.value.headroom,
    ) == (
        "namespace_full",
        2048,
        2048,
        0,
    )
    with pytest.raises(client.ProducerCapacityError) as pilot:
        client._decode(
            _response(
                outcome="capacity",
                reason="pilot_occupancy_limit",
                lifetime_occupancy=1600,
                lifetime_headroom=448,
            )
        )
    assert pilot.value.headroom == 448
    with pytest.raises(client.ProducerClientError, match="identity"):
        client._decode(_response(lifetime_occupancy=True))


async def test_request_rejects_non_boolean_first_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "_enabled", lambda: True)
    with pytest.raises(client.ProducerClientError, match="mode or schedule"):
        await client.request_task(
            operation="enqueue",
            domain="jobs.example.com",
            posting_id="posting-1",
            next_scrape_at=1,
            config={},
            browser=True,
            first_time=cast(bool, 1),
        )


async def test_client_forwards_nonzero_first_time_for_legacy_classification(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(client, "_enabled", lambda: True)
    observed: dict[str, object] = {}

    async def exchange(request: dict[str, object]) -> client.ProducerResult:
        observed.update(request)
        return client.ProducerResult("legacy")

    monkeypatch.setattr(client, "_exchange", exchange)
    result = await client.request_task(
        operation="enqueue",
        domain="jobs.example.com",
        posting_id="posting-1",
        next_scrape_at=500,
        config={},
        browser=True,
        first_time=True,
    )
    assert result.is_legacy
    assert (observed["first_time"], observed["next_scrape_at_ms"]) == (True, 500_000)


async def test_noncanonical_frame_prefix_is_rejected() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(b"\x80\x00")
    reader.feed_eof()
    with pytest.raises(client.ProducerClientError, match="prefix"):
        await client._read_frame(reader)
