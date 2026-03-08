from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx

from src.core.monitors import DiscoveredJob
from src.core.monitors.nextdata import (
    _add_query_param,
    _build_url,
    _extract_field,
    _extract_next_data,
    _extract_salary,
    _resolve_field,
    _resolve_path,
    can_handle,
    discover,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

NEXT_DATA = {
    "props": {
        "pageProps": {
            "positions": [
                {
                    "id": "abc-123",
                    "text": "Engineer",
                    "locations": [{"name": "London"}, {"name": "Remote"}],
                    "team": "Engineering",
                    "category": {"name": "Tech"},
                },
                {
                    "id": "def-456",
                    "text": "Designer",
                    "locations": [{"name": "Remote"}],
                    "team": "Design",
                    "category": {"name": "Creative"},
                },
            ]
        }
    }
}


def _html_with_next_data(data: dict) -> str:
    payload = json.dumps(data)
    return (
        f'<html><body><script id="__NEXT_DATA__"'
        f' type="application/json">{payload}</script></body></html>'
    )


SAMPLE_HTML = _html_with_next_data(NEXT_DATA)

BOARD_RICH = {
    "board_url": "https://example.com/careers",
    "metadata": {
        "path": "props.pageProps.positions",
        "url_template": "https://example.com/careers/{slug}-{id}/",
        "slug_fields": ["text"],
        "fields": {
            "title": "text",
            "locations": "locations[].name",
            "metadata.team": "team",
        },
    },
}

BOARD_URL_ONLY = {
    "board_url": "https://example.com/careers",
    "metadata": {
        "path": "props.pageProps.positions",
        "url_template": "https://example.com/careers/{slug}-{id}/",
        "slug_fields": ["text"],
    },
}


def _mock_transport(html: str, status: int = 200):
    def handler(request):
        return httpx.Response(status, text=html)

    return httpx.MockTransport(handler)


# ---------------------------------------------------------------------------
# Helper tests
# ---------------------------------------------------------------------------


class TestResolvePath:
    def test_valid_path(self):
        assert _resolve_path({"a": {"b": {"c": 42}}}, "a.b.c") == 42

    def test_list_value(self):
        expected = NEXT_DATA["props"]["pageProps"]["positions"]
        assert _resolve_path(NEXT_DATA, "props.pageProps.positions") == expected

    def test_missing_key(self):
        assert _resolve_path({"a": {"b": 1}}, "a.x") is None

    def test_empty_data(self):
        assert _resolve_path({}, "a.b") is None

    def test_single_key(self):
        assert _resolve_path({"a": 1}, "a") == 1

    def test_non_dict_intermediate(self):
        assert _resolve_path({"a": "string"}, "a.b") is None


class TestExtractField:
    def test_simple_key(self):
        item = {"text": "Engineer", "id": "123"}
        assert _extract_field(item, "text") == "Engineer"

    def test_nested_key(self):
        item = {"category": {"name": "Tech"}}
        assert _extract_field(item, "category.name") == "Tech"

    def test_array_unwrap(self):
        item = {"locations": [{"name": "London"}, {"name": "Remote"}]}
        assert _extract_field(item, "locations[].name") == ["London", "Remote"]

    def test_missing_key(self):
        item = {"text": "Engineer"}
        assert _extract_field(item, "missing") is None

    def test_missing_nested(self):
        item = {"category": {"name": "Tech"}}
        assert _extract_field(item, "category.missing") is None

    def test_array_unwrap_missing_array(self):
        item = {"text": "Engineer"}
        assert _extract_field(item, "locations[].name") is None

    def test_numeric_value_converted(self):
        item = {"count": 42}
        assert _extract_field(item, "count") == "42"

    def test_array_unwrap_empty_array(self):
        item = {"locations": []}
        assert _extract_field(item, "locations[].name") is None


class TestBuildUrl:
    def test_basic_substitution(self):
        item = {"id": "abc-123", "text": "Engineer"}
        url = _build_url(item, "https://example.com/{slug}-{id}/", ["text"])
        assert url == "https://example.com/engineer-abc-123/"

    def test_no_slug_fields(self):
        item = {"id": "abc-123"}
        url = _build_url(item, "https://example.com/jobs/{id}", None)
        assert url == "https://example.com/jobs/abc-123"

    def test_missing_variable(self):
        item = {"id": "abc-123"}
        url = _build_url(item, "https://example.com/{slug}-{id}/", ["text"])
        # "text" not in item, so slug won't be set -> KeyError -> None
        assert url is None

    def test_multiple_slug_fields(self):
        item = {"title": "Senior Engineer", "dept": "Backend"}
        url = _build_url(item, "https://example.com/{slug}/", ["title", "dept"])
        assert url == "https://example.com/senior-engineer-backend/"

    def test_integer_values(self):
        item = {"id": 42}
        url = _build_url(item, "https://example.com/jobs/{id}", None)
        assert url == "https://example.com/jobs/42"


class TestExtractNextData:
    def test_valid_html(self):
        data = _extract_next_data(SAMPLE_HTML)
        assert data == NEXT_DATA

    def test_no_script(self):
        assert _extract_next_data("<html><body>No script</body></html>") is None

    def test_invalid_json(self):
        html = '<html><script id="__NEXT_DATA__">{invalid json}</script></html>'
        assert _extract_next_data(html) is None

    def test_multiline_json(self):
        data = {"props": {"test": True}}
        html = f'<script id="__NEXT_DATA__" type="application/json">\n{json.dumps(data)}\n</script>'
        assert _extract_next_data(html) == data


# ---------------------------------------------------------------------------
# Rich mode tests
# ---------------------------------------------------------------------------


class TestDiscoverRichMode:
    async def test_returns_discovered_jobs(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)

        assert isinstance(result, list)
        assert len(result) == 2
        assert all(isinstance(j, DiscoveredJob) for j in result)

    async def test_job_fields_mapped(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)

        eng = next(j for j in result if j.title == "Engineer")
        assert eng.url == "https://example.com/careers/engineer-abc-123/"
        assert eng.locations == ["London", "Remote"]
        assert eng.metadata == {"team": "Engineering"}

    async def test_partial_fields(self):
        """Items with missing fields still produce DiscoveredJob with None."""
        data = {
            "props": {
                "pageProps": {
                    "positions": [
                        {"id": "x", "text": "PM"},  # no locations, no team
                    ]
                }
            }
        }
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "path": "props.pageProps.positions",
                "url_template": "https://example.com/{slug}-{id}/",
                "slug_fields": ["text"],
                "fields": {
                    "title": "text",
                    "locations": "locations[].name",
                    "metadata.team": "team",
                },
            },
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)

        assert len(result) == 1
        assert result[0].title == "PM"
        assert result[0].locations is None
        assert result[0].metadata is None

    async def test_locations_array_unwrap(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)

        designer = next(j for j in result if j.title == "Designer")
        assert designer.locations == ["Remote"]


