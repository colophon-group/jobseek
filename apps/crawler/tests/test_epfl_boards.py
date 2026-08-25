"""Live-shape contracts for EPFL's first-party board configurations."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitor import monitor_one
from src.core.monitors.inline import discover
from src.core.monitors.rss import discover as rss_discover

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_FAIL_CLOSED_INLINE_BOARDS = {
    "epfl-csea",
    "epfl-gr-ost",
    "epfl-hqc",
    "epfl-imos",
    "epfl-lanes",
    "epfl-las",
    "epfl-lemaitre",
    "epfl-lfim",
    "epfl-lpdc",
    "epfl-lrm",
    "epfl-luxs",
    "epfl-mesobio",
    "epfl-phd-edbb",
    "epfl-phd-edma",
    "epfl-phd-edms",
    "epfl-phd-edne",
    "epfl-spc-phd",
}


def _epfl_boards() -> dict[str, dict]:
    with _BOARDS_PATH.open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "epfl"]
    return {
        row["board_slug"]: {
            "board_url": row["board_url"],
            "monitor_type": row["monitor_type"],
            "metadata": json.loads(row["monitor_config"] or "{}"),
            "scraper_type": row["scraper_type"],
            "scraper_config": json.loads(row["scraper_config"] or "{}"),
        }
        for row in rows
    }


async def _discover_fixture(board: dict, html: str):
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        return await discover(board, client)


def test_epfl_inventory_uses_verified_current_sources():
    boards = _epfl_boards()

    assert {
        "epfl-csea",
        "epfl-gr-ost",
        "epfl-hqc",
        "epfl-imos",
        "epfl-lanes",
        "epfl-las",
        "epfl-lemaitre",
        "epfl-lfim",
        "epfl-lpdc",
        "epfl-lrm",
        "epfl-luxs",
        "epfl-mesobio",
        "epfl-phd-edpy",
    } <= boards.keys()
    assert len(boards) == 21
    assert {"epfl-bion", "epfl-hylab", "epfl-math", "epfl-quantum"}.isdisjoint(boards)
    assert boards["epfl-careers"]["metadata"] == {
        "preset": "successfactors",
        "feed_url": "https://careers.epfl.ch/googlefeed.xml",
    }
    assert boards["epfl-phd-edpy"]["scraper_type"] == "dom"
    assert boards["epfl-phd-edpy"]["scraper_config"]["enrich"] == ["description"]
    assert all(
        boards[slug]["metadata"].get("require_zero_proof") is True
        for slug in _FAIL_CLOSED_INLINE_BOARDS
    )


@pytest.mark.asyncio
async def test_edbb_live_config_filters_each_explicit_deadline_before_cycle_default():
    board = _epfl_boards()["epfl-phd-edbb"]
    html = """
    <h1>EDBB Open positions</h1>
    <div>• Old Lab (posted 01.01.2000)</div>
    <p>Old role. Application deadline: 27.02.2000.</p>
    <div>• Future Lab A</div>
    <p>Current role. Application deadline: April 15, 2099.</p>
    <div>• Future Lab B</div>
    <p>Current role. Application deadline: 1st December 2099.</p>
    <h2>Upcoming EDBB Openings</h2>
    <div>• Not Yet Open</div><p>Future cycle.</p>
    """

    jobs = await _discover_fixture(board, html)

    assert [(job.title, job.extras) for job in jobs] == [
        ("Future Lab A", {"valid_through": "2099-04-15"}),
        ("Future Lab B", {"valid_through": "2099-12-01"}),
    ]


@pytest.mark.asyncio
async def test_edms_live_config_extracts_unseen_table_rows_without_name_allowlist():
    board = _epfl_boards()["epfl-phd-edms"]

    jobs = await _discover_fixture(
        board,
        '<h3>Institute</h3><table><tr><td><a href="/future-lab">Future Director</a></td>'
        '<td><a href="/future-project.pdf">A newly posted molecular-science project.</a> '
        "(1 position).</td></tr></table>",
    )

    assert [(job.title, job.description) for job in jobs] == [
        (
            "A newly posted molecular-science project.",
            "A newly posted molecular-science project.",
        )
    ]
    assert all(job.title != "Future Director" for job in jobs)


@pytest.mark.asyncio
async def test_isic_live_config_partitions_current_and_inactive_pdf_identities():
    board = _epfl_boards()["epfl-isic"]
    active_url = (
        "https://www.epfl.ch/schools/sb/research/isic/wp-content/uploads/2026/03/LPMT_Phd_2026.pdf"
    )
    inactive_url = (
        "https://www.epfl.ch/schools/sb/research/isic/"
        "wp-content/uploads/2025/02/LIFMET_Annonce_2025.pdf"
    )
    html = f"""
    <h3>Postdoctoral Positions</h3>
    <table>
      <tr><td><a href="https://www.epfl.ch/labs/lfim/openings/">LFIM openings</a></td></tr>
      <tr><td><a href="https://www.epfl.ch/labs/lrm/job-openings/">NMR role</a></td></tr>
      <tr><td><a href="https://www.epfl.ch/labs/luxs/openings/">First LUXS role</a></td></tr>
      <tr><td><a href="https://www.epfl.ch/labs/luxs/openings/">Second LUXS role</a></td></tr>
      <tr><td><a href="{active_url}">Current PDF role</a></td></tr>
      <tr><td><a href="{inactive_url}">Expired PDF role</a></td></tr>
    </table>
    <h3 id="Masterprojects">Master projects</h3>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await monitor_one(
            board["board_url"],
            "dom",
            board["metadata"],
            client,
        )

    assert result.urls == {active_url}


