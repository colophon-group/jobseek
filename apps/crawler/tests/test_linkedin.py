from __future__ import annotations

import httpx

from src.core.monitors import slugs_from_url
from src.core.monitors.linkedin import (
    _company_id_from_url,
    _company_slug_from_url,
    _parse_listing_cards,
    can_handle,
    discover,
)
from src.core.scrapers.linkedin import _job_id_from_url, parse_html, scrape
from src.workspace._compat import auto_scraper_type, detect_ats_from_url

BOARD_URL = "https://www.linkedin.com/company/damora-therapeutics/jobs/"
COMPANY_ID = "109559449"


def _listing_html(*, company_slug: str = "damora-therapeutics") -> str:
    return f"""
    <li>
      <div class="base-search-card" data-entity-urn="urn:li:jobPosting:4442073767">
        <a class="base-card__full-link"
           href="https://www.linkedin.com/jobs/view/manager-regulatory-affairs-at-damora-therapeutics-4442073767?position=1">
          Manager/Senior Manager, Regulatory Affairs
        </a>
        <h3 class="base-search-card__title">Manager/Senior Manager, Regulatory Affairs</h3>
        <h4 class="base-search-card__subtitle">
          <a href="https://www.linkedin.com/company/{company_slug}?trk=jobs">Damora</a>
        </h4>
        <span class="job-search-card__location">Massachusetts, United States</span>
        <time datetime="2026-07-20">1 day ago</time>
      </div>
    </li>
    """


DETAIL_HTML = f"""
<section class="top-card-layout">
  <h2 class="top-card-layout__title">Manager/Senior Manager, Regulatory Affairs</h2>
  <a class="topcard__org-name-link"
     href="https://www.linkedin.com/company/damora-therapeutics?trk=jobs">Damora</a>
  <span class="topcard__flavor topcard__flavor--bullet">Boston, MA (Hybrid)</span>
  <a href="https://www.linkedin.com/login?session_redirect=facetCurrentCompany%3D{COMPANY_ID}">People</a>
</section>
<section class="show-more-less-html">
  <div class="show-more-less-html__markup">
    <p>Lead regulatory strategy.</p><ul><li>File submissions</li></ul>
  </div>
</section>
<ul class="description__job-criteria-list">
  <li class="description__job-criteria-item">
    <h3 class="description__job-criteria-subheader">Seniority level</h3>
    <span class="description__job-criteria-text">Director</span>
  </li>
  <li class="description__job-criteria-item">
    <h3 class="description__job-criteria-subheader">Employment type</h3>
    <span class="description__job-criteria-text">Full-time</span>
  </li>
  <li class="description__job-criteria-item">
    <h3 class="description__job-criteria-subheader">Industries</h3>
    <span class="description__job-criteria-text">Biotechnology Research</span>
  </li>
</ul>
"""


class TestListingParser:
    def test_extracts_summary_and_stable_url(self):
        jobs = _parse_listing_cards(_listing_html())

        assert len(jobs) == 1
        assert jobs[0].job_id == "4442073767"
        assert jobs[0].url == (
            "https://www.linkedin.com/jobs/view/"
            "manager-regulatory-affairs-at-damora-therapeutics-4442073767"
        )
        assert jobs[0].title == "Manager/Senior Manager, Regulatory Affairs"
        assert jobs[0].locations == ["Massachusetts, United States"]
        assert jobs[0].date_posted == "2026-07-20"
        assert jobs[0].company_slug == "damora-therapeutics"

    def test_url_detection(self):
        assert _company_slug_from_url(BOARD_URL) == "damora-therapeutics"
        assert (
            _company_id_from_url(f"https://www.linkedin.com/jobs/search/?f_C={COMPANY_ID}")
            == COMPANY_ID
        )
        assert _company_slug_from_url("https://example.com/company/acme/jobs") is None

    def test_linkedin_is_not_an_ats_slug_guess(self):
        assert slugs_from_url(BOARD_URL) == []


class TestMonitor:
    async def test_discovers_rich_summaries(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.params.get("f_C") == COMPANY_ID
            return httpx.Response(200, text=_listing_html(), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await discover(
                {
                    "board_url": BOARD_URL,
                    "metadata": {
                        "company_id": COMPANY_ID,
                        "company_slug": "damora-therapeutics",
                    },
                },
                client,
            )

        assert len(result) == 1
        job = result[0]
        assert job.title == "Manager/Senior Manager, Regulatory Affairs"
        assert job.description is None
        assert job.locations == ["Massachusetts, United States"]
        assert job.date_posted == "2026-07-20"
        assert job.metadata == {
            "job_id": "4442073767",
            "linkedin_company_id": COMPANY_ID,
            "linkedin_company_slug": "damora-therapeutics",
        }

    async def test_probe_resolves_company_id_from_exact_slug(self):
        def handler(request: httpx.Request) -> httpx.Response:
            path = request.url.path
            if path.endswith("/seeMoreJobPostings/search"):
                if request.url.params.get("keywords"):
                    return httpx.Response(200, text=_listing_html(), request=request)
                assert request.url.params.get("f_C") == COMPANY_ID
                return httpx.Response(200, text=_listing_html(), request=request)
            if path.endswith("/jobPosting/4442073767"):
                return httpx.Response(200, text=DETAIL_HTML, request=request)
            raise AssertionError(f"unexpected request: {request.url}")

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            assert await can_handle(BOARD_URL, client) == {
                "company_slug": "damora-therapeutics",
                "company_id": COMPANY_ID,
                "jobs": 1,
            }

    async def test_probe_rejects_keyword_result_for_different_company(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing_html(company_slug="different-company"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            assert await can_handle(BOARD_URL, client) is None

    async def test_direct_detection_without_client(self):
        assert await can_handle(BOARD_URL) == {"company_slug": "damora-therapeutics"}
        assert await can_handle("https://example.com/jobs") is None


class TestScraper:
    def test_parses_guest_detail(self):
        result = parse_html(DETAIL_HTML)

        assert result.title == "Manager/Senior Manager, Regulatory Affairs"
        assert result.description == (
            "<p>Lead regulatory strategy.</p><ul><li>File submissions</li></ul>"
        )
        assert result.locations == ["Boston, MA (Hybrid)"]
        assert result.employment_type == "Full-time"
        assert result.job_location_type == "hybrid"
        assert result.metadata == {
            "seniority_level": "Director",
            "industries": "Biotechnology Research",
            "linkedin_company_slug": "damora-therapeutics",
        }

    def test_extracts_job_id(self):
        assert _job_id_from_url("https://www.linkedin.com/jobs/view/title-4442073767") == (
            "4442073767"
        )
        assert _job_id_from_url("https://www.linkedin.com/jobs/view/4442073767") == "4442073767"
        assert _job_id_from_url("https://example.com/jobs/4442073767") is None

    async def test_fetches_guest_detail_endpoint(self):
        def handler(request: httpx.Request) -> httpx.Response:
            assert request.url.path.endswith("/jobPosting/4442073767")
            return httpx.Response(200, text=DETAIL_HTML, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await scrape(
                "https://www.linkedin.com/jobs/view/title-4442073767",
                {},
                client,
            )
        assert result.description


def test_workspace_auto_configuration():
    assert detect_ats_from_url(BOARD_URL) == "linkedin"
    assert auto_scraper_type("linkedin") == (
        "linkedin",
        {"enrich": ["description", "employment_type", "job_location_type"]},
    )
