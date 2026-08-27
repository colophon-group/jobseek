"""Regression coverage for the European Aquatics inline board."""

from __future__ import annotations

import csv
import json
from copy import deepcopy
from pathlib import Path

import httpx

from src.core.monitors.inline import discover

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BOARD_SLUG = "european-aquatics-job-offers"
COMPANY_SLUG = "european-aquatics"

# Reduced from the live 2026-08-24 representation. The empty-state widget is
# hidden at every Elementor breakpoint while Sustainability Intern is visible.
LIVE_ACTIVE_HTML = """
<html><body>
  <h1>JOB OFFERS</h1>
  <div class="elementor-element elementor-hidden-desktop elementor-hidden-tablet
              elementor-hidden-mobile elementor-widget elementor-widget-heading">
    <div class="elementor-widget-container">
      <h5>No vacancies are currently available, but we thank you for your interest.</h5>
    </div>
  </div>
  <div class="elementor-element elementor-widget elementor-widget-heading">
    <div class="elementor-widget-container"><h2>Sustainability Intern</h2></div>
  </div>
  <div class="elementor-widget-text-editor">
    <p>European Aquatics is opening applications for an internship of 6 months.</p>
    <p><strong>Location:</strong> Remote or from the office in Nyon, Switzerland</p>
    <p>The intern will support European Aquatics sustainability initiatives.</p>
  </div>
</body></html>
"""

# Reduced from the official 2025-11-14 Wayback capture. The page explicitly
# declared an empty inventory while retaining this closed role in its markup.
ARCHIVED_EMPTY_HTML = """
<html><body>
  <h1>JOB OFFER</h1>
  <h5>Do you want to be part of a dynamic team?</h5>
  <div class="elementor-element elementor-widget elementor-widget-heading">
    <div class="elementor-widget-container">
      <h5>No vacancies are currently available, but we thank you for your interest.</h5>
    </div>
  </div>
  <h2>Aquatics Social Responsibility Project Manager</h2>
  <h4>REPORTS TO: Executive Director</h4>
  <h4>TYPE OF CONTRACT: Full-time</h4>
  <h4>
    LOCATION: Possibility for working arrangements with need to travel on regular
    basis to the European Aquatics HQ (Nyon, Switzerland) and across Europe
    START DATE: Position to be filled as early as possible
  </h4>
  <p>European Aquatics is seeking a project manager to drive its ASR initiatives.</p>
</body></html>
"""

# Reduced from the official 2026-06-10 Wayback capture. The first retained role
# uses WORK ARRANGEMENT rather than LOCATION; the following role has LOCATION.
# Without item bounding, the first role consumed the second role's fields.
ARCHIVED_MIXED_LAYOUT_HTML = """
<html><body>
  <h1>JOB OFFERS</h1>
  <h2>Sport Assistant Water Polo</h2>
  <h4>REPORTS TO: Sport Manager Water Polo</h4>
  <h4>WORK ARRANGEMENT: Office location Belgrade with occasional travel</h4>
  <h4>START DATE: As soon as possible</h4>
  <p>Career Opportunity:</p>
  <p>European Aquatics is looking for a Sport Assistant Water Polo.</p>

  <h2>European Aquatics Service Team</h2>
  <p>Career Opportunity:</p>
  <p>European Aquatics is establishing a new Service Team, based in Belgrade.</p>

  <h2>European Aquatics Academy Project Manager</h2>
  <h4>REPORTS TO: Executive Director</h4>
  <h4>
    LOCATION: Possibility for flexible working arrangements with need to travel on regular
    basis to the European Aquatics HQ (Nyon, Switzerland) and across Europe
    START DATE: Position to be filled as early as possible
  </h4>
  <p>European Aquatics is developing its Academy strategy and courses.</p>
</body></html>
"""


def _csv_row(filename: str, key: str, value: str) -> dict[str, str]:
    with (DATA_DIR / filename).open(newline="", encoding="utf-8") as handle:
        return next(row for row in csv.DictReader(handle) if row[key] == value)


def _board() -> dict:
    row = _csv_row("boards.csv", "board_slug", BOARD_SLUG)
    return {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"]),
    }


async def _discover_fixture(board: dict, html: str):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "europeanaquatics.org"
        assert request.url.path == "/wp-json/wp/v2/pages"
        return httpx.Response(
            200,
            json=[{"content": {"rendered": html}}],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        return await discover(board, client)


async def test_visible_archived_empty_marker_wins_over_retained_role() -> None:
    board = _board()
    board["metadata"] = deepcopy(board["metadata"])
    board["metadata"]["fetch_contains"] = "JOB OFFER"

    jobs = await _discover_fixture(board, ARCHIVED_EMPTY_HTML)

    assert jobs == []


async def test_live_css_hidden_empty_marker_does_not_suppress_visible_role() -> None:
    board = _board()
    board["metadata"] = deepcopy(board["metadata"])
    board["metadata"]["exclude_titles"] = []

    jobs = await _discover_fixture(board, LIVE_ACTIVE_HTML)

    assert [job.title for job in jobs] == ["Sustainability Intern"]
    assert jobs[0].locations == ["Remote or from the office in Nyon, Switzerland"]


async def test_live_hidden_marker_proves_empty_after_archived_roles_are_excluded() -> None:
    board = _board()

    jobs = await _discover_fixture(board, LIVE_ACTIVE_HTML)

    assert jobs == []


async def test_archived_roles_are_bounded_before_optional_location_lookup() -> None:
    board = _board()
    board["metadata"] = deepcopy(board["metadata"])
    board["metadata"]["exclude_titles"] = []

    jobs = await _discover_fixture(board, ARCHIVED_MIXED_LAYOUT_HTML)

    assert [job.title for job in jobs] == [
        "Sport Assistant Water Polo",
        "European Aquatics Service Team",
        "European Aquatics Academy Project Manager",
    ]
    assert jobs[0].locations is None
    assert "Sport Assistant Water Polo" in (jobs[0].description or "")
    assert "Academy" not in (jobs[0].description or "")
    assert jobs[1].locations is None
    assert "Service Team" in (jobs[1].description or "")
    assert "Academy" not in (jobs[1].description or "")
    assert jobs[2].locations == [
        "Possibility for flexible working arrangements with need to travel on regular basis "
        "to the European Aquatics HQ (Nyon, Switzerland) and across Europe"
    ]
    assert "Academy strategy" in (jobs[2].description or "")
    assert "Water Polo" not in (jobs[2].description or "")


def test_board_uses_explicit_empty_and_item_boundary_contracts() -> None:
    metadata = _board()["metadata"]

    assert metadata["fetch_contains"] == "JOB OFFERS"
    assert metadata["fetch_urls"] == [
        "https://europeanaquatics.org/wp-json/wp/v2/pages"
        "?slug=ea-job-offers&_fields=content.rendered"
    ]
    assert metadata["fetch_json_path"] == "[0].content.rendered"
    assert metadata["empty_selector"] == ".elementor-widget-heading h5"
    assert metadata["empty_text"] == "No vacancies are currently available"
    assert metadata["empty_requires_no_jobs"] is True
    assert metadata["item_boundary_tag"] == "h2"
    assert metadata["preserve_single_location"] is True
    assert "Aquatics Social Responsibility Project Manager" in metadata["exclude_titles"]


def test_len_alias_is_structured_company_metadata() -> None:
    company = _csv_row("companies.csv", "slug", COMPANY_SLUG)
    extras = json.loads(company["extras"])

    assert extras["alternateName"] == "LEN"
    assert extras["wikidataId"] == "Q383128"
