from __future__ import annotations

import httpx

from src.core.monitors import _REGISTRY
from src.core.monitors.hibob import (
    _build_description,
    _origin_from_url,
    _parse_job,
    _salary,
    can_handle,
    discover,
)

ORIGIN = "https://acme.careers.hibob.com"


def _posting(**overrides) -> dict:
    posting = {
        "id": "25d303ad-e3c3-40a3-a1af-b08b3aa261a5",
        "title": "Support Engineer",
        "departmentId": "Support",
        "department": "Support",
        "employmentTypeId": "Permanent",
        "employmentType": "Employee",
        "siteId": 1795425,
        "site": "Israel",
        "country": "Israel",
        "language": "en",
        "description": "<p>Work with customers.</p>",
        "requirements": "<ul><li>Linux</li></ul>",
        "responsibilities": "<ul><li>Troubleshoot</li></ul>",
        "benefits": "<p>Health insurance</p>",
        "publishedAt": "2025-11-20T08:53:08.443293133Z",
        "workspaceTypeId": "hybrid",
        "workspaceType": "Hybrid",
        "payTransparencyMinSalary": 70_000,
        "payTransparencyMaxSalary": 90_000,
        "payTransparencySalaryCurrency": "USD",
        "payTransparencySalaryPayPeriod": "annually",
    }
    posting.update(overrides)
    return posting


def _transport(payload: dict, *, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == f"{ORIGIN}/api/job-ad"
        assert request.headers["referer"] == f"{ORIGIN}/"
        assert request.headers["accept"] == "application/json"
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


class TestOriginFromUrl:
    def test_board_and_detail_urls(self):
        assert _origin_from_url(f"{ORIGIN}/") == ORIGIN
        assert _origin_from_url(f"{ORIGIN}/jobs/123") == ORIGIN

    def test_normalizes_host_case(self):
        assert _origin_from_url("https://ACME.CAREERS.HIBOB.COM/jobs") == ORIGIN

    def test_rejects_unrelated_or_spoofed_hosts(self):
        assert _origin_from_url("https://example.com/jobs") is None
        assert _origin_from_url("https://careers.hibob.com.evil.test/jobs") is None


class TestMapping:
    def test_combines_description_sections_and_extras(self):
        description, extras = _build_description(_posting())
        assert description is not None
        assert description.startswith("<p>Work with customers.</p>")
        assert "<h3>Responsibilities</h3>" in description
        assert "<h3>Requirements</h3>" in description
        assert "<h3>Benefits</h3>" in description
        assert extras == {
            "responsibilities": "<ul><li>Troubleshoot</li></ul>",
            "qualifications": "<ul><li>Linux</li></ul>",
            "benefits": "<p>Health insurance</p>",
        }

    def test_empty_description(self):
        assert _build_description({}) == (None, None)

    def test_salary(self):
        assert _salary(_posting()) == {
            "currency": "USD",
            "min": 70_000,
            "max": 90_000,
            "unit": "year",
        }
        assert _salary({}) is None

    def test_parse_complete_job(self):
        job = _parse_job(_posting(), ORIGIN)
        assert job is not None
        assert job.url == f"{ORIGIN}/jobs/25d303ad-e3c3-40a3-a1af-b08b3aa261a5"
        assert job.title == "Support Engineer"
        assert job.locations == ["Israel"]
        assert job.employment_type == "Employee"
        assert job.job_location_type == "hybrid"
        assert job.date_posted == "2025-11-20T08:53:08.443293133Z"
        assert job.language == "en"
        assert job.metadata == {
            "id": "25d303ad-e3c3-40a3-a1af-b08b3aa261a5",
            "department": "Support",
            "department_id": "Support",
            "site_id": 1795425,
            "country": "Israel",
            "employment_type_id": "Permanent",
            "workspace_type_id": "hybrid",
        }

    def test_country_location_fallback_and_missing_id(self):
        job = _parse_job(_posting(site=None, country="United Kingdom"), ORIGIN)
        assert job is not None
        assert job.locations == ["United Kingdom"]
        assert _parse_job(_posting(id=None), ORIGIN) is None


async def test_discover_maps_job_ad_details():
    payload = {
        "filterGroups": {},
        "jobAdDetails": [
            _posting(),
            _posting(
                id="6f783b4e-4059-41ee-a6e4-72842d20b282",
                title="Regional Sales Manager",
                site="EMEA",
                country="United Kingdom",
                employmentType="Contractor",
                employmentTypeId="Contractor",
                workspaceType="Remote",
                workspaceTypeId="remote",
                payTransparencyMinSalary=None,
                payTransparencyMaxSalary=None,
            ),
        ],
    }
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        jobs = await discover({"board_url": f"{ORIGIN}/", "metadata": {}}, client)

    assert len(jobs) == 2
    assert jobs[1].locations == ["EMEA"]
    assert jobs[1].job_location_type == "remote"
    assert jobs[1].base_salary is None


async def test_discover_rejects_invalid_payload():
    async with httpx.AsyncClient(transport=_transport({"sections": []})) as client:
        try:
            await discover({"board_url": ORIGIN, "metadata": {}}, client)
        except ValueError as exc:
            assert "jobAdDetails" in str(exc)
        else:
            raise AssertionError("invalid HiBob payload should fail")


async def test_can_handle_verifies_feed_and_accepts_empty_board():
    async with httpx.AsyncClient(
        transport=_transport({"filterGroups": {}, "jobAdDetails": []})
    ) as client:
        assert await can_handle(ORIGIN, client) == {"origin": ORIGIN, "jobs": 0}


async def test_can_handle_rejects_unrelated_url_without_request():
    def handler(request: httpx.Request) -> httpx.Response:
        raise AssertionError(f"unexpected request: {request.url}")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        assert await can_handle("https://example.com/careers", client) is None


async def test_can_handle_rejects_failed_feed():
    async with httpx.AsyncClient(
        transport=_transport({"error": "unauthorized"}, status_code=401)
    ) as client:
        assert await can_handle(ORIGIN, client) is None


def test_registered_as_rich_monitor():
    monitor = next(item for item in _REGISTRY if item.name == "hibob")
    assert monitor.rich is True
    assert monitor.cost == 10
    assert monitor.save_raw is not None
