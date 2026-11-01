from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from src.core.monitors.taleo import can_handle
from src.core.monitors.taleo import discover as taleo_discover
from src.core.monitors.taleo_enterprise_legacy import board_from_url, discover, parse_listing
from src.workspace._compat import auto_scraper_type, detect_ats_from_url
from src.workspace.career_discover import _scan_ats_urls_in_html

BOARD = "https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en"
OFFICIAL_SEARCH = (
    "https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/"
    "jobsearch.ftl?lang=en&portal=321050%E2%80%A6"
)
FIXTURE = (
    Path(__file__).parent / "fixtures" / "taleo_enterprise" / "jumeirah_listing.html"
).read_text()


def test_jumeirah_official_legacy_listing_complete() -> None:
    board = board_from_url(BOARD)
    assert board is not None
    assert board_from_url(OFFICIAL_SEARCH) == board
    jobs = parse_listing(FIXTURE, board)
    assert len(jobs) == len({job["url"] for job in jobs}) == 19
    assert jobs[0]["url"] == (
        "https://dubaiholding.taleo.net/careersection/jum_ext_quickapply/"
        "jobdetail.ftl?job=26000037&lang=en"
    )
    assert jobs[0]["title"] == "Florist - Housekeeping - Jumeirah Muscat Bay"
    assert jobs[0]["locations"] == ["Oman-Muscat"]
    assert jobs[0]["date_posted"] == "Oct 11, 2026"


@pytest.mark.parametrize(
    "url",
    [
        "https://foreign.example/careersection/jum_ext_quickapply/joblist.ftl?lang=en",
        "https://dubaiholding.tbe.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en",
        "http://dubaiholding.taleo.net/careersection/jum_ext_quickapply/joblist.ftl?lang=en",
        BOARD + "&next=https://evil.example",
    ],
)
def test_legacy_identity_rejects_untrusted_urls(url: str) -> None:
    assert board_from_url(url) is None


def test_legacy_listing_fails_closed_on_count_or_duplicate() -> None:
    board = board_from_url(BOARD)
    assert board is not None
    with pytest.raises(ValueError, match="incomplete"):
        parse_listing(FIXTURE.replace("19 jobs found", "20 jobs found", 1), board)
    with pytest.raises(ValueError, match="repeats"):
        parse_listing(FIXTURE.replace("26000036", "26000037", 1), board)


@pytest.mark.asyncio
async def test_legacy_offline_discovery_reuses_public_page() -> None:
    board = board_from_url(BOARD)
    assert board is not None

    def handle(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == BOARD
        return httpx.Response(200, text=FIXTURE)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
        urls = await discover(board, client)
        routed_urls = await taleo_discover(
            {"board_url": BOARD, "metadata": {"enterprise_section": BOARD}}, client
        )
    assert len(urls) == 19
    assert urls == routed_urls


@pytest.mark.asyncio
async def test_legacy_workspace_detection_chooses_enterprise_detail() -> None:
    assert detect_ats_from_url(BOARD) == "taleo"
    assert detect_ats_from_url(OFFICIAL_SEARCH) == "taleo"
    found = await can_handle(BOARD)
    assert found == {"enterprise_section": BOARD, "jobs": "unknown"}
    assert await can_handle(OFFICIAL_SEARCH) == found
    assert auto_scraper_type("taleo", found) == ("taleo", None)
    assert auto_scraper_type("taleo", {"host": "phe.tbe.taleo.net"}) == ("json-ld", None)
    embedded = _scan_ats_urls_in_html(f'<a href="{OFFICIAL_SEARCH}">Bali, China, Oman, Bahrain</a>')
    assert any(link.url == OFFICIAL_SEARCH for link in embedded)
