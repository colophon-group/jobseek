from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors import DiscoveredJob
from src.core.monitors.welcometothejungle import (
    _deduplicate_summaries,
    _identity_from_url,
    _parse_job,
    can_handle,
    discover,
)

BOARD_URL = "https://www.welcometothejungle.com/fr/companies/wojo/jobs"


def _summary(slug: str, reference: str, website: str) -> dict:
    return {
        "name": "Responsable Comptable",
        "slug": slug,
        "reference": reference,
        "website": {"reference": website},
        "organization": {"slug": "nextdoor"},
    }


def _detail(slug: str = "responsable-comptable_paris") -> dict:
    return {
        "job": {
            "slug": slug,
            "reference": "WOJO_123",
            "name": "Responsable Comptable",
            "description": "<p>Lead accounting for the group.</p>",
            "profile": "<p>Five years of accounting experience.</p>",
            "skills": [{"name": {"en": "Adaptability", "fr": "Adaptabilite"}}],
            "offices": [
                {
                    "local_address": "Paris, Ile-de-France, France",
                    "address": "Paris, France",
                    "city": "Paris",
                }
            ],
            "contract_type": "full_time",
            "remote": "punctual",
            "published_at": "2026-08-05T14:44:30Z",
            "salary_min": 60_000,
            "salary_max": 75_000,
            "salary_currency": "EUR",
            "salary_period": "annual",
            "language": "fr",
            "profession": {"name": {"en": "Accounting", "fr": "Comptabilite"}},
            "start_date": "2026-09-01",
            "archived_at": None,
            "status": "published",
        }
    }


class TestIdentity:
    def test_company_jobs_url(self):
        assert _identity_from_url(BOARD_URL) == ("fr", "wojo")

    def test_job_detail_url(self):
        assert _identity_from_url(f"{BOARD_URL}/engineer_paris") == ("fr", "wojo")

    @pytest.mark.parametrize(
        "url",
        [
            "https://example.com/fr/companies/wojo/jobs",
            "https://www.welcometothejungle.com/fr/jobs",
        ],
    )
    def test_rejects_unrelated_url(self, url):
        assert _identity_from_url(url) is None


class TestDeduplication:
    def test_prefers_company_copy_and_deduplicates_reference(self):
        marketplace = _summary("accountant", "WOJO_1", "wttj_fr")
        company = _summary("accountant", "WOJO_1", "wojo")
        other = _summary("engineer", "WOJO_2", "wttj_fr")

        result = _deduplicate_summaries([marketplace, company, other], "wojo")

        assert result == [company, other]

    def test_falls_back_to_slug_when_reference_is_missing(self):
        first = _summary("accountant", "", "wttj_fr")
        duplicate = _summary("accountant", "", "wojo")
        assert _deduplicate_summaries([first, duplicate], "wojo") == [duplicate]


class TestParseJob:
    def test_maps_complete_detail(self):
        job = _parse_job(_detail()["job"], locale="fr", public_slug="wojo")

        assert isinstance(job, DiscoveredJob)
        assert job.url == (
            "https://www.welcometothejungle.com/fr/companies/wojo/jobs/responsable-comptable_paris"
        )
        assert job.title == "Responsable Comptable"
        assert job.description == "<p>Lead accounting for the group.</p>"
        assert job.locations == ["Paris, Ile-de-France, France"]
        assert job.employment_type == "full_time"
        assert job.job_location_type == "hybrid"
        assert job.date_posted == "2026-08-05T14:44:30Z"
        assert job.base_salary == {
            "currency": "EUR",
            "min": 60_000,
            "max": 75_000,
            "unit": "year",
        }
        assert job.language == "fr"
        assert job.extras == {
            "qualifications": "<p>Five years of accounting experience.</p>",
            "skills": ["Adaptabilite"],
        }
        assert job.metadata == {
            "reference": "WOJO_123",
            "start_date": "2026-09-01",
            "profession": "Comptabilite",
        }

    def test_archived_or_incomplete_job_is_skipped(self):
        archived = _detail()["job"]
        archived["archived_at"] = "2026-08-01T00:00:00Z"
        archived["status"] = "archived"
        assert _parse_job(archived, locale="fr", public_slug="wojo") is None
        assert _parse_job({"slug": "missing-title"}, locale="fr", public_slug="wojo") is None


class TestDiscover:
    async def test_resolves_legacy_slug_deduplicates_and_hydrates(self):
        requests: list[httpx.Request] = []
        summaries = [
            _summary("responsable-comptable_paris", "WOJO_1", "wttj_fr"),
            _summary("responsable-comptable_paris", "WOJO_1", "wojo"),
            _summary("business-partner_lyon", "WOJO_2", "wojo"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.url.host == "api.welcometothejungle.com":
                if request.url.path.endswith("/organizations/wojo"):
                    return httpx.Response(200, json={"organization": {"slug": "nextdoor"}})
                job_slug = request.url.path.rsplit("/", 1)[-1]
                return httpx.Response(200, json=_detail(job_slug))
            assert request.method == "POST"
            body = json.loads(request.content)
            assert "organization.slug%3Anextdoor" in body["requests"][0]["params"]
            assert request.headers["x-algolia-application-id"] == "CSEKHVMS53"
            return httpx.Response(
                200,
                json={"results": [{"hits": summaries, "nbHits": 3, "nbPages": 1}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

        assert isinstance(jobs, list)
        assert len(jobs) == 2
        assert {job.url.rsplit("/", 1)[-1] for job in jobs} == {
            "responsable-comptable_paris",
            "business-partner_lyon",
        }
        detail_requests = [
            request
            for request in requests
            if request.url.host == "api.welcometothejungle.com" and "/jobs/" in request.url.path
        ]
        assert len(detail_requests) == 2

    async def test_detail_disappearing_during_hydration_is_skipped(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.welcometothejungle.com":
                return httpx.Response(404)
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "hits": [_summary("closed", "WOJO_CLOSED", "wojo")],
                            "nbHits": 1,
                        }
                    ]
                },
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": BOARD_URL,
                    "metadata": {"slug": "wojo", "organization_slug": "nextdoor"},
                },
                client,
            )
        assert jobs == []

    async def test_marks_algolia_ceiling_as_truncated(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json={"results": [{"hits": [], "nbHits": 1_001, "nbPages": 2}]},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {
                    "board_url": BOARD_URL,
                    "metadata": {"slug": "wojo", "organization_slug": "nextdoor"},
                },
                client,
            )
        assert isinstance(result, MonitorResult)
        assert result.truncated is True


class TestCanHandle:
    async def test_detects_board_and_reports_deduplicated_count(self):
        summaries = [
            _summary("accountant", "WOJO_1", "wttj_fr"),
            _summary("accountant", "WOJO_1", "wojo"),
            _summary("engineer", "WOJO_2", "wojo"),
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "api.welcometothejungle.com":
                return httpx.Response(200, json={"organization": {"slug": "nextdoor"}})
            return httpx.Response(200, json={"results": [{"hits": summaries, "nbHits": 3}]})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(BOARD_URL, client)

        assert result == {
            "slug": "wojo",
            "locale": "fr",
            "organization_slug": "nextdoor",
            "jobs": 2,
        }

    async def test_rejects_non_wttj_url_without_request(self):
        def handler(request: httpx.Request) -> httpx.Response:
            raise AssertionError("unexpected request")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/jobs", client) is None
