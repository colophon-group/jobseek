"""Tests for DOM monitor pagination support."""

from __future__ import annotations

import asyncio
import re
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.monitors.dom import (
    BotChallengeError,
    _build_url_matcher,
    _dualoo_probe_config,
    _extract_links_rendered,
    _extract_links_static,
    _extract_rich_rows_static,
    _fetch_via_page,
    _filter_jsonld_job_urls,
    _filter_pdf_text_urls,
    _filter_unexpired_pdf_urls,
    _fingerprint_response_urls,
    _lucca_probe_config,
    _paginate_urls,
    _prospective_probe_config,
    _vagas_probe_config,
    _validated_pdf_text_config,
    _validated_response_fingerprint_config,
    _validated_rich_rows,
    _validated_unexpired_pdf_config,
    can_handle,
    dom_discover,
)
from src.shared.http_retry import PaginationFetchError
from src.shared.response_fingerprint import (
    MAX_RESPONSE_FINGERPRINT_BYTES,
    MAX_RESPONSE_FINGERPRINT_URL_CHARS,
)
from src.shared.tdm import TDMReservedError
from src.workspace._compat import auto_scraper_type

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patch target: ``_paginate_urls`` does ``from src.shared.http_retry import
# fetch_with_retry`` (#2722). Earlier patches at ``src.core.monitors.
# fetch_page_text`` no longer apply.
_FETCH_PATCH = "src.shared.http_retry.fetch_with_retry"
_EMPTY_FETCH_PATCH = "src.shared.http_retry.fetch_text_page_with_retry"


def _html_with_links(*urls: str) -> str:
    """Build minimal HTML with anchor tags for the given URLs."""
    links = "".join(f'<a href="{url}">link</a>' for url in urls)
    return f"<html><body>{links}</body></html>"


class _AsyncChunks(httpx.AsyncByteStream):
    def __init__(self, *chunks: bytes, fail_on_read: bool = False) -> None:
        self.chunks = chunks
        self.fail_on_read = fail_on_read

    async def __aiter__(self):
        if self.fail_on_read:
            raise AssertionError("response body must not be read")
        for chunk in self.chunks:
            yield chunk

    async def aclose(self) -> None:
        return None


def _make_fetch(pages: dict[str, str | None]):
    """Return an async function mimicking ``fetch_with_retry`` with
    per-URL canned responses. Signature matches the real function:
    ``(client, url, **kwargs) -> str | None``.
    """

    async def fake_fetch(client, url, **kwargs):
        return pages.get(url)

    return fake_fetch


# ---------------------------------------------------------------------------
# _extract_links_static
# ---------------------------------------------------------------------------


class TestExtractLinksStatic:
    def test_filters_job_keywords(self):
        html = _html_with_links(
            "https://example.com/jobs/123",
            "https://example.com/about",
            "https://example.com/career/456",
            "https://example.com/stellenangebote/detail/789",
        )
        urls = _extract_links_static(html, "https://example.com")
        assert urls == {
            "https://example.com/jobs/123",
            "https://example.com/career/456",
            "https://example.com/stellenangebote/detail/789",
        }

    def test_resolves_relative_urls(self):
        html = _html_with_links("/jobs/42", "/about")
        urls = _extract_links_static(html, "https://example.com/careers/")
        assert "https://example.com/jobs/42" in urls
        assert "https://example.com/about" not in urls

    def test_empty_html(self):
        assert _extract_links_static("", "https://example.com") == set()

    def test_url_matcher_overrides_keywords(self):
        """url_matcher regex replaces the default keyword filter."""
        import re

        html = _html_with_links(
            "https://example.com/emploi/paris/dev/123",
            "https://example.com/about",
            "https://example.com/emploi/lyon/pm/456",
        )
        matcher = re.compile(r"/emploi/")
        urls = _extract_links_static(html, "https://example.com", url_matcher=matcher)
        assert urls == {
            "https://example.com/emploi/paris/dev/123",
            "https://example.com/emploi/lyon/pm/456",
        }

    def test_url_matcher_none_uses_keywords(self):
        """Without url_matcher, default keyword filter applies."""
        html = _html_with_links(
            "https://example.com/emploi/123",
            "https://example.com/jobs/456",
        )
        urls = _extract_links_static(html, "https://example.com", url_matcher=None)
        # /emploi/ doesn't match keywords, /jobs/ does
        assert urls == {"https://example.com/jobs/456"}

    def test_ignores_job_keyword_in_hostname(self):
        """A careers hostname must not make navigation links look like jobs."""
        html = _html_with_links(
            "#",
            "/?page=login",
            "/?page=advertisement&sort=date",
            "/?page=advertisement_display&id=15794",
        )

        urls = _extract_links_static(html, "https://careers.example.com/?page=advertisement")

        assert urls == {"https://careers.example.com/?page=advertisement_display&id=15794"}

    def test_link_selector_scopes_and_trusts_matching_anchors(self):
        html = """
        <a class="job-card" href="/fr/emploi/analyste">Analyste</a>
        <a class="support-card" href="/fr/carrieres/formation">Formation</a>
        """

        urls = _extract_links_static(
            html,
            "https://example.com/carrieres",
            link_selector="a.job-card",
        )

        assert urls == {"https://example.com/fr/emploi/analyste"}

    def test_url_matcher_can_further_narrow_link_selector(self):
        html = """
        <a class="job-card" href="/emploi/active/1">Active</a>
        <a class="job-card" href="/emploi/archive/2">Archived</a>
        """

        urls = _extract_links_static(
            html,
            "https://example.com",
            url_matcher=re.compile(r"/active/"),
            link_selector="a.job-card",
        )

        assert urls == {"https://example.com/emploi/active/1"}


class TestExplicitEmptyState:
    @pytest.mark.parametrize("html", [None, ""])
    async def test_rejects_missing_response_with_configured_empty_marker(self, html):
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_selector": ".vacancy-list .view-empty",
                    },
                },
                AsyncMock(),
            )

    async def test_forbidden_link_selector_rejects_discovered_link(self):
        html = """
        <section class="vacancies">
          <h2>No positions available</h2>
          <a href="https://example.com/jobs/role-1">Unexpected role</a>
        </section>
        """
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="forbidden links present"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancies a[href]",
                        "empty_states": [
                            {
                                "selector": ".vacancies h2",
                                "exact_text": "No positions available",
                                "forbidden_link_selector": ".vacancies a[href]",
                            }
                        ],
                    },
                },
                AsyncMock(),
            )

    async def test_accepts_zero_links_with_configured_empty_marker(self):
        html = """
        <div class="vacancy-list">
          <div class="view-empty">No results found</div>
        </div>
        """
        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_selector": ".vacancy-list .view-empty",
                    },
                },
                AsyncMock(),
            )

        assert result == set()

    async def test_accepts_zero_rich_rows_with_configured_empty_marker(self):
        html = """
        <div class="job-list">
          <p class="empty">There are no job vacancies at the moment.</p>
        </div>
        """
        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "rich_rows": {
                            "row_selector": ".job-card",
                            "link_selector": "a[href]",
                        },
                        "empty_selector": ".job-list .empty",
                        "empty_text": "There are no job vacancies at the moment.",
                    },
                },
                AsyncMock(),
            )

        assert result == []

    async def test_rejects_zero_rich_rows_without_configured_marker(self):
        html = '<div class="job-list"></div>'
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "rich_rows": {
                            "row_selector": ".job-card",
                            "link_selector": "a[href]",
                        },
                        "empty_selector": ".job-list .empty",
                    },
                },
                AsyncMock(),
            )

    async def test_uses_finite_streaming_cap_for_trailing_empty_marker(self):
        html = (" " * 500_000) + '<h4 class="view-empty">No jobs available</h4>'

        async def bounded_fetch(_client, _url, **_kwargs):
            return html

        fetch = AsyncMock(side_effect=bounded_fetch)
        with patch(_EMPTY_FETCH_PATCH, fetch):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": "a.vacancy",
                        "empty_selector": "h4.view-empty",
                        "empty_text": "No jobs available",
                    },
                },
                AsyncMock(),
            )

        assert result == set()
        assert fetch.await_args.kwargs["max_bytes"] == 2 * 1024 * 1024
        assert fetch.await_args.kwargs["require_nonempty"] is True

    async def test_rejects_zero_links_without_configured_empty_marker(self):
        html = '<div class="vacancy-list"></div>'
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_selector": ".vacancy-list .view-empty",
                    },
                },
                AsyncMock(),
            )

    async def test_accepts_jobs_without_empty_marker(self):
        html = """
        <div class="vacancy-list">
          <a class="vacancy" href="/vacancies/legal-officer">Legal Officer</a>
        </div>
        """
        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_selector": ".vacancy-list .view-empty",
                    },
                },
                AsyncMock(),
            )

        assert result == {"https://example.com/vacancies/legal-officer"}

    @pytest.mark.parametrize("href", ["#apply", "https://example.com/vacancies"])
    async def test_board_self_links_do_not_bypass_empty_validation(self, href):
        html = f"""
        <div class="vacancy-list">
          <a href="{href}">Apply</a>
          <p id="apply">Email careers@example.com</p>
        </div>
        """
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a[href]",
                        "empty_selector": ".vacancy-list:not(:has(a[href]))",
                    },
                },
                AsyncMock(),
            )

    async def test_empty_text_disambiguates_a_shared_count_element(self):
        html = '<div class="vacancy-list"><p class="count">2 jobs found</p></div>'
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_selector": ".vacancy-list .count",
                        "empty_text": "0 jobs found",
                    },
                },
                AsyncMock(),
            )

    async def test_accepts_zero_links_with_matching_empty_text(self):
        html = '<div class="vacancy-list"><p class="count">0 Jobs Found</p></div>'
        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_selector": ".vacancy-list .count",
                        "empty_text": "0 jobs found",
                    },
                },
                AsyncMock(),
            )

        assert result == set()

    async def test_accepts_one_selector_specific_exact_empty_state(self):
        html = '<div class="vacancy-list"><h2 class="empty">No positions available</h2></div>'
        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_states": [
                            {"selector": ".vacancy-list .migrated", "exact_text": "Vacancies"},
                            {
                                "selector": ".vacancy-list h2.empty",
                                "exact_text": "No positions available",
                            },
                        ],
                    },
                },
                AsyncMock(),
            )

        assert result == set()

    async def test_accepts_exact_empty_state_with_only_matching_required_links(self):
        html = """
        <section class="vacancies">
          <h2>Jobs moved</h2>
          <div><a href="https://ats.example/jobs/role-123">Role one</a></div>
          <div><a href="https://ats.example/jobs/role-456">Role two</a></div>
        </section>
        """
        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancies a[href$='.pdf']",
                        "empty_states": [
                            {
                                "selector": ".vacancies h2",
                                "exact_text": "Jobs moved",
                                "required_link_selector": ".vacancies a[href]",
                                "required_link_url_pattern": (
                                    r"https://ats\.example/jobs/role-\d+"
                                ),
                            }
                        ],
                    },
                },
                AsyncMock(),
            )

        assert result == set()

    async def test_required_links_validate_every_matching_anchor(self):
        html = """
        <section class="vacancies">
          <h2>Jobs moved</h2>
          <div><a href="https://ats.example/jobs/role-123">Known role</a></div>
          <div><a href="https://unknown.example/jobs/role-456">Unknown role</a></div>
        </section>
        """
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancies a[href$='.pdf']",
                        "empty_states": [
                            {
                                "selector": ".vacancies h2",
                                "exact_text": "Jobs moved",
                                "required_link_selector": ".vacancies a[href]",
                                "required_link_url_pattern": (
                                    r"https://ats\.example/jobs/role-\d+"
                                ),
                            }
                        ],
                    },
                },
                AsyncMock(),
            )

    async def test_forbidden_link_selector_rejects_matching_anchor(self):
        html = """
        <section class="vacancies">
          <h2>No positions available</h2>
          <a href="https://unknown.example/jobs/role-1">Unexpected role</a>
        </section>
        """
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="forbidden links present"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancies a[href$='.pdf']",
                        "empty_states": [
                            {
                                "selector": ".vacancies h2",
                                "exact_text": "No positions available",
                                "forbidden_link_selector": ".vacancies a[href]",
                            }
                        ],
                    },
                },
                AsyncMock(),
            )

    async def test_selector_specific_empty_text_rejects_styled_error(self):
        html = (
            '<div class="vacancy-list"><h2 class="empty">Error: No positions available</h2></div>'
        )
        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_states": [
                            {
                                "selector": ".vacancy-list h2.empty",
                                "exact_text": "No positions available",
                            }
                        ],
                    },
                },
                AsyncMock(),
            )

    async def test_accepts_exact_empty_state_when_matching_marker_is_not_first(self):
        html = """
        <main>
          <p>About the organisation.</p>
          <p>There are currently no vacancies available.</p>
        </main>
        """
        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/careers",
                    "metadata": {
                        "link_selector": "main a[href]",
                        "empty_states": [
                            {
                                "selector": "main p",
                                "exact_text": "There are currently no vacancies available.",
                                "forbidden_link_selector": "main a[href]",
                            }
                        ],
                    },
                },
                AsyncMock(),
            )

        assert result == set()

    async def test_accepts_rendered_zero_links_with_empty_marker(self):
        page = MagicMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=page)
        context.__aexit__ = AsyncMock(return_value=None)
        html = '<div class="vacancy-list"><p class="view-empty">No jobs</p></div>'

        with (
            patch("src.core.monitors.dom.open_page", return_value=context),
            patch("src.core.monitors.dom._extract_links_rendered", AsyncMock(return_value=set())),
            patch("src.core.monitors.dom.safe_content", AsyncMock(return_value=html)),
        ):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "render": True,
                        "link_selector": ".vacancy-list a.vacancy",
                        "empty_selector": ".vacancy-list .view-empty",
                    },
                },
                AsyncMock(),
                pw=MagicMock(),
            )

        assert result == set()

    async def test_rendered_card_link_drift_is_not_accepted_as_empty(self):
        page = MagicMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=page)
        context.__aexit__ = AsyncMock(return_value=None)
        html = """
        <div class="resource">
          <div class="card-list">
            <div class="card-item">
              <a class="card-link-v2" href="/static-assets/pdf/role.pdf">Role</a>
            </div>
          </div>
        </div>
        """

        with (
            patch("src.core.monitors.dom.open_page", return_value=context),
            patch("src.core.monitors.dom._extract_links_rendered", AsyncMock(return_value=set())),
            patch("src.core.monitors.dom.safe_content", AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://www.fih.hockey/about-fih/work-in-hockey",
                    "metadata": {
                        "render": True,
                        "link_selector": ".resource .card-list a.card-link[href$='.pdf']",
                        "empty_selector": ".resource .card-list:not(:has(.card-item))",
                    },
                },
                AsyncMock(),
                pw=MagicMock(),
            )

    @pytest.mark.parametrize(
        "urls",
        [
            {"https://example.com/vacancies#apply"},
            {"https://example.com/vacancies"},
        ],
    )
    async def test_rendered_board_self_links_do_not_bypass_empty_validation(self, urls):
        page = MagicMock()
        context = MagicMock()
        context.__aenter__ = AsyncMock(return_value=page)
        context.__aexit__ = AsyncMock(return_value=None)
        html = """
        <div class="vacancy-list">
          <a href="#apply">Apply</a>
          <p id="apply">Email careers@example.com</p>
        </div>
        """

        with (
            patch("src.core.monitors.dom.open_page", return_value=context),
            patch("src.core.monitors.dom._extract_links_rendered", AsyncMock(return_value=urls)),
            patch("src.core.monitors.dom.safe_content", AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="did not match the configured explicit empty state"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "render": True,
                        "link_selector": ".vacancy-list a[href]",
                        "empty_selector": ".vacancy-list:not(:has(a[href]))",
                    },
                },
                AsyncMock(),
                pw=MagicMock(),
            )

    @pytest.mark.parametrize(
        "metadata",
        [
            {"empty_selector": ".view-empty"},
            {"link_selector": "a.vacancy", "empty_text": "0 jobs"},
            {
                "link_selector": "a.vacancy",
                "empty_selector": ".view-empty",
                "pagination": {"param_name": "page"},
            },
        ],
    )
    async def test_rejects_unsafe_empty_selector_combinations(self, metadata):
        with pytest.raises(ValueError, match="empty_selector"):
            await dom_discover(
                {"board_url": "https://example.com/vacancies", "metadata": metadata},
                AsyncMock(),
            )

    @pytest.mark.parametrize(
        "empty_states",
        [
            [],
            [{"selector": ".empty"}],
            [{"selector": ".empty", "exact_text": ""}],
            [{"selector": ".empty", "exact_text": "none"}] * 5,
            [
                {
                    "selector": ".empty",
                    "exact_text": "none",
                    "required_link_selector": "a[href]",
                }
            ],
            [
                {
                    "selector": ".empty",
                    "exact_text": "none",
                    "required_link_url_pattern": r"https://example\.com/jobs/",
                }
            ],
            [
                {
                    "selector": ".empty",
                    "exact_text": "none",
                    "required_link_selector": "a[href]",
                    "required_link_url_pattern": "(",
                }
            ],
            [
                {
                    "selector": ".empty",
                    "exact_text": "none",
                    "forbidden_link_selector": "",
                }
            ],
        ],
    )
    async def test_rejects_invalid_selector_specific_empty_states(self, empty_states):
        with pytest.raises(ValueError, match="empty_states"):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": "a.vacancy",
                        "empty_states": empty_states,
                    },
                },
                AsyncMock(),
            )

    async def test_rejects_mixed_legacy_and_selector_specific_empty_states(self):
        with pytest.raises(ValueError, match="cannot be combined"):
            await dom_discover(
                {
                    "board_url": "https://example.com/vacancies",
                    "metadata": {
                        "link_selector": "a.vacancy",
                        "empty_selector": ".empty",
                        "empty_states": [{"selector": ".empty", "exact_text": "No positions"}],
                    },
                },
                AsyncMock(),
            )


