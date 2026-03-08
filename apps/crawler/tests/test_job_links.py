"""Tests for workspace.job_links pattern inference."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from src.workspace.job_links import (
    analyze_job_links,
    fetch_page_for_job_link_analysis,
)


def test_infers_pattern_for_repeated_job_paths():
    html = """
    <html><body>
      <a href="/jobs/backend-engineer-123">Backend</a>
      <a href="/jobs/frontend-engineer-456">Frontend</a>
      <a href="/jobs/data-engineer-789">Data</a>
      <a href="/about">About</a>
    </body></html>
    """
    analysis = analyze_job_links("https://example.com/careers", html)

    assert analysis.pattern is not None
    assert analysis.pattern_source == "inferred"
    assert analysis.job_links_total >= 3
    assert analysis.matched_job_links >= 3


def test_provided_pattern_counts_matches_without_inference():
    html = """
    <html><body>
      <a href="https://example.com/jobs/1">Job 1</a>
      <a href="https://example.com/jobs/2">Job 2</a>
      <a href="https://example.com/about">About</a>
    </body></html>
    """
    pattern = r"^https?://example\.com/jobs/"
    analysis = analyze_job_links("https://example.com/careers", html, provided_pattern=pattern)

    assert analysis.pattern == pattern
    assert analysis.pattern_source == "provided"
    assert analysis.matched_job_links == 2
    assert analysis.matched_outgoing_links == 2


def test_inference_warns_when_too_few_job_links():
    html = """
    <html><body>
      <a href="/jobs/only-one-role">Only one role</a>
      <a href="/about">About</a>
    </body></html>
    """
    analysis = analyze_job_links("https://example.com/careers", html)

    assert analysis.pattern is None
    assert analysis.job_links_total <= 1
    assert analysis.warnings


def test_localized_career_listing_links_are_not_treated_as_job_links():
    html = """
    <html><body>
      <a href="/en-gb/careers/">EN careers</a>
      <a href="/fr-fr/careers/">FR careers</a>
      <a href="/careers/jobs/">All jobs</a>
      <a href="/careers/">Careers</a>
    </body></html>
    """
    analysis = analyze_job_links("https://www.example.com/careers/", html)

    assert analysis.job_links_total == 0
    assert analysis.pattern is None
    assert analysis.warnings


def test_greenhouse_sign_in_link_not_counted_as_job_link():
    html = """
    <html><body>
      <a href="https://my.greenhouse.io/users/sign_in?job_board=twitch">Sign in</a>
      <a href="https://job-boards.greenhouse.io/twitch/jobs/8204076002">Job 1</a>
      <a href="https://job-boards.greenhouse.io/twitch/jobs/8221777002">Job 2</a>
      <a href="https://job-boards.greenhouse.io/twitch/jobs/8174019002">Job 3</a>
    </body></html>
    """
    analysis = analyze_job_links("https://job-boards.greenhouse.io/twitch", html)

    assert analysis.job_links_total == 3
    assert analysis.pattern is not None
    assert all("sign_in" not in link for link in analysis.sample_job_links)


def test_same_host_job_detail_slugs_still_match():
    html = """
    <html><body>
      <a href="/careers/senior-backend-engineer">Backend</a>
      <a href="/careers/product-designer">Design</a>
      <a href="/careers/data-scientist">Data</a>
      <a href="/careers">Landing</a>
    </body></html>
    """
    analysis = analyze_job_links("https://acme.example/careers", html)

    assert analysis.job_links_total == 3
    assert analysis.pattern is not None


def test_career_content_pages_not_treated_as_job_details():
    html = """
    <html><body>
      <a href="/careers/life-at-acme">Life at Acme</a>
      <a href="/careers/our-culture">Culture</a>
      <a href="/careers/benefits">Benefits</a>
    </body></html>
    """
    analysis = analyze_job_links("https://acme.example/careers", html)

    assert analysis.job_links_total <= 2
    assert analysis.pattern is None


def test_infers_pattern_for_mixed_smartrecruiters_job_paths():
    html = """
    <html><body>
      <a href="https://jobs.smartrecruiters.com/oneclick-ui/company/Acme/job/12345?lang=en">Oneclick</a>
      <a href="https://jobs.smartrecruiters.com/Acme/744000106787141-backend-engineer">Backend</a>
      <a href="https://jobs.smartrecruiters.com/Acme/744000105614995-data-engineer">Data</a>
      <a href="https://jobs.smartrecruiters.com/Acme/744000104444444-product-manager">PM</a>
    </body></html>
    """
    analysis = analyze_job_links("https://careers.smartrecruiters.com/Acme", html)

    assert analysis.job_links_total >= 3
    assert analysis.pattern is not None
    assert analysis.matched_job_links >= 3


def test_multilingual_role_slugs_detected_without_keyword_matching():
    html = """
    <html><body>
      <a href="/karriere/senior-berater-4711">Role 1</a>
      <a href="/karriere/datenanalyst-4820">Role 2</a>
      <a href="/karriere/projektleiter-4930">Role 3</a>
    </body></html>
    """
    analysis = analyze_job_links("https://beispiel.de/karriere", html)

    assert analysis.job_links_total == 3
    assert analysis.pattern is not None


def test_tracking_links_with_small_numbers_not_classified_as_jobs():
    html = """
    <html><body>
      <a href="https://www.youtube.com/@Company?sub_confirmation=1">YouTube</a>
      <a href="https://example.com/download?ref=abc123">Download</a>
      <a href="https://example.com/">Home</a>
    </body></html>
    """
    analysis = analyze_job_links("https://example.com/careers", html)

    assert analysis.job_links_total == 0
    assert analysis.pattern is None


@pytest.mark.asyncio
async def test_fetch_falls_back_to_render_for_sparse_js_workday_page(monkeypatch):
    html = """
    <html><head><script src="/app.js"></script></head>
    <body><div id="root"></div></body></html>
    """

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True):
            return SimpleNamespace(
                status_code=200,
                url="https://zalando.wd3.myworkdayjobs.com/ZalandoSiteWD",
                text=html,
            )

    async def _fake_render(url: str, opts: dict):
        return '<a href="https://zalando.wd3.myworkdayjobs.com/ZalandoSiteWD/job/foo/JR1">Job</a>'

    import src.shared.browser as browser

    monkeypatch.setattr(browser, "render", _fake_render)
    monkeypatch.setattr(browser, "DEFAULT_USER_AGENT", "ua")

    result = await fetch_page_for_job_link_analysis(
        "https://zalando.wd3.myworkdayjobs.com/ZalandoSiteWD",
        _Client(),
        allow_render_fallback=True,
    )

    assert result.fetch_mode == "render"
    assert "JS-rendered" in " ".join(result.warnings)
    assert "Used browser rendering" in " ".join(result.warnings)


@pytest.mark.asyncio
async def test_fetch_does_not_force_render_for_sparse_non_js_host(monkeypatch):
    html = """
    <html><head><script src="/app.js"></script></head>
    <body><div id="root"></div></body></html>
    """

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True):
            return SimpleNamespace(
                status_code=200,
                url="https://example.com/careers",
                text=html,
            )

    async def _should_not_run(*args, **kwargs):
        raise AssertionError("render fallback should not run for non-JS-heavy host")

    import src.shared.browser as browser

    monkeypatch.setattr(browser, "render", _should_not_run)
    monkeypatch.setattr(browser, "DEFAULT_USER_AGENT", "ua")

    result = await fetch_page_for_job_link_analysis(
        "https://example.com/careers",
        _Client(),
        allow_render_fallback=True,
    )

    assert result.fetch_mode == "http"


@pytest.mark.asyncio
async def test_fetch_falls_back_for_sparse_js_heavy_unknown_host(monkeypatch):
    html = """
    <html><head>
      <script>window.__BOOTSTRAP__ = {}</script>
      <script src="/bundle.js"></script>
      <script src="/vendor.js"></script>
      <script src="/runtime.js"></script>
      <script src="/chunk.js"></script>
    </head><body><div id="jobs-root"></div></body></html>
    """

    class _Client:
        async def get(self, url: str, follow_redirects: bool = True):
            return SimpleNamespace(
                status_code=200,
                url="https://careers.example.com/open-positions",
                text=html,
            )

    async def _fake_render(url: str, opts: dict):
        return '<a href="https://careers.example.com/open-positions/senior-engineer">Job</a>'

    import src.shared.browser as browser

    monkeypatch.setattr(browser, "render", _fake_render)
    monkeypatch.setattr(browser, "DEFAULT_USER_AGENT", "ua")

    result = await fetch_page_for_job_link_analysis(
        "https://careers.example.com/open-positions",
        _Client(),
        allow_render_fallback=True,
    )

    assert result.fetch_mode == "render"
    assert "JS-rendered" in " ".join(result.warnings)
