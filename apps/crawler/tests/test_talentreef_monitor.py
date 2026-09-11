from __future__ import annotations

import json

import httpx

from src.core.monitors import _REGISTRY
from src.core.monitors.talentreef import (
    _career_page_url,
    _identity_from_url,
    _search_url,
    can_handle,
    discover,
)

ALIAS = "fulen-tacobell"
BOARD_URL = f"https://apply.jobappnetwork.com/{ALIAS}/en"
CAREER_URL = _career_page_url(ALIAS)
SEARCH_URL = _search_url("en-us")


def _career_page() -> list[dict]:
    return [
        {
            "id": "8317f5ac-91d4-4393-b971-d1fe73331336",
            "clientId": "18746",
            "locale": "en",
            "brands": ["Taco Bell"],
            "brandIds": ["814"],
            "published": True,
            "defaultLocale": True,
            "url": ALIAS,
        }
    ]


def _search_payload(hits: list[dict]) -> dict:
    return {"hits": {"total": len(hits), "hits": hits}}


def _transport(hits: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if str(request.url) == CAREER_URL:
            assert request.method == "GET"
            return httpx.Response(200, json=_career_page())
        assert str(request.url) == SEARCH_URL
        assert request.method == "POST"
        body = json.loads(request.content)
        assert body["query"]["bool"]["filter"][:2] == [
            {"terms": {"clientId.raw": ["18746"]}},
            {"terms": {"brand.raw": ["Taco Bell"]}},
        ]
        return httpx.Response(200, json=_search_payload(hits))

    return httpx.MockTransport(handler)


def test_identity_accepts_career_alias_and_rejects_spoofs():
    assert _identity_from_url(BOARD_URL) == (ALIAS, "en")
    assert _identity_from_url(f"https://apply.jobappnetwork.com/{ALIAS}") == (
        ALIAS,
        "en",
    )
    assert _identity_from_url(f"https://apply.jobappnetwork.com.evil.test/{ALIAS}") is None
    assert _identity_from_url("https://example.com/fulen-tacobell") is None


async def test_can_handle_and_discover_accept_verified_empty_board():
    async with httpx.AsyncClient(transport=_transport([])) as client:
        assert await can_handle(BOARD_URL, client) == {
            "alias": ALIAS,
            "client_id": "18746",
            "locale": "en",
            "brands": ["Taco Bell"],
            "jobs": 0,
        }
        assert await discover({"board_url": BOARD_URL, "metadata": {}}, client) == []


async def test_discover_maps_rich_talentreef_posting():
    hits = [
        {
            "_id": "95001",
            "_source": {
                "jobId": 95001,
                "positionType": "Shift Manager",
                "description": "<p>Lead a restaurant shift.</p>",
                "address": {
                    "street1": "100 Main St",
                    "city": "Charlotte",
                    "stateOrProvince": "NC",
                    "postalCode": "28202",
                    "country": "US",
                },
                "category": "Full-time",
                "createdDate": "2026-09-01T12:00:00Z",
                "clientId": "18746",
                "clientName": "Fulenwider Enterprises",
                "brand": "Taco Bell",
                "postingUuid": "posting-uuid",
            },
        }
    ]
    async with httpx.AsyncClient(transport=_transport(hits)) as client:
        jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.url == "https://apply.jobappnetwork.com/clients/18746/posting/95001/en"
    assert job.title == "Shift Manager"
    assert job.description == "<p>Lead a restaurant shift.</p>"
    assert job.locations == ["100 Main St, Charlotte, NC, 28202, US"]
    assert job.employment_type == "Full-time"
    assert job.date_posted == "2026-09-01T12:00:00Z"
    assert job.source_identity == "talentreef:18746:95001"


def test_registered_as_rich_monitor():
    monitor = next(item for item in _REGISTRY if item.name == "talentreef")
    assert monitor.rich is True
    assert monitor.cost == 10
