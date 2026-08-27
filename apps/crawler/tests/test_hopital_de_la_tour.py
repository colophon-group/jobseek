"""Hôpital de La Tour inventory, tenant, and identity contracts."""

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
            if row["board_slug"] == "hopital-de-la-tour-recrutement"
        )
    return {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"]),
    }


def _offer(
    offer_id: int,
    *,
    worksite: str = "Hôpital de La Tour",
    host: str = "recrutement.latour.ch",
) -> dict:
    return {
        "id": offer_id,
        "uri": f"https://{host}/fr/nos-offres/role-{offer_id}",
        "title": f"Role {offer_id}",
        "information": [
            {
                "id": "type-contrat",
                "value": "Contrat à Durée Indéterminée",
            },
            {"id": "lieu-travail", "value": worksite},
            {"id": "reference", "value": f"LT-{offer_id}"},
            {"id": "taux-occupation", "value": "80 - 100"},
        ],
    }


def _listing_html(
    offers: list[dict],
    *,
    title: str = "Hôpital de La Tour - Carrières",
    path: str = "offers",
) -> str:
    page_props = {
        path: offers,
        "jobs": [{"id": index, "title": f"Taxonomy {index}"} for index in range(24)],
    }
    payload = json.dumps({"props": {"pageProps": page_props}})
    return (
        f"<html><head><title>{title}</title></head><body>"
        f'<script id="__NEXT_DATA__" type="application/json">{payload}</script>'
        "</body></html>"
    )


def _client(html: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, text=html, request=request)
        )
    )


@pytest.mark.asyncio
async def test_la_tour_uses_offers_not_taxonomy_and_keeps_provider_identity():
    async with _client(_listing_html([_offer(1537), _offer(1536)])) as client:
        jobs = await discover(_board(), client)

    assert [job.source_identity for job in jobs] == [
        "adequasys:latour:1537",
        "adequasys:latour:1536",
    ]
    assert [job.title for job in jobs] == ["Role 1537", "Role 1536"]
    assert [job.locations for job in jobs] == [None, None]
    assert [job.metadata["reference"] for job in jobs] == ["LT-1537", "LT-1536"]
    assert [job.metadata["worksite"] for job in jobs] == [
        "Hôpital de La Tour",
        "Hôpital de La Tour",
    ]


@pytest.mark.asyncio
async def test_la_tour_stream_preserves_provider_identity():
    async with _client(_listing_html([_offer(1537)])) as client:
        batches = [batch async for batch in discover_stream(_board(), client)]

    assert [[job.source_identity for job in batch] for batch in batches] == [
        ["adequasys:latour:1537"]
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("html", "message"),
    [
        (
            _listing_html([_offer(1537)], title="Unrelated Careers"),
            "page title did not match",
        ),
        (
            _listing_html([_offer(1537, worksite="Another hospital")]),
            "item did not match",
        ),
        (
            _listing_html([_offer(1537, host="attacker.example")]),
            "outside its configured allowlist",
        ),
    ],
)
async def test_la_tour_fails_closed_on_tenant_or_outbound_drift(html, message):
    async with _client(html) as client:
        with pytest.raises(ValueError, match=message):
            await discover(_board(), client)


@pytest.mark.asyncio
async def test_la_tour_accepts_proved_empty_but_rejects_inventory_path_drift():
    async with _client(_listing_html([])) as client:
        assert await discover(_board(), client) == []

    async with _client(_listing_html([], path="vacancies")) as client:
        with pytest.raises(ValueError, match="strict_path did not resolve to a list"):
            await discover(_board(), client)


def test_la_tour_registry_contains_only_the_scoped_board():
    boards_path = Path(__file__).resolve().parents[1] / "data" / "boards.csv"
    with boards_path.open(newline="", encoding="utf-8") as handle:
        rows = [
            row for row in csv.DictReader(handle) if row["company_slug"] == "hopital-de-la-tour"
        ]

    assert [row["board_slug"] for row in rows] == ["hopital-de-la-tour-recrutement"]
    assert rows[0]["monitor_type"] == "nextdata"
    assert rows[0]["scraper_type"] == "dom"
    scraper_config = json.loads(rows[0]["scraper_config"])
    assert scraper_config["enrich"] == ["description", "locations"]
    assert scraper_config["defaults"]["locations"] == ["Switzerland"]
