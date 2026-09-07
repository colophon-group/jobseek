"""Tests for the api_sniffer scraper."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import structlog
from structlog.testing import capture_logs

from src.core.scrapers.api_sniffer import (
    _ajinga_http_config,
    _extract_from_object,
    _extract_heuristic,
    _find_single_job,
    _score_job_object,
    _scrape_http,
    _seek_http_config,
    probe_pw,
)
from src.shared.api_sniff import Exchange
from src.shared.http_retry import PaginationFetchError


def _make_exchange(url="https://example.com/api/job", body=None, phase="load"):
    return Exchange(
        method="GET",
        url=url,
        request_headers={},
        post_data=None,
        status=200,
        body=body,
        content_type="application/json",
        phase=phase,
    )


class TestScoreJobObject:
    def test_good_job_object(self):
        obj = {
            "title": "Software Engineer",
            "description": "A " * 30 + "long description",
            "location": "NYC",
            "department": "Engineering",
            "id": "123",
        }
        score = _score_job_object(obj)
        assert score >= 30  # title(10) + description(20) + location(5) + keys(5)

    def test_no_title_returns_zero(self):
        obj = {"description": "Some text", "location": "NYC"}
        assert _score_job_object(obj) == 0

    def test_short_description(self):
        obj = {"title": "Dev", "description": "Short"}
        score = _score_job_object(obj)
        assert score == 10  # Only title, description too short


class TestFindSingleJob:
    def test_finds_top_level(self):
        body = {
            "title": "Developer",
            "description": "A " * 30 + "long description",
            "location": "NYC",
            "id": "123",
            "department": "Eng",
        }
        ex = _make_exchange(body=body)
        result = _find_single_job([ex])
        assert result is not None
        assert result["title"] == "Developer"

    def test_finds_nested(self):
        body = {
            "data": {
                "title": "PM",
                "description": "A " * 30 + "long desc",
                "location": "SF",
                "id": "456",
                "team": "Product",
            }
        }
        ex = _make_exchange(body=body)
        result = _find_single_job([ex])
        assert result is not None
        assert result["title"] == "PM"

    def test_finds_fountain_funnel(self):
        body = {
            "account": {"name": "Albertsons Companies"},
            "funnel": {
                "title": "Pharmacy Technician Assistant",
                "position_description_html": "<p>" + "A " * 30 + "</p>",
                "location": {"name": "Walla Walla, WA"},
                "job_hours": "part_time",
                "job_type": "permanent",
            },
        }
        ex = _make_exchange(body=body)

        result = _find_single_job([ex])

        assert result is not None
        assert result["title"] == "Pharmacy Technician Assistant"

    def test_returns_none_no_job(self):
        body = {"config": {"theme": "dark"}}
        ex = _make_exchange(body=body)
        result = _find_single_job([ex])
        assert result is None

    def test_best_score_wins(self):
        body_weak = {"title": "X"}
        body_strong = {
            "title": "Developer",
            "description": "A " * 30 + "rich HTML content here",
            "location": "NYC",
            "id": "1",
            "dept": "Eng",
        }
        ex1 = _make_exchange(url="https://example.com/a", body=body_weak)
        ex2 = _make_exchange(url="https://example.com/b", body=body_strong)
        result = _find_single_job([ex1, ex2])
        assert result is not None
        assert result["title"] == "Developer"


class TestExtractHeuristic:
    def test_all_fields(self):
        obj = {
            "title": "Dev",
            "description": "HTML content",
            "location": "NYC",
            "employmentType": "Full-time",
            "datePosted": "2024-01-15",
            "workplaceType": "remote",
        }
        content = _extract_heuristic(obj)
        assert content.title == "Dev"
        assert content.description == "HTML content"
        assert content.locations == ["NYC"]
        assert content.employment_type == "Full-time"
        assert content.date_posted == "2024-01-15"
        assert content.job_location_type == "remote"

    def test_locations_array_of_strings(self):
        obj = {"title": "Dev", "locations": ["NYC", "SF"]}
        content = _extract_heuristic(obj)
        assert content.locations == ["NYC", "SF"]

    def test_locations_array_of_objects(self):
        obj = {
            "title": "Dev",
            "locations": [{"name": "NYC"}, {"name": "SF"}],
        }
        content = _extract_heuristic(obj)
        assert content.locations == ["NYC", "SF"]

    def test_empty_object(self):
        content = _extract_heuristic({})
        assert content.title is None
        assert content.description is None

    def test_fountain_position_description(self):
        content = _extract_heuristic(
            {
                "title": "Pharmacy Technician Assistant",
                "position_description_html": "<p>Assist pharmacy patients.</p>",
            }
        )

        assert content.description == "<p>Assist pharmacy patients.</p>"


class TestExtractFromObject:
    def test_with_explicit_mapping(self):
        obj = {
            "jobTitle": "Engineer",
            "bodyHtml": "<p>Job desc</p>",
            "offices": [{"name": "NYC"}, {"name": "LA"}],
        }
        config = {
            "fields": {
                "title": "jobTitle",
                "description": "bodyHtml",
                "locations": "offices[].name",
            }
        }
        content = _extract_from_object(obj, config)
        assert content.title == "Engineer"
        assert content.description == "<p>Job desc</p>"
        assert content.locations == ["NYC", "LA"]

    def test_without_mapping_uses_heuristic(self):
        obj = {"title": "Dev", "description": "HTML content"}
        content = _extract_from_object(obj, {})
        assert content.title == "Dev"
        assert content.description == "HTML content"

    def test_metadata_fields(self):
        obj = {"title": "Dev", "url": "/jobs/1", "department": "Eng"}
        config = {"fields": {"title": "title", "metadata.team": "department"}}
        content = _extract_from_object(obj, config)
        assert content.title == "Dev"
        assert content.metadata == {"team": "Eng"}


class TestScrapeHttpPlaceholders:
    @pytest.mark.asyncio
    async def test_url_pattern_extracts_id_from_query_parameter(self):
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            api_calls.append(str(request.url))
            return httpx.Response(
                200,
                json={
                    "requisitionTitle": "WMS Clerk",
                    "requisitionDescription": "<p>Manage warehouse inventory.</p>",
                    "requisitionLocations": [{"nameCode": {"shortName": "Wilmer, TX, US"}}],
                },
            )

        config = {
            "api_url": "https://api.example.com/job-requisitions/{id}",
            "url_pattern": r"[?&]jobId=(?P<id>[^&]+)",
            "fields": {
                "title": "requisitionTitle",
                "description": "requisitionDescription",
                "locations": "requisitionLocations[].nameCode.shortName",
            },
        }
        job_url = (
            "https://jobs.example.com/recruitment.html?cid=company"
            "&jobId=554734&jwId=9201178824629_1"
        )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            content = await _scrape_http(job_url, config, http)

        assert api_calls == ["https://api.example.com/job-requisitions/554734"]
        assert content.title == "WMS Clerk"
        assert content.description == "<p>Manage warehouse inventory.</p>"
        assert content.locations == ["Wilmer, TX, US"]

    @pytest.mark.asyncio
    async def test_auth_request_injects_fresh_detail_headers(self):
        requests: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request.url.path)
            if request.url.path == "/auth":
                assert request.headers["authorization"] == "Basic public-portal"
                return httpx.Response(
                    200,
                    json={"token": "fresh-token", "portalID": 198, "a": "tenant"},
                )
            assert request.headers["token"] == "fresh-token"
            assert request.headers["portalid"] == "198"
            assert request.headers["a"] == "tenant"
            return httpx.Response(
                200,
                json={
                    "job": {
                        "title": "Project Manager",
                        "jobDescription": "<p>Lead a complex delivery program.</p>",
                        "location": "New York, NY",
                    }
                },
            )

        config = {
            "api_url": "https://api.example.com/jobs/{id}",
            "url_pattern": r"[?&]id=(?P<id>\d+)",
            "json_path": "job",
            "auth_request": {
                "api_url": "https://api.example.com/auth",
                "request_headers": {"authorization": "Basic public-portal"},
                "header_fields": {
                    "token": "token",
                    "portalid": "portalID",
                    "a": "a",
                },
            },
            "fields": {
                "title": "title",
                "description": "jobDescription",
                "locations": "location",
            },
        }

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
            content = await _scrape_http(
                "https://jobs.example.com/portal/?id=29179927",
                config,
                http,
            )

        assert requests == ["/auth", "/jobs/29179927"]
        assert content.title == "Project Manager"
        assert content.description == "<p>Lead a complex delivery program.</p>"
        assert content.locations == ["New York, NY"]


class TestSeekHttpPreset:
    def test_recognizes_canonical_au_job_urls(self):
        config = _seek_http_config(
            [
                "https://au.seek.com/job/93985149?cid=company-profile",
                "https://www.seek.com.au/job/93985150",
            ]
        )

        assert config is not None
        assert config["api_url"] == "https://au.seek.com/graphql"
        assert config["method"] == "POST"
        assert config["json_path"] == "data.jobDetails.job"
        assert config["fields"]["description"] == "content"
        assert config["fields"]["locations"] == "location.label"
        assert '"id":"{id}"' in config["post_body"]
        assert 'locale: \\"en-AU\\"' in config["post_body"]

    @pytest.mark.parametrize(
        "urls",
        [
            ["https://example.com/job/93985149"],
            ["https://au.seek.com/jobs/93985149"],
            ["https://au.seek.com/job/not-a-number"],
            ["http://au.seek.com/job/93985149"],
            [
                "https://au.seek.com/job/93985149",
                "https://nz.seek.com/job/93985150",
            ],
        ],
    )
    def test_rejects_noncanonical_or_mixed_market_urls(self, urls):
        assert _seek_http_config(urls) is None

    @pytest.mark.asyncio
    async def test_probe_uses_graphql_without_detail_page_navigation(self):
        requested_ids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = __import__("json").loads(request.content)
            job_id = body["variables"]["id"]
            requested_ids.append(job_id)
            return httpx.Response(
                200,
                json={
                    "data": {
                        "jobDetails": {
                            "job": {
                                "id": job_id,
                                "title": f"Aircraft Engineer {job_id}",
                                "content": (
                                    "<p>Maintain Pilatus aircraft safely.</p>"
                                    if job_id == "93985149"
                                    else None
                                ),
                                "abstract": "Maintain aircraft.",
                                "location": (
                                    {"label": "Adelaide SA"} if job_id == "93985149" else None
                                ),
                                "advertiser": {
                                    "id": "34477850",
                                    "name": "Pilatus Aircraft Australia Pty Ltd",
                                },
                                "workTypes": {"label": "Full time"},
                                "createdAt": {"dateTimeUtc": "2026-08-14T07:46:34Z"},
                                "expiresAt": {"dateTimeUtc": "2026-09-13T13:59:59Z"},
                                "isExpired": False,
                                "status": "Active",
                            }
                        }
                    }
                },
            )

        def make_client():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        urls = [
            "https://au.seek.com/job/93985149?cid=company-profile",
            "https://au.seek.com/job/93985150?cid=company-profile",
        ]
        with patch("src.shared.http.create_http_client", side_effect=make_client):
            metadata, comment = await probe_pw(urls, MagicMock())

        assert requested_ids == ["93985149", "93985150"]
        assert metadata is not None
        assert metadata["total"] == 1
        assert metadata["titles"] == 1
        assert metadata["descriptions"] == 1
        assert metadata["locations"] == 1
        assert metadata["config"]["api_url"] == "https://au.seek.com/graphql"
        assert "SEEK GraphQL" in comment


class TestAjingaHttpPreset:
    def test_recognizes_canonical_short_id_job_urls(self):
        config = _ajinga_http_config(
            [
                "https://www.ajinga.com/job-description/bfS0LMmSO",
                "https://ajinga.com/job-description/aB_123-cD/",
            ]
        )

        assert config is not None
        assert config["api_url"] == ("https://www.ajinga.com/django_rest/job-detail/info/{id}/")
        assert config["method"] == "GET"
        assert config["json_path"] == "data.data.job"
        assert config["fields"]["description"] == "description || cn_description"
        assert config["fields"]["locations"] == "cities[].name"

    @pytest.mark.parametrize(
        "urls",
        [
            ["https://example.com/job-description/bfS0LMmSO"],
            ["https://www.ajinga.com/job-detail-new/214993/c/"],
            ["https://www.ajinga.com/job-description/short"],
            ["http://www.ajinga.com/job-description/bfS0LMmSO"],
        ],
    )
    def test_rejects_noncanonical_urls(self, urls):
        assert _ajinga_http_config(urls) is None

    @pytest.mark.asyncio
    async def test_probe_uses_detail_api_without_page_navigation(self):
        requested_ids: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            job_id = request.url.path.rstrip("/").rsplit("/", 1)[-1]
            requested_ids.append(job_id)
            return httpx.Response(
                200,
                json={
                    "message": "",
                    "code": 200,
                    "data": {
                        "data": {
                            "job": {
                                "id": 214993,
                                "short_unique_id": job_id,
                                "en_title": "Lead Mapping Programmer",
                                "cn_title": "Lead Mapping Programmer",
                                "description": "<p>Build and validate clinical data maps.</p>",
                                "cn_description": None,
                                "cities": [{"name": "北京市"}],
                                "role_type": "F",
                                "company": {"i18n_name": "研发中心"},
                                "experience": "-1",
                                "updated_time": "2026-08-20 22:16:24",
                            }
                        }
                    },
                },
            )

        def make_client():
            return httpx.AsyncClient(transport=httpx.MockTransport(handler))

        urls = [
            "https://www.ajinga.com/job-description/bfS0LMmSO",
            "https://www.ajinga.com/job-description/aB_123-cD/",
        ]
        with patch("src.shared.http.create_http_client", side_effect=make_client):
            metadata, comment = await probe_pw(urls, MagicMock())

        assert requested_ids == ["bfS0LMmSO", "aB_123-cD"]
        assert metadata is not None
        assert metadata["total"] == 2
        assert metadata["titles"] == 2
        assert metadata["descriptions"] == 2
        assert metadata["locations"] == 2
        assert metadata["config"]["json_path"] == "data.data.job"
        assert "Ajinga detail API" in comment


class TestProbePw:
    async def test_detects_job_data(self):
        """probe_pw detects single-job XHR responses and returns metadata."""
        job_body = {
            "title": "Software Engineer",
            "description": "A " * 30 + "long description here",
            "location": "NYC",
            "department": "Engineering",
            "id": "123",
        }

        exchange = _make_exchange(body=job_body)

        async def fake_capture(page, host):
            return [exchange]

        async def fake_navigate(page, url, opts):
            pass

        # Mock open_page as an async context manager
        mock_page = MagicMock()
        mock_open_page = MagicMock()
        mock_open_page.return_value.__aenter__ = AsyncMock(return_value=mock_page)
        mock_open_page.return_value.__aexit__ = AsyncMock(return_value=False)

        pw = MagicMock()

        with (
            patch("src.shared.browser.open_page", mock_open_page),
            patch("src.core.scrapers.api_sniffer.capture_exchanges", fake_capture),
            patch("src.shared.browser.navigate", fake_navigate),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            metadata, comment = await probe_pw(
                ["https://example.com/job/1", "https://example.com/job/2"],
                pw,
            )

        assert metadata is not None
        assert metadata["titles"] == 2
        assert metadata["descriptions"] == 2
        assert metadata["total"] == 2
        assert "config" in metadata
        assert "fields" in metadata["config"]
        assert "titles" in comment

    async def test_no_data_returns_none(self):
        """probe_pw returns None when no XHR job data found."""
        exchange = _make_exchange(body={"config": {"theme": "dark"}})

        async def fake_capture(page, host):
            return [exchange]

        async def fake_navigate(page, url, opts):
            pass

        mock_page = MagicMock()
        mock_open_page = MagicMock()
        mock_open_page.return_value.__aenter__ = AsyncMock(return_value=mock_page)
        mock_open_page.return_value.__aexit__ = AsyncMock(return_value=False)

        pw = MagicMock()

        with (
            patch("src.shared.browser.open_page", mock_open_page),
            patch("src.core.scrapers.api_sniffer.capture_exchanges", fake_capture),
            patch("src.shared.browser.navigate", fake_navigate),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            metadata, comment = await probe_pw(
                ["https://example.com/job/1"],
                pw,
            )

        assert metadata is None
        assert "Not detected" in comment

    async def test_below_threshold_returns_none(self):
        """probe_pw returns None when < 50% of pages have job data."""
        job_body = {
            "title": "Engineer",
            "description": "A " * 30 + "long description",
            "location": "NYC",
            "id": "1",
            "dept": "Eng",
        }
        no_job_body = {"settings": {"locale": "en"}}

        call_count = 0

        async def fake_capture(page, host):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return [_make_exchange(body=job_body)]
            return [_make_exchange(body=no_job_body)]

        async def fake_navigate(page, url, opts):
            pass

        mock_page = MagicMock()
        mock_open_page = MagicMock()
        mock_open_page.return_value.__aenter__ = AsyncMock(return_value=mock_page)
        mock_open_page.return_value.__aexit__ = AsyncMock(return_value=False)

        pw = MagicMock()

        with (
            patch("src.shared.browser.open_page", mock_open_page),
            patch("src.core.scrapers.api_sniffer.capture_exchanges", fake_capture),
            patch("src.shared.browser.navigate", fake_navigate),
            patch("asyncio.sleep", new_callable=AsyncMock),
        ):
            metadata, comment = await probe_pw(
                [
                    "https://example.com/job/1",
                    "https://example.com/job/2",
                    "https://example.com/job/3",
                ],
                pw,
            )

        assert metadata is None
        assert "1/3" in comment

    async def test_navigation_http_status_error_propagates(self):
        from src.shared.browser import BrowserNavigationHTTPStatusError

        error = BrowserNavigationHTTPStatusError(
            requested_url="https://example.com/job/1",
            response_url="https://example.com/error",
            status=503,
            phase="primary",
        )

        async def fake_capture(page, host):
            return []

        async def fake_navigate(page, url, opts):
            raise error

        mock_open_page = MagicMock()
        mock_open_page.return_value.__aenter__ = AsyncMock(return_value=MagicMock())
        mock_open_page.return_value.__aexit__ = AsyncMock(return_value=False)

        with (
            patch("src.shared.browser.open_page", mock_open_page),
            patch("src.core.scrapers.api_sniffer.capture_exchanges", fake_capture),
            patch("src.shared.browser.navigate", fake_navigate),
            pytest.raises(BrowserNavigationHTTPStatusError) as exc_info,
        ):
            await probe_pw(["https://example.com/job/1"], MagicMock())

        assert exc_info.value is error


class TestScrapeHttpEmptyItems:
    """Pin the INFO/WARN split in _scrape_http (#2227)."""

    @staticmethod
    def _patched_fetch(body):
        """Patch the strict fetcher to return *body* regardless of input."""
        return patch(
            "src.core.monitors.api_sniffer.http_fetch_with_retry",
            new=AsyncMock(return_value=body),
        )

    @pytest.mark.asyncio
    async def test_empty_items_logs_info_empty_result(self, caplog):
        """`items: []` + `json_path: items[0]` → None → INFO empty_result."""
        structlog.configure(
            processors=[structlog.stdlib.add_log_level, structlog.processors.JSONRenderer()],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            logger_factory=structlog.stdlib.LoggerFactory(),
        )
        caplog.set_level(logging.DEBUG)

        cfg = {"api_url": "https://x/api", "json_path": "items[0]", "fields": {}}
        async with httpx.AsyncClient() as http:
            with self._patched_fetch({"items": []}):
                result = await _scrape_http("https://x/job/1", cfg, http)

        assert result.title is None
        records = [r for r in caplog.records if "empty_result" in r.getMessage()]
        assert records, "expected api_sniffer_scraper.empty_result log"
        assert records[0].levelname == "INFO"
        warn_records = [r for r in caplog.records if "no_job_data" in r.getMessage()]
        assert not warn_records, "empty items should NOT emit no_job_data warning"

    @pytest.mark.asyncio
    async def test_unexpected_shape_logs_warning_no_job_data(self, caplog):
        """`items: [{...}]` but `json_path: items[0].broken` → something non-dict → WARN."""
        structlog.configure(
            processors=[structlog.stdlib.add_log_level, structlog.processors.JSONRenderer()],
            wrapper_class=structlog.make_filtering_bound_logger(logging.DEBUG),
            logger_factory=structlog.stdlib.LoggerFactory(),
        )
        caplog.set_level(logging.DEBUG)

        # data resolved via json_path is a string (non-dict, non-None) → WARN
        cfg = {"api_url": "https://x/api", "json_path": "items[0].name", "fields": {}}
        async with httpx.AsyncClient() as http:
            with self._patched_fetch({"items": [{"name": "plain-string"}]}):
                await _scrape_http("https://x/job/1", cfg, http)

        info_records = [r for r in caplog.records if "empty_result" in r.getMessage()]
        assert not info_records, "unexpected shape should NOT emit empty_result info"
        warn_records = [r for r in caplog.records if "no_job_data" in r.getMessage()]
        assert warn_records, "expected api_sniffer_scraper.no_job_data log"
        assert warn_records[0].levelname == "WARNING"

    @pytest.mark.asyncio
    async def test_fetch_failure_propagates_with_stable_context(self):
        """A failed detail fetch is retryable pipeline failure, not empty success."""
        error = PaginationFetchError(
            "https://x/api",
            attempts=3,
            last_status=502,
        )
        cfg = {"api_url": "https://x/api", "fields": {}}

        async with httpx.AsyncClient() as http:
            with (
                patch(
                    "src.core.monitors.api_sniffer.http_fetch_with_retry",
                    new=AsyncMock(side_effect=error),
                ),
                capture_logs() as logs,
                pytest.raises(PaginationFetchError) as exc_info,
            ):
                await _scrape_http("https://x/job/1", cfg, http)

        assert exc_info.value is error
        failed = next(
            log for log in logs if log["event"] == "api_sniffer_scraper.http_fetch_failed"
        )
        assert failed["error_class"] == "PaginationFetchError"
        assert failed["attempts"] == 3
        assert failed["last_status"] == 502
        assert failed["last_error"] is None
