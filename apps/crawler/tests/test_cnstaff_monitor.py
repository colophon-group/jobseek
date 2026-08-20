from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.cnstaff import (
    _origin_from_url,
    _parse_job,
    _parse_page,
    can_handle,
    discover,
)


def _row(job_id: int, **overrides) -> dict:
    row = {
        "job_id": str(job_id),
        "job_name": f"Role {job_id}",
        "job_name_show": f"Public role {job_id}",
        "job_address_name": "上海",
        "job_published_at": "2026-08-20 09:30:00",
        "job_end_at": "2026-09-30 23:59:59",
        "job_detail": "<p>Responsibilities</p>",
        "job_desc2": "<p>Qualifications</p>",
        "company_orgnize_name_show": "Example China",
        "ws_system_job_type_ids_name": "Medical",
        "g_job_type": "Medical Affairs",
    }
    row.update(overrides)
    return row


def _payload(*, total: int, page: int, rows: list[dict]) -> dict:
    pages = (total + 14) // 15
    return {
        "total": total,
        "page": {"now": page, "total": pages},
        "list": rows,
    }


class TestOriginFromUrl:
    def test_exact_board_url(self):
        assert (
            _origin_from_url("https://daiichisankyo.cnstaff.com/recruit")
            == "https://daiichisankyo.cnstaff.com"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "http://example.cnstaff.com/recruit",
            "https://cnstaff.com/recruit",
            "https://example.cnstaff.com/jobs",
            "https://example.cnstaff.com/recruit?area=shanghai",
            "https://example.com/recruit",
        ],
    )
    def test_rejects_noncanonical_urls(self, url):
        assert _origin_from_url(url) is None


class TestParseJob:
    def test_maps_complete_rich_record(self):
        job = _parse_job(_row(12823), "https://daiichisankyo.cnstaff.com")

        assert isinstance(job, DiscoveredJob)
        assert job.url == ("https://daiichisankyo.cnstaff.com/recruitment/job/detail/id/12823/")
        assert job.title == "Public role 12823"
        assert job.locations == ["上海"]
        assert job.date_posted == "2026-08-20"
        assert job.language == "zh"
        assert "Responsibilities" in job.description
        assert "Qualifications" in job.description
        assert job.extras == {
            "responsibilities": "<p>Responsibilities</p>",
            "qualifications": "<p>Qualifications</p>",
            "valid_through": "2026-09-30",
        }
        assert job.metadata == {
            "id": "12823",
            "employer": "Example China",
            "department": "Medical",
            "job_function": "Medical Affairs",
        }

    def test_rejects_invalid_identity(self):
        invalid_id = _row(1)
        invalid_id["job_id"] = "../bad"
        with pytest.raises(ValueError, match="valid ID or title"):
            _parse_job(invalid_id, "https://example.cnstaff.com")
        with pytest.raises(ValueError, match="valid ID or title"):
            _parse_job(_row(1, job_name_show="", job_name=""), "https://example.cnstaff.com")

    def test_rejects_job_without_rich_description(self):
        with pytest.raises(ValueError, match="without a description"):
            _parse_job(
                _row(1, job_detail="", job_desc2=""),
                "https://example.cnstaff.com",
            )

    def test_omits_zero_date_and_blank_optional_fields(self):
        job = _parse_job(
            _row(
                1,
                job_address_name=" ",
                job_published_at="0000-00-00 00:00:00",
                job_end_at="0000-00-00 00:00:00",
                job_desc2="",
            ),
            "https://example.cnstaff.com",
        )
        assert job.locations is None
        assert job.date_posted is None
        assert "valid_through" not in job.extras
        assert "qualifications" not in job.extras


class TestParsePage:
    def test_accepts_numeric_string_page_metadata(self):
        total, pages, rows = _parse_page(
            {
                "total": "1",
                "page": {"now": "1", "total": "1"},
                "list": [_row(1)],
            },
            requested_page=1,
        )
        assert (total, pages, len(rows)) == (1, 1, 1)

    def test_rejects_wrong_row_count(self):
        with pytest.raises(ValueError, match="returned 1 rows, expected 2"):
            _parse_page(_payload(total=2, page=1, rows=[_row(1)]), requested_page=1)


class TestDiscover:
    async def test_paginates_and_returns_complete_jobs(self):
        rows = [_row(job_id) for job_id in range(1, 17)]

        def handler(request: httpx.Request) -> httpx.Response:
            assert request.headers["x-requested-with"] == "XMLHttpRequest"
            page = int(request.url.params["p"])
            start = (page - 1) * 15
            return httpx.Response(
                200,
                json=_payload(total=16, page=page, rows=rows[start : start + 15]),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {"board_url": "https://example.cnstaff.com/recruit"},
                client,
            )

        assert len(jobs) == 16
        assert jobs[0].title == "Public role 1"
        assert jobs[-1].metadata["id"] == "16"
        assert all(job.description and job.locations for job in jobs)

    async def test_fails_when_snapshot_changes_between_pages(self):
        rows = [_row(job_id) for job_id in range(1, 17)]

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params["p"])
            if page == 1:
                return httpx.Response(200, json=_payload(total=16, page=1, rows=rows[:15]))
            return httpx.Response(
                200,
                json=_payload(total=17, page=2, rows=[rows[15], _row(17)]),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="inventory changed"):
                await discover(
                    {"board_url": "https://example.cnstaff.com/recruit"},
                    client,
                )

    async def test_rejects_unsupported_board_url(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: None)) as client:
            with pytest.raises(ValueError, match="Unsupported CNStaff"):
                await discover({"board_url": "https://example.com/recruit"}, client)


class TestCanHandle:
    async def test_verifies_live_count(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_payload(total=1, page=1, rows=[_row(1)]))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://example.cnstaff.com/recruit", client)

        assert result == {"origin": "https://example.cnstaff.com", "jobs": 1}

    async def test_rejects_unrelated_url_without_request(self):
        assert await can_handle("https://example.com/recruit") is None
