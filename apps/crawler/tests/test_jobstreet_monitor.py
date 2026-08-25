from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors import all_monitor_types, jobstreet
from src.core.monitors.jobstreet import _identity_from_url, can_handle, discover
from src.core.scrapers import JobContent
from src.core.scrapers.jobstreet import (
    _job_identity_from_url,
    parse_payload,
    scrape,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

COMPANY_ID = "175608148114568"
ORGANISATION_ID = "744981"
BOARD_URL = f"https://my.jobstreet.com/companies/tecan-cdmo-solutions-pn-{COMPANY_ID}/jobs"
SG_BOARD_URL = f"https://sg.jobstreet.com/companies/ehl-educational-group-{COMPANY_ID}/jobs"


def _summary(
    job_id: str,
    *,
    employer_id: str = ORGANISATION_ID,
    company_id: str = COMPANY_ID,
) -> dict:
    return {
        "id": job_id,
        "title": f"Role {job_id}",
        "listingDate": "2026-08-10T05:56:46Z",
        "locations": [{"label": "Simpang Ampat, Penang"}],
        "workTypes": ["Full time"],
        "workArrangements": {"data": [{"label": {"text": "On-site"}}]},
        "salaryLabel": "",
        "employer": {
            "id": employer_id,
            "name": "Tecan CDMO Solutions PN Sdn. Bhd.",
            "companyId": company_id,
        },
        "classifications": [
            {
                "classification": {"description": "Engineering"},
                "subclassification": {"description": "Quality Assurance"},
            }
        ],
    }


def _search_payload(
    rows: list[dict],
    *,
    total: int | None = None,
    page: int = 1,
    page_size: int | None = None,
) -> dict:
    size = jobstreet.PAGE_SIZE if page_size is None else page_size
    count = len(rows) if total is None else total
    return {
        "data": rows,
        "totalCount": count,
        "solMetadata": {
            "pageNumber": page,
            "pageSize": size,
            "totalJobCount": count,
        },
    }


def _company_payload(organisation_id: str | None = ORGANISATION_ID) -> dict:
    profile = {"organisationId": organisation_id} if organisation_id is not None else None
    return {"data": {"companyDetails": {"companyProfile": profile}}}


def _detail_payload(job_id: str, *, expired: bool = False) -> dict:
    return {
        "data": {
            "jobDetails": {
                "job": {
                    "id": job_id,
                    "title": f"Role {job_id}",
                    "content": (
                        "<h2>About the role</h2><p>Complete responsibilities.</p>"
                        "<h2>Requirements</h2><ul><li>Relevant experience</li></ul>"
                    ),
                    "isExpired": expired,
                    "status": "Expired" if expired else "Active",
                    "location": {"label": "Simpang Ampat, Penang"},
                    "workTypes": {"label": "Full time"},
                    "salary": {"label": "RM 8,000 – RM 12,000 per month"},
                    "advertiser": {"name": "Tecan CDMO Solutions PN Sdn. Bhd."},
                    "createdAt": {"dateTimeUtc": "2026-08-10T05:56:10.450Z"},
                    "expiresAt": {"dateTimeUtc": "2026-09-09T13:59:59.999Z"},
                }
            }
        }
    }


def _request_json(request: httpx.Request) -> dict:
    return json.loads(request.content.decode())


class TestIdentity:
    def test_provider_is_registered_with_workspace_defaults(self):
        assert "jobstreet" in all_monitor_types()
        assert detect_ats_from_url(BOARD_URL) == "jobstreet"
        assert auto_scraper_type("jobstreet") == (
            "jobstreet",
            {
                "enrich": [
                    "title",
                    "description",
                    "locations",
                    "employment_type",
                    "date_posted",
                    "base_salary",
                ]
            },
        )

    @pytest.mark.parametrize(
        "url",
        [
            BOARD_URL,
            BOARD_URL.removesuffix("/jobs"),
            BOARD_URL + "/",
            SG_BOARD_URL,
        ],
    )
    def test_extracts_company_identity(self, url: str):
        expected_host = "sg.jobstreet.com" if url == SG_BOARD_URL else "my.jobstreet.com"
        assert _identity_from_url(url) == (expected_host, COMPANY_ID)

    @pytest.mark.parametrize(
        "url",
        [
            "http://my.jobstreet.com/companies/tecan-175608148114568/jobs",
            "https://id.jobstreet.com/companies/tecan-175608148114568/jobs",
            "https://user@my.jobstreet.com/companies/tecan-175608148114568/jobs",
            "https://my.jobstreet.com:444/companies/tecan-175608148114568/jobs",
            BOARD_URL + "?page=2",
            BOARD_URL + "#jobs",
            "https://my.jobstreet.com/Tecan-CDMO-Solutions-PN-Sdn.-Bhd.-jobs",
            "https://my.jobstreet.com/companies/tecan-123/jobs",
            "https://my.jobstreet.com.evil.test/companies/tecan-175608148114568/jobs",
        ],
    )
    def test_rejects_untrusted_or_filtered_urls(self, url: str):
        assert _identity_from_url(url) is None
        assert detect_ats_from_url(url) != "jobstreet"


class TestMonitor:
    async def test_discovers_rich_summaries_without_detail_requests(self):
        job_id = "93872133"
        graphql_queries: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                body = _request_json(request)
                graphql_queries.append(body["query"])
                return httpx.Response(200, json=_company_payload(), request=request)
            assert request.url.path == "/api/jobsearch/v5/search"
            assert request.url.params["companyid"] == ORGANISATION_ID
            assert request.url.params["siteKey"] == "my"
            assert request.url.params["source"] == "COMPANY"
            return httpx.Response(
                200,
                json=_search_payload([_summary(job_id)]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert len(result) == 1
        job = result[0]
        assert job.url == f"https://my.jobstreet.com/job/{job_id}"
        assert job.title == f"Role {job_id}"
        assert job.description is None
        assert job.locations == ["Simpang Ampat, Penang"]
        assert job.employment_type == "Full time"
        assert job.job_location_type == "onsite"
        assert job.date_posted == "2026-08-10T05:56:46Z"
        assert job.language == "en"
        assert job.metadata == {
            "jobstreet_job_id": job_id,
            "jobstreet_company_id": COMPANY_ID,
            "employer": "Tecan CDMO Solutions PN Sdn. Bhd.",
            "classifications": ["Engineering", "Quality Assurance"],
        }
        assert len(graphql_queries) == 1
        assert "companyDetails" in graphql_queries[0]

    async def test_uses_verified_metadata_without_company_lookup(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                raise AssertionError("verified metadata must skip GraphQL")
            return httpx.Response(
                200,
                json=_search_payload([_summary("93872133")]),
                request=request,
            )

        board = {
            "board_url": BOARD_URL,
            "metadata": {"organisation_id": ORGANISATION_ID},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert len(result) == 1

    async def test_paginates_and_rejects_cross_employer_rows(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(jobstreet, "PAGE_SIZE", 2)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                raise AssertionError("verified metadata must skip GraphQL")
            page = int(request.url.params["page"])
            rows = (
                [_summary("93872133"), _summary("93625531")]
                if page == 1
                else [_summary("93961179")]
            )
            return httpx.Response(
                200,
                json=_search_payload(rows, total=3, page=page, page_size=2),
                request=request,
            )

        board = {
            "board_url": BOARD_URL,
            "metadata": {"organisation_id": ORGANISATION_ID},
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)
        assert {job.metadata["jobstreet_job_id"] for job in result} == {
            "93872133",
            "93625531",
            "93961179",
        }

        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_search_payload([_summary("93872133", employer_id="999")]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="invalid job identity"):
                await discover(board, client)

    async def test_rejects_company_id_mismatch_even_when_organisation_matches(self):
        board = {
            "board_url": BOARD_URL,
            "metadata": {"organisation_id": ORGANISATION_ID},
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json=_search_payload([_summary("93872133", company_id="175608148114569")]),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="invalid job identity"):
                await discover(board, client)


class TestProbe:
    async def test_direct_url_is_detected_without_network(self):
        assert await can_handle(BOARD_URL) == {
            "host": "my.jobstreet.com",
            "company_id": COMPANY_ID,
        }

    async def test_singapore_url_uses_singapore_market_config(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                return httpx.Response(200, json=_company_payload(), request=request)
            assert request.url.params["siteKey"] == "sg"
            assert request.url.params["locale"] == "en-SG"
            return httpx.Response(200, json=_search_payload([]), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(SG_BOARD_URL, client) == {
                "host": "sg.jobstreet.com",
                "company_id": COMPANY_ID,
                "organisation_id": ORGANISATION_ID,
                "jobs": 0,
            }

    async def test_reachable_apis_add_verified_identity_and_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/graphql":
                return httpx.Response(200, json=_company_payload(), request=request)
            return httpx.Response(
                200,
                json=_search_payload([_summary("93872133"), _summary("93625531")]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(BOARD_URL, client) == {
                "host": "my.jobstreet.com",
                "company_id": COMPANY_ID,
                "organisation_id": ORGANISATION_ID,
                "jobs": 2,
            }


class TestScraper:
    @pytest.mark.parametrize(
        "url",
        [
            "https://my.jobstreet.com/job/93872133",
            "https://my.jobstreet.com/job/93872133/",
            "https://sg.jobstreet.com/job/93872133",
        ],
    )
    def test_extracts_job_identity(self, url: str):
        expected_host = "sg.jobstreet.com" if url.startswith("https://sg.") else "my.jobstreet.com"
        assert _job_identity_from_url(url) == (expected_host, "93872133")

    @pytest.mark.parametrize(
        "url",
        [
            "http://my.jobstreet.com/job/93872133",
            "https://id.jobstreet.com/job/93872133",
            "https://my.jobstreet.com/job/not-a-number",
            "https://my.jobstreet.com/job/93872133?tracking=x",
        ],
    )
    def test_rejects_untrusted_job_urls(self, url: str):
        assert _job_identity_from_url(url) is None

    def test_parses_complete_detail(self):
        result = parse_payload(
            _detail_payload("93872133")["data"],
            host="my.jobstreet.com",
            job_id="93872133",
        )

        assert result.title == "Role 93872133"
        assert "Complete responsibilities" in (result.description or "")
        assert result.locations == ["Simpang Ampat, Penang"]
        assert result.employment_type == "Full time"
        assert result.date_posted == "2026-08-10T05:56:10.450Z"
        assert result.language == "en"
        assert result.metadata == {
            "jobstreet_job_id": "93872133",
            "employer": "Tecan CDMO Solutions PN Sdn. Bhd.",
            "expiration_date": "2026-09-09T13:59:59.999Z",
        }

    def test_expired_job_returns_empty_content(self):
        result = parse_payload(
            _detail_payload("93872133", expired=True)["data"],
            host="my.jobstreet.com",
            job_id="93872133",
        )
        assert result == JobContent()

    async def test_scrape_uses_anonymous_graphql_detail(self):
        def handler(request: httpx.Request) -> httpx.Response:
            body = _request_json(request)
            assert request.url.path == "/graphql"
            assert body["variables"] == {"id": "93872133", "locale": "en-MY"}
            return httpx.Response(
                200,
                json=_detail_payload("93872133"),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://my.jobstreet.com/job/93872133",
                {},
                client,
            )
        assert "Complete responsibilities" in (result.description or "")

    async def test_graphql_errors_fail_closed(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={"errors": [{"message": "upstream failed"}]},
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="contains errors"):
                await scrape("https://my.jobstreet.com/job/93872133", {}, client)
