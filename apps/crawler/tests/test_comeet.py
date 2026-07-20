from __future__ import annotations

import json

import httpx
import pytest

from src.core.monitors import BoardGoneError, DiscoveredJob
from src.core.monitors.comeet import (
    _board_parts,
    _content,
    _credentials_from_api_url,
    _credentials_from_html,
    _extract_positions,
    _parse_job,
    can_handle,
    discover,
    save_raw,
)

COMPANY_ID = "67.007"
TOKEN = "7672C6A163533D11D9C429F33D10250333D1"
API_URL = f"https://www.comeet.co/careers-api/2.0/company/{COMPANY_ID}/positions?token={TOKEN}"

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
            {"name": "Responsibilities", "value": "<ul><li>Ship software</li></ul>"},
            {"name": "The Skill Set", "value": "<ul><li>Python</li></ul>"},
            {"name": "Requirements", "value": None},
        ]
    },
}


def _page(*jobs: dict) -> str:
    return (
        '<html><script>\nCOMPANY_DATA = {"slug":"acme","company_uid":"12.345"};\n'
        "COMPANY_POSITIONS_DATA = "
        + json.dumps(list(jobs))
        + ";\nPOSITION_DATA = null;\n</script></html>"
    )


def _api_position() -> dict:
    position = {key: value for key, value in SAMPLE_JOB.items() if key != "custom_fields"}
    position["url_active_page"] = position.pop("url_comeet_hosted_page")
    position["details"] = [
        {"name": "Description", "value": "<p>Build secure systems.</p>", "order": 1},
        {
            "name": "Responsibilities",
            "value": "<ul><li>Ship software</li></ul>",
            "order": 2,
        },
        {"name": "Requirements", "value": "<ul><li>Python</li></ul>", "order": 3},
    ]
    return position


class TestBoardParts:
    def test_board_url(self):
        assert _board_parts("https://www.comeet.com/jobs/acme/12.345") == ("acme", "12.345")

    def test_job_detail_url_uses_same_board(self):
        assert _board_parts("https://www.comeet.com/jobs/acme/12.345/engineer/AB.123") == (
            "acme",
            "12.345",
        )

    def test_unrelated_url(self):
        assert _board_parts("https://example.com/jobs/acme/12.345") is None


class TestEmbeddedPositions:
    def test_decodes_json_assignment(self):
        assert _extract_positions(_page(SAMPLE_JOB)) == [SAMPLE_JOB]

    def test_json_string_may_contain_assignment_terminator(self):
        job = {**SAMPLE_JOB, "name": "Engineer ]; still valid"}
        assert _extract_positions(_page(job))[0]["name"] == "Engineer ]; still valid"

    def test_missing_or_malformed_payload(self):
        assert _extract_positions("<html>no data</html>") == []
        assert _extract_positions("COMPANY_POSITIONS_DATA = [broken") == []


class TestCredentialExtraction:
    def test_api_url(self):
        assert _credentials_from_api_url(API_URL) == (COMPANY_ID, TOKEN)

    def test_wrong_host_or_missing_token(self):
        assert _credentials_from_api_url(API_URL.replace("comeet.co", "example.com")) is None
        assert _credentials_from_api_url(API_URL.split("?")[0]) is None

    def test_html_reference_and_escaped_ampersand(self):
        assert _credentials_from_html(f'<script>$.ajax({{url: "{API_URL}"}})</script>') == (
            COMPANY_ID,
            TOKEN,
        )
        escaped = (
            '<script src="https://www.comeet.co/careers-api/2.0/company/'
            f'{COMPANY_ID}/positions?details=true&amp;token={TOKEN}"></script>'
        )
        assert _credentials_from_html(escaped) == (COMPANY_ID, TOKEN)


class TestParseJob:
    def test_embedded_and_api_shapes_share_full_mapping(self):
        for raw in (SAMPLE_JOB, _api_position()):
            job = _parse_job(raw)
            assert isinstance(job, DiscoveredJob)
            assert job.title == "Senior Engineer"
            assert job.locations == ["Tel Aviv"]
            assert job.employment_type == "Full-time"
            assert job.job_location_type == "hybrid"
            assert job.date_posted == "2026-07-20T10:00:00Z"
            assert "Build secure systems" in job.description
            assert job.extras == {
                "qualifications": "<ul><li>Python</li></ul>",
                "responsibilities": "<ul><li>Ship software</li></ul>",
            }

    def test_preserves_all_description_sections(self):
        description, _ = _content(SAMPLE_JOB)
        assert "<h3>Description</h3>" in description
        assert "<h3>Responsibilities</h3>" in description
        assert "<h3>The Skill Set</h3>" in description

    def test_missing_url_is_skipped(self):
        assert _parse_job({"name": "No URL"}) is None

    def test_structured_location_and_remote_fallbacks(self):
        raw = {**SAMPLE_JOB, "location": {"city": "Vienna", "country": "AT"}}
        assert _parse_job(raw).locations == ["Vienna, AT"]
        remote = {**_api_position(), "workplace_type": None, "location": {"is_remote": True}}
        assert _parse_job(remote).job_location_type == "remote"


