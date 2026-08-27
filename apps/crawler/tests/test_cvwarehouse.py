from __future__ import annotations

import httpx
import pytest

from src.core.monitors import DiscoveredJob
from src.core.monitors.cvwarehouse import _section_from_page, can_handle, discover

BOARD_URL = "https://acme.cvw.io/?lang=nl-BE"
SECTION = "a4c5d125-8de6-c54b-64df-1502274510e7"


def _landing() -> str:
    return f"""
    <html><head><meta name="keywords" content="Jobs, CVWarehouse"></head><body>
      <a href="/?lang=nl-BE&amp;section=small-section-0000"><span class="badge">2</span></a>
      <a href="/?lang=nl-BE&amp;section={SECTION}"><span class="badge">2</span></a>
      <div data-item="readmore"></div>
    </body></html>
    """


def _locale_page(lang: str, jobs: list[tuple[str, str, str]]) -> str:
    cards = []
    details = []
    for job_id, title, location in jobs:
        slug = title.replace(" ", "-")
        href = f"/?lang={lang}&amp;section={SECTION}&amp;job={job_id}&amp;q={slug}"
        cards.append(
            f"""
            <div data-item-collection="jobs" data-filter-worktype="Bediende"
                 data-filter-workschedule='["Voltijds"]'
                 data-filter-attribute='["Acme"]'>
              <a data-jobid="{job_id}" href="{href}">
                <span class="job-title">{title}</span>
                <div class="location">{location}</div>
                <div class="workType"><i class="lni-laptop"></i>Gedeeltelijk afstandswerk</div>
              </a>
            </div>
            """
        )
        details.append(
            f"""
            <div data-jobdetail-job-id="{job_id}">
              <h2 class="job-title">{title}</h2>
              <div class="additional-data"><div class="location">{location}</div></div>
              <div class="jobDescriptionText"><p>Build important infrastructure.</p></div>
            </div>
            """
        )
    return f"""
    <html><head><meta name="keywords" content="Jobs, CVWarehouse"></head><body>
      <div id="language-modal">
        <a href="/?lang=nl-BE&amp;section={SECTION}">NL</a>
        <a href="/?lang=fr-FR&amp;section={SECTION}">FR</a>
      </div>
      {"".join(cards)}
      {"".join(details)}
    </body></html>
    """


def test_largest_section_is_unfiltered_inventory() -> None:
    page = _landing().replace(
        'section=small-section-0000"><span class="badge">2',
        'section=12345678-1234-1234-1234-123456789012"><span class="badge">1',
    )
    assert _section_from_page(page) == (SECTION, 2)


async def test_discovers_all_locales_and_deduplicates_job_ids() -> None:
    nl = _locale_page("nl-BE", [("101", "Projectleider", "Brussel"), ("102", "Engineer", "Gent")])
    fr = _locale_page("fr-FR", [("102", "Ingénieur", "Gand")])

    def handler(request: httpx.Request) -> httpx.Response:
        lang = request.url.params.get("lang")
        if request.url.params.get("section") == SECTION:
            return httpx.Response(200, text=fr if lang == "fr-FR" else nl)
        return httpx.Response(200, text=_landing())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        jobs = await discover({"board_url": BOARD_URL}, client)

    assert len(jobs) == 2
    assert all(isinstance(job, DiscoveredJob) for job in jobs)
    first = jobs[0]
    assert first.title == "Projectleider"
    assert first.locations == ["Brussel"]
    assert first.description == "<p>Build important infrastructure.</p>"
    assert first.employment_type == "Voltijds"
    assert first.job_location_type == "Gedeeltelijk afstandswerk"
    assert first.language == "nl"
    assert first.metadata == {
        "job_id": "101",
        "work_type": "Bediende",
        "work_schedule": ["Voltijds"],
        "brand": ["Acme"],
    }


async def test_advertised_count_mismatch_fails_closed() -> None:
    page = _locale_page("nl-BE", [("101", "Projectleider", "Brussel")])
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=page))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="advertised 2 jobs but exposed 1"):
            await discover(
                {"board_url": BOARD_URL, "metadata": {"section": SECTION, "jobs": 2}},
                client,
            )


async def test_can_handle_returns_section_and_count() -> None:
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            text=_landing() if request.url.host == "acme.cvw.io" else "<html></html>",
        )
    )
    async with httpx.AsyncClient(transport=transport) as client:
        assert await can_handle(BOARD_URL, client) == {"section": SECTION, "jobs": 2}
        assert await can_handle("https://example.com/jobs", client) is None


async def test_missing_required_detail_field_fails() -> None:
    broken = _locale_page("nl-BE", [("101", "Projectleider", "Brussel")]).replace(
        "<p>Build important infrastructure.</p>", ""
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=broken))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="missing required rich fields"):
            await discover(
                {"board_url": BOARD_URL, "metadata": {"section": SECTION, "jobs": 1}},
                client,
            )
