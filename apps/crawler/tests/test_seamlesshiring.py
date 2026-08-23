from __future__ import annotations

import httpx
import pytest

from src.core.monitors.seamlesshiring import (
    _parse_job,
    _tenant_from_url,
    can_handle,
    discover,
)
from src.workspace._compat import detect_ats_from_url


def _posting(**overrides):
    value = {
        "id": 10310,
        "title": "Project Manager",
        "summary": "Lead the project.",
        "details": "<p>Responsibilities and requirements.</p>",
        "location": "Borno State",
        "post_date": "2026-08-06",
        "expiry_date": "2026-08-20",
        "job_type": "full-time",
        "work_style": "onsite",
        "position": "GRADE G",
    }
    value.update(overrides)
    return value


def _response(posts, *, page=1, last_page=1, total=None):
    total = len(posts) if total is None else total
    return {
        "status_code": 200,
        "status": "success",
        "data": {
            "jobs": {
                "current_page": page,
                "data": posts,
                "next_page_url": (
                    f"http://care.seamlesshiring.com/v2/jobs/job-list?page={page + 1}"
                    if page < last_page
                    else None
                ),
                "total": total,
            }
        },
    }


class TestTenant:
    def test_board_url(self):
        assert _tenant_from_url("https://carenigeria.seamlesshiring.com/") == "carenigeria"

    def test_unrelated_url(self):
        assert _tenant_from_url("https://example.com/jobs") is None

    def test_compat_detection(self):
        assert detect_ats_from_url("https://carenigeria.seamlesshiring.com/") == "seamlesshiring"


class TestParseJob:
    def test_rich_fields(self):
        job = _parse_job(_posting(), "carenigeria")
        assert job.url == "https://carenigeria.seamlesshiring.com/job/view/10310"
        assert job.title == "Project Manager"
        assert job.description == "Lead the project.\n<p>Responsibilities and requirements.</p>"
        assert job.locations == ["Borno State"]
        assert job.employment_type == "full-time"
        assert job.job_location_type == "onsite"
        assert job.date_posted == "2026-08-06"
        assert job.metadata == {
            "id": 10310,
            "valid_through": "2026-08-20",
            "position": "GRADE G",
        }

    def test_missing_id(self):
        assert _parse_job(_posting(id=None), "carenigeria") is None


class TestDiscover:
    async def test_empty_authoritative_response(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_response([], total=0))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            jobs = await discover(
                {
                    "board_url": "https://carenigeria.seamlesshiring.com/",
                    "metadata": {},
                },
                client,
            )
        assert jobs == []

    async def test_paginates(self):
        def handler(request: httpx.Request):
            page = int(request.url.params["page"])
            payload = _response(
                [_posting(id=page, title=f"Job {page}")],
                page=page,
                last_page=2,
                total=2,
            )
            return httpx.Response(200, json=payload)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://care.seamlesshiring.com/",
                    "metadata": {"tenant": "care"},
                },
                client,
            )
        assert [job.title for job in jobs] == ["Job 1", "Job 2"]

    async def test_invalid_payload_raises(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json={}))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="Unexpected SeamlessHiring"):
                await discover(
                    {
                        "board_url": "https://care.seamlesshiring.com/",
                        "metadata": {},
                    },
                    client,
                )


class TestCanHandle:
    async def test_zero_is_detected(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=_response([], total=0))
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle("https://care.seamlesshiring.com/", client)
        assert result == {"tenant": "care", "jobs": 0}

    async def test_unrelated(self):
        assert await can_handle("https://example.com/jobs") is None
