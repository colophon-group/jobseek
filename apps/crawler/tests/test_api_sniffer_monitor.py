"""Tests for the api_sniffer monitor."""

from __future__ import annotations

import csv
import json
import re
from inspect import isawaitable
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.core.monitor import monitor_one
from src.core.monitors import DiscoveredJob
from src.core.monitors.api_sniffer import (
    ApiSnifferFallbackError,
    _apply_item_filter,
    _apply_pdf_document_gate,
    _build_item_projector,
    _configured_post_data,
    _detect_prospective_config,
    _discover_live_url,
    _extract_rich,
    _extract_urls_from_template,
    _lumesse_config_overrides,
    _matches_explicit_empty_response,
    _matches_url_field_contract,
    _materially_below_advertised_total,
    _paginate_until_converged,
    _refresh_post_data,
    _serialize_post_data,
    _validated_item_filter,
    _validated_pagination_convergence,
    _validated_required_pdf_pattern,
    _validated_slug_fields,
    _validated_url_field_match,
    can_handle,
    discover,
)


def _board_row(board_slug: str) -> dict[str, str]:
    boards_path = Path(__file__).resolve().parents[1] / "data" / "boards.csv"
    with boards_path.open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row["board_slug"] == board_slug)


async def _monitor_fenaco_payload(payload: object):
    config = json.loads(_board_row("fenaco-main")["monitor_config"])
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json=payload, request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        return await monitor_one(
            "https://jobs.fenaco.com/",
            "api_sniffer",
            config,
            client,
        )


@pytest.mark.asyncio
async def test_fenaco_uses_stable_viewkey_identity_across_locales_and_titles():
    config = json.loads(_board_row("fenaco-main")["monitor_config"])
    viewkey = "6f811874-a6d0-48f5-9d6b-57c369861d2a"
    result = await _monitor_fenaco_payload(
        {
            "jobs": [
                {
                    "viewkey": viewkey,
                    "title": "Leiterin Verkauf",
                    "language": "de",
                    "links": {
                        "directlink": (
                            f"https://jobs.fenaco.com/offene-stellen/leiterin-verkauf/{viewkey}"
                        )
                    },
                },
                {
                    "viewkey": viewkey,
                    "title": "Responsable des ventes",
                    "language": "fr",
                    "links": {
                        "directlink": (
                            "https://jobs.fenaco.com/postes-vacants/responsable-des-ventes/"
                            f"{viewkey}"
                        )
                    },
                },
            ],
            "total": 2,
        }
    )

    canonical_url = f"https://jobs.fenaco.com/offene-stellen/_/{viewkey}"
    assert result.urls == {canonical_url}
    assert result.jobs_by_url is not None
    assert set(result.jobs_by_url) == {canonical_url}
    assert result.security_filtered_count == 0
    assert not result.truncated
    assert re.fullmatch(config["url_allowlist"], canonical_url)
    assert not re.fullmatch(
        config["url_allowlist"],
        "https://jobs.fenaco.com/offene-stellen/_/not-a-provider-uuid",
    )


@pytest.mark.asyncio
async def test_fenaco_accepts_only_authoritative_empty_jobs_envelope():
    result = await _monitor_fenaco_payload({"jobs": [], "total": 0})

    assert result.urls == set()
    assert result.jobs_by_url == {}
    assert result.security_filtered_count == 0
    assert not result.truncated


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        pytest.param({"total": 0}, id="missing-jobs-even-with-zero-total"),
        pytest.param({"jobs": [], "total": False}, id="boolean-false-total"),
        pytest.param({"jobs": [], "total": 0.0}, id="floating-zero-total"),
        pytest.param(
            {
                "jobs": [
                    {
                        "title": "Title-bearing URL must not become identity",
                        "links": {
                            "directlink": (
                                "https://jobs.fenaco.com/offene-stellen/title-bearing-url/"
                                "6f811874-a6d0-48f5-9d6b-57c369861d2a"
                            )
                        },
                    }
                ],
                "total": 1,
            },
            id="missing-viewkey",
        ),
        pytest.param(
            {
                "jobs": [
                    {
                        "viewkey": "not-a-provider-uuid",
                        "title": "Invalid identity",
                    }
                ],
                "total": 1,
            },
            id="invalid-viewkey",
        ),
        pytest.param(
            {
                "jobs": [
                    {
                        "viewkey": "6F811874-A6D0-48F5-9D6B-57C369861D2A",
                        "title": "Uppercase UUID alias",
                    }
                ],
                "total": 1,
            },
            id="noncanonical-uppercase-viewkey",
        ),
        pytest.param(
            {
                "jobs": [
                    {
                        "viewkey": "6f811874-a6d0-48f5-9d6b-57c369861d2a",
                        "title": "Valid row",
                    },
                    None,
                ],
                "total": 2,
            },
            id="mixed-non-object-row",
        ),
    ],
)
async def test_fenaco_schema_or_identity_loss_fails_before_normalization(payload):
    with pytest.raises(ValueError):
        await _monitor_fenaco_payload(payload)


def test_fenaco_config_requires_identity_and_provider_boundary():
    config = json.loads(_board_row("fenaco-main")["monitor_config"])

    assert "url_filter" not in config
    assert "url_allowlist" in config
    assert config["empty_response"] == {"jobs": [], "total": 0}
    assert config["item_filter"]["dedupe_by"] == ["viewkey"]
    assert re.fullmatch(
        config["item_filter"]["require_regex"]["viewkey"],
        "6f811874-a6d0-48f5-9d6b-57c369861d2a",
    )
    assert not re.fullmatch(
        config["item_filter"]["require_regex"]["viewkey"],
        "6F811874-A6D0-48F5-9D6B-57C369861D2A",
    )
    assert not re.fullmatch(
        config["url_allowlist"],
        "https://jobs.fenaco.com/offene-stellen/_/6F811874-A6D0-48F5-9D6B-57C369861D2A",
    )


@pytest.mark.asyncio
async def test_fenaco_paginated_non_object_identity_fails_closed():
    config = json.loads(_board_row("fenaco-main")["monitor_config"])
    viewkey = "6f811874-a6d0-48f5-9d6b-57c369861d2a"

    def handler(request: httpx.Request) -> httpx.Response:
        offset = int(request.url.params.get("offset", "0"))
        jobs = [{"viewkey": viewkey, "title": "Valid row"}] if offset == 0 else [None]
        return httpx.Response(200, json={"jobs": jobs, "total": 2}, request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError, match="non-object.*index 0"):
            await monitor_one(
                "https://jobs.fenaco.com/",
                "api_sniffer",
                config,
                client,
            )


def _auto_detected_mixed_payload() -> dict[str, list[dict[str, str] | None]]:
    return {
        "jobs": [
            {"id": "101", "title": "One"},
            {"id": "102", "title": "Two"},
            {"id": "103", "title": "Three"},
            None,
        ]
    }


async def _monitor_auto_detected_mixed_payload(*, strict: bool):
    config = {
        "api_url": "https://api.example.com/jobs",
        "url_template": "https://jobs.example.com/jobs/{id}",
        "fields": {"title": "title"},
    }
    if strict:
        config["item_filter"] = {"require_regex": {"id": r"[0-9]+"}}

    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            json=_auto_detected_mixed_payload(),
            request=request,
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        return await monitor_one(
            "https://jobs.example.com/",
            "api_sniffer",
            config,
            client,
        )


@pytest.mark.asyncio
async def test_auto_detected_initial_array_rejects_non_object_with_required_identity():
    with pytest.raises(ValueError, match="non-object.*index 3"):
        await _monitor_auto_detected_mixed_payload(strict=True)


@pytest.mark.asyncio
async def test_auto_detected_initial_array_preserves_non_strict_compatibility():
    result = await _monitor_auto_detected_mixed_payload(strict=False)

    assert result.urls == {
        "https://jobs.example.com/jobs/101",
        "https://jobs.example.com/jobs/102",
        "https://jobs.example.com/jobs/103",
    }
    assert result.jobs_by_url is not None
    assert len(result.jobs_by_url) == 3


def _lumesse_items():
    return [
        {
            "id": 160572,
            "customFields": [
                {"title": "Job Purpose", "content": "<p>Build data products.</p>"},
                {"title": "Experience", "content": "<p>Five years required.</p>"},
            ],
            "jobFields": {
                "jobTitle": "Full Stack Developer",
                "FFIELD008_001": "Morocco, Lebanon, Egypt or Jordan",
                "SLOVLIST2": "MENA Different Locations",
                "SLOVLIST7": "National",
                "jobNumber": "S18240",
                "externalJobNumber": "0441796",
                "applicationUrl": "https://emea3.recruitmentplatform.com/apply/160572",
            },
        }
    ]


def test_lumesse_config_overrides_build_rich_canonical_config():
    items = _lumesse_items()
    config = _lumesse_config_overrides(
        "https://careers.ifrc.org/lumesse_jobsearch.html",
        "https://emea3.recruitmentplatform.com/fo/rest/jobs?firstResult=0",
        items,
        {"globals": {"jobsCount": 21}, "jobs": items},
    )

    assert config is not None
    assert config["browser"] is False
    assert config["total_path"] == "globals.jobsCount"
    assert config["total"] == 21
    assert config["url_template"] == (
        "https://careers.ifrc.org/lumesse_jobdescription.html?jobId={id}"
    )

    jobs = _extract_rich(
        items,
        config["fields"],
        None,
        config["url_template"],
        "https://careers.ifrc.org/lumesse_jobsearch.html",
    )
    assert len(jobs) == 1
    assert jobs[0].url == "https://careers.ifrc.org/lumesse_jobdescription.html?jobId=160572"
    assert jobs[0].title == "Full Stack Developer"
    assert jobs[0].locations == ["Morocco, Lebanon, Egypt or Jordan"]
    assert jobs[0].description == (
        "<h3>Job Purpose</h3>\n<p>Build data products.</p>\n\n"
        "<h3>Experience</h3>\n<p>Five years required.</p>"
    )
    assert jobs[0].metadata == {
        "ats_job_id": "160572",
        "job_number": "S18240",
        "external_job_number": "0441796",
        "scope": "National",
        "apply_url": "https://emea3.recruitmentplatform.com/apply/160572",
    }


@pytest.mark.parametrize(
    ("board_url", "api_url"),
    [
        (
            "https://careers.ifrc.org/jobs",
            "https://emea3.recruitmentplatform.com/fo/rest/jobs",
        ),
        (
            "https://careers.ifrc.org/lumesse_jobsearch.html",
            "https://example.com/fo/rest/jobs",
        ),
        (
            "http://careers.ifrc.org/lumesse_jobsearch.html",
            "https://emea3.recruitmentplatform.com/fo/rest/jobs",
        ),
    ],
)
def test_lumesse_config_overrides_rejects_noncanonical_endpoints(board_url, api_url):
    items = _lumesse_items()
    assert _lumesse_config_overrides(board_url, api_url, items, {"jobs": items}) is None


def test_serialize_post_data_accepts_json_values_and_existing_strings():
    assert _serialize_post_data({"pageNo": 1}) == '{"pageNo":1}'
    assert _serialize_post_data(["one", 2]) == '["one",2]'
    assert _serialize_post_data("page=1") == "page=1"
    assert _serialize_post_data(None) is None
    with pytest.raises(ValueError, match="post_data must be"):
        _serialize_post_data(1)


def test_configured_post_data_preserves_explicit_empty_json():
    assert _configured_post_data({"post_data": {}, "post_body": "fallback=1"}) == "{}"
    assert _configured_post_data({"post_data": [], "post_body": "fallback=1"}) == "[]"
    assert _configured_post_data({"post_body": "fallback=1"}) == "fallback=1"


class TestPostDataRefresh:
    @pytest.mark.asyncio
    async def test_refreshes_urlencoded_token_and_establishes_page_session(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            return httpx.Response(
                200,
                text='<section data-action="get_offers" data-nonce="fresh-123"></section>',
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await _refresh_post_data(
                client,
                "https://example.com/careers",
                "action=get_offers&nonce=stale&page=1",
                {
                    "fields": {
                        "nonce": r'data-action="get_offers"[^>]*data-nonce="([^"]+)"',
                    }
                },
            )

        assert result == "action=get_offers&nonce=fresh-123&page=1"
        assert [str(request.url) for request in requests] == ["https://example.com/careers"]

    @pytest.mark.asyncio
    async def test_missing_token_fails_instead_of_returning_false_empty(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html></html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="did not match one bounded value"):
                await _refresh_post_data(
                    client,
                    "https://example.com/careers",
                    "nonce=stale",
                    {"fields": {"nonce": r'data-nonce="([^"]+)"'}},
                )

    @pytest.mark.asyncio
    async def test_pattern_definition_requires_exactly_one_capture_group(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text='<section data-key="nonce" data-nonce="fresh"></section>',
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="exactly one capture group"):
                await _refresh_post_data(
                    client,
                    "https://example.com/careers",
                    "nonce=stale",
                    {"fields": {"nonce": r'data-(key)="nonce".*data-nonce="([^"]+)"'}},
                )

    @pytest.mark.asyncio
    async def test_http_discovery_refreshes_token_before_api_replay(self):
        requests: list[httpx.Request] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requests.append(request)
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text='<section data-action="jobs" data-nonce="fresh"></section>',
                    request=request,
                )
            return httpx.Response(
                200,
                json={"jobs": [{"url": "https://example.com/jobs/1"}]},
                request=request,
            )

        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "method": "POST",
                "request_headers": {"content-type": "application/x-www-form-urlencoded"},
                "post_data": "action=jobs&nonce=stale&page=1",
                "post_data_refresh": {
                    "fields": {
                        "nonce": r'data-action="jobs"[^>]*data-nonce="([^"]+)"',
                    }
                },
                "json_path": "jobs",
                "url_field": "url",
            },
        }

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert result == {"https://example.com/jobs/1"}
        assert [request.method for request in requests] == ["GET", "POST"]
        assert requests[1].content == b"action=jobs&nonce=fresh&page=1"

    @pytest.mark.asyncio
    async def test_explicit_empty_response_requires_all_markers(self):
        response_payload = {
            "status": 201,
            "action": "get_offers",
            "label": "No offer available",
        }

        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text='<section data-nonce="fresh"></section>',
                    request=request,
                )
            return httpx.Response(200, json=response_payload, request=request)

        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "method": "POST",
                "post_data": "nonce=stale",
                "post_data_refresh": {"fields": {"nonce": r'data-nonce="([^"]+)"'}},
                "json_path": "elements",
                "url_field": "link",
                "empty_response": response_payload,
            },
        }

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await discover(board, client) == set()

    @pytest.mark.asyncio
    async def test_unrecognized_missing_job_list_fails_closed(self):
        def handler(request: httpx.Request) -> httpx.Response:
            if request.method == "GET":
                return httpx.Response(
                    200,
                    text='<section data-nonce="fresh"></section>',
                    request=request,
                )
            return httpx.Response(
                200,
                json={"status": 403, "label": "Invalid nonce"},
                request=request,
            )

        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "method": "POST",
                "post_data": "nonce=stale",
                "post_data_refresh": {"fields": {"nonce": r'data-nonce="([^"]+)"'}},
                "json_path": "elements",
                "url_field": "link",
                "empty_response": {"status": 201, "label": "No offer available"},
            },
        }

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="neither a job list"):
                await discover(board, client)


