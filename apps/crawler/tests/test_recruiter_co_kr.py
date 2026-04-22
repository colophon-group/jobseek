from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.recruiter_co_kr import (
    _api_headers,
    _board_url,
    _dt_date,
    _job_url,
    _parse_detail,
    _parse_list_item,
    _slug_from_url,
    can_handle,
    discover,
)


class TestSlugFromUrl:
    def test_standard_home_url(self):
        assert _slug_from_url("https://mcdonalds.recruiter.co.kr/career/home") == "mcdonalds"

    def test_with_hyphens(self):
        assert _slug_from_url("https://tel.recruiter.co.kr/career/apply") == "tel"

    def test_hyphenated_slug(self):
        assert (
            _slug_from_url("https://tokyo-electron.recruiter.co.kr/career/home") == "tokyo-electron"
        )

    def test_ignored_slugs(self):
        for slug in ("www", "api", "api-recruiter", "infra1-static", "cdn"):
            assert _slug_from_url(f"https://{slug}.recruiter.co.kr") is None

    def test_unrelated_domain(self):
        assert _slug_from_url("https://example.com/careers") is None

    def test_apex_domain_only(self):
        assert _slug_from_url("https://recruiter.co.kr") is None

    def test_deep_subdomain_rejected(self):
        # Slugs containing a dot are rejected to avoid accidentally
        # matching *.*.recruiter.co.kr.
        assert _slug_from_url("https://foo.bar.recruiter.co.kr/career/home") is None


class TestHelpers:
    def test_board_url(self):
        assert _board_url("mcdonalds") == "https://mcdonalds.recruiter.co.kr/career/home"

    def test_job_url(self):
        assert (
            _job_url("mcdonalds", 107659) == "https://mcdonalds.recruiter.co.kr/career/jobs/107659"
        )

    def test_api_headers_uses_tenant_prefix(self):
        headers = _api_headers("mcdonalds")
        assert headers["prefix"] == "mcdonalds.recruiter.co.kr"
        assert "application/json" in headers["accept"]
        assert headers["content-type"] == "application/json"

    def test_dt_date_strips_time(self):
        assert _dt_date("2026-04-22T00:00:00") == "2026-04-22"
        assert _dt_date("2026-04-22") == "2026-04-22"
        assert _dt_date(None) is None
        assert _dt_date("") is None


class TestParseListItem:
    def test_basic(self):
        item = {
            "positionSn": 107659,
            "title": "Counsel",
            "startDateTime": "2026-04-22T00:00:00",
            "careerType": "CAREER",
            "classificationCode": "본사",
            "tagList": [{"tagSn": 1, "tagName": "본사"}],
            "openStatus": "OPEN",
            "submissionStatus": "IN_SUBMISSION",
        }
        summary = _parse_list_item(item, "mcdonalds")
        assert summary is not None
        assert summary["positionSn"] == 107659
        assert summary["url"] == ("https://mcdonalds.recruiter.co.kr/career/jobs/107659")
        assert summary["list_title"] == "Counsel"

    def test_missing_position_sn_returns_none(self):
        assert _parse_list_item({"title": "No SN"}, "mcdonalds") is None


class TestParseDetail:
    def _summary(self, **overrides):
        base = {
            "positionSn": 100,
            "url": "https://mcdonalds.recruiter.co.kr/career/jobs/100",
            "list_title": "Sample",
            "startDateTime": "2026-01-15T00:00:00",
            "careerType": "CAREER",
            "classificationCode": "본사",
            "tagList": [],
        }
        base.update(overrides)
        return base

    def test_basic_detail(self):
        detail = {
            "title": "Manager",
            "jobDescription": "<p>Job desc</p>",
            "jobDescriptionType": "HTML",
            "careerType": "CAREER",
            "startDateTime": "2026-02-01T00:00:00",
            "endDateTime": "2026-03-01T00:00:00",
            "tagList": [{"tagName": "경영기획"}, {"tagName": "본사"}],
            "classificationCode": "본사",
            "announcementType": "NORMAL",
            "recruitmentType": "GENERAL",
        }
        job = _parse_detail(detail, self._summary(), "mcdonalds")
        assert isinstance(job, DiscoveredJob)
        assert job.url == ("https://mcdonalds.recruiter.co.kr/career/jobs/100")
        assert job.title == "Manager"
        assert job.description == "<p>Job desc</p>"
        assert job.employment_type == "Full-time"
        assert job.date_posted == "2026-02-01"
        assert job.language == "ko"
        assert job.metadata["tags"] == ["경영기획", "본사"]
        assert job.metadata["classification"] == "본사"
        assert job.metadata["valid_through"] == "2026-03-01"
        assert job.metadata["announcement_type"] == "NORMAL"
        assert job.metadata["recruitment_type"] == "GENERAL"

    def test_empty_detail_falls_back_to_summary(self):
        detail: dict = {}
        job = _parse_detail(detail, self._summary(list_title="FallbackTitle"), "x")
        assert job is not None
        assert job.title == "FallbackTitle"
        assert job.date_posted == "2026-01-15"
        # No description available — still returns a URL-bearing record.
        assert job.description is None

    def test_intern_career_type(self):
        detail = {"title": "Intern", "careerType": "INTERN"}
        job = _parse_detail(detail, self._summary(), "x")
        assert job.employment_type == "Intern"

    def test_unknown_career_type(self):
        detail = {"title": "X", "careerType": "SOMETHING_NEW"}
        job = _parse_detail(detail, self._summary(), "x")
        assert job.employment_type is None

    def test_missing_title_returns_none(self):
        job = _parse_detail({}, self._summary(list_title=None), "x")
        assert job is None

    def test_non_html_description_wrapped(self):
        detail = {
            "title": "Plain",
            "jobDescription": "Plain text description",
            "jobDescriptionType": "TEXT",
        }
        job = _parse_detail(detail, self._summary(), "x")
        assert job.description == "<pre>Plain text description</pre>"


