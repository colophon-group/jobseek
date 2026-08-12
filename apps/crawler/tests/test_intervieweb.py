from __future__ import annotations

import json
from urllib.parse import parse_qs

import httpx
import pytest

from src.core.monitors import all_monitor_types
from src.core.monitors.intervieweb import (
    _canonical_job_url,
    _page_count,
    can_handle,
    discover,
)
from src.redis_queue import _KNOWN_ATS_DOMAINS
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

BOARD_URL = "https://acme.intervieweb.it/en/career"
AJAX_URL = (
    "https://acme.intervieweb.it/app.php?opmode=guest&module=newcareer&ajax=1"
    "&IdAzienda=42&CSRFToken=token&CSRFHash=hash"
)
SECTION = "abc123-def"


def _job_link(slug: str, language: str = "en") -> str:
    return f"https://acme.intervieweb.it/jobs/{slug}/{language}/"


def _page(*slugs: str, current: int = 1, pages: int = 1, ajax_url: str = AJAX_URL) -> str:
    links = "".join(
        f'<a href="{_job_link(slug)}"><h3>{slug}</h3></a><a href="{_job_link(slug)}">Apply</a>'
        for slug in slugs
    )
    return f"""
      <html><body>
        <input type="hidden" id="url-for-announces" value="{ajax_url.replace("&", "&amp;")}">
        <div id="tab-annunci">{links}</div>
        <div>Page {current} of {pages}</div>
        <script>
          function researchAnnounces(pageNumber, order) {{
            $.ajax({{
              url: $('#url-for-announces').val(),
              type: "POST",
              data: {{'act1': 'vacancyListCareer', 'section': '{SECTION}'}}
            }});
          }}
        </script>
      </body></html>
    """


class TestParsing:
    def test_canonical_job_url(self):
        assert _canonical_job_url("/jobs/software-engineer-123/en/", BOARD_URL) == (
            "https://acme.intervieweb.it/jobs/software-engineer-123/en/"
        )

    @pytest.mark.parametrize(
        "url",
        [
            "https://evil.example/jobs/software-engineer-123/en/",
            "https://acme.intervieweb.it/career",
            "https://acme.intervieweb.it/jobs/software-engineer-123/english/",
            "http://acme.intervieweb.it/jobs/software-engineer-123/en/",
        ],
    )
    def test_rejects_non_job_or_cross_origin_url(self, url: str):
        assert _canonical_job_url(url, BOARD_URL) is None

    def test_page_count_uses_advertised_maximum(self):
        assert _page_count("Page 1 of 3 ... Page 1 of 3") == 3

    def test_page_count_defaults_to_one(self):
        assert _page_count("No pagination") == 1


class TestMonitor:
    async def test_discovers_all_post_paginated_jobs(self):
        seen: list[tuple[str, str]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append((request.method, str(request.url)))
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=_page("role-one-101", "role-two-102", pages=2),
                    request=request,
                )

            form = parse_qs(request.content.decode())
            assert form["act1"] == ["vacancyListCareer"]
            assert form["section"] == [SECTION]
            assert form["page"] == ["2"]
            assert request.headers["x-requested-with"] == "XMLHttpRequest"
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": _page("role-three-103", current=2, pages=2),
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover({"board_url": BOARD_URL}, client)

        assert result == {
            _job_link("role-one-101"),
            _job_link("role-two-102"),
            _job_link("role-three-103"),
        }
        assert [method for method, _url in seen] == ["GET", "POST"]

    async def test_single_page_empty_listing_is_authoritative(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=_page(), request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await discover({"board_url": BOARD_URL}, client) == set()

    async def test_advertised_page_must_not_be_empty(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=_page("role-one-101", pages=2),
                    request=request,
                )
            return httpx.Response(
                200,
                json={"success": True, "data": _page(current=2, pages=2)},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="advertised page 2 returned no jobs"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_pagination_failure_is_not_treated_as_empty(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=_page("role-one-101", pages=2),
                    request=request,
                )
            return httpx.Response(
                200,
                text=json.dumps({"success": False, "data": ""}),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="reported failure"):
                await discover({"board_url": BOARD_URL}, client)

    async def test_rejects_cross_origin_pagination_endpoint(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_page(
                    "role-one-101",
                    pages=2,
                    ajax_url="https://evil.example/app.php?module=newcareer",
                ),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="changed origin"):
                await discover({"board_url": BOARD_URL}, client)


class TestDetectionAndCompatibility:
    async def test_probe_verifies_and_counts_all_pages(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text=_page("role-one-101", pages=2),
                    request=request,
                )
            return httpx.Response(
                200,
                json={
                    "success": True,
                    "data": _page("role-two-102", current=2, pages=2),
                },
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(BOARD_URL, client) == {
                "provider": "intervieweb",
                "jobs": 2,
                "pages": 2,
            }

    async def test_probe_rejects_non_provider_page(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>Careers</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) is None

    def test_workspace_compatibility(self):
        assert "intervieweb" in all_monitor_types()
        assert "intervieweb" in _KNOWN_ATS_DOMAINS
        assert detect_ats_from_url(BOARD_URL) == "intervieweb"
        assert auto_scraper_type("intervieweb", {}) == ("json-ld", None)
