from __future__ import annotations

import httpx

from src.core.monitors.talentbrew import (
    _page_url,
    _parse_page,
    can_handle,
    discover,
)


def _html(
    *,
    total_jobs: int = 3,
    total_pages: int = 1,
    current_page: int = 1,
    links: list[str] | None = None,
    extra: str = "",
    prefix: str = "",
    search_results_tag: str = "section",
    results_list_tag: str = "section",
    ajax_url: str | None = None,
) -> str:
    items = "\n".join(
        f'<li><a href="{href}" data-job-id="{idx}">Job {idx}</a><br></li>'
        for idx, href in enumerate(links or [], start=1)
    )
    return f"""
    <html>
      <head><script src="//tbcdn.talentbrew.com/js/client/search.js"></script></head>
      <body>
        {prefix}
        <{search_results_tag} id="search-results"
          data-total-job-results="{total_jobs}"
          data-total-pages="{total_pages}"
          data-current-page="{current_page}"
          data-records-per-page="14"
          {f'data-ajax-url="{ajax_url}"' if ajax_url else ""}>
          <{results_list_tag} id="search-results-list">
            <ul>{items}</ul>
          </{results_list_tag}>
        </{search_results_tag}>
        {extra}
      </body>
    </html>
    """


class TestParsePage:
    def test_extracts_search_result_links_only(self):
        html = _html(
            total_jobs=2,
            total_pages=1,
            links=["/job/toronto/engineer/4853/1"],
            extra='<a href="/job/related/not-a-result/4853/2" data-job-id="2">Related</a>',
        )

        parsed = _parse_page(html, "https://careers.example.com/search-jobs")

        assert parsed.total_jobs == 2
        assert parsed.total_pages == 1
        assert parsed.current_page == 1
        assert parsed.records_per_page == 14
        assert parsed.urls == {"https://careers.example.com/job/toronto/engineer/4853/1"}

    def test_extracts_metadata_from_div_containers(self):
        html = _html(
            total_jobs=1027,
            total_pages=65,
            links=["/job/london/analyst/13015/1"],
            search_results_tag="div",
            results_list_tag="div",
        )

        parsed = _parse_page(html, "https://search.jobs.example.com/search-jobs")

        assert parsed.total_jobs == 1027
        assert parsed.total_pages == 65
        assert parsed.urls == {"https://search.jobs.example.com/job/london/analyst/13015/1"}

    def test_falls_back_to_data_job_id_when_results_list_missing(self):
        html = """
        <html>
          <head><script src="//tbcdn.talentbrew.com/js/client/search.js"></script></head>
          <body>
            <section id="search-results" data-total-job-results="1"></section>
            <a href="/job/toronto/engineer/4853/1" data-job-id="1">Engineer</a>
          </body>
        </html>
        """

        parsed = _parse_page(html, "https://careers.example.com/search-jobs")

        assert parsed.urls == {"https://careers.example.com/job/toronto/engineer/4853/1"}


class TestPageUrl:
    def test_adds_page_query(self):
        assert (
            _page_url("https://careers.example.com/search-jobs", 2)
            == "https://careers.example.com/search-jobs?p=2"
        )

    def test_preserves_existing_query(self):
        assert (
            _page_url("https://careers.example.com/search-jobs?location=Canada", 3)
            == "https://careers.example.com/search-jobs?location=Canada&p=3"
        )

    def test_replaces_existing_page_query(self):
        assert (
            _page_url("https://careers.example.com/search-jobs?location=Canada&p=2", 4)
            == "https://careers.example.com/search-jobs?location=Canada&p=4"
        )


