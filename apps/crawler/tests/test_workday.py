from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.workday import (
    _api_base,
    _api_detail_url,
    _api_list_url,
    _job_url,
    _parse_components,
    _parse_job,
    _parse_job_location_type,
    _pick_split_facet,
    can_handle,
    discover,
)


class TestParseComponents:
    def test_standard_url(self):
        result = _parse_components(
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
        )
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")

    def test_with_locale_prefix(self):
        result = _parse_components(
            "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite"
        )
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")

    def test_hyphenated_company(self):
        result = _parse_components(
            "https://my-company.wd1.myworkdayjobs.com/External"
        )
        assert result == ("my-company", "wd1", "External")

    def test_non_matching_url(self):
        assert _parse_components("https://example.com/careers") is None

    def test_with_trailing_slash(self):
        result = _parse_components(
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite/"
        )
        assert result == ("nvidia", "wd5", "NVIDIAExternalCareerSite")


class TestApiBase:
    def test_basic(self):
        result = _api_base("nvidia", "wd5")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia"


class TestApiListUrl:
    def test_basic(self):
        result = _api_list_url("nvidia", "wd5", "ExtSite")
        assert result == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/ExtSite/jobs"


class TestApiDetailUrl:
    def test_basic(self):
        result = _api_detail_url("nvidia", "wd5", "ExtSite", "/Senior-Engineer/JR001")
        assert (
            result
            == "https://nvidia.wd5.myworkdayjobs.com/wday/cxs/nvidia/ExtSite/job/Senior-Engineer/JR001"
        )


class TestJobUrl:
    def test_basic(self):
        result = _job_url("nvidia", "wd5", "ExtSite", "/Senior-Engineer/JR001")
        assert (
            result
            == "https://nvidia.wd5.myworkdayjobs.com/ExtSite/Senior-Engineer/JR001"
        )


class TestParseJobLocationType:
    def test_remote(self):
        assert _parse_job_location_type("Remote") == "remote"

    def test_flexible(self):
        assert _parse_job_location_type("Flexible") == "hybrid"

    def test_hybrid(self):
        assert _parse_job_location_type("Hybrid") == "hybrid"

    def test_none(self):
        assert _parse_job_location_type(None) is None

    def test_onsite(self):
        assert _parse_job_location_type("On-Site") is None

    def test_case_insensitive(self):
        assert _parse_job_location_type("REMOTE") == "remote"