@pytest.mark.asyncio
async def test_isic_lifecycle_partition_fails_closed_on_unreviewed_pdf():
    board = _epfl_boards()["epfl-isic"]
    html = """
    <h3>Postdoctoral Positions</h3>
    <table><tr><td><a href="https://www.epfl.ch/jobs/new-role.pdf">New role</a></td></tr></table>
    <h3 id="Masterprojects">Master projects</h3>
    """
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(ValueError, match="unclassified lifecycle URL"):
            await monitor_one(board["board_url"], "dom", board["metadata"], client)


@pytest.mark.asyncio
async def test_edne_live_config_extracts_structural_lab_names_and_ignores_instructions():
    board = _epfl_boards()["epfl-phd-edne"]
    html = """
    <button>Deadline, 1st November</button>
    <p>Future Laboratory of New Science has openings for two students.</p>
    <p>Please indicate your interest in the application form.</p>
    <p>Novel Laboratory for Experimental Systems encourages candidates to apply.</p>
    <p>More positions may be still posted!</p>
    """

    jobs = await _discover_fixture(board, html)

    assert [job.title for job in jobs] == [
        "Future Laboratory of New Science",
        "Novel Laboratory for Experimental Systems",
    ]


@pytest.mark.asyncio
async def test_quantum_roles_use_central_stable_ids_and_source_owned_hqc():
    boards = _epfl_boards()
    feed = """<?xml version="1.0" encoding="UTF-8"?>
    <rss version="2.0"><channel>
      <item><title>Ph.D. position in Microcombs for atomic clocks</title>
        <link>https://careers.epfl.ch/job/Lausanne/Microcombs/1165041355/</link></item>
      <item><title>PhD. Student position in Integrated photonic-spintronic devices</title>
        <link>https://careers.epfl.ch/job/Lausanne/Spintronics/1165041255/</link></item>
    </channel></rss>"""
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=feed, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        hylab = await rss_discover(boards["epfl-careers"], client)

    hqc = await _discover_fixture(
        boards["epfl-hqc"],
        """
        <h3>Postdoc positions available in HQC lab</h3>
        <div>Hybrid Circuit Role</div><p>Applications are continuously evaluated.</p>
        <div>Metamaterial Role</div><p>Applications are continuously evaluated.</p>
        <h3>PhD positions available in HQC lab</h3>
        """,
    )
    titles = [job.title for job in [*hqc, *hylab]]
    assert titles == [
        "Hybrid Circuit Role",
        "Metamaterial Role",
        "Ph.D. position in Microcombs for atomic clocks",
        "PhD. Student position in Integrated photonic-spintronic devices",
    ]
    assert len({job.url for job in hylab}) == 2


