from __future__ import annotations

import httpx

from src.core.scrapers import all_scraper_types
from src.core.scrapers.recruiterbox import can_handle, parse_html, scrape


def _detail(*, remote: bool = True) -> str:
    remote_html = "<span>| Fully remote</span>" if remote else ""
    return f"""
    <html>
      <head><title>Field Sales Representative at Top Closers</title></head>
      <body>
        <header>
          <div class="js-job-title rb-text-1">Field Sales Representative</div>
          <p class="opening-info rb-text-3">
            <span class="meta-job-location-city">Middlesbrough</span>,
            <span class="meta-job-location-state">England</span>,
            <span class="meta-job-location-country">United Kingdom</span>
            <span>| Full-time</span>
            {remote_html}
          </p>
        </header>
        <div class="jobdesciption">
          <p>Sell renewable energy products to qualified customers.</p>
          <ul><li>Two years of sales experience</li></ul>
        </div>
      </body>
    </html>
    """


def test_parse_html_extracts_required_and_optional_fields():
    content = parse_html(_detail())

    assert content.title == "Field Sales Representative"
    assert content.locations == ["Middlesbrough, England, United Kingdom"]
    assert content.employment_type == "Full-time"
    assert content.job_location_type == "remote"
    assert content.description is not None
    assert "<p>Sell renewable energy products" in content.description
    assert "<li>Two years of sales experience</li>" in content.description


def test_parse_html_allows_missing_remote_marker():
    content = parse_html(_detail(remote=False))

    assert content.locations == ["Middlesbrough, England, United Kingdom"]
    assert content.employment_type == "Full-time"
    assert content.job_location_type is None


def test_can_handle_requires_provider_detail_markers():
    assert can_handle([_detail(), _detail(remote=False)]) == {}
    assert can_handle(["<html><body>unrelated</body></html>"]) is None
    assert can_handle([]) is None


async def test_scrape_fetches_static_detail_page():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, text=_detail(), request=request)
    )
    async with httpx.AsyncClient(transport=transport) as client:
        content = await scrape("https://acme.hire.trakstar.com/jobs/abc123/", {}, client)

    assert content.title == "Field Sales Representative"
    assert content.locations == ["Middlesbrough, England, United Kingdom"]
    assert content.description is not None


def test_scraper_is_registered():
    assert "recruiterbox" in all_scraper_types()