class TestParseJob:
    def test_full_detail(self):
        detail = {
            "jobPostingInfo": {
                "title": "Senior Engineer",
                "externalPath": "/Senior-Engineer/JR001",
                "jobDescription": "<p>Build software</p>",
                "location": "Santa Clara, CA",
                "additionalLocations": ["Austin, TX", "Remote"],
                "timeType": "Full-time",
                "remoteType": "Hybrid",
                "startDate": "2024-01-15",
                "jobReqId": "JR001",
            }
        }
        result = _parse_job(detail, "nvidia", "wd5", "ExtSite")
        assert result is not None
        assert result.url == "https://nvidia.wd5.myworkdayjobs.com/ExtSite/Senior-Engineer/JR001"
        assert result.title == "Senior Engineer"
        assert result.description == "<p>Build software</p>"
        assert result.locations == ["Santa Clara, CA", "Austin, TX", "Remote"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "hybrid"
        assert result.date_posted == "2024-01-15"
        assert result.metadata == {"jobReqId": "JR001"}

    def test_missing_external_path_returns_none(self):
        detail = {"jobPostingInfo": {"title": "Test"}}
        assert _parse_job(detail, "nvidia", "wd5", "ExtSite") is None

    def test_missing_job_posting_info_returns_none(self):
        assert _parse_job({}, "nvidia", "wd5", "ExtSite") is None

    def test_external_url_override(self):
        detail = {
            "jobPostingInfo": {
                "externalPath": "/Engineer/JR002",
                "externalUrl": "https://custom.example.com/job/123",
            }
        }
        result = _parse_job(detail, "nvidia", "wd5", "ExtSite")
        assert result.url == "https://custom.example.com/job/123"

    def test_locations_dedup(self):
        detail = {
            "jobPostingInfo": {
                "externalPath": "/Job/JR003",
                "location": "NYC",
                "additionalLocations": ["NYC", "LA"],
            }
        }
        result = _parse_job(detail, "co", "wd1", "Site")
        assert result.locations == ["NYC", "LA"]

    def test_no_locations(self):
        detail = {"jobPostingInfo": {"externalPath": "/Job/JR004"}}
        result = _parse_job(detail, "co", "wd1", "Site")
        assert result.locations is None

    def test_no_metadata(self):
        detail = {"jobPostingInfo": {"externalPath": "/Job/JR005"}}
        result = _parse_job(detail, "co", "wd1", "Site")
        assert result.metadata is None


class TestPickSplitFacet:
    def test_picks_facet_with_most_values(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [
                    {"id": "cat1", "count": 500},
                    {"id": "cat2", "count": 300},
                    {"id": "cat3", "count": 200},
                ],
            },
            {
                "facetParameter": "location",
                "values": [
                    {"id": "loc1", "count": 900},
                    {"id": "loc2", "count": 100},
                ],
            },
        ]
        result = _pick_split_facet(facets)
        assert result is not None
        param, ids = result
        assert param == "category"
        assert ids == ["cat1", "cat2", "cat3"]

    def test_skips_facet_with_value_at_cap(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [
                    {"id": "cat1", "count": 2000},  # At cap
                    {"id": "cat2", "count": 100},
                ],
            },
            {
                "facetParameter": "location",
                "values": [
                    {"id": "loc1", "count": 900},
                ],
            },
        ]
        result = _pick_split_facet(facets)
        assert result is not None
        param, ids = result
        assert param == "location"

    def test_no_valid_facets(self):
        facets = [
            {
                "facetParameter": "category",
                "values": [{"id": "cat1", "count": 2000}],
            }
        ]
        assert _pick_split_facet(facets) is None

    def test_empty_facets(self):
        assert _pick_split_facet([]) is None

    def test_facet_without_values(self):
        facets = [{"facetParameter": "category", "values": []}]
        assert _pick_split_facet(facets) is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            url = str(request.url)
            if request.method == "POST" and "/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "total": 2,
                        "jobPostings": [
                            {"externalPath": "/Engineer/JR001"},
                            {"externalPath": "/Designer/JR002"},
                        ],
                        "facets": [],
                    },
                )
            if "/job/" in url and "JR001" in url:
                return httpx.Response(
                    200,
                    json={
                        "jobPostingInfo": {
                            "title": "Engineer",
                            "externalPath": "/Engineer/JR001",
                            "jobDescription": "<p>Build</p>",
                        }
                    },
                )
            if "/job/" in url and "JR002" in url:
                return httpx.Response(
                    200,
                    json={
                        "jobPostingInfo": {
                            "title": "Designer",
                            "externalPath": "/Designer/JR002",
                            "jobDescription": "<p>Design</p>",
                        }
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://nvidia.wd5.myworkdayjobs.com/ExtSite",
                "metadata": {
                    "company": "nvidia",
                    "wd_instance": "wd5",
                    "site": "ExtSite",
                },
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"total": 0, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://co.wd1.myworkdayjobs.com/Site",
                "metadata": {
                    "company": "co",
                    "wd_instance": "wd1",
                    "site": "Site",
                },
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_components_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot parse Workday"):
                await discover(board, client)

    async def test_components_from_url(self):
        def handler(request):
            url = str(request.url)
            assert "nvidia" in url
            return httpx.Response(
                200,
                json={"total": 0, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://nvidia.wd5.myworkdayjobs.com/ExtSite",
                "metadata": {},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0


class TestCanHandle:
    async def test_workday_url_match(self):
        result = await can_handle(
            "https://nvidia.wd5.myworkdayjobs.com/NVIDIAExternalCareerSite"
        )
        assert result is not None
        assert result["company"] == "nvidia"
        assert result["wd_instance"] == "wd5"
        assert result["site"] == "NVIDIAExternalCareerSite"

    async def test_non_matching_url(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_url_match_with_client(self):
        def handler(request):
            return httpx.Response(
                200,
                json={"total": 42, "jobPostings": [], "facets": []},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(
                "https://nvidia.wd5.myworkdayjobs.com/ExtSite", client
            )
            assert result is not None
            assert result["jobs"] == 42

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "myworkdayjobs.com" in url and "wday/cxs" in url:
                return httpx.Response(
                    200,
                    json={"total": 10, "jobPostings": [], "facets": []},
                )
            # Place the Workday URL at the end of the text so the regex's $ anchor works
            return httpx.Response(
                200,
                text='<html>Apply at https://acme.wd1.myworkdayjobs.com/Careers',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.acme.com/careers", client)
            assert result is not None
            assert result["company"] == "acme"
            assert result["site"] == "Careers"

    async def test_no_match_with_client(self):
        def handler(request):
            return httpx.Response(200, text="<html>no workday</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None
