from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.deel import (
    _API_BASE,
    _DEEL_RE,
    _FRONTEND_BASE,
    _IGNORE_SLUGS,
    _parse_job,
    can_handle,
    discover,
)


class TestSlugRegex:
    def test_new_layout(self):
        m = _DEEL_RE.search("https://jobs.deel.com/klarna")
        assert m and m.group(1) == "klarna"

    def test_legacy_job_boards_layout(self):
        m = _DEEL_RE.search("https://jobs.deel.com/job-boards/klarna")
        assert m and m.group(1) == "klarna"

    def test_ignores_job_details_path(self):
        # /job-details/... is not a slug; it's matched but filtered via _IGNORE_SLUGS
        m = _DEEL_RE.search("https://jobs.deel.com/job-details/abc/overview")
        assert m and m.group(1) in _IGNORE_SLUGS

    def test_no_match(self):
        assert _DEEL_RE.search("https://example.com/careers") is None


class TestParseJob:
    def test_full_post(self):
        post = {
            "id": "69b25a17-12af-4b34-9c20-ad181734ec59",
            "title": "Business Development Manager, Zurich",
            "richtextDescription": "<p>Role description</p>",
            "isCompensationVisible": False,
            "createdAt": "2026-03-09T18:26:23.272Z",
            "job": {
                "jobLocations": [{"location": {"name": "Zurich"}}],
                "jobEmploymentTypes": [{"employmentType": {"name": "Full-time"}}],
                "jobDepartments": [{"department": {"name": "Sales"}}],
                "jobTeams": [{"team": {"name": "EU"}}],
            },
        }
        result = _parse_job(post, "klarna")
        assert result is not None
        assert result.url == (
            "https://jobs.deel.com/klarna/job-details/69b25a17-12af-4b34-9c20-ad181734ec59/overview"
        )
        assert result.title == "Business Development Manager, Zurich"
        assert result.description == "<p>Role description</p>"
        assert result.locations == ["Zurich"]
        assert result.employment_type == "Full-time"
        assert result.date_posted == "2026-03-09T18:26:23.272Z"
        assert result.base_salary is None
        assert result.metadata == {
            "team": "EU",
            "department": "Sales",
            "id": "69b25a17-12af-4b34-9c20-ad181734ec59",
        }

    def test_missing_id_returns_none(self):
        assert _parse_job({"title": "no id"}, "klarna") is None

    def test_salary_visible(self):
        post = {
            "id": "p1",
            "title": "Eng",
            "isCompensationVisible": True,
            "job": {
                "currentCompensation": {
                    "minAmount": 100000,
                    "maxAmount": 150000,
                    "currencyIsoCode": "USD",
                },
            },
        }
        result = _parse_job(post, "acme")
        assert result.base_salary == {
            "currency": "USD",
            "min": 100000,
            "max": 150000,
            "unit": "year",
        }

    def test_salary_hidden(self):
        post = {
            "id": "p1",
            "title": "Eng",
            "isCompensationVisible": False,
            "job": {
                "currentCompensation": {"minAmount": 100000, "maxAmount": 150000},
            },
        }
        assert _parse_job(post, "acme").base_salary is None


class TestDiscover:
    async def test_happy_path(self):
        def handler(request):
            url = str(request.url)
            assert url.startswith(_API_BASE), f"API host must be {_API_BASE}, got {url}"
            if "career_page_settings" in url:
                assert "/guest/ats/organizations/klarna/career_page_settings" in url
                return httpx.Response(
                    200,
                    json={
                        "organizationId": "org-1",
                        "jobBoard": {"id": "board-1"},
                    },
                )
            if "job_postings" in url:
                assert "/guest/ats/organizations/org-1/job_boards/board-1/job_postings" in url
                return httpx.Response(
                    200,
                    json=[
                        {
                            "id": "p-1",
                            "title": "Eng",
                            "richtextDescription": "<p>x</p>",
                            "job": {"jobLocations": [{"location": {"name": "Zurich"}}]},
                        }
                    ],
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.deel.com/klarna",
                "metadata": {"slug": "klarna"},
            }
            jobs = await discover(board, client)
        assert len(jobs) == 1
        assert isinstance(jobs[0], DiscoveredJob)
        assert jobs[0].url.startswith(f"{_FRONTEND_BASE}/klarna/job-details/p-1")

    async def test_settings_failure_raises(self):
        def handler(request):
            return httpx.Response(404, text="not found")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.deel.com/klarna",
                "metadata": {"slug": "klarna"},
            }
            with pytest.raises(ValueError, match="Failed to fetch Deel settings"):
                await discover(board, client)

    async def test_slug_from_legacy_board_url(self):
        captured = {}

        def handler(request):
            url = str(request.url)
            if "career_page_settings" in url:
                captured["settings_url"] = url
                return httpx.Response(
                    200, json={"organizationId": "org-1", "jobBoard": {"id": "board-1"}}
                )
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.deel.com/job-boards/klarna",
                "metadata": {},
            }
            jobs = await discover(board, client)
        assert jobs == []
        assert "/organizations/klarna/" in captured["settings_url"]

    async def test_cached_ids_skip_settings(self):
        def handler(request):
            url = str(request.url)
            if "career_page_settings" in url:
                raise AssertionError("settings must not be fetched when ids are cached")
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://jobs.deel.com/klarna",
                "metadata": {"slug": "klarna", "org_id": "o", "board_id": "b"},
            }
            jobs = await discover(board, client)
        assert jobs == []


class TestCanHandle:
    async def test_non_deel_url(self):
        assert await can_handle("https://example.com/careers") is None

    async def test_ignore_slug(self):
        assert await can_handle("https://jobs.deel.com/job-boards/") is None

    async def test_no_client_returns_slug(self):
        assert await can_handle("https://jobs.deel.com/klarna") == {"slug": "klarna"}

    async def test_probe_success(self):
        def handler(request):
            url = str(request.url)
            if "career_page_settings" in url:
                return httpx.Response(
                    200,
                    json={"organizationId": "o", "jobBoard": {"id": "b"}},
                )
            if "job_postings" in url:
                return httpx.Response(200, json=[{"id": "p1"}, {"id": "p2"}])
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.deel.com/klarna", client)
        assert result == {
            "slug": "klarna",
            "org_id": "o",
            "board_id": "b",
            "jobs": 2,
        }
