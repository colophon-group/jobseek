from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitor import monitor_one, monitor_one_stream
from src.core.monitors import DiscoveredJob, monitor_needs_browser
from src.core.monitors.nextdata import (
    _add_query_param,
    _board_gone_statuses,
    _build_url,
    _extract_salary,
    _filter_included_items,
    _find_jobs_path,
    _resolve_field,
    _validated_item_inclusions,
    can_handle,
    discover,
    discover_stream,
)
from src.shared.nextdata import (
    extract_field as _extract_field,
)
from src.shared.nextdata import (
    extract_next_data as _extract_next_data,
)
from src.shared.nextdata import (
    extract_react_router_data,
    extract_rsc_data,
)
from src.shared.nextdata import (
    resolve_path as _resolve_path,
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


@pytest.mark.parametrize("statuses", [[200], [403], [404, 500], "404", [True]])
def test_board_gone_statuses_are_limited_to_explicit_retirement_responses(statuses):
    with pytest.raises(ValueError, match="only HTTP 404 and 410"):
        _board_gone_statuses({"board_gone_statuses": statuses})


def test_board_gone_statuses_accepts_join_retirement_responses():
    assert _board_gone_statuses({"board_gone_statuses": [404, 410]}) == frozenset({404, 410})


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


class TestExtractFieldShared:
    """Tests for shared extract_field (list specs, constants, templates)."""

    def _extract(self, item, spec):
        from src.shared.nextdata import extract_field

        return extract_field(item, spec)

    def test_list_spec_concatenates(self):
        item = {"a": "va", "b": "vb"}
        assert self._extract(item, ["a", "b"]) == "va\nvb"

    def test_list_spec_flattens_array(self):
        item = {"items": [{"name": "n1"}, {"name": "n2"}]}
        assert self._extract(item, ["items[*].name"]) == "n1\nn2"

    def test_list_spec_with_constants(self):
        item = {"a": "va"}
        assert self._extract(item, ["=prefix", "a"]) == "prefix\nva"

    def test_list_spec_null_drops_preceding_constant(self):
        item = {}
        assert self._extract(item, ["=heading", "null_path"]) is None

    def test_list_spec_null_drops_constant_keeps_rest(self):
        item = {"exists": "val"}
        result = self._extract(item, ["=<h3>X</h3>", "missing", "=<h3>Y</h3>", "exists"])
        assert result == "<h3>Y</h3>\nval"

    def test_list_spec_all_null_returns_none(self):
        item = {"a": "va"}
        assert self._extract(item, ["missing1", "missing2"]) is None

    def test_list_spec_constant_only(self):
        assert self._extract({}, ["=fallback"]) == "fallback"

    def test_list_spec_each_wrap(self):
        item = {
            "items": [
                {"title": "Heading1", "body": "<ul>1</ul>"},
                {"title": "Heading2", "body": "<ul>2</ul>"},
            ]
        }
        result = self._extract(item, [{"each": "items[*]", "wrap": "<h3>{title}</h3>\n{body}"}])
        assert result == "<h3>Heading1</h3>\n<ul>1</ul>\n<h3>Heading2</h3>\n<ul>2</ul>"

    def test_list_spec_each_wrap_null_array(self):
        item = {}
        result = self._extract(item, [{"each": "missing[*]", "wrap": "<h3>{t}</h3>"}])
        assert result is None

    def test_path_spec_html_unescape(self):
        item = {"body": "&lt;h2&gt;Tasks&lt;/h2&gt;&lt;p&gt;Build &amp; test.&lt;/p&gt;"}
        result = self._extract(item, {"path": "body", "html_unescape": True})
        assert result == "<h2>Tasks</h2><p>Build & test.</p>"

    def test_path_spec_html_unescape_preserves_none(self):
        result = self._extract({}, {"path": "body", "html_unescape": True})
        assert result is None


class TestExtractFieldLookupJoin:
    """``lookup_from`` + ``key_from`` — sibling-table resolution.

    Motivated by ATSes that ship a compact listing (``{id, dp, f, l}``)
    plus a sibling lookup dict (``{lookup: {departments: {...}}}``).
    Tesla's ``/cua-api/apps/careers/state`` endpoint is the canonical
    case; eightfold PCSX and some Workday tenants use similar shapes.
    """

    @staticmethod
    def _tesla_root() -> dict:
        return {
            "lookup": {
                "regions": {"3": "Europe", "5": "North America"},
                "sites": {"CH": "Switzerland", "US": "United States"},
                "departments": {"4": "AI", "10": "Sales & Customer Support"},
                "locations": {
                    "401022": "Palo Alto, CA",
                    "501033": "Cadenazzo, CH",
                },
            },
            "listings": [
                {"id": "1", "t": "AI Engineer", "dp": "4", "l": 401022},
                {"id": "2", "t": "Sales Advisor", "dp": "10", "l": 501033},
            ],
        }

    def _extract(self, item, spec, root):
        from src.shared.nextdata import extract_field

        return extract_field(item, spec, root=root)

    def test_resolves_string_key(self):
        root = self._tesla_root()
        item = root["listings"][0]
        v = self._extract(item, {"lookup_from": "lookup.departments", "key_from": "dp"}, root)
        assert v == "AI"

    def test_resolves_numeric_key_as_string(self):
        """Listing IDs are often ints in the wire format; lookup keys are
        always strings (JSON dict keys). Must coerce rather than return
        None on type mismatch."""
        root = self._tesla_root()
        item = root["listings"][0]
        v = self._extract(item, {"lookup_from": "lookup.locations", "key_from": "l"}, root)
        assert v == "Palo Alto, CA"

    def test_missing_key_returns_none(self):
        root = self._tesla_root()
        item = {"id": "x"}  # no "dp"
        v = self._extract(item, {"lookup_from": "lookup.departments", "key_from": "dp"}, root)
        assert v is None

    def test_missing_lookup_table_returns_none(self):
        root = self._tesla_root()
        item = root["listings"][0]
        v = self._extract(item, {"lookup_from": "lookup.nonexistent", "key_from": "dp"}, root)
        assert v is None

    def test_lookup_path_resolves_to_non_dict_returns_none(self):
        """If the jmespath lands on a scalar or array, that's a misconfig
        — return None rather than raise so one bad field doesn't kill
        the whole listing batch."""
        root = {"lookup": {"departments": "not-a-dict"}}
        v = self._extract(
            {"dp": "4"}, {"lookup_from": "lookup.departments", "key_from": "dp"}, root
        )
        assert v is None

    def test_key_not_in_table_returns_none(self):
        root = self._tesla_root()
        v = self._extract(
            {"dp": "999"},
            {"lookup_from": "lookup.departments", "key_from": "dp"},
            root,
        )
        assert v is None

    def test_no_root_returns_none(self):
        """When a caller didn't thread root through — common during
        auto-discover paths that only see a paginated page — the lookup
        can't resolve. Fail soft and log rather than raise."""
        item = {"dp": "4"}
        v = self._extract(item, {"lookup_from": "lookup.departments", "key_from": "dp"}, None)
        assert v is None


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

    async def test_embedded_payload_after_generic_fetch_cap_is_not_truncated(self):
        html = f"<html><body>{'x' * 550_000}{SAMPLE_HTML}</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RICH, client)

        assert len(result) == 2


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

    async def test_browser_source_maps_client_side_job_list(self):
        board = {
            "board_url": "https://example.com/campus",
            "metadata": {
                "source": "browser",
                "browser_expression": "({jobs: jobList})",
                "path": "jobs",
                "url_template": "{link}",
                "fields": {
                    "title": "title",
                    "description": "desc",
                    "locations": "=Shanghai, China",
                },
            },
        }
        data = {
            "jobs": [
                {
                    "title": "Management Trainee",
                    "desc": "<p>Rotate through corporate functions.</p>",
                    "link": "https://apply.example.com/123",
                }
            ]
        }

        with patch(
            "src.core.monitors.nextdata._evaluate_browser_data",
            new_callable=AsyncMock,
            return_value=data,
        ) as evaluate:
            result = await discover(board, httpx.AsyncClient(), pw=object())

        evaluate.assert_awaited_once()
        assert len(result) == 1
        assert result[0].url == "https://apply.example.com/123"
        assert result[0].title == "Management Trainee"
        assert result[0].description == "<p>Rotate through corporate functions.</p>"
        assert result[0].locations == ["Shanghai, China"]
        assert monitor_needs_browser("nextdata", board["metadata"]) is True

    async def test_browser_source_requires_expression(self):
        board = {
            "board_url": "https://example.com/campus",
            "metadata": {
                "source": "browser",
                "path": "jobs",
                "url_template": "{link}",
                "fields": {"title": "title"},
            },
        }

        with pytest.raises(ValueError, match="requires a non-empty browser_expression"):
            await discover(board, httpx.AsyncClient(), pw=object())

    async def test_browser_source_propagates_evaluation_failure(self):
        board = {
            "board_url": "https://example.com/campus",
            "metadata": {
                "source": "browser",
                "browser_expression": "({jobs: jobList})",
                "path": "jobs",
                "url_template": "{link}",
                "fields": {"title": "title"},
            },
        }

        with (
            patch(
                "src.core.monitors.nextdata._evaluate_browser_data",
                new_callable=AsyncMock,
                side_effect=RuntimeError("page changed"),
            ),
            pytest.raises(RuntimeError, match="page changed"),
        ):
            await discover(board, httpx.AsyncClient(), pw=object())

    async def test_browser_source_rejects_missing_evaluated_data(self):
        board = {
            "board_url": "https://example.com/campus",
            "metadata": {
                "source": "browser",
                "browser_expression": "({jobs: jobList})",
                "path": "jobs",
                "url_template": "{link}",
                "fields": {"title": "title"},
            },
        }

        with (
            patch(
                "src.core.monitors.nextdata._evaluate_browser_data",
                new_callable=AsyncMock,
                return_value=None,
            ),
            pytest.raises(RuntimeError, match="returned no data"),
        ):
            await discover(board, httpx.AsyncClient(), pw=object())


# ---------------------------------------------------------------------------
# can_handle tests
# ---------------------------------------------------------------------------


class TestCanHandle:
    def test_nested_job_search_is_opt_in_for_rsc_payloads(self):
        jobs = [
            {
                "id": f"job-{i}",
                "title": "Watchmaker",
                "description": f"Build watch movements {i}",
            }
            for i in range(6)
        ]
        data = {"component": {"items": jobs}}

        assert _find_jobs_path(data) is None
        assert _find_jobs_path(data, allow_nested=True) == (
            "component.items",
            6,
        )

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

    async def test_rsc_jobs_nested_in_component_tree(self):
        """App Router listings may live in a deeply nested component prop."""
        jobs = [
            {
                "id": f"job-{i}",
                "position": {"name": "Watchmaker"},
                "description": f"Build watch movements {i}",
                "production": {"production_id": {"name": "Factory", "area": "Tokyo"}},
            }
            for i in range(6)
        ]
        data = {
            "children": [
                "$",
                "$component",
                None,
                {"filters": [{"id": str(i), "label": f"Filter {i}"} for i in range(8)]},
                ["$", "$list", None, {"items": jobs}],
            ]
        }
        html = _html_with_rsc_data(data)

        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await can_handle("https://example.com/careers", client)

        assert result == {
            "source": "rsc",
            "path": "children[4][3].items",
            "count": 6,
        }


class TestItemInclusions:
    def test_validates_and_filters_scalar_or_list_values(self):
        inclusions = _validated_item_inclusions(
            {"company": ["UNIQLO"], "countries": ["DE"]}
        )

        result = _filter_included_items(
            [
                {"id": "1", "company": "UNIQLO", "countries": ["DE", "AT"]},
                {"id": "2", "company": "UNIQLO", "countries": ["FR"]},
                {"id": "3", "company": "Unique GmbH", "countries": ["DE"]},
                "not-an-object",
            ],
            inclusions,
        )

        assert result == [
            {"id": "1", "company": "UNIQLO", "countries": ["DE", "AT"]}
        ]

    @pytest.mark.parametrize(
        "value",
        [
            {},
            {"": ["UNIQLO"]},
            {"company": []},
            {"company": [1]},
        ],
    )
    def test_rejects_invalid_config(self, value):
        with pytest.raises(ValueError, match="include_item_values"):
            _validated_item_inclusions(value)

    async def test_discover_filters_after_paginating(self):
        page_one = {
            "props": {
                "pageProps": {
                    "search": {
                        "jobs": [
                            {"id": "1", "company": "UNIQLO", "title": "Sales"},
                            {"id": "2", "company": "Unique GmbH", "title": "Driver"},
                        ],
                        "totalPages": 2,
                    }
                }
            }
        }
        page_two = {
            "props": {
                "pageProps": {
                    "search": {
                        "jobs": [
                            {"id": "3", "company": "UNIQLO", "title": "Manager"},
                            {"id": "4", "company": "Other", "title": "Engineer"},
                        ],
                        "totalPages": 2,
                    }
                }
            }
        }
        board = {
            "board_url": "https://example.com/jobs?q=UNIQLO",
            "metadata": {
                "path": "props.pageProps.search.jobs",
                "url_template": "https://example.com/jobs/{id}",
                "fields": {"title": "title"},
                "include_item_values": {"company": ["UNIQLO"]},
                "pagination": {
                    "path": "props.pageProps.search",
                    "page_count": "totalPages",
                    "page_param": "page",
                },
            },
        }

        def handler(request):
            data = page_two if request.url.params.get("page") == "2" else page_one
            return httpx.Response(200, text=_html_with_next_data(data))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert [(job.url, job.title) for job in result] == [
            ("https://example.com/jobs/1", "Sales"),
            ("https://example.com/jobs/3", "Manager"),
        ]

    async def test_stream_filters_each_page(self):
        page_one = {
            "props": {
                "pageProps": {
                    "search": {
                        "jobs": [{"id": "1", "company": "UNIQLO", "title": "Sales"}],
                        "totalPages": 2,
                    }
                }
            }
        }
        page_two = {
            "props": {
                "pageProps": {
                    "search": {
                        "jobs": [
                            {"id": "2", "company": "Unique GmbH", "title": "Driver"},
                            {"id": "3", "company": "UNIQLO", "title": "Manager"},
                        ],
                        "totalPages": 2,
                    }
                }
            }
        }
        board = {
            "board_url": "https://example.com/jobs?q=UNIQLO",
            "metadata": {
                "path": "props.pageProps.search.jobs",
                "url_template": "https://example.com/jobs/{id}",
                "fields": {"title": "title"},
                "include_item_values": {"company": ["UNIQLO"]},
                "pagination": {
                    "path": "props.pageProps.search",
                    "page_count": "totalPages",
                    "page_param": "page",
                },
            },
        }

        def handler(request):
            data = page_two if request.url.params.get("page") == "2" else page_one
            return httpx.Response(200, text=_html_with_next_data(data))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            batches = [batch async for batch in discover_stream(board, client)]

        assert [[job.title for job in batch] for batch in batches] == [["Sales"], ["Manager"]]


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
        from src.core.monitors.nextdata import MAX_URLS

        items = [{"id": str(i), "text": f"Job {i}"} for i in range(MAX_URLS + 500)]
        data = {"props": {"pageProps": {"positions": items}}}
        html = _html_with_next_data(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_URL_ONLY, client)
        assert len(result) <= MAX_URLS

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

    def test_dict_spec_map_unmapped_returns_none(self):
        """Values not in map are dropped (None)."""
        item = {"workplaceType": "UNKNOWN"}
        spec = {"path": "workplaceType", "map": {"REMOTE": "remote"}}
        assert _resolve_field(item, spec) is None

    def test_dict_spec_no_map(self):
        """Dict spec without map behaves like a plain path."""
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

    async def test_confirmed_empty_first_page_is_valid(self):
        """A one-page empty inventory must reach confirmed-empty handling."""
        transport = _paginated_transport(page_count=1, items_per_page=0)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_PAGINATED, client)
        assert result == set()

    async def test_stream_confirmed_empty_first_page_is_valid(self):
        transport = _paginated_transport(page_count=1, items_per_page=0)
        async with httpx.AsyncClient(transport=transport) as client:
            batches = [batch async for batch in discover_stream(BOARD_PAGINATED, client)]
        assert batches == [set()]

    async def test_empty_first_page_requires_configured_zero_total(self):
        board = {
            **BOARD_PAGINATED,
            "metadata": {
                **BOARD_PAGINATED["metadata"],
                "pagination": {
                    **BOARD_PAGINATED["metadata"]["pagination"],
                    "total_records": "total",
                },
            },
        }

        def handler(request: httpx.Request):
            data = _paginated_data(page=1, page_count=1, items_per_page=0)
            del data["props"]["pageProps"]["data"]["pagination"]["total"]
            return httpx.Response(200, text=_html_with_next_data(data))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="first page was empty"):
                await discover(board, client)

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
        """Incomplete pagination config fails closed."""
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
            with pytest.raises(ValueError, match="valid page count"):
                await discover(board, client)

    async def test_page_fetch_failure_fails_run_after_bounded_retries(self):
        """A missing page must fail the run instead of tombstoning its jobs."""
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
            with (
                patch("src.core.monitors.nextdata.asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(RuntimeError, match="required page failed after 3 attempts"),
            ):
                await discover(BOARD_PAGINATED, client)
        # Page 1 and page 3 succeed once; required page 2 exhausts three attempts.
        assert call_count == 5

    @pytest.mark.parametrize(
        "bad_html",
        [
            "<html><body>No embedded data</body></html>",
            _html_with_next_data(
                {
                    "props": {
                        "pageProps": {
                            "data": {
                                "jobs": {"unexpected": "object"},
                                "pagination": {"page": 2, "pageCount": 2, "total": 4},
                            }
                        }
                    }
                }
            ),
        ],
        ids=["missing-embedded-data", "wrong-path-type"],
    )
    async def test_stream_parse_failure_fails_run_after_retries(self, bad_html):
        page_two_calls = 0

        def handler(request: httpx.Request):
            nonlocal page_two_calls
            page = int(request.url.params.get("page", "1"))
            if page == 2:
                page_two_calls += 1
                return httpx.Response(200, text=bad_html)
            return httpx.Response(
                200,
                text=_html_with_next_data(_paginated_data(page, 2)),
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch("src.core.monitors.nextdata.asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(RuntimeError, match="required page failed after 3 attempts"),
            ):
                async for _batch in monitor_one_stream(
                    BOARD_PAGINATED["board_url"],
                    "nextdata",
                    BOARD_PAGINATED["metadata"],
                    client,
                ):
                    pass

        assert page_two_calls == 3

    async def test_empty_required_page_fails_run_after_retries(self):
        """A valid embedded shell with no jobs is still an incomplete page."""
        page_two_calls = 0

        def handler(request: httpx.Request):
            nonlocal page_two_calls
            page = int(request.url.params.get("page", "1"))
            data = _paginated_data(page, 3)
            if page == 2:
                page_two_calls += 1
                data["props"]["pageProps"]["data"]["jobs"] = []
            return httpx.Response(200, text=_html_with_next_data(data))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch("src.core.monitors.nextdata.asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(
                    RuntimeError,
                    match="path resolved to an empty required page",
                ),
            ):
                await discover(BOARD_PAGINATED, client)

        assert page_two_calls == 3

    async def test_stream_validates_unique_count_against_first_page_total(self):
        board = {
            **BOARD_PAGINATED,
            "metadata": {
                **BOARD_PAGINATED["metadata"],
                "pagination": {
                    **BOARD_PAGINATED["metadata"]["pagination"],
                    "total_records": "total",
                    "page_size": 2,
                },
            },
        }

        def handler(request: httpx.Request):
            page = int(request.url.params.get("page", "1"))
            data = _paginated_data(page, 3)
            data["props"]["pageProps"]["data"]["pagination"]["total"] = 5
            return httpx.Response(200, text=_html_with_next_data(data))

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(RuntimeError, match="discovered 6 unique jobs.*expected 5"):
                async for _batch in monitor_one_stream(
                    board["board_url"],
                    "nextdata",
                    board["metadata"],
                    client,
                ):
                    pass


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


# ---------------------------------------------------------------------------
# React Router hydration data tests
# ---------------------------------------------------------------------------


REACT_ROUTER_DATA = {
    "loaderData": {
        "search": {
            "searchResults": [
                {
                    "positionId": "100001",
                    "postingTitle": "Software Engineer",
                    "transformedPostingTitle": "software-engineer",
                    "postDateInGMT": "2026-03-15T10:00:00Z",
                    "locations": [{"name": "Cupertino, CA"}],
                    "team": {"teamName": "Engineering", "teamCode": "SFTWR"},
                },
                {
                    "positionId": "100002",
                    "postingTitle": "Product Designer",
                    "transformedPostingTitle": "product-designer",
                    "postDateInGMT": "2026-03-16T10:00:00Z",
                    "locations": [{"name": "London"}, {"name": "Remote"}],
                    "team": {"teamName": "Design", "teamCode": "DSGN"},
                },
            ],
            "totalRecords": 2,
        }
    }
}


def _html_with_react_router(data: dict) -> str:
    """Build HTML with React Router __staticRouterHydrationData."""
    inner = json.dumps(json.dumps(data))  # double-encode
    # inner is '"{\\"loaderData\\"...}"', we need the content without outer quotes
    escaped = inner[1:-1]  # strip outer quotes from json.dumps
    return (
        f"<html><body><script>"
        f'window.__staticRouterHydrationData = JSON.parse("{escaped}");'
        f"</script></body></html>"
    )


REACT_ROUTER_HTML = _html_with_react_router(REACT_ROUTER_DATA)

BOARD_REACT_ROUTER = {
    "board_url": "https://example.com/search?page=1",
    "metadata": {
        "source": "reactrouter",
        "path": "loaderData.search.searchResults",
        "url_template": "https://example.com/details/{positionId}/{transformedPostingTitle}",
        "fields": {
            "title": "postingTitle",
            "locations": "locations[].name",
            "date_posted": "postDateInGMT",
            "metadata.team": "team.teamName",
        },
    },
}


class TestExtractReactRouterData:
    def test_valid_html(self):
        data = extract_react_router_data(REACT_ROUTER_HTML)
        assert data == REACT_ROUTER_DATA

    def test_no_hydration_data(self):
        assert extract_react_router_data("<html><body>No data</body></html>") is None

    def test_invalid_json(self):
        html = '<script>window.__staticRouterHydrationData = JSON.parse("{bad}");</script>'
        assert extract_react_router_data(html) is None


class TestReactRouterDiscover:
    async def test_returns_rich_jobs(self):
        async with httpx.AsyncClient(transport=_mock_transport(REACT_ROUTER_HTML)) as client:
            result = await discover(BOARD_REACT_ROUTER, client)

        assert isinstance(result, list)
        assert len(result) == 2

    async def test_job_fields_mapped(self):
        async with httpx.AsyncClient(transport=_mock_transport(REACT_ROUTER_HTML)) as client:
            result = await discover(BOARD_REACT_ROUTER, client)

        eng = next(j for j in result if j.title == "Software Engineer")
        assert eng.url == "https://example.com/details/100001/software-engineer"
        assert eng.locations == ["Cupertino, CA"]
        assert eng.date_posted == "2026-03-15T10:00:00Z"
        assert eng.metadata == {"team": "Engineering"}

        designer = next(j for j in result if j.title == "Product Designer")
        assert designer.locations == ["London", "Remote"]

    async def test_url_only_mode(self):
        board = {
            "board_url": "https://example.com/search?page=1",
            "metadata": {
                "source": "reactrouter",
                "path": "loaderData.search.searchResults",
                "url_template": "https://example.com/details/{positionId}/{transformedPostingTitle}",
            },
        }
        async with httpx.AsyncClient(transport=_mock_transport(REACT_ROUTER_HTML)) as client:
            result = await discover(board, client)

        assert isinstance(result, set)
        assert len(result) == 2
        assert "https://example.com/details/100001/software-engineer" in result

    async def test_no_data_returns_empty(self):
        html = "<html><body>No React Router data</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_REACT_ROUTER, client)
        assert result == []


# ---------------------------------------------------------------------------
# Pagination with total_records / page_size
# ---------------------------------------------------------------------------


def _react_router_paginated_data(page: int, total: int, page_size: int = 2) -> dict:
    start = (page - 1) * page_size
    count = min(page_size, total - start)
    items = [
        {
            "positionId": str(start + i),
            "postingTitle": f"Job {start + i}",
            "transformedPostingTitle": f"job-{start + i}",
        }
        for i in range(count)
    ]
    return {
        "loaderData": {
            "search": {
                "searchResults": items,
                "totalRecords": total,
            }
        }
    }


def _react_router_paginated_transport(total: int, page_size: int = 2):
    def handler(request: httpx.Request):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(str(request.url))
        qs = parse_qs(parsed.query)
        page = int(qs.get("page", ["1"])[0])
        data = _react_router_paginated_data(page, total, page_size)
        html = _html_with_react_router(data)
        return httpx.Response(200, text=html)

    return httpx.MockTransport(handler)


BOARD_REACT_ROUTER_PAGINATED = {
    "board_url": "https://example.com/search?page=1",
    "metadata": {
        "source": "reactrouter",
        "path": "loaderData.search.searchResults",
        "url_template": "https://example.com/details/{positionId}/{transformedPostingTitle}",
        "pagination": {
            "path": "loaderData.search",
            "total_records": "totalRecords",
            "page_size": 2,
            "page_param": "page",
        },
    },
}


class TestTotalRecordsPagination:
    async def test_multi_page(self):
        """6 total records / page_size 2 = 3 pages."""
        transport = _react_router_paginated_transport(total=6, page_size=2)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_REACT_ROUTER_PAGINATED, client)
        assert isinstance(result, set)
        assert len(result) == 6

    async def test_single_page(self):
        """2 total / page_size 2 = 1 page, no extra fetches."""
        transport = _react_router_paginated_transport(total=2, page_size=2)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_REACT_ROUTER_PAGINATED, client)
        assert len(result) == 2

    async def test_partial_last_page(self):
        """5 total / page_size 2 = 3 pages (last page has 1 item)."""
        transport = _react_router_paginated_transport(total=5, page_size=2)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_REACT_ROUTER_PAGINATED, client)
        assert len(result) == 5