class TestRequireUnexpiredPdf:
    CONFIG = _validated_unexpired_pdf_config(
        {
            "pattern": (
                r"Applications must be submitted by "
                r"(\d{1,2}(?:st|nd|rd|th)? [A-Za-z]+ \d{4})"
            ),
            "date_format": "%d %B %Y",
        }
    )

    @staticmethod
    def _fake_reader(stream):
        from types import SimpleNamespace

        text = stream.read().removeprefix(b"%PDF ").decode()
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)])

    async def test_filters_expired_pdf_and_keeps_future_deadline(self, monkeypatch):
        active = "https://example.com/jobs/active.pdf"
        expired = "https://example.com/jobs/expired.pdf"
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)

        def handler(request):
            deadline = "31st December 2999" if str(request.url) == active else "1 January 2000"
            return httpx.Response(
                200,
                content=f"%PDF Applications must be submitted by {deadline}".encode(),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await _filter_unexpired_pdf_urls({active, expired}, client, self.CONFIG)

        assert result == {active}

    async def test_required_text_pattern_excludes_foreign_employer(self, monkeypatch):
        owned = "https://example.com/jobs/owned.pdf"
        foreign = "https://example.com/jobs/member.pdf"
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)

        def handler(request):
            employer = "European Athletics" if str(request.url) == owned else "Danish Athletics"
            return httpx.Response(
                200,
                content=(
                    f"%PDF {employer} is seeking a manager. "
                    "Applications must be submitted by 31 December 2999"
                ).encode(),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await _filter_unexpired_pdf_urls(
                {owned, foreign},
                client,
                self.CONFIG,
                required_text_pattern=re.compile(r"\bEuropean Athletics\b"),
            )

        assert result == {owned}

    async def test_required_text_pattern_can_fail_for_unclassified_employer(self, monkeypatch):
        url = "https://example.com/jobs/unclassified.pdf"
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=(
                    b"%PDF Danish Athletics is seeking a manager. "
                    b"Applications must be submitted by 31 December 2999"
                ),
                request=request,
            )
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="required ownership markers"):
                await _filter_unexpired_pdf_urls(
                    {url},
                    client,
                    self.CONFIG,
                    required_text_pattern=re.compile(r"\bEuropean Athletics\b"),
                    raise_on_required_text_mismatch=True,
                )

    async def test_omits_removed_pdf(self):
        url = "https://example.com/jobs/removed.pdf"
        transport = httpx.MockTransport(lambda request: httpx.Response(410, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

        assert result == set()

    async def test_rejects_declared_oversize_before_reading_body(self):
        url = "https://example.com/jobs/oversize.pdf"

        def handler(request):
            return httpx.Response(
                200,
                headers={"content-length": "999999999"},
                stream=_AsyncChunks(fail_on_read=True),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="document exceeds"):
                await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

    async def test_rejects_chunked_oversize_while_streaming(self, monkeypatch):
        url = "https://example.com/jobs/oversize.pdf"
        monkeypatch.setattr("src.core.monitors.dom._MAX_PDF_EXPIRATION_BYTES", 8)

        def handler(request):
            return httpx.Response(
                200,
                headers={"transfer-encoding": "chunked"},
                stream=_AsyncChunks(b"%PDF", b" 1234", b"5"),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="document exceeds 8 bytes"):
                await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

    async def test_honors_tdm_header_before_reading_body(self):
        url = "https://example.com/jobs/reserved.pdf"

        def handler(request):
            return httpx.Response(
                200,
                headers={"tdm-reservation": "1"},
                stream=_AsyncChunks(fail_on_read=True),
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(TDMReservedError, match="tdm-reservation=1"):
                await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

    async def test_rejects_pdf_over_page_limit(self, monkeypatch):
        from types import SimpleNamespace

        url = "https://example.com/jobs/many-pages.pdf"
        monkeypatch.setattr("src.core.monitors.dom._MAX_PDF_EXPIRATION_PAGES", 1)
        monkeypatch.setattr(
            "pypdf.PdfReader",
            lambda _stream: SimpleNamespace(
                pages=[
                    SimpleNamespace(extract_text=lambda: "page one"),
                    SimpleNamespace(extract_text=lambda: "page two"),
                ]
            ),
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"%PDF pages", request=request)
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="exceeds 1 pages"):
                await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

    async def test_rejects_extracted_text_over_limit(self, monkeypatch):
        from types import SimpleNamespace

        url = "https://example.com/jobs/expanded.pdf"
        monkeypatch.setattr("src.core.monitors.dom._MAX_PDF_EXPIRATION_TEXT_CHARS", 5)
        monkeypatch.setattr(
            "pypdf.PdfReader",
            lambda _stream: SimpleNamespace(
                pages=[SimpleNamespace(extract_text=lambda: "expanded text")]
            ),
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=b"%PDF text", request=request)
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="extracted text exceeds 5 characters"):
                await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

    async def test_keeps_deadline_on_current_utc_day(self, monkeypatch):
        url = "https://example.com/jobs/today.pdf"
        today = datetime.now(UTC).strftime("%-d %B %Y")
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=f"%PDF Applications must be submitted by {today}".encode(),
                request=request,
            )
        )

        async with httpx.AsyncClient(transport=transport) as client:
            result = await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

        assert result == {url}

    async def test_fails_closed_when_deadline_is_missing(self, monkeypatch):
        url = "https://example.com/jobs/undated.pdf"
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"%PDF No application date",
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="deadline was not found"):
                await _filter_unexpired_pdf_urls({url}, client, self.CONFIG)

    async def test_dom_discover_applies_pdf_deadline_filter(self, monkeypatch):
        board_url = "https://example.com/careers"
        active = "https://example.com/jobs/active.pdf"
        expired = "https://example.com/jobs/expired.pdf"
        listing = _html_with_links(active, expired)
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)

        def handler(request):
            deadline = "31 December 2999" if str(request.url) == active else "1 January 2000"
            return httpx.Response(
                200,
                content=f"%PDF Applications must be submitted by {deadline}".encode(),
                request=request,
            )

        with patch(_FETCH_PATCH, new=_make_fetch({board_url: listing})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await dom_discover(
                    {
                        "board_url": board_url,
                        "metadata": {
                            "link_selector": "a[href$='.pdf']",
                            "require_unexpired_pdf": {
                                "pattern": (
                                    r"Applications must be submitted by "
                                    r"(\d{1,2} [A-Za-z]+ \d{4})"
                                ),
                                "date_format": "%d %B %Y",
                            },
                        },
                    },
                    client,
                )

        assert result == {active}

    @pytest.mark.parametrize(
        "value",
        [
            True,
            {},
            {"pattern": "deadline", "date_format": "%Y-%m-%d"},
            {"pattern": "(deadline)", "date_format": "%Y", "extra": True},
        ],
    )
    def test_rejects_invalid_config(self, value):
        with pytest.raises(ValueError, match="require_unexpired_pdf"):
            _validated_unexpired_pdf_config(value)


class TestRequirePdfText:
    CONFIG = _validated_pdf_text_config(
        {"include": r"World Gymnastics", "exclude": r"Member Federation"}
    )

    @staticmethod
    def _fake_reader(stream):
        from types import SimpleNamespace

        text = stream.read().removeprefix(b"%PDF ").decode()
        return SimpleNamespace(pages=[SimpleNamespace(extract_text=lambda: text)])

    async def test_keeps_matching_employer_and_omits_other_pdf(self, monkeypatch):
        own_job = "https://example.com/files/own.pdf"
        member_job = "https://example.com/files/member.pdf"
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)

        def handler(request):
            employer = (
                "Fédération Internationale de Gymnastique"
                if str(request.url) == own_job
                else "National Gymnastics Federation"
            )
            return httpx.Response(200, content=f"%PDF {employer}".encode(), request=request)

        config = _validated_pdf_text_config(
            {
                "include": r"(?i)F[ée]d[ée]ration Internationale de Gymnastique",
                "exclude": r"National Gymnastics Federation",
            }
        )
        assert config is not None
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await _filter_pdf_text_urls({own_job, member_job}, client, config)

        assert result == {own_job}

    async def test_dom_discover_applies_pdf_text_filter(self, monkeypatch):
        board_url = "https://example.com/opportunities"
        own_job = "https://example.com/files/own.pdf"
        member_job = "https://example.com/files/member.pdf"
        listing = _html_with_links(own_job, member_job)
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)

        def handler(request):
            employer = "World Gymnastics" if str(request.url) == own_job else "Member Federation"
            return httpx.Response(200, content=f"%PDF {employer}".encode(), request=request)

        with patch(_FETCH_PATCH, new=_make_fetch({board_url: listing})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await dom_discover(
                    {
                        "board_url": board_url,
                        "metadata": {
                            "link_selector": "a[href$='.pdf']",
                            "require_pdf_text": {
                                "include": r"World Gymnastics",
                                "exclude": r"Member Federation",
                            },
                        },
                    },
                    client,
                )

        assert result == {own_job}

    async def test_accepts_explicitly_excluded_only_inventory(self, monkeypatch):
        member_job = "https://example.com/files/member.pdf"
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b"%PDF Member Federation",
                request=request,
            )
        )

        assert self.CONFIG is not None
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _filter_pdf_text_urls({member_job}, client, self.CONFIG)

        assert result == set()

    @pytest.mark.parametrize(
        "text", ["Unclassified employer", "World Gymnastics Member Federation"]
    )
    async def test_fails_closed_for_unclassified_or_overlapping_markers(self, monkeypatch, text):
        url = "https://example.com/files/unknown.pdf"
        monkeypatch.setattr("pypdf.PdfReader", self._fake_reader)
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, content=f"%PDF {text}".encode(), request=request)
        )

        assert self.CONFIG is not None
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="ownership markers"):
                await _filter_pdf_text_urls({url}, client, self.CONFIG)

    @pytest.mark.parametrize(
        "value",
        [
            True,
            "World Gymnastics",
            {},
            {"include": "World Gymnastics"},
            {"include": "(", "exclude": "Member"},
            {"include": "World", "exclude": "x" * 1_025},
            {"include": "World", "exclude": "Member", "extra": "value"},
        ],
    )
    def test_rejects_invalid_config(self, value):
        with pytest.raises(ValueError, match="require_pdf_text"):
            _validated_pdf_text_config(value)