class TestCanHandle:
    async def test_detects_talentbrew_search_page(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_html(
                    total_jobs=28,
                    total_pages=2,
                    links=["/job/toronto/engineer/4853/1"],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://careers.example.com/search-jobs", client)

        assert result == {"urls": 1, "jobs": 28, "pages": 2}

    async def test_detects_search_results_after_large_cms_payload(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text=_html(
                    total_jobs=1295,
                    total_pages=87,
                    links=["/job/bay-city/field-technician/4673/95591908288"],
                    prefix=f"<section>{'x' * 1_200_000}</section>",
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle("https://jobs.example.com/search-jobs", client)

        assert result == {"urls": 1, "jobs": 1295, "pages": 87}

    async def test_rejects_non_talentbrew_page(self):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(
                200,
                text='<section id="search-results" data-total-job-results="1"></section>',
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle("https://example.com/jobs", client) is None


class TestDiscover:
    async def test_uses_ajax_results_endpoint(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.path == "/search-jobs/results":
                assert request.url.params.get("CurrentPage") == "1"
                assert request.url.params.get("RecordsPerPage") == "1000"
                return httpx.Response(
                    200,
                    json={
                        "results": """
                        <a href="/job/toronto/engineer/4853/1" data-job-id="1">Engineer</a>
                        <a href="/job/montreal/manager/4853/2" data-job-id="2">Manager</a>
                        <a href="/job/vancouver/designer/4853/3" data-job-id="3">Designer</a>
                        """
                    },
                )
            return httpx.Response(
                200,
                text=_html(
                    total_jobs=3,
                    total_pages=1,
                    links=["/job/toronto/engineer/4853/1"],
                    ajax_url="/search-jobs/results",
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://careers.example.com/search-jobs", "metadata": {}}
            urls = await discover(board, client)

        assert urls == {
            "https://careers.example.com/job/toronto/engineer/4853/1",
            "https://careers.example.com/job/montreal/manager/4853/2",
            "https://careers.example.com/job/vancouver/designer/4853/3",
        }
        assert seen == [
            "https://careers.example.com/search-jobs",
            "https://careers.example.com/search-jobs/results?ActiveFacetID=0&CurrentPage=1&RecordsPerPage=1000&Distance=50&ShowRadius=False&CustomFacetName=&FacetTerm=&FacetType=0&SearchResultsModuleName=Search+Results&SearchFiltersModuleName=Search+Filters&SortCriteria=0&SortDirection=0&SearchType=5&PostalCode=",
        ]

    async def test_ajax_paginates_with_configured_page_size(self):
        seen_pages: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/search-jobs/results":
                page = request.url.params.get("CurrentPage", "1")
                seen_pages.append(page)
                return httpx.Response(
                    200,
                    json={
                        "results": (
                            f'<a href="/job/city/role-{page}-a/4853/{page}1" '
                            f'data-job-id="{page}1">A</a>'
                            f'<a href="/job/city/role-{page}-b/4853/{page}2" '
                            f'data-job-id="{page}2">B</a>'
                        )
                    },
                )
            return httpx.Response(
                200,
                text=_html(
                    total_jobs=4,
                    total_pages=2,
                    links=["/job/city/role-1-a/4853/11"],
                    ajax_url="/search-jobs/results",
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.example.com/search-jobs",
                "metadata": {"page_size": 2},
            }
            urls = await discover(board, client)

        assert len(urls) == 4
        assert seen_pages == ["1", "2"]

    async def test_paginates_search_results(self):
        seen: list[str] = []

        def handler(request: httpx.Request) -> httpx.Response:
            seen.append(str(request.url))
            if request.url.params.get("p") == "2":
                return httpx.Response(
                    200,
                    text=_html(
                        total_jobs=3,
                        total_pages=2,
                        current_page=2,
                        links=["/job/vancouver/designer/4853/3"],
                    ),
                )
            return httpx.Response(
                200,
                text=_html(
                    total_jobs=3,
                    total_pages=2,
                    current_page=1,
                    links=[
                        "/job/toronto/engineer/4853/1",
                        "/job/montreal/manager/4853/2",
                    ],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {"board_url": "https://careers.example.com/search-jobs", "metadata": {}}
            urls = await discover(board, client)

        assert urls == {
            "https://careers.example.com/job/toronto/engineer/4853/1",
            "https://careers.example.com/job/montreal/manager/4853/2",
            "https://careers.example.com/job/vancouver/designer/4853/3",
        }
        assert seen == [
            "https://careers.example.com/search-jobs",
            "https://careers.example.com/search-jobs?p=2",
        ]

    async def test_max_pages_caps_pagination(self):
        def handler(request: httpx.Request) -> httpx.Response:
            page = int(request.url.params.get("p", "1"))
            return httpx.Response(
                200,
                text=_html(
                    total_jobs=42,
                    total_pages=3,
                    current_page=page,
                    links=[f"/job/city/role-{page}/4853/{page}"],
                ),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            board = {
                "board_url": "https://careers.example.com/search-jobs",
                "metadata": {"max_pages": 2},
            }
            urls = await discover(board, client)

        assert urls == {
            "https://careers.example.com/job/city/role-1/4853/1",
            "https://careers.example.com/job/city/role-2/4853/2",
        }