class TestDiscover:
    async def test_hosted_board_uses_canonical_url_and_embedded_data(self):
        requests = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(str(request.url))
            return httpx.Response(200, text=_page(SAMPLE_JOB))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {"board_url": "https://www.comeet.com/jobs/acme/12.345/engineer/AB.123"},
                client,
            )

        assert requests == ["https://www.comeet.com/jobs/acme/12.345"]
        assert len(jobs) == 1
        assert jobs[0].description

    async def test_custom_page_can_use_embedded_data(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=_page(SAMPLE_JOB)))
        async with httpx.AsyncClient(transport=transport) as client:
            jobs = await discover({"board_url": "https://example.com/careers"}, client)
        assert len(jobs) == 1

    async def test_api_metadata_requests_details(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params["token"] == TOKEN
            assert request.url.params["details"] == "true"
            return httpx.Response(200, json=[_api_position()])

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            jobs = await discover(
                {
                    "board_url": "https://www.hunters.security/careers",
                    "metadata": {"company_id": COMPANY_ID, "token": TOKEN},
                },
                client,
            )
        assert len(jobs) == 1
        assert jobs[0].description

    async def test_empty_api_feed_is_valid(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[]))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": API_URL}, client) == []

    async def test_missing_payload_raises(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text="<html></html>"))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="positions payload not found"):
                await discover({"board_url": "https://example.com/careers"}, client)

    async def test_api_404_marks_board_gone(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(404))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": API_URL}, client)


class TestCanHandle:
    async def test_without_client_detects_direct_urls(self):
        assert await can_handle("https://www.comeet.com/jobs/acme/12.345") == {
            "company": "acme",
            "board_id": "12.345",
        }
        assert await can_handle(API_URL) == {"company_id": COMPANY_ID, "token": TOKEN}
        assert await can_handle("https://example.com/careers") is None

    async def test_hosted_probe_verifies_payload_and_count(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=_page(SAMPLE_JOB)))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle("https://www.comeet.com/jobs/acme/12.345", client)
        assert result == {"company": "acme", "board_id": "12.345", "jobs": 1}

    async def test_custom_embedded_page_is_detected(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=_page(SAMPLE_JOB)))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle("https://example.com/careers", client) == {
                "company": "acme",
                "board_id": "12.345",
                "jobs": 1,
            }

    async def test_empty_embedded_api_feed_is_detected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "www.comeet.co":
                return httpx.Response(200, json=[])
            return httpx.Response(200, text=f'<script src="{API_URL}"></script>')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://www.hunters.security/careers", client)
        assert result == {"company_id": COMPANY_ID, "token": TOKEN, "jobs": 0}

    async def test_invalid_api_response_is_rejected(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.host == "www.comeet.co":
                return httpx.Response(200, json={"error": "invalid token"})
            return httpx.Response(200, text=f'<script src="{API_URL}"></script>')

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/careers", client) is None


class TestSaveRaw:
    async def test_api_metadata_saves_json(self, tmp_path):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[_api_position()]))
        async with httpx.AsyncClient(transport=transport) as client:
            await save_raw(
                tmp_path,
                "https://www.hunters.security/careers",
                {"company_id": COMPANY_ID, "token": TOKEN},
                client,
            )
        assert json.loads((tmp_path / "response.json").read_text())[0]["name"] == "Senior Engineer"

    async def test_hosted_board_saves_html(self, tmp_path):
        transport = httpx.MockTransport(lambda request: httpx.Response(200, text=_page(SAMPLE_JOB)))
        async with httpx.AsyncClient(transport=transport) as client:
            await save_raw(
                tmp_path,
                "https://www.comeet.com/jobs/acme/12.345",
                {},
                client,
            )
        assert "COMPANY_POSITIONS_DATA" in (tmp_path / "comeet.html").read_text()