class TestFingerprintResponse:
    CONTENT_TYPE = "application/pdf"
    BASE_HEADERS = {
        "content-type": CONTENT_TYPE,
        "etag": '"0123456789abcdef"',
        "last-modified": "Thu, 23 Jul 2026 14:06:14 GMT",
        "content-length": "3987062",
    }

    async def test_stable_validators_do_not_duplicate_or_change_identity(self):
        url = "https://assets.example.com/job.pdf"
        requests: list[httpx.Request] = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers=self.BASE_HEADERS, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await _fingerprint_response_urls({url, url}, client, self.CONTENT_TYPE)
            second = await _fingerprint_response_urls({url}, client, self.CONTENT_TYPE)

        assert len(first) == 1
        assert first == second
        assert all(request.method == "HEAD" for request in requests)
        assert next(iter(first)).startswith(f"{url}?_jobseek_fp=")

    async def test_fragment_is_not_part_of_request_or_identity(self):
        base = "https://assets.example.com/job.pdf"
        requests: list[httpx.Request] = []

        def handler(request):
            requests.append(request)
            return httpx.Response(200, headers=self.BASE_HEADERS, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await _fingerprint_response_urls({f"{base}#one"}, client, self.CONTENT_TYPE)
            second = await _fingerprint_response_urls({f"{base}#two"}, client, self.CONTENT_TYPE)

        assert first == second
        assert all(request.url.fragment == "" for request in requests)

    async def test_preserves_existing_query_and_rejects_reserved_parameter(self):
        url = "https://assets.example.com/job.pdf?download=1"
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, headers=self.BASE_HEADERS, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            result = await _fingerprint_response_urls({url}, client, self.CONTENT_TYPE)
            with pytest.raises(ValueError, match="reserved query parameter"):
                await _fingerprint_response_urls(
                    {f"{url}&_jobseek_fp=0123456789abcdef01234567"},
                    client,
                    self.CONTENT_TYPE,
                )

        assert next(iter(result)).startswith(f"{url}&_jobseek_fp=")

    async def test_validates_redirect_origin_before_issuing_next_request(self):
        requests: list[httpx.Request] = []

        def handler(request):
            requests.append(request)
            return httpx.Response(
                302,
                headers={"location": "https://other.example/job.pdf"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(ValueError, match="cross-origin redirect"):
                await _fingerprint_response_urls(
                    {"https://assets.example.com/job.pdf"}, client, self.CONTENT_TYPE
                )

        assert [request.url.host for request in requests] == ["assets.example.com"]

    async def test_follows_bounded_same_origin_redirect(self):
        requests: list[httpx.Request] = []

        def handler(request):
            requests.append(request)
            if request.url.path == "/job.pdf":
                return httpx.Response(302, headers={"location": "/current.pdf"}, request=request)
            return httpx.Response(200, headers=self.BASE_HEADERS, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await _fingerprint_response_urls(
                {"https://assets.example.com/job.pdf"}, client, self.CONTENT_TYPE
            )

        assert [request.url.path for request in requests] == ["/job.pdf", "/current.pdf"]
        assert next(iter(result)).startswith("https://assets.example.com/job.pdf?_jobseek_fp=")

    async def test_changed_strong_validator_creates_one_new_identity(self):
        url = "https://assets.example.com/job.pdf"
        etag = '"0123456789abcdef"'

        def handler(request):
            return httpx.Response(
                200,
                headers={**self.BASE_HEADERS, "etag": etag},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            first = await _fingerprint_response_urls({url}, client, self.CONTENT_TYPE)
            etag = '"fedcba9876543210"'
            second = await _fingerprint_response_urls({url}, client, self.CONTENT_TYPE)

        assert len(first) == len(second) == 1
        assert first.isdisjoint(second)

    @pytest.mark.parametrize(
        ("header", "value", "error"),
        [
            ("etag", None, "strong ETag"),
            ("etag", 'W/"0123456789abcdef"', "strong ETag"),
            ("etag", '"short"', "invalid strong ETag"),
            ("last-modified", None, "Last-Modified"),
            ("last-modified", "not a date", "invalid Last-Modified"),
            ("content-length", None, "invalid Content-Length"),
            ("content-length", "unknown", "invalid Content-Length"),
            ("content-length", str(MAX_RESPONSE_FINGERPRINT_BYTES + 1), "invalid Content-Length"),
            ("content-type", "text/html", "unexpected Content-Type"),
        ],
    )
    async def test_fails_closed_for_absent_or_weak_validators(self, header, value, error):
        url = "https://assets.example.com/job.pdf"
        headers = dict(self.BASE_HEADERS)
        if value is None:
            headers.pop(header)
        else:
            headers[header] = value
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, request=request)
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match=error):
                await _fingerprint_response_urls({url}, client, self.CONTENT_TYPE)

    async def test_rejects_tdm_reservation_and_oversized_url(self):
        url = "https://assets.example.com/job.pdf"
        headers = {**self.BASE_HEADERS, "tdm-reservation": "1"}
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, headers=headers, request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(TDMReservedError):
                await _fingerprint_response_urls({url}, client, self.CONTENT_TYPE)
            with pytest.raises(ValueError, match="supported length"):
                await _fingerprint_response_urls(
                    {f"https://assets.example.com/{'x' * MAX_RESPONSE_FINGERPRINT_URL_CHARS}.pdf"},
                    client,
                    self.CONTENT_TYPE,
                )

    async def test_request_failure_aborts_instead_of_returning_partial_inventory(self):
        urls = {
            "https://assets.example.com/active.pdf",
            "https://assets.example.com/unavailable.pdf",
        }

        def handler(request):
            status = 503 if request.url.path.endswith("unavailable.pdf") else 200
            return httpx.Response(status, headers=self.BASE_HEADERS, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await _fingerprint_response_urls(urls, client, self.CONTENT_TYPE)

    async def test_request_failure_cancels_and_drains_sibling_requests(self):
        slow_started = asyncio.Event()
        slow_cancelled = asyncio.Event()

        async def handler(request):
            if request.url.path.endswith("slow.pdf"):
                slow_started.set()
                try:
                    await asyncio.Future()
                except asyncio.CancelledError:
                    slow_cancelled.set()
                    raise
            await slow_started.wait()
            return httpx.Response(503, headers=self.BASE_HEADERS, request=request)

        urls = {
            "https://assets.example.com/a-failure.pdf",
            "https://assets.example.com/b-slow.pdf",
        }
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(httpx.HTTPStatusError):
                await _fingerprint_response_urls(urls, client, self.CONTENT_TYPE)

        assert slow_cancelled.is_set()

    async def test_dom_discover_fingerprints_only_current_listing_urls(self):
        board_url = "https://example.com/careers"
        document_url = "https://assets.example.com/job.pdf"
        listing = _html_with_links(document_url, document_url)

        def handler(request):
            return httpx.Response(200, headers=self.BASE_HEADERS, request=request)

        with patch(_FETCH_PATCH, new=_make_fetch({board_url: listing})):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result = await dom_discover(
                    {
                        "board_url": board_url,
                        "metadata": {
                            "link_selector": "a[href$='.pdf']",
                            "fingerprint_response": {"content_type": self.CONTENT_TYPE},
                        },
                    },
                    client,
                )

        assert len(result) == 1
        assert next(iter(result)).startswith(f"{document_url}?_jobseek_fp=")

    @pytest.mark.parametrize(
        "value",
        [True, {}, {"content_type": "application/pdf", "extra": True}, {"content_type": "pdf"}],
    )
    def test_rejects_invalid_config(self, value):
        with pytest.raises(ValueError, match="fingerprint_response"):
            _validated_response_fingerprint_config(value)


class TestRichRowsStatic:
    CONFIG = {
        "row_selector": ".job",
        "link_selector": ".job-title a",
        "location_selectors": [".job-location", ".job-country"],
    }

    def test_extracts_canonical_rows_with_joined_locations(self):
        html = """
        <div class="job">
          <div class="job-title"><a href="jobs/engineer---123">Engineer</a></div>
          <div class="job-location">Winterthur</div>
          <div class="job-country">Switzerland</div>
        </div>
        <div class="job">
          <div class="job-title"><a href="jobs/developer---456">Developer</a></div>
          <div class="job-location">Süßen</div>
          <div class="job-country">Germany</div>
        </div>
        """
        config = _validated_rich_rows(self.CONFIG)

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://example.com/careers/", config, None)

        assert [(job.url, job.title, job.locations) for job in jobs] == [
            (
                "https://example.com/careers/jobs/engineer---123",
                "Engineer",
                ["Winterthur, Switzerland"],
            ),
            (
                "https://example.com/careers/jobs/developer---456",
                "Developer",
                ["Süßen, Germany"],
            ),
        ]

    def test_fails_closed_when_a_configured_location_is_missing(self):
        html = """
        <div class="job">
          <div class="job-title"><a href="jobs/engineer---123">Engineer</a></div>
          <div class="job-location">Winterthur</div>
        </div>
        """
        config = _validated_rich_rows(self.CONFIG)

        assert config is not None
        with pytest.raises(ValueError, match="omitted configured location"):
            _extract_rich_rows_static(html, "https://example.com/careers/", config, None)

    def test_can_opt_in_to_missing_locations_for_detail_enrichment(self):
        html = """
        <div class="job">
          <div class="job-title"><a href="jobs/engineer---123">Engineer</a></div>
          <div class="job-location">Winterthur</div>
          <div class="job-country">Switzerland</div>
        </div>
        <div class="job">
          <div class="job-title"><a href="jobs/consultant---456">Consultant</a></div>
          <div class="job-location"></div>
          <div class="job-country"></div>
        </div>
        """
        config = _validated_rich_rows({**self.CONFIG, "allow_missing_locations": True})

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://example.com/careers/", config, None)

        assert [(job.title, job.locations) for job in jobs] == [
            ("Engineer", ["Winterthur, Switzerland"]),
            ("Consultant", None),
        ]

    def test_extracts_synhelion_live_row_shape(self):
        html = """
        <li class="uk-card job-card">
          <div class="name">
            <h2>Process and Operations Engineer (w/m/d) 100%</h2>
            <a href="https://synhelion.com/jobs/process-and-operations-engineer-w-m-d-100">
              Read more
            </a>
          </div>
          <div class="job-meta">
            <div class="location"><span uk-icon="location"></span> Jülich, Germany</div>
          </div>
        </li>
        """
        config = _validated_rich_rows(
            {
                "row_selector": 'li:has(a[href*="/jobs/"])',
                "link_selector": 'a[href*="/jobs/"]',
                "title_selector": "h2",
                "location_selectors": [".location"],
            }
        )

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://synhelion.com/about/careers", config, None)

        assert [(job.url, job.title, job.locations) for job in jobs] == [
            (
                "https://synhelion.com/jobs/process-and-operations-engineer-w-m-d-100",
                "Process and Operations Engineer (w/m/d) 100%",
                ["Jülich, Germany"],
            )
        ]

    def test_extracts_row_data_href_with_separate_title_selector(self):
        html = """
        <table><tbody>
          <tr data-href="/jobs/engineer">
            <td class="title">Engineer</td><td class="city">Winterthur</td>
            <td class="country">Switzerland</td>
          </tr>
        </tbody></table>
        """
        config = _validated_rich_rows(
            {
                "row_selector": "tbody tr[data-href]",
                "link_attr": "data-href",
                "title_selector": ".title",
                "location_selectors": [".city", ".country"],
            }
        )

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://example.com/careers", config, None)

        assert [(job.url, job.title, job.locations) for job in jobs] == [
            ("https://example.com/jobs/engineer", "Engineer", ["Winterthur, Switzerland"])
        ]

    def test_extracts_anchor_row_href(self):
        html = """
        <a class="job" href="/jobs/engineer">
          <span class="title">Engineer</span>
          <span class="location">Winterthur, Switzerland</span>
        </a>
        """
        config = _validated_rich_rows(
            {
                "row_selector": "a.job[href]",
                "title_selector": ".title",
                "location_selectors": [".location"],
            }
        )

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://example.com/careers", config, None)

        assert [(job.url, job.title, job.locations) for job in jobs] == [
            ("https://example.com/jobs/engineer", "Engineer", ["Winterthur, Switzerland"])
        ]

    def test_rejects_conflicting_rows_that_share_one_canonical_url(self):
        html = """
        <a class="job" href="/openings/">First distinct opportunity</a>
        <a class="job" href="/openings/">Second distinct opportunity</a>
        """
        config = _validated_rich_rows(
            {"row_selector": "a.job[href]", "allow_missing_locations": True}
        )

        assert config is not None
        with pytest.raises(ValueError, match="conflicting rows for one canonical URL"):
            _extract_rich_rows_static(html, "https://example.com/careers", config, None)

    def test_lifecycle_partition_accepts_current_and_ignores_known_inactive_rows(self):
        html = """
        <a class="job" href="/jobs/current.pdf?download=1">Current role</a>
        <a class="job" href="/jobs/another.pdf#preview">Another current role</a>
        <a class="job" href="/jobs/expired.pdf?legacy=1#top">Expired role</a>
        """
        config = _validated_rich_rows(
            {
                "row_selector": "a.job[href]",
                "allow_missing_locations": True,
                "active_urls": [
                    "https://example.com/jobs/current.pdf",
                    "https://example.com/jobs/another.pdf",
                ],
                "inactive_urls": ["https://example.com/jobs/expired.pdf"],
            }
        )

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://example.com/careers", config, None)

        assert [(job.url, job.title) for job in jobs] == [
            ("https://example.com/jobs/current.pdf", "Current role"),
            ("https://example.com/jobs/another.pdf", "Another current role"),
        ]

    def test_lifecycle_partition_fails_closed_on_an_unclassified_row(self):
        html = """
        <a class="job" href="/jobs/current.pdf">Current role</a>
        <a class="job" href="/jobs/new.pdf?download=1#preview">New unreviewed role</a>
        """
        config = _validated_rich_rows(
            {
                "row_selector": "a.job[href]",
                "allow_missing_locations": True,
                "active_urls": ["https://example.com/jobs/current.pdf"],
                "inactive_urls": [],
            }
        )

        assert config is not None
        with pytest.raises(ValueError, match="unclassified lifecycle URL"):
            _extract_rich_rows_static(html, "https://example.com/careers", config, None)

    @pytest.mark.parametrize(
        "active_urls,inactive_urls,error",
        [
            ([], [], "active_urls must be a bounded URL list"),
            (["/relative"], [], "active_urls must contain undecorated absolute HTTP URLs"),
            (
                ["https://example.com/jobs/current.pdf?download=1"],
                [],
                "active_urls must contain undecorated absolute HTTP URLs",
            ),
            (
                ["https://example.com/jobs/shared"],
                ["https://example.com/jobs/shared"],
                "active_urls and inactive_urls must be disjoint",
            ),
        ],
    )
    def test_lifecycle_partition_rejects_invalid_configuration(
        self,
        active_urls,
        inactive_urls,
        error,
    ):
        with pytest.raises(ValueError, match=error):
            _validated_rich_rows(
                {
                    "row_selector": "a.job[href]",
                    "active_urls": active_urls,
                    "inactive_urls": inactive_urls,
                }
            )

    def test_extracts_only_rows_between_named_section_markers(self):
        html = """
        <h2>Past roles</h2>
        <a class="job" href="/jobs/old">Old role</a>
        <h2>Current roles</h2>
        <a class="job" href="/jobs/current">Current role</a>
        <h2 id="students">Student projects</h2>
        <a class="job" href="/jobs/thesis">Thesis project</a>
        """
        config = _validated_rich_rows(
            {
                "row_selector": "a.job[href]",
                "section_start": {"selector": "h2", "text": "Current roles"},
                "section_end": {"selector": "h2#students"},
            }
        )

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://example.com/careers", config, None)

        assert [(job.url, job.title) for job in jobs] == [
            ("https://example.com/jobs/current", "Current role")
        ]

    def test_section_boundaries_fail_closed_when_markup_drifts(self):
        config = _validated_rich_rows(
            {
                "row_selector": "a.job[href]",
                "section_start": {"selector": "h2", "text": "Current roles"},
                "section_end": {"selector": "h2", "text": "Student projects"},
            }
        )

        assert config is not None
        with pytest.raises(ValueError, match="section_end did not match"):
            _extract_rich_rows_static(
                '<h2>Current roles</h2><a class="job" href="/jobs/current">Current</a>',
                "https://example.com/careers",
                config,
                None,
            )

    @pytest.mark.parametrize(
        ("html", "message"),
        [
            (
                '<h2>Current roles</h2><a class="job" href="/jobs/old">Old</a>'
                '<h2>Current roles</h2><a class="job" href="/jobs/current">Current</a>'
                "<h2>Student projects</h2>",
                "section_start matched multiple",
            ),
            (
                '<h2>Current roles</h2><a class="job" href="/jobs/current">Current</a>'
                "<h2>Student projects</h2><h2>Student projects</h2>",
                "section_end matched multiple",
            ),
        ],
    )
    def test_section_boundaries_reject_ambiguous_markers(self, html, message):
        config = _validated_rich_rows(
            {
                "row_selector": "a.job[href]",
                "section_start": {"selector": "h2", "text": "Current roles"},
                "section_end": {"selector": "h2", "text": "Student projects"},
            }
        )

        assert config is not None
        with pytest.raises(ValueError, match=message):
            _extract_rich_rows_static(
                html,
                "https://example.com/careers",
                config,
                None,
            )

    def test_extracts_metadata_for_semantic_job_filtering(self):
        html = """
        <div class="job">
          <div class="type">Vacancy</div>
          <div class="title"><a href="/jobs/engineer">Engineer</a></div>
        </div>
        <div class="job">
          <div class="type">RFP</div>
          <div class="title"><a href="/jobs/hosting">Website hosting</a></div>
        </div>
        """
        config = _validated_rich_rows(
            {
                "row_selector": ".job",
                "link_selector": ".title a",
                "metadata_selectors": {"opportunity_type": ".type"},
            }
        )

        assert config is not None
        jobs = _extract_rich_rows_static(html, "https://example.com/careers", config, None)

        assert [job.metadata for job in jobs] == [
            {"opportunity_type": "Vacancy"},
            {"opportunity_type": "RFP"},
        ]

    def test_fails_closed_when_configured_metadata_is_missing(self):
        html = '<div class="job"><a href="/jobs/engineer">Engineer</a></div>'
        config = _validated_rich_rows(
            {
                "row_selector": ".job",
                "link_selector": "a",
                "metadata_selectors": {"opportunity_type": ".type"},
            }
        )

        assert config is not None
        with pytest.raises(ValueError, match="omitted configured metadata 'opportunity_type'"):
            _extract_rich_rows_static(html, "https://example.com/careers", config, None)

    @pytest.mark.parametrize(
        "config",
        [
            {},
            {"row_selector": ".job", "link_selector": ".job a", "unexpected": True},
            {"row_selector": ".job", "link_attr": "not valid!"},
            {
                "row_selector": ".job",
                "link_selector": ".job a",
                "allow_missing_locations": "yes",
            },
            {"row_selector": ".job", "link_selector": ".job a", "location_selectors": "p"},
            {"row_selector": ".job", "link_selector": ".job a", "total_selector": "a["},
            {"row_selector": ".job", "location_selectors": False},
            {"row_selector": ".job", "location_selectors": 0},
            {"row_selector": ".job", "location_selectors": {}},
            {
                "row_selector": ".job",
                "link_selector": ".job a",
                "location_selectors": ["p"] * 5,
            },
            {
                "row_selector": ".job",
                "link_selector": ".job a",
                "metadata_selectors": {"not valid!": ".type"},
            },
            {
                "row_selector": ".job",
                "link_selector": ".job a",
                "metadata_selectors": {"type": "a["},
            },
            {"row_selector": ".job", "metadata_selectors": []},
            {"row_selector": ".job", "metadata_selectors": ""},
            {"row_selector": ".job", "metadata_selectors": 0},
            {"row_selector": ".job", "metadata_selectors": False},
            {
                "row_selector": ".job",
                "section_start": {"selector": "h2"},
            },
            {
                "row_selector": ".job",
                "section_start": {"selector": "h2", "unexpected": True},
                "section_end": {"selector": "footer"},
            },
        ],
    )
    def test_rejects_invalid_configs(self, config):
        with pytest.raises(ValueError, match="rich_rows"):
            _validated_rich_rows(config)

    @pytest.mark.asyncio
    async def test_static_discovery_returns_rich_jobs(self):
        html = """
        <div class="job">
          <div class="job-title"><a href="jobs/engineer---123">Engineer</a></div>
          <div class="job-location">Winterthur</div>
          <div class="job-country">Switzerland</div>
        </div>
        """
        with patch(_FETCH_PATCH, AsyncMock(return_value=html)):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/careers/",
                    "metadata": {"render": False, "rich_rows": self.CONFIG},
                },
                AsyncMock(),
            )

        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0].title == "Engineer"
        assert result[0].locations == ["Winterthur, Switzerland"]

    @pytest.mark.asyncio
    async def test_static_rich_discovery_disables_shared_body_truncation(self):
        first = """
        <div class="job">
          <div class="job-title"><a href="jobs/first---123">First</a></div>
          <div class="job-location">Winterthur</div>
          <div class="job-country">Switzerland</div>
        </div>
        """
        second = """
        <div class="job">
          <div class="job-title"><a href="jobs/second---456">Second</a></div>
          <div class="job-location">Süßen</div>
          <div class="job-country">Germany</div>
        </div>
        """
        html = first + (" " * 500_000) + second

        async def bounded_fetch(_client, _url, *, max_chars=500_000, **_kwargs):
            return html[:max_chars] if max_chars is not None else html

        fetch = AsyncMock(side_effect=bounded_fetch)

        with patch(_FETCH_PATCH, fetch):
            result = await dom_discover(
                {
                    "board_url": "https://example.com/careers/",
                    "metadata": {"render": False, "rich_rows": self.CONFIG},
                },
                AsyncMock(),
            )

        assert isinstance(result, list)
        assert [job.title for job in result] == ["First", "Second"]
        assert fetch.await_args.kwargs["max_chars"] is None

    @pytest.mark.asyncio
    async def test_static_rich_discovery_paginates_and_merges_rows(self):
        first = """
        <div class="job">
          <div class="job-title"><a href="/jobs/first">First</a></div>
          <div class="job-location">Winterthur</div>
          <div class="job-country">Switzerland</div>
        </div>
        """
        second = """
        <div class="job">
          <div class="job-title"><a href="/jobs/second">Second</a></div>
          <div class="job-location">Berlin</div>
          <div class="job-country">Germany</div>
        </div>
        """
        board_url = "https://example.com/careers/"
        pages = {
            board_url: first,
            "https://example.com/results?start=25": second,
            "https://example.com/results?start=50": "   ",
        }

        with patch(_FETCH_PATCH, side_effect=_make_fetch(pages)):
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {
                        "render": False,
                        "rich_rows": self.CONFIG,
                        "pagination": {
                            "url_template": "https://example.com/results?start={page}",
                            "start": 0,
                            "increment": 25,
                        },
                    },
                },
                AsyncMock(),
            )

        assert isinstance(result, list)
        assert [(job.title, job.locations) for job in result] == [
            ("First", ["Winterthur, Switzerland"]),
            ("Second", ["Berlin, Germany"]),
        ]

    @pytest.mark.asyncio
    async def test_rejects_rendered_rich_rows(self):
        for incompatible in (
            {"render": True},
            {"require_jsonld_jobposting": True},
        ):
            with pytest.raises(ValueError, match="static listing"):
                await dom_discover(
                    {
                        "board_url": "https://example.com/careers/",
                        "metadata": {**incompatible, "rich_rows": self.CONFIG},
                    },
                    AsyncMock(),
                )

    @pytest.mark.asyncio
    async def test_rejects_browser_pagination_for_rich_rows(self):
        with (
            patch(
                _FETCH_PATCH,
                AsyncMock(
                    return_value="""
                <div class="job">
                  <div class="job-title"><a href="/jobs/first">First</a></div>
                  <div class="job-location">Winterthur</div>
                  <div class="job-country">Switzerland</div>
                </div>
            """
                ),
            ),
            pytest.raises(ValueError, match="static sequential pages"),
        ):
            await dom_discover(
                {
                    "board_url": "https://example.com/careers/",
                    "metadata": {
                        "rich_rows": self.CONFIG,
                        "pagination": {"param_name": "page", "browser": True},
                    },
                },
                AsyncMock(),
            )


