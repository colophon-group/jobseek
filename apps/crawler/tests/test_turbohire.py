from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors import DiscoveredJob, all_monitor_types, is_rich_monitor
from src.core.monitors.turbohire import (
    _jobs_request_body,
    _locations,
    _org_id_from_url,
    _parse_job,
    can_handle,
    discover,
)

ORG_ID = "4d757ba0-3d57-448a-b82c-238ed87ac90f"
BOARD_URL = f"https://flipkart.turbohire.co/careerpage/{ORG_ID}"


def _raw_job() -> dict:
    return {
        "JobId": "d48b732d-c749-4e82-bd53-fcef465411db",
        "JobIdObfuscated": "abc%2Fdef",
        "JobTitle": "Branch Manager",
        "JobCode": "FIPL-54320",
        "Department": "Service Delivery",
        "ClientName": "Flipkart",
        "JobDescriptionV2": "<p>Lead the service-delivery team.</p>",
        "RolesAndResponsibilitiesV2": "<p>Own branch operations.</p>",
        "EligibilityV2": "<p>Five years of experience.</p>",
        "Location": json.dumps([{"Address": "Ghaziabad, Uttar Pradesh, India"}]),
        "JobTypeV2": "Full Time",
        "PublishedDates": {"CAREERPAGE": "2026-07-31T12:06:25Z"},
        "Experience": {"MinExp": 5, "MaxExp": 10},
        "Skills": [" Leadership ", "Operations", "Leadership"],
    }


class TestOrgId:
    def test_career_page_url(self):
        assert _org_id_from_url(BOARD_URL) == ORG_ID

    def test_dashboard_url(self):
        assert (
            _org_id_from_url(f"https://flipkart.turbohire.co/dashboardv2?orgId={ORG_ID}&type=0")
            == ORG_ID
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/careerpage/4d757ba0-3d57-448a-b82c-238ed87ac90f",
            "https://flipkart.turbohire.co/careerpage/not-a-uuid",
            "https://turbohire.co/careerpage/4d757ba0-3d57-448a-b82c-238ed87ac90f",
        ],
    )
    def test_rejects_unrelated_or_invalid_url(self, url: str):
        assert _org_id_from_url(url) is None


def test_locations_parse_and_deduplicate():
    value = json.dumps(
        [
            {"Address": "Bengaluru, Karnataka, India"},
            {"Address": " bengaluru, Karnataka, India "},
            {"Address": "Ahmedabad, Gujarat, India"},
        ]
    )
    assert _locations(value) == [
        "Bengaluru, Karnataka, India",
        "Ahmedabad, Gujarat, India",
    ]


def test_parse_rich_job():
    job = _parse_job(_raw_job(), portal_origin="https://flipkart.turbohire.co")

    assert isinstance(job, DiscoveredJob)
    assert job.url == "https://flipkart.turbohire.co/job/publicjobs/abc%2Fdef"
    assert job.title == "Branch Manager"
    assert job.description == "<p>Lead the service-delivery team.</p>"
    assert job.locations == ["Ghaziabad, Uttar Pradesh, India"]
    assert job.employment_type == "Full Time"
    assert job.date_posted == "2026-07-31T12:06:25Z"
    assert job.language == "en"
    assert job.extras == {
        "skills": ["Leadership", "Operations"],
        "responsibilities": "<p>Own branch operations.</p>",
        "qualifications": "<p>Five years of experience.</p>",
    }
    assert job.metadata == {
        "id": "d48b732d-c749-4e82-bd53-fcef465411db",
        "job_code": "FIPL-54320",
        "department": "Service Delivery",
        "client_name": "Flipkart",
        "experience_min": 5,
        "experience_max": 10,
    }


def _handler(request: httpx.Request) -> httpx.Response:
    assert request.headers["origin"] == "https://flipkart.turbohire.co"
    if request.url.path == "/api/token/noauth":
        assert "authorization" not in request.headers
        return httpx.Response(200, json={"access_token": "public-token"})
    assert request.headers["authorization"] == "Bearer public-token"
    if request.url.path == "/api/careerpagev2/filteredjobs":
        assert request.method == "POST"
        assert request.url.params["orgId"] == ORG_ID
        assert json.loads(request.content) == _jobs_request_body()
        return httpx.Response(200, json={"Total": 1, "Result": [_raw_job()]})
    if request.url.path == "/api/publicjobs":
        assert request.url.params["jobId"] == "abc%2Fdef"
        return httpx.Response(200, json=_raw_job())
    raise AssertionError(f"unexpected request: {request.method} {request.url}")


async def test_discover_fetches_token_listing_and_details():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert len(jobs) == 1
    assert jobs[0].title == "Branch Manager"
    assert jobs[0].description == "<p>Lead the service-delivery team.</p>"


async def test_discover_rejects_incomplete_listing():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/token/noauth":
            return httpx.Response(200, json={"access_token": "public-token"})
        return httpx.Response(200, json={"Total": 2, "Result": [_raw_job()]})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="returned 1 of 2 jobs"):
            await discover({"board_url": BOARD_URL, "metadata": {}}, client)


async def test_can_handle_verifies_live_api():
    async with httpx.AsyncClient(transport=httpx.MockTransport(_handler)) as client:
        result = await can_handle(BOARD_URL, client)

    assert result == {"org_id": ORG_ID, "jobs": 1}


def test_registered_as_rich_monitor():
    assert "turbohire" in all_monitor_types()
    assert is_rich_monitor("turbohire") is True
