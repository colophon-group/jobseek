from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.workable import (
    _build_description,
    _build_locations,
    _parse_job,
    _parse_job_location_type,
    _token_from_url,
    can_handle,
    discover,
)


class TestTokenFromUrl:
    def test_standard_url(self):
        assert _token_from_url("https://apply.workable.com/acme-corp") == "acme-corp"

    def test_with_path(self):
        assert _token_from_url("https://apply.workable.com/acme-corp/j/ABC123") == "acme-corp"

    def test_ignored_token(self):
        assert _token_from_url("https://apply.workable.com/api") is None
        assert _token_from_url("https://apply.workable.com/accounts") is None
        assert _token_from_url("https://apply.workable.com/jobs") is None

    def test_non_matching_url(self):
        assert _token_from_url("https://example.com/careers") is None


class TestBuildDescription:
    def test_all_parts(self):
        detail = {
            "description": "<p>Desc</p>",
            "requirements": "<p>Reqs</p>",
            "benefits": "<p>Bens</p>",
        }
        result = _build_description(detail)
        assert "<p>Desc</p>" in result
        assert "<p>Reqs</p>" in result
        assert "<p>Bens</p>" in result

    def test_only_description(self):
        detail = {"description": "<p>Desc only</p>"}
        result = _build_description(detail)
        assert result == "<p>Desc only</p>"

    def test_empty(self):
        assert _build_description({}) is None

    def test_non_string_values_skipped(self):
        detail = {"description": 123, "requirements": "<p>Real</p>"}
        result = _build_description(detail)
        assert result == "<p>Real</p>"

    def test_concatenation_order(self):
        detail = {
            "description": "A",
            "requirements": "B",
            "benefits": "C",
        }
        result = _build_description(detail)
        assert result == "A\nB\nC"


class TestBuildLocations:
    def test_locations_array_of_dicts(self):
        detail = {
            "locations": [
                {"city": "NYC", "region": "NY", "country": "US"},
                {"city": "London", "country": "UK"},
            ]
        }
        result = _build_locations(detail)
        assert result == ["NYC, NY, US", "London, UK"]

    def test_dedup(self):
        detail = {
            "locations": [
                {"city": "NYC", "country": "US"},
                {"city": "NYC", "country": "US"},
            ]
        }
        result = _build_locations(detail)
        assert result == ["NYC, US"]

    def test_fallback_to_single_location_dict(self):
        detail = {
            "location": {"city": "Berlin", "region": "Berlin", "country": "Germany"},
        }
        result = _build_locations(detail)
        assert result == ["Berlin, Berlin, Germany"]

    def test_fallback_to_string_location(self):
        detail = {"location": "Remote"}
        result = _build_locations(detail)
        assert result == ["Remote"]

    def test_no_locations(self):
        assert _build_locations({}) is None

    def test_empty_locations_array(self):
        detail = {"locations": []}
        # Empty list is falsy, falls through to single location check
        assert _build_locations(detail) is None

    def test_locations_array_with_strings(self):
        detail = {"locations": ["NYC", "London"]}
        result = _build_locations(detail)
        assert result == ["NYC", "London"]

    def test_empty_single_location_dict(self):
        detail = {"location": {}}
        assert _build_locations(detail) is None

    def test_empty_string_location(self):
        detail = {"location": ""}
        assert _build_locations(detail) is None


class TestParseJobLocationType:
    def test_workplace_remote(self):
        assert _parse_job_location_type({"workplace": "remote"}) == "remote"

    def test_workplace_hybrid(self):
        assert _parse_job_location_type({"workplace": "hybrid"}) == "hybrid"

    def test_workplace_onsite(self):
        assert _parse_job_location_type({"workplace": "onsite"}) == "onsite"

    def test_workplace_on_site(self):
        assert _parse_job_location_type({"workplace": "on_site"}) == "onsite"

    def test_workplace_case_insensitive(self):
        assert _parse_job_location_type({"workplace": "Remote"}) == "remote"
        assert _parse_job_location_type({"workplace": "HYBRID"}) == "hybrid"

    def test_remote_flag_fallback(self):
        assert _parse_job_location_type({"remote": True}) == "remote"

    def test_no_workplace_no_remote(self):
        assert _parse_job_location_type({}) is None

    def test_remote_flag_false(self):
        assert _parse_job_location_type({"remote": False}) is None


