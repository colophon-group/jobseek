from __future__ import annotations

import httpx
import pytest

from src.core.monitors import all_monitor_types
from src.core.monitors.papa_johns import (
    BOARD_URL,
    _page_url,
    _parse_listing,
    can_handle,
    discover,
)
from src.workspace._compat import auto_scraper_type, detect_ats_from_url


def _job_url(index: int) -> str:
    return f"https://jobs.papajohns.com/job/{34000 + index}/role-{index}/"


def _listing(
    *,
    total: int,
    pages: int,
    indexes: list[int],
    featured: list[int] | None = None,
) -> str:
    pagination = "".join(
        f'<a href="/jobs/?page_jobs={page}">{page}</a>' for page in range(1, pages + 1)
    )
    jobs = "".join(f'<a class="job-result" href="{_job_url(i)}">Role {i}</a>' for i in indexes)
    featured_jobs = "".join(
        f'<a class="featured-job" href="{_job_url(i)}">Featured {i}</a>' for i in (featured or [])
    )
    return f"""
    <html><body>
      <h1>Jobs at Papa Johns</h1>
      <h2>Found {total:,} jobs at Papa Johns</h2>
      <nav>{pagination}</nav>
      <main>{jobs}</main>
      <aside>{featured_jobs}</aside>
    </body></html>
    """


class TestIdentity:
    def test_registered_and_auto_configured(self) -> None:
        assert "papa_johns" in all_monitor_types()
        assert detect_ats_from_url(BOARD_URL) == "papa_johns"
        assert auto_scraper_type("papa_johns") == ("json-ld", None)

    @pytest.mark.parametrize(
        "url",
        [
            "http://jobs.papajohns.com/jobs/",
            "https://jobs.papajohns.com/jobs/?page_jobs=2",
            "https://jobs.papajohns.com/jobs/#openings",
            "https://jobs.papajohns.com.evil.test/jobs/",
            "https://jobs.papajohns.com/job/34113/role/",
        ],
    )
    def test_rejects_noncanonical_board_urls(self, url: str) -> None:
        assert detect_ats_from_url(url) != "papa_johns"


class TestParser:
    def test_extracts_count_pages_and_canonical_jobs(self) -> None:
        parsed = _parse_listing(
            _listing(total=3, pages=2, indexes=[1, 2], featured=[1]),
        )

        assert parsed.total_jobs == 3
        assert parsed.total_pages == 2
        assert parsed.urls == {_job_url(1), _job_url(2)}

    def test_rejects_challenge_or_broken_inventory(self) -> None:
        with pytest.raises(ValueError, match="inventory count"):
            _parse_listing("<html><title>Forbidden</title></html>")
        with pytest.raises(ValueError, match="no canonical job URLs"):
            _parse_listing("<h2>Found 2 jobs at Papa Johns</h2>")

    def test_page_url_uses_provider_parameter(self) -> None:
        assert _page_url(1) == BOARD_URL
        assert _page_url(3) == f"{BOARD_URL}?page_jobs=3"


class TestMonitor:
    async def test_paginates_complete_inventory_and_dedupes_featured_jobs(self) -> None:
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            page = int(request.url.params.get("page_jobs", "1"))
            if page == 1:
                body = _listing(total=3, pages=2, indexes=[1, 2], featured=[1])
            elif page == 2:
                body = _listing(total=3, pages=2, indexes=[3], featured=[1])
            else:  # pragma: no cover - proves the page bound
                raise AssertionError(f"unexpected page {page}")
            return httpx.Response(200, text=body, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {_job_url(1), _job_url(2), _job_url(3)}
        assert seen == [BOARD_URL, f"{BOARD_URL}?page_jobs=2"]

    async def test_fails_closed_on_incomplete_inventory(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_listing(total=3, pages=1, indexes=[1, 2]),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="discovered 2 jobs, expected 3"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_probe_returns_exact_host_evidence_when_origin_is_blocked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(403, text="Forbidden", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(BOARD_URL, client)

        assert result == {
            "host": "jobs.papajohns.com",
            "proxy_required": True,
        }
