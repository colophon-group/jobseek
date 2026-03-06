from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.dvinci import (
    _api_url,
    _board_url,
    _parse_job,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_standard_url(self):
        assert _slug_from_url("https://at-careers.dvinci-hr.com") == "at-careers"

    def test_with_path(self):
        assert _slug_from_url("https://inverto.dvinci-hr.com/career/list") == "inverto"

    def test_ignored_slugs(self):
        for slug in ("www", "static", "api", "cdn"):
            assert _slug_from_url(f"https://{slug}.dvinci-hr.com") is None

    def test_non_dvinci_url(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_empty_subdomain(self):
        assert _slug_from_url("https://dvinci-hr.com") is None


class TestApiUrl:
    def test_basic(self):
        assert _api_url("inverto") == "https://inverto.dvinci-hr.com/jobPublication/list.json"

    def test_with_hyphens(self):
        assert _api_url("at-careers") == "https://at-careers.dvinci-hr.com/jobPublication/list.json"


class TestBoardUrl:
    def test_basic(self):
        assert _board_url("inverto") == "https://inverto.dvinci-hr.com"


class TestParseJob:
    def test_basic(self):
        raw = {
            "jobPublicationURL": "https://inverto.dvinci-hr.com/career/detail/1",
            "position": "Consultant",
            "introduction": "<p>We are looking for</p>",
            "tasks": "<ul><li>Task 1</li></ul>",
        }
        result = _parse_job(raw)
        assert result is not None
        assert result.url == "https://inverto.dvinci-hr.com/career/detail/1"
        assert result.title == "Consultant"
        assert "<p>We are looking for</p>" in result.description
        assert "<ul><li>Task 1</li></ul>" in result.description

    def test_missing_url_returns_none(self):
        assert _parse_job({}) is None
        assert _parse_job({"position": "No URL"}) is None

    def test_all_description_sections(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "introduction": "intro",
            "tasks": "tasks",
            "profile": "profile",
            "weOffer": "offer",
            "closingText": "closing",
        }
        result = _parse_job(raw)
        assert result.description == "intro\ntasks\nprofile\noffer\nclosing"

    def test_no_description_sections(self):
        raw = {"jobPublicationURL": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.description is None

    def test_locations(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "locations": [{"name": "Munich"}, {"name": "Berlin"}],
            },
        }
        result = _parse_job(raw)
        assert result.locations == ["Munich", "Berlin"]

    def test_deduplicates_locations(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "locations": [{"name": "Munich"}, {"name": "Munich"}],
            },
        }
        result = _parse_job(raw)
        assert result.locations == ["Munich"]

    def test_no_locations(self):
        raw = {"jobPublicationURL": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.locations is None

    def test_employment_type_full_time(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "workingTimes": [{"internalName": "FULL_TIME"}],
            },
        }
        result = _parse_job(raw)
        assert result.employment_type == "full-time"

    def test_employment_type_part_time(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "workingTimes": [{"internalName": "PART_TIME"}],
            },
        }
        result = _parse_job(raw)
        assert result.employment_type == "part-time"

    def test_employment_type_unknown(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "workingTimes": [{"internalName": "OTHER"}],
            },
        }
        result = _parse_job(raw)
        assert result.employment_type is None

    def test_salary(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "salary": {
                    "currency": "EUR",
                    "value": {"minValue": 50000, "maxValue": 70000, "unitText": "year"},
                },
            },
        }
        result = _parse_job(raw)
        assert result.base_salary == {
            "currency": "EUR",
            "min": 50000,
            "max": 70000,
            "unit": "year",
        }

    def test_salary_monthly(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "salary": {
                    "currency": "EUR",
                    "value": {"minValue": 4000, "maxValue": None, "unitText": "month"},
                },
            },
        }
        result = _parse_job(raw)
        assert result.base_salary == {
            "currency": "EUR",
            "min": 4000,
            "max": None,
            "unit": "month",
        }

    def test_no_salary(self):
        raw = {"jobPublicationURL": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.base_salary is None

    def test_date_posted(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {"createdDate": "2024-06-15"},
        }
        result = _parse_job(raw)
        assert result.date_posted == "2024-06-15"

    def test_metadata_contract_period(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "contractPeriod": {"internalName": "PERMANENT"},
            },
        }
        result = _parse_job(raw)
        assert result.metadata["contract_period"] == "permanent"

    def test_metadata_reference(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {"reference": "REF-001"},
        }
        result = _parse_job(raw)
        assert result.metadata["reference"] == "REF-001"

    def test_metadata_categories(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {
                "categories": [{"name": "IT"}, {"name": "Engineering"}],
            },
        }
        result = _parse_job(raw)
        assert result.metadata["categories"] == ["IT", "Engineering"]

    def test_metadata_department(self):
        raw = {
            "jobPublicationURL": "https://example.com/job",
            "jobOpening": {"department": "Technology"},
        }
        result = _parse_job(raw)
        assert result.metadata["department"] == "Technology"

    def test_no_metadata(self):
        raw = {"jobPublicationURL": "https://example.com/job"}
        result = _parse_job(raw)
        assert result.metadata is None


