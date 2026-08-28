from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitor import MonitorResult
from src.core.monitors.oracle_hcm import (
    _RETRY_ATTEMPTS,
    _complete_facet_partition,
    _get_with_retry,
    _parse_candidate_url,
    can_handle,
    discover,
)
from src.core.scrapers.oracle_hcm import (
    _build_detail_url,
    scrape,
)
from src.core.scrapers.oracle_hcm import can_handle as scraper_can_handle


def _response(status: int) -> httpx.Response:
    return httpx.Response(status, request=httpx.Request("GET", "https://example.com/"))


class TestGetWithRetry:
    @pytest.mark.parametrize("status", [200, 204, 404, 410])
    async def test_returns_immediately_on_non_transient_status(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(status))

        resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == status
        assert client.get.await_count == 1

    @pytest.mark.parametrize("status", [403, 429, 500, 502, 503, 504])
    async def test_retries_on_transient_status_then_succeeds(self, status):
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(side_effect=[_response(status), _response(status), _response(200)])

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
            resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == 200
        assert client.get.await_count == 3

    async def test_retries_transient_302_then_succeeds(self):
        """Reproduce #5715's Oracle response with an empty Location header."""
        client = AsyncMock(spec=httpx.AsyncClient)
        redirect = httpx.Response(
            302,
            headers={"Location": ""},
            request=httpx.Request("GET", "https://example.com/"),
        )
        client.get = AsyncMock(side_effect=[redirect, _response(200)])

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
            resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == 200
        assert client.get.await_count == 2

    async def test_returns_final_transient_response_after_exhaustion(self):
        """After _RETRY_ATTEMPTS transient responses, return the last one (not
        raise) so the caller's raise_for_status() still triggers the board-level
        _RECORD_FAILURE path with the correct status code."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(503))

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
            resp = await _get_with_retry(client, "https://example.com/")

        assert resp.status_code == 503
        assert client.get.await_count == _RETRY_ATTEMPTS

    async def test_does_not_sleep_after_final_failed_attempt(self):
        """Sleep is for back-off between attempts — sleeping after the last
        attempt (when we're giving up anyway) just pointlessly delays the
        caller's error handling."""
        client = AsyncMock(spec=httpx.AsyncClient)
        client.get = AsyncMock(return_value=_response(503))
        sleep = AsyncMock()

        with patch("src.core.monitors.oracle_hcm.asyncio.sleep", sleep):
            await _get_with_retry(client, "https://example.com/")

        # _RETRY_ATTEMPTS attempts → _RETRY_ATTEMPTS - 1 sleeps between them
        assert sleep.await_count == _RETRY_ATTEMPTS - 1


def test_complete_facet_partition_falls_back_to_organizations():
    """An incomplete category taxonomy must not hide a complete safe facet."""
    wrapper = {
        "categoriesFacet": [{"Id": "category-a", "TotalCount": 300}],
        "organizationsFacet": [
            {"Id": "org-a", "TotalCount": 300},
            {"Id": "org-b", "TotalCount": 150},
        ],
    }

    with patch("src.core.monitors.oracle_hcm._RESULT_WINDOW_LIMIT", 400):
        partitions = _complete_facet_partition(wrapper, 450)

    assert partitions == [
        ("selectedOrganizationsFacet", "org-a", 300),
        ("selectedOrganizationsFacet", "org-b", 150),
    ]


def test_complete_facet_partition_rejects_oversized_bucket():
    """A mathematically complete facet is unsafe if a bucket still hits the cap."""
    wrapper = {
        "categoriesFacet": [
            {"Id": "category-a", "TotalCount": 401},
            {"Id": "category-b", "TotalCount": 49},
        ]
    }

    with patch("src.core.monitors.oracle_hcm._RESULT_WINDOW_LIMIT", 400):
        assert _complete_facet_partition(wrapper, 450) is None


@pytest.mark.asyncio
@pytest.mark.parametrize("region", ["us6", "em2"])
async def test_can_handle_retries_transient_forbidden_response(region):
    """Akamai can intermittently return 403 for a valid Oracle tenant."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        side_effect=[
            _response(403),
            httpx.Response(
                200,
                json={"items": [{"TotalJobsCount": 42}]},
                request=httpx.Request("GET", "https://example.com/"),
            ),
        ]
    )

    with patch("src.core.monitors.oracle_hcm.asyncio.sleep", new_callable=AsyncMock):
        config = await can_handle(
            f"https://example.fa.{region}.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
            client,
        )

    assert config == {
        "host": f"example.fa.{region}.oraclecloud.com",
        "site": "CX_1",
        "jobs_count": 42,
    }
    assert client.get.await_count == 2


@pytest.mark.asyncio
async def test_can_handle_oraclecloud_eu_tenant():
    """European Oracle HCM tenants use oraclecloud.eu as well as .com."""
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": 30}]},
            request=httpx.Request("GET", "https://example.com/"),
        )
    )

    config = await can_handle(
        "https://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001",
        client,
    )

    assert config == {
        "host": "evht.fa.ocs.oraclecloud.eu",
        "site": "CX_7001",
        "jobs_count": 30,
    }


@pytest.mark.asyncio
async def test_can_handle_preserves_selected_organization_facet():
    client = AsyncMock(spec=httpx.AsyncClient)
    client.get = AsyncMock(
        return_value=httpx.Response(
            200,
            json={
                "items": [
                    {
                        "TotalJobsCount": 42,
                        "SelectedOrganizationsFacet": "org-42",
                        "organizationsFacet": [
                            {"Id": "org-42", "Name": "Example", "TotalCount": 42}
                        ],
                    }
                ]
            },
            request=httpx.Request("GET", "https://example.com/"),
        )
    )

    config = await can_handle(
        "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/"
        "jobs?selectedOrganizationsFacet=org-42",
        client,
    )

    assert config == {
        "host": "example.fa.us2.oraclecloud.com",
        "site": "CX_1",
        "jobs_count": 42,
        "organization_id": "org-42",
    }
    assert "selectedOrganizationsFacet=org-42" in client.get.await_args.args[0]


@pytest.mark.asyncio
async def test_scraper_can_handle_oraclecloud_eu_job_url():
    config = await scraper_can_handle(
        "https://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001/job/2334",
        AsyncMock(spec=httpx.AsyncClient),
    )

    assert config == {}


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        (
            "https://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001",
            ("evht.fa.ocs.oraclecloud.eu", "CX_7001", None),
        ),
        (
            "https://hcbt.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX/jobs",
            ("hcbt.fa.em2.oraclecloud.com", "CX", None),
        ),
        (
            "https://emit.fa.ca3.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001",
            ("emit.fa.ca3.oraclecloud.com", "CX_2001", None),
        ),
        (
            "https://iaayey.fa.ocs.oraclecloud26.com/hcmUI/CandidateExperience/en/sites/CX_1",
            ("iaayey.fa.ocs.oraclecloud26.com", "CX_1", None),
        ),
        (
            "https://jpmc.fa.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1001/requisitions/preview/240348971",
            ("jpmc.fa.oraclecloud.com", "CX_1001", "240348971"),
        ),
    ],
)
def test_parse_candidate_url_accepts_supported_oracle_tenants(url, expected):
    assert _parse_candidate_url(url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "http://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001",
        "https://user@evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001",
        "https://evht.fa.ocs.oraclecloud.eu:8443/hcmUI/CandidateExperience/en/sites/CX_7001",
        "https://evil.example/evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001",
        "https://evht.fa.ocs.oraclecloud.eu.evil.example/hcmUI/CandidateExperience/en/sites/CX_7001",
        "https://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites",
        "https://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001/other",
        "https://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001/job/1/extra",
    ],
)
def test_parse_candidate_url_rejects_untrusted_or_malformed_urls(url):
    assert _parse_candidate_url(url) is None


def test_parse_candidate_url_requires_job_route_when_requested():
    board_url = "https://evht.fa.ocs.oraclecloud.eu/hcmUI/CandidateExperience/en/sites/CX_7001"

    assert _parse_candidate_url(board_url, require_job=True) is None


@pytest.mark.asyncio
async def test_scraper_can_handle_rejects_oracle_lookalike_url():
    config = await scraper_can_handle(
        "https://evil.example/evht.fa.ocs.oraclecloud.eu"
        "/hcmUI/CandidateExperience/en/sites/CX_7001/job/2334",
        AsyncMock(spec=httpx.AsyncClient),
    )

    assert config is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "url",
    [
        "http://example.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
        "https://user@example.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
        "https://evil.test/example.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1",
        "https://example.fa.em2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1,offset=200",
        "https://example.fa.em2.oraclecloud.com/other/en/sites/CX_1",
    ],
)
async def test_can_handle_rejects_noncanonical_oracle_url(url):
    client = AsyncMock(spec=httpx.AsyncClient)

    assert await can_handle(url, client) is None
    client.get.assert_not_awaited()


@pytest.mark.parametrize(
    "host",
    [
        "example.fa.em2.oraclecloud.com",
        "fa-example-saasfaprod1.fa.ocs.oraclecloud.com",
        "example.fa.ocs.oraclecloud26.com",
        "example.fa.ocs.oraclecloud.eu",
    ],
)
def test_host_allowlist_covers_configured_oracle_cloud_domains(host):
    from src.core.monitors.oracle_hcm import _ORACLE_HCM_HOST_RE

    assert _ORACLE_HCM_HOST_RE.fullmatch(host)


@pytest.mark.asyncio
async def test_discover_uses_native_finder_suffix_pagination():
    """The workspace path paginates past short pages via Oracle's finder suffix."""
    requested_urls: list[str] = []

    def _jobs(start: int, count: int) -> list[dict]:
        return [
            {
                "Id": str(i),
                "Title": f"Job {i}",
                "PrimaryLocation": "Cincinnati, OH, United States",
                "PostedDate": "2026-08-09",
                "JobSchedule": "Full time",
            }
            for i in range(start, start + count)
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested_urls.append(url)
        if ",offset=400" in url:
            offset, count = 400, 50
        elif ",offset=200" in url:
            # Oracle can return a short page while jobs are changing. It is
            # not a reliable end-of-results signal (Kroger #6379).
            offset, count = 200, 199
        else:
            offset, count = 0, 200
        rows = _jobs(offset, count)
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": 450, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001"
        ),
        "metadata": {"host": "example.fa.us2.oraclecloud.com", "site": "CX_2001"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover(board, client)

    assert isinstance(jobs, MonitorResult)
    assert jobs.truncated is True
    assert len(jobs.urls) == 449
    assert requested_urls[0].endswith("limit=200,sortBy=POSTING_DATES_DESC")
    assert requested_urls[1].endswith("limit=200,sortBy=POSTING_DATES_DESC,offset=200")
    assert requested_urls[2].endswith("limit=200,sortBy=POSTING_DATES_DESC,offset=400")
    assert [
        jobs.jobs_by_url[
            f"https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001/job/{i}"
        ].title
        for i in range(198, 202)
    ] == [
        "Job 198",
        "Job 199",
        "Job 200",
        "Job 201",
    ]


@pytest.mark.asyncio
async def test_discover_infers_and_applies_board_url_organization_facet():
    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        wrapper = {
            "TotalJobsCount": 2,
            "SelectedOrganizationsFacet": "org-42",
            "organizationsFacet": [{"Id": "org-42", "TotalCount": 2}],
            "requisitionList": [
                {"Id": "1", "Title": "Job 1", "PrimaryLocation": "Regina, SK, Canada"},
                {"Id": "2", "Title": "Job 2", "PrimaryLocation": "Saskatoon, SK, Canada"},
            ],
        }
        return httpx.Response(200, json={"items": [wrapper]})

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/"
            "jobs?selectedOrganizationsFacet=org-42"
        ),
        "metadata": {"host": "example.fa.us2.oraclecloud.com", "site": "CX_1"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    assert isinstance(result, list)
    assert len(result) == 2
    assert "selectedOrganizationsFacet=org-42" in requested_urls[0]


@pytest.mark.asyncio
async def test_discover_fails_closed_when_oracle_ignores_organization_filter():
    def handler(request: httpx.Request) -> httpx.Response:
        wrapper = {
            "TotalJobsCount": 2,
            "SelectedOrganizationsFacet": None,
            "organizationsFacet": [
                {"Id": "org-42", "TotalCount": 1},
                {"Id": "other", "TotalCount": 1},
            ],
            "requisitionList": [
                {"Id": "1", "Title": "Job 1", "PrimaryLocation": "Regina, SK, Canada"},
                {"Id": "2", "Title": "Other", "PrimaryLocation": "Regina, SK, Canada"},
            ],
        }
        return httpx.Response(200, json={"items": [wrapper]})

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/"
            "jobs?selectedOrganizationsFacet=org-42"
        ),
        "metadata": {"host": "example.fa.us2.oraclecloud.com", "site": "CX_1"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="did not apply"):
            await discover(board, client)


@pytest.mark.asyncio
async def test_discover_rejects_conflicting_organization_filter_sources():
    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1/"
            "jobs?selectedOrganizationsFacet=org-url"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "organization_id": "org-config",
        },
    }

    with pytest.raises(ValueError, match="conflicts"):
        await discover(board, AsyncMock(spec=httpx.AsyncClient))


