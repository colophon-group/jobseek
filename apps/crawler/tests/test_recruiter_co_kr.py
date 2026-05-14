from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.recruiter_co_kr import (
    _RETRY_ATTEMPTS,
    _api_headers,
    _board_url,
    _dt_date,
    _extract_locations,
    _job_url,
    _parse_detail,
    _parse_list_item,
    _post_with_retry,
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

    def test_dt_date_passes_through_date_only(self):
        # Date-only strings carry no time-of-day, so TZ conversion is
        # undefined — pass through unchanged.
        assert _dt_date("2026-04-22") == "2026-04-22"

    def test_dt_date_returns_none_for_missing(self):
        assert _dt_date(None) is None
        assert _dt_date("") is None
        # Non-string types: defensive fall-through to ``None``.
        assert _dt_date(12345) is None  # type: ignore[arg-type]


class TestDtDateKstToUtc:
    """``_dt_date`` must localise the naive KST timestamp returned by the
    recruiter.co.kr API to UTC before truncating, otherwise ``date_posted``
    and ``valid_through`` shift by one calendar day for any non-Asia
    viewer (see #3208).

    KST is UTC+9, so:
      00:00 KST = 15:00 UTC the previous day
      12:00 KST = 03:00 UTC the same day
      23:59 KST = 14:59 UTC the same day
    """

    def test_kst_midnight_shifts_back_one_day(self):
        # 2026-04-22T00:00:00 KST = 2026-04-21T15:00:00 UTC
        assert _dt_date("2026-04-22T00:00:00") == "2026-04-21"

    def test_kst_midday_stays_on_same_date(self):
        # 2026-04-22T12:00:00 KST = 2026-04-22T03:00:00 UTC
        assert _dt_date("2026-04-22T12:00:00") == "2026-04-22"

    def test_kst_late_evening_stays_on_same_date(self):
        # 2026-04-22T23:59:59 KST = 2026-04-22T14:59:59 UTC — must NOT
        # clip the valid_through expiry one day early.
        assert _dt_date("2026-04-22T23:59:59") == "2026-04-22"

    def test_malformed_value_falls_back_to_pre_t_segment(self):
        # Malformed inputs degrade to the pre-fix behaviour (split on T)
        # rather than dropping the posting field entirely.
        assert _dt_date("not-a-date") == "not-a-date"
        assert _dt_date("garbageTand-more") == "garbage"

    def test_value_with_explicit_offset_respected(self):
        # If the API ever starts returning explicit offsets, respect them
        # rather than re-localising as KST. ``+00:00`` here means the
        # value is already UTC midnight, so the UTC date is unchanged.
        assert _dt_date("2026-04-22T00:00:00+00:00") == "2026-04-22"


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
        # Use mid-day KST so the UTC date matches the KST date — these
        # fixtures aren't about TZ math, that's covered in
        # ``TestDtDateKstToUtc``.
        base = {
            "positionSn": 100,
            "url": "https://mcdonalds.recruiter.co.kr/career/jobs/100",
            "list_title": "Sample",
            "startDateTime": "2026-01-15T12:00:00",
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
            # Mid-day / late-evening KST so the UTC date matches the
            # source date — TZ-shift math is covered in TestDtDateKstToUtc.
            "startDateTime": "2026-02-01T12:00:00",
            "endDateTime": "2026-03-01T23:59:59",
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
        # Raw careerType passes through; central normaliser handles canonicalisation.
        assert job.employment_type == "CAREER"
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
        assert job.employment_type == "INTERN"

    def test_unknown_career_type_passthrough(self):
        # Unknown raw careerType passes through unchanged; the central
        # normaliser decides how to bucket it downstream.
        detail = {"title": "X", "careerType": "SOMETHING_NEW"}
        job = _parse_detail(detail, self._summary(), "x")
        assert job.employment_type == "SOMETHING_NEW"

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
        assert by_url["https://mcdonalds.recruiter.co.kr/career/jobs/2"].employment_type == "INTERN"

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
                        # Mid-day KST → same UTC date.
                        "startDateTime": "2026-04-01T12:00:00",
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
        assert jobs[0].employment_type == "CAREER"
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
        """400 ``NotFoundCompanyException`` on the list endpoint means
        the tenant slug doesn't exist — ``can_handle`` must return
        ``None`` so the auto-detect stack moves on."""

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": "NotFoundCompanyException", "message": "no"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://nobody.recruiter.co.kr/career/home", client)
        assert result is None

    async def test_non_recruiter_url_returns_none(self):
        result = await can_handle("https://example.com/careers")
        assert result is None

    async def test_api_probe_detects_without_count(self):
        """If the list endpoint returns 200 but the body lacks the
        pagination hint, still detect the tenant — just without a count
        in the returned metadata. Covers partial/slim API responses
        without regressing detection."""

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/position/v1/jobflex":
                return httpx.Response(200, json={"list": []})  # no "pagination"
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


class TestTagListTypeGuard:
    """_parse_detail must not crash on non-list tagList values.

    The API is schemaless: if a misbehaving tenant returns a dict or string
    for ``tagList``, the monitor should fall back to empty tags instead of
    raising AttributeError / TypeError.
    """

    def _summary(self):
        return {
            "positionSn": 1,
            "url": "https://mcdonalds.recruiter.co.kr/career/jobs/1",
            "list_title": "Sample",
            "startDateTime": "2026-04-22T00:00:00",
            "careerType": "CAREER",
        }

    def test_taglist_as_dict_does_not_crash(self):
        detail = {"title": "Weird", "tagList": {"oops": "dict"}}
        job = _parse_detail(detail, self._summary(), "x")
        assert job is not None
        assert job.title == "Weird"
        assert (job.metadata or {}).get("tags") is None

    def test_taglist_as_string_does_not_crash(self):
        detail = {"title": "Weird2", "tagList": "oops"}
        job = _parse_detail(detail, self._summary(), "x")
        assert job is not None
        assert (job.metadata or {}).get("tags") is None

    def test_summary_taglist_as_dict_still_ignored(self):
        summary = self._summary()
        summary["tagList"] = {"bad": "type"}
        job = _parse_detail({"title": "T"}, summary, "x")
        assert job is not None
        assert (job.metadata or {}).get("tags") is None

    def test_non_string_jobdescription_non_html_is_coerced(self):
        # If an upstream caller returns, say, a list for jobDescription
        # with a non-HTML content type, the <pre>-wrapping step must not
        # crash with a TypeError.
        detail = {
            "title": "T",
            "jobDescription": ["line 1", "line 2"],
            "jobDescriptionType": "TEXT",
        }
        job = _parse_detail(detail, self._summary(), "x")
        assert job is not None
        assert job.description is not None
        assert job.description.startswith("<pre>")


class TestExtractLocations:
    def test_empty_detail_returns_empty(self):
        assert _extract_locations({}, {}) == []

    def test_region_list_with_dicts(self):
        detail = {
            "regionList": [
                {"regionSn": 1, "regionName": "Seoul"},
                {"regionSn": 2, "regionName": "Busan"},
            ],
        }
        assert _extract_locations(detail, {}) == ["Seoul", "Busan"]

    def test_region_list_with_strings(self):
        detail = {"regionNameList": ["Seoul", "Busan"]}
        assert _extract_locations(detail, {}) == ["Seoul", "Busan"]

    def test_work_place_scalar(self):
        detail = {"workPlace": "Gangnam HQ"}
        assert _extract_locations(detail, {}) == ["Gangnam HQ"]

    def test_detail_overrides_but_merges_summary(self):
        summary = {"regionList": [{"regionName": "Seoul"}]}
        detail = {"regionList": [{"regionName": "Busan"}]}
        # Both are preserved; dedup preserves order (summary first).
        assert _extract_locations(detail, summary) == ["Seoul", "Busan"]

    def test_dedupes_identical_entries(self):
        detail = {"regionList": [{"regionName": "Seoul"}, {"regionName": "Seoul"}]}
        assert _extract_locations(detail, {}) == ["Seoul"]

    def test_parse_detail_populates_locations(self):
        detail = {
            "title": "Engineer",
            "regionList": [{"regionName": "Seoul"}, {"regionName": "Busan"}],
        }
        summary = {
            "positionSn": 99,
            "url": "https://mcdonalds.recruiter.co.kr/career/jobs/99",
            "list_title": "Engineer",
        }
        job = _parse_detail(detail, summary, "mcdonalds")
        assert job is not None
        assert job.locations == ["Seoul", "Busan"]

    def test_parse_detail_no_locations_stays_none(self):
        # Matches the real McDonald's-KR payload — no location fields.
        detail = {"title": "T"}
        summary = {
            "positionSn": 1,
            "url": "https://mcdonalds.recruiter.co.kr/career/jobs/1",
            "list_title": "T",
        }
        job = _parse_detail(detail, summary, "mcdonalds")
        assert job is not None
        assert job.locations is None


def _response(status: int, *, url: str = "https://example.com/", json_body=None) -> httpx.Response:
    return httpx.Response(
        status,
        json=json_body if json_body is not None else {},
        request=httpx.Request("POST", url),
    )


class TestPostWithRetry:
    @pytest.mark.parametrize("status", [200, 400, 404, 410])
    async def test_returns_immediately_on_non_transient_status(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(status))
        resp = await _post_with_retry(client, "https://x/", headers={}, json={})
        assert resp.status_code == status
        assert client.post.await_count == 1

    @pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
    async def test_retries_on_transient_status_then_succeeds(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(side_effect=[_response(status), _response(status), _response(200)])
        with patch("src.core.monitors.recruiter_co_kr.asyncio.sleep", new_callable=AsyncMock):
            resp = await _post_with_retry(client, "https://x/", headers={}, json={})
        assert resp.status_code == 200
        assert client.post.await_count == 3

    async def test_returns_final_transient_response_after_exhaustion(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(503))
        with patch("src.core.monitors.recruiter_co_kr.asyncio.sleep", new_callable=AsyncMock):
            resp = await _post_with_retry(client, "https://x/", headers={}, json={})
        assert resp.status_code == 503
        assert client.post.await_count == _RETRY_ATTEMPTS

    async def test_does_not_sleep_after_final_failed_attempt(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.post = AsyncMock(return_value=_response(503))
        sleep = AsyncMock()
        with patch("src.core.monitors.recruiter_co_kr.asyncio.sleep", sleep):
            await _post_with_retry(client, "https://x/", headers={}, json={})
        assert sleep.await_count == _RETRY_ATTEMPTS - 1


class TestDiscoverRetriesListEndpoint:
    """Integration: discover() should survive a transient 503 on the list
    endpoint and still return jobs once the upstream recovers."""

    async def test_discover_recovers_from_transient_503(self):
        # First call returns 503, second returns the list payload.
        list_responses = [
            _response(503),
            _response(
                200,
                json_body={
                    "pagination": {"page": 1, "size": 100, "totalCount": 1, "totalPages": 1},
                    "list": [{"positionSn": 7, "title": "T", "careerType": "CAREER"}],
                },
            ),
        ]
        list_iter = iter(list_responses)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/position/v1/jobflex":
                return next(list_iter)
            if request.url.path.startswith("/position/v2/jobflex/"):
                return httpx.Response(200, json={"title": "Detail-T", "careerType": "CAREER"})
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("src.core.monitors.recruiter_co_kr.asyncio.sleep", new_callable=AsyncMock):
                board = {
                    "board_url": "https://mcdonalds.recruiter.co.kr/career/home",
                    "metadata": {"slug": "mcdonalds"},
                }
                jobs = await discover(board, client)

        assert len(jobs) == 1
        assert jobs[0].title == "Detail-T"

    async def test_discover_persistent_503_raises(self):
        # All retries return 503 -> resp.raise_for_status() inside
        # _fetch_list_page should raise HTTPStatusError.
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/position/v1/jobflex":
                return _response(503)
            return httpx.Response(404)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("src.core.monitors.recruiter_co_kr.asyncio.sleep", new_callable=AsyncMock):
                board = {
                    "board_url": "https://mcdonalds.recruiter.co.kr/career/home",
                    "metadata": {"slug": "mcdonalds"},
                }
                with pytest.raises(httpx.HTTPStatusError):
                    await discover(board, client)


class TestTenantGoneSemantics:
    """``_fetch_list_page`` must distinguish tenant-gone from generic
    validation errors. A 400 with ``NotFoundCompanyException`` in the
    body → ``BoardGoneError`` (board auto-disables after one cycle).
    A 400 with any other error code → generic ``HTTPStatusError``
    (retried by the worker).
    """

    async def test_not_found_company_raises_board_gone(self):
        from src.core.monitors import BoardGoneError

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": "NotFoundCompanyException", "message": "no"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://gone-tenant.recruiter.co.kr/career/home",
                "metadata": {"slug": "gone-tenant"},
            }
            with pytest.raises(BoardGoneError):
                await discover(board, client)

    async def test_other_400_propagates_as_http_status_error(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                400,
                json={"code": "MethodArgumentNotValidException", "message": "no"},
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://mcdonalds.recruiter.co.kr/career/home",
                "metadata": {"slug": "mcdonalds"},
            }
            with pytest.raises(httpx.HTTPStatusError):
                await discover(board, client)
