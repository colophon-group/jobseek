from __future__ import annotations

import httpx
import pytest

from src.core.enum_normalize import (
    normalize_employment_type,
    normalize_job_location_type,
)
from src.core.monitor import MonitorResult
from src.core.monitors import _REGISTRY, beehire
from src.core.monitors.beehire import (
    _api_url,
    _parse_job,
    _slug_from_url,
    can_handle,
    discover,
)

SLUG = "gichd"
BOARD_URL = f"https://app.beehire.com/career/{SLUG}"
API_URL = f"https://app.beehire.com/users/getPublicCampaigns/{SLUG}"


def _campaign(**overrides) -> dict:
    campaign = {
        "id": "69a164e08ff169af49fe8dd3",
        "title": {"0": "Consultant on Ammunition Safety Management - Moldova"},
        "description": {"0": "<p>Support ammunition safety management.</p>"},
        "location": {
            "name": "Chișinău, Moldova",
            "city": "Chișinău",
            "state": "Chisinau",
            "country": "Moldova",
        },
        "created": "2026-02-27T09:33:20.234Z",
        "language": 0,
        "inviteKey": "6L-oDP2wk",
        "details": {
            "contract": {
                "type": "contractType_fixedTerm",
                "remote": "remoteWork_partial",
            }
        },
        "jobCategories": [{"label": "Consulting"}],
    }
    campaign.update(overrides)
    return campaign


def _transport(payload: dict, *, status_code: int = 200) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url == API_URL
        assert request.headers["accept"] == "application/json"
        assert request.headers["referer"] == BOARD_URL
        return httpx.Response(status_code, json=payload)

    return httpx.MockTransport(handler)


class TestSlugFromUrl:
    def test_career_and_rss_urls(self):
        assert _slug_from_url(BOARD_URL) == SLUG
        assert _slug_from_url(f"https://app.beehire.com/careerRss/{SLUG}") == SLUG

    def test_rejects_invites_unrelated_hosts_and_spoofs(self):
        assert _slug_from_url("https://app.beehire.com/invite/6L-oDP2wk") is None
        assert _slug_from_url("https://example.com/career/gichd") is None
        assert _slug_from_url("https://app.beehire.com.evil.test/career/gichd") is None

    def test_api_url(self):
        assert _api_url(SLUG) == API_URL


class TestMapping:
    def test_parse_complete_campaign(self):
        job = _parse_job(_campaign())
        assert job is not None
        assert job.url == "https://app.beehire.com/invite/6L-oDP2wk"
        assert job.title == "Consultant on Ammunition Safety Management - Moldova"
        assert job.description == "<p>Support ammunition safety management.</p>"
        assert job.locations == ["Chișinău, Moldova"]
        assert job.date_posted == "2026-02-27T09:33:20.234Z"
        assert job.language == "en"
        assert job.localizations == {
            "en": {
                "title": "Consultant on Ammunition Safety Management - Moldova",
                "description": "<p>Support ammunition safety management.</p>",
                "locations": ["Chișinău, Moldova"],
            }
        }
        assert normalize_employment_type(job.employment_type) == "contract"
        assert normalize_job_location_type(job.job_location_type) == "hybrid"
        assert job.metadata == {
            "id": "69a164e08ff169af49fe8dd3",
            "invite_key": "6L-oDP2wk",
            "contract": {
                "type": "contractType_fixedTerm",
                "remote": "remoteWork_partial",
            },
            "categories": ["Consulting"],
        }

    def test_prefers_invite_link_and_full_description(self):
        job = _parse_job(
            _campaign(
                inviteLink="/invite/custom?form=false",
                fullDescription={"0": "<p>Complete description.</p>"},
            )
        )
        assert job is not None
        assert job.url == "https://app.beehire.com/invite/custom?form=false"
        assert job.description == "<p>Complete description.</p>"

    def test_location_fallback_and_missing_identity(self):
        job = _parse_job(_campaign(location={"city": "Geneva", "country": "Switzerland"}))
        assert job is not None
        assert job.locations == ["Geneva, Switzerland"]
        assert _parse_job(_campaign(inviteKey=None, inviteLink=None)) is None


async def test_discover_maps_public_campaigns():
    payload = {
        "campaigns": [
            _campaign(),
            _campaign(
                id="6a61c9856635f4eccf26c3a6",
                inviteKey="LQmcmkvH6",
                title={"0": "Finance Internship"},
                location={"name": "Geneva, Switzerland"},
                details={"contract": {"type": "contractType_internship"}},
            ),
        ],
        "employerBranding": {"careerPage": {"slug": SLUG}},
    }
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert len(jobs) == 2
    assert jobs[1].locations == ["Geneva, Switzerland"]
    assert normalize_employment_type(jobs[1].employment_type) == "internship"


async def test_discover_rejects_invalid_payload():
    async with httpx.AsyncClient(transport=_transport({"jobs": []})) as client:
        try:
            await discover({"board_url": BOARD_URL, "metadata": {}}, client)
        except ValueError as exc:
            assert "campaigns" in str(exc)
        else:
            raise AssertionError("invalid Beehire payload should fail")


async def test_discover_marks_partial_invalid_and_duplicate_campaigns_truncated():
    valid = _campaign()
    payload = {
        "campaigns": [valid, "not-an-object", _campaign(inviteKey=None), valid.copy()],
        "employerBranding": {"careerPage": {"slug": SLUG}},
    }
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        result = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert result.urls == {"https://app.beehire.com/invite/6L-oDP2wk"}
    assert result.jobs_by_url is not None
    assert set(result.jobs_by_url) == result.urls


async def test_discover_rejects_nonempty_all_invalid_campaigns():
    payload = {
        "campaigns": ["not-an-object", _campaign(inviteKey=None)],
        "employerBranding": {"careerPage": {"slug": SLUG}},
    }
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        with pytest.raises(ValueError, match="no valid jobs"):
            await discover({"board_url": BOARD_URL, "metadata": {}}, client)


async def test_discover_marks_raw_campaign_count_above_cap_truncated(monkeypatch):
    monkeypatch.setattr(beehire, "MAX_JOBS", 1)
    payload = {
        "campaigns": [
            _campaign(),
            _campaign(inviteKey="LQmcmkvH6", title={"0": "Finance Internship"}),
        ],
        "employerBranding": {"careerPage": {"slug": SLUG}},
    }
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        result = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert isinstance(result, MonitorResult)
    assert result.truncated is True
    assert len(result.urls) == 2


async def test_discover_accepts_empty_campaign_inventory():
    payload = {
        "campaigns": [],
        "employerBranding": {"careerPage": {"slug": SLUG}},
    }
    async with httpx.AsyncClient(transport=_transport(payload)) as client:
        result = await discover({"board_url": BOARD_URL, "metadata": {}}, client)

    assert result == []


async def test_can_handle_verifies_api_and_accepts_empty_board():
    async with httpx.AsyncClient(
        transport=_transport({"campaigns": [], "employerBranding": {}})
    ) as client:
        assert await can_handle(BOARD_URL, client) == {"slug": SLUG, "jobs": 0}


async def test_can_handle_rejects_failed_and_unrelated_boards():
    async with httpx.AsyncClient(
        transport=_transport({"error": "not found"}, status_code=404)
    ) as client:
        assert await can_handle(BOARD_URL, client) is None
        assert await can_handle("https://example.com/careers", client) is None


def test_registered_as_rich_monitor():
    monitor = next(item for item in _REGISTRY if item.name == "beehire")
    assert monitor.rich is True
    assert monitor.cost == 10
    assert monitor.save_raw is not None