# ---------------------------------------------------------------------------
# URL-only mode tests
# ---------------------------------------------------------------------------


class TestDiscoverUrlOnlyMode:
    async def test_returns_set_of_urls(self):
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_URL_ONLY, client)

        assert isinstance(result, set)
        assert len(result) == 2
        assert "https://example.com/careers/engineer-abc-123/" in result
        assert "https://example.com/careers/designer-def-456/" in result


# ---------------------------------------------------------------------------
# Fetch method tests
# ---------------------------------------------------------------------------


class TestFetchMethods:
    async def test_httpx_fetch(self):
        """Default (render=False) uses httpx."""
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(BOARD_RICH, client)
        assert len(result) == 2

    async def test_render_uses_playwright(self):
        """render=True delegates to shared.browser.render."""
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                **BOARD_RICH["metadata"],
                "render": True,
            },
        }
        with patch("src.core.monitors.nextdata.fetch_page_text") as mock_fetch:
            mock_fetch.return_value = None  # should NOT be called
            with patch("src.core.monitors.nextdata._fetch_html", new_callable=AsyncMock) as mock_fh:
                mock_fh.return_value = SAMPLE_HTML
                result = await discover(board, httpx.AsyncClient())

        assert isinstance(result, list)
        assert len(result) == 2


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