@pytest.mark.asyncio
async def test_discover_partitions_results_above_oracle_window_limit():
    """Complete facets make a large Oracle board fully traversable."""
    requested_urls: list[str] = []

    def _jobs(start: int, count: int) -> list[dict]:
        return [
            {
                "Id": str(i),
                "Title": f"Job {i}",
                "PrimaryLocation": "Bethesda, MD, United States",
            }
            for i in range(start, start + count)
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        requested_urls.append(url)
        if "selectedCategoriesFacet=A" in url:
            offset = 200 if ",offset=200" in url else 0
            total = 250
            rows = _jobs(offset, min(200, total - offset))
            wrapper = {"TotalJobsCount": total, "requisitionList": rows}
        elif "selectedCategoriesFacet=B" in url:
            total = 200
            wrapper = {"TotalJobsCount": total, "requisitionList": _jobs(250, total)}
        else:
            wrapper = {
                "TotalJobsCount": 450,
                "requisitionList": _jobs(0, 200),
                "categoriesFacet": [
                    {"Id": "A", "TotalCount": 250},
                    {"Id": "B", "TotalCount": 200},
                ],
            }
        return httpx.Response(200, json={"items": [wrapper]})

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001"
        ),
        "metadata": {"host": "example.fa.us2.oraclecloud.com", "site": "CX_2001"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch("src.core.monitors.oracle_hcm._RESULT_WINDOW_LIMIT", 400):
            result = await discover(board, client)

    assert isinstance(result, list)
    assert len(result) == 450
    assert requested_urls[0].endswith("limit=200,sortBy=POSTING_DATES_DESC")
    assert "selectedCategoriesFacet=A" in requested_urls[1]
    assert requested_urls[2].endswith("selectedCategoriesFacet=A,offset=200")
    assert requested_urls[3].endswith("selectedCategoriesFacet=B")


@pytest.mark.asyncio
async def test_discover_applies_overlap_independently_to_facet_partitions():
    """Partitioned tenants retain bounded churn protection per facet."""

    def _jobs(start: int, count: int) -> list[dict]:
        return [
            {"Id": str(i), "Title": f"Job {i}", "PrimaryLocation": "United States"}
            for i in range(start, start + count)
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "selectedCategoriesFacet=A" in url:
            if ",offset=180" in url:
                # One deletion before the boundary shifts the remaining rows
                # left; the 20-row overlap still captures the original set.
                wrapper = {"TotalJobsCount": 249, "requisitionList": _jobs(181, 69)}
            else:
                wrapper = {"TotalJobsCount": 250, "requisitionList": _jobs(0, 200)}
        elif "selectedCategoriesFacet=B" in url:
            wrapper = {"TotalJobsCount": 200, "requisitionList": _jobs(250, 200)}
        else:
            wrapper = {
                "TotalJobsCount": 450,
                "requisitionList": _jobs(0, 200),
                "categoriesFacet": [
                    {"Id": "A", "TotalCount": 250},
                    {"Id": "B", "TotalCount": 200},
                ],
            }
        return httpx.Response(200, json={"items": [wrapper]})

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_2001",
            "offset_overlap": 20,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch("src.core.monitors.oracle_hcm._RESULT_WINDOW_LIMIT", 400):
            result = await discover(board, client)

    assert isinstance(result, list)
    assert len(result) == 450


@pytest.mark.asyncio
async def test_discover_marks_cross_partition_duplicate_as_truncated():
    """Facet churn cannot silently omit a job during a partitioned cycle."""

    def _jobs(start: int, count: int) -> list[dict]:
        return [
            {"Id": str(i), "Title": f"Job {i}", "PrimaryLocation": "United States"}
            for i in range(start, start + count)
        ]

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if "selectedCategoriesFacet=A" in url:
            offset = 200 if ",offset=200" in url else 0
            total = 250
            rows = _jobs(offset, min(200, total - offset))
            wrapper = {"TotalJobsCount": total, "requisitionList": rows}
        elif "selectedCategoriesFacet=B" in url:
            # ID 249 moved between categories while the cycle was running;
            # ID 449 is consequently missing even though counts still match.
            wrapper = {"TotalJobsCount": 200, "requisitionList": _jobs(249, 200)}
        else:
            wrapper = {
                "TotalJobsCount": 450,
                "requisitionList": _jobs(0, 200),
                "categoriesFacet": [
                    {"Id": "A", "TotalCount": 250},
                    {"Id": "B", "TotalCount": 200},
                ],
            }
        return httpx.Response(200, json={"items": [wrapper]})

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001"
        ),
        "metadata": {"host": "example.fa.us2.oraclecloud.com", "site": "CX_2001"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with patch("src.core.monitors.oracle_hcm._RESULT_WINDOW_LIMIT", 400):
            result = await discover(board, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 449


@pytest.mark.asyncio
async def test_discover_marks_cross_page_duplicate_as_truncated():
    """Offset churn can repeat one ID while silently omitting another."""

    def handler(request: httpx.Request) -> httpx.Response:
        start = 199 if ",offset=200" in str(request.url) else 0
        rows = [
            {
                "Id": str(i),
                "Title": f"Job {i}",
                "PrimaryLocation": "Cincinnati, OH, United States",
            }
            for i in range(start, start + 200)
        ]
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": 400, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001"
        ),
        "metadata": {"host": "example.fa.us2.oraclecloud.com", "site": "CX_2001"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 399


@pytest.mark.asyncio
async def test_discover_overlap_absorbs_total_drop_and_offset_shift():
    """An overlap catches the row skipped when a deletion shifts later pages."""

    def _job(job_id: int) -> dict:
        return {
            "Id": str(job_id),
            "Title": f"Job {job_id}",
            "PrimaryLocation": "Boise, ID, United States",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ",offset=360" in url:
            rows = [_job(i) for i in range(361, 400)]
            total = 399
        elif ",offset=180" in url:
            rows = [_job(i) for i in range(181, 381)]
            total = 399
        else:
            rows = [_job(i) for i in range(200)]
            total = 400
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": total, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "offset_overlap": 20,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    assert not isinstance(result, MonitorResult)
    assert len(result) == 400
    assert {job.url.rsplit("/", 1)[-1] for job in result} == {str(i) for i in range(400)}


@pytest.mark.asyncio
async def test_discover_allows_configured_advertised_total_tail_tolerance():
    """Some tenants advertise a few more jobs than their final page exposes."""

    def handler(request: httpx.Request) -> httpx.Response:
        offset = 200 if ",offset=200" in str(request.url) else 0
        count = 195 if offset else 200
        rows = [
            {
                "Id": str(i),
                "Title": f"Job {i}",
                "PrimaryLocation": "Boise, ID, United States",
            }
            for i in range(offset, offset + count)
        ]
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": 400, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "total_count_tolerance": 5,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    assert not isinstance(result, MonitorResult)
    assert len(result) == 395


@pytest.mark.asyncio
@pytest.mark.parametrize("page_shortfall_tolerance", [0, 4])
async def test_discover_requires_explicit_intermediate_page_shortfall_tolerance(
    page_shortfall_tolerance: int,
):
    """A stable provider-side gap is accepted only for an opted-in tenant."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ",offset=400" in url:
            offset, count = 400, 170
        elif ",offset=200" in url:
            offset, count = 200, 196
        else:
            offset, count = 0, 200
        rows = [
            {
                "Id": str(i),
                "Title": f"Job {i}",
                "PrimaryLocation": "Pittsburgh, PA, United States",
            }
            for i in range(offset, offset + count)
        ]
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": 570, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "page_shortfall_tolerance": page_shortfall_tolerance,
            "total_count_tolerance": 4,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    if page_shortfall_tolerance:
        assert not isinstance(result, MonitorResult)
        assert len(result) == 566
    else:
        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 566


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("duplicate_row_tolerance", "is_truncated"),
    [(0, True), (1, False)],
)
async def test_discover_requires_explicit_within_page_duplicate_tolerance(
    duplicate_row_tolerance: int,
    is_truncated: bool,
):
    """Offset overlap must not implicitly bless duplicates within one page."""

    def _job(job_id: int) -> dict:
        return {
            "Id": str(job_id),
            "Title": f"Job {job_id}",
            "PrimaryLocation": "Boise, ID, United States",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ",offset=360" in url:
            rows = [_job(i) for i in range(360, 400)]
        elif ",offset=180" in url:
            rows = [_job(i) for i in range(180, 379)] + [_job(378)]
        else:
            rows = [_job(i) for i in range(200)]
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": 400, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "offset_overlap": 20,
            "duplicate_row_tolerance": duplicate_row_tolerance,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    assert isinstance(result, MonitorResult) is is_truncated
    if is_truncated:
        assert result.truncated is True
        assert len(result.urls) == 400
    else:
        assert len(result) == 400


@pytest.mark.asyncio
async def test_discover_overlap_still_truncates_when_churn_exceeds_margin():
    """A shift larger than the configured overlap must remain fail-closed."""

    def _job(job_id: int) -> dict:
        return {
            "Id": str(job_id),
            "Title": f"Job {job_id}",
            "PrimaryLocation": "Boise, ID, United States",
        }

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ",offset=360" in url:
            rows = [_job(i) for i in range(385, 400)]
            total = 375
        elif ",offset=180" in url:
            rows = [_job(i) for i in range(205, 400)]
            total = 375
        else:
            rows = [_job(i) for i in range(200)]
            total = 400
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": total, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "offset_overlap": 20,
        },
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 395


@pytest.mark.asyncio
@pytest.mark.parametrize("offset_overlap", [-1, 200, True, "20"])
async def test_discover_rejects_invalid_offset_overlap(offset_overlap):
    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "offset_overlap": offset_overlap,
        },
    }

    with pytest.raises(ValueError, match="offset_overlap"):
        await discover(board, AsyncMock(spec=httpx.AsyncClient))


@pytest.mark.asyncio
@pytest.mark.parametrize("total_count_tolerance", [-1, True, "5"])
async def test_discover_rejects_invalid_total_count_tolerance(total_count_tolerance):
    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "total_count_tolerance": total_count_tolerance,
        },
    }

    with pytest.raises(ValueError, match="total_count_tolerance"):
        await discover(board, AsyncMock(spec=httpx.AsyncClient))


@pytest.mark.asyncio
@pytest.mark.parametrize("page_shortfall_tolerance", [-1, 200, True, "5"])
async def test_discover_rejects_invalid_page_shortfall_tolerance(page_shortfall_tolerance):
    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "page_shortfall_tolerance": page_shortfall_tolerance,
        },
    }

    with pytest.raises(ValueError, match="page_shortfall_tolerance"):
        await discover(board, AsyncMock(spec=httpx.AsyncClient))


@pytest.mark.asyncio
@pytest.mark.parametrize("duplicate_row_tolerance", [-1, True, "5"])
async def test_discover_rejects_invalid_duplicate_row_tolerance(duplicate_row_tolerance):
    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_1"
        ),
        "metadata": {
            "host": "example.fa.us2.oraclecloud.com",
            "site": "CX_1",
            "duplicate_row_tolerance": duplicate_row_tolerance,
        },
    }

    with pytest.raises(ValueError, match="duplicate_row_tolerance"):
        await discover(board, AsyncMock(spec=httpx.AsyncClient))


@pytest.mark.asyncio
async def test_discover_marks_oracle_result_window_cap_as_truncated():
    """An empty page before TotalJobsCount suppresses unsafe gone-detection."""

    def handler(request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        if ",offset=200" in url:
            offset, count = 200, 200
        elif ",offset=400" in url:
            offset, count = 400, 50
        elif ",offset=600" in url:
            offset, count = 600, 0
        else:
            offset, count = 0, 200
        rows = [
            {
                "Id": str(i),
                "Title": f"Job {i}",
                "PrimaryLocation": "Cincinnati, OH, United States",
            }
            for i in range(offset, offset + count)
        ]
        return httpx.Response(
            200,
            json={"items": [{"TotalJobsCount": 900, "requisitionList": rows}]},
        )

    board = {
        "board_url": (
            "https://example.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001"
        ),
        "metadata": {"host": "example.fa.us2.oraclecloud.com", "site": "CX_2001"},
    }
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await discover(board, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 450


# ── Vanity-domain config-driven scraper path ────────────────────────
#
# Regression for #2920: Nokia (jobs.nokia.com) and TI (careers.ti.com) host
# Oracle HCM Cloud SPAs on vanity domains. The `_ORACLE_HCM_URL_RE` auto-detect
# regex requires a *.fa.*.oraclecloud.com host, which vanity URLs don't have —
# so boards.csv must point at the canonical tenant via explicit `host` + `site`
# config. This path bypasses the regex entirely and goes straight through
# `_build_detail_url(host, site)`.
#
# The job id has to be extracted from the *vanity* URL (not the canonical
# host), so `_JOB_ID_RE` must match `/en/job/{id}` paths regardless of host.


def _oracle_hcm_payload(
    req_id: str,
    title: str,
    *,
    description: str = "<p>Role description</p>",
    organization_description: str = "",
    corporate_description: str = "",
    location: str = "United States",
) -> dict:
    """Mimic the shape of the Oracle HCM `recruitingCEJobRequisitionDetails` REST response."""
    return {
        "items": [
            {
                "Id": req_id,
                "Title": title,
                "PrimaryLocation": location,
                "ExternalDescriptionStr": description,
                "OrganizationDescriptionStr": organization_description,
                "CorporateDescriptionStr": corporate_description,
                "ExternalQualificationsStr": "",
                "ExternalResponsibilitiesStr": "",
                "ExternalPostedStartDate": "2026-05-01",
                "JobSchedule": "Full-time",
            }
        ],
    }


class TestBuildDetailUrl:
    def test_uses_canonical_tenant_not_vanity_host(self):
        # Caller passes the canonical Oracle tenant — even when the job URL
        # lives on a vanity domain. The vanity host (jobs.nokia.com) does NOT
        # appear in the API URL because the vanity host returns 405 from the
        # Oracle API Gateway.
        url = _build_detail_url("fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com", "CX_1")
        assert "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com" in url
        assert "siteNumber=CX_1" in url
        assert "jobs.nokia.com" not in url

    def test_url_pattern_compatible_with_api_sniffer(self):
        # Sanity: the {req_id} placeholder is the one api_sniffer substitutes
        # via `url_pattern` — must be present unchanged so the substitution
        # actually fires.
        url = _build_detail_url("edbz.fa.us2.oraclecloud.com", "CX")
        assert "{req_id}" in url


@pytest.mark.asyncio
class TestVanityDomainConfigPath:
    """End-to-end through `oracle_hcm.scrape` → `api_sniffer._scrape_http`."""

    async def test_nokia_vanity_url_with_explicit_config(self):
        """Nokia URL on jobs.nokia.com routes to the canonical tenant via config."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            api_calls.append(url)
            return httpx.Response(
                200,
                json=_oracle_hcm_payload("36037", "Machine Learning Test Capability Eng."),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://jobs.nokia.com/en/job/36037",
                {"host": "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com", "site": "CX_1"},
                client,
            )

        assert len(api_calls) == 1
        # API must hit the canonical tenant, not the vanity host
        assert "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com" in api_calls[0]
        assert "jobs.nokia.com" not in api_calls[0]
        # req_id from vanity URL must land in the API URL (not the literal `{req_id}`)
        assert '"36037"' in api_calls[0] or "%2236037%22" in api_calls[0]
        assert "siteNumber=CX_1" in api_calls[0]
        assert content.title == "Machine Learning Test Capability Eng."
        assert content.locations == ["United States"]
        assert content.description

    async def test_falls_back_to_organization_description(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                json=_oracle_hcm_payload(
                    "108622",
                    "Executive Club Agent",
                    description="",
                    organization_description="<p>Join our hotel team in Xi'an.</p>",
                    corporate_description="<p>Corporate boilerplate.</p>",
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://jobs.nokia.com/en/job/108622",
                {"host": "fa-evmr-saasfaprod1.fa.ocs.oraclecloud.com", "site": "CX_1"},
                client,
            )

        assert content.description == "<p>Join our hotel team in Xi'an.</p>"

    async def test_ti_vanity_url_with_explicit_config(self):
        """TI URL on careers.ti.com routes to its own canonical tenant via config."""
        api_calls: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            url = str(request.url)
            api_calls.append(url)
            return httpx.Response(
                200,
                json=_oracle_hcm_payload(
                    "25009746",
                    "Lead Software Engineer | Radar SDK",
                    description="<p>Role details</p>",
                    location="Bengaluru, India",
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            content = await scrape(
                "https://careers.ti.com/en/job/25009746",
                {"host": "edbz.fa.us2.oraclecloud.com", "site": "CX"},
                client,
            )

        assert len(api_calls) == 1
        assert "edbz.fa.us2.oraclecloud.com" in api_calls[0]
        assert "careers.ti.com" not in api_calls[0]
        assert "siteNumber=CX" in api_calls[0]
        assert content.title == "Lead Software Engineer | Radar SDK"
        assert content.locations == ["Bengaluru, India"]