class TestBuildUrlMatcher:
    def test_string_filter(self):
        m = _build_url_matcher("/emploi/")
        assert m is not None
        assert m.search("https://example.com/emploi/123")
        assert not m.search("https://example.com/about")

    def test_dict_filter_include(self):
        m = _build_url_matcher({"include": "/jobs/", "exclude": "/blog/"})
        assert m is not None
        assert m.search("https://example.com/jobs/123")

    def test_none_filter(self):
        assert _build_url_matcher(None) is None
        assert _build_url_matcher("") is None


class TestCanHandle:
    DUALOO_URL = "https://jobs.dualoo.com/portal/fyuan4bk?lang=DE"
    DUALOO_HTML = """
    <html><head><link rel="stylesheet" href="/css/fyuan4bk"></head><body>
      <div class="JobInfoBox">
        <a class="row jobElement"
           href="fyuan4bk/ef8b03a4-9219-4c19-a351-d01c0e07cc4f/detail?lang=DE">Role 1</a>
        <a class="row jobElement"
           href="fyuan4bk/ffc75823-9ee1-4edb-9c65-dc6c8e0992af/detail?lang=DE">Role 2</a>
        <a class="row jobElement" href="https://evil.example/portal/fyuan4bk/ffc75823-9ee1-4edb-9c65-dc6c8e0992af/detail">Injected</a>
      </div>
    </body></html>
    """
    LUCCA_URL = "https://jobs.world.luccasoftware.com/world-aquatics"
    LUCCA_HTML = """
    <html><body class="jobBoard"><div id="jobBoardOffers">
      <ul class="jobBoard-offers-list">
        <li class="jobBoard-offers-item">
          <a class="jobBoard-offers-item-link"
             href="/world-aquatics/athlete-intern-050521f8-610b-4d01-b201-6007b42b6a93">
            Athlete Intern
          </a>
          <div class="jobBoard-offers-item-tags">
            <span class="tag palette-glacier">Lausanne</span>
            <span class="tag palette-lime">Internship | 6 months</span>
          </div>
        </li>
        <li class="jobBoard-offers-item">
          <a class="jobBoard-offers-item-link"
             href="/world-aquatics/development-manager-517a3a34-bf2b-43cc-b15a-db3761bcd3c3">
            Development Manager
          </a>
          <div class="jobBoard-offers-item-tags">
            <span class="tag palette-glacier">Budapest</span>
          </div>
        </li>
      </ul>
    </div></body></html>
    """
    LUCCA_EMPTY_HTML = """
    <html><body class="jobBoard"><div id="jobBoardOffers">
      <p class="jobBoard-offers-empty">There are no job vacancies at the moment.</p>
    </div></body></html>
    """
    PROSPECTIVE_URL = "https://jobs.example.com/?lang=de"
    PROSPECTIVE_HTML = """
    <html lang="de"><head>
      <link href="/careercenter/1000973/assets/css/company.css" rel="stylesheet">
    </head><body class="career-center">
      <header><h1 class="jobs-total"><span class="total">2</span> Jobs</h1></header>
      <div id="jobs-list">
        <div class="job">
          <a class="job-title" href="/offene-stellen/engineer/268ceacb-05c3-4a11-a8a0-80b078a3f4e4">
            Platform Engineer
          </a>
          <span class="place-of-work">Bern oder Zürich</span>
        </div>
        <div class="job">
          <a class="job-title"
             href="/emplois-vacantes/analyste/5f1e0316-6225-4e57-b40c-cb605e046331">
            Analyste
          </a>
          <span class="place-of-work">Bern</span>
        </div>
      </div>
    </body></html>
    """

    def test_lucca_board_uses_static_rich_row_preset(self):
        result = _lucca_probe_config(self.LUCCA_HTML, self.LUCCA_URL)

        assert result is not None
        assert result["lucca_board"] is True
        assert result["urls"] == 2
        assert result["rich_rows"] == {
            "row_selector": ".jobBoard-offers-item",
            "link_selector": ".jobBoard-offers-item-link[href]",
            "location_selectors": [".jobBoard-offers-item-tags > .tag:first-child"],
        }
        jobs = _extract_rich_rows_static(
            self.LUCCA_HTML,
            self.LUCCA_URL,
            _validated_rich_rows(result["rich_rows"]),
            re.compile(result["url_filter"], re.IGNORECASE),
        )
        assert [(job.title, job.locations) for job in jobs] == [
            ("Athlete Intern", ["Lausanne"]),
            ("Development Manager", ["Budapest"]),
        ]

        scraper_type, scraper_config = auto_scraper_type("dom", result) or (None, None)
        assert scraper_type == "dom"
        assert scraper_config is not None
        assert scraper_config["enrich"] == ["description"]
        assert scraper_config["scope"] == ".jobOffer-article"

    def test_lucca_empty_board_keeps_authoritative_provider_preset(self):
        result = _lucca_probe_config(self.LUCCA_EMPTY_HTML, self.LUCCA_URL)

        assert result is not None
        assert result["urls"] == 0
        assert result["empty_selector"] == ".jobBoard-offers-empty"
        assert result["empty_text"] == "There are no job vacancies at the moment."

    async def test_lucca_can_handle_returns_provider_preset(self):
        with patch(
            "src.core.monitors.fetch_page_text",
            new=AsyncMock(return_value=self.LUCCA_HTML),
        ):
            result = await can_handle(self.LUCCA_URL, MagicMock())

        assert result == _lucca_probe_config(self.LUCCA_HTML, self.LUCCA_URL)

    def test_prospective_board_uses_static_rich_row_preset(self):
        result = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)

        assert result is not None
        assert result["prospective_board"] == "1000973"
        assert result["prospective_canonical_path"] == "/offene-stellen/job/"
        assert result["urls"] == 2
        assert result["rich_rows"] == {
            "row_selector": "#jobs-list .job",
            "link_selector": "a.job-title[href]",
            "total_selector": ".jobs-total .total",
            "location_selectors": [".place-of-work"],
        }
        jobs = _extract_rich_rows_static(
            self.PROSPECTIVE_HTML,
            self.PROSPECTIVE_URL,
            _validated_rich_rows(result["rich_rows"]),
            re.compile(result["url_filter"], re.IGNORECASE),
        )
        assert [(job.title, job.locations) for job in jobs] == [
            ("Platform Engineer", ["Bern oder Zürich"]),
            ("Analyste", ["Bern"]),
        ]

        scraper_type, scraper_config = auto_scraper_type("dom", result) or (None, None)
        assert scraper_type == "dom"
        assert scraper_config is not None
        assert scraper_config["enrich"] == ["description"]
        assert scraper_config["scope"] == "#job"

    def test_prospective_empty_board_requires_exact_zero_total(self):
        empty_html = """
        <html><head><link href="/careercenter/1000973/assets/css/company.css"></head>
        <body class="career-center">
          <header><span class="jobs-total"><span class="total">0</span></span></header>
          <div id="jobs-list"></div>
        </body></html>
        """
        result = _prospective_probe_config(empty_html, self.PROSPECTIVE_URL)

        assert result is not None
        assert result["urls"] == 0
        assert result["empty_states"] == [
            {
                "selector": "body.career-center:has(#jobs-list) .jobs-total .total",
                "exact_text": "0",
                "required_link_selector": "link[href*='careercenter/1000973/assets/']",
                "required_link_url_pattern": (
                    r"^(?:https://jobs\.example\.com|https://ohws\.prospective\.ch)/"
                    r"(?:public/v[12]/)?careercenter/1000973/assets/[^?#]+(?:[?#].*)?$"
                ),
                "forbidden_link_selector": "#jobs-list a.job-title[href]",
            }
        ]

        drifted_html = empty_html.replace(">0<", ">10<")
        assert _prospective_probe_config(drifted_html, self.PROSPECTIVE_URL) is None

    @pytest.mark.parametrize(
        "partial_html",
        [
            """
            <html><head><link href="/careercenter/1000973/assets/css/company.css"></head>
            <body class="career-center">
              <span class="jobs-total"><span class="total">0</span></span>
            </body></html>
            """,
            """
            <html><head></head><body class="career-center">
              <span class="jobs-total"><span class="total">0</span></span>
              <div id="jobs-list"></div>
            </body></html>
            """,
        ],
        ids=["missing-list-container", "missing-provider-asset"],
    )
    async def test_prospective_runtime_rejects_partial_zero_shell(self, partial_html: str):
        valid_empty_html = """
        <html><head><link href="/careercenter/1000973/assets/css/company.css"></head>
        <body class="career-center">
          <span class="jobs-total"><span class="total">0</span></span>
          <div id="jobs-list"></div>
        </body></html>
        """
        config = _prospective_probe_config(valid_empty_html, self.PROSPECTIVE_URL)
        assert config is not None

        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=partial_html)),
            pytest.raises(ValueError, match="provider identity proof is missing or ambiguous"),
        ):
            await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

    def test_prospective_rejects_partial_advertised_inventory(self):
        partial_html = self.PROSPECTIVE_HTML.replace(
            """
        <div class="job">
          <a class="job-title"
             href="/emplois-vacantes/analyste/5f1e0316-6225-4e57-b40c-cb605e046331">
            Analyste
          </a>
          <span class="place-of-work">Bern</span>
        </div>
""",
            "",
        )

        assert _prospective_probe_config(partial_html, self.PROSPECTIVE_URL) is None

    def test_prospective_rejects_a_listing_row_outside_the_url_contract(self):
        rejected_html = self.PROSPECTIVE_HTML.replace(
            "/emplois-vacantes/analyste/5f1e0316-6225-4e57-b40c-cb605e046331",
            "https://evil.example/jobs/5f1e0316-6225-4e57-b40c-cb605e046331",
        )

        assert _prospective_probe_config(rejected_html, self.PROSPECTIVE_URL) is None

    def test_prospective_rejects_cross_origin_asset_identity(self):
        spoofed_html = self.PROSPECTIVE_HTML.replace(
            "/careercenter/1000973/assets/css/company.css",
            "https://attacker.example/careercenter/1000973/assets/css/company.css",
        )

        assert _prospective_probe_config(spoofed_html, self.PROSPECTIVE_URL) is None

    def test_prospective_accepts_canonical_provider_asset_identity(self):
        canonical_html = self.PROSPECTIVE_HTML.replace(
            "/careercenter/1000973/assets/css/company.css",
            "https://ohws.prospective.ch/public/v1/careercenter/1000973/assets/css/company.css",
        )

        result = _prospective_probe_config(canonical_html, self.PROSPECTIVE_URL)

        assert result is not None
        assert result["prospective_board"] == "1000973"

    async def test_prospective_runtime_accepts_uppercase_uuid_hex(self):
        uppercase_html = self.PROSPECTIVE_HTML.replace(
            "5f1e0316-6225-4e57-b40c-cb605e046331",
            "5F1E0316-6225-4E57-B40C-CB605E046331",
        )
        config = _prospective_probe_config(uppercase_html, self.PROSPECTIVE_URL)
        assert config is not None

        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=uppercase_html)):
            jobs = await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

        assert isinstance(jobs, list)
        assert len(jobs) == 2
        assert {job.url for job in jobs} == {
            "https://jobs.example.com/offene-stellen/job/268ceacb-05c3-4a11-a8a0-80b078a3f4e4",
            "https://jobs.example.com/offene-stellen/job/5f1e0316-6225-4e57-b40c-cb605e046331",
        }

    @pytest.mark.parametrize("inventory", ["positive", "zero"])
    async def test_prospective_runtime_rejects_wrong_tenant_identity(self, inventory: str):
        config = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)
        assert config is not None
        html = self.PROSPECTIVE_HTML
        if inventory == "zero":
            html = """
            <html><head><link href="/careercenter/999999/assets/css/company.css"></head>
            <body class="career-center">
              <span class="jobs-total"><span class="total">0</span></span>
              <div id="jobs-list"></div>
            </body></html>
            """
        else:
            html = html.replace("careercenter/1000973/", "careercenter/999999/")

        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="medium does not match listing assets"),
        ):
            await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

    async def test_prospective_runtime_rejects_missing_positive_identity_proof(self):
        config = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)
        assert config is not None
        html = self.PROSPECTIVE_HTML.replace(
            '<link href="/careercenter/1000973/assets/css/company.css" rel="stylesheet">',
            "",
        )

        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)),
            pytest.raises(ValueError, match="provider identity proof is missing or ambiguous"),
        ):
            await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

    async def test_prospective_runtime_dedupes_localized_query_and_fragment_variants(self):
        config = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)
        assert config is not None
        duplicate_html = (
            self.PROSPECTIVE_HTML.replace(">2<", ">1<")
            .replace(
                "/offene-stellen/engineer/268ceacb-05c3-4a11-a8a0-80b078a3f4e4",
                "/offene-stellen/engineer/268CEACB-05C3-4A11-A8A0-80B078A3F4E4?lang=de#top",
            )
            .replace(
                "/emplois-vacantes/analyste/5f1e0316-6225-4e57-b40c-cb605e046331",
                "/emplois-vacantes/analyste/268ceacb-05c3-4a11-a8a0-80b078a3f4e4?lang=fr#details",
            )
        )

        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=duplicate_html)):
            jobs = await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

        assert isinstance(jobs, list)
        assert [job.url for job in jobs] == [
            "https://jobs.example.com/offene-stellen/job/268ceacb-05c3-4a11-a8a0-80b078a3f4e4"
        ]

    async def test_prospective_runtime_counts_unique_uuid_identities(self):
        config = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)
        assert config is not None
        duplicate_html = self.PROSPECTIVE_HTML.replace(
            "5f1e0316-6225-4e57-b40c-cb605e046331",
            "268ceacb-05c3-4a11-a8a0-80b078a3f4e4",
        )

        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=duplicate_html)),
            pytest.raises(ValueError, match="accepted 1 rows but the page advertised 2"),
        ):
            await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

    async def test_prospective_runtime_requires_canonical_path_for_positive_inventory(self):
        config = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)
        assert config is not None
        config.pop("prospective_canonical_path")

        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=self.PROSPECTIVE_HTML)),
            pytest.raises(ValueError, match="positive inventory requires"),
        ):
            await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

    async def test_prospective_runtime_accepts_authoritative_zero_with_identity(self):
        config = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)
        assert config is not None
        html = """
        <html><head><link href="/careercenter/1000973/assets/css/company.css"></head>
        <body class="career-center">
          <span class="jobs-total"><span class="total">0</span></span>
          <div id="jobs-list"></div>
        </body></html>
        """

        with patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=html)):
            jobs = await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

        assert jobs == []

    async def test_prospective_runtime_rejects_partial_advertised_inventory(self):
        config = _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)
        assert config is not None
        partial_html = self.PROSPECTIVE_HTML.replace(
            """
        <div class="job">
          <a class="job-title"
             href="/emplois-vacantes/analyste/5f1e0316-6225-4e57-b40c-cb605e046331">
            Analyste
          </a>
          <span class="place-of-work">Bern</span>
        </div>
""",
            "",
        )

        with (
            patch(_EMPTY_FETCH_PATCH, AsyncMock(return_value=partial_html)),
            pytest.raises(ValueError, match="accepted 1 rows but the page advertised 2"),
        ):
            await dom_discover(
                {"board_url": self.PROSPECTIVE_URL, "metadata": config},
                AsyncMock(),
            )

    async def test_prospective_can_handle_returns_provider_preset(self):
        with patch(
            "src.core.monitors.fetch_page_text",
            new=AsyncMock(return_value=self.PROSPECTIVE_HTML),
        ):
            result = await can_handle(self.PROSPECTIVE_URL, MagicMock())

        assert result == _prospective_probe_config(self.PROSPECTIVE_HTML, self.PROSPECTIVE_URL)

    def test_dualoo_portal_uses_scoped_static_preset(self):
        result = _dualoo_probe_config(self.DUALOO_HTML, self.DUALOO_URL)

        assert result is not None
        assert result["dualoo_portal"] == "fyuan4bk"
        assert result["urls"] == 2
        assert result["link_selector"] == "a.jobElement[href]"
        assert result["require_jsonld_jobposting"] is True
        matcher = re.compile(result["url_filter"], re.IGNORECASE)
        assert matcher.search(
            "https://jobs.dualoo.com/portal/fyuan4bk/"
            "ef8b03a4-9219-4c19-a351-d01c0e07cc4f/detail?lang=DE"
        )
        assert not matcher.search(
            "https://jobs.dualoo.com/portal/other/"
            "ef8b03a4-9219-4c19-a351-d01c0e07cc4f/detail?lang=DE"
        )
        assert auto_scraper_type("dom", result) == ("json-ld", None)

    def test_dualoo_empty_portal_keeps_provider_preset(self):
        html = '<link rel="stylesheet" href="/css/fyuan4bk"><div class="JobInfoBox"></div>'

        result = _dualoo_probe_config(html, self.DUALOO_URL)

        assert result is not None
        assert result["urls"] == 0

    def test_dualoo_explicit_default_port_matches_detail_urls(self):
        url = "https://jobs.dualoo.com:443/portal/fyuan4bk?lang=DE"

        result = _dualoo_probe_config(self.DUALOO_HTML, url)

        assert result is not None
        assert result["urls"] == 2
        assert result["url_filter"].startswith(r"^https://jobs\.dualoo\.com:443/portal/fyuan4bk/")

    async def test_dualoo_can_handle_returns_provider_preset(self):
        with patch(
            "src.core.monitors.fetch_page_text",
            new=AsyncMock(return_value=self.DUALOO_HTML),
        ):
            result = await can_handle(self.DUALOO_URL, MagicMock())

        assert result == _dualoo_probe_config(self.DUALOO_HTML, self.DUALOO_URL)

    def test_vagas_employer_board_uses_proxy_pagination_preset(self):
        result = _vagas_probe_config(
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidades"
        )
        assert result == {
            "vagas_tenant": "beiersdorf",
            "proxy": True,
            "url_filter": (
                r"(?i:^https://trabalheconosco\.vagas\.com\.br/beiersdorf/"
                r"oportunidade/[^/?#]+/\d+/?(?:[?#].*)?$)"
            ),
            "pagination": {"param_name": "pagina", "max_pages": 1_000},
        }
        assert auto_scraper_type("dom", result) == ("json-ld", {"proxy": True})

        matcher = re.compile(result["url_filter"])
        assert matcher.search(
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidade/role/123"
        )
        assert not matcher.search("https://evil.example/beiersdorf/oportunidade/role/123")
        assert not matcher.search(
            "https://trabalheconosco.vagas.com.br/other/oportunidade/role/123"
        )

    def test_vagas_tenant_home_paginates_the_canonical_complete_listing(self):
        result = _vagas_probe_config("https://trabalheconosco.vagas.com.br/bdobrazil")

        assert result is not None
        assert result["pagination"] == {
            "param_name": "pagina",
            "max_pages": 1_000,
            "url_template": (
                "https://trabalheconosco.vagas.com.br/bdobrazil/oportunidades?pagina={page}"
            ),
            "start": 0,
        }

    @pytest.mark.parametrize(
        "url",
        [
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidade/role/1",
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidades?pagina=1",
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidades#jobs",
            "https://www.vagas.com.br/beiersdorf/oportunidades",
            "http://trabalheconosco.vagas.com.br/beiersdorf/oportunidades",
            "https://user:secret@trabalheconosco.vagas.com.br/beiersdorf/oportunidades",
            "https://trabalheconosco.vagas.com.br:444/beiersdorf/oportunidades",
            "https://trabalheconosco.vagas.com.br:invalid/beiersdorf/oportunidades",
            "https://evil.example/beiersdorf/oportunidades",
        ],
    )
    def test_vagas_preset_rejects_non_listing_routes(self, url):
        assert _vagas_probe_config(url) is None

    async def test_vagas_probe_does_not_fetch_blocked_listing(self):
        client = MagicMock()
        result = await can_handle(
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidades",
            client,
        )
        assert result == _vagas_probe_config(
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidades"
        )
        client.get.assert_not_called()

    async def test_talentsoft_without_safran_facets_keeps_ordinary_pagination(self):
        html = """
        <html><body>
          <a class="ts-search-engine-form__rss-cta" href="/job/all-rss-feeds.aspx">RSS</a>
          <li class="ts-offer-list-item">
            <a href="/job/job-credit-risk-analyst_112267.aspx">Credit Risk Analyst</a>
          </li>
          <a href="/offre-de-emploi/emploi-wealth-manager_112582.aspx">Wealth Manager</a>
          <a href="https://evil.example/job/job-lookalike_999.aspx">External lookalike</a>
          <a href="/candidate/login.aspx">Sign in</a>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle(
                "https://jobs.example.com/offre-de-emploi/liste-toutes-offres.aspx",
                MagicMock(),
            )
        assert result is not None
        assert result["urls"] == 2
        assert result["pagination"] == {"param_name": "page", "max_pages": 1_000}
        assert re.search(
            result["url_filter"],
            "https://jobs.example.com/job/job-credit-risk-analyst_112267.aspx",
        )
        assert not re.search(
            result["url_filter"], "https://evil.example/job/job-lookalike_999.aspx"
        )

    async def test_talentsoft_with_counted_facets_enables_partitioning(self):
        html = """
        <html>
          <head><title>Search results(2 vacancies/1)</title></head>
          <body>
            <a class="ts-search-engine-form__rss-cta" href="/job/all-rss-feeds.aspx">RSS</a>
            <li class="ts-offer-list-item">
              <a href="/job/job-engineer_1.aspx">Engineer</a>
            </li>
            <ul class="facette-titre-niv1">
              <li><a href="?changefacet=1&amp;facet_Contract=10">Permanent (2)</a></li>
              <li><a href="?changefacet=1&amp;facet_JobFamily=20">Engineering (2)</a></li>
            </ul>
          </body>
        </html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle(
                "https://jobs.example.com/job/list-of-all-jobs.aspx?LCID=2057",
                MagicMock(),
            )

        assert result is not None
        assert result["pagination"] == {
            "param_name": "page",
            "max_pages": 1_000,
            "partition_selector": "ul.facette-titre-niv1 a[href*='facet_Contract=']",
            "partition_fallback_selector": ("ul.facette-titre-niv1 a[href*='facet_JobFamily=']"),
            "partition_count_regex": r"\((\d+)\s+(?:vacancies|offres)",
            "partition_result_limit": 1_000,
            "partition_validate_total": True,
            "partition_drop_params": ["changefacet"],
            "partition_stateless": True,
        }

    async def test_rexx_board_uses_detail_pattern_and_ignores_job_alert(self):
        html = """
        <html><body>
          <a href="https://talent.example.com/jobalert-eng.html">Job alert</a>
          <a href="/Senior-Impact-Manager-eng-j346.html">Role</a>
          <a href="https://attacker.example/Injected-eng-j999.html">Other host</a>
          <a href="https://www.rexx-systems.com/en/">Rexx Systems</a>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle("https://talent.example.com/job-offers.html", MagicMock())
        assert result == {
            "urls": 1,
            "url_filter": (
                r"^https://talent\.example\.com/(?:[^/?#]+/)*"
                r"(?:[^/?#]+-j\d+\.html|(?:job-offer|stellenangebot)"
                r"\.html\?yid=\d+)(?:[&#].*)?$"
            ),
            "url_transform": {"find": r"&sid=[^&#]*", "replace": ""},
        }

    async def test_rexx_empty_board_keeps_provider_preset(self):
        html = """
        <html><body>
          <p>No vacancies are currently available.</p>
          <a href="https://talent.example.com/jobalert-eng.html">Job alert</a>
          <a href="https://www.rexx-systems.com/en/">Rexx Systems</a>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle("https://talent.example.com/job-offers.html", MagicMock())
        assert result is not None
        assert result["urls"] == 0
        assert result["url_filter"].startswith(r"^https://talent\.example\.com/")

    async def test_rexx_portal7_localized_links_strip_expiring_session(self):
        html = """
        <html><head><meta name="generator" content="Rexx Recruitment - Portal7"></head>
        <body>
          <a href="/portal-vag/stellenangebot.html?yid=867&amp;sid=expired">Role</a>
          <a href="https://www.rexx-systems.com/en/">Rexx Systems</a>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle("https://ds6.rexx-server.com/portal-vag/eng", MagicMock())

        assert result is not None
        assert result["urls"] == 1
        assert result["url_transform"] == {"find": r"&sid=[^&#]*", "replace": ""}
        matcher = re.compile(result["url_filter"])
        assert matcher.search(
            "https://ds6.rexx-server.com/portal-vag/stellenangebot.html?yid=867&sid=expired"
        )

    async def test_talentlink_empty_board_returns_provider_preset(self):
        html = """
        <html><head><script>
          WCN.global_config.baseUrl = "https://example.tal.net/vx/candidate";
        </script></head><body>
          <a href="/candidate/jobboard/vacancy/1/adv/">Programmes</a>
          <a href="/candidate/jobboard/vacancy/2/adv/">Events</a>
          <p id="no_results_message">No active programmes.</p>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle(
                "https://example.tal.net/vx/lang-en-GB/brand-5/candidate/jobboard/vacancy/1/adv/",
                MagicMock(),
            )
        assert result == {
            "urls": 0,
            "url_filter": r"^https://example\.tal\.net/[^?#]*/opp/[^?#]+(?:[?#].*)?$",
        }

    async def test_talentlink_populated_board_counts_only_opportunities(self):
        html = """
        <html><head><script>
          WCN.global_config.baseUrl = "https://example.tal.net/vx/candidate";
        </script></head><body>
          <a href="/candidate/jobboard/vacancy/1/adv/">Programmes</a>
          <a href="/vx/xf-a1b2c3/candidate/so/pm/1/pl/1/opp/42-Analyst/en-GB">
            Analyst
          </a>
          <a href="https://attacker.example/opp/999-Fake/en-GB">Other host</a>
          <a href="/candidate/jobboard/talentbank/1">Register interest</a>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle(
                "https://example.tal.net/vx/candidate/jobboard/vacancy/1",
                MagicMock(),
            )
        assert result == {
            "urls": 1,
            "url_filter": r"^https://example\.tal\.net/[^?#]*/opp/[^?#]+(?:[?#].*)?$",
        }

    async def test_talentlink_preset_does_not_claim_non_board_pages(self):
        html = """
        <html><head><script>
          WCN.global_config.baseUrl = "https://example.tal.net/vx/candidate";
        </script></head><body>
          <a href="/candidate/jobboard/vacancy/1/adv/">Programmes</a>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle("https://example.tal.net/vx/candidate/login", MagicMock())
        assert result == {"urls": 1}

    async def test_talentlink_preset_rejects_partial_board_shell(self):
        html = """
        <html><head><script>
          WCN.global_config.baseUrl = "https://example.tal.net/vx/candidate";
        </script></head><body>
          <a href="/candidate/jobboard/vacancy/2/adv/">Events</a>
        </body></html>
        """
        with patch("src.core.monitors.fetch_page_text", new=AsyncMock(return_value=html)):
            result = await can_handle(
                "https://example.tal.net/vx/candidate/jobboard/vacancy/1/adv/",
                MagicMock(),
            )
        assert result == {"urls": 1}

    async def test_jposting_empty_board_returns_provider_preset(self):
        html = """
        <html><head><style>.jobname { font-weight: bold; }</style></head>
        <body><a href="#pagetop">Page top</a></body></html>
        """
        with patch(
            "src.core.monitors.fetch_page_text",
            new=AsyncMock(return_value=html),
        ):
            result = await can_handle(
                "https://js03.jposting.net/example/u/job.phtml",
                MagicMock(),
            )

        assert result == {
            "urls": 0,
            "url_filter": r"[?&]job_code=[^&#]+",
            "encoding": "euc_jp",
        }

    async def test_jposting_populated_board_counts_only_detail_links(self):
        html = """
        <html><head><style>.jobname { font-weight: bold; }</style></head><body>
          <a href="?job_code=13">Role</a>
          <a href="job.phtml?job_code=14#details">Another role</a>
          <a href="#pagetop">Page top</a>
        </body></html>
        """
        with patch(
            "src.core.monitors.fetch_page_text",
            new=AsyncMock(return_value=html),
        ):
            result = await can_handle(
                "https://js03.jposting.net/example/u/job.phtml",
                MagicMock(),
            )

        assert result is not None
        assert result["urls"] == 2

    async def test_kontact_board_returns_complete_browser_pagination_config(self):
        html = """
        <html><head>
          <meta name="Author" content="KontactIntelligence.com">
        </head><body>
          <a href="/Physician_Job/Details/Family-Medicine/123">View</a>
          <a href="?pg=2">2</a>
        </body></html>
        """
        with patch(
            "src.core.monitors.fetch_page_text",
            new=AsyncMock(return_value=html),
        ):
            result = await can_handle("https://example.com/Physician_Jobs", MagicMock())

        assert result == {
            "urls": 1,
            "url_filter": r"/Physician_Job/Details/",
            "pagination": {
                "param_name": "pg",
                "max_pages": 1_000,
            },
        }

    async def test_linkedin_jobs_use_public_guest_endpoint(self):
        html = _html_with_links(
            "https://www.linkedin.com/jobs/view/software-engineer-4434484597/",
            "https://se.linkedin.com/jobs/view/4434489306?trackingId=abc",
            "https://example.com/lightbringer-learn/learn/patents",
        )
        with patch(
            "src.core.monitors.fetch_page_text",
            new=AsyncMock(return_value=html),
        ):
            result = await can_handle("https://example.com/careers", MagicMock())

        assert result == {
            "urls": 2,
            "url_filter": r"linkedin\.com/jobs/view/",
            "url_transform": {
                "find": r".*(?:-|/)(\d+)(?:/?(?:\?.*)?)$",
                "replace": r"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/\1",
            },
        }