class TestDiscover:
    def _make_transport(self, pages, details):
        """Build a MockTransport with paginated list + detail responses."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/position/v1/jobflex":
                # Need to detect page from body
                import json as _json

                body = _json.loads(request.content)
                page = body["pageableRq"]["page"]
                return httpx.Response(200, json=pages[page - 1])
            if request.url.path.startswith("/position/v2/jobflex/"):
                sn = request.url.path.rsplit("/", 1)[-1]
                return httpx.Response(200, json=details.get(sn, {}))
            if request.url.path == "/design/v2":
                return httpx.Response(200, json={"title": "X"})
            return httpx.Response(404)

        return httpx.MockTransport(handler)

    async def test_fetches_and_merges_detail(self):
        pages = [
            {
                "pagination": {"page": 1, "size": 100, "totalCount": 2, "totalPages": 1},
                "list": [
                    {"positionSn": 1, "title": "List1", "careerType": "CAREER"},
                    {"positionSn": 2, "title": "List2", "careerType": "INTERN"},
                ],
            }
        ]
        details = {
            "1": {
                "title": "Detail1",
                "jobDescription": "<p>First</p>",
                "jobDescriptionType": "HTML",
                "careerType": "CAREER",
                "startDateTime": "2026-03-01T00:00:00",
            },
            "2": {
                "title": "Detail2",
                "jobDescription": "<p>Second</p>",
                "jobDescriptionType": "HTML",
                "careerType": "INTERN",
                "startDateTime": "2026-03-02T00:00:00",
            },
        }
        async with httpx.AsyncClient(transport=self._make_transport(pages, details)) as client:
            board = {
                "board_url": "https://mcdonalds.recruiter.co.kr/career/home",
                "metadata": {"slug": "mcdonalds"},
            }
            jobs = await discover(board, client)
        assert len(jobs) == 2
        titles = sorted(j.title for j in jobs)
        assert titles == ["Detail1", "Detail2"]
        by_url = {j.url: j for j in jobs}
        assert "https://mcdonalds.recruiter.co.kr/career/jobs/1" in by_url
        assert (
            by_url["https://mcdonalds.recruiter.co.kr/career/jobs/1"].description == "<p>First</p>"
        )
        assert by_url["https://mcdonalds.recruiter.co.kr/career/jobs/2"].employment_type == "Intern"

    async def test_paginates_until_total_pages(self):
        pages = [
            {
                "pagination": {"page": 1, "size": 100, "totalCount": 3, "totalPages": 2},
                "list": [
                    {"positionSn": 1, "title": "a", "careerType": "CAREER"},
                    {"positionSn": 2, "title": "b", "careerType": "CAREER"},
                ],
            },
            {
                "pagination": {"page": 2, "size": 100, "totalCount": 3, "totalPages": 2},
                "list": [{"positionSn": 3, "title": "c", "careerType": "CAREER"}],
            },
        ]
        details = {str(i): {"title": f"D{i}", "careerType": "CAREER"} for i in (1, 2, 3)}
        async with httpx.AsyncClient(transport=self._make_transport(pages, details)) as client:
            board = {
                "board_url": "https://mcdonalds.recruiter.co.kr/career/home",
                "metadata": {"slug": "mcdonalds"},
            }
            jobs = await discover(board, client)
        assert {j.title for j in jobs} == {"D1", "D2", "D3"}

    async def test_detail_404_falls_back_to_summary(self):
        pages = [
            {
                "pagination": {"page": 1, "size": 100, "totalCount": 1, "totalPages": 1},
                "list": [
                    {
                        "positionSn": 42,
                        "title": "From List",
                        "careerType": "CAREER",
                        "startDateTime": "2026-04-01T00:00:00",
                    }
                ],
            }
        ]

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/position/v1/jobflex":
                return httpx.Response(200, json=pages[0])
            if request.url.path.startswith("/position/v2/jobflex/"):
                return httpx.Response(404, json={"code": "NotFound"})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://mcdonalds.recruiter.co.kr/career/home",
                "metadata": {"slug": "mcdonalds"},
            }
            jobs = await discover(board, client)
        assert len(jobs) == 1
        assert jobs[0].title == "From List"
        assert jobs[0].url == ("https://mcdonalds.recruiter.co.kr/career/jobs/42")
        # Summary-only record still carries the list's careerType.
        assert jobs[0].employment_type == "Full-time"
        assert jobs[0].date_posted == "2026-04-01"

    async def test_no_slug_raises(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200))
        async with httpx.AsyncClient(transport=transport) as client:
            board = {
                "board_url": "https://example.com/careers",
                "metadata": {},
            }
            with pytest.raises(ValueError, match="recruiter.co.kr slug"):
                await discover(board, client)

    async def test_slug_from_board_url(self):
        captured: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            captured.append(request.headers.get("prefix", ""))
            if request.url.path == "/position/v1/jobflex":
                return httpx.Response(
                    200,
                    json={
                        "pagination": {
                            "page": 1,
                            "size": 100,
                            "totalCount": 0,
                            "totalPages": 1,
                        },
                        "list": [],
                    },
                )
            return httpx.Response(200, json={})

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://tel.recruiter.co.kr/career/home",
                "metadata": {},
            }
            jobs = await discover(board, client)
        assert jobs == []
        assert any(h == "tel.recruiter.co.kr" for h in captured)

    async def test_include_closed_flag_changes_filter(self):
        observed: list[dict] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/position/v1/jobflex":
                import json as _json

                observed.append(_json.loads(request.content))
                return httpx.Response(
                    200,
                    json={
                        "pagination": {
                            "page": 1,
                            "size": 100,
                            "totalCount": 0,
                            "totalPages": 1,
                        },
                        "list": [],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://mcdonalds.recruiter.co.kr/career/home",
                "metadata": {"slug": "mcdonalds", "include_closed": True},
            }
            await discover(board, client)
        assert observed[0]["filter"]["submissionStatusList"] == []
        assert observed[0]["filter"]["openStatusList"] == []


class TestCanHandle:
    async def test_recognises_url_without_client(self):
        result = await can_handle("https://mcdonalds.recruiter.co.kr/career/home")
        assert result == {"slug": "mcdonalds"}

    async def test_probes_api_for_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/design/v2":
                return httpx.Response(200, json={"title": "X"})
            if request.url.path == "/position/v1/jobflex":
                return httpx.Response(
                    200,
                    json={
                        "pagination": {
                            "page": 1,
                            "size": 1,
                            "totalCount": 42,
                            "totalPages": 42,
                        },
                        "list": [{"positionSn": 1}],
                    },
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://mcdonalds.recruiter.co.kr/career/home", client)
        assert result == {"slug": "mcdonalds", "jobs": 42}

    async def test_unknown_tenant_returns_none(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/design/v2":
                return httpx.Response(
                    400,
                    json={"code": "NotFoundCompanyException", "message": "no"},
                )
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://nobody.recruiter.co.kr/career/home", client)
        assert result is None

    async def test_non_recruiter_url_returns_none(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_api_probe_count_missing_ok(self):
        """If the list endpoint fails but design/v2 succeeds, still detect."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/design/v2":
                return httpx.Response(200, json={"title": "X"})
            if request.url.path == "/position/v1/jobflex":
                return httpx.Response(500)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://mcdonalds.recruiter.co.kr/career/home", client)
        assert result == {"slug": "mcdonalds"}


class TestRegistration:
    def test_registered_as_rich_api_monitor(self):
        from src.core.monitors import all_monitor_types, api_monitor_types

        assert "recruiter_co_kr" in all_monitor_types()
        assert "recruiter_co_kr" in api_monitor_types()

    def test_throttle_bucket_has_monitor(self):
        from src.redis_queue import _KNOWN_ATS_DOMAINS

        assert "recruiter_co_kr" in _KNOWN_ATS_DOMAINS

    def test_compat_includes_monitor(self):
        from src.workspace._compat import api_monitor_types

        assert "recruiter_co_kr" in api_monitor_types()

    def test_help_card_registered(self):
        from src.workspace.commands.help import MONITOR_CARDS

        assert "recruiter_co_kr" in MONITOR_CARDS
        assert "Recruiter.co.kr" in MONITOR_CARDS["recruiter_co_kr"]
