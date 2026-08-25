from __future__ import annotations

from unittest.mock import AsyncMock, patch

import httpx
import pytest

from src.core.monitor import MonitorResult
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


def _listing_html(
    *,
    company_slug: str = "damora-therapeutics",
    job_id: str = "4442073767",
    title: str = "Manager/Senior Manager, Regulatory Affairs",
    location: str = "Massachusetts, United States",
    href: str | None = None,
) -> str:
    href = href or (
        "https://www.linkedin.com/jobs/view/"
        f"manager-regulatory-affairs-at-damora-therapeutics-{job_id}?position=1"
    )
    return f"""
    <li>
      <div class="base-search-card" data-entity-urn="urn:li:jobPosting:{job_id}">
        <a class="base-card__full-link"
           href="{href}">
          {title}
        </a>
        <h3 class="base-search-card__title">{title}</h3>
        <h4 class="base-search-card__subtitle">
          <a href="https://www.linkedin.com/company/{company_slug}?trk=jobs">Damora</a>
        </h4>
        <span class="job-search-card__location">{location}</span>
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
        assert jobs[0].url == "https://www.linkedin.com/jobs/view/4442073767"
        assert jobs[0].title == "Manager/Senior Manager, Regulatory Affairs"
        assert jobs[0].locations == ["Massachusetts, United States"]
        assert jobs[0].date_posted == "2026-07-20"
        assert jobs[0].company_slug == "damora-therapeutics"

    def test_ignores_untrusted_or_mismatched_job_href(self):
        html = _listing_html().replace(
            "https://www.linkedin.com/jobs/view/manager-regulatory-affairs-at-damora-therapeutics-4442073767?position=1",
            "https://attacker.example/jobs/view/wrong-9999999999",
        )

        jobs = _parse_listing_cards(html)

        assert jobs[0].url == "https://www.linkedin.com/jobs/view/4442073767"

    @pytest.mark.parametrize(
        "href",
        [
            "https://www.linkedin.com/jobs/view/human-resources-generalist-4453689816",
            "https://ch.linkedin.com/jobs/view/generaliste-rh-4453689816?trk=public_jobs",
            "https://fr.linkedin.com/jobs/view/arbitrary-renamed-title-4453689816?position=3",
            "https://www.linkedin.com/jobs/view/4453689816",
        ],
    )
    def test_title_and_locale_paths_share_numeric_provider_identity(self, href: str):
        job = _parse_listing_cards(_listing_html(job_id="4453689816", href=href))[0]

        assert job.url == "https://www.linkedin.com/jobs/view/4453689816"

    def test_rejects_malformed_listing_card(self):
        html = _listing_html().replace("urn:li:jobPosting:4442073767", "missing-job-urn")
        with pytest.raises(ValueError, match="numeric job URN"):
            _parse_listing_cards(html)

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
            assert request.url.params.get("location") == "Worldwide"
            assert request.url.params.get("sortBy") == "DD"
            assert request.headers["accept-language"] == "en-US,en;q=0.9"
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

        assert isinstance(result, list)
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

    async def test_source_ownership_routes_exact_country_codes_without_title_matching(self):
        # Live overlap fixtures audited 2026-08-25:
        # - LinkedIn 4453689816 == jobs.ch 8da62f2b-... (LinkedIn owns Switzerland)
        # - LinkedIn 4457321546 == PhuketAll 057289-213283 (PhuketAll owns Thailand)
        html = "".join(
            [
                _listing_html(
                    job_id="4453689816",
                    title="Human Resources Generalist",
                    location="Montreux, Vaud, Switzerland",
                ),
                _listing_html(
                    job_id="4457317622",
                    title="AI Transformation Intern",
                    location="Montreux, Vaud, Switzerland",
                ),
                _listing_html(
                    job_id="4457321546",
                    title="Marketing Director Phuket",
                    location="Phuket, Thailand",
                ),
                _listing_html(
                    job_id="4458171439",
                    title="Paymaster",
                    location="Umluj, Tabuk, Saudi Arabia",
                ),
            ]
        )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=html, request=request)
        )

        async with httpx.AsyncClient(transport=transport) as client:
            result = await discover(
                {
                    "board_url": BOARD_URL,
                    "metadata": {
                        "company_id": COMPANY_ID,
                        "company_slug": "damora-therapeutics",
                        "source_ownership_excluded_country_codes": ["THA"],
                    },
                },
                client,
            )

        assert isinstance(result, list)
        assert {job.metadata["job_id"] for job in result} == {
            "4453689816",
            "4457317622",
            "4458171439",
        }
        assert {job.metadata["location_country_code"] for job in result} == {"CHE", "SAU"}

    @pytest.mark.parametrize("location", [None, "Remote", "Phuket, ประเทศไทย"])
    async def test_source_ownership_fails_closed_without_exact_country(
        self,
        location: str | None,
    ):
        html = _listing_html(location=location or "Massachusetts, United States")
        if location is None:
            html = html.replace(
                '<span class="job-search-card__location">Massachusetts, United States</span>',
                "",
            )
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text=html, request=request)
        )

        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="source ownership"):
                await discover(
                    {
                        "board_url": BOARD_URL,
                        "metadata": {
                            "company_id": COMPANY_ID,
                            "company_slug": "damora-therapeutics",
                            "source_ownership_excluded_country_codes": ["THA"],
                        },
                    },
                    client,
                )

    async def test_paginates_provider_ten_card_pages_without_skipping_offsets(self):
        requested_starts: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params["start"])
            requested_starts.append(start)
            count = 10 if start == 0 else 1
            html = "".join(
                _listing_html(job_id=str(4_442_073_767 + start + offset)) for offset in range(count)
            )
            return httpx.Response(200, text=html, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("src.core.monitors.linkedin.asyncio.sleep", new_callable=AsyncMock) as sleep:
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

        assert isinstance(result, list)
        assert len(result) == 11
        assert requested_starts == [0, 10]
        sleep.assert_awaited_once_with(1.0)

    async def test_keyword_query_supplements_instead_of_replacing_exact_company_query(self):
        requested_keywords: list[str | None] = []

        def handler(request: httpx.Request) -> httpx.Response:
            keyword = request.url.params.get("keywords")
            requested_keywords.append(keyword)
            job_id = "4442073768" if keyword else "4442073767"
            return httpx.Response(200, text=_listing_html(job_id=job_id), request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with patch("src.core.monitors.linkedin.asyncio.sleep", new_callable=AsyncMock) as sleep:
                result = await discover(
                    {
                        "board_url": BOARD_URL,
                        "metadata": {
                            "company_id": COMPANY_ID,
                            "company_slug": "damora-therapeutics",
                            "keywords": "Damora Therapeutics",
                        },
                    },
                    client,
                )

        assert isinstance(result, MonitorResult)
        assert result.truncated is True
        assert result.jobs_by_url is not None
        assert {job.metadata["job_id"] for job in result.jobs_by_url.values()} == {
            "4442073767",
            "4442073768",
        }
        assert requested_keywords == [None, "Damora Therapeutics"]
        sleep.assert_awaited_once_with(1.0)

    async def test_rejects_foreign_company_card_in_filtered_results(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text=_listing_html(company_slug="different-company"),
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="company filter returned"):
                await discover(
                    {
                        "board_url": BOARD_URL,
                        "metadata": {
                            "company_id": COMPANY_ID,
                            "company_slug": "damora-therapeutics",
                        },
                    },
                    client,
                )

    async def test_rejects_repeated_job_across_pages(self):
        def handler(request: httpx.Request) -> httpx.Response:
            start = int(request.url.params["start"])
            if start == 0:
                html = "".join(
                    _listing_html(job_id=str(4_442_073_767 + offset)) for offset in range(10)
                )
            else:
                html = _listing_html(job_id="4442073767")
            return httpx.Response(200, text=html, request=request)

        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with (
                patch("src.core.monitors.linkedin.asyncio.sleep", new_callable=AsyncMock),
                pytest.raises(ValueError, match="pagination repeated job"),
            ):
                await discover(
                    {
                        "board_url": BOARD_URL,
                        "metadata": {
                            "company_id": COMPANY_ID,
                            "company_slug": "damora-therapeutics",
                        },
                    },
                    client,
                )

    async def test_rejects_nonempty_authwall_without_cards(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(200, text="<html>authwall</html>", request=request)
        )
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(ValueError, match="without job cards"):
                await discover(
                    {
                        "board_url": BOARD_URL,
                        "metadata": {
                            "company_id": COMPANY_ID,
                            "company_slug": "damora-therapeutics",
                        },
                    },
                    client,
                )

    async def test_accepts_doctype_prefixed_empty_fragment(self):
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                text="<!DOCTYPE html>\n\n<!---->  ",
                request=request,
            )
        )
        async with httpx.AsyncClient(transport=transport) as client:
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

        assert result == []

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
        assert (
            _job_id_from_url("https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4442073767")
            == "4442073767"
        )
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

    @pytest.mark.parametrize("status", [404, 410])
    async def test_surfaces_closed_job_for_immediate_delisting(self, status: int):
        transport = httpx.MockTransport(lambda request: httpx.Response(status, request=request))
        async with httpx.AsyncClient(transport=transport) as client:
            with pytest.raises(httpx.HTTPStatusError) as error:
                await scrape("https://www.linkedin.com/jobs/view/4442073767", {}, client)

        assert error.value.response.status_code == status

    async def test_uses_provider_specific_rate_limit_retry_budget(self):
        client = AsyncMock(spec=httpx.AsyncClient)
        with patch(
            "src.core.scrapers.linkedin.fetch_text_page_with_retry",
            new_callable=AsyncMock,
            return_value=DETAIL_HTML,
        ) as fetch:
            result = await scrape(
                "https://www.linkedin.com/jobs/view/title-4442073767",
                {},
                client,
            )

        assert result.description
        fetch.assert_awaited_once_with(
            client,
            "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4442073767",
            retries=4,
            base_delay=1.5,
            end_of_pagination_statuses=(),
        )


def test_workspace_auto_configuration():
    assert detect_ats_from_url(BOARD_URL) == "linkedin"
    assert auto_scraper_type("linkedin") == (
        "linkedin",
        {"enrich": ["description", "employment_type", "job_location_type"]},
    )