class TestDiscover:
    async def test_returns_jobs(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "jobPublicationURL": "https://inverto.dvinci-hr.com/career/detail/1",
                        "position": "Consultant",
                        "introduction": "<p>Intro</p>",
                        "jobOpening": {"type": "EXTERNAL"},
                    },
                    {
                        "jobPublicationURL": "https://inverto.dvinci-hr.com/career/detail/2",
                        "position": "Analyst",
                        "tasks": "<p>Tasks</p>",
                        "jobOpening": {"type": "EXTERNAL"},
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://inverto.dvinci-hr.com",
                "metadata": {"slug": "inverto"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 2
            assert all(isinstance(j, DiscoveredJob) for j in jobs)

    async def test_empty_response(self):
        def handler(request):
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://inverto.dvinci-hr.com",
                "metadata": {"slug": "inverto"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {"board_url": "https://example.com/careers", "metadata": {}}
            with pytest.raises(ValueError, match="Cannot derive d.vinci slug"):
                await discover(board, client)

    async def test_slug_from_metadata(self):
        def handler(request):
            assert "inverto" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {"slug": "inverto"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_slug_from_board_url(self):
        def handler(request):
            assert "inverto" in str(request.url)
            return httpx.Response(200, json=[])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://inverto.dvinci-hr.com",
                "metadata": {},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 0

    async def test_skips_unsolicited(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {
                        "jobPublicationURL": "https://example.com/job/1",
                        "position": "Initiative",
                        "jobOpening": {"type": "UNSOLICITED"},
                    },
                    {
                        "jobPublicationURL": "https://example.com/job/2",
                        "position": "Real Job",
                        "jobOpening": {"type": "EXTERNAL"},
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://inverto.dvinci-hr.com",
                "metadata": {"slug": "inverto"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Real Job"

    async def test_skips_jobs_without_url(self):
        def handler(request):
            return httpx.Response(
                200,
                json=[
                    {"position": "No URL"},
                    {
                        "jobPublicationURL": "https://example.com/job",
                        "position": "Has URL",
                    },
                ],
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://inverto.dvinci-hr.com",
                "metadata": {"slug": "inverto"},
            }
            jobs = await discover(board, client)
            assert len(jobs) == 1
            assert jobs[0].title == "Has URL"

    async def test_http_error_raises(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://inverto.dvinci-hr.com",
                "metadata": {"slug": "inverto"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)


class TestCanHandle:
    async def test_dvinci_url_with_api(self):
        def handler(request):
            return httpx.Response(200, json=[{"id": 1}, {"id": 2}])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://inverto.dvinci-hr.com", client)
            assert result is not None
            assert result["slug"] == "inverto"
            assert result["jobs"] == 2

    async def test_dvinci_url_without_client(self):
        result = await can_handle("https://inverto.dvinci-hr.com")
        assert result == {"slug": "inverto"}

    async def test_dvinci_url_api_fails_still_returns(self):
        def handler(request):
            return httpx.Response(500)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://inverto.dvinci-hr.com", client)
            assert result == {"slug": "inverto"}

    async def test_non_dvinci_url_no_client(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_detects_in_page_html(self):
        def handler(request):
            url = str(request.url)
            if "dvinci-hr.com/jobPublication" in url:
                return httpx.Response(200, json=[{"id": 1}])
            return httpx.Response(
                200,
                text='<html><body ng-app="dvinci.apps.Dvinci">'
                '<script src="https://myco.dvinci-hr.com/assets/app.js"></script>'
                "</body></html>",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["slug"] == "myco"
            assert result["jobs"] == 1

    async def test_detects_dvinci_version_meta(self):
        def handler(request):
            url = str(request.url)
            if "dvinci-hr.com/jobPublication" in url:
                return httpx.Response(200, json=[])
            return httpx.Response(
                200,
                text='<html><head><meta name="dvinciVersion" content="3.0"></head>'
                '<body><iframe src="https://company.dvinci-hr.com/embed"></iframe>'
                "</body></html>",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is not None
            assert result["slug"] == "company"

    async def test_no_match(self):
        def handler(request):
            return httpx.Response(200, text="<html>no dvinci refs</html>")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None

    async def test_marker_found_but_ignored_slug(self):
        def handler(request):
            return httpx.Response(
                200,
                text='<html><body ng-app="dvinci.apps.Dvinci">'
                '<link href="https://static.dvinci-hr.com/style.css">'
                "</body></html>",
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.com/careers", client)
            assert result is None