class TestDomDiscoverInitialFetch:
    async def test_partitioned_pagination_unions_every_talentsoft_facet(self):
        board_url = "https://jobs.example.com/job/list-of-all-jobs.aspx?LCID=2057"
        partition_one_link = (
            "https://jobs.example.com/job/list-of-all-jobs.aspx?changefacet=1&facet_JobFamily=100"
        )
        partition_two_link = (
            "https://jobs.example.com/job/list-of-all-jobs.aspx?changefacet=1&facet_JobFamily=200"
        )
        partition_one = (
            "https://jobs.example.com/job/list-of-all-jobs.aspx?LCID=2057&facet_JobFamily=100"
        )
        partition_two = (
            "https://jobs.example.com/job/list-of-all-jobs.aspx?LCID=2057&facet_JobFamily=200"
        )
        job_one = "https://jobs.example.com/job/job-engineer_1.aspx"
        job_two = "https://jobs.example.com/job/job-manager_2.aspx"
        job_three = "https://jobs.example.com/job/job-analyst_3.aspx"
        initial = f"""
        <ul class="facette-titre-niv1">
          <li><a href="{partition_one_link}">Engineering</a></li>
          <li><a href="{partition_two_link}">Management</a></li>
        </ul>
        <a href="{job_one}">capped unfiltered result</a>
        """
        pages = {
            board_url: initial,
            partition_one: _html_with_links(job_one),
            f"{partition_one}&page=2": _html_with_links(job_two),
            f"{partition_one}&page=3": _html_with_links(job_two),
            partition_two: _html_with_links(job_three),
            f"{partition_two}&page=2": _html_with_links(job_three),
        }
        metadata = {
            "url_filter": (
                r"^https://jobs\.example\.com/"
                r"(?:job/job|offre-de-emploi/emploi)-[^/?#]+_\d+\.aspx(?:[?#]|$)"
            ),
            "pagination": {
                "param_name": "page",
                "max_pages": 10,
                "partition_selector": ("ul.facette-titre-niv1 a[href*='facet_JobFamily=']"),
                "partition_drop_params": ["changefacet"],
                "partition_stateless": True,
            },
        }

        fetch = _make_fetch(pages)

        async def stateless_fetch(client, url, **kwargs):
            if url != board_url:
                assert kwargs.get("headers") == {"Cookie": ""}
            return await fetch(client, url, **kwargs)

        with patch(_FETCH_PATCH, new=stateless_fetch):
            result = await dom_discover(
                {"board_url": board_url, "metadata": metadata},
                MagicMock(),
            )

        assert result == {job_one, job_two, job_three}

    async def test_partitioned_pagination_bounds_parallel_facets(self):
        board_url = "https://jobs.example.com/job/list.aspx"
        partition_urls = [f"{board_url}?facet_JobFamily={number}" for number in range(6)]
        job_urls = [f"https://jobs.example.com/job/job-role_{number}.aspx" for number in range(6)]
        initial = (
            "<ul class='facette-titre-niv1'>"
            + "".join(f"<li><a href='{url}'>Family</a></li>" for url in partition_urls)
            + "</ul>"
        )
        active = 0
        max_active = 0

        async def fetch(_client, url, **_kwargs):
            nonlocal active, max_active
            if url == board_url:
                return initial
            for partition_url, job_url in zip(partition_urls, job_urls, strict=True):
                if url == partition_url:
                    active += 1
                    max_active = max(max_active, active)
                    await asyncio.sleep(0.01)
                    active -= 1
                    return _html_with_links(job_url)
                if url == f"{partition_url}&page=2":
                    return _html_with_links(job_url)
            return None

        metadata = {
            "url_filter": r"^https://jobs\.example\.com/job/job-.+_\d+\.aspx$",
            "pagination": {
                "param_name": "page",
                "max_pages": 10,
                "partition_selector": "ul.facette-titre-niv1 a[href*='facet_JobFamily=']",
            },
        }

        with patch(_FETCH_PATCH, new=fetch):
            result = await dom_discover(
                {"board_url": board_url, "metadata": metadata},
                MagicMock(),
            )

        assert result == set(job_urls)
        assert max_active == 4

    async def test_partitioned_pagination_splits_oversized_primary_facet(self):
        board_url = "https://jobs.example.com/job/list.aspx?LCID=2057"
        small_link = f"{board_url.split('?')[0]}?changefacet=1&facet_Contract=10"
        large_link = f"{board_url.split('?')[0]}?changefacet=1&facet_Contract=20"
        small = f"{board_url.split('?')[0]}?LCID=2057&facet_Contract=10"
        large = f"{board_url.split('?')[0]}?LCID=2057&facet_Contract=20"
        child_one = f"{large}&facet_JobFamily=100"
        child_two = f"{large}&facet_JobFamily=200"
        jobs = [f"https://jobs.example.com/job/job-role_{number}.aspx" for number in range(5)]

        def counted_page(count, *links, facets=""):
            return (
                f"<html><head><title>Search results({count} vacancies/1)</title></head>"
                f"<body>{facets}{''.join(f'<a href={url!r}>job</a>' for url in links)}</body>"
                "</html>"
            )

        initial = counted_page(
            5,
            jobs[0],
            facets=(
                "<ul class='facette-titre-niv1'>"
                f"<li><a href='{small_link}'>Fixed (2)</a></li>"
                f"<li><a href='{large_link}'>Permanent (3)</a></li>"
                "</ul>"
            ),
        )
        large_page = counted_page(
            3,
            jobs[2],
            facets=(
                "<ul class='facette-titre-niv1'>"
                "<li><a href='?changefacet=1&facet_JobFamily=100'>Family A (1)</a></li>"
                "<li><a href='?changefacet=1&facet_JobFamily=200'>Family B (2)</a></li>"
                "</ul>"
            ),
        )
        pages = {
            board_url: initial,
            small: counted_page(2, jobs[0], jobs[1]),
            f"{small}&page=2": counted_page(2, jobs[0], jobs[1]),
            large: large_page,
            child_one: counted_page(1, jobs[2]),
            f"{child_one}&page=2": counted_page(1, jobs[2]),
            child_two: counted_page(2, jobs[3], jobs[4]),
            f"{child_two}&page=2": counted_page(2, jobs[3], jobs[4]),
        }
        metadata = {
            "url_filter": r"^https://jobs\.example\.com/job/job-.+_\d+\.aspx$",
            "pagination": {
                "param_name": "page",
                "max_pages": 10,
                "partition_selector": "ul.facette-titre-niv1 a[href*='facet_Contract=']",
                "partition_fallback_selector": (
                    "ul.facette-titre-niv1 a[href*='facet_JobFamily=']"
                ),
                "partition_count_regex": r"\((\d+)\s+vacancies",
                "partition_result_limit": 2,
                "partition_validate_total": True,
                "partition_drop_params": ["changefacet"],
            },
        }

        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await dom_discover(
                {"board_url": board_url, "metadata": metadata},
                MagicMock(),
            )

        assert result == set(jobs)

    async def test_partitioned_pagination_caps_fallbacks_across_all_parents(self):
        board_url = "https://jobs.example.com/job/list.aspx?LCID=2057"
        base = board_url.split("?")[0]
        primary_urls = [f"{base}?LCID=2057&facet_Contract={number}" for number in (10, 20)]
        child_urls = [
            [f"{primary}&facet_JobFamily={family}" for family in (100, 200)]
            for primary in primary_urls
        ]
        jobs = [f"https://jobs.example.com/job/job-role_{number}.aspx" for number in range(4)]

        def counted_page(count, job_url, facets=""):
            return (
                f"<html><head><title>Search results({count} vacancies/1)</title></head>"
                f"<body>{facets}<a href='{job_url}'>job</a></body></html>"
            )

        initial_facets = (
            "<ul class='facette-titre-niv1'>"
            + "".join(
                f"<li><a href='?changefacet=1&facet_Contract={number}'>Contract ({2})</a></li>"
                for number in (10, 20)
            )
            + "</ul>"
        )
        child_facets = (
            "<ul class='facette-titre-niv1'>"
            + "".join(
                f"<li><a href='?changefacet=1&facet_JobFamily={number}'>Family (1)</a></li>"
                for number in (100, 200)
            )
            + "</ul>"
        )
        pages = {
            board_url: counted_page(4, jobs[0], initial_facets),
            primary_urls[0]: counted_page(2, jobs[0], child_facets),
            primary_urls[1]: counted_page(2, jobs[2], child_facets),
            child_urls[0][0]: counted_page(1, jobs[0]),
            child_urls[0][1]: counted_page(1, jobs[1]),
        }
        seen: list[str] = []
        fetch = _make_fetch(pages)

        async def recording_fetch(client, url, **kwargs):
            seen.append(url)
            return await fetch(client, url, **kwargs)

        metadata = {
            "url_filter": r"^https://jobs\.example\.com/job/job-.+_\d+\.aspx$",
            "pagination": {
                "param_name": "page",
                "max_pages": 10,
                "partition_selector": "ul.facette-titre-niv1 a[href*='facet_Contract=']",
                "partition_fallback_selector": (
                    "ul.facette-titre-niv1 a[href*='facet_JobFamily=']"
                ),
                "partition_count_regex": r"\((\d+)\s+vacancies",
                "partition_result_limit": 1,
                "partition_validate_total": True,
                "partition_drop_params": ["changefacet"],
            },
        }

        with (
            patch(_FETCH_PATCH, new=recording_fetch),
            patch("src.core.monitors.dom._MAX_PAGINATION_PARTITIONS", 3),
            pytest.raises(ValueError, match="global partition limit"),
        ):
            await dom_discover(
                {"board_url": board_url, "metadata": metadata},
                MagicMock(),
            )

        assert not set(child_urls[1]) & set(seen)

    async def test_partitioned_pagination_fails_closed_on_missing_partition(self):
        board_url = "https://jobs.example.com/job/list.aspx"
        partition = "https://jobs.example.com/job/list.aspx?facet_JobFamily=100"
        initial = (
            f"<ul class='facette-titre-niv1'><li><a href='{partition}'>Engineering</a></li></ul>"
        )
        metadata = {
            "url_filter": r"^https://jobs\.example\.com/job/job-.+_\d+\.aspx$",
            "pagination": {
                "param_name": "page",
                "max_pages": 10,
                "partition_selector": ("ul.facette-titre-niv1 a[href*='facet_JobFamily=']"),
            },
        }

        with (
            patch(_FETCH_PATCH, new=_make_fetch({board_url: initial, partition: None})),
            pytest.raises(PaginationFetchError, match="empty partition"),
        ):
            await dom_discover(
                {"board_url": board_url, "metadata": metadata},
                MagicMock(),
            )

    async def test_vagas_tenant_home_discovers_full_listing_not_only_featured_jobs(self):
        tenant_home = "https://trabalheconosco.vagas.com.br/bdobrazil"
        listing = f"{tenant_home}/oportunidades"
        job_1 = f"{tenant_home}/oportunidade/role-one/1001"
        job_2 = f"{tenant_home}/oportunidade/role-two/1002"
        job_3 = f"{tenant_home}/oportunidade/role-three/1003"
        pages = {
            tenant_home: _html_with_links(job_1),
            f"{listing}?pagina=1": _html_with_links(job_1, job_2),
            f"{listing}?pagina=2": _html_with_links(job_3),
            f"{listing}?pagina=3": _html_with_links(job_3),
        }
        config = _vagas_probe_config(tenant_home)
        assert config is not None

        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await dom_discover(
                {"board_url": tenant_home, "metadata": config},
                MagicMock(),
            )

        assert result == {job_1, job_2, job_3}

    async def test_direct_document_can_include_board_url(self):
        board_url = "https://example.com/jobs/current-opening.pdf"

        def handler(request):
            return httpx.Response(
                200,
                content=b"%PDF-1.7 direct job document",
                headers={"content-type": "application/pdf"},
                request=request,
            )

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {"include_board_url": True},
                },
                client,
            )

        assert result == {board_url}

    async def test_missing_direct_document_is_not_included(self):
        board_url = "https://example.com/jobs/removed-opening.pdf"

        def handler(request):
            return httpx.Response(404, text="Not found", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {"include_board_url": True},
                },
                client,
            )

        assert result == set()

    async def test_jsonld_verification_omits_stale_profile_links(self):
        board_url = "https://jobs.example.com/company"
        active_url = "https://jobs.example.com/profile/1-active"
        stale_url = "https://jobs.example.com/profile/2-stale"
        listing = _html_with_links(active_url, stale_url)
        active = """
        <script type="application/ld+json">
        {"@context":"https://schema.org","@type":"JobPosting","title":"Engineer"}
        </script>
        """

        def handler(request):
            pages = {board_url: listing, active_url: active, stale_url: "<html>Closed</html>"}
            return httpx.Response(200, text=pages[str(request.url)], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {
                        "link_selector": "a[href*='/profile/']",
                        "require_jsonld_jobposting": True,
                    },
                },
                client,
            )

        assert result == {active_url}

    async def test_detail_selector_excludes_ats_mirrors_and_keeps_email_roles(self):
        board_url = "https://www.example.com/vacancies"
        mirrored_url = "https://www.example.com/fundraising-manager"
        email_url = "https://www.example.com/research-consultant"
        listing = """
        <main>
          <a class="vacancy" href="/fundraising-manager">Fundraising Manager</a>
          <a class="vacancy" href="/research-consultant">Research Consultant</a>
        </main>
        """
        pages = {
            board_url: listing,
            mirrored_url: (
                "<h1>Fundraising Manager</h1>"
                '<a href="https://apply.workable.com/example/j/ABC123/">Apply</a>'
            ),
            email_url: "<h1>Research Consultant</h1><p>Apply by email.</p>",
        }

        def handler(request):
            return httpx.Response(200, text=pages[str(request.url)], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {
                        "link_selector": "a.vacancy",
                        "exclude_detail_selector": ('a[href*="apply.workable.com"][href*="/j/"]'),
                    },
                },
                client,
            )

        assert result == {email_url}

    async def test_detail_selector_can_exclude_every_mirrored_role(self):
        board_url = "https://www.example.com/vacancies"
        mirrored_url = "https://www.example.com/fundraising-manager"
        pages = {
            board_url: '<a class="vacancy" href="/fundraising-manager">Role</a>',
            mirrored_url: '<a class="apply" href="https://ats.example/job/1">Apply</a>',
        }

        def handler(request):
            return httpx.Response(200, text=pages[str(request.url)], request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {
                        "link_selector": "a.vacancy",
                        "exclude_detail_selector": "a.apply",
                    },
                },
                client,
            )

        assert result == set()

    async def test_detail_selector_fails_closed_on_detail_fetch_error(self, monkeypatch):
        board_url = "https://www.example.com/vacancies"
        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", AsyncMock())

        def handler(request):
            if str(request.url) == board_url:
                return httpx.Response(
                    200,
                    text='<a class="vacancy" href="/research-consultant">Role</a>',
                    request=request,
                )
            return httpx.Response(503, text="Unavailable", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await dom_discover(
                    {
                        "board_url": board_url,
                        "metadata": {
                            "link_selector": "a.vacancy",
                            "exclude_detail_selector": "a.apply",
                        },
                    },
                    client,
                )

    @pytest.mark.parametrize("status_code", [400, 422])
    async def test_detail_selector_fails_closed_on_terminal_detail_error(self, status_code):
        board_url = "https://www.example.com/vacancies"

        def handler(request):
            if str(request.url) == board_url:
                return httpx.Response(
                    200,
                    text='<a class="vacancy" href="/research-consultant">Role</a>',
                    request=request,
                )
            return httpx.Response(status_code, text="Invalid request", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await dom_discover(
                    {
                        "board_url": board_url,
                        "metadata": {
                            "link_selector": "a.vacancy",
                            "exclude_detail_selector": "a.apply",
                        },
                    },
                    client,
                )

    @pytest.mark.parametrize("status_code", [404, 410])
    async def test_detail_selector_omits_retired_detail(self, status_code):
        board_url = "https://www.example.com/vacancies"

        def handler(request):
            if str(request.url) == board_url:
                return httpx.Response(
                    200,
                    text='<a class="vacancy" href="/retired-role">Role</a>',
                    request=request,
                )
            return httpx.Response(status_code, text="Gone", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {
                        "link_selector": "a.vacancy",
                        "exclude_detail_selector": "a.apply",
                    },
                },
                client,
            )

        assert result == set()

    @pytest.mark.parametrize("selector", ["", "a[", "a\x00b", "a" * 257, 123])
    async def test_rejects_invalid_detail_exclusion_selector(self, selector):
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="exclude_detail_selector"):
                await dom_discover(
                    {
                        "board_url": "https://example.com/vacancies",
                        "metadata": {"exclude_detail_selector": selector},
                    },
                    client,
                )

    async def test_jsonld_verification_reads_jobposting_after_one_megabyte(self):
        board_url = "https://jobs.example.com/company"
        active_url = "https://jobs.example.com/profile/1-active"
        jobposting = (
            '<script type="application/ld+json">'
            '{"@context":"https://schema.org","@type":"JobPosting","title":"Engineer"}'
            "</script>"
        )
        pages = {
            board_url: _html_with_links(active_url),
            active_url: f"<html><body>{'x' * 1_000_100}{jobposting}</body></html>",
        }

        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await dom_discover(
                {
                    "board_url": board_url,
                    "metadata": {
                        "link_selector": "a[href*='/profile/']",
                        "require_jsonld_jobposting": True,
                    },
                },
                MagicMock(),
            )

        assert result == {active_url}

    async def test_jsonld_verification_cancels_siblings_after_fetch_error(self):
        urls = {f"https://jobs.example.com/profile/{index:02d}" for index in range(20)}
        failure_url = min(urls)
        active_urls: set[str] = set()
        concurrency_reached = asyncio.Event()
        blocker = asyncio.Event()

        async def failing_fetch(_client, url, **_kwargs):
            active_urls.add(url)
            try:
                if len(active_urls) == 8:
                    concurrency_reached.set()
                await concurrency_reached.wait()
                if url == failure_url:
                    raise PaginationFetchError(url, 1, last_error="detail fetch failed")
                await blocker.wait()
            finally:
                active_urls.discard(url)

        with (
            patch(_FETCH_PATCH, new=failing_fetch),
            pytest.raises(PaginationFetchError, match="detail fetch failed"),
        ):
            await _filter_jsonld_job_urls(urls, MagicMock())

        assert active_urls == set()

    async def test_jsonld_verification_fails_closed_on_detail_fetch_error(self, monkeypatch):
        board_url = "https://jobs.example.com/company"
        detail_url = "https://jobs.example.com/profile/1-active"
        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", AsyncMock())

        def handler(request):
            if str(request.url) == board_url:
                return httpx.Response(200, text=_html_with_links(detail_url), request=request)
            return httpx.Response(503, text="Unavailable", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError):
                await dom_discover(
                    {
                        "board_url": board_url,
                        "metadata": {
                            "link_selector": "a[href*='/profile/']",
                            "require_jsonld_jobposting": True,
                        },
                    },
                    client,
                )

    @pytest.mark.parametrize("selector", ["", "   ", "a[", "a\x00b", "a" * 257, 123])
    async def test_rejects_invalid_or_unbounded_link_selector(self, selector):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html></html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="link_selector"):
                await dom_discover(
                    {
                        "board_url": "https://example.com/careers",
                        "metadata": {"link_selector": selector},
                    },
                    client,
                )

    async def test_fragment_only_self_link_is_not_a_job(self):
        page = _html_with_links("#pagetop")

        def handler(request):
            return httpx.Response(200, text=page, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {"board_url": "https://example.com/job.phtml", "metadata": {}},
                client,
            )

        assert result == set()

    async def test_static_request_verification_challenge_raises(self):
        """A 200 verification shell must fail instead of returning zero jobs."""

        challenge = (
            "<html><head><title>Verifying...</title></head>"
            "<body>Please wait while your request is being verified...</body></html>"
        )

        def handler(request):
            return httpx.Response(200, text=challenge, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(BotChallengeError, match="proxy transport"):
                await dom_discover(
                    {
                        "board_url": "https://blocked.example/careers",
                        "metadata": {"url_filter": "/job/"},
                    },
                    client,
                )

    async def test_siteground_challenge_raises_instead_of_successful_empty(self, monkeypatch):
        """A SiteGround HTTP-202 captcha shell is not an empty board."""

        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", AsyncMock())

        challenge = (
            '<html><head><meta http-equiv="refresh" '
            'content="0;/.well-known/sgcaptcha/?r=%2Fcareer%2F"></head></html>'
        )

        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(202, text=challenge)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await dom_discover(
                    {
                        "board_url": "https://blocked.example/careers",
                        "metadata": {"url_filter": "/job/"},
                    },
                    client,
                )

        assert attempts == 3
        assert exc_info.value.last_status == 202

    async def test_rendered_siteground_challenge_raises(self, monkeypatch):
        """The Playwright redirect target must also fail the monitor cycle."""

        page = MagicMock()
        page.url = "https://blocked.example/.well-known/captcha/?r=%2Fcareers"
        page.content = AsyncMock(return_value="<html><body>Checking your browser</body></html>")
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        with pytest.raises(BotChallengeError, match="proxy transport"):
            await _extract_links_rendered(
                page,
                {"_board_url": "https://blocked.example/careers"},
            )

    async def test_rendered_siteground_meta_refresh_raises(self, monkeypatch):
        """Challenge markup must be caught before its redirect settles."""

        page = MagicMock()
        page.url = "https://blocked.example/careers"
        page.content = AsyncMock(
            return_value=(
                '<html><head><meta http-equiv="refresh" '
                'content="0;/.well-known/sgcaptcha/?r=%2Fcareers"></head></html>'
            )
        )
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        with pytest.raises(BotChallengeError, match="proxy transport"):
            await _extract_links_rendered(
                page,
                {"_board_url": "https://blocked.example/careers"},
            )

    async def test_rendered_cloudflare_challenge_raises(self, monkeypatch):
        """Cloudflare's interstitial must not look like an empty board."""

        page = MagicMock()
        page.url = "https://blocked.example/careers"
        page.content = AsyncMock(
            return_value=(
                "<html><head><title>Just a moment...</title></head>"
                "<body><script src='/cdn-cgi/challenge-platform/scripts/jsd/main.js'>"
                "</script><p>Enable JavaScript and cookies to continue</p></body></html>"
            )
        )
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        with pytest.raises(BotChallengeError, match="proxy transport"):
            await _extract_links_rendered(
                page,
                {"_board_url": "https://blocked.example/careers"},
            )

    async def test_rendered_request_verification_challenge_raises(self, monkeypatch):
        """HTTP-200 verification interstitials must not become empty boards."""

        page = MagicMock()
        page.url = "https://blocked.example/careers"
        page.content = AsyncMock(
            return_value=(
                "<html><head><title>Verifying...</title></head>"
                "<body>Please wait while your request is being verified...</body></html>"
            )
        )
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        with pytest.raises(BotChallengeError, match="proxy transport"):
            await _extract_links_rendered(
                page,
                {"_board_url": "https://blocked.example/careers"},
            )

    async def test_rendered_incapsula_interstitial_raises(self, monkeypatch):
        """Imperva's HTTP-200 iframe shell must not look like an empty board."""

        page = MagicMock()
        page.url = "https://blocked.example/careers"
        page.content = AsyncMock(
            return_value=(
                '<html><body><iframe id="main-iframe" '
                'src="/_Incapsula_Resource?CWUDNSAI=23&incident_id=6110">'
                "</iframe></body></html>"
            )
        )
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        with pytest.raises(BotChallengeError, match="proxy transport"):
            await _extract_links_rendered(
                page,
                {"_board_url": "https://blocked.example/careers"},
            )

    async def test_rendered_cloudflare_block_page_raises(self, monkeypatch):
        """Cloudflare's permanent block page must also fail the cycle."""

        page = MagicMock()
        page.url = "https://blocked.example/careers"
        page.content = AsyncMock(
            return_value=(
                "<html><head><title>Attention Required! | Cloudflare</title></head>"
                "<body><script src='/cdn-cgi/challenge-platform/h/g/orchestrate/'>"
                "</script><h1>Sorry, you have been blocked</h1></body></html>"
            )
        )
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        with pytest.raises(BotChallengeError, match="proxy transport"):
            await _extract_links_rendered(
                page,
                {"_board_url": "https://blocked.example/careers"},
            )

    async def test_rendered_captcha_library_is_not_a_challenge(self, monkeypatch):
        """A normal page may load BotDetect without being a challenge page."""

        page = MagicMock()
        page.url = "https://example.com/careers"
        page.content = AsyncMock(
            return_value='<html><script src="/BotDetectCaptcha.ashx"></script></html>'
        )
        page.evaluate = AsyncMock(return_value=["https://example.com/job/123"])
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        urls = await _extract_links_rendered(
            page,
            {"_board_url": "https://example.com/careers"},
        )

        assert urls == {"https://example.com/job/123"}

    async def test_rendered_link_selector_scopes_and_trusts_links(self, monkeypatch):
        page = MagicMock()
        page.url = "https://example.com/carrieres"
        page.content = AsyncMock(return_value="<html></html>")
        page.evaluate = AsyncMock(return_value=["https://example.com/fr/emploi/analyste"])
        monkeypatch.setattr("src.core.monitors.dom.navigate", AsyncMock())
        monkeypatch.setattr("src.core.monitors.dom.run_actions", AsyncMock())

        urls = await _extract_links_rendered(
            page,
            {
                "_board_url": "https://example.com/carrieres",
                "link_selector": "a.job-card",
            },
        )

        assert urls == {"https://example.com/fr/emploi/analyste"}
        assert page.evaluate.await_args.args[1] == "a.job-card"

    async def test_initial_403_raises_instead_of_successful_empty(self, monkeypatch):
        """A blocked listing page must fail the monitor cycle.

        Returning an empty set records a healthy empty crawl and masks
        missing jobs, which is the mcdonalds-ph failure mode from #4945.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", AsyncMock())
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, text="Forbidden")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await dom_discover(
                    {
                        "board_url": "https://blocked.example/careers",
                        "metadata": {"url_filter": "/career/"},
                    },
                    client,
                )

        assert attempts == 3
        assert exc_info.value.last_status == 403

    async def test_initial_404_remains_empty(self):
        """A missing static page keeps the existing lenient empty-result path."""

        def handler(request):
            return httpx.Response(404, text="Not found")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await dom_discover(
                {
                    "board_url": "https://missing.example/careers",
                    "metadata": {"url_filter": "/career/"},
                },
                client,
            )

        assert result == set()


# ---------------------------------------------------------------------------
# _paginate_urls
# ---------------------------------------------------------------------------


class TestPaginateUrls:
    async def test_explicit_one_based_start_fetches_page_two_first(self):
        """``start`` names the already-fetched listing page."""
        initial = {"https://example.com/jobs/1"}
        pages: dict[str, str | None] = {
            "https://example.com/careers?page=2": _html_with_links("https://example.com/jobs/2"),
            "https://example.com/careers?page=3": _html_with_links("https://example.com/jobs/3"),
        }
        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "page", "start": 1, "max_pages": 3},
                initial,
                MagicMock(),
            )

        assert result == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }

    async def test_accumulates_urls(self):
        """Pages with different job links get merged."""
        initial = {"https://example.com/jobs/1"}
        pages = {
            "https://example.com/careers?p=2": _html_with_links("https://example.com/jobs/2"),
            "https://example.com/careers?p=3": _html_with_links("https://example.com/jobs/3"),
        }
        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 3},
                initial,
                MagicMock(),
            )
        assert result == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
            "https://example.com/jobs/3",
        }

    async def test_stops_on_no_new_links(self):
        """Same links on page 2 as initial -> stops."""
        initial = {"https://example.com/jobs/1"}
        pages = {
            "https://example.com/careers?p=2": _html_with_links(
                "https://example.com/jobs/1"  # duplicate
            ),
        }
        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 10},
                initial,
                MagicMock(),
            )
        assert result == {"https://example.com/jobs/1"}

    async def test_bot_challenge_mid_pagination_raises(self):
        """A challenge on page 2 must not silently truncate the board."""

        challenge = (
            "<html><head><title>Attention Required! | Cloudflare</title></head>"
            "<body><script src='/cdn-cgi/challenge-platform/h/g/orchestrate/'>"
            "</script><h1>Sorry, you have been blocked</h1></body></html>"
        )
        pages = {"https://example.com/careers?p=2": challenge}

        with (
            patch(_FETCH_PATCH, new=_make_fetch(pages)),
            pytest.raises(BotChallengeError, match="proxy transport"),
        ):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 3},
                {"https://example.com/jobs/1"},
                MagicMock(),
            )

    async def test_stops_when_tracking_variants_transform_to_the_same_job(self):
        """Transformation-aware identity prevents duplicate pages and 429s."""
        initial = {
            "https://www.linkedin.com/jobs/view/example-role-4436727677?position=1&pageNum=0"
        }
        fetched_urls = []

        async def tracking_fetch(client, url, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(
                "https://www.linkedin.com/jobs/view/example-role-4436727677?position=1&pageNum=1"
            )

        with patch(_FETCH_PATCH, new=tracking_fetch):
            result = await _paginate_urls(
                "https://www.linkedin.com/jobs-guest/jobs/api/search?start=0",
                {"param_name": "start", "start": 0, "increment": 25, "max_pages": 20},
                initial,
                MagicMock(),
                url_matcher=re.compile(r"linkedin\.com/jobs/view/"),
                url_transform={
                    "find": r".*-(\d+)(?:\?.*)?$",
                    "replace": r"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/\1",
                },
            )

        assert len(fetched_urls) == 1
        assert result == initial

    async def test_keeps_only_new_identity_representatives(self):
        """New pages must not re-add duplicate raw URL variants."""
        pages = {
            "https://example.com/careers?page=2": _html_with_links(
                "https://example.com/job/1?apply=1",
                "https://example.com/job/2",
                "https://example.com/job/2?apply=1",
            ),
            "https://example.com/careers?page=3": _html_with_links(
                "https://example.com/job/2?apply=1",
            ),
        }

        with patch(_FETCH_PATCH, new=_make_fetch(pages)):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "page", "max_pages": 5},
                {"https://example.com/job/1"},
                MagicMock(),
                url_matcher=re.compile(r"/job/"),
                url_transform={"find": r"\?apply=1$", "replace": ""},
            )

        assert result == {
            "https://example.com/job/1",
            "https://example.com/job/2",
        }

    async def test_stops_on_legitimate_end(self):
        """``fetch_with_retry`` returning ``None`` (404/410, empty body)
        stops pagination cleanly — pagination has reached its natural end.
        """
        initial = {"https://example.com/jobs/1"}
        with patch(_FETCH_PATCH, new=_make_fetch({})):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 5},
                initial,
                MagicMock(),
            )
        assert result == {"https://example.com/jobs/1"}

    async def test_propagates_persistent_fetch_error(self):
        """``fetch_with_retry`` raising ``PaginationFetchError`` after
        retries propagates out of ``_paginate_urls`` instead of being
        treated as silent end-of-pagination — the fix for the 2026-04-26
        NHS spike (#2722). The exception lands in
        ``_process_one_board_streaming``'s generic ``except Exception``
        which records the run as a failure rather than a partial
        success, so ``_MARK_GONE_BY_TIMESTAMP`` does not run.
        """
        from src.shared.http_retry import PaginationFetchError

        async def transient_fail(client, url, **kwargs):
            raise PaginationFetchError(url, attempts=3, last_status=503)

        initial = {"https://example.com/jobs/1"}
        with patch(_FETCH_PATCH, new=transient_fail):
            try:
                await _paginate_urls(
                    "https://example.com/careers",
                    {"param_name": "p", "max_pages": 5},
                    initial,
                    MagicMock(),
                )
            except PaginationFetchError as exc:
                assert exc.last_status == 503
            else:
                raise AssertionError("expected PaginationFetchError to propagate")

    async def test_transient_403_opt_in_fails_closed(self, monkeypatch):
        """A WAF block after page one must not accept a partial inventory."""
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.shared.http_retry.asyncio.sleep", AsyncMock())
        attempts = 0

        def handler(request):
            nonlocal attempts
            attempts += 1
            return httpx.Response(403, text="Access denied", request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with pytest.raises(PaginationFetchError) as exc_info:
                await _paginate_urls(
                    "https://blocked.example/careers",
                    {
                        "param_name": "page",
                        "max_pages": 5,
                        "transient_403": True,
                    },
                    {"https://blocked.example/job/1"},
                    client,
                )

        assert attempts == 3
        assert exc_info.value.last_status == 403

    async def test_transient_403_must_be_boolean(self):
        with pytest.raises(ValueError, match="transient_403 must be a boolean"):
            await _paginate_urls(
                "https://example.com/careers",
                {
                    "param_name": "page",
                    "max_pages": 5,
                    "transient_403": "true",
                },
                {"https://example.com/job/1"},
                MagicMock(),
            )

    async def test_browser_path_propagates_persistent_fetch_error(self, monkeypatch):
        """Same contract as the static path, but for ``pagination.browser=true``
        (#2737). A persistent Playwright-side failure must raise rather than
        truncate — the lenovo-careers board's failure mode.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        # First page succeeds with no new links so we get past the
        # ``no_new_urls`` short-circuit only on a real failure path —
        # here every page returns 503 so the very first paginated
        # fetch raises.
        page.evaluate = AsyncMock(return_value={"status": 503, "text": ""})

        initial = {"https://example.com/jobs/1"}
        try:
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 5, "browser": True},
                initial,
                MagicMock(),
                page=page,
            )
        except PaginationFetchError as exc:
            assert exc.last_status == 503
        else:
            raise AssertionError("expected PaginationFetchError to propagate")

    async def test_propagates_persistent_empty_200(self):
        """Empty-200 mid-pagination on the static httpx path now raises
        rather than silently breaking the loop (#2739). Without the
        fix, ``""`` falls through to ``if not html: break`` and the
        un-fetched tail is tombstoned by ``_MARK_GONE_BY_TIMESTAMP``.
        """
        from src.shared.http_retry import PaginationFetchError

        async def empty_200(client, url, **kwargs):
            raise PaginationFetchError(url, attempts=3, last_status=200)

        initial = {"https://example.com/jobs/1"}
        with patch(_FETCH_PATCH, new=empty_200):
            try:
                await _paginate_urls(
                    "https://example.com/careers",
                    {"param_name": "p", "max_pages": 5},
                    initial,
                    MagicMock(),
                )
            except PaginationFetchError as exc:
                assert exc.last_status == 200
                assert exc.last_error is None
            else:
                raise AssertionError("expected PaginationFetchError to propagate")

    async def test_browser_path_recovers_on_transient(self, monkeypatch):
        """Browser path retries through a single 503 and continues paginating."""
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 503, "text": ""},
                {"status": 200, "text": _html_with_links("https://example.com/jobs/2")},
                # Second pagination loop iteration: page=3 returns 404
                # (legitimate end of pagination).
                {"status": 404, "text": ""},
            ]
        )

        initial = {"https://example.com/jobs/1"}
        result = await _paginate_urls(
            "https://example.com/careers",
            {"param_name": "p", "max_pages": 5, "browser": True},
            initial,
            MagicMock(),
            page=page,
        )
        assert result == {
            "https://example.com/jobs/1",
            "https://example.com/jobs/2",
        }

    async def test_respects_max_pages(self):
        """Only fetches up to max_pages."""
        initial = {"https://example.com/jobs/1"}
        call_count = 0
        url_map = {}
        for i in range(2, 20):
            url_map[f"https://example.com/careers?p={i}"] = _html_with_links(
                f"https://example.com/jobs/{i}"
            )

        async def counting_fetch(client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return url_map.get(url)

        with patch(_FETCH_PATCH, new=counting_fetch):
            result = await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 4},
                initial,
                MagicMock(),
            )
        # max_pages=4 means pages 2, 3, 4 fetched (page 1 is initial)
        assert call_count == 3
        assert len(result) == 4

    async def test_system_cap(self, monkeypatch):
        """max_pages is capped at _MAX_PAGINATION_PAGES."""
        monkeypatch.setattr("src.core.monitors.dom._MAX_PAGINATION_PAGES", 7)
        initial = set()
        call_count = 0

        async def counting_fetch(client, url, **kwargs):
            nonlocal call_count
            call_count += 1
            return _html_with_links(f"https://example.com/jobs/{call_count}")

        with patch(_FETCH_PATCH, new=counting_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "p", "max_pages": 999},
                initial,
                MagicMock(),
            )
        # Patched cap is 7, so pages 2..7 are fetched.
        assert call_count == 6

    async def test_start_and_increment(self):
        """Custom start and increment produce correct URL params."""
        initial = {"https://example.com/jobs/1"}
        fetched_urls = []

        async def tracking_fetch(client, url, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(f"https://example.com/jobs/{len(fetched_urls) + 1}")

        with patch(_FETCH_PATCH, new=tracking_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "offset", "start": 0, "increment": 20, "max_pages": 3},
                initial,
                MagicMock(),
            )
        # start=0, increment=20 -> first fetch at offset=20, second at offset=40
        assert "offset=20" in fetched_urls[0]
        assert "offset=40" in fetched_urls[1]

    async def test_start_value_alias(self):
        """``start_value`` is accepted as an alias for ``start``."""
        initial = {"https://example.com/jobs/1"}
        fetched_urls = []

        async def tracking_fetch(client, url, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(f"https://example.com/jobs/{len(fetched_urls) + 1}")

        with patch(_FETCH_PATCH, new=tracking_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"param_name": "offset", "start_value": 0, "increment": 20, "max_pages": 3},
                initial,
                MagicMock(),
            )
        # start_value=0, increment=20 -> first fetch at offset=20, second at offset=40
        assert "offset=20" in fetched_urls[0]
        assert "offset=40" in fetched_urls[1]

    async def test_url_template(self):
        """``url_template`` with ``{page}`` produces path-based pagination URLs."""
        initial = {"https://example.com/jobs/1"}
        fetched_urls = []

        async def tracking_fetch(client, url, **kwargs):
            fetched_urls.append(url)
            return _html_with_links(f"https://example.com/jobs/{len(fetched_urls) + 1}")

        with patch(_FETCH_PATCH, new=tracking_fetch):
            await _paginate_urls(
                "https://example.com/careers",
                {"url_template": "https://example.com/careers/0-0-2-0-{page}", "max_pages": 4},
                initial,
                MagicMock(),
            )
        assert fetched_urls[0] == "https://example.com/careers/0-0-2-0-2"
        assert fetched_urls[1] == "https://example.com/careers/0-0-2-0-3"
        assert fetched_urls[2] == "https://example.com/careers/0-0-2-0-4"


