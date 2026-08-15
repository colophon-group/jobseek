"""Tests for DOM monitor pagination support."""

from __future__ import annotations

import re
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from src.core.monitors.dom import (
    BotChallengeError,
    _build_url_matcher,
    _extract_links_rendered,
    _extract_links_static,
    _fetch_via_page,
    _paginate_urls,
    _vagas_probe_config,
    can_handle,
    dom_discover,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# Patch target: ``_paginate_urls`` does ``from src.shared.http_retry import
# fetch_with_retry`` (#2722). Earlier patches at ``src.core.monitors.
# fetch_page_text`` no longer apply.
_FETCH_PATCH = "src.shared.http_retry.fetch_with_retry"


def _html_with_links(*urls: str) -> str:
    """Build minimal HTML with anchor tags for the given URLs."""
    links = "".join(f'<a href="{url}">link</a>' for url in urls)
    return f"<html><body>{links}</body></html>"


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
    def test_vagas_employer_board_uses_proxy_pagination_preset(self):
        assert _vagas_probe_config(
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidades"
        ) == {
            "proxy": True,
            "url_filter": (
                r"^https://trabalheconosco\.vagas\.com\.br/[^/?#]+/"
                r"oportunidade/[^?#]+/\d+(?:[?#].*)?$"
            ),
            "pagination": {
                "param_name": "pagina",
                "max_pages": 1_000,
            },
        }

    @pytest.mark.parametrize(
        "url",
        [
            "https://trabalheconosco.vagas.com.br/beiersdorf",
            "https://trabalheconosco.vagas.com.br/beiersdorf/oportunidade/role/1",
            "https://www.vagas.com.br/beiersdorf/oportunidades",
            "http://trabalheconosco.vagas.com.br/beiersdorf/oportunidades",
            "https://trabalheconosco.vagas.com.br:444/beiersdorf/oportunidades",
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