class TestCanHandle:
    async def test_nextjs_page_with_jobs(self):
        # can_handle requires >=5 items to consider the array plausible
        data = {
            "props": {
                "pageProps": {"positions": [{"id": str(i), "text": f"Job {i}"} for i in range(6)]}
            }
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)

        assert result is not None
        assert result["path"] == "props.pageProps.positions"

    async def test_non_nextjs_page(self):
        html = "<html><body>Regular page</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_nextjs_no_jobs_array(self):
        """__NEXT_DATA__ exists but no recognized jobs path."""
        data = {"props": {"pageProps": {"somethingElse": "data"}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_nextjs_too_few_items(self):
        """Array exists but has <5 items — not plausible."""
        data = {"props": {"pageProps": {"positions": [{"id": 1}, {"id": 2}]}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_fetch_failure(self):
        async with httpx.AsyncClient(transport=_mock_transport("", status=500)) as client:
            result = await can_handle("https://example.com/careers", client)
        assert result is None

    async def test_render_fallback(self):
        """When static HTTP has no __NEXT_DATA__, falls back to Playwright."""
        data = {
            "props": {
                "pageProps": {"positions": [{"id": str(i), "text": f"Job {i}"} for i in range(6)]}
            }
        }
        rendered_html = _html_with_next_data(data)
        # Static HTML has no __NEXT_DATA__
        plain_html = "<html><body>Regular page</body></html>"

        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            mock_render.return_value = rendered_html
            async with httpx.AsyncClient(transport=_mock_transport(plain_html)) as client:
                result = await can_handle("https://example.com/careers", client)

        assert result is not None
        assert result["path"] == "props.pageProps.positions"
        assert result["render"] is True
        mock_render.assert_awaited_once()

    async def test_render_fallback_not_used_when_static_works(self):
        """Playwright is not invoked when static HTTP finds __NEXT_DATA__."""
        data = {
            "props": {
                "pageProps": {"positions": [{"id": str(i), "text": f"Job {i}"} for i in range(6)]}
            }
        }
        html = _html_with_next_data(data)

        with patch("src.shared.browser.render", new_callable=AsyncMock) as mock_render:
            async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
                result = await can_handle("https://example.com/careers", client)

        assert result is not None
        assert "render" not in result
        mock_render.assert_not_awaited()


# ---------------------------------------------------------------------------
# Error handling tests
# ---------------------------------------------------------------------------


class TestErrorHandling:
    async def test_missing_next_data(self):
        html = "<html><body>No Next.js here</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RICH, client)
        assert result == []

    async def test_missing_next_data_url_mode(self):
        html = "<html><body>No Next.js here</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_URL_ONLY, client)
        assert result == set()

    async def test_invalid_json(self):
        html = '<html><script id="__NEXT_DATA__">{bad json</script></html>'
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RICH, client)
        assert result == []

    async def test_path_not_found(self):
        data = {"props": {"pageProps": {"other": []}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RICH, client)
        assert result == []

    async def test_max_urls_cap(self):
        items = [{"id": str(i), "text": f"Job {i}"} for i in range(10_500)]
        data = {"props": {"pageProps": {"positions": items}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_URL_ONLY, client)
        assert len(result) <= 10_000

    async def test_missing_path_config(self):
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {"url_template": "https://example.com/{id}"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(board, client)
        assert result == set()

    async def test_missing_url_template_config(self):
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {"path": "props.pageProps.positions"},
        }
        async with httpx.AsyncClient(transport=_mock_transport(SAMPLE_HTML)) as client:
            result = await discover(board, client)
        assert result == set()

    async def test_non_dict_items_skipped(self):
        data = {
            "props": {
                "pageProps": {
                    "positions": [
                        "string1",
                        "string2",
                        {"id": "1", "text": "Job"},
                    ],
                },
            },
        }
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "path": "props.pageProps.positions",
                "url_template": "https://example.com/{id}",
            },
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)
        assert isinstance(result, set)
        assert len(result) == 1


# ---------------------------------------------------------------------------
# Helper: _add_query_param
# ---------------------------------------------------------------------------


class TestAddQueryParam:
    def test_adds_param_to_clean_url(self):
        result = _add_query_param("https://example.com/jobs", "page", 2)
        assert result == "https://example.com/jobs?page=2"

    def test_adds_param_to_url_with_existing_params(self):
        result = _add_query_param("https://example.com/jobs?lang=en", "page", 3)
        assert "page=3" in result
        assert "lang=en" in result

    def test_replaces_existing_param(self):
        result = _add_query_param("https://example.com/jobs?page=1", "page", 5)
        assert "page=5" in result
        assert "page=1" not in result


# ---------------------------------------------------------------------------
# Helper: _resolve_field
# ---------------------------------------------------------------------------


class TestResolveField:
    def test_string_spec(self):
        item = {"title": "Engineer"}
        assert _resolve_field(item, "title") == "Engineer"

    def test_dict_spec_with_map(self):
        item = {"workplaceType": "REMOTE"}
        spec = {"path": "workplaceType", "map": {"REMOTE": "remote", "HYBRID": "hybrid"}}
        assert _resolve_field(item, spec) == "remote"

    def test_dict_spec_map_passthrough(self):
        """Values not in map are passed through unchanged."""
        item = {"workplaceType": "UNKNOWN"}
        spec = {"path": "workplaceType", "map": {"REMOTE": "remote"}}
        assert _resolve_field(item, spec) == "UNKNOWN"

    def test_dict_spec_no_map(self):
        item = {"title": "Engineer"}
        spec = {"path": "title"}
        assert _resolve_field(item, spec) == "Engineer"

    def test_dict_spec_missing_value(self):
        item = {"other": "data"}
        spec = {"path": "workplaceType", "map": {"REMOTE": "remote"}}
        assert _resolve_field(item, spec) is None

    def test_dict_spec_list_with_map(self):
        item = {"types": [{"name": "FullTime"}, {"name": "Contract"}]}
        spec = {
            "path": "types[].name",
            "map": {"FullTime": "Full-time", "Contract": "Contract"},
        }
        assert _resolve_field(item, spec) == ["Full-time", "Contract"]


# ---------------------------------------------------------------------------
# Helper: _extract_salary
# ---------------------------------------------------------------------------


class TestExtractSalary:
    def test_basic_salary(self):
        item = {
            "salaryAmountFrom": {"amount": 7500000, "currency": "EUR"},
            "salaryAmountTo": {"amount": 9000000, "currency": "EUR"},
            "salaryFrequency": "PER_YEAR",
        }
        cfg = {
            "min": "salaryAmountFrom.amount",
            "max": "salaryAmountTo.amount",
            "currency": "salaryAmountFrom.currency",
            "unit": "salaryFrequency",
            "divisor": 100,
            "unit_map": {"PER_YEAR": "year", "PER_MONTH": "month"},
        }
        result = _extract_salary(item, cfg)
        assert result == {
            "min": 75000,
            "max": 90000,
            "currency": "EUR",
            "unit": "year",
        }

    def test_no_divisor(self):
        item = {"min_salary": 50000, "max_salary": 80000}
        cfg = {"min": "min_salary", "max": "max_salary"}
        result = _extract_salary(item, cfg)
        assert result == {"min": 50000, "max": 80000}

    def test_missing_salary_fields(self):
        item = {"title": "Engineer"}
        cfg = {
            "min": "salaryAmountFrom.amount",
            "max": "salaryAmountTo.amount",
            "currency": "salaryAmountFrom.currency",
        }
        assert _extract_salary(item, cfg) is None

    def test_partial_salary(self):
        """Only some salary fields present."""
        item = {"salaryAmountFrom": {"amount": 5000000, "currency": "CHF"}}
        cfg = {
            "min": "salaryAmountFrom.amount",
            "max": "salaryAmountTo.amount",
            "currency": "salaryAmountFrom.currency",
            "divisor": 100,
        }
        result = _extract_salary(item, cfg)
        assert result == {"min": 50000, "currency": "CHF"}

    def test_unit_map_passthrough(self):
        item = {"freq": "WEEKLY", "min_salary": 1000}
        cfg = {
            "min": "min_salary",
            "unit": "freq",
            "unit_map": {"PER_YEAR": "year"},
        }
        result = _extract_salary(item, cfg)
        assert result == {"min": 1000, "unit": "WEEKLY"}

    def test_unit_only_returns_none(self):
        """Salary with only unit (no min/max) is not meaningful."""
        item = {"freq": "PER_YEAR"}
        cfg = {"unit": "freq", "unit_map": {"PER_YEAR": "year"}}
        assert _extract_salary(item, cfg) is None

    def test_fractional_salary(self):
        item = {"amount": 333}
        cfg = {"min": "amount", "divisor": 100}
        result = _extract_salary(item, cfg)
        assert result == {"min": 3.33}


# ---------------------------------------------------------------------------
# Pagination tests
# ---------------------------------------------------------------------------


def _paginated_data(page: int, page_count: int, items_per_page: int = 2) -> dict:
    """Build a __NEXT_DATA__ blob for a specific page of a paginated site."""
    start = (page - 1) * items_per_page
    items = [{"id": str(start + i), "text": f"Job {start + i}"} for i in range(items_per_page)]
    return {
        "props": {
            "pageProps": {
                "data": {
                    "jobs": items,
                    "pagination": {
                        "page": page,
                        "pageCount": page_count,
                        "total": page_count * items_per_page,
                    },
                }
            }
        }
    }


def _paginated_transport(page_count: int, items_per_page: int = 2):
    """MockTransport that returns different data per page query param."""

    def handler(request: httpx.Request):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(str(request.url))
        qs = parse_qs(parsed.query)
        page = int(qs.get("page", ["1"])[0])
        data = _paginated_data(page, page_count, items_per_page)
        html = _html_with_next_data(data)
        return httpx.Response(200, text=html)

    return httpx.MockTransport(handler)


BOARD_PAGINATED = {
    "board_url": "https://example.com/jobs",
    "metadata": {
        "path": "props.pageProps.data.jobs",
        "url_template": "https://example.com/jobs/{id}",
        "pagination": {
            "path": "props.pageProps.data.pagination",
            "page_count": "pageCount",
            "page_param": "page",
        },
    },
}

BOARD_PAGINATED_RICH = {
    "board_url": "https://example.com/jobs",
    "metadata": {
        **BOARD_PAGINATED["metadata"],
        "fields": {"title": "text"},
    },
}


class TestPagination:
    async def test_single_page_no_extra_fetches(self):
        """pageCount=1 returns first-page items without extra requests."""
        transport = _paginated_transport(page_count=1)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_PAGINATED, client)
        assert isinstance(result, set)
        assert len(result) == 2

    async def test_multi_page_merges_items(self):
        """Three pages of 2 items each → 6 total URLs."""
        transport = _paginated_transport(page_count=3)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_PAGINATED, client)
        assert isinstance(result, set)
        assert len(result) == 6

    async def test_multi_page_rich_mode(self):
        """Pagination works in rich mode too."""
        transport = _paginated_transport(page_count=2)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_PAGINATED_RICH, client)
        assert isinstance(result, list)
        assert len(result) == 4
        assert all(isinstance(j, DiscoveredJob) for j in result)
        titles = {j.title for j in result}
        assert titles == {"Job 0", "Job 1", "Job 2", "Job 3"}

    async def test_missing_pagination_config_fields(self):
        """Incomplete pagination config is silently ignored."""
        board = {
            "board_url": "https://example.com/jobs",
            "metadata": {
                "path": "props.pageProps.data.jobs",
                "url_template": "https://example.com/jobs/{id}",
                "pagination": {"path": "props.pageProps.data.pagination"},
                # missing page_count
            },
        }
        transport = _paginated_transport(page_count=3)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(board, client)
        # Falls back to first page only
        assert len(result) == 2

    async def test_page_fetch_failure_skips_page(self):
        """If a page fetch fails, other pages still work."""
        call_count = 0

        def handler(request: httpx.Request):
            nonlocal call_count
            call_count += 1
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(str(request.url))
            qs = parse_qs(parsed.query)
            page = int(qs.get("page", ["1"])[0])
            if page == 2:
                return httpx.Response(500)
            data = _paginated_data(page, 3)
            html = _html_with_next_data(data)
            return httpx.Response(200, text=html)

        transport = httpx.MockTransport(handler)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_PAGINATED, client)
        # Page 1 (2 items) + page 2 (failed, 0) + page 3 (2 items) = 4
        assert len(result) == 4