# ---------------------------------------------------------------------------
# _fetch_via_page
# ---------------------------------------------------------------------------


class TestFetchViaPage:
    """``_fetch_via_page`` mirrors ``fetch_with_retry``'s strict semantics
    on the Playwright path: 200 → text, 404/410 → None (legit end), other
    4xx → None (lenient stop), and 5xx / 408 / 425 / 429 / page.evaluate
    exceptions → retry then raise ``PaginationFetchError``. See #2737.
    """

    async def test_returns_html_on_200(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 200, "text": "<html>ok</html>"})
        result = await _fetch_via_page(page, "https://example.com/page2")
        assert result == "<html>ok</html>"
        page.evaluate.assert_awaited_once()

    async def test_returns_none_on_404(self):
        """404 / 410 are legitimate end-of-pagination — return None, no retry."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 404, "text": "not found"})
        result = await _fetch_via_page(page, "https://example.com/past-end")
        assert result is None
        assert page.evaluate.await_count == 1

    async def test_returns_none_on_410(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 410, "text": ""})
        result = await _fetch_via_page(page, "https://example.com/gone")
        assert result is None

    async def test_returns_none_on_non_retryable_4xx(self):
        """Non-retryable 4xx (403 etc.) is a lenient stop, parity with httpx path."""
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 403, "text": "forbidden"})
        result = await _fetch_via_page(page, "https://example.com/forbidden")
        assert result is None
        assert page.evaluate.await_count == 1

    async def test_transient_403_opt_in_retries_then_raises(self, monkeypatch):
        """Browser pagination can fail closed for explicitly WAF-gated boards."""
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 403, "text": "Access denied"})

        with pytest.raises(PaginationFetchError) as exc_info:
            await _fetch_via_page(
                page,
                "https://example.com/forbidden",
                transient_403=True,
            )

        assert page.evaluate.await_count == 2
        assert exc_info.value.last_status == 403

    async def test_cloudflare_403_raises_instead_of_stopping(self):
        """A WAF block is not a legitimate end-of-pagination."""

        page = MagicMock()
        page.evaluate = AsyncMock(
            return_value={
                "status": 403,
                "text": (
                    "<title>Attention Required! | Cloudflare</title>"
                    "<script src='/cdn-cgi/challenge-platform/h/g/orchestrate/'></script>"
                    "<h1>Sorry, you have been blocked</h1>"
                ),
            }
        )

        with pytest.raises(BotChallengeError, match="proxy transport"):
            await _fetch_via_page(page, "https://example.com/forbidden")

        assert page.evaluate.await_count == 1

    async def test_retries_on_503_then_succeeds(self, monkeypatch):
        """Transient 503 retries, then 200 returns text."""
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 503, "text": "down"},
                {"status": 200, "text": "<html>recovered</html>"},
            ]
        )
        result = await _fetch_via_page(page, "https://example.com/p2")
        assert result == "<html>recovered</html>"
        assert page.evaluate.await_count == 2

    async def test_retries_on_429_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 429, "text": ""},
                {"status": 200, "text": "ok"},
            ]
        )
        result = await _fetch_via_page(page, "https://example.com/p2")
        assert result == "ok"
        assert page.evaluate.await_count == 2

    async def test_raises_after_persistent_5xx(self, monkeypatch):
        """Persistent 5xx exhausts retries -> PaginationFetchError."""
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 503, "text": ""})
        try:
            await _fetch_via_page(
                page,
                "https://example.com/flaky",
                retries=3,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            assert exc.url == "https://example.com/flaky"
            assert exc.attempts == 3
            assert exc.last_status == 503
            assert page.evaluate.await_count == 3
        else:
            raise AssertionError("expected PaginationFetchError")

    async def test_raises_after_persistent_evaluate_exception(self, monkeypatch):
        """Playwright ``page.evaluate`` raising (timeout, navigation,
        page closed) is treated as transient — retry then raise.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(side_effect=TimeoutError("evaluate timed out"))
        try:
            await _fetch_via_page(
                page,
                "https://example.com/p2",
                retries=2,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            assert exc.last_error == "TimeoutError"
            assert exc.last_status is None
            assert page.evaluate.await_count == 2
        else:
            raise AssertionError("expected PaginationFetchError")

    async def test_recovers_from_evaluate_exception(self, monkeypatch):
        """Single transient evaluate exception then success — recovery
        without raising.
        """
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                Exception("transient crash"),
                {"status": 200, "text": "ok"},
            ]
        )
        result = await _fetch_via_page(page, "https://example.com/p2")
        assert result == "ok"
        assert page.evaluate.await_count == 2

    async def test_truncates_to_max_chars(self):
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 200, "text": "x" * 1_000_000})
        result = await _fetch_via_page(page, "https://example.com")
        # Default cap is _BROWSER_FETCH_MAX_CHARS = 500_000.
        assert len(result) == 500_000
        assert set(result) == {"x"}

    async def test_recovers_from_empty_200(self, monkeypatch):
        """Single empty-200 on the browser path retries and recovers
        (#2739) — symmetric with the static httpx path. The bug shape
        otherwise: ``""`` is falsy, ``_paginate_urls``'s ``if not html:
        break`` treats it as end-of-pagination, the un-fetched tail is
        tombstoned by ``_MARK_GONE_BY_TIMESTAMP``.
        """
        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(
            side_effect=[
                {"status": 200, "text": ""},
                {"status": 200, "text": "<html>recovered</html>"},
            ]
        )

        result = await _fetch_via_page(page, "https://example.com/p2")

        assert result == "<html>recovered</html>"
        assert page.evaluate.await_count == 2

    async def test_raises_after_persistent_empty_200(self, monkeypatch):
        """Persistent empty-200 on the browser path exhausts retries
        and raises ``PaginationFetchError`` with ``last_status=200``
        (#2739) — same operator-facing signal as the static path.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value={"status": 200, "text": ""})

        try:
            await _fetch_via_page(
                page,
                "https://example.com/empty",
                retries=2,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            assert exc.url == "https://example.com/empty"
            assert exc.attempts == 2
            assert exc.last_status == 200
            assert exc.last_error is None
            assert page.evaluate.await_count == 2
        else:
            raise AssertionError("expected PaginationFetchError")

    async def test_unexpected_result_shape_retries_then_raises(self, monkeypatch):
        """A malformed ``page.evaluate`` return value (e.g. a string from
        an injected content script substituting our async function)
        falls through to the ``except Exception`` branch via natural
        attribute access — same retry-then-raise contract as a
        ``page.evaluate`` raise. Pinning the contract here so the
        absence of a defensive shape-check is intentional.
        """
        from src.shared.http_retry import PaginationFetchError

        monkeypatch.setattr("src.core.monitors.dom.asyncio.sleep", AsyncMock())
        page = MagicMock()
        page.evaluate = AsyncMock(return_value="not a dict")
        try:
            await _fetch_via_page(
                page,
                "https://example.com",
                retries=2,
                base_delay=0.001,
            )
        except PaginationFetchError as exc:
            # ``"not a dict"["status"]`` raises ``TypeError``.
            assert exc.last_error == "TypeError"
            assert page.evaluate.await_count == 2
        else:
            raise AssertionError("expected PaginationFetchError")