# ---------------------------------------------------------------------------
# RSC flight payload tests
# ---------------------------------------------------------------------------


def _html_with_rsc_data(data: dict) -> str:
    """Build HTML with RSC flight payload containing *data*.

    Mimics Next.js App Router output: data is placed inside
    ``self.__next_f.push([1,"<id>:<RSC array>"])`` with proper escaping.
    """
    # Build the RSC array: ["$","$L10",null,{...data...}]
    rsc_array = ["$", "$L10", None, data]
    rsc_json = json.dumps(rsc_array)
    # RSC line: "7:" + json
    rsc_line = f"7:{rsc_json}\n"
    # Escape for embedding inside a JS string: quote → \", backslash → \\, newline → \n
    escaped = rsc_line.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    return f'<html><body><script>self.__next_f.push([1,"{escaped}"])</script></body></html>'


RSC_JOB_DATA = {
    "job": {
        "slug": "software-engineer",
        "title": "Software Engineer",
        "location": "Zurich (On-site)/Remote",
        "type": "Full-time",
        "aboutRole": "We are hiring Software Engineers to build our platform.",
        "responsibilities": ["Design systems", "Write code"],
        "minimumQualifications": ["3+ years experience"],
    }
}

RSC_HTML = _html_with_rsc_data(RSC_JOB_DATA)


