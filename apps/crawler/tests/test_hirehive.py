from __future__ import annotations

from unittest.mock import AsyncMock

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob
from src.core.monitors.hirehive import (
    _parse_job,
    _parse_language,
    _parse_locations,
    _parse_salary,
    _slug_from_url,
    can_handle,
    discover,
)


def _raw_job(job_id: str = "job_abc123") -> dict:
    return {
        "id": job_id,
        "title": "Software Engineer",
        "location": "Munich or Dublin",
        "state_code": None,
        "country": {"name": "Germany", "code": "DE"},
        "salary": None,
        "description": {"html": "<p>Build medical imaging software.</p>", "text": "Build."},
        "category": {"id": "cat_1", "name": "Engineering"},
        "type": {"type": "FullTime", "name": "Full Time"},
        "experience": {"type": "MidLevel", "name": "Mid Level"},
        "language": {"name": "English", "code": "en-US"},
        "published_date": "2026-07-01T14:05:14.983Z",
        "hosted_url": f"https://acme.hirehive.com/software-engineer-{job_id}",
        "compensation_tiers": [
            {
                "type": "range_salary",
                "interval": "year",
                "currency_code": "EUR",
                "min_value": 70_000,
                "max_value": 90_000,
            }
        ],
    }


class TestSlugFromUrl:
    def test_hosted_board(self):
        assert _slug_from_url("https://luma-vision.hirehive.com/") == "luma-vision"

    def test_job_url(self):
        assert _slug_from_url("https://acme.hirehive.com/engineer-abc123") == "acme"

    @pytest.mark.parametrize("slug", ["api", "app", "docs", "www", "hirehive-testing-account"])
    def test_ignored_hirehive_hosts(self, slug: str):
        assert _slug_from_url(f"https://{slug}.hirehive.com") is None

    def test_unrelated_url(self):
        assert _slug_from_url("https://example.com/careers") is None


class TestParsing:
    def test_full_job(self):
        result = _parse_job(_raw_job())

        assert isinstance(result, DiscoveredJob)
        assert result.url == "https://acme.hirehive.com/software-engineer-job_abc123"
        assert result.title == "Software Engineer"
        assert result.description == "<p>Build medical imaging software.</p>"
        assert result.locations == ["Munich or Dublin"]
        assert result.employment_type == "FullTime"
        assert result.date_posted == "2026-07-01T14:05:14.983Z"
        assert result.language == "en"
        assert result.base_salary == {
            "currency": "EUR",
            "min": 70_000,
            "max": 90_000,
            "unit": "year",
        }
        assert result.metadata == {
            "id": "job_abc123",
            "category": "Engineering",
            "experience": "MidLevel",
        }

    def test_missing_hosted_url_is_skipped(self):
        raw = _raw_job()
        raw["hosted_url"] = None
        assert _parse_job(raw) is None

    def test_location_falls_back_to_structured_fields(self):
        assert _parse_locations(
            {"location": None, "state_code": "CA", "country": {"name": "United States"}}
        ) == ["CA, United States"]

    @pytest.mark.parametrize(
        ("code", "expected"),
        [("de-DE", "de"), ("fr_FR", "fr"), ("english", None), (None, None)],
    )
    def test_language_normalization(self, code: str | None, expected: str | None):
        assert _parse_language({"language": {"code": code}}) == expected

    def test_salary_ignores_empty_tier_and_uses_populated_tier(self):
        raw = {
            "compensation_tiers": [
                {"type": "none", "min_value": None, "max_value": None},
                {
                    "type": "fixed_salary",
                    "interval": "month",
                    "currency_code": "CHF",
                    "min_value": 8_000,
                    "max_value": None,
                },
            ]
        }
        assert _parse_salary(raw) == {
            "currency": "CHF",
            "min": 8_000,
            "max": None,
            "unit": "month",
        }


class TestDiscover:
    async def test_paginates_and_returns_rich_jobs(self):
        calls: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path == "/api/v2/jobs"
            page = int(request.url.params["page"])
            calls.append(page)
            return httpx.Response(
                200,
                json={
                    "meta": {
                        "page": page,
                        "page_size": 100,
                        "total_items": 2,
                        "total_pages": 2,
                        "has_next_page": page == 1,
                        "has_previous_page": page > 1,
                    },
                    "items": [_raw_job(f"job_{page}")],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://acme.hirehive.com/", "metadata": {}},
                client,
            )

        assert isinstance(result, list)
        assert len(result) == 2
        assert calls == [1, 2]

    async def test_empty_published_board(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={
                    "meta": {"total_items": 0, "has_next_page": False},
                    "items": [],
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://acme.hirehive.com", "metadata": {}},
                client,
            )
        assert result == []

    async def test_metadata_slug_supports_custom_board_url(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.host == "tenant.hirehive.com"
            return httpx.Response(
                200,
                json={"meta": {"has_next_page": False}, "items": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://careers.example.com", "metadata": {"slug": "tenant"}},
                client,
            )
        assert result == []

    async def test_missing_slug_raises(self):
        async with httpx.AsyncClient(
            transport=httpx.MockTransport(lambda r: httpx.Response(200))
        ) as client:
            with pytest.raises(ValueError, match="Cannot derive HireHive slug"):
                await discover(
                    {"board_url": "https://example.com/careers", "metadata": {}},
                    client,
                )

    async def test_retry_then_recovers(self, monkeypatch: pytest.MonkeyPatch):
        from src.core.monitors import hirehive as hirehive_module

        monkeypatch.setattr(hirehive_module.asyncio, "sleep", AsyncMock())
        calls = 0

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            if calls == 1:
                return httpx.Response(429)
            return httpx.Response(
                200,
                json={"meta": {"has_next_page": False}, "items": [_raw_job()]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://acme.hirehive.com", "metadata": {}},
                client,
            )

        assert isinstance(result, list)
        assert len(result) == 1
        assert calls == 2

    async def test_truncation_is_signalled(self, monkeypatch: pytest.MonkeyPatch):
        from src.core.monitors import hirehive as hirehive_module

        monkeypatch.setattr(hirehive_module, "MAX_JOBS", 1)

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"meta": {"has_next_page": True}, "items": [_raw_job()]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {"board_url": "https://acme.hirehive.com", "metadata": {}},
                client,
            )
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 1


class TestCanHandle:
    async def test_direct_url_without_client(self):
        assert await can_handle("https://acme.hirehive.com") == {"slug": "acme"}

    async def test_direct_url_probes_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"meta": {"total_items": 17}, "items": [_raw_job()]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://acme.hirehive.com", client)
        assert result == {"slug": "acme", "jobs": 17}

    async def test_unrelated_url_without_client(self):
        assert await can_handle("https://example.com/careers") is None
