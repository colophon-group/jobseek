from __future__ import annotations

import math

import httpx
import pytest

from src.core.monitors import BoardGoneError, all_monitor_types, universia
from src.core.monitors.universia import _slug_from_url, can_handle, discover
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.commands.crawl import _MONITOR_CONFIG_HINTS
from src.workspace.commands.help import MONITOR_CARDS

SLUG = "santandercolombia"
BOARD_ID = "80aad5c7-634e-4127-a715-6984ff63555b"
BOARD_URL = f"https://jobboard.universia.net/{SLUG}"


def _config_payload(*, slug: str = SLUG, board_id: str = BOARD_ID) -> dict:
    return {
        "slug": slug,
        "entity": {"id": board_id, "entityType": "company"},
        "languages": ["es-CO"],
    }


def _job(index: int = 1, *, board_id: str = BOARD_ID) -> dict:
    job_id = f"00000000-0000-4000-8000-{index:012d}"
    return {
        "identifier": job_id,
        "boards": [board_id],
        "status": "published",
        "url": (
            f"https://www.universia.net/co/empleo/{job_id}/role-{index}.html"
            f"?referer={board_id}&entityid={board_id}"
        ),
        "title": f"Role {index}",
        "description": "<p>Complete responsibilities.</p>",
        "requirements": "<ul><li>Relevant experience</li></ul>",
        "incentiveCompensation": "<p>Relevant degree</p>",
        "jobLocation": {
            "address": {
                "streetAddress": "Bogot\u00e1, Colombia",
                "addressLocality": "Bogot\u00e1",
                "addressRegion": "Bogot\u00e1",
                "addressCountry": "CO",
            }
        },
        "employmentType": "full-time",
        "jobLocationType": "hybrid",
        "datePosted": "2026-08-27T16:46:24+00:00",
        "validThrough": "2026-09-26T00:00:00+00:00",
        "postingType": "job",
        "educationalLevel": "intermediate_tech",
        "totalJobOpenings": 1,
        "role": {"name": "Operations Manager"},
        "contractType": {"name": "Contrato indefinido"},
    }


def _listing_payload(rows: list[dict], *, total: int | None = None, offset: int = 0) -> dict:
    count = len(rows) if total is None else total
    return {
        "offset": offset,
        "limit": universia.PAGE_SIZE,
        "size": len(rows),
        "total": count,
        "totalPages": math.ceil(count / universia.PAGE_SIZE),
        "results": rows,
    }


def _transport(rows_by_offset: dict[int, list[dict]], *, total: int) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if "/config/" in request.url.path:
            assert request.url.params["status"] == "published"
            return httpx.Response(200, json=_config_payload(), request=request)
        assert request.url.path.endswith("/api/job-posting")
        assert request.url.params["boards"] == BOARD_ID
        assert request.url.params.get_list("postingType") == ["job", "internship"]
        offset = int(request.url.params["offset"])
        return httpx.Response(
            200,
            json=_listing_payload(rows_by_offset[offset], total=total, offset=offset),
            request=request,
        )

    return httpx.MockTransport(handler)


class TestIdentity:
    @pytest.mark.parametrize("url", [BOARD_URL, BOARD_URL + "/"])
    def test_extracts_exact_board_slug(self, url: str):
        assert _slug_from_url(url) == SLUG

    @pytest.mark.parametrize(
        "url",
        [
            f"http://jobboard.universia.net/{SLUG}",
            f"https://user@jobboard.universia.net/{SLUG}",
            f"https://jobboard.universia.net:444/{SLUG}",
            f"{BOARD_URL}/jobs",
            f"{BOARD_URL}?country=co",
            f"{BOARD_URL}#jobs",
            f"https://jobboard.universia.net.evil.test/{SLUG}",
        ],
    )
    def test_rejects_untrusted_or_filtered_urls(self, url: str):
        assert _slug_from_url(url) is None
        assert detect_ats_from_url(url) != "universia"