class TestExtractRscData:
    def test_valid_html(self):
        data = extract_rsc_data(RSC_HTML)
        assert data is not None
        assert "job" in data
        assert data["job"]["title"] == "Software Engineer"

    def test_no_push_calls(self):
        assert extract_rsc_data("<html><body>Nothing</body></html>") is None

    def test_multiple_chunks(self):
        """Data spread across multiple push calls is merged."""
        chunk1 = {"meta": {"version": 1}}
        chunk2 = {"job": {"title": "Engineer"}}
        html = _html_with_rsc_data(chunk1) + _html_with_rsc_data(chunk2)
        # Replace second script to use a different line id
        html = html.replace(
            'self.__next_f.push([1,"7:',
            'self.__next_f.push([1,"8:',
            1,  # only replace second occurrence
        )
        data = extract_rsc_data(html)
        assert data is not None
        assert data["meta"] == {"version": 1}
        assert data["job"]["title"] == "Engineer"

    def test_plain_dict_payload(self):
        """Handles payloads that are plain dicts (not RSC arrays)."""
        payload = {"title": "Test Job"}
        rsc_line = f"5:{json.dumps(payload)}\n"
        escaped = rsc_line.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
        html = f'<html><script>self.__next_f.push([1,"{escaped}"])</script></html>'
        data = extract_rsc_data(html)
        assert data is not None
        assert data["title"] == "Test Job"


