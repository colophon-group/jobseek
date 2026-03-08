from __future__ import annotations

import json

import httpx

from src.core.monitors.join import (
    _build_metadata,
    _slug_from_url,
    can_handle,
    discover,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _join_page(items: list[dict], page: int = 1, page_count: int = 1) -> dict:
    """Build a JOIN __NEXT_DATA__ blob."""
    return {
        "props": {
            "pageProps": {
                "initialState": {
                    "company": {"domain": "acme"},
                    "jobs": {
                        "items": items,
                        "pagination": {
                            "page": page,
                            "pageCount": page_count,
                            "total": page_count * len(items),
                        },
                    },
                }
            }
        }
    }


SAMPLE_JOBS = [
    {
        "id": 101,
        "idParam": "101-software-engineer",
        "title": "Software Engineer",
        "createdAt": "2026-01-15T10:00:00Z",
        "workplaceType": "HYBRID",
        "employmentType": {"name": "Employee"},
        "city": {"cityName": "Berlin", "countryName": "Germany"},
        "category": {"name": "Engineering"},
        "salaryAmountFrom": {"amount": 6000000, "currency": "EUR"},
        "salaryAmountTo": {"amount": 8000000, "currency": "EUR"},
        "salaryFrequency": "PER_YEAR",
    },
    {
        "id": 102,
        "idParam": "102-intern-marketing",
        "title": "Marketing Intern",
        "createdAt": "2026-02-01T10:00:00Z",
        "workplaceType": "ONSITE",
        "employmentType": {"name": "Internship"},
        "city": {"cityName": "Zurich", "countryName": "Switzerland"},
        "category": {"name": "Marketing"},
    },
]


def _html(data: dict) -> str:
    payload = json.dumps(data)
    return (
        f'<html><body><script id="__NEXT_DATA__"'
        f' type="application/json">{payload}</script></body></html>'
    )


def _mock_transport(html: str, status: int = 200):
    def handler(request):
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# _slug_from_url
# ---------------------------------------------------------------------------


class TestSlugFromUrl:
    def test_standard_url(self):
        assert _slug_from_url("https://join.com/companies/acme") == "acme"

    def test_with_trailing_slash(self):
        assert _slug_from_url("https://join.com/companies/acme/") == "acme"

    def test_www_prefix(self):
        assert _slug_from_url("https://www.join.com/companies/acme") == "acme"

    def test_non_join_domain(self):
        assert _slug_from_url("https://example.com/companies/acme") is None

    def test_no_companies_path(self):
        assert _slug_from_url("https://join.com/about") is None

    def test_hyphenated_slug(self):
        assert _slug_from_url("https://join.com/companies/my-company") == "my-company"


# ---------------------------------------------------------------------------
# _build_metadata
# ---------------------------------------------------------------------------


class TestBuildMetadata:
    def test_contains_required_keys(self):
        meta = _build_metadata("acme")
        assert meta["path"] == "props.pageProps.initialState.jobs.items"
        assert "acme" in meta["url_template"]
        assert "{idParam}" in meta["url_template"]
        assert "pagination" in meta
        assert "fields" in meta
        assert "base_salary" in meta

    def test_url_template_has_slug(self):
        meta = _build_metadata("my-co")
        assert meta["url_template"] == "https://join.com/companies/my-co/{idParam}"


# ---------------------------------------------------------------------------
# discover
# ---------------------------------------------------------------------------


class TestDiscover:
    async def test_basic_extraction(self):
        data = _join_page(SAMPLE_JOBS)
        html = _html(data)
        board = {
            "board_url": "https://join.com/companies/acme",
            "metadata": {"slug": "acme"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)

        assert isinstance(result, list)
        assert len(result) == 2

        eng = next(j for j in result if j.title == "Software Engineer")
        assert eng.url == "https://join.com/companies/acme/101-software-engineer"
        assert eng.locations == ["Berlin"]
        assert eng.employment_type == "Full-time"
        assert eng.job_location_type == "hybrid"
        assert eng.date_posted == "2026-01-15T10:00:00Z"
        assert eng.base_salary == {
            "min": 60000,
            "max": 80000,
            "currency": "EUR",
            "unit": "year",
        }
        assert eng.metadata["category"] == "Engineering"

        intern = next(j for j in result if j.title == "Marketing Intern")
        assert intern.employment_type == "Intern"
        assert intern.job_location_type == "onsite"
        assert intern.locations == ["Zurich"]
        assert intern.base_salary is None

    async def test_slug_derived_from_url(self):
        """When slug is not in metadata, derive from board_url."""
        data = _join_page(SAMPLE_JOBS[:1])
        html = _html(data)
        board = {
            "board_url": "https://join.com/companies/acme",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)
        assert len(result) == 1
        assert "acme" in result[0].url

    async def test_missing_slug_returns_empty(self):
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {},
        }
        async with httpx.AsyncClient(transport=_mock_transport("")) as client:
            result = await discover(board, client)
        assert result == []

    async def test_pagination(self):
        """Multi-page boards merge all items."""
        page1_jobs = [
            {"id": 1, "idParam": "1-job-a", "title": "Job A"},
            {"id": 2, "idParam": "2-job-b", "title": "Job B"},
        ]
        page2_jobs = [
            {"id": 3, "idParam": "3-job-c", "title": "Job C"},
        ]

        def handler(request: httpx.Request):
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(str(request.url))
            qs = parse_qs(parsed.query)
            page = int(qs.get("page", ["1"])[0])
            if page == 1:
                data = _join_page(page1_jobs, page=1, page_count=2)
            else:
                data = _join_page(page2_jobs, page=2, page_count=2)
            return httpx.Response(200, text=_html(data))

        transport = httpx.MockTransport(handler)
        board = {
            "board_url": "https://join.com/companies/acme",
            "metadata": {"slug": "acme"},
        }
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(board, client)

        assert len(result) == 3
        titles = {j.title for j in result}
        assert titles == {"Job A", "Job B", "Job C"}


# ---------------------------------------------------------------------------
# can_handle
# ---------------------------------------------------------------------------


class TestCanHandle:
    async def test_join_url_with_jobs(self):
        data = _join_page(SAMPLE_JOBS)
        html = _html(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://join.com/companies/acme", client)
        assert result is not None
        assert result["slug"] == "acme"
        assert result["jobs"] == 2

    async def test_non_join_url(self):
        async with httpx.AsyncClient(transport=_mock_transport("")) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_join_url_no_client(self):
        result = await can_handle("https://join.com/companies/acme")
        assert result == {"slug": "acme"}

    async def test_join_url_no_next_data(self):
        html = "<html><body>No Next.js</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://join.com/companies/acme", client)
        assert result is None

    async def test_join_url_no_jobs_path(self):
        """join.com page but data at unexpected path."""
        data = {"props": {"pageProps": {"other": "stuff"}}}
        html = _html(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://join.com/companies/acme", client)
        assert result is None