# ---------------------------------------------------------------------------
# Rich mode with field mappings and base_salary
# ---------------------------------------------------------------------------


class TestRichModeWithMappingsAndSalary:
    async def test_field_mapping_applied(self):
        data = {
            "props": {
                "pageProps": {
                    "positions": [
                        {
                            "id": "1",
                            "title": "Engineer",
                            "workplaceType": "REMOTE",
                            "employmentType": {"name": "Employee"},
                        },
                    ]
                }
            }
        }
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "path": "props.pageProps.positions",
                "url_template": "https://example.com/jobs/{id}",
                "fields": {
                    "title": "title",
                    "job_location_type": {
                        "path": "workplaceType",
                        "map": {
                            "REMOTE": "remote",
                            "HYBRID": "hybrid",
                            "ONSITE": "onsite",
                        },
                    },
                    "employment_type": {
                        "path": "employmentType.name",
                        "map": {"Employee": "Full-time", "Internship": "Intern"},
                    },
                },
            },
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)

        assert len(result) == 1
        job = result[0]
        assert job.job_location_type == "remote"
        assert job.employment_type == "Full-time"

    async def test_base_salary_extracted(self):
        data = {
            "props": {
                "pageProps": {
                    "positions": [
                        {
                            "id": "1",
                            "title": "Engineer",
                            "salaryAmountFrom": {
                                "amount": 7500000,
                                "currency": "EUR",
                            },
                            "salaryAmountTo": {
                                "amount": 9000000,
                                "currency": "EUR",
                            },
                            "salaryFrequency": "PER_YEAR",
                        },
                    ]
                }
            }
        }
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "path": "props.pageProps.positions",
                "url_template": "https://example.com/jobs/{id}",
                "fields": {"title": "title"},
                "base_salary": {
                    "min": "salaryAmountFrom.amount",
                    "max": "salaryAmountTo.amount",
                    "currency": "salaryAmountFrom.currency",
                    "unit": "salaryFrequency",
                    "divisor": 100,
                    "unit_map": {"PER_YEAR": "year", "PER_MONTH": "month"},
                },
            },
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)

        assert len(result) == 1
        job = result[0]
        assert job.base_salary == {
            "min": 75000,
            "max": 90000,
            "currency": "EUR",
            "unit": "year",
        }

    async def test_no_salary_when_fields_missing(self):
        """Jobs without salary data should have base_salary=None."""
        data = {
            "props": {
                "pageProps": {
                    "positions": [
                        {"id": "1", "title": "Designer"},
                    ]
                }
            }
        }
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "path": "props.pageProps.positions",
                "url_template": "https://example.com/jobs/{id}",
                "fields": {"title": "title"},
                "base_salary": {
                    "min": "salaryAmountFrom.amount",
                    "max": "salaryAmountTo.amount",
                    "currency": "salaryAmountFrom.currency",
                    "divisor": 100,
                },
            },
        }
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)

        assert len(result) == 1
        assert result[0].base_salary is None