BOARD_RSC = {
    "board_url": "https://example.com/jobs",
    "metadata": {
        "source": "rsc",
        "path": "jobs",
        "url_template": "https://example.com/jobs/{slug}",
        "fields": {
            "title": "title",
            "locations": "location",
        },
    },
}


class TestRscDiscover:
    async def test_returns_rich_jobs(self):
        rsc_data = {
            "jobs": [
                {"title": "Engineer", "slug": "engineer", "location": ["NYC"]},
                {"title": "Designer", "slug": "designer", "location": ["London"]},
            ]
        }
        html = _html_with_rsc_data(rsc_data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RSC, client)
        assert isinstance(result, list)
        assert len(result) == 2

    async def test_job_fields_mapped(self):
        rsc_data = {
            "jobs": [
                {"title": "Engineer", "slug": "engineer", "location": ["NYC", "SF"]},
            ]
        }
        html = _html_with_rsc_data(rsc_data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RSC, client)
        eng = result[0]
        assert eng.title == "Engineer"
        assert eng.locations == ["NYC", "SF"]

    async def test_no_data_returns_empty(self):
        html = "<html><body>No RSC data</body></html>"
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(BOARD_RSC, client)
        assert result == []

    async def test_recovers_when_component_tree_path_shifts(self):
        jobs = [
            {
                "id": f"job-{i}",
                "position": {"name": "Watchmaker"},
                "description": f"Build watch movements {i}",
            }
            for i in range(6)
        ]
        html = _html_with_rsc_data({"children": ["$", "$component", None, {"items": jobs}]})
        board = {
            "board_url": "https://example.com/jobs",
            "metadata": {
                "source": "rsc",
                # A deploy inserted a component and shifted the old index.
                "path": "children[4][3].items",
                "url_template": "https://example.com/jobs/{id}",
                "fields": {
                    "title": "position.name",
                    "description": "description",
                },
            },
        }

        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            result = await discover(board, client)

        assert len(result) == 6
        assert result[0].title == "Watchmaker"
        assert result[0].description == "Build watch movements 0"