@pytest.mark.asyncio
async def test_source_owned_isic_labs_extract_each_distinct_current_role():
    boards = _epfl_boards()
    fixtures = {
        "epfl-las": (
            "<h2>PhD position</h2><p>Current PhD role.</p>"
            "<h2>Postdoctoral position</h2><p>We currently do not have an open position.</p>",
            ["PhD position"],
        ),
        "epfl-lpdc": (
            "<h3>Internships</h3><p>Student work.</p>"
            "<h3>PhD Positions</h3><p>Current doctoral role.</p>"
            "<h3>Post-doc Positions</h3><p>Current postdoc role.</p>",
            ["PhD Positions", "Post-doc Positions"],
        ),
        "epfl-lrm": (
            "<p>Postdoctoral positions in NMR spectroscopy</p><p>Current role.</p>",
            ["Postdoctoral positions in NMR spectroscopy"],
        ),
        "epfl-luxs": (
            "<p>Expired role</p><p>Application deadline: 01.04.2020</p>"
            "<p>Ph.D. Positions</p><ul><li>Current topic A</li><li>Current topic B</li></ul>"
            "<p>For applications and further information contact the laboratory.</p>",
            ["Current topic A", "Current topic B"],
        ),
    }

    for slug, (html, expected_titles) in fixtures.items():
        jobs = await _discover_fixture(boards[slug], html)
        assert [job.title for job in jobs] == expected_titles


@pytest.mark.asyncio
async def test_missing_source_owned_labs_have_stable_complete_position_identities():
    boards = _epfl_boards()
    imos = await _discover_fixture(
        boards["epfl-imos"],
        """
        <h1>Open positions</h1>
        <div class="entry-content container-grid pb-5">PhD Position in Urban Energy Systems</div>
        <p>Develop hierarchical graph neural networks.</p>
        <div class="entry-content container-grid pb-5">
          Summer Internship in Computer Vision and Machine Learning
        </div>
        <p>A paid, on-site internship at EPFL.</p>
        <div>Back to top</div>
        """,
    )
    lemaitre = await _discover_fixture(
        boards["epfl-lemaitre"],
        """
        <h1>Open Positions</h1><h3>Innate immunity in Drosophila</h3><h3>PhD student</h3>
        <p>One PhD position is now available in these areas.</p><div>Back to top</div>
        """,
    )
    gr_ost = await _discover_fixture(
        boards["epfl-gr-ost"],
        """
        <h1>Open Positions</h1><p>There are three open PhD positions in our group.</p>
        <p>Study liquid-liquid interface dynamics.</p><div>Back to top</div>
        """,
    )

    assert [(job.title, job.employment_type) for job in imos] == [
        ("PhD Position in Urban Energy Systems", "full_time"),
        ("Summer Internship in Computer Vision and Machine Learning", "internship"),
    ]
    assert [job.title for job in lemaitre] == ["PhD student"]
    assert [job.title for job in gr_ost] == ["PhD position"] * 3
    all_jobs = [*imos, *lemaitre, *gr_ost]
    assert len(all_jobs) == 6
    assert len({job.url for job in all_jobs}) == 6


@pytest.mark.asyncio
async def test_edpy_partitions_current_expired_and_hidden_stale_pdf_anchors():
    board = _epfl_boards()["epfl-phd-edpy"]
    uploads = "https://www.epfl.ch/education/phd/edpy-physics/wp-content/uploads"
    active_url = (
        f"{uploads}/2026/05/PhD-position-in-Mechanics-of-Soft-and-Biological-Matter-Laboratory.pdf"
    )
    inactive_url = f"{uploads}/2026/04/PhD-position-in-experimental-particle-physics-LHCb.pdf"
    html = (
        '<div class="entry-content"><ul>'
        f'<li><a href="{active_url}">Current role</a></li>'
        f'<li><a href="{inactive_url}">Expired role</a></li>'
        f'<li><a href="{uploads}/2025/06/'
        'PhD-in-Mechanics-of-Soft-and-Biological-Matter-Laboratory.pdf">&nbsp;</a></li>'
        f'<li><a href="{uploads}/2023/12/'
        'PhD-position-in-data-processing-for-cryo-electron.pdf"> </a></li>'
        "</ul></div>"
    )
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        result = await monitor_one(board["board_url"], "dom", board["metadata"], client)

    assert result.urls == {active_url}


@pytest.mark.asyncio
async def test_lfim_live_configs_extract_employment_roles_without_student_projects():
    boards = _epfl_boards()
    html = """
    <div>Postdoc positions</div>
    <div>1. Direct Air Capture</div><p>Postdoc description.</p>
    <p>Ph.D positions</p>
    <p>1. Sustainable Metal Recovery</p><p>First PhD description.</p>
    <p>2. Materials for CO2 Capture</p><p>Second PhD description.</p>
    <p>Master/Semester projects</p>
    <p>1. Student Project</p><p>Course credit only.</p>
    """

    jobs = await _discover_fixture(boards["epfl-lfim"], html)

    assert [job.title for job in jobs] == [
        "Direct Air Capture",
        "Sustainable Metal Recovery",
        "Materials for CO2 Capture",
    ]
