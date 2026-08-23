from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.manatal import _parse_job, _slug_from_url, can_handle, discover
from src.workspace._compat import detect_ats_from_url


def _posting(**overrides):
    value = {
        "id": 2432140,
        "hash": "QYXXR8Y5",
        "position_name": "Administrative & Finance Volunteer",
        "description": "<p>Support the finance team.</p>",
        "location_display": "Hanoi, Vietnam",
        "city": "Hanoi",
        "state": "",
        "country": "Vietnam",
    }
    value.update(overrides)
    return value


class TestSlug:
    def test_board_url(self):
        assert _slug_from_url("https://www.careers-page.com/care-vietnam") == "care-vietnam"

    def test_detail_url_keeps_board_slug(self):
        assert (
            _slug_from_url("https://www.careers-page.com/care-vietnam/job/QYXXR8Y5")
            == "care-vietnam"
        )

    def test_unrelated_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_compat_detection(self):
        assert detect_ats_from_url("https://www.careers-page.com/care-vietnam") == "manatal"


class TestParseJob:
    def test_rich_fields(self):
        job = _parse_job(_posting(), "care-vietnam")
        assert job == DiscoveredJob(
            url="https://www.careers-page.com/care-vietnam/job/QYXXR8Y5",
            title="Administrative & Finance Volunteer",
            description="<p>Support the finance team.</p>",
            locations=["Hanoi, Vietnam"],
            metadata={"id": 2432140},
        )

    def test_location_parts_fallback(self):
        job = _parse_job(_posting(location_display=""), "care-vietnam")
        assert job.locations == ["Hanoi, Vietnam"]

    def test_missing_hash(self):
        assert _parse_job(_posting(hash=None), "care-vietnam") is None


class TestDiscover:
    async def test_paginates(self):
        def handler(request: httpx.Request):
            page = request.url.params.get("page")
            if page == "1":
                return httpx.Response(
                    200,
                    json={
                        "count": 2,
                        "next": "https://www.careers-page.com/api/v1.0/c/acme/jobs/?page=2",
                        "results": [_posting(hash="ONE")],
                    },
                )
            return httpx.Response(
                200,
                json={"count": 2, "next": None, "results": [_posting(id=2, hash="TWO")]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://www.careers-page.com/acme",
                    "metadata": {"slug": "acme"},
                },
                client,
            )

        assert [job.url for job in jobs] == [
            "https://www.careers-page.com/acme/job/ONE",
            "https://www.careers-page.com/acme/job/TWO",
        ]

    async def test_invalid_payload_raises(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="Unexpected Manatal"):
                await discover(
                    {"board_url": "https://www.careers-page.com/acme", "metadata": {}},
                    client,
                )


class TestCanHandle:
    async def test_zero_is_detected(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200, json={"count": 0, "next": None, "previous": None, "results": []}
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle("https://www.careers-page.com/acme", client)
        assert result == {"slug": "acme", "jobs": 0}

    async def test_unrelated(self):
        assert await can_handle("https://example.com/jobs") is None