def _onlyfy_rsc_data(page: int, *, page_count: int = 2, page_size: int = 5) -> dict:
    start = (page - 1) * page_size
    jobs = [
        {
            "id": start + i,
            "title": f"Job {start + i}",
            "cityName": f"City {start + i}",
            "positionTypeName": "Full-time employee",
            "publishedAt": "2026-07-20T12:00:00+02:00",
            "jobAdUrl": f"https://acme.onlyfy.jobs/job/token{start + i}",
        }
        for i in range(page_size)
    ]
    return {
        "jobsData": {
            "data": jobs,
            "meta": {
                "totalItems": page_count * page_size,
                "currentPage": page,
                "itemsPerPage": page_size,
                "totalPages": page_count,
            },
        }
    }


def _onlyfy_transport(page_count: int = 2, page_size: int = 5):
    def handler(request: httpx.Request):
        page = int(request.url.params.get("page", "1"))
        data = _onlyfy_rsc_data(page, page_count=page_count, page_size=page_size)
        return httpx.Response(200, text=_html_with_rsc_data(data))

    return httpx.MockTransport(handler)


class TestOnlyfyRsc:
    async def test_can_handle_returns_ready_rich_config(self):
        transport = _onlyfy_transport()
        async with httpx.AsyncClient(transport=transport) as client:
            result = await can_handle("https://acme.onlyfy.jobs/en", client)

        assert result == {
            "source": "rsc",
            "path": "jobsData.data",
            "count": 10,
            "url_template": "{jobAdUrl}",
            "url_transform": {
                "find": "/job/",
                "replace": "/candidate/job/print/",
            },
            "fields": {
                "title": "title",
                "locations": "cityName",
                "employment_type": "positionTypeName",
                "date_posted": "publishedAt",
            },
            "pagination": {
                "path": "jobsData.meta",
                "page_count": "totalPages",
                "page_param": "page",
            },
        }

    async def test_monitor_one_paginates_and_maps_print_urls_once(self):
        transport = _onlyfy_transport()
        async with httpx.AsyncClient(transport=transport) as client:
            config = await can_handle("https://acme.onlyfy.jobs/en", client)
            result = await monitor_one(
                "https://acme.onlyfy.jobs/en",
                "nextdata",
                config,
                client,
            )

        assert result.jobs_by_url is not None
        jobs = list(result.jobs_by_url.values())
        assert len(jobs) == 10
        assert jobs[0] == DiscoveredJob(
            url="https://acme.onlyfy.jobs/candidate/job/print/token0",
            title="Job 0",
            locations=["City 0"],
            employment_type="Full-time employee",
            date_posted="2026-07-20T12:00:00+02:00",
        )
        assert jobs[-1].url.endswith("/candidate/job/print/token9")
        assert all("/candidate/candidate/" not in job.url for job in jobs)

    async def test_monitor_stream_maps_print_urls_once(self):
        transport = _onlyfy_transport()
        async with httpx.AsyncClient(transport=transport) as client:
            config = await can_handle("https://acme.onlyfy.jobs/en", client)
            batches = [
                batch
                async for batch in monitor_one_stream(
                    "https://acme.onlyfy.jobs/en",
                    "nextdata",
                    config,
                    client,
                )
            ]

        jobs = [job for batch in batches for job in (batch.jobs_by_url or {}).values()]
        assert len(jobs) == 10
        assert jobs[0].url.endswith("/candidate/job/print/token0")
        assert jobs[-1].url.endswith("/candidate/job/print/token9")
        assert all("/candidate/candidate/" not in job.url for job in jobs)