class TestMonitor:
    async def test_returns_complete_rich_job(self):
        async with httpx.AsyncClient(transport=_transport({0: [_job()]}, total=1)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert len(result) == 1
        job = result[0]
        assert job.title == "Role 1"
        assert job.description == (
            "<p>Complete responsibilities.</p>"
            "<h3>Requirements</h3><ul><li>Relevant experience</li></ul>"
            "<h3>Education</h3><p>Relevant degree</p>"
        )
        assert job.locations == ["Bogot\u00e1, Colombia"]
        assert job.employment_type == "full_time"
        assert job.job_location_type == "hybrid"
        assert job.date_posted == "2026-08-27T16:46:24+00:00"
        assert job.language == "es"
        assert job.extras == {
            "qualifications": ("<ul><li>Relevant experience</li></ul><p>Relevant degree</p>"),
            "valid_through": "2026-09-26T00:00:00+00:00",
        }
        assert job.metadata == {
            "universia_job_id": "00000000-0000-4000-8000-000000000001",
            "universia_board_id": BOARD_ID,
            "posting_type": "job",
            "educational_level": "intermediate_tech",
            "total_job_openings": 1,
            "role": "Operations Manager",
            "contract_type": "Contrato indefinido",
        }
        assert job.source_identity == (f"universia:{BOARD_ID}:00000000-0000-4000-8000-000000000001")

    async def test_paginates_and_checks_exact_total(self, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setattr(universia, "PAGE_SIZE", 2)
        transport = _transport({0: [_job(1), _job(2)], 2: [_job(3)]}, total=3)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover({"board_url": BOARD_URL}, client)
        assert {job.metadata["universia_job_id"] for job in result} == {
            "00000000-0000-4000-8000-000000000001",
            "00000000-0000-4000-8000-000000000002",
            "00000000-0000-4000-8000-000000000003",
        }

    async def test_explicit_empty_inventory_is_authoritative(self):
        async with httpx.AsyncClient(transport=_transport({0: []}, total=0)) as client:
            assert await discover({"board_url": BOARD_URL}, client) == []

    async def test_rejects_cross_board_rows(self):
        row = _job(board_id="11111111-1111-4111-8111-111111111111")
        transport = _transport({0: [row]}, total=1)
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="board-scoped identity"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_rejects_untrusted_public_job_url(self):
        row = _job()
        row["url"] += f"&referer={BOARD_ID}"
        async with httpx.AsyncClient(transport=_transport({0: [row]}, total=1)) as client:
            with pytest.raises(ValueError, match="untrusted public URL"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_configured_board_id_must_match_live_identity(self):
        async with httpx.AsyncClient(transport=_transport({0: []}, total=0)) as client:
            with pytest.raises(ValueError, match="configured board_id"):
                await discover(
                    {
                        "board_url": BOARD_URL,
                        "metadata": {"board_id": "11111111-1111-4111-8111-111111111111"},
                    },
                    client,
                )

    async def test_malformed_pagination_fails_not_empty(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if "/config/" in request.url.path:
                return httpx.Response(200, json=_config_payload(), request=request)
            payload = _listing_payload([], total=1)
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="expected 1"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_terminal_config_status_is_board_gone(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(BoardGoneError):
                await discover({"board_url": BOARD_URL}, client)


class TestProbe:
    async def test_direct_url_detects_without_network(self):
        assert await can_handle(BOARD_URL) == {"slug": SLUG}

    async def test_api_verification_returns_identity_language_and_count(self):
        async with httpx.AsyncClient(transport=_transport({0: [_job()]}, total=1)) as client:
            assert await can_handle(BOARD_URL, client) == {
                "slug": SLUG,
                "board_id": BOARD_ID,
                "language": "es",
                "jobs": 1,
            }

    async def test_gone_board_does_not_detect(self):
        transport = httpx.MockTransport(lambda request: httpx.Response(404, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) is None


def test_runtime_and_workspace_integration():
    assert "universia" in all_monitor_types()
    assert detect_ats_from_url(BOARD_URL) == "universia"
    assert auto_scraper_type("universia") == ("skip", None)
    assert "universia" in MONITOR_CARDS
    assert "universia" in _MONITOR_CONFIG_HINTS
