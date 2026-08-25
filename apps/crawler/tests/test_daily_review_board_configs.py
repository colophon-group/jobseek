from __future__ import annotations

import csv
import json
from pathlib import Path

import httpx

from src.core.monitors.dom import dom_discover

DATA_DIR = Path(__file__).resolve().parents[1] / "data"


def _board(slug: str) -> dict:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == slug)
    return {
        "board_url": row["board_url"],
        "metadata": json.loads(row["monitor_config"] or "{}"),
    }


async def _discover_dom_fixture(board: dict, html: str) -> set[str]:
    transport = httpx.MockTransport(lambda request: httpx.Response(200, text=html, request=request))
    async with httpx.AsyncClient(transport=transport) as client:
        return await dom_discover(board, client)


async def test_gcsp_current_empty_marker_is_exact_among_multiple_paragraphs() -> None:
    board = _board("geneva-centre-for-security-policy-main")
    html = """
    <main>
      <div class="textmedia-text__bodytext"><p>About the GCSP.</p></div>
      <div class="textmedia-text__bodytext">
        <p>We currently have no opportunities available.</p>
      </div>
    </main>
    """

    assert await _discover_dom_fixture(board, html) == set()


async def test_eea_grants_current_vacancy_article_is_discovered() -> None:
    board = _board("european-free-trade-association-fmo")
    html = """
    <div class="view view-content-group-list view-id-content_group_list
                view-display-id-vacancy_index_list">
      <div class="view-content">
        <article class="node node--type-vacancy node--view-mode-teaser">
          <a href="/en/fmo/about/vacancies/finance-and-control-officer">Role</a>
        </article>
      </div>
    </div>
    """

    assert await _discover_dom_fixture(board, html) == {
        "https://eeagrants.org/en/fmo/about/vacancies/finance-and-control-officer"
    }


def test_qdrant_uses_verified_ashby_board() -> None:
    board = _board("qdrant-ashby")

    assert board == {
        "board_url": "https://jobs.ashbyhq.com/qdrant.tech",
        "metadata": {"token": "qdrant.tech"},
    }


def test_ecom_uses_canonical_teamtailor_tenant() -> None:
    board = _board("ecom-agroindustrial-global")

    assert board["board_url"] == "https://ecomtradinggroup.teamtailor.com/jobs"
    assert board["metadata"]["preset"] == "teamtailor"
    assert board["metadata"]["feed_url"] == "https://ecomtradinggroup.teamtailor.com/jobs.rss"
