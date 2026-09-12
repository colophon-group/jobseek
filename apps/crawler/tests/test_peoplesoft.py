from __future__ import annotations

import httpx
import pytest

from src.core.monitors.peoplesoft import (
    PeopleSoftBoard,
    can_handle,
    discover,
    parse_listing,
    peoplesoft_job_from_url,
    peoplesoft_listing_from_url,
)
from src.core.scrapers.peoplesoft import parse_detail, scrape

BOARD_URL = (
    "https://jobs.example.com/psc/applicant/EMPLOYEE/HRMS/c/"
    "HRS_HRAM_FL.HRS_CG_SEARCH_FL.GBL?Action=U&Page=HRS_APP_SCHJOB_FL"
)


def _listing(*, count: int = 1, rows: int = 1, more: bool = False) -> str:
    job_rows = "".join(
        f"""
        <li class="ps_grid-row" id="HRS_AGNT_RSLT_I$0_row_{index}">
          <span id="SCH_JOB_TITLE${index}">R&amp;D Engineer {index}, Hybrid</span>
          <span id="HRS_APP_JBSCH_I_HRS_JOB_OPENING_ID${index}">{699007 + index}</span>
          <span id="LOCATION${index}">Albuquerque, NM</span>
          <span id="HRS_APP_JBSCH_I_HRS_DEPT_DESCR${index}">02229</span>
          <span id="JOB_FAMILY_LABEL${index}">Research &amp; Development</span>
          <span id="SCH_OPENED${index}">09/08/2026</span>
        </li>
        """
        for index in range(rows)
    )
    continuation = (
        """<div class="ps_box-more"
        onclick="submitAction_win0(document.win0,'HRS_AGNT_RSLT_I$hdown$0');">more</div>"""
        if more
        else ""
    )
    return f"""
    <html><body>
      <h2>Search Results List</h2><b>{count}</b> jobs found.
      <form><input type="hidden" name="ICStateNum" value="{rows}" />
      <input type="hidden" name="ICAction" value="" />
      <div id="HRS_AGNT_RSLT_I"><ul>{job_rows}</ul>{continuation}</div></form>
    </body></html>
    """


def _detail() -> str:
    return """
    <html><body>
      <span id="HRS_SCH_WRK2_POSTING_TITLE">R&amp;D Engineer, Hybrid</span>
      <span id="HRS_SCH_WRK2_HRS_JOB_OPENING_ID">699007</span>
      <span id="HRS_SCH_WRK_HRS_DESCRLONG">Albuquerque, NM</span>
      <span id="HRS_SCH_WRK_HRS_FULL_PART_TIME">Full-Time</span>
      <span id="HRS_SCH_WRK_HRS_REG_TEMP">Regular</span>
      <div id="win0divHRS_SCH_PSTDSC_row$0">
        <h2><span class="ps-text">What Your Job Will Be Like</span></h2>
        <span id="HRS_SCH_PSTDSC_DESCRLONG$0"><p>Design flight systems.</p></span>
      </div>
      <div id="win0divHRS_SCH_PSTDSC_row$1">
        <h2><span class="ps-text">Salary Range</span></h2>
        <span id="HRS_SCH_PSTDSC_DESCRLONG$1"><p>$117,500 - $235,700</p></span>
      </div>
      <div id="win0divHRS_SCH_PSTDSC_row$2">
        <h2><span class="ps-text">Qualifications We Require</span></h2>
        <span id="HRS_SCH_PSTDSC_DESCRLONG$2"><ul><li>Engineering degree</li></ul></span>
      </div>
    </body></html>
    """


def test_strict_listing_and_detail_identity():
    board = peoplesoft_listing_from_url(BOARD_URL)
    assert board == PeopleSoftBoard(
        origin="https://jobs.example.com",
        site="applicant",
        portal="EMPLOYEE",
    )
    assert peoplesoft_listing_from_url(f"{BOARD_URL}&Location=NM") is None
    detail = board.detail_url("699007")
    assert peoplesoft_job_from_url(detail) == (board, "699007")


def test_parse_listing_validates_count_and_maps_summary_fields():
    board = peoplesoft_listing_from_url(BOARD_URL)
    assert board is not None
    jobs = parse_listing(_listing(), board)

    assert len(jobs) == 1
    assert jobs[0].title == "R&D Engineer 0, Hybrid"
    assert jobs[0].locations == ["Albuquerque, NM"]
    assert jobs[0].date_posted == "2026-09-08"
    assert jobs[0].metadata == {
        "job_id": "699007",
        "department": "02229",
        "job_family": "Research & Development",
    }

    with pytest.raises(ValueError, match="count mismatch"):
        parse_listing(_listing(count=2), board)


def test_parse_detail_preserves_sections_and_optional_fields():
    content = parse_detail(_detail(), expected_job_id="699007")

    assert content.title == "R&D Engineer, Hybrid"
    assert content.locations == ["Albuquerque, NM"]
    assert content.employment_type == "full_time"
    assert content.job_location_type == "hybrid"
    assert content.base_salary == {
        "min": 117500.0,
        "max": 235700.0,
        "currency": "USD",
        "unit": "year",
    }
    assert "<h2>What Your Job Will Be Like</h2>" in content.description
    assert "Design flight systems" in content.extras["responsibilities"]
    assert "Engineering degree" in content.extras["qualifications"]


async def test_monitor_and_scraper_bootstrap_anonymous_session():
    detail_url = None
    continuation_posts = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal continuation_posts, detail_url
        if request.url.path.startswith("/psp/"):
            return httpx.Response(
                200,
                text="anonymous session",
                headers={"set-cookie": "PSJSESSIONID=abc; Path=/; Secure"},
            )
        assert "PSJSESSIONID=abc" in request.headers.get("cookie", "")
        if request.method == "POST":
            continuation_posts += 1
            assert b"HRS_AGNT_RSLT_I%24hdown%240" in request.content
            return httpx.Response(200, text=_listing(count=2, rows=2))
        if request.url.params.get("Page") == "HRS_APP_SCHJOB_FL":
            return httpx.Response(200, text=_listing(count=2, rows=1, more=True))
        detail_url = str(request.url)
        return httpx.Response(200, text=_detail())

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        detected = await can_handle(BOARD_URL, client)
        jobs = await discover({"board_url": BOARD_URL, "metadata": {}}, client)
        content = await scrape(jobs[0].url, {}, client)

    assert detected == {"jobs": 2}
    assert len(jobs) == 2
    assert continuation_posts == 2
    assert detail_url is not None and "JobOpeningId=699007" in detail_url
    assert content.description and "Design flight systems" in content.description
