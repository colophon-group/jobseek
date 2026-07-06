from __future__ import annotations

import httpx
import pytest

from src.core.monitors import BoardGoneError, DiscoveredJob, amazon
from src.core.monitors.amazon import _parse_job, discover


def _raw_job(job_id: str, **overrides) -> dict:
    raw = {
        "job_path": f"/en/jobs/{job_id}/software-development-engineer",
        "title": "Software Development Engineer",
        "description": "<p>Build APIs</p>",
        "basic_qualifications": "<p>Python</p>",
        "preferred_qualifications": "<p>Distributed systems</p>",
        "normalized_location": "Berlin, BE, DEU",
        "job_schedule_type": "Full-Time",
        "posted_date": "March  9, 2026",
        "salary": "$151,300/year - $261,500/year",
        "id_icims": f"ICIMS-{job_id}",
        "job_category": "Software Development",
        "job_family": "Engineering",
        "business_category": "AWS",
        "company_name": "Amazon",
        "country_code": "DEU",
    }
    raw.update(overrides)
    return raw


def _board(metadata: dict | None = None) -> dict:
    return {"board_url": "https://www.amazon.jobs/en/", "metadata": metadata or {}}


class TestParseJob:
    def test_maps_listing_payload_to_discovered_job(self):
        job = _parse_job(_raw_job("123"))

        assert job is not None
        assert job.url == "https://www.amazon.jobs/en/jobs/123/software-development-engineer"
        assert job.title == "Software Development Engineer"
        assert "<p>Build APIs</p>" in job.description
        assert "<h3>Basic Qualifications</h3>" in job.description
        assert "<h3>Preferred Qualifications</h3>" in job.description
        assert job.locations == ["Berlin, BE, DEU"]
        assert job.employment_type == "Full-Time"
        assert job.date_posted == "2026-03-09"
        assert job.base_salary == {
            "currency": "USD",
            "min": 151300.0,
            "max": 261500.0,
            "unit": "year",
        }
        assert job.metadata == {
            "id_icims": "ICIMS-123",
            "job_category": "Software Development",
            "job_family": "Engineering",
            "business_category": "AWS",
            "company_name": "Amazon",
            "country_code": "DEU",
        }

    def test_skips_payload_without_job_path(self):
        assert _parse_job({"title": "No URL"}) is None


class TestDiscover:
    async def test_discovers_single_page_jobs(self):
        seen_offsets: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen_offsets.append(int(request.url.params["offset"]))
            return httpx.Response(
                200,
                json={
                    "hits": 2,
                    "jobs": [
                        _raw_job("1", title="SDE I"),
                        _raw_job("2", title="SDE II"),
                    ],
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(_board(), client)

        assert seen_offsets == [0]
        assert [job.title for job in jobs] == ["SDE I", "SDE II"]
        assert all(isinstance(job, DiscoveredJob) for job in jobs)

    async def test_first_page_404_is_board_gone(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(404, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BoardGoneError, match="Amazon Jobs API returned 404") as exc:
                await discover(_board(), client)

        assert exc.value.url is not None
        assert exc.value.url.startswith(amazon.API_URL)

    async def test_partitions_by_country_and_flags_truncation(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(amazon, "_API_RESULT_CAP", 3)
        monkeypatch.setattr(amazon, "MAX_JOBS", 3)
        seen_countries: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            country = request.url.params.get("country")
            seen_countries.append(country)
            if country is None:
                payload = {
                    "hits": 3,
                    "jobs": [
                        _raw_job("seed-usa", country_code="USA"),
                        _raw_job("seed-can", country_code="CAN"),
                    ],
                }
            elif country == "CAN":
                payload = {
                    "hits": 2,
                    "jobs": [
                        _raw_job("can-1", country_code="CAN"),
                        _raw_job("can-2", country_code="CAN"),
                    ],
                }
            elif country == "USA":
                payload = {
                    "hits": 2,
                    "jobs": [
                        _raw_job("usa-1", country_code="USA"),
                        _raw_job("usa-2", country_code="USA"),
                    ],
                }
            else:
                payload = {"hits": 0, "jobs": []}
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(_board(), client)

        assert seen_countries == [None, "CAN", "USA"]
        assert result.truncated is True
        assert len(result.jobs_by_url) == 4

    async def test_country_cap_partitions_by_category(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(amazon, "_API_RESULT_CAP", 2)

        async def category_slugs(_client: httpx.AsyncClient) -> list[str]:
            return ["software-development", "operations"]

        monkeypatch.setattr(amazon, "_fetch_category_slugs", category_slugs)
        seen_categories: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            category = request.url.params.get("category[]")
            seen_categories.append(category)
            if category is None:
                payload = {"hits": 2, "jobs": [_raw_job("seed", country_code="USA")]}
            elif category == "software-development":
                payload = {
                    "hits": 2,
                    "jobs": [
                        _raw_job("seed", country_code="USA"),
                        _raw_job("software", country_code="USA"),
                    ],
                }
            elif category == "operations":
                payload = {
                    "hits": 1,
                    "jobs": [_raw_job("operations", country_code="USA")],
                }
            else:
                payload = {"hits": 0, "jobs": []}
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(_board({"country": "USA"}), client)

        assert seen_categories == [None, "software-development", "operations"]
        assert [job.url.rsplit("/", 2)[1] for job in jobs] == ["seed", "software", "operations"]