# ---------------------------------------------------------------------------
# Join.com integration test (end-to-end config)
# ---------------------------------------------------------------------------


JOIN_NEXT_DATA_PAGE1 = {
    "props": {
        "pageProps": {
            "initialState": {
                "company": {"domain": "acme"},
                "jobs": {
                    "items": [
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
                            "idParam": "102-product-manager",
                            "title": "Product Manager",
                            "createdAt": "2026-01-20T10:00:00Z",
                            "workplaceType": "REMOTE",
                            "employmentType": {"name": "Employee"},
                            "city": {"cityName": "Zurich", "countryName": "Switzerland"},
                            "category": {"name": "Product"},
                        },
                    ],
                    "pagination": {
                        "page": 1,
                        "pageCount": 1,
                        "total": 2,
                    },
                },
            }
        }
    },
}

JOIN_BOARD = {
    "board_url": "https://join.com/companies/acme",
    "metadata": {
        "path": "props.pageProps.initialState.jobs.items",
        "url_template": "https://join.com/companies/acme/{idParam}",
        "pagination": {
            "path": "props.pageProps.initialState.jobs.pagination",
            "page_count": "pageCount",
            "page_param": "page",
        },
        "fields": {
            "title": "title",
            "date_posted": "createdAt",
            "locations": "city.cityName",
            "employment_type": {
                "path": "employmentType.name",
                "map": {
                    "Employee": "Full-time",
                    "Internship": "Intern",
                    "Working Student": "Working Student",
                    "Freelancer": "Contract",
                },
            },
            "job_location_type": {
                "path": "workplaceType",
                "map": {
                    "REMOTE": "remote",
                    "HYBRID": "hybrid",
                    "ONSITE": "onsite",
                },
            },
            "metadata.category": "category.name",
            "metadata.id": "id",
        },
        "base_salary": {
            "min": "salaryAmountFrom.amount",
            "max": "salaryAmountTo.amount",
            "currency": "salaryAmountFrom.currency",
            "unit": "salaryFrequency",
            "divisor": 100,
            "unit_map": {
                "PER_YEAR": "year",
                "PER_MONTH": "month",
                "PER_HOUR": "hour",
            },
        },
    },
}


class TestJoinComConfig:
    async def test_join_full_extraction(self):
        html = _html_with_next_data(JOIN_NEXT_DATA_PAGE1)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(JOIN_BOARD, client)

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
        assert eng.metadata == {"category": "Engineering", "id": "101"}

        pm = next(j for j in result if j.title == "Product Manager")
        assert pm.job_location_type == "remote"
        assert pm.base_salary is None  # no salary data on this job