class TestParseJob:
    def test_full_detail(self):
        detail = {
            "shortcode": "ABC123",
            "title": "Software Engineer",
            "description": "<p>Desc</p>",
            "requirements": "<p>Reqs</p>",
            "benefits": "<p>Bens</p>",
            "locations": [{"city": "NYC", "country": "US"}],
            "workplace": "hybrid",
            "type": "full",
            "published": "2024-01-15",
            "department": "Engineering",
        }
        result = _parse_job(detail, "acme")
        assert result is not None
        assert result.url == "https://apply.workable.com/acme/j/ABC123/"
        assert result.title == "Software Engineer"
        assert "<p>Desc</p>" in result.description
        assert result.locations == ["NYC, US"]
        assert result.job_location_type == "hybrid"
        assert result.employment_type == "Full-time"
        assert result.date_posted == "2024-01-15"
        assert result.metadata == {"department": "Engineering"}

    def test_missing_shortcode_returns_none(self):
        assert _parse_job({"title": "No shortcode"}, "acme") is None

    def test_employment_type_mapping(self):
        for raw, expected in [
            ("full", "Full-time"),
            ("part", "Part-time"),
            ("contract", "Contract"),
            ("internship", "Intern"),
        ]:
            result = _parse_job({"shortcode": "X", "type": raw}, "acme")
            assert result.employment_type == expected

    def test_unknown_employment_type_passthrough(self):
        result = _parse_job({"shortcode": "X", "type": "seasonal"}, "acme")
        assert result.employment_type == "seasonal"

    def test_department_string(self):
        result = _parse_job({"shortcode": "X", "department": "Sales"}, "acme")
        assert result.metadata == {"department": "Sales"}

    def test_department_list(self):
        result = _parse_job(
            {"shortcode": "X", "department": ["Eng", "Product"]}, "acme"
        )
        assert result.metadata == {"department": "Eng, Product"}

    def test_no_metadata(self):
        result = _parse_job({"shortcode": "X"}, "acme")
        assert result.metadata is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            url = str(request.url)
            method = request.method
            if method == "POST" and "/v3/" in url and "/jobs" in url:
                return httpx.Response(
                    200,
                    json={
                        "results": [
                            {"shortcode": "SC1"},
                            {"shortcode": "SC2"},
                        ],
                        "nextPage": None,
                    },
                )
            if method == "GET" and "/v2/" in url and "/SC1" in url:
                return httpx.Response(
                    200,
                    json={
                        "shortcode": "SC1",
                        "title": "Engineer",
                        "description": "<p>Build</p>",
                    },
                )
            if method == "GET" and "/v2/" in url and "/SC2" in url:
                return httpx.Response(
                    200,
                    json={
                        "shortcode": "SC2",
                        "title": "Designer",
                        "description": "<p>Design</p>",
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://apply.workable.com/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json={"results": [], "nextPage": None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://apply.workable.com/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_token_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive Workable"):
                await discover(board, client)

    async def test_token_from_metadata(self):
        def handler(request):
            assert "mytoken" in str(request.url)
            return httpx.Response(200, json={"results": [], "nextPage": None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"token": "mytoken"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_token_from_board_url(self):
        def handler(request):
            assert "testco" in str(request.url)
            return httpx.Response(200, json={"results": [], "nextPage": None})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://apply.workable.com/testco",
                "metadata": {},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_pagination(self):
        call_count = 0

        def handler(request):
            nonlocal call_count
            url = str(request.url)
            method = request.method
            if method == "POST" and "/v3/" in url:
                call_count += 1
                if call_count == 1:
                    return httpx.Response(
                        200,
                        json={
                            "results": [{"shortcode": "SC1"}],
                            "nextPage": "token123",
                        },
                    )
                else:
                    return httpx.Response(
                        200,
                        json={
                            "results": [{"shortcode": "SC2"}],
                            "nextPage": None,
                        },
                    )
            # Detail endpoints
            if method == "GET" and "/v2/" in url:
                return httpx.Response(
                    200,
                    json={
                        "shortcode": "SC1" if "SC1" in url else "SC2",
                        "title": "Job",
                        "description": "<p>D</p>",
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://apply.workable.com/testco",
                "metadata": {"token": "testco"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert call_count == 2


class TestCanHandle:
    async def test_workable_url_match(self):
        result = await can_handle("https://apply.workable.com/acme-corp")
        assert result is not None
        assert result["token"] == "acme-corp"

    async def test_non_matching_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_url_match_with_client(self):
        def handler(request):
            return httpx.Response(200, json={"total": 25, "results": []})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://apply.workable.com/acme", client)
            assert result is not None
            assert result["token"] == "acme"
            assert result["jobs"] == 25

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "apply.workable.com/api" in url:
                return httpx.Response(200, json={"total": 10, "results": []})
            return httpx.Response(
                200,
                text='<html><a href="https://apply.workable.com/myco/j/ABC">Apply</a></html>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is not None
            assert result["token"] == "myco"

    async def test_no_match(self):
        def handler(request):
            url = str(request.url)
            if "apply.workable.com" in url:
                return httpx.Response(404)
            return httpx.Response(200, text="<html>no workable refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.example.com/careers", client)
            assert result is None