class TestHtmlExplicitEmptyResponse:
    @staticmethod
    def _board() -> dict:
        return {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/wp-json/wp/v2/pages?slug=jobs",
                "method": "GET",
                "json_path": "[0].content.rendered",
                "url_regex": r'href="(https://example\.com/uploads/[^" ]+\.pdf)',
                "empty_response": {
                    "[0].id": 44033,
                    "[0].content.protected": False,
                },
            },
        }

    @staticmethod
    async def _discover(payload: object, *, status_code: int = 200):
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(status_code, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await discover(TestHtmlExplicitEmptyResponse._board(), client)

    @pytest.mark.asyncio
    async def test_matching_list_root_markers_authorize_zero_links(self):
        payload = [
            {
                "id": 44033,
                "content": {"protected": False, "rendered": "<div>No links yet</div>"},
            }
        ]

        assert await self._discover(payload) == set()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            [
                {
                    "id": 99999,
                    "content": {"protected": False, "rendered": "<div>No links</div>"},
                }
            ],
            [
                {
                    "id": 44033,
                    "content": {"protected": True, "rendered": "<div>No links</div>"},
                }
            ],
        ],
    )
    async def test_wrong_empty_markers_fail_closed(self, payload):
        with pytest.raises(ValueError, match="did not match the configured empty response"):
            await self._discover(payload)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            [{"id": 44033, "content": {"protected": False}}],
            [],
        ],
    )
    async def test_missing_html_content_fails_even_when_identity_markers_match(self, payload):
        with pytest.raises(ValueError, match="explicit empty-response validation"):
            await self._discover(payload)

    @pytest.mark.asyncio
    async def test_http_404_fails_instead_of_becoming_empty(self):
        with pytest.raises(ValueError, match="did not return the configured explicit empty"):
            await self._discover({}, status_code=404)

    @pytest.mark.asyncio
    async def test_nonempty_links_do_not_require_empty_markers(self):
        payload = [
            {
                "id": 99999,
                "content": {
                    "protected": True,
                    "rendered": (
                        '<a href="https://example.com/uploads/Job-Vacancy-Engineer.pdf">Vacancy</a>'
                    ),
                },
            }
        ]

        assert await self._discover(payload) == {
            "https://example.com/uploads/Job-Vacancy-Engineer.pdf"
        }


class TestListExplicitEmptyResponse:
    @staticmethod
    async def _discover(payload: object):
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "json_path": "data",
                "url_field": "url",
                "fields": {"title": "title"},
                "empty_response": {"meta.filter_count": 0},
            },
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            return await discover(board, client)

    @pytest.mark.asyncio
    async def test_matching_marker_authorizes_empty_list(self):
        assert await self._discover({"meta": {"filter_count": 0}, "data": []}) == []

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "payload",
        [
            {"meta": {"filter_count": 1}, "data": []},
            {"data": []},
        ],
    )
    async def test_missing_or_wrong_marker_fails_closed(self, payload):
        with pytest.raises(ValueError, match="empty job list"):
            await self._discover(payload)

    def test_exact_empty_list_marker_distinguishes_missing_path(self):
        markers = {"jobs": [], "total": 0}

        assert _matches_explicit_empty_response({"jobs": [], "total": 0}, markers)
        assert not _matches_explicit_empty_response({"total": 0}, markers)

    @pytest.mark.parametrize("total", [False, 0.0])
    def test_empty_scalar_markers_require_exact_json_type(self, total):
        assert not _matches_explicit_empty_response(
            {"jobs": [], "total": total},
            {"jobs": [], "total": 0},
        )

    @pytest.mark.parametrize("expected", [["unexpected"], {}, {"nested": True}])
    def test_rejects_structured_nonempty_marker_values(self, expected):
        with pytest.raises(ValueError, match=r"JSON scalars or the \[\] marker"):
            _matches_explicit_empty_response({"jobs": expected}, {"jobs": expected})


class TestPdfDocumentGate:
    @staticmethod
    def _metadata() -> dict:
        return {
            "require_pdf_pattern": r"(?i)\bEuropean Athletics\b",
            "require_unexpired_pdf": {
                "pattern": r"Deadline: (\d{1,2} [A-Za-z]+ \d{4})",
                "date_format": "%d %B %Y",
            },
        }

    @pytest.mark.asyncio
    async def test_applies_bounded_pdf_filter_to_url_only_result(self):
        urls = {"https://example.com/jobs/role.pdf"}
        expected = set()
        with patch(
            "src.core.monitors.dom._filter_unexpired_pdf_urls",
            AsyncMock(return_value=(expected, {})),
        ) as filter_pdfs:
            result = await _apply_pdf_document_gate(urls, AsyncMock(), self._metadata())

        assert result == expected
        filter_pdfs.assert_awaited_once()
        args, kwargs = filter_pdfs.await_args
        assert args[0] == urls
        assert args[2].deadlines[0].date_format == "%d %B %Y"
        assert kwargs["required_text_pattern"].pattern == r"(?i)\bEuropean Athletics\b"
        assert kwargs["raise_on_required_text_mismatch"] is True
        assert kwargs["return_deadlines"] is True

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "metadata",
        [
            {"require_pdf_pattern": r"European Athletics"},
            {
                "require_unexpired_pdf": {
                    "pattern": r"Deadline: (\d{1,2} [A-Za-z]+ \d{4})",
                    "date_format": "%d %B %Y",
                }
            },
        ],
    )
    async def test_requires_ownership_and_deadline_checks_together(self, metadata):
        with pytest.raises(ValueError, match="requires both"):
            await _apply_pdf_document_gate(set(), AsyncMock(), metadata)

    @pytest.mark.parametrize("value", [True, "", "(", "x" * 1_025])
    def test_rejects_invalid_required_pdf_pattern(self, value):
        with pytest.raises(ValueError, match="require_pdf_pattern"):
            _validated_required_pdf_pattern(value)

    @pytest.mark.asyncio
    async def test_filters_rich_discovery_without_losing_structured_fields(self):
        active = DiscoveredJob(
            url="https://example.com/jobs/active.pdf",
            title="Communications Manager",
            locations=["Lausanne"],
        )
        foreign = DiscoveredJob(
            url="https://example.com/jobs/member-role.pdf",
            title="Member Federation Role",
            locations=["Copenhagen"],
        )
        with patch(
            "src.core.monitors.dom._filter_unexpired_pdf_urls",
            AsyncMock(return_value=({active.url}, {active.url: "2999-12-31"})),
        ):
            result = await _apply_pdf_document_gate(
                [active, foreign],
                AsyncMock(),
                {**self._metadata(), "fields": {"title": "title"}},
            )

        assert result == [active]
        assert result[0].title == "Communications Manager"
        assert result[0].locations == ["Lausanne"]
        assert result[0].extras == {"valid_through": "2999-12-31"}


