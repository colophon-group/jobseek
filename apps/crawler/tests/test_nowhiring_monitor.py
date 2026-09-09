from __future__ import annotations

import json

import httpx

from src.core.monitors import _REGISTRY
from src.core.monitors.nowhiring import (
    _site_url,
    _slug_from_url,
    can_handle,
    discover,
)

SLUG = "fulenwiderkfc"
BOARD_URL = f"https://nowhiring.com/{SLUG}/"
SITE_URL = _site_url(SLUG)
SEARCH_URL = "https://nowhiring.com/api/jobs/search"


def _site() -> dict:
    return {
        "domain": f"nowhiring.com/{SLUG}",
        "jobSearchCriteria": [{"fieldName": "billingAccountId", "fieldValue": "1196902"}],
    }


def _summary() -> dict:
    return {
        "id": "1227688605",
        "jobTitle": "KFC Cook",
        "city": "Charlotte",
        "stateProvCode": "NC",
    }


def _detail() -> dict:
    return {
        **_summary(),
        "billingAccountId": "1196902",
        "jobDescription": "<p>Prepare food and serve guests.</p>",
        "postedDate": "2026-02-06T21:30:06.582Z",
        "addressLine1": "1045 W. Sugar Creek",
        "postalCode": "28213",
        "categories": ["Full-time"],
        "company": "KFC",
        "brandId": 1211000,
        "applicationURL": "https://my.peoplematter.com/application",
    }


def _transport(jobs: list[dict]) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if url == SITE_URL:
            assert request.method == "GET"
            return httpx.Response(200, json=_site())
        if url == SEARCH_URL:
            assert request.method == "POST"
            body = json.loads(request.content)
            assert body["customerId"] == ["1196902"]
            assert body["start"] == "0"
            return httpx.Response(200, json={"list": jobs, "total": len(jobs)})
        assert url == "https://nowhiring.com/api/jobs/1227688605"
        return httpx.Response(200, json=_detail())

    return httpx.MockTransport(handler)


def test_slug_accepts_board_root_and_rejects_spoofs_and_details():
    assert _slug_from_url(BOARD_URL) == SLUG
    assert _slug_from_url(f"https://nowhiring.com/{SLUG}/job-details/1") is None
    assert _slug_from_url(f"https://nowhiring.com.evil.test/{SLUG}") is None
    assert _slug_from_url("https://example.com/fulenwiderkfc") is None


async def test_can_handle_accepts_verified_empty_board():
    async with httpx.AsyncClient(transport=_transport([])) as client:
        assert await can_handle(BOARD_URL, client) == {
            "slug": SLUG,
            "customer_ids": ["1196902"],
            "brand_template_ids": [],
            "brand_ids": [],
            "jobs": 0,
        }


async def test_discover_hydrates_rich_nowhiring_jobs():
    async with httpx.AsyncClient(transport=_transport([_summary()])) as client:
        jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert len(jobs) == 1
    job = jobs[0]
    assert job.url == "https://nowhiring.com/fulenwiderkfc/job-details/1227688605"
    assert job.title == "KFC Cook"
    assert job.description == "<p>Prepare food and serve guests.</p>"
    assert job.locations == ["1045 W. Sugar Creek, Charlotte, NC, 28213"]
    assert job.employment_type == "Full-time"
    assert job.date_posted == "2026-02-06T21:30:06.582Z"
    assert job.source_identity == "nowhiring:1196902:1227688605"
    assert job.metadata == {
        "job_id": "1227688605",
        "company": "KFC",
        "application_url": "https://my.peoplematter.com/application",
        "brand_id": 1211000,
    }


async def test_discover_accepts_empty_inventory_without_detail_requests():
    async with httpx.AsyncClient(transport=_transport([])) as client:
        assert await discover({"board_url": BOARD_URL, "metadata": {}}, client) == []


def test_registered_as_rich_monitor():
    monitor = next(item for item in _REGISTRY if item.name == "nowhiring")
    assert monitor.rich is True
    assert monitor.cost == 10
