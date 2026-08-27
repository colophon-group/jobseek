"""Hôpital Riviera-Chablais inventory, employer, and identity contracts."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitors.nextdata import discover, discover_stream


def _board() -> dict:
    boards_path = Path(__file__).resolve().parents[1] / "data" / "boards.csv"
    with boards_path.open(newline="", encoding="utf-8") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == "hopital-riviera-chablais-main"
        )
    return {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"]),
    }


def _listing_html(offers: list[dict]) -> str:
    payload = json.dumps({"props": {"pageProps": {"offers": offers}}})
    return f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'


def _detail_html(organization: str) -> str:
    payload = json.dumps(
        {
            "@context": "https://schema.org/",
            "@type": "JobPosting",
            "title": "Role",
            "description": "<p>Substantive description.</p>",
            "hiringOrganization": {
                "@type": "Organization",
                "name": organization,
            },
        }
    )
    return f'<script type="application/ld+json">{payload}</script>'


def _offer(offer_id: int, *, host: str = "emploi.hopitalrivierachablais.ch") -> dict:
    return {
        "id": offer_id,
        "uri": f"https://{host}/fr/nos-offres/role-{offer_id}",
        "title": f"Role {offer_id}",
        "information": [
            {"id": "csi-cd", "value": "Rennaz"},
            {"id": "reference", "value": f"HRC-{offer_id}"},
        ],
    }


def _transport(offers: list[dict], organizations: dict[int, str]):
    requested_paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_paths.append(request.url.path)
        if request.url.path == "/fr":
            return httpx.Response(200, text=_listing_html(offers), request=request)
        offer_id = int(request.url.path.rsplit("-", 1)[1])
        return httpx.Response(
            200,
            text=_detail_html(organizations[offer_id]),
            request=request,
        )

    return httpx.MockTransport(handler), requested_paths


@pytest.mark.asyncio
async def test_hrc_keeps_real_url_with_separate_provider_identity_and_employer_scope():
    transport, _requested = _transport(
        [_offer(600), _offer(601)],
        {
            600: "Hôpital Riviera-Chablais",
            601: "Pôle de psychiatrie et psychothérapie du Chablais",
        },
    )
    async with httpx.AsyncClient(transport=transport) as client:
        jobs = await discover(_board(), client)

    assert len(jobs) == 1
    assert jobs[0].url == ("https://emploi.hopitalrivierachablais.ch/fr/nos-offres/role-600")
    assert jobs[0].source_identity == "adequasys:hrc:600"
    assert jobs[0].title == "Role 600"
    assert jobs[0].locations == ["Rennaz"]


@pytest.mark.asyncio
async def test_hrc_stream_preserves_durable_identity():
    transport, _requested = _transport(
        [_offer(600)],
        {600: "Hôpital Riviera-Chablais"},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        batches = [batch async for batch in discover_stream(_board(), client)]

    assert len(batches) == 1
    assert [job.source_identity for job in batches[0]] == ["adequasys:hrc:600"]


@pytest.mark.asyncio
async def test_hrc_refuses_untrusted_outbound_before_detail_fetch():
    transport, requested = _transport(
        [_offer(600, host="attacker.example")],
        {600: "Hôpital Riviera-Chablais"},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="outside its configured allowlist"):
            await discover(_board(), client)

    assert requested == ["/fr"]


@pytest.mark.asyncio
async def test_hrc_identity_contract_requires_an_outbound_allowlist():
    board = _board()
    board["metadata"].pop("url_allowlist")
    transport, requested = _transport(
        [_offer(600)],
        {600: "Hôpital Riviera-Chablais"},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="source_identity requires a url_allowlist"):
            await discover(board, client)

    assert requested == []


@pytest.mark.asyncio
@pytest.mark.parametrize("offer_id", [True, None, "", "offer 600"])
async def test_hrc_rejects_missing_or_unsafe_provider_identity(offer_id):
    offer = _offer(600)
    offer["id"] = offer_id
    transport, requested = _transport(
        [offer],
        {600: "Hôpital Riviera-Chablais"},
    )
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(
            ValueError,
            match="identity field was missing or invalid|source_identity",
        ):
            await discover(_board(), client)

    assert requested == ["/fr"]


@pytest.mark.asyncio
async def test_hrc_strict_path_rejects_missing_inventory_but_accepts_proved_empty():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing_html([]),
                request=request,
            )
        )
    ) as client:
        assert await discover(_board(), client) == []

    drifted = json.dumps({"props": {"pageProps": {"jobs": []}}})
    html = f'<script id="__NEXT_DATA__" type="application/json">{drifted}</script>'
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=html, request=request)
        )
    ) as client:
        with pytest.raises(ValueError, match="strict_path did not resolve to a list"):
            await discover(_board(), client)


def test_hrc_registry_contains_only_the_scoped_board():
    boards_path = Path(__file__).resolve().parents[1] / "data" / "boards.csv"
    with boards_path.open(newline="", encoding="utf-8") as handle:
        rows = [
            row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "hopital-riviera-chablais"
        ]

    assert [row["board_slug"] for row in rows] == ["hopital-riviera-chablais-main"]
    assert rows[0]["monitor_type"] == "nextdata"
    assert rows[0]["scraper_type"] == "json-ld"
