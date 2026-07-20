from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.comeet import (
    _board_parts,
    _content,
    _extract_positions,
    _parse_job,
    can_handle,
    discover,
)

SAMPLE_JOB = {
    "name": "Senior Engineer",
    "department": "Cyber R&D",
    "employment_type": "Full-time",
    "experience_level": "Senior",
    "location": {"name": "Tel Aviv", "country": "IL"},
    "url_comeet_hosted_page": "https://www.comeet.com/jobs/acme/12.345/senior-engineer/AB.123",
    "uid": "AB.123",
    "company_name": "Acme",
    "time_updated": "2026-07-20T10:00:00Z",
    "workplace_type": "Hybrid",
    "custom_fields": {
        "details": [
            {"name": "Description", "value": "<p>Build secure systems.</p>"},
            {
                "name": "Responsibilities",
                "value": "<ul><li>Ship software</li></ul>",
            },
            {"name": "The Skill Set", "value": "<ul><li>Python</li></ul>"},
            {"name": "Requirements", "value": None},
        ]
    },
}


def _page(*jobs: dict) -> str:
    return (
        "<html><script>\nCOMPANY_DATA = {};\nCOMPANY_POSITIONS_DATA = "
        + json.dumps(list(jobs))
        + ";\nPOSITION_DATA = null;\n</script></html>"
    )


class TestBoardParts:
    def test_board_url(self):
        assert _board_parts("https://www.comeet.com/jobs/acme/12.345") == ("acme", "12.345")

    def test_job_detail_url_uses_same_board(self):
        assert _board_parts("https://www.comeet.com/jobs/acme/12.345/senior-engineer/AB.123") == (
            "acme",
            "12.345",
        )

    def test_unrelated_url(self):
        assert _board_parts("https://example.com/jobs/acme/12.345") is None


class TestExtractPositions:
    def test_decodes_json_assignment(self):
        assert _extract_positions(_page(SAMPLE_JOB)) == [SAMPLE_JOB]

    def test_json_string_may_contain_assignment_terminator(self):
        job = {**SAMPLE_JOB, "name": "Engineer ]; still valid"}
        assert _extract_positions(_page(job))[0]["name"] == "Engineer ]; still valid"

    def test_missing_or_malformed_payload(self):
        assert _extract_positions("<html>no data</html>") == []
        assert _extract_positions("COMPANY_POSITIONS_DATA = [broken") == []


class TestContent:
    def test_preserves_all_sections_and_structured_extras(self):
        description, extras = _content(SAMPLE_JOB)
        assert "<h3>Description</h3>" in description
        assert "<h3>Responsibilities</h3>" in description
        assert "<h3>The Skill Set</h3>" in description
        assert extras == {
            "responsibilities": "<ul><li>Ship software</li></ul>",
            "qualifications": "<ul><li>Python</li></ul>",
        }


class TestParseJob:
    def test_full_mapping(self):
        job = _parse_job(SAMPLE_JOB)
        assert job is not None
        assert job.title == "Senior Engineer"
        assert job.locations == ["Tel Aviv"]
        assert job.employment_type == "Full-time"
        assert job.job_location_type == "hybrid"
        assert "Build secure systems" in job.description
        assert job.metadata == {
            "uid": "AB.123",
            "department": "Cyber R&D",
            "experience_level": "Senior",
            "company_name": "Acme",
            "time_updated": "2026-07-20T10:00:00Z",
        }

    def test_missing_url_is_skipped(self):
        assert _parse_job({"name": "No URL"}) is None

    def test_structured_location_fallback(self):
        raw = {**SAMPLE_JOB, "location": {"city": "Vienna", "country": "AT"}}
        assert _parse_job(raw).locations == ["Vienna, AT"]


class TestDiscover:
    async def test_returns_rich_jobs_from_one_board_request(self):
        requests = []

        def handler(request):
            requests.append(str(request.url))
            return httpx.Response(200, text=_page(SAMPLE_JOB))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {"board_url": ("https://www.comeet.com/jobs/acme/12.345/senior-engineer/AB.123")},
                client,
            )

        assert requests == ["https://www.comeet.com/jobs/acme/12.345"]
        assert len(jobs) == 1
        assert isinstance(jobs[0], DiscoveredJob)
        assert jobs[0].description

    async def test_invalid_board_url_raises(self):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: None)) as client:
            with pytest.raises(ValueError, match="Cannot derive Comeet board"):
                await discover({"board_url": "https://example.com/careers"}, client)


class TestCanHandle:
    async def test_non_comeet_url(self):
        assert await can_handle("https://example.com/careers") is None

    async def test_without_client_returns_identifiers(self):
        assert await can_handle("https://www.comeet.com/jobs/acme/12.345") == {
            "company": "acme",
            "board_id": "12.345",
        }

    async def test_probe_verifies_payload_and_count(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, text=_page(SAMPLE_JOB)))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle("https://www.comeet.com/jobs/acme/12.345", client)
        assert result == {"company": "acme", "board_id": "12.345", "jobs": 1}

    async def test_probe_rejects_page_without_payload(self):
        transport = httpx.MockTransport(lambda r: httpx.Response(200, text="<html></html>"))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle("https://www.comeet.com/jobs/acme/12.345", client) is None