class TestEuropeanAthleticsDirectusConfig:
    PDF_URL = (
        "https://european-athletics.directus.app/assets/"
        "11111111-2222-3333-4444-555555555555/role.pdf"
    )

    @staticmethod
    def _fake_reader(stream):
        from types import SimpleNamespace

        text = stream.read().removeprefix(b"%PDF ").decode()
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)])

    @classmethod
    def _payload(cls) -> dict:
        return {
            "meta": {"filter_count": 1, "total_count": 2},
            "data": [
                {
                    "id": 69,
                    "title": "Communications Manager",
                    "location": "Lausanne, Switzerland",
                    "work_location_type": "On site",
                    "work_hours": "Full Time",
                    "status": "published",
                    "date_created": "2999-01-02T03:04:05",
                    "date_updated": "2999-01-03T04:05:06",
                    "pdf_job_description": {
                        "id": "11111111-2222-3333-4444-555555555555",
                        "composed_url": cls.PDF_URL,
                        "filename_download": "role.pdf",
                        "type": "application/pdf",
                    },
                }
            ],
        }

    @staticmethod
    def _board() -> dict:
        row = _board_row("european-athletics-directus-careers")
        config = json.loads(row["monitor_config"])
        return {"board_url": row["board_url"], "metadata": config}

    @classmethod
    async def _discover(cls, payload: dict, pdf_text: str | None, monkeypatch):
        monkeypatch.setattr("pypdf.PdfReader", cls._fake_reader)

        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/items/careers_jobs":
                return httpx.Response(200, json=payload, request=request)
            assert pdf_text is not None
            return httpx.Response(200, content=f"%PDF {pdf_text}".encode(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            return await discover(cls._board(), client)

    @pytest.mark.asyncio
    async def test_reviewed_foreign_item_is_excluded_upstream_and_zero_is_authoritative(
        self, monkeypatch
    ):
        board = self._board()
        params = board["metadata"]["params"]

        assert params["filter[id][_neq]"] == "68"
        result = await self._discover(
            {"meta": {"filter_count": 0, "total_count": 1}, "data": []},
            None,
            monkeypatch,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_owned_future_pdf_keeps_api_fields_and_adds_normalized_deadline(
        self, monkeypatch
    ):
        result = await self._discover(
            self._payload(),
            (
                "European Athletics is seeking a Communications Manager.\n"
                "Avenue Louis-Ruchonnet 16, Lausanne.\n"
                "Deadline for receipt of applications: Friday the 31st of December 2999"
            ),
            monkeypatch,
        )

        assert len(result) == 1
        job = result[0]
        assert job.url == self.PDF_URL
        assert job.title == "Communications Manager"
        assert job.locations == ["Lausanne, Switzerland"]
        assert job.employment_type == "Full Time"
        assert job.job_location_type == "On site"
        assert job.date_posted == "2999-01-02T03:04:05"
        assert job.metadata == {
            "ats_job_id": "69",
            "source_file_id": "11111111-2222-3333-4444-555555555555",
            "source_filename": "role.pdf",
            "source_updated_at": "2999-01-03T04:05:06",
        }
        assert job.extras == {"valid_through": "2999-12-31"}

    @pytest.mark.asyncio
    async def test_expired_owned_pdf_is_omitted(self, monkeypatch):
        result = await self._discover(
            self._payload(),
            (
                "European Athletic Association vacancy.\n"
                "jobs@european-athletics.org\n"
                "Deadline for receipt of applications: 1st of January 2000"
            ),
            monkeypatch,
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_unclassified_employer_fails_closed(self, monkeypatch):
        with pytest.raises(ValueError, match="required ownership markers"):
            await self._discover(
                self._payload(),
                (
                    "Danish Athletics is seeking a manager.\n"
                    "Deadline for receipt of applications: 31st of December 2999"
                ),
                monkeypatch,
            )

    @pytest.mark.asyncio
    async def test_owned_pdf_without_deadline_fails_closed(self, monkeypatch):
        with pytest.raises(ValueError, match="deadline was not found"):
            await self._discover(
                self._payload(),
                "European Athletics vacancy at Avenue Louis-Ruchonnet 16.",
                monkeypatch,
            )


class TestProspectiveDetection:
    @pytest.mark.asyncio
    async def test_can_handle_uses_plain_http_detection_without_playwright(self):
        expected = {"api_url": "https://ohws.prospective.ch/jobs"}
        with patch(
            "src.core.monitors.api_sniffer._detect_prospective_config",
            AsyncMock(return_value=expected),
        ):
            config = await can_handle(
                "https://jobs.example.com/",
                AsyncMock(),
                pw=None,
            )

        assert config is expected

    @pytest.mark.asyncio
    async def test_builds_rich_direct_api_config_from_careercenter_asset(self):
        html = """
        <html lang="fr">
          <link href="/careercenter/1002787/assets/css/company.css" rel="stylesheet">
        </html>
        """
        payload = {
            "medium_id": "1002787",
            "total": 38,
            "jobs": [
                {
                    "id": "101",
                    "title": "Analyste",
                    "links": {"directlink": "https://jobs.example.com/jobs/101"},
                }
            ],
        }
        client = AsyncMock()

        with (
            patch(
                "src.core.monitors.api_sniffer.fetch_text_page_with_retry",
                AsyncMock(return_value=html),
            ) as fetch_page,
            patch(
                "src.core.monitors.api_sniffer.http_fetch_with_retry",
                AsyncMock(return_value=payload),
            ) as fetch_api,
        ):
            config = await _detect_prospective_config(
                "https://jobs.example.com/",
                client,
            )

        assert config is not None
        assert config["api_url"] == ("https://ohws.prospective.ch/public/v1/medium/1002787/jobs")
        assert config["params"] == {"lang": "fr", "offset": "0", "limit": "12"}
        assert config["json_path"] == "jobs"
        assert config["total_path"] == "total"
        assert config["url_field"] == "links.directlink"
        assert config["url_filter"] == r"(?i)^https://jobs\.example\.com/"
        assert config["fields"]["locations"] == 'szas."sza_location.city"'
        assert config["fields"]["description"] == [
            "szas.sza_introduction",
            "szas.sza_tasks",
            "szas.sza_requirements",
        ]
        assert config["items"] == 1
        assert config["total"] == 38
        fetch_page.assert_awaited_once_with(
            client,
            "https://jobs.example.com/",
            retries=5,
            base_delay=0.5,
            retryable_statuses={403},
            require_nonempty=True,
            max_chars=250_000,
            log_event="api_sniffer.prospective_page_backoff",
        )
        api_url = fetch_api.await_args.args[2]
        assert api_url.startswith("https://ohws.prospective.ch/public/v1/medium/1002787/jobs?")
        assert "lang=fr" in api_url

    @pytest.mark.asyncio
    async def test_rejects_unverified_medium_payload(self):
        html = '<link href="/careercenter/1002787/assets/site.css">'
        with (
            patch(
                "src.core.monitors.api_sniffer.fetch_text_page_with_retry",
                AsyncMock(return_value=html),
            ),
            patch(
                "src.core.monitors.api_sniffer.http_fetch_with_retry",
                AsyncMock(return_value={"medium_id": "other", "jobs": []}),
            ),
        ):
            config = await _detect_prospective_config(
                "https://jobs.example.com/",
                AsyncMock(),
            )

        assert config is None

    @pytest.mark.asyncio
    async def test_rejects_mixed_or_untrusted_directlink_origins(self):
        html = '<link href="/careercenter/1002787/assets/site.css">'

        for directlink in (
            "http://jobs.example.com/jobs/101",
            "https://user@jobs.example.com/jobs/101",
            "https://jobs.example.com.evil.test/jobs/101",
        ):
            with (
                patch(
                    "src.core.monitors.api_sniffer.fetch_text_page_with_retry",
                    AsyncMock(return_value=html),
                ),
                patch(
                    "src.core.monitors.api_sniffer.http_fetch_with_retry",
                    AsyncMock(
                        return_value={
                            "medium_id": "1002787",
                            "jobs": [
                                {
                                    "id": "101",
                                    "title": "Analyst",
                                    "links": {"directlink": directlink},
                                }
                            ],
                        }
                    ),
                ),
            ):
                assert (
                    await _detect_prospective_config(
                        "https://jobs.example.com/",
                        AsyncMock(),
                    )
                    is None
                )


class TestItemProjector:
    def test_keeps_only_explicit_peoplestrong_fields(self):
        projector = _build_item_projector(
            {
                "title": "jobTitle",
                "locations": "locationHierarchy",
                "date_posted": "jobPostedDate",
            },
            "jobDetailUrl",
            None,
            {},
        )

        assert projector is not None
        item = {
            "jobTitle": "Engineer",
            "locationHierarchy": ["India", "Pune"],
            "jobPostedDate": "2026-07-21",
            "jobDetailUrl": "/job/123",
            "description": "x" * 50_000,
            "twenty_more_vendor_fields": {"large": "x" * 50_000},
        }
        assert projector(item) == {
            "jobTitle": "Engineer",
            "locationHierarchy": ["India", "Pune"],
            "jobPostedDate": "2026-07-21",
            "jobDetailUrl": "/job/123",
        }

    def test_keeps_nested_roots_lookup_keys_and_template_aliases(self):
        projector = _build_item_projector(
            {
                "title": "details.title",
                "locations": {"concat": ["office.city", "office.country"]},
                "metadata.team": {
                    "lookup_from": "lookups.departments",
                    "key_from": "department_id",
                },
            },
            None,
            "https://example.com/jobs/{external_id}/{itemID}",
            {"external_id": "custom.fields[0].value"},
        )

        assert projector is not None
        projected = projector(
            {
                "details": {"title": "Engineer", "unused": "large"},
                "office": {"city": "Paris", "country": "France"},
                "department_id": 7,
                "custom": {"fields": [{"value": "123"}]},
                "itemID": "abc",
                "unused": "large",
            }
        )
        assert set(projected) == {
            "details",
            "office",
            "department_id",
            "custom",
            "itemID",
        }

    def test_keeps_slug_field_for_template_projection(self):
        projector = _build_item_projector(
            {"title": "details.title"},
            None,
            "https://example.com/jobs/{id}/{slug}",
            {},
            ["details.title"],
        )

        assert projector is not None
        assert projector(
            {
                "id": "123",
                "details": {"title": "Senior Risk & Insurance Advisor"},
                "unused": "large",
            }
        ) == {
            "id": "123",
            "details": {"title": "Senior Risk & Insurance Advisor"},
        }

    def test_keeps_absolute_url_fallback(self):
        projector = _build_item_projector(
            {"title": "title"},
            "configured_url",
            None,
            {},
        )

        assert projector is not None
        assert projector(
            {
                "title": "Engineer",
                "configured_url": None,
                "canonical": "https://example.com/jobs/123",
                "unused": "large",
            }
        ) == {
            "title": "Engineer",
            "configured_url": None,
            "canonical": "https://example.com/jobs/123",
        }

    @pytest.mark.parametrize(
        ("fields", "url_field"),
        [
            ({}, "url"),
            ({"title": "jobs[?active].title"}, "url"),
            ({"title": "@"}, "url"),
            ({"title": "title"}, "url || canonical"),
        ],
    )
    def test_unsafe_or_auto_detected_configs_disable_compaction(self, fields, url_field):
        assert _build_item_projector(fields, url_field, None, {}) is None


class TestSlugFields:
    @pytest.mark.parametrize("value", ["title", [""], [1], {"title": True}])
    def test_rejects_invalid_config(self, value):
        with pytest.raises(ValueError, match="slug_fields"):
            _validated_slug_fields({"slug_fields": value})

    def test_accepts_field_path_list(self):
        assert _validated_slug_fields({"slug_fields": [" details.title "]}) == ["details.title"]


class TestItemFilter:
    @pytest.mark.asyncio
    async def test_rejects_auto_discovery_config(self):
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {"item_filter": {"exclude": {"market": ["local"]}}},
        }

        with pytest.raises(ValueError, match="item_filter requires a configured api_url"):
            await discover(board, AsyncMock(), pw=AsyncMock())

    def test_excludes_partitioned_values_and_deduplicates_stable_ids(self):
        config = {
            "item_filter": {
                "exclude": {"attributes.country": ["Switzerland", "USA"]},
                "exclude_regex": {"provider.name": ["^External(?: Agency)?$"]},
                "dedupe_by": ["provider.tenant_id", "provider.apply_id"],
            }
        }
        item_filter = _validated_item_filter(config)
        items = [
            {
                "url": "https://example.com/1",
                "attributes": {"country": ["Germany"]},
                "provider": {
                    "tenant_id": "tenant",
                    "apply_id": "stable-1",
                    "name": "Internal",
                },
            },
            {
                "url": "https://example.com/duplicate",
                "attributes": {"country": ["Germany"]},
                "provider": {
                    "tenant_id": "tenant",
                    "apply_id": "stable-1",
                    "name": "Internal",
                },
            },
            {
                "url": "https://example.com/other-tenant",
                "attributes": {"country": ["Germany"]},
                "provider": {
                    "tenant_id": "other",
                    "apply_id": "stable-1",
                    "name": "Internal",
                },
            },
            {
                "url": "https://example.com/usa",
                "attributes": {"country": ["USA"]},
                "provider": {
                    "tenant_id": "tenant",
                    "apply_id": "stable-2",
                    "name": "Internal",
                },
            },
            {
                "url": "https://example.com/external",
                "attributes": {"country": ["Germany"]},
                "provider": {
                    "tenant_id": "tenant",
                    "apply_id": "stable-3",
                    "name": "External Agency",
                },
            },
            {
                "url": "https://example.com/no-id",
                "attributes": {"country": ["Germany"]},
                "provider": {"tenant_id": "tenant", "apply_id": "", "name": "Internal"},
            },
        ]

        scoped, total = _apply_item_filter(items, item_filter, advertised_total=6)

        assert [item["url"] for item in scoped] == [
            "https://example.com/1",
            "https://example.com/other-tenant",
            "https://example.com/no-id",
        ]
        assert total == 3

    def test_include_fails_closed_for_missing_null_and_non_matching_values(self):
        item_filter = _validated_item_filter(
            {"item_filter": {"include": {"employer": ["Swiss Olympic"]}}}
        )
        items = [
            {"url": "https://example.com/exact", "employer": "Swiss Olympic"},
            {
                "url": "https://example.com/list",
                "employer": ["Partner", "Swiss Olympic"],
            },
            {"url": "https://example.com/other", "employer": "Swiss-Ski"},
            {"url": "https://example.com/empty", "employer": ""},
            {"url": "https://example.com/null", "employer": None},
            {"url": "https://example.com/empty-list", "employer": []},
            {"url": "https://example.com/missing"},
        ]

        scoped, total = _apply_item_filter(items, item_filter, advertised_total=7)

        assert [item["url"] for item in scoped] == [
            "https://example.com/exact",
            "https://example.com/list",
        ]
        assert total == 2

    def test_required_regex_validates_in_scope_items_before_deduplication(self):
        item_filter = _validated_item_filter(
            {
                "item_filter": {
                    "exclude": {"market": ["local"]},
                    "require_regex": {"viewkey": r"[0-9a-f]{8}"},
                    "dedupe_by": ["viewkey"],
                }
            }
        )

        scoped, total = _apply_item_filter(
            [
                {"market": "local", "viewkey": "invalid"},
                {"market": "global", "viewkey": "12ab34cd", "locale": "de"},
                {"market": "global", "viewkey": "12ab34cd", "locale": "fr"},
            ],
            item_filter,
            advertised_total=3,
        )

        assert scoped == [{"market": "global", "viewkey": "12ab34cd", "locale": "de"}]
        assert total == 1

    @pytest.mark.parametrize("item", [{}, {"viewkey": ""}, {"viewkey": "invalid"}])
    def test_required_regex_rejects_missing_or_invalid_in_scope_identity(self, item):
        item_filter = _validated_item_filter(
            {"item_filter": {"require_regex": {"viewkey": r"[0-9a-f]{8}"}}}
        )

        with pytest.raises(ValueError, match="missing or invalid.*viewkey"):
            _apply_item_filter([item], item_filter, advertised_total=1)

    def test_incomplete_upstream_total_remains_truncated_after_filtering(self):
        item_filter = _validated_item_filter({"item_filter": {"exclude": {"market": ["local"]}}})

        scoped, total = _apply_item_filter(
            [{"market": "global"}, {"market": "local"}],
            item_filter,
            advertised_total=10,
        )

        assert scoped == [{"market": "global"}]
        assert total == 9

    @pytest.mark.parametrize(
        ("advertised_total", "scoped_total"),
        [(4, 2), (6, 4)],
    )
    def test_source_total_drift_is_preserved_after_filtering(
        self,
        advertised_total,
        scoped_total,
    ):
        item_filter = _validated_item_filter({"item_filter": {"exclude": {"market": ["local"]}}})

        scoped, total = _apply_item_filter(
            [
                {"url": "https://example.com/1", "market": "global"},
                {"url": "https://example.com/2", "market": "local"},
                {"url": "https://example.com/3", "market": "local"},
                {"url": "https://example.com/4", "market": "global"},
                {"url": "https://example.com/5", "market": "global"},
            ],
            item_filter,
            advertised_total=advertised_total,
        )

        assert len(scoped) == 3
        assert total == scoped_total
        assert not _materially_below_advertised_total(len(scoped), total)

    def test_dedupe_preference_uses_preferred_locale_then_lexical_fallback(self):
        item_filter = _validated_item_filter(
            {
                "item_filter": {
                    "dedupe_by": ["req_id"],
                    "dedupe_preference": {
                        "path": "language",
                        "preferred_values": ["en-us"],
                        "fallback_by": ["language", "canonical_url", "raw_uuid"],
                    },
                }
            }
        )
        items = [
            {
                "req_id": "a",
                "language": "fr-fr",
                "canonical_url": "https://example.com/a?lang=fr-fr",
                "raw_uuid": "raw-a-fr",
            },
            {
                "req_id": "b",
                "language": "it-it",
                "canonical_url": "https://example.com/b?lang=it-it",
                "raw_uuid": "raw-b-it",
            },
            {
                "req_id": "a",
                "language": "en-us",
                "canonical_url": "https://example.com/a?lang=en-us",
                "raw_uuid": "raw-a-en",
            },
            {
                "req_id": "b",
                "language": "de-de",
                "canonical_url": "https://example.com/b?lang=de-de",
                "raw_uuid": "raw-b-de",
            },
        ]

        scoped, total = _apply_item_filter(items, item_filter, advertised_total=4)

        assert [(item["req_id"], item["language"]) for item in scoped] == [
            ("a", "en-us"),
            ("b", "de-de"),
        ]
        assert total == 2

    def test_required_identity_and_dedupe_preference_compose(self):
        item_filter = _validated_item_filter(
            {
                "item_filter": {
                    "require_regex": {"req_id": r"[0-9]{4}"},
                    "dedupe_by": ["req_id"],
                    "dedupe_preference": {
                        "path": "language",
                        "preferred_values": ["en-us"],
                        "fallback_by": ["language", "canonical_url"],
                    },
                }
            }
        )

        scoped, total = _apply_item_filter(
            [
                {
                    "req_id": "1234",
                    "language": "de-de",
                    "canonical_url": "https://example.com/1234?lang=de-de",
                },
                {
                    "req_id": "1234",
                    "language": "en-us",
                    "canonical_url": "https://example.com/1234?lang=en-us",
                },
            ],
            item_filter,
            advertised_total=2,
        )

        assert scoped == [
            {
                "req_id": "1234",
                "language": "en-us",
                "canonical_url": "https://example.com/1234?lang=en-us",
            }
        ]
        assert total == 1

        with pytest.raises(ValueError, match="missing or invalid.*req_id"):
            _apply_item_filter(
                [
                    {
                        "req_id": "not-numeric",
                        "language": "en-us",
                        "canonical_url": "https://example.com/invalid",
                    }
                ],
                item_filter,
                advertised_total=1,
            )

    @pytest.mark.parametrize(
        "preference",
        [
            {},
            {"path": "language", "preferred_values": ["en-us"]},
            {
                "path": "language",
                "preferred_values": [],
                "fallback_by": ["language"],
            },
            {
                "path": "language",
                "preferred_values": ["en-us", "en-us"],
                "fallback_by": ["language"],
            },
            {
                "path": "language",
                "preferred_values": ["en-us"],
                "fallback_by": ["canonical_url", "language"],
            },
        ],
    )
    def test_rejects_malformed_dedupe_preference(self, preference):
        with pytest.raises(ValueError, match="dedupe_preference"):
            _validated_item_filter(
                {
                    "item_filter": {
                        "dedupe_by": ["req_id"],
                        "dedupe_preference": preference,
                    }
                }
            )

    @pytest.mark.parametrize(
        "value",
        [
            {},
            {"unexpected": True},
            {"include": {}, "exclude": {"market": ["local"]}},
            {"include": [], "exclude": {"market": ["local"]}},
            {"include": "", "exclude": {"market": ["local"]}},
            {"include": False, "exclude": {"market": ["local"]}},
            {"include": None, "exclude": {"market": ["local"]}},
            {"include": {"market": []}},
            {"include": {"": ["global"]}},
            {"include": {"attributes.25": ["global"]}},
            {"exclude": {"market": []}},
            {"exclude": {"": ["local"]}},
            {"exclude": {"attributes.25": ["USA"]}},
            {"exclude_regex": {"market": []}},
            {"exclude_regex": {"market": ["("]}},
            {"exclude_regex": {"": ["local"]}},
            {"require_regex": []},
            {"require_regex": {"viewkey": ""}},
            {"require_regex": {"viewkey": "("}},
            {"require_regex": {"": "valid"}},
            {"dedupe_by": ""},
            {"dedupe_by": "stable"},
            {"dedupe_by": []},
        ],
    )
    def test_rejects_invalid_config(self, value):
        with pytest.raises(ValueError, match="item_filter"):
            _validated_item_filter({"item_filter": value})

    def test_compaction_preserves_filter_and_dedupe_roots(self):
        projector = _build_item_projector(
            {"title": "details.title"},
            "url",
            None,
            {},
            preserve_paths=[
                "attributes.country",
                "provider.owner",
                "provider.name",
                "provider.tenant_id",
                "provider.apply_id",
            ],
        )

        assert projector is not None
        assert projector(
            {
                "details": {"title": "Engineer"},
                "url": "https://example.com/1",
                "attributes": {"country": ["Germany"]},
                "provider": {
                    "owner": "Internal",
                    "name": "Internal",
                    "tenant_id": "tenant",
                    "apply_id": "stable-1",
                },
                "unused": "large",
            }
        ) == {
            "details": {"title": "Engineer"},
            "url": "https://example.com/1",
            "attributes": {"country": ["Germany"]},
            "provider": {
                "owner": "Internal",
                "name": "Internal",
                "tenant_id": "tenant",
                "apply_id": "stable-1",
            },
        }

    @pytest.mark.asyncio
    async def test_http_discovery_applies_filter_after_complete_response(self):
        payload = {
            "jobs": [
                {
                    "url": "https://example.com/1",
                    "market": "global",
                    "owner": "Internal",
                    "stable": "one",
                },
                {
                    "url": "https://example.com/duplicate",
                    "market": "global",
                    "owner": "Internal",
                    "stable": "one",
                },
                {
                    "url": "https://example.com/usa",
                    "market": "local",
                    "owner": "Internal",
                    "stable": "two",
                },
                {
                    "url": "https://example.com/2",
                    "market": "global",
                    "owner": "Internal",
                    "stable": "three",
                },
                {
                    "url": "https://example.com/external",
                    "market": "global",
                    "owner": "External",
                    "stable": "four",
                },
                {
                    "url": "https://example.com/missing-owner",
                    "market": "global",
                    "stable": "five",
                },
            ],
            "total": 6,
        }
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "method": "GET",
                "json_path": "jobs",
                "url_field": "url",
                "total_path": "total",
                "item_filter": {
                    "include": {"owner": ["Internal"]},
                    "exclude": {"market": ["local"]},
                    "dedupe_by": ["stable"],
                },
            },
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(board, client)

        assert result == {"https://example.com/1", "https://example.com/2"}


class TestPaginationConvergence:
    def test_requires_bounded_offset_config_and_stable_identity(self):
        with pytest.raises(ValueError, match="requires identity_by or item_filter.dedupe_by"):
            _validated_pagination_convergence(
                {
                    "pagination": {"style": "offset"},
                    "pagination_convergence": {
                        "max_passes": 4,
                        "required_no_growth_passes": 2,
                    },
                },
                (),
            )

    def test_accepts_numbered_page_pagination(self):
        convergence = _validated_pagination_convergence(
            {
                "pagination": {"style": "page"},
                "pagination_convergence": {
                    "max_passes": 3,
                    "required_no_growth_passes": 2,
                },
            },
            ("id",),
        )
        assert convergence is not None
        assert convergence.max_passes == 3
        assert convergence.required_no_growth_passes == 2
        assert convergence.identity_paths == ("id",)
        assert convergence.stable_fields == ()
        assert convergence.reject_duplicate_identities is False

    def test_explicit_raw_identity_is_separate_from_logical_dedupe(self):
        convergence = _validated_pagination_convergence(
            {
                "pagination": {"style": "page"},
                "pagination_convergence": {
                    "max_passes": 3,
                    "required_no_growth_passes": 2,
                    "identity_by": ["data.raw_uuid"],
                    "stable_fields": [
                        "data.req_id",
                        "data.language",
                        "data.canonical_url",
                    ],
                },
            },
            ("data.req_id",),
        )

        assert convergence is not None
        assert convergence.identity_paths == ("data.raw_uuid",)
        assert convergence.stable_fields == (
            "data.req_id",
            "data.language",
            "data.canonical_url",
        )
        assert convergence.reject_duplicate_identities is True

    def test_url_field_match_requires_exact_named_groups_and_cross_fields(self):
        config = {
            "url_field": "data.canonical_url",
            "pagination": {"style": "page"},
            "pagination_convergence": {
                "max_passes": 3,
                "required_no_growth_passes": 2,
                "identity_by": ["data.raw_uuid"],
            },
            "url_field_match": {
                "pattern": (
                    r"^https://example\.com/jobs/(?P<req_id>[0-9]+)"
                    r"\?lang=(?P<language>[a-z-]+)$"
                ),
                "fields": {
                    "req_id": "data.req_id",
                    "language": "data.language",
                },
            },
        }
        convergence = _validated_pagination_convergence(config, ("data.req_id",))
        contract = _validated_url_field_match(config, convergence)
        assert contract is not None
        valid = {
            "data": {
                "req_id": "123",
                "language": "en-us",
                "canonical_url": "https://example.com/jobs/123?lang=en-us",
            }
        }
        assert _matches_url_field_contract(valid, "data.canonical_url", contract)

        wrong_req = {
            "data": {
                **valid["data"],
                "canonical_url": "https://example.com/jobs/456?lang=en-us",
            }
        }
        wrong_language = {
            "data": {
                **valid["data"],
                "canonical_url": "https://example.com/jobs/123?lang=fr-fr",
            }
        }
        assert not _matches_url_field_contract(wrong_req, "data.canonical_url", contract)
        assert not _matches_url_field_contract(wrong_language, "data.canonical_url", contract)

    def test_url_field_match_rejects_ambiguous_config(self):
        convergence_config = {
            "pagination": {"style": "page"},
            "pagination_convergence": {
                "max_passes": 3,
                "required_no_growth_passes": 2,
                "identity_by": ["raw_uuid"],
            },
        }
        convergence = _validated_pagination_convergence(convergence_config, ())
        with pytest.raises(ValueError, match="requires url_field"):
            _validated_url_field_match(
                {
                    **convergence_config,
                    "url_field_match": {
                        "pattern": r"(?P<id>[0-9]+)",
                        "fields": {"id": "id"},
                    },
                },
                convergence,
            )
        with pytest.raises(ValueError, match="named groups must exactly match"):
            _validated_url_field_match(
                {
                    **convergence_config,
                    "url_field": "url",
                    "url_field_match": {
                        "pattern": r"(?P<id>[0-9]+)",
                        "fields": {"req_id": "id"},
                    },
                },
                convergence,
            )

    @staticmethod
    def _pages_fetcher(passes):
        current_pass = 0

        async def fetch(_method, url, _headers, _body):
            nonlocal current_pass
            raw_skip = parse_qs(urlparse(url).query).get("skip", ["0"])[0]
            skip = int(raw_skip)
            if skip == 0:
                current_pass += 1
            return {"count": 4, "jobs": passes[current_pass][skip]}

        return fetch

    @pytest.mark.asyncio
    async def test_accumulates_until_two_complete_passes_add_no_identity(self):
        a = {"id": "a", "url": "https://example.com/a"}
        b = {"id": "b", "url": "https://example.com/b"}
        c = {"id": "c", "url": "https://example.com/c"}
        d = {"id": "d", "url": "https://example.com/d"}
        passes = [
            {0: [a, b], 2: [a, c]},
            {0: [a, b], 2: [b, d]},
            {0: [a, c], 2: [b, d]},
            {0: [a, d], 2: [b, c]},
        ]

        items, converged = await _paginate_until_converged(
            fetch_fn=self._pages_fetcher(passes),
            method="GET",
            api_url="https://example.com/jobs?skip=0",
            request_headers={},
            post_data=None,
            initial_data={"count": 4, "jobs": passes[0][0]},
            initial_items=passes[0][0],
            json_path="jobs",
            total_path="count",
            total_count=4,
            pagination_config={
                "param_name": "skip",
                "style": "offset",
                "start_value": 0,
                "increment": 2,
                "location": "query",
            },
            max_pages=2,
            identity_paths=("id",),
            max_passes=4,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is True
        assert {item["id"] for item in items} == {"a", "b", "c", "d"}

    @pytest.mark.asyncio
    async def test_total_change_invalidates_convergence_proof(self):
        a = {"id": "a"}
        b = {"id": "b"}

        async def fetch(_method, _url, _headers, _body):
            return {"count": 3, "jobs": [a, b]}

        items, converged = await _paginate_until_converged(
            fetch_fn=fetch,
            method="GET",
            api_url="https://example.com/jobs?skip=0",
            request_headers={},
            post_data=None,
            initial_data={"count": 4, "jobs": [a, b]},
            initial_items=[a, b],
            json_path="jobs",
            total_path="count",
            total_count=4,
            pagination_config={
                "param_name": "skip",
                "style": "offset",
                "start_value": 0,
                "increment": 2,
                "location": "query",
            },
            max_pages=2,
            identity_paths=("id",),
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is False
        assert {item["id"] for item in items} == {"a", "b"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "page_payload",
        [
            None,
            {"count": 2},
            {"count": 2, "jobs": [None]},
        ],
        ids=["missing-response", "missing-list", "item-schema-drift"],
    )
    async def test_every_paginated_response_requires_the_configured_list_schema(self, page_payload):
        first = {"id": "a"}

        async def fetch(_method, _url, _headers, _body):
            return page_payload

        items, converged = await _paginate_until_converged(
            fetch_fn=fetch,
            method="GET",
            api_url="https://example.com/jobs?skip=0",
            request_headers={},
            post_data=None,
            initial_data={"count": 2, "jobs": [first]},
            initial_items=[first],
            json_path="jobs",
            total_path="count",
            total_count=2,
            pagination_config={
                "param_name": "skip",
                "style": "offset",
                "start_value": 0,
                "increment": 1,
                "location": "query",
            },
            max_pages=2,
            identity_paths=("id",),
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is False
        assert items == [first]

    @pytest.mark.asyncio
    async def test_cross_pass_record_conflict_invalidates_proof_and_preserves_first(self):
        passes = [
            {
                0: [{"id": "a", "title": "version one"}],
                1: [{"id": "b", "title": "stable"}],
            },
            {
                0: [{"id": "a", "title": "version two"}],
                1: [{"id": "b", "title": "stable"}],
            },
        ]
        current_pass = 0

        async def fetch(_method, url, _headers, _body):
            nonlocal current_pass
            skip = int(parse_qs(urlparse(url).query)["skip"][0])
            if skip == 0:
                current_pass += 1
            return {"count": 2, "jobs": passes[current_pass][skip]}

        items, converged = await _paginate_until_converged(
            fetch_fn=fetch,
            method="GET",
            api_url="https://example.com/jobs?skip=0",
            request_headers={},
            post_data=None,
            initial_data={"count": 2, "jobs": passes[0][0]},
            initial_items=passes[0][0],
            json_path="jobs",
            total_path="count",
            total_count=2,
            pagination_config={
                "param_name": "skip",
                "style": "offset",
                "start_value": 0,
                "increment": 1,
                "location": "query",
            },
            max_pages=2,
            identity_paths=("id",),
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is False
        assert {item["id"]: item["title"] for item in items} == {
            "a": "version one",
            "b": "stable",
        }

    @pytest.mark.asyncio
    async def test_explicit_raw_identity_tolerates_only_unprojected_record_drift(self):
        payloads = [
            {"count": 1, "jobs": [{"uuid": "raw-a", "req": "a", "body": "one"}]},
            {"count": 1, "jobs": [{"uuid": "raw-a", "req": "a", "body": "two"}]},
        ]
        calls = 0

        async def fetch(_method, _url, _headers, _body):
            nonlocal calls
            payload = payloads[min(calls, len(payloads) - 1)]
            calls += 1
            return payload

        items, converged = await _paginate_until_converged(
            fetch_fn=fetch,
            method="GET",
            api_url="https://example.com/jobs?page=1",
            request_headers={},
            post_data=None,
            initial_data={
                "count": 1,
                "jobs": [{"uuid": "raw-a", "req": "a", "body": "initial"}],
            },
            initial_items=[{"uuid": "raw-a", "req": "a", "body": "initial"}],
            json_path="jobs",
            total_path="count",
            total_count=1,
            pagination_config={
                "param_name": "page",
                "style": "page",
                "start_value": 1,
                "increment": 1,
                "location": "query",
            },
            max_pages=1,
            identity_paths=("uuid",),
            stable_fields=("req",),
            reject_duplicate_identities=True,
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is True
        assert items == [{"uuid": "raw-a", "req": "a", "body": "initial"}]
        assert calls == 2

    @pytest.mark.asyncio
    async def test_explicit_raw_identity_rejects_projected_drift(self):
        async def fetch(_method, _url, _headers, _body):
            return {"count": 1, "jobs": [{"uuid": "raw-a", "req": "changed"}]}

        initial = {"uuid": "raw-a", "req": "a"}
        items, converged = await _paginate_until_converged(
            fetch_fn=fetch,
            method="GET",
            api_url="https://example.com/jobs?page=1",
            request_headers={},
            post_data=None,
            initial_data={"count": 1, "jobs": [initial]},
            initial_items=[initial],
            json_path="jobs",
            total_path="count",
            total_count=1,
            pagination_config={
                "param_name": "page",
                "style": "page",
                "start_value": 1,
                "increment": 1,
                "location": "query",
            },
            max_pages=1,
            identity_paths=("uuid",),
            stable_fields=("req",),
            reject_duplicate_identities=True,
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is False
        assert items == [initial]

    @pytest.mark.asyncio
    async def test_explicit_raw_identity_rejects_duplicate_uuid(self):
        row = {"uuid": "raw-a", "req": "a"}

        items, converged = await _paginate_until_converged(
            fetch_fn=AsyncMock(),
            method="GET",
            api_url="https://example.com/jobs?page=1",
            request_headers={},
            post_data=None,
            initial_data={"count": 2, "jobs": [row, row]},
            initial_items=[row, row],
            json_path="jobs",
            total_path="count",
            total_count=2,
            pagination_config={
                "param_name": "page",
                "style": "page",
                "start_value": 1,
                "increment": 1,
                "location": "query",
            },
            max_pages=1,
            identity_paths=("uuid",),
            stable_fields=("req",),
            reject_duplicate_identities=True,
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is False
        assert items == [row]

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("rows", "expected_ids"),
        [
            (
                [{"id": "a", "title": "same"}, {"id": "a", "title": "same"}],
                {"a"},
            ),
            (
                [{"id": "a", "title": "first"}, {"id": "a", "title": "second"}],
                {"a"},
            ),
        ],
    )
    async def test_duplicate_rows_cannot_prove_a_short_unique_inventory(self, rows, expected_ids):
        calls = 0

        async def fetch(_method, _url, _headers, _body):
            nonlocal calls
            calls += 1
            return {"count": 2, "jobs": rows}

        items, converged = await _paginate_until_converged(
            fetch_fn=fetch,
            method="GET",
            api_url="https://example.com/jobs?skip=0",
            request_headers={},
            post_data=None,
            initial_data={"count": 2, "jobs": rows},
            initial_items=rows,
            json_path="jobs",
            total_path="count",
            total_count=2,
            pagination_config={
                "param_name": "skip",
                "style": "offset",
                "start_value": 0,
                "increment": 2,
                "location": "query",
            },
            max_pages=1,
            identity_paths=("id",),
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert {item["id"] for item in items} == expected_ids
        assert converged is False
        if rows[0] == rows[1]:
            assert calls == 2
        else:
            assert items == [rows[0]]
            assert calls == 0

    @pytest.mark.asyncio
    async def test_no_growth_cannot_rescue_a_short_pass(self):
        rows = [{"id": "a"}, {"id": "b"}]

        async def fetch(_method, _url, _headers, _body):
            return {"count": 3, "jobs": rows}

        items, converged = await _paginate_until_converged(
            fetch_fn=fetch,
            method="GET",
            api_url="https://example.com/jobs?skip=0",
            request_headers={},
            post_data=None,
            initial_data={"count": 3, "jobs": rows},
            initial_items=rows,
            json_path="jobs",
            total_path="count",
            total_count=3,
            pagination_config={
                "param_name": "skip",
                "style": "offset",
                "start_value": 0,
                "increment": 2,
                "location": "query",
            },
            max_pages=1,
            identity_paths=("id",),
            max_passes=3,
            required_no_growth_passes=2,
            item_projector=None,
        )

        assert converged is False
        assert {item["id"] for item in items} == {"a", "b"}

    @pytest.mark.asyncio
    @pytest.mark.parametrize("browser", [False, True], ids=["http", "browser"])
    @pytest.mark.parametrize(
        ("payloads", "expected_truncated", "expected_urls"),
        [
            ([{"count": 0, "jobs": []}] * 3, False, set()),
            (
                [
                    {"count": 1, "jobs": [{"id": "a", "url": "/a"}]},
                ]
                * 3,
                False,
                {"https://example.com/a"},
            ),
            ([{"count": 1, "jobs": []}], True, set()),
            ([{"jobs": []}], True, set()),
            ([{"count": 0}], True, set()),
            ([{"count": 0, "jobs": []}, {"count": 1, "jobs": []}], True, set()),
            ([{"count": 0, "jobs": []}, {"count": 0}], True, set()),
            ([{"count": 0, "jobs": []}, {"count": 0, "jobs": [None]}], True, set()),
        ],
        ids=[
            "stable-zero",
            "stable-nonzero",
            "nonzero-empty",
            "missing-total-empty",
            "missing-list-zero",
            "changing-total-empty",
            "later-missing-list-zero",
            "later-item-schema-drift-zero",
        ],
    )
    async def test_replay_paths_require_bounded_empty_and_nonempty_proof(
        self,
        browser,
        payloads,
        expected_truncated,
        expected_urls,
    ):
        from src.core.monitor import MonitorResult

        config = {
            "api_url": "https://example.com/jobs?skip=0",
            "json_path": "jobs",
            "total_path": "count",
            "url_field": "url",
            "pagination": {
                "param_name": "skip",
                "style": "offset",
                "start_value": 0,
                "increment": 1,
                "location": "query",
                "max_pages": 1,
            },
            "pagination_convergence": {
                "max_passes": 3,
                "required_no_growth_passes": 2,
            },
            "item_filter": {"dedupe_by": ["id"]},
            "browser": browser,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}
        call_count = 0

        def next_payload():
            nonlocal call_count
            payload = payloads[min(call_count, len(payloads) - 1)]
            call_count += 1
            return payload

        if browser:

            def browser_fetch(_script, _args):
                return {"headers": {}, "text": json.dumps(next_payload())}

            mock_page = AsyncMock()
            mock_page.evaluate = AsyncMock(side_effect=browser_fetch)
            result = await discover(board, AsyncMock(), pw=_make_mock_pw(mock_page))
        else:

            def handler(request):
                return httpx.Response(200, json=next_payload(), request=request)

            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await discover(board, client)

        if expected_truncated:
            assert isinstance(result, MonitorResult)
            assert result.truncated is True
            assert result.urls == expected_urls
        else:
            assert not isinstance(result, MonitorResult)
            assert result == expected_urls
        assert call_count == len(payloads)

    @pytest.mark.asyncio
    async def test_unstable_offset_pages_cannot_return_healthy_partial_inventory(self):
        from src.core.monitor import MonitorResult

        items = {key: {"id": key, "url": f"https://example.com/{key}"} for key in "abcdef"}
        passes = [
            {0: [items["a"], items["b"]], 2: [items["a"], items["c"]]},
            {0: [items["a"], items["b"]], 2: [items["b"], items["d"]]},
            {0: [items["a"], items["c"]], 2: [items["c"], items["e"]]},
            {0: [items["a"], items["d"]], 2: [items["d"], items["f"]]},
        ]
        current_pass = 0
        initial_returned = False

        def handler(request: httpx.Request) -> httpx.Response:
            nonlocal current_pass, initial_returned
            raw_skip = parse_qs(request.url.query.decode()).get("skip", ["0"])[0]
            skip = int(raw_skip)
            if skip == 0:
                if initial_returned:
                    current_pass += 1
                initial_returned = True
            return httpx.Response(
                200,
                json={"count": 4, "jobs": passes[current_pass][skip]},
                request=request,
            )

        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/jobs?skip=0",
                "json_path": "jobs",
                "total_path": "count",
                "url_field": "url",
                "pagination": {
                    "param_name": "skip",
                    "style": "offset",
                    "start_value": 0,
                    "increment": 2,
                    "location": "query",
                    "max_pages": 2,
                },
                "pagination_convergence": {
                    "max_passes": 4,
                    "required_no_growth_passes": 2,
                },
                "item_filter": {"dedupe_by": ["id"]},
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {
            "https://example.com/a",
            "https://example.com/b",
            "https://example.com/c",
            "https://example.com/d",
            "https://example.com/e",
        }

    @pytest.mark.asyncio
    async def test_raw_uuid_proof_emits_one_preferred_locale_url_per_requisition(self):
        pages = {
            1: [
                {
                    "data": {
                        "req_id": "a",
                        "language": "fr-fr",
                        "meta_data": {
                            "canonical_url": "https://example.com/jobs/a?lang=fr-fr",
                            "icims": {"uuid": "raw-a-fr"},
                        },
                    }
                },
                {
                    "data": {
                        "req_id": "b",
                        "language": "de-de",
                        "meta_data": {
                            "canonical_url": "https://example.com/jobs/b?lang=de-de",
                            "icims": {"uuid": "raw-b-de"},
                        },
                    }
                },
            ],
            2: [
                {
                    "data": {
                        "req_id": "a",
                        "language": "en-us",
                        "meta_data": {
                            "canonical_url": "https://example.com/jobs/a?lang=en-us",
                            "icims": {"uuid": "raw-a-en"},
                        },
                    }
                },
                {
                    "data": {
                        "req_id": "c",
                        "language": "en-us",
                        "meta_data": {
                            "canonical_url": "https://example.com/jobs/c?lang=en-us",
                            "icims": {"uuid": "raw-c-en"},
                        },
                    }
                },
            ],
        }

        def handler(request: httpx.Request) -> httpx.Response:
            page = int(parse_qs(request.url.query.decode()).get("page", ["1"])[0])
            return httpx.Response(
                200,
                json={"totalCount": 4, "jobs": pages[page]},
                request=request,
            )

        board = {
            "board_url": "https://example.com/jobs",
            "metadata": {
                "api_url": "https://example.com/api/jobs?limit=2&page=1",
                "json_path": "jobs",
                "total_path": "totalCount",
                "url_field": "data.meta_data.canonical_url",
                "pagination": {
                    "param_name": "page",
                    "style": "page",
                    "start_value": 1,
                    "increment": 1,
                    "location": "query",
                    "max_pages": 2,
                },
                "pagination_convergence": {
                    "max_passes": 3,
                    "required_no_growth_passes": 2,
                    "identity_by": ["data.meta_data.icims.uuid"],
                    "stable_fields": [
                        "data.req_id",
                        "data.language",
                        "data.meta_data.canonical_url",
                    ],
                },
                "item_filter": {
                    "dedupe_by": ["data.req_id"],
                    "dedupe_preference": {
                        "path": "data.language",
                        "preferred_values": ["en-us"],
                        "fallback_by": [
                            "data.language",
                            "data.meta_data.canonical_url",
                            "data.meta_data.icims.uuid",
                        ],
                    },
                },
                "url_field_match": {
                    "pattern": (
                        r"^https://example\.com/jobs/(?P<req_id>[a-z]+)"
                        r"\?lang=(?P<language>[a-z]{2}-[a-z]{2})$"
                    ),
                    "fields": {
                        "req_id": "data.req_id",
                        "language": "data.language",
                    },
                },
            },
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(board, client)

        assert result == {
            "https://example.com/jobs/a?lang=en-us",
            "https://example.com/jobs/b?lang=de-de",
            "https://example.com/jobs/c?lang=en-us",
        }

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "canonical_url",
        [
            "https://example.com/jobs/other?lang=en-us",
            "https://example.com/jobs/a",
        ],
        ids=["cross-field-mismatch", "missing-required-language"],
    )
    async def test_raw_uuid_proof_fails_closed_on_invalid_provider_url(self, canonical_url):
        from src.core.monitor import MonitorResult

        payload = {
            "totalCount": 1,
            "jobs": [
                {
                    "data": {
                        "req_id": "a",
                        "language": "en-us",
                        "meta_data": {
                            "canonical_url": canonical_url,
                            "icims": {"uuid": "raw-a"},
                        },
                    }
                }
            ],
        }
        config = {
            "api_url": "https://example.com/api/jobs?page=1",
            "json_path": "jobs",
            "total_path": "totalCount",
            "url_field": "data.meta_data.canonical_url",
            "pagination": {
                "param_name": "page",
                "style": "page",
                "start_value": 1,
                "increment": 1,
                "location": "query",
                "max_pages": 1,
            },
            "pagination_convergence": {
                "max_passes": 3,
                "required_no_growth_passes": 2,
                "identity_by": ["data.meta_data.icims.uuid"],
                "stable_fields": [
                    "data.req_id",
                    "data.language",
                    "data.meta_data.canonical_url",
                ],
            },
            "item_filter": {"dedupe_by": ["data.req_id"]},
            "url_field_match": {
                "pattern": (
                    r"^https://example\.com/jobs/(?P<req_id>[a-z]+)"
                    r"\?lang=(?P<language>[a-z]{2}-[a-z]{2})$"
                ),
                "fields": {
                    "req_id": "data.req_id",
                    "language": "data.language",
                },
            },
        }
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, json=payload, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(
                {"board_url": "https://example.com/jobs", "metadata": config},
                client,
            )

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == set()


def _http_status_error_resp(status: int) -> MagicMock:
    """Build a mock httpx.Response whose ``raise_for_status()`` raises a real
    :class:`httpx.HTTPStatusError`. The api_sniffer retry classifier
    (#2733) reads ``exc.response.status_code`` to decide retryable vs.
    fail-fast, so generic ``Exception("403 Forbidden")`` no longer
    suffices — it would be caught as a transient and burn the full
    retry budget. Use this helper for tests simulating an HTTP error.
    """
    resp = MagicMock()
    request = httpx.Request("GET", "https://example.com")
    response = httpx.Response(status, request=request)
    resp.raise_for_status.side_effect = httpx.HTTPStatusError(
        f"HTTP {status}", request=request, response=response
    )
    return resp


class TestCanHandle:
    async def test_prospective_careercenter_uses_public_medium_api_without_browser(self):
        payload = {
            "medium_id": "1002048",
            "total": 2,
            "jobs": [
                {
                    "title": "Engineer",
                    "start_date": "2026-08-01T00:00:00Z",
                    "end_date": "2026-09-01T00:00:00Z",
                    "language": "de",
                    "links": {"directlink": "https://jobs.example.com/engineer/one"},
                    "szas": {
                        "sza_tasks": "<p>Build services.</p>",
                        "sza_requirements": "<p>Python.</p>",
                        "sza_benefits": "<p>Flexible work.</p>",
                        "sza_location.city": "Zürich",
                        "sza_employment_type": "Festanstellung",
                        "sza_pensum": "Vollzeit",
                    },
                },
                {
                    "title": "Analyst",
                    "start_date": "2026-08-02T00:00:00Z",
                    "end_date": "2026-09-02T00:00:00Z",
                    "language": "fr",
                    "links": {"directlink": "https://jobs.example.com/analyst/two"},
                    "szas": {},
                },
            ],
        }

        def handler(request):
            assert request.url.path == "/public/v1/medium/1002048/jobs"
            assert dict(request.url.params) == {
                "lang": "fr",
                "offset": "0",
                "limit": "100",
            }
            return httpx.Response(200, json=payload, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await can_handle(
                "https://ohws.prospective.ch/public/v1/careercenter/1002048/?lang=fr",
                client,
                pw=None,
            )

        assert result == {
            "api_url": "https://ohws.prospective.ch/public/v1/medium/1002048/jobs",
            "method": "GET",
            "json_path": "jobs",
            "total_path": "total",
            "url_field": "links.directlink",
            "params": {"lang": "fr", "offset": "0", "limit": "2"},
            "pagination": {
                "param_name": "offset",
                "style": "offset",
                "start_value": 0,
                "increment": 2,
                "location": "query",
            },
            "fields": {
                "title": "title",
                "date_posted": "start_date",
                "metadata.language": "language",
                "metadata.end_date": "end_date",
                "metadata.ats_job_id": "id",
                "description": [
                    "=<h3>Tasks</h3>",
                    "szas.sza_tasks",
                    "=<h3>Requirements</h3>",
                    "szas.sza_requirements",
                    "=<h3>Benefits</h3>",
                    "szas.sza_benefits",
                ],
                "responsibilities": "szas.sza_tasks",
                "qualifications": "szas.sza_requirements",
                "locations": 'szas."sza_location.city"',
                "employment_type": "szas.sza_employment_type",
                "metadata.pensum": "szas.sza_pensum",
            },
            "items": 2,
            "total": 2,
            "score": 100,
        }

    @pytest.mark.parametrize(
        "url",
        [
            "http://ohws.prospective.ch/public/v1/careercenter/1002048/",
            "https://ohws.prospective.ch:8443/public/v1/careercenter/1002048/",
            "https://user@ohws.prospective.ch/public/v1/careercenter/1002048/",
            "https://example.com/public/v1/careercenter/1002048/",
        ],
    )
    async def test_prospective_careercenter_rejects_noncanonical_origin(self, url):
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: None)) as client:
            assert await can_handle(url, client, pw=None) is None


@pytest.fixture(autouse=True)
def _zero_settle():
    """Eliminate 3-second settle sleeps in tests."""
    with patch("src.core.monitors.api_sniffer._DEFAULT_SETTLE", 0):
        yield


class TestExtractRich:
    def test_basic_fields(self):
        items = [
            {"title": "Dev", "bodyHtml": "<p>Description</p>", "url": "/jobs/1", "location": "NYC"},
            {"title": "PM", "bodyHtml": "<p>PM desc</p>", "url": "/jobs/2", "location": "SF"},
        ]
        fields = {"title": "title", "description": "bodyHtml", "locations": "location"}
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 2
        assert jobs[0].title == "Dev"
        assert jobs[0].description == "<p>Description</p>"
        assert jobs[0].locations == ["NYC"]
        assert jobs[0].url == "https://example.com/jobs/1"

    def test_entity_encoded_html_fields(self):
        items = [
            {
                "JobTitle": "Service Technician",
                "PublicationUrl": "https://example.com/jobs/1",
                "PlaceOfWorkCity": "Netstal",
                "PlaceOfWorkCountry": "CH",
                "Organization": "&lt;p&gt;About the employer&lt;/p&gt;",
                "Tasks": "&lt;h2&gt;Tasks&lt;/h2&gt;&lt;p&gt;Repair appliances.&lt;/p&gt;",
                "Requirements": (
                    "&lt;h2&gt;Profile&lt;/h2&gt;&lt;p&gt;Electrical training.&lt;/p&gt;"
                ),
            }
        ]
        fields = {
            "title": "JobTitle",
            "description": [
                {"path": "Organization", "html_unescape": True},
                {"path": "Tasks", "html_unescape": True},
                {"path": "Requirements", "html_unescape": True},
            ],
            "locations": {
                "concat": ["PlaceOfWorkCity", "PlaceOfWorkCountry"],
                "separator": ", ",
            },
        }

        jobs = _extract_rich(items, fields, "PublicationUrl", None, "https://example.com")

        assert len(jobs) == 1
        assert jobs[0].title == "Service Technician"
        assert jobs[0].locations == ["Netstal, CH"]
        assert jobs[0].description == (
            "<p>About the employer</p>\n\n"
            "<h2>Tasks</h2><p>Repair appliances.</p>\n\n"
            "<h2>Profile</h2><p>Electrical training.</p>"
        )

    def test_url_template(self):
        items = [
            {"title": "Dev", "id": "123", "slug": "developer"},
        ]
        fields = {"title": "title"}
        jobs = _extract_rich(
            items,
            fields,
            None,
            "https://example.com/jobs/{id}/{slug}",
            "https://example.com",
        )
        assert len(jobs) == 1
        assert jobs[0].url == "https://example.com/jobs/123/developer"

    def test_bad_url_template_falls_back_to_url_field(self):
        items = [
            {"title": "Dev", "id": "123", "url": "/jobs/123"},
        ]
        fields = {"title": "title"}
        jobs = _extract_rich(
            items,
            fields,
            "url",
            "https://example.com/jobs/{id}/{missing}",
            "https://example.com",
        )
        assert len(jobs) == 1
        assert jobs[0].url == "https://example.com/jobs/123"

    def test_url_template_with_nested_field_alias(self):
        items = [
            {
                "itemID": "9201870385175_1",
                "customFieldGroup": {
                    "stringFields": [{"stringValue": "616994"}],
                },
                "title": "Underwriter",
            },
        ]
        jobs = _extract_rich(
            items,
            {"title": "title"},
            None,
            "https://example.com/jobs?jobId={external_id}&itemId={itemID}",
            "https://example.com",
            url_template_fields={
                "external_id": "customFieldGroup.stringFields[0].stringValue",
            },
        )
        assert len(jobs) == 1
        assert jobs[0].url == ("https://example.com/jobs?jobId=616994&itemId=9201870385175_1")

    def test_url_template_with_generated_slug(self):
        jobs = _extract_rich(
            [{"reqId": "R_123", "title": "Risk & Insurance Advisor"}],
            {"title": "title"},
            None,
            "https://example.com/jobs/{reqId}/{slug}",
            "https://example.com",
            slug_fields=["title"],
        )

        assert len(jobs) == 1
        assert jobs[0].url == "https://example.com/jobs/R_123/risk-and-insurance-advisor"

    def test_metadata_fields(self):
        items = [{"title": "Dev", "url": "/jobs/1", "department": "Eng"}]
        fields = {"title": "title", "metadata.team": "department"}
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert jobs[0].metadata == {"team": "Eng"}

    def test_array_locations(self):
        items = [
            {
                "title": "Dev",
                "url": "/jobs/1",
                "offices": [{"name": "NYC"}, {"name": "SF"}],
            }
        ]
        fields = {"title": "title", "locations": "offices[].name"}
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert jobs[0].locations == ["NYC", "SF"]

    def test_array_description_fragments_are_joined(self):
        items = [
            {
                "title": "Dev",
                "url": "/jobs/1",
                "modularContent": [
                    {"text": "<p>About the role</p>"},
                    {"text": "<ul><li>Build things</li></ul>"},
                ],
            }
        ]
        fields = {
            "title": "title",
            "description": "modularContent[].text",
        }

        jobs = _extract_rich(items, fields, "url", None, "https://example.com")

        assert jobs[0].description == ("<p>About the role</p>\n\n<ul><li>Build things</li></ul>")

    def test_no_url_skipped(self):
        items = [{"title": "Dev", "score": 5}]
        fields = {"title": "title"}
        jobs = _extract_rich(items, fields, None, None, "https://example.com")
        assert len(jobs) == 0

    def test_multi_field_concat(self):
        items = [
            {
                "title": "Engineer",
                "url": "/jobs/1",
                "intro": "Join our team.",
                "tasks": "<ul><li>Build things</li></ul>",
                "reqs": "<ul><li>5 years exp</li></ul>",
            },
        ]
        fields = {
            "title": "title",
            "description": ["intro", "tasks", "reqs"],
        }
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 1
        assert jobs[0].description == (
            "Join our team.\n\n<ul><li>Build things</li></ul>\n\n<ul><li>5 years exp</li></ul>"
        )

    def test_multi_field_concat_partial(self):
        """Missing fields are skipped, present ones still concatenated."""
        items = [
            {
                "title": "PM",
                "url": "/jobs/2",
                "tasks": "Manage projects",
            },
        ]
        fields = {
            "title": "title",
            "description": ["intro", "tasks", "reqs"],
        }
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 1
        assert jobs[0].description == "Manage projects"

    def test_multi_field_concat_all_missing(self):
        """When all paths in a list resolve to None, the field is absent."""
        items = [{"title": "QA", "url": "/jobs/3"}]
        fields = {
            "title": "title",
            "description": ["intro", "tasks"],
        }
        jobs = _extract_rich(items, fields, "url", None, "https://example.com")
        assert len(jobs) == 1
        assert jobs[0].description is None


class TestExtractUrlsFromTemplate:
    def test_basic(self):
        items = [
            {"id": "1", "slug": "dev"},
            {"id": "2", "slug": "pm"},
        ]
        urls = _extract_urls_from_template(
            items,
            "https://example.com/jobs/{id}/{slug}",
            "https://example.com",
        )
        assert "https://example.com/jobs/1/dev" in urls
        assert "https://example.com/jobs/2/pm" in urls

    def test_missing_key(self):
        items = [{"id": "1"}]
        urls = _extract_urls_from_template(
            items,
            "https://example.com/jobs/{id}/{slug}",
            "https://example.com",
        )
        assert len(urls) == 0  # KeyError → skipped

    def test_nested_field_alias(self):
        items = [
            {
                "itemID": "9201870385175_1",
                "customFieldGroup": {
                    "stringFields": [{"stringValue": "616994"}],
                },
            },
        ]
        urls = _extract_urls_from_template(
            items,
            "https://example.com/jobs?jobId={external_id}&itemId={itemID}",
            "https://example.com",
            url_template_fields={
                "external_id": "customFieldGroup.stringFields[0].stringValue",
            },
        )
        assert urls == {
            "https://example.com/jobs?jobId=616994&itemId=9201870385175_1",
        }

    def test_generated_slug(self):
        urls = _extract_urls_from_template(
            [{"reqId": "R_123", "title": "Risk & Insurance Advisor"}],
            "https://example.com/jobs/{reqId}/{slug}",
            "https://example.com",
            slug_fields=["title"],
        )

        assert urls == {"https://example.com/jobs/R_123/risk-and-insurance-advisor"}

    def test_non_sluggable_value_does_not_emit_malformed_url(self):
        urls = _extract_urls_from_template(
            [{"reqId": "R_123", "title": "東京"}],
            "https://example.com/jobs/{reqId}/{slug}",
            "https://example.com",
            slug_fields=["title"],
        )

        assert urls == set()


def _make_mock_pw(mock_page):
    """Create a mock Playwright instance that yields the given page."""
    if isinstance(mock_page.on, AsyncMock):
        mock_page.on = MagicMock()
    if isinstance(mock_page.remove_listener, AsyncMock):
        mock_page.remove_listener = MagicMock()

    mock_pw = MagicMock()
    mock_context = MagicMock()
    mock_context.new_page = AsyncMock(return_value=mock_page)
    mock_context.close = AsyncMock()

    mock_browser = AsyncMock()
    mock_browser.new_context = AsyncMock(return_value=mock_context)
    mock_browser.close = AsyncMock()

    mock_pw.chromium.launch = AsyncMock(return_value=mock_browser)
    return mock_pw


class TestDiscoverReplay:
    """Test discover() in replay mode (api_url in config)."""

    @pytest.mark.asyncio
    async def test_replay_rich_mode(self):
        """When fields are in config, discover should return list[DiscoveredJob]."""
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML"},
            {"title": "PM", "url": "/jobs/2", "desc": "More HTML"},
            {"title": "QA", "url": "/jobs/3", "desc": "QA HTML"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"

    @pytest.mark.asyncio
    async def test_http_rich_mode_uses_nested_url_template_fields(self):
        """HTTP replay wires nested aliases into canonical job URLs."""
        api_response = {
            "jobRequisitions": [
                {
                    "itemID": "9201870385175_1",
                    "customFieldGroup": {
                        "stringFields": [{"stringValue": "616994"}],
                    },
                    "requisitionTitle": "Underwriter",
                },
            ],
        }
        config = {
            "api_url": "https://api.example.com/job-requisitions",
            "method": "GET",
            "json_path": "jobRequisitions",
            "url_template": (
                "https://jobs.example.com/careers?jobId={external_job_id}&itemId={itemID}"
            ),
            "url_template_fields": {
                "external_job_id": "customFieldGroup.stringFields[0].stringValue",
            },
            "fields": {"title": "requisitionTitle"},
        }
        board = {"board_url": "https://jobs.example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)

        assert len(result) == 1
        assert result[0].url == (
            "https://jobs.example.com/careers?jobId=616994&itemId=9201870385175_1"
        )
        assert result[0].title == "Underwriter"

    @staticmethod
    def _datocms_board(total: int) -> tuple[dict, list[int], httpx.MockTransport]:
        requested_offsets: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            body = json.loads(request.content)
            offset = body["variables"]["skip"]
            requested_offsets.append(offset)
            count = 100 if offset == 0 else 1
            jobs = [
                {
                    "id": str(offset + index),
                    "title": f"Job {offset + index}",
                    "slug": f"job-{offset + index}",
                    "description": "Complete description",
                }
                for index in range(count)
            ]
            return httpx.Response(
                200,
                json={"data": {"_allCareersMeta": {"count": total}, "allCareers": jobs}},
                request=request,
            )

        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://graphql.example.com/",
                "method": "POST",
                "json_path": "data.allCareers",
                "total_path": "data._allCareersMeta.count",
                "post_data": {
                    "query": "query Careers { allCareers { id } }",
                    "variables": {"first": 100, "skip": 0},
                },
                "pagination": {
                    "param_name": "variables.skip",
                    "style": "offset",
                    "start_value": 0,
                    "increment": 100,
                    "location": "body",
                },
                "url_template": "https://example.com/career/{slug}/",
                "fields": {"title": "title", "description": "description"},
            },
        }
        return board, requested_offsets, httpx.MockTransport(handler)

    @pytest.mark.asyncio
    async def test_http_post_paginates_nested_graphql_offset_past_100(self):
        board, requested_offsets, transport = self._datocms_board(total=101)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(board, client, pw=None)

        assert isinstance(result, list)
        assert len(result) == 101
        assert requested_offsets == [0, 100]

    @pytest.mark.asyncio
    async def test_http_post_marks_short_graphql_pagination_truncated(self):
        from src.core.monitor import MonitorResult

        board, requested_offsets, transport = self._datocms_board(total=150)
        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(board, client, pw=None)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 101
        assert requested_offsets == [0, 100]

    @pytest.mark.asyncio
    async def test_replay_url_only_mode(self):
        """When no fields in config, discover should return set[str]."""
        items = [
            {"title": "Dev", "url": "https://example.com/jobs/1"},
            {"title": "PM", "url": "https://example.com/jobs/2"},
            {"title": "QA", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, set)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_no_playwright_returns_empty(self):
        """Without pw and no api_url, discover should return empty set."""
        board = {"board_url": "https://example.com/careers", "metadata": {}}
        http = AsyncMock()
        result = await discover(board, http, pw=None)
        assert isinstance(result, set)
        assert len(result) == 0


class TestJsonPathValues:
    """Tests for the ``json_path_values`` flag that coerces a dict-of-items
    at ``json_path`` to ``list(dict.values())``.

    This supports APIs like TalentClue that return
    ``{"jobs": {"<id>": {...}, ...}}`` instead of an array.
    """

    @pytest.mark.asyncio
    async def test_http_dict_values_mode_yields_items(self):
        """POST returns {"jobs": {"1": {...}, "2": {...}}}.

        With ``json_path_values: true, json_path: "jobs"``, both items
        should surface.
        """
        api_response = {
            "jobs": {
                "101": {"title": "Dev", "url": "/jobs/101"},
                "102": {"title": "PM", "url": "/jobs/102"},
            }
        }

        config = {
            "api_url": "https://api.example.com/jobs",
            "method": "POST",
            "headers": {"Accept": "application/json"},
            "json_path": "jobs",
            "json_path_values": True,
            "url_field": "url",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=api_response)
        http.request = AsyncMock(return_value=resp)

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 2
        titles = sorted(job.title for job in result)
        assert titles == ["Dev", "PM"]

    @pytest.mark.asyncio
    async def test_http_json_path_values_no_op_on_non_dict(self):
        """When resolved content is not a dict, ``json_path_values`` is a no-op."""
        api_response = {
            "jobs": [
                {"title": "Dev", "url": "/jobs/1"},
                {"title": "PM", "url": "/jobs/2"},
            ]
        }

        config = {
            "api_url": "https://api.example.com/jobs",
            "method": "GET",
            "json_path": "jobs",
            "json_path_values": True,  # flag present but content is already a list
            "url_field": "url",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=api_response)
        http.request = AsyncMock(return_value=resp)

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_http_without_flag_dict_response_yields_nothing(self):
        """Without the flag, a dict at ``json_path`` is not an item list —
        preserves existing behavior (no items surfaced, empty result).
        """
        api_response = {
            "jobs": {
                "101": {"title": "Dev", "url": "/jobs/101"},
                "102": {"title": "PM", "url": "/jobs/102"},
            }
        }

        config = {
            "api_url": "https://api.example.com/jobs",
            "method": "POST",
            "json_path": "jobs",
            # no json_path_values
            "url_field": "url",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value=api_response)
        http.request = AsyncMock(return_value=resp)

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_replay_dict_values_mode_yields_items(self):
        """Replay path: in-browser fetch returns dict-of-items; flag coerces
        to values list and items surface as DiscoveredJob."""
        api_response = {
            "jobs": {
                "101": {"title": "Dev", "url": "/jobs/101"},
                "102": {"title": "PM", "url": "/jobs/102"},
                "103": {"title": "QA", "url": "/jobs/103"},
            }
        }

        config = {
            "api_url": "https://api.example.com/jobs/{CLIENT_ID}/{BASE64}",
            "method": "POST",
            "json_path": "jobs",
            "json_path_values": True,
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        titles = sorted(job.title for job in result)
        assert titles == ["Dev", "PM", "QA"]


class TestHTTPFallback:
    """Test HTTP fallback behavior when Playwright fails."""

    @pytest.mark.asyncio
    async def test_replay_http_fallback_on_playwright_failure(self):
        """When browser fetch fails, falls back to httpx and returns data."""
        items = [
            {"title": "Dev", "url": "https://example.com/jobs/1"},
            {"title": "PM", "url": "https://example.com/jobs/2"},
            {"title": "QA", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        # Playwright fetch fails
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        # httpx succeeds — response methods are sync (not awaited)
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"

    @pytest.mark.asyncio
    async def test_browser_true_no_playwright_falls_back_to_http(self):
        """With browser: true but pw=None, falls back to _discover_http."""
        items = [
            {"title": "Dev", "url": "https://example.com/jobs/1"},
            {"title": "PM", "url": "https://example.com/jobs/2"},
        ]
        api_response = {"results": items, "total": 2}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        # httpx response — json() and raise_for_status() are sync
        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        # pw=None — should fall back to HTTP instead of returning empty
        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 2
        assert result[0].title == "Dev"

    @pytest.mark.asyncio
    async def test_replay_both_fail_raises(self):
        """When both browser and HTTP fallback fail, raises ApiSnifferFallbackError.

        The raised exception propagates up to the board processor, which records
        a failure (incrementing ``consecutive_failures``) so the auto-disable at
        5 kicks in for persistently-broken boards.  Previously this returned an
        empty list, causing the counter to bounce between success and empty and
        never trip the disable threshold.
        """
        config = {
            "api_url": "https://example.com/api/jobs",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        # httpx also fails
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP failed too")

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ApiSnifferFallbackError) as exc_info:
            await discover(board, http, pw=mock_pw)
        assert exc_info.value.api_url == "https://example.com/api/jobs"
        assert exc_info.value.board_url == "https://example.com/careers"
        # Chained from the underlying httpx failure
        assert exc_info.value.__cause__ is not None

    @pytest.mark.asyncio
    async def test_replay_both_fail_logs_at_warning_not_error(self):
        """http_fallback_failed must be logged at WARNING, not ERROR.

        These events are expected ends-of-fallback-chain; logging them at ERROR
        muddies the error budget.  The exception raised propagates the failure
        through the normal board-failure pipeline instead.
        """
        config = {
            "api_url": "https://example.com/api/jobs",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP failed too")
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        # Patch the module-level structlog BoundLogger to intercept calls.
        with (
            patch("src.core.monitors.api_sniffer.log") as mock_log,
            pytest.raises(ApiSnifferFallbackError),
        ):
            await discover(board, http, pw=mock_pw)

        warning_events = [c.args[0] for c in mock_log.warning.call_args_list]
        error_events = [c.args[0] for c in mock_log.error.call_args_list]

        assert "api_sniffer.http_fallback_failed" in warning_events, (
            f"expected http_fallback_failed at WARNING, got warnings={warning_events}"
        )
        assert "api_sniffer.http_fallback_failed" not in error_events, (
            f"http_fallback_failed must not be logged at ERROR, got errors={error_events}"
        )

    @pytest.mark.asyncio
    async def test_replay_fallback_raise_chains_original_exception(self):
        """The raised ApiSnifferFallbackError preserves the underlying cause.

        Operators reading the failure in Loki need to know which HTTP status
        or network error caused the fallback to exhaust; the chain carries
        that context via ``__cause__``.
        """
        config = {
            "api_url": "https://us.api.csod.com/rec-job-search/external/jobs",
            "browser": True,
            "fields": {"title": "title"},
        }
        board = {
            "board_url": "https://bradesco.csod.com/ux/ats/careersite/1/home",
            "metadata": config,
        }

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("Browser crashed"))
        mock_pw = _make_mock_pw(mock_page)

        original_error = RuntimeError("401 Unauthorized")
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = original_error
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ApiSnifferFallbackError) as exc_info:
            await discover(board, http, pw=mock_pw)
        assert exc_info.value.__cause__ is original_error
        assert "401 Unauthorized" in str(exc_info.value)


def _make_mock_response(url, data=None):
    """Create a mock Playwright Response with url and json()."""
    resp = MagicMock()
    resp.url = url
    if data is not None:
        resp.json = AsyncMock(return_value=data)
    else:
        resp.json = AsyncMock(side_effect=Exception("no body"))
    return resp


async def _emit_response(handlers, response):
    """Emit a response to every listener registered on a mock page."""
    for handler in list(handlers):
        result = handler(response)
        if isawaitable(result):
            await result


class TestLiveUrlDiscovery:
    """Test api_url_match dynamic URL discovery."""

    @pytest.mark.asyncio
    async def test_live_url_replaces_stale_token(self):
        """When a response matches api_url_match with a new token, api_url is updated."""
        stored_url = (
            "https://gateway.example.com/apigw-OLD_TOKEN/v1/api/jobs/search"
            "?pageSize=100&start=1&lang=en"
        )
        live_response_url = (
            "https://gateway.example.com/apigw-NEW_TOKEN/v1/api/jobs/search?pageSize=100&start=1"
        )
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on
        mock_page.remove_listener = MagicMock()

        async def fake_navigate(*args, **kwargs):
            await _emit_response(
                captured_handlers,
                _make_mock_response(live_response_url, {"jobs": []}),
            )

        mock_page.goto = fake_navigate

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert "apigw-NEW_TOKEN" in url
        assert "apigw-OLD_TOKEN" not in url
        assert "pageSize=100" in url
        assert "lang=en" in url
        assert data == {"jobs": []}

    @pytest.mark.asyncio
    async def test_no_match_keeps_stored_url(self):
        """When no response matches api_url_match, original api_url is returned."""
        stored_url = "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search?page=1"
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        mock_page.on = lambda event, handler: None
        mock_page.remove_listener = MagicMock()

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert url == stored_url
        assert data is None

    @pytest.mark.asyncio
    async def test_same_token_keeps_stored_url(self):
        """When live URL has the same base as stored, URL unchanged but data captured."""
        stored_url = "https://gateway.example.com/apigw-SAME/v1/api/jobs/search?page=1&lang=en"
        live_response_url = "https://gateway.example.com/apigw-SAME/v1/api/jobs/search?page=1"
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on
        mock_page.remove_listener = MagicMock()

        async def fake_navigate(*args, **kwargs):
            await _emit_response(
                captured_handlers,
                _make_mock_response(live_response_url, {"jobs": []}),
            )

        mock_page.goto = fake_navigate

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert url == stored_url
        assert data == {"jobs": []}

    @pytest.mark.asyncio
    async def test_navigation_failure_keeps_stored_url(self):
        """When navigation raises, stored api_url is returned (no crash)."""
        stored_url = "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search?page=1"
        api_url_match = "gateway.example.com/*/v1/api/jobs/search"

        mock_page = AsyncMock()
        mock_page.on = lambda event, handler: None
        mock_page.remove_listener = MagicMock()
        mock_page.goto = AsyncMock(side_effect=Exception("Akamai blocked"))

        url, data = await _discover_live_url(
            mock_page,
            board_url="https://www.example.com/careers",
            api_url=stored_url,
            api_url_match=api_url_match,
            wait="load",
            timeout=20000,
            settle=0,
        )

        assert url == stored_url
        assert data is None

    @pytest.mark.asyncio
    async def test_navigation_http_status_error_propagates(self, monkeypatch):
        """A concrete error document must not degrade to a stale API replay."""
        from src.shared import browser as browser_module
        from src.shared.browser import BrowserNavigationHTTPStatusError

        error = BrowserNavigationHTTPStatusError(
            requested_url="https://www.example.com/careers",
            response_url="https://www.example.com/error",
            status=503,
            phase="primary",
        )

        async def _raise_http_status(*_args, **_kwargs):
            raise error

        monkeypatch.setattr(browser_module, "navigate", _raise_http_status)
        mock_page = AsyncMock()
        mock_page.on = lambda event, handler: None

        with pytest.raises(BrowserNavigationHTTPStatusError) as exc_info:
            await _discover_live_url(
                mock_page,
                board_url="https://www.example.com/careers",
                api_url="https://gateway.example.com/api/jobs",
                api_url_match="gateway.example.com/api/jobs",
                wait="load",
                timeout=20_000,
                settle=0,
            )

        assert exc_info.value is error

    @pytest.mark.asyncio
    async def test_end_to_end_replay_with_api_url_match_and_route_params(self):
        """With route_params, navigates upfront and captures response directly."""
        items = [
            {"title": "Consultant", "url": "/jobs/1"},
            {"title": "Associate", "url": "/jobs/2"},
            {"title": "Analyst", "url": "/jobs/3"},
        ]
        api_response = {"docs": items, "total": 3}

        stored_url = "https://gateway.example.com/apigw-OLD/v1/api/jobs/search"
        live_url = "https://gateway.example.com/apigw-NEW/v1/api/jobs/search?pageSize=1000"

        config = {
            "api_url": stored_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "route_params": {"pageSize": "1000"},
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            await _emit_response(captured_handlers, _make_mock_response(live_url, api_response))

        mock_page.goto = fake_goto
        mock_page.route = AsyncMock()

        mock_pw = _make_mock_pw(mock_page)
        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Consultant"

        # No evaluate call — data came from captured response
        mock_page.evaluate.assert_not_called()
        # page.route was called to set up param overrides
        mock_page.route.assert_called_once()

    @pytest.mark.asyncio
    async def test_end_to_end_replay_api_url_match_no_route_params(self):
        """Without route_params, tries replay first, falls back to live discovery."""
        items = [
            {"title": "Consultant", "url": "/jobs/1"},
            {"title": "Associate", "url": "/jobs/2"},
            {"title": "Analyst", "url": "/jobs/3"},
        ]
        api_response = {"docs": items, "total": 3}

        stored_url = "https://gateway.example.com/apigw-OLD/v1/api/jobs/search"
        live_url = "https://gateway.example.com/apigw-NEW/v1/api/jobs/search?pageSize=100"

        config = {
            "api_url": stored_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        # Replay with stale URL fails
        mock_page.evaluate = AsyncMock(side_effect=Exception("stale token"))

        goto_count = 0

        async def fake_goto(*args, **kwargs):
            nonlocal goto_count
            goto_count += 1
            # Second navigation (retry) triggers the response handler
            if goto_count >= 2 and captured_handlers:
                await _emit_response(
                    captured_handlers,
                    _make_mock_response(live_url, api_response),
                )

        mock_page.goto = fake_goto

        mock_pw = _make_mock_pw(mock_page)
        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Consultant"

        # Evaluate was called (replay attempt with stale URL)
        assert mock_page.evaluate.call_count >= 1
        # Two navigations: initial (cookies) + retry (live discovery)
        assert goto_count >= 2

    @pytest.mark.asyncio
    async def test_route_params_modifies_outgoing_request(self):
        """route_params sets up page.route() to modify the page's own API request."""
        items = [{"title": f"Job{i}", "url": f"/jobs/{i}"} for i in range(100)]
        api_response = {"docs": items, "numFound": 100}

        config = {
            "api_url": "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search",
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "route_params": {"pageSize": "1000"},
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []
        routed_pattern = None

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_route(pattern, handler):
            nonlocal routed_pattern
            routed_pattern = pattern

        mock_page.route = fake_route

        async def fake_goto(*args, **kwargs):
            await _emit_response(
                captured_handlers,
                _make_mock_response(
                    "https://gateway.example.com/apigw-TOKEN/v1/api/jobs/search?pageSize=1000",
                    api_response,
                ),
            )

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        result = await discover(board, AsyncMock(), pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 100
        # page.route was called with a matching pattern
        assert routed_pattern is not None
        assert "v1/api/jobs/search" in routed_pattern


class TestRetryWithApiUrlMatch:
    """Test that fetch failure + api_url_match triggers live URL re-discovery."""

    @pytest.mark.asyncio
    async def test_upfront_discovery_misses_then_retry_succeeds(self):
        """When upfront _discover_live_url finds nothing (API hadn't fired yet),
        then the stale fetch fails, the retry re-navigates and captures the response."""
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        stale_url = "https://gateway.example.com/apigw-OLD/v1/api/jobs/search"
        live_url = "https://gateway.example.com/apigw-NEW/v1/api/jobs/search?p=1"

        config = {
            "api_url": stale_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "browser": True,
            "api_url_match": "gateway.example.com/*/v1/api/jobs/search",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        goto_count = 0
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            nonlocal goto_count
            goto_count += 1
            # First navigation: API hasn't fired yet (slow JS) — no match
            # Second navigation (retry): API fires, handler captures response
            if goto_count >= 2 and captured_handlers:
                await _emit_response(
                    captured_handlers,
                    _make_mock_response(live_url, api_response),
                )

        mock_page.goto = fake_goto

        # Browser fetch with stale URL fails
        mock_page.evaluate = AsyncMock(side_effect=Exception("404 Not Found"))

        mock_pw = _make_mock_pw(mock_page)
        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"

        # Two navigations: upfront miss + retry
        assert goto_count >= 2

    @pytest.mark.asyncio
    async def test_no_api_url_match_skips_rediscovery(self):
        """Without api_url_match, fetch failure goes straight to HTTP fallback.

        When the HTTP fallback also fails, ApiSnifferFallbackError is raised so
        the board processor advances the consecutive-failure counter rather
        than recording an empty check (which would reset it).
        """
        config = {
            "api_url": "https://api.example.com/v1/jobs",
            "method": "GET",
            "json_path": "docs",
            "browser": True,
            # No api_url_match — no re-discovery possible
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(side_effect=Exception("API down"))

        mock_pw = _make_mock_pw(mock_page)

        # HTTP fallback also fails
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("HTTP failed")
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        with pytest.raises(ApiSnifferFallbackError):
            await discover(board, http, pw=mock_pw)


class TestHttpModeUrlMatchFallback:
    """Test that _discover_http retries with live URL when api_url_match is set."""

    @pytest.mark.asyncio
    async def test_http_stale_url_uses_captured_response(self):
        """HTTP fetch fails → browser captures live response → uses it directly."""
        stale_url = "https://gateway.example.com/apigw-x0old0token/v1/api/jobs"
        live_url = "https://gateway.example.com/apigw-x0new0token/v1/api/jobs?page=1"
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        config = {
            "api_url": stale_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            await _emit_response(
                captured_handlers,
                _make_mock_response(live_url, api_response),
            )

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        # httpx: stale URL fails
        call_count = 0

        async def http_side_effect(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return _http_status_error_resp(403)

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Only one HTTP call (the initial 403 — non-retryable, no retry);
        # data came from the captured browser response.
        assert call_count == 1

    @pytest.mark.asyncio
    async def test_http_stale_url_retries_when_body_capture_fails(self):
        """HTTP fails → browser finds new URL but body read fails → HTTP retry with new URL."""
        stale_url = "https://gateway.example.com/apigw-x0old0token/v1/api/jobs"
        live_url = "https://gateway.example.com/apigw-x0new0token/v1/api/jobs?page=1"
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        config = {
            "api_url": stale_url,
            "method": "GET",
            "json_path": "docs",
            "url_field": "url",
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
            "fields": {"title": "title"},
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            if captured_handlers:
                # Response matched but body read will fail
                await _emit_response(
                    captured_handlers,
                    _make_mock_response(live_url, None),  # json() raises
                )

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        call_count = 0

        async def http_side_effect(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            if "x0old0token" in url:
                return _http_status_error_resp(403)
            resp = MagicMock()
            resp.raise_for_status = MagicMock()
            resp.json = MagicMock(return_value=api_response)
            return resp

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Two HTTP calls: stale (fail) + new URL (success)
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_http_no_api_url_match_returns_empty(self):
        """Without api_url_match, HTTP failure returns empty immediately."""
        config = {
            "api_url": "https://api.example.com/v1/jobs",
            "method": "GET",
            "json_path": "docs",
            "fields": {"title": "title"},
            # No api_url_match
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        http = AsyncMock()
        http.request = AsyncMock(return_value=_http_status_error_resp(403))

        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_http_url_match_no_playwright_returns_empty(self):
        """With api_url_match but no Playwright, HTTP failure returns empty."""
        config = {
            "api_url": "https://gateway.example.com/apigw-x0old0token/v1/api/jobs",
            "method": "GET",
            "json_path": "docs",
            "fields": {"title": "title"},
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        http = AsyncMock()
        http.request = AsyncMock(return_value=_http_status_error_resp(403))

        result = await discover(board, http, pw=None)
        assert isinstance(result, list)
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_http_url_match_same_url_uses_captured_data(self):
        """When URL didn't rotate but response was captured, uses captured data."""
        url = "https://gateway.example.com/apigw-x0same0token/v1/api/jobs"
        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        api_response = {"docs": items}

        config = {
            "api_url": url,
            "method": "GET",
            "json_path": "docs",
            "fields": {"title": "title"},
            "api_url_match": "gateway.example.com/*/v1/api/jobs",
        }
        board = {"board_url": "https://www.example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        captured_handlers = []

        def capture_on(event, handler):
            if event == "response":
                captured_handlers.append(handler)

        mock_page.on = capture_on

        async def fake_goto(*args, **kwargs):
            await _emit_response(captured_handlers, _make_mock_response(url, api_response))

        mock_page.goto = fake_goto
        mock_pw = _make_mock_pw(mock_page)

        call_count = 0

        async def http_side_effect(method, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return _http_status_error_resp(403)

        http = AsyncMock()
        http.request = AsyncMock(side_effect=http_side_effect)

        result = await discover(board, http, pw=mock_pw)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0].title == "Dev"
        # Only one HTTP call (the initial 403 — non-retryable); data from
        # captured browser response.
        assert call_count == 1


# ---------------------------------------------------------------------------
# Pagination retry semantics (#2733)
# ---------------------------------------------------------------------------


class TestHttpFetchWithRetry:
    """``http_fetch_with_retry`` mirrors ``fetch_with_retry``'s contract on
    api_sniffer's httpx surface: retryable statuses (5xx, 408/425/429)
    retry-then-raise, 404/410 return None (legitimate end-of-pagination),
    other non-retryable 4xx are lenient by default but can be raised for
    detail enrichment, and arbitrary network exceptions retry-then-raise.
    Pinned for #2733.
    """

    @pytest.mark.asyncio
    async def test_returns_json_on_200(self):
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        resp = MagicMock()
        resp.raise_for_status = MagicMock()
        resp.json = MagicMock(return_value={"items": []})
        client = AsyncMock()
        client.request = AsyncMock(return_value=resp)
        out = await http_fetch_with_retry(client, "GET", "https://x/api")
        assert out == {"items": []}
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_404(self):
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(404))
        out = await http_fetch_with_retry(client, "GET", "https://x/api")
        assert out is None
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_returns_none_on_403(self):
        """Other non-retryable 4xx — lenient stop with a warning."""
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(403))
        out = await http_fetch_with_retry(client, "GET", "https://x/api")
        assert out is None
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_strict_mode_raises_on_403(self):
        """Detail scrapers must not turn a forbidden response into empty content."""
        from src.core.monitors.api_sniffer import http_fetch_with_retry
        from src.shared.http_retry import PaginationFetchError

        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(403))
        with pytest.raises(PaginationFetchError) as exc_info:
            await http_fetch_with_retry(
                client,
                "GET",
                "https://x/api",
                raise_non_retryable=True,
            )

        assert exc_info.value.attempts == 1
        assert exc_info.value.last_status == 403
        assert client.request.await_count == 1

    @pytest.mark.asyncio
    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={"items": [1]})

        client = AsyncMock()
        client.request = AsyncMock(
            side_effect=[
                _http_status_error_resp(503),
                _http_status_error_resp(503),
                ok_resp,
            ]
        )
        out = await http_fetch_with_retry(client, "GET", "https://x/api", base_delay=0.001)
        assert out == {"items": [1]}
        assert client.request.await_count == 3

    @pytest.mark.asyncio
    async def test_raises_after_persistent_5xx(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(503))
        with pytest.raises(PaginationFetchError) as exc_info:
            await http_fetch_with_retry(client, "GET", "https://x/api", retries=3, base_delay=0.001)
        assert exc_info.value.last_status == 503
        assert exc_info.value.attempts == 3
        assert client.request.await_count == 3

    @pytest.mark.asyncio
    async def test_retries_on_cloudflare_5xx(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        ok_resp = MagicMock()
        ok_resp.raise_for_status = MagicMock()
        ok_resp.json = MagicMock(return_value={"items": [1]})
        for status in (520, 525, 530):
            client = AsyncMock()
            client.request = AsyncMock(side_effect=[_http_status_error_resp(status), ok_resp])
            out = await http_fetch_with_retry(client, "GET", "https://x/api", base_delay=0.001)
            assert out == {"items": [1]}, f"status {status} should retry then succeed"
            assert client.request.await_count == 2

    @pytest.mark.asyncio
    async def test_raises_after_persistent_network_error(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch_with_retry
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        client = AsyncMock()
        client.request = AsyncMock(side_effect=httpx.ConnectError("conn refused"))
        with pytest.raises(PaginationFetchError) as exc_info:
            await http_fetch_with_retry(client, "GET", "https://x/api", retries=2, base_delay=0.001)
        assert exc_info.value.last_error == "ConnectError"
        assert exc_info.value.last_status is None
        assert client.request.await_count == 2

    @pytest.mark.asyncio
    async def test_lenient_http_fetch_returns_none_on_persistent_5xx(self, monkeypatch):
        """The legacy ``http_fetch`` wrapper preserves the "any failure → None"
        contract by catching ``PaginationFetchError`` from
        ``http_fetch_with_retry`` for legacy monitor callers."""
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.core.monitors.api_sniffer import http_fetch

        monkeypatch.setattr(api_sniffer_module.asyncio, "sleep", AsyncMock())
        client = AsyncMock()
        client.request = AsyncMock(return_value=_http_status_error_resp(503))
        out = await http_fetch(client, "GET", "https://x/api")
        assert out is None
        # 3 attempts (default), retries exhausted, exception caught + None returned.
        assert client.request.await_count == 3


@pytest.mark.parametrize(
    ("discovered", "total", "expected"),
    [
        (99, 100, False),
        (98, 100, True),
        (9_900, 10_000, False),
        (9_899, 10_000, True),
        (8, 10, True),
        (10, 10, False),
        (11, 10, False),
        (0, None, False),
    ],
)
def test_advertised_total_gap_allows_only_race_sized_drift(
    discovered: int,
    total: int | None,
    expected: bool,
) -> None:
    assert _materially_below_advertised_total(discovered, total) is expected


class TestMaxItemsTruncation:
    """Regression tests for the MAX_ITEMS truncation guard (#3216 / #3267).

    The two silent-truncation sites in ``api_sniffer`` (HTTP-only mode at
    ``_discover_http`` and replay/browser mode at ``_discover_replay``)
    used to slice ``items[:MAX_ITEMS]`` and return a plain
    ``list[DiscoveredJob]`` / ``set[str]``. That dropped every URL
    beyond the cap and looked like a clean cycle to the board
    processor — so ``_MARK_GONE_BY_TIMESTAMP`` would tombstone the
    unseen tail on the next pass (the same silent-data-loss shape
    fixed by #2722 for fetch-failure-driven truncation).

    The fix matches the pattern used by the 29 monitors migrated in
    #3266: drop the slice, keep every collected item, and wrap the
    result via :mod:`src.shared.truncation` helpers so
    ``MonitorResult.truncated`` is ``True``. The board processor sees
    the flag, marks the cycle partial, and skips gone-detection.
    """

    @pytest.mark.asyncio
    async def test_http_mode_rich_returns_truncated_monitor_result(self):
        """``_discover_http`` rich path: > ``max_items`` -> truncated rich result.

        Uses ``max_items`` override in config (cheaper than 10k items)
        so the slice triggers at 2; the test still pins the contract.
        Returns a :class:`MonitorResult` with ``truncated=True`` and
        all URLs preserved (no slicing).
        """
        from src.core.monitor import MonitorResult

        # 3 items, max_items=2 → truncated.
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "max_items": 2,
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)

        assert isinstance(result, MonitorResult), (
            "HTTP truncation must return a MonitorResult (not a plain "
            "list[DiscoveredJob]) so the board processor sees "
            "truncated=True and skips _MARK_GONE_BY_TIMESTAMP."
        )
        assert result.truncated is True
        # All 3 URLs preserved — the cap is a safety stop, not a slice.
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is not None
        assert set(result.jobs_by_url) == result.urls

    @pytest.mark.asyncio
    async def test_http_mode_url_only_returns_truncated_monitor_result(self):
        """``_discover_http`` URL-only path: > ``max_items`` -> truncated URL result."""
        from src.core.monitor import MonitorResult

        items = [
            {"id": "1", "url": "https://example.com/jobs/1"},
            {"id": "2", "url": "https://example.com/jobs/2"},
            {"id": "3", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "max_items": 2,
            # No fields → URL-only mode.
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        # All 3 URLs preserved — no slicing.
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        # URL-only mode keeps jobs_by_url as None.
        assert result.jobs_by_url is None

    @pytest.mark.asyncio
    async def test_http_mode_under_cap_returns_plain_list(self):
        """Below ``max_items``: behaviour unchanged — plain list returned.

        Verifies the helper only fires when ``len(items) > max_items``;
        clean cycles must continue to return ``list[DiscoveredJob]`` /
        ``set[str]`` so unrelated callers (and the gone-detection path)
        keep working.

        Uses 3 items because ``find_arrays`` (in ``src/shared/api_sniff.py``)
        only surfaces arrays of 3+ dicts.
        """
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "max_items": 10,  # 3 items, cap=10 → not truncated
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_resp = MagicMock()
        mock_resp.json.return_value = api_response
        mock_resp.raise_for_status.return_value = None

        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)

        result = await discover(board, http, pw=None)

        # Below-cap path returns the plain list (not a MonitorResult).
        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_http_mode_advertised_total_gap_is_truncated(self):
        from src.core.monitor import MonitorResult

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": items, "total": 10}
        mock_resp.raise_for_status.return_value = None
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "json_path": "results",
                "url_field": "url",
                "max_items": 100,
                "fields": {"title": "title", "description": "desc"},
            },
        }

        result = await discover(board, http, pw=None)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 3

    @pytest.mark.asyncio
    async def test_http_mode_compares_unique_extracted_jobs_to_total(self):
        from src.core.monitor import MonitorResult

        items = [{"title": f"Job {i}", "url": "/jobs/duplicate", "desc": "HTML"} for i in range(3)]
        mock_resp = MagicMock()
        mock_resp.json.return_value = {"results": items, "total": 3}
        mock_resp.raise_for_status.return_value = None
        http = AsyncMock()
        http.request = AsyncMock(return_value=mock_resp)
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "json_path": "results",
                "url_field": "url",
                "max_items": 100,
                "fields": {"title": "title", "description": "desc"},
            },
        }

        result = await discover(board, http, pw=None)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {"https://example.com/jobs/duplicate"}

    @pytest.mark.asyncio
    async def test_replay_mode_rich_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_replay`` rich path: > ``MAX_ITEMS`` -> truncated rich result.

        ``_discover_replay`` doesn't honour ``max_items`` (only ``_discover_http``
        does), so the test patches the module-level ``MAX_ITEMS`` constant
        instead of crafting 10k items. Same pattern as the silent-slice
        regression suite in ``tests/test_truncation.py``.
        """
        from src.core.monitor import MonitorResult
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult), (
            "Replay truncation must return a MonitorResult so the board "
            "processor skips _MARK_GONE_BY_TIMESTAMP and the unseen tail "
            "beyond the cap is not tombstoned."
        )
        assert result.truncated is True
        # All 3 URLs preserved — no slicing.
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is not None
        assert set(result.jobs_by_url) == result.urls

    @pytest.mark.asyncio
    async def test_replay_mode_url_only_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_replay`` URL-only path: > ``MAX_ITEMS`` -> truncated URL result."""
        from src.core.monitor import MonitorResult
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)

        items = [
            {"id": "1", "url": "https://example.com/jobs/1"},
            {"id": "2", "url": "https://example.com/jobs/2"},
            {"id": "3", "url": "https://example.com/jobs/3"},
        ]
        api_response = {"results": items}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            # No fields → URL-only.
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is None

    @pytest.mark.asyncio
    async def test_replay_mode_under_cap_returns_plain_list(self, monkeypatch):
        """Below ``MAX_ITEMS``: behaviour unchanged — plain list returned.

        Uses 3 items because ``find_arrays`` (in ``src/shared/api_sniff.py``)
        only surfaces arrays of 3+ dicts.
        """
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 10)

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_response = {"results": items, "total": 3}

        config = {
            "api_url": "https://example.com/api/jobs",
            "method": "GET",
            "json_path": "results",
            "url_field": "url",
            "browser": True,
            "fields": {"title": "title", "description": "desc"},
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={"headers": {}, "text": json.dumps(api_response)}
        )
        mock_pw = _make_mock_pw(mock_page)

        http = AsyncMock()

        result = await discover(board, http, pw=mock_pw)

        # Below-cap path returns the plain list (not a MonitorResult).
        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_replay_mode_advertised_total_gap_is_truncated(self, monkeypatch):
        from src.core.monitor import MonitorResult
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 100)
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={
                "headers": {},
                "text": json.dumps({"results": items, "total": 10}),
            }
        )
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "json_path": "results",
                "url_field": "url",
                "browser": True,
                "fields": {"title": "title", "description": "desc"},
            },
        }

        result = await discover(board, AsyncMock(), pw=_make_mock_pw(mock_page))

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 3

    @pytest.mark.asyncio
    async def test_replay_mode_compares_unique_extracted_jobs_to_total(self, monkeypatch):
        from src.core.monitor import MonitorResult
        from src.core.monitors import api_sniffer as api_sniffer_module

        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 100)
        items = [{"title": f"Job {i}", "url": "/jobs/duplicate", "desc": "HTML"} for i in range(3)]
        mock_page = AsyncMock()
        mock_page.evaluate = AsyncMock(
            return_value={
                "headers": {},
                "text": json.dumps({"results": items, "total": 3}),
            }
        )
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "api_url": "https://example.com/api/jobs",
                "json_path": "results",
                "url_field": "url",
                "browser": True,
                "fields": {"title": "title", "description": "desc"},
            },
        }

        result = await discover(board, AsyncMock(), pw=_make_mock_pw(mock_page))

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {"https://example.com/jobs/duplicate"}


class TestDiscoverAutoTruncation:
    """Regression tests for the MAX_ITEMS truncation guard inside
    ``_discover_auto`` (#3336).

    ``_discover_auto`` is the auto-discover entry point (no ``api_url``
    in metadata) — full capture + detect + paginate pipeline. The third
    silent-slice site in ``api_sniffer.py`` used to slice
    ``items[:MAX_ITEMS]`` and return a plain list/set; same mass-delisting
    risk as #3216 / #3267 because the board processor never saw a
    truncation flag and ``_MARK_GONE_BY_TIMESTAMP`` would tombstone every
    URL beyond the cap.

    Stubs the module-level helpers (``capture_exchanges``,
    ``trigger_interactions``, ``detect_job_list``, ``infer_pagination``,
    ``paginate_all``, ``extract_urls_via_dom_crossref``) plus the
    locally-imported ``src.shared.browser`` symbols so the test exercises
    only the truncation branch.
    """

    @staticmethod
    def _patch_auto_pipeline(monkeypatch, items, *, url_field="url", total_count=None):
        """Stub the auto-discover pipeline so ``paginate_all`` returns *items*.

        Returns the patched module so the caller can `setattr` ``MAX_ITEMS``.
        """
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.shared import browser as browser_module
        from src.shared.api_sniff import ArrayCandidate, Exchange, JobListResult

        exchange = Exchange(
            method="GET",
            url="https://example.com/api/jobs",
            request_headers={},
            post_data=None,
            status=200,
            body={"results": items},
            content_type="application/json",
            phase="load",
        )
        candidate = ArrayCandidate(exchange=exchange, json_path="results", items=items)
        job_list_result = JobListResult(
            candidate=candidate,
            url_field=url_field,
            total_count=len(items) if total_count is None else total_count,
            pagination=None,
        )

        # Stubs on the api_sniffer module (where the names are bound).
        async def _fake_capture_exchanges(_page, _host):
            return [exchange]

        async def _fake_trigger_interactions(_page, _exchanges):
            return None

        def _fake_detect_job_list(_exchanges, _board_url):
            return job_list_result

        def _fake_infer_pagination(_exchanges, _url, _page_size):
            return None

        async def _fake_paginate_all(_fetcher, _result, _max_pages):
            return list(items)

        async def _fake_extract_urls_via_dom_crossref(_page, _items, _board_url):
            return []

        def _fake_make_browser_fetcher(_page):
            return None

        monkeypatch.setattr(api_sniffer_module, "capture_exchanges", _fake_capture_exchanges)
        monkeypatch.setattr(api_sniffer_module, "trigger_interactions", _fake_trigger_interactions)
        monkeypatch.setattr(api_sniffer_module, "detect_job_list", _fake_detect_job_list)
        monkeypatch.setattr(api_sniffer_module, "infer_pagination", _fake_infer_pagination)
        monkeypatch.setattr(api_sniffer_module, "paginate_all", _fake_paginate_all)
        monkeypatch.setattr(
            api_sniffer_module,
            "extract_urls_via_dom_crossref",
            _fake_extract_urls_via_dom_crossref,
        )
        monkeypatch.setattr(api_sniffer_module, "make_browser_fetcher", _fake_make_browser_fetcher)

        # Stub the locally-imported browser helpers. ``open_page`` is an
        # ``@asynccontextmanager`` so swap it for one that yields a mock page.
        from contextlib import asynccontextmanager

        @asynccontextmanager
        async def _fake_open_page(_pw, _config, use_proxy=False):
            yield AsyncMock()

        async def _fake_navigate(_page, _url, _opts):
            return None

        async def _fake_dismiss_overlays(_page):
            return None

        monkeypatch.setattr(browser_module, "open_page", _fake_open_page)
        monkeypatch.setattr(browser_module, "navigate", _fake_navigate)
        monkeypatch.setattr(browser_module, "dismiss_overlays", _fake_dismiss_overlays)

        return api_sniffer_module

    @pytest.mark.asyncio
    async def test_auto_rich_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_auto`` rich path: > ``MAX_ITEMS`` -> truncated rich result.

        Patches ``MAX_ITEMS = 2`` and feeds 3 items so the cycle trips the
        truncation guard. All 3 URLs must be preserved in the result.
        """
        from src.core.monitor import MonitorResult

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_sniffer_module = self._patch_auto_pipeline(monkeypatch, items)
        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)
        # _DEFAULT_SETTLE is autouse-patched to 0 elsewhere, keep symmetry.

        config = {
            # No api_url → auto-discover branch.
            "fields": {"title": "title", "description": "desc"},
            "settle": 0,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult), (
            "Auto-discover truncation must return a MonitorResult so the "
            "board processor skips _MARK_GONE_BY_TIMESTAMP and the unseen "
            "tail beyond the cap is not tombstoned."
        )
        assert result.truncated is True
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is not None
        assert set(result.jobs_by_url) == result.urls

    @pytest.mark.asyncio
    async def test_auto_advertised_total_gap_is_truncated(self, monkeypatch):
        from src.core.monitor import MonitorResult

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        self._patch_auto_pipeline(monkeypatch, items, total_count=10)
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "fields": {"title": "title", "description": "desc"},
                "settle": 0,
            },
        }

        result = await discover(board, AsyncMock(), pw=_make_mock_pw(AsyncMock()))

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert len(result.urls) == 3

    @pytest.mark.asyncio
    async def test_auto_compares_unique_extracted_jobs_to_total(self, monkeypatch):
        from src.core.monitor import MonitorResult

        items = [
            {"title": f"Job {i}", "url": "https://example.com/jobs/duplicate", "desc": "HTML"}
            for i in range(3)
        ]
        self._patch_auto_pipeline(monkeypatch, items, total_count=3)
        board = {
            "board_url": "https://example.com/careers",
            "metadata": {
                "fields": {"title": "title", "description": "desc"},
                "settle": 0,
            },
        }

        result = await discover(board, AsyncMock(), pw=_make_mock_pw(AsyncMock()))

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {"https://example.com/jobs/duplicate"}

    @pytest.mark.asyncio
    async def test_auto_url_only_returns_truncated_monitor_result(self, monkeypatch):
        """``_discover_auto`` URL-only path: > ``MAX_ITEMS`` -> truncated URL result."""
        from src.core.monitor import MonitorResult

        items = [
            {"id": "1", "url": "https://example.com/jobs/1"},
            {"id": "2", "url": "https://example.com/jobs/2"},
            {"id": "3", "url": "https://example.com/jobs/3"},
        ]
        api_sniffer_module = self._patch_auto_pipeline(monkeypatch, items)
        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 2)

        config = {
            # No api_url, no fields → URL-only auto-discover branch.
            "settle": 0,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.urls == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }
        assert result.jobs_by_url is None

    @pytest.mark.asyncio
    async def test_auto_under_cap_returns_plain_collection(self, monkeypatch):
        """Below ``MAX_ITEMS``: behaviour unchanged — plain list/set returned.

        Verifies the helper only fires when ``len(items) > MAX_ITEMS``;
        an under-cap auto-discover must keep returning the original
        ``list[DiscoveredJob]`` shape (regression: no MonitorResult wrap).
        """
        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        api_sniffer_module = self._patch_auto_pipeline(monkeypatch, items)
        monkeypatch.setattr(api_sniffer_module, "MAX_ITEMS", 10)

        config = {
            "fields": {"title": "title", "description": "desc"},
            "settle": 0,
        }
        board = {"board_url": "https://example.com/careers", "metadata": config}

        http = AsyncMock()
        mock_pw = _make_mock_pw(AsyncMock())

        result = await discover(board, http, pw=mock_pw)

        # Below-cap path returns the plain list (not a MonitorResult).
        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_auto_uses_captured_api_when_dom_interactions_are_unavailable(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.shared.api_sniff import ApiSnifferDomUnavailableError

        items = [
            {"title": "Dev", "url": "/jobs/1", "desc": "HTML1"},
            {"title": "PM", "url": "/jobs/2", "desc": "HTML2"},
            {"title": "QA", "url": "/jobs/3", "desc": "HTML3"},
        ]
        self._patch_auto_pipeline(monkeypatch, items)

        async def _no_dom(_page, _exchanges):
            raise ApiSnifferDomUnavailableError(
                "API sniffer fallback interactions require a usable document body"
            )

        monkeypatch.setattr(api_sniffer_module, "trigger_interactions", _no_dom)

        result = await discover(
            {
                "board_url": "https://example.com/careers",
                "metadata": {
                    "fields": {"title": "title", "description": "desc"},
                    "settle": 0,
                },
            },
            AsyncMock(),
            pw=_make_mock_pw(AsyncMock()),
        )

        assert isinstance(result, list)
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_auto_fails_when_navigation_leaves_no_dom_or_api(self, monkeypatch):
        from src.core.monitors import api_sniffer as api_sniffer_module
        from src.shared.api_sniff import ApiSnifferDomUnavailableError

        items = [
            {"title": "Dev", "url": "/jobs/1"},
            {"title": "PM", "url": "/jobs/2"},
            {"title": "QA", "url": "/jobs/3"},
        ]
        self._patch_auto_pipeline(monkeypatch, items)

        async def _no_dom(_page, _exchanges):
            raise ApiSnifferDomUnavailableError(
                "API sniffer fallback interactions require a usable document body"
            )

        monkeypatch.setattr(api_sniffer_module, "trigger_interactions", _no_dom)
        monkeypatch.setattr(api_sniffer_module, "detect_job_list", lambda *_args: None)

        with pytest.raises(
            ApiSnifferDomUnavailableError,
            match="require a usable document body",
        ):
            await discover(
                {
                    "board_url": "https://example.com/careers",
                    "metadata": {"settle": 0},
                },
                AsyncMock(),
                pw=_make_mock_pw(AsyncMock()),
            )
