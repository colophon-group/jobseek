"""Live-shape contracts for EPFL's first-party board configurations."""

from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx
import pytest

from src.core.monitors.inline import discover

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"


def _epfl_boards() -> dict[str, dict]:
    with _BOARDS_PATH.open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "epfl"]
    return {
        row["board_slug"]: {
            "board_url": row["board_url"],
            "metadata": json.loads(row["monitor_config"] or "{}"),
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
        "epfl-hqc",
        "epfl-hylab",
        "epfl-lanes",
        "epfl-lfim",
        "epfl-mesobio",
    } <= boards.keys()
    assert {"epfl-math", "epfl-phd-edpy", "epfl-quantum"}.isdisjoint(boards)


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
        "<h3>Institute</h3><table><tr><td>Future Director</td>"
        "<td>A newly posted molecular-science project.</td></tr></table>",
    )

    assert [(job.title, job.description) for job in jobs] == [
        ("Future Director", "A newly posted molecular-science project.")
    ]


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
async def test_quantum_live_configs_keep_hqc_roles_and_de_duplicate_central_hylab_role():
    boards = _epfl_boards()
    hqc = await _discover_fixture(
        boards["epfl-hqc"],
        """
        <h3>Postdoc positions available in HQC lab</h3>
        <div>Hybrid Circuit Role</div><p>Applications are continuously evaluated.</p>
        <div>Metamaterial Role</div><p>Applications are continuously evaluated.</p>
        <h3>PhD positions available in HQC lab</h3>
        """,
    )
    hylab = await _discover_fixture(
        boards["epfl-hylab"],
        """
        <ul>
          <li>SB – HQC aggregate role</li>
          <li>STI – HYLAB 1. PhD in Cavity Quantum Electrodynamics
              2. PhD in integrated photonic-spintronic devices</li>
        </ul>
        """,
    )

    titles = [job.title for job in [*hqc, *hylab]]
    assert titles == [
        "Hybrid Circuit Role",
        "Metamaterial Role",
        "PhD in Cavity Quantum Electrodynamics",
    ]
    assert all("integrated photonic-spintronic" not in title for title in titles)


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