# ---------------------------------------------------------------------------
# Phenom Canvas source
# ---------------------------------------------------------------------------


def _html_with_canvas_ddo(data: dict) -> str:
    """Wrap a phApp.ddo assignment in a minimal HTML page.

    Trailing JS after the assignment emulates the real Canvas pages
    (bracket-counting should stop at the correct brace).
    """
    payload = json.dumps(data)
    return (
        "<html><body><script>"
        f"phApp.ddo = {payload};"
        "phApp.somethingElse = true;"
        "</script></body></html>"
    )


def _canvas_ddo(
    *,
    total: int,
    offset: int,
    page_size: int,
    ref_num: str = "TESTREF",
) -> dict:
    """Build a Canvas-shaped phApp.ddo for a specific ``?from=<offset>`` page."""
    remaining = max(0, total - offset)
    take = min(page_size, remaining)
    jobs = [
        {
            "jobId": f"R{offset + i:04d}",
            "title": f"Job {offset + i}",
            "descriptionTeaser": f"Teaser {offset + i}",
            "multi_location": [f"City{offset + i}"],
            "type": "Full time",
            "remote": "On-Site",
            "postedDate": "2026-04-01T00:00:00.000+0000",
        }
        for i in range(take)
    ]
    return {
        "siteConfig": {"data": {"refNum": ref_num, "size": page_size}},
        "eagerLoadRefineSearch": {
            "hits": take,
            "totalHits": total,
            "data": {"jobs": jobs},
        },
    }


def _canvas_transport(total: int, page_size: int):
    """Return HTML pages based on the ``from`` query parameter."""

    def handler(request: httpx.Request):
        from urllib.parse import parse_qs, urlparse

        parsed = urlparse(str(request.url))
        qs = parse_qs(parsed.query)
        offset = int(qs.get("from", ["0"])[0])
        data = _canvas_ddo(total=total, offset=offset, page_size=page_size)
        return httpx.Response(200, text=_html_with_canvas_ddo(data))

    return httpx.MockTransport(handler)


BOARD_CANVAS = {
    "board_url": "https://example.com/us/en/search-results",
    "metadata": {
        "source": "phenom_canvas",
        "path": "eagerLoadRefineSearch.data.jobs",
        "url_template": "https://example.com/us/en/job/{jobId}",
        "fields": {
            "title": "title",
            "description": "descriptionTeaser",
            "locations": "multi_location",
            "employment_type": "type",
        },
        "pagination": {
            "mode": "offset",
            "path": "eagerLoadRefineSearch",
            "total_records": "totalHits",
            "page_size": 25,
            "offset_param": "from",
        },
    },
}


class TestPhenomCanvasExtraction:
    """phApp.ddo is extracted via bracket-counting, not script id."""

    def test_extract_embedded_json_canvas(self):
        from src.shared.nextdata import extract_embedded_json

        data = {
            "siteConfig": {"data": {"refNum": "ABC"}},
            "eagerLoadRefineSearch": {"totalHits": 5},
        }
        html = _html_with_canvas_ddo(data)
        result = extract_embedded_json(html, source="phenom_canvas")
        assert result == data

    def test_extract_stops_at_correct_brace(self):
        """Trailing JS with unmatched braces must not confuse the parser."""
        from src.shared.nextdata import extract_phenom_canvas_data

        html = (
            '<script>phApp.ddo = {"a": 1, "b": {"c": 2}};'
            'phApp.other = {"unclosed":'  # trailing junk
            "</script>"
        )
        assert extract_phenom_canvas_data(html) == {"a": 1, "b": {"c": 2}}

    def test_missing_assignment_returns_none(self):
        from src.shared.nextdata import extract_phenom_canvas_data

        assert extract_phenom_canvas_data("<html>no canvas here</html>") is None


class TestCanvasDiscover:
    async def test_small_board_single_page(self):
        """22 jobs at page_size=25 → no extra fetches."""
        transport = _canvas_transport(total=22, page_size=25)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_CANVAS, client)
        assert isinstance(result, list)
        assert len(result) == 22
        ids = {j.url.rsplit("/", 1)[-1] for j in result}
        assert len(ids) == 22  # all unique

    async def test_multi_page_offset_pagination(self):
        """157 jobs at page_size=25 → 7 pages; merges to 157 unique URLs."""
        transport = _canvas_transport(total=157, page_size=25)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_CANVAS, client)
        assert len(result) == 157
        ids = {j.url.rsplit("/", 1)[-1] for j in result}
        assert len(ids) == 157

    async def test_fields_extracted(self):
        transport = _canvas_transport(total=3, page_size=10)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(BOARD_CANVAS, client)
        first = result[0]
        assert first.title == "Job 0"
        assert first.description == "Teaser 0"
        assert first.locations == ["City0"]
        assert first.employment_type == "Full time"


class TestOffsetPaginationHelpers:
    def test_compute_page_urls_offset(self):
        from src.core.monitors.nextdata import _compute_page_urls

        cfg = {"mode": "offset", "page_size": 25, "offset_param": "from"}
        urls = _compute_page_urls("https://x.com/jobs", page_count=4, cfg=cfg)
        assert urls == [
            "https://x.com/jobs?from=25",
            "https://x.com/jobs?from=50",
            "https://x.com/jobs?from=75",
        ]

    def test_compute_page_urls_page_mode_default(self):
        from src.core.monitors.nextdata import _compute_page_urls

        urls = _compute_page_urls("https://x.com/jobs", page_count=3, cfg={})
        assert urls == [
            "https://x.com/jobs?page=2",
            "https://x.com/jobs?page=3",
        ]

    def test_offset_page_count_one_returns_empty(self):
        from src.core.monitors.nextdata import _compute_page_urls

        cfg = {"mode": "offset", "page_size": 25}
        assert _compute_page_urls("https://x.com/jobs", page_count=1, cfg=cfg) == []


class TestCanvasAutoDetect:
    """Monitor and scraper can_handle detect Phenom Canvas from plain HTML."""

    async def test_monitor_can_handle_phenom_canvas(self):
        """Returns source=phenom_canvas plus a ready pagination stub."""
        jobs = [{"jobId": f"R{i}", "title": f"Job {i}", "multi_location": ["X"]} for i in range(10)]
        data = {
            "siteConfig": {"data": {"refNum": "ACME"}},
            "eagerLoadRefineSearch": {
                "hits": 10,
                "totalHits": 250,
                "data": {"jobs": jobs},
            },
        }
        html = _html_with_canvas_ddo(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            meta = await can_handle("https://x.com/us/en/search-results", client)
        assert meta is not None
        assert meta["source"] == "phenom_canvas"
        assert meta["path"] == "eagerLoadRefineSearch.data.jobs"
        assert meta["count"] == 10
        assert meta["total"] == 250
        assert meta["url_template"] == "https://x.com/us/en/job/{jobId}"
        assert meta["pagination"]["mode"] == "offset"
        assert meta["pagination"]["page_size"] == 10
        assert meta["pagination"]["offset_param"] == "from"

    async def test_monitor_can_handle_phenom_canvas_preserves_locale_with_query(self):
        jobs = [{"jobId": f"R{i}", "title": f"Job {i}"} for i in range(10)]
        data = {
            "eagerLoadRefineSearch": {
                "hits": 10,
                "totalHits": 10,
                "data": {"jobs": jobs},
            },
        }
        html = _html_with_canvas_ddo(data)
        async with httpx.AsyncClient(transport=_mock_transport(html)) as client:
            meta = await can_handle(
                "https://careers.example.com/global/en/search-results/?from=10",
                client,
            )

        assert meta is not None
        assert meta["url_template"] == ("https://careers.example.com/global/en/job/{jobId}")

    def test_scraper_can_handle_phenom_canvas_detail(self):
        """Embedded scraper auto-detects phApp.ddo detail pages at depth 3."""
        from src.core.scrapers.embedded import can_handle as sc_can_handle

        # jobDetail.data.job — typical Canvas detail shape
        detail_data = {
            "siteConfig": {"data": {}},
            "jobDetail": {
                "status": "success",
                "data": {
                    "job": {
                        "title": "Engineer",
                        "description": "<p>Full description</p>",
                        "multi_location": [{"location": "NYC"}],
                        "type": "Full time",
                        "postedDate": "2026-04-01T00:00:00Z",
                    }
                },
            },
        }
        html = _html_with_canvas_ddo(detail_data)
        cfg = sc_can_handle([html, html])  # majority threshold needs >=1/2
        assert cfg is not None
        assert cfg["variable"] == "phApp.ddo"
        assert cfg["path"] == "jobDetail.data.job"
        assert "title" in cfg["fields"]
        assert "description" in cfg["fields"]


def test_extract_field_selects_dynamic_object_keys_by_pattern():
    item = {
        "attributes": {
            "verwaltungseinheit": ["Federal Department"],
            "verwaltungseinheit_1083359": ["Federal Office"],
            "unrelated": ["Ignore me"],
        }
    }

    assert (
        _extract_field(
            item,
            {"path": "attributes", "key_pattern": r"^verwaltungseinheit_"},
        )
        == "Federal Office"
    )


def test_extract_field_dynamic_key_pattern_fails_closed_on_wrong_shape():
    with pytest.raises(ValueError, match="resolve to an object"):
        _extract_field(
            {"attributes": []},
            {"path": "attributes", "key_pattern": r"^verwaltungseinheit_"},
        )
