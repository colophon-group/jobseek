"""University of Geneva source-coverage and ownership regressions."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from src.core.monitors.dom import (
    _build_url_matcher,
    _extract_pagination_advertised_total,
    _extract_rich_rows_static,
    _validated_pagination_advertised_ranges,
    _validated_rich_rows,
)

DATA_DIR = Path(__file__).parents[1] / "data"


def _geneva_boards() -> dict[str, dict]:
    with (DATA_DIR / "boards.csv").open(encoding="utf-8", newline="") as handle:
        rows = [
            row for row in csv.DictReader(handle) if row["company_slug"] == "university-of-geneva"
        ]
    for row in rows:
        row["monitor_config"] = json.loads(row["monitor_config"])
        row["scraper_config"] = json.loads(row["scraper_config"] or "{}")
    return {row["board_slug"]: row for row in rows}


def test_complete_audited_source_inventory_is_configured():
    assert set(_geneva_boards()) == {
        "university-of-geneva-astronomy-eas-jobs",
        "university-of-geneva-astronomy-eas-phd",
        "university-of-geneva-careers",
        "university-of-geneva-cvu",
        "university-of-geneva-earth-sciences",
        "university-of-geneva-physics-dpnc",
        "university-of-geneva-physics-paruch",
    }


def test_central_board_proves_all_advertised_ranges_and_explicit_zero():
    config = _geneva_boards()["university-of-geneva-careers"]["monitor_config"]
    pagination = config["pagination"]
    advertised_ranges = _validated_pagination_advertised_ranges(pagination)

    assert advertised_ranges is not None
    assert config["url_filter"] == config["url_allowlist"]
    assert pagination["transient_403"] is True
    assert (
        _extract_pagination_advertised_total(
            """
        <ul class="resultsNavTop"><li class="resultsNav">
          <ul><li>1-10</li><li>11-16</li></ul>
        </li></ul>
        """,
            advertised_ranges,
            10,
        )
        == 16
    )
    assert config["empty_states"] == [
        {
            "selector": ".searchresults.advancedcheckbox_intro h2 strong",
            "exact_text": "Actuellement, aucune offre ne correspond à votre recherche.",
            "forbidden_link_selector": ".jobpost_body h2 a[href*='wd_portal.show_job']",
        }
    ]


def test_eas_mixed_directory_keeps_only_unige_owned_stable_ids():
    row = _geneva_boards()["university-of-geneva-astronomy-eas-jobs"]
    config = _validated_rich_rows(row["monitor_config"]["rich_rows"])
    matcher = _build_url_matcher(row["monitor_config"]["url_filter"])
    html = """
    <table class="job">
      <tr><td class="title">Geneva postdoc</td><td class="right">Closing date: 2099-01-01</td></tr>
      <tr><td class="abstract">Work at the University of Geneva.</td></tr>
      <tr><td><a href="javascript:easMail('owner','unige.ch','')">Contact</a></td></tr>
      <tr><td><a href="jobs.jsp?type=job&amp;id=2198">Academic Job Offer 2198</a></td></tr>
    </table>
    <table class="job">
      <tr><td class="title">Bern postdoc</td><td class="right">Closing date: 2099-01-01</td></tr>
      <tr><td class="abstract">Work at the University of Bern.</td></tr>
      <tr><td><a href="jobs.jsp?type=job&amp;id=2193">Academic Job Offer 2193</a></td></tr>
    </table>
    """

    assert config is not None
    jobs = _extract_rich_rows_static(
        html,
        row["board_url"],
        config,
        matcher,
    )
    assert [(job.title, job.url) for job in jobs] == [
        ("Geneva postdoc", "https://eas.unige.ch/jobs.jsp?type=job&id=2198")
    ]


def test_every_department_source_has_a_fail_closed_zero_contract():
    boards = _geneva_boards()
    assert boards["university-of-geneva-cvu"]["monitor_config"]["empty_states"]
    assert boards["university-of-geneva-physics-paruch"]["monitor_config"]["empty_states"]
    assert boards["university-of-geneva-earth-sciences"]["monitor_config"]["require_zero_proof"]
    dpnc = boards["university-of-geneva-physics-dpnc"]["monitor_config"]
    assert dpnc["fetch_contains"] == "Other Positions"
    assert dpnc["empty_selector"] == (
        "article.unige-toc-content:not(:has(p[style*='text-align: center'] strong)) p em"
    )
    assert dpnc["empty_text"] == "Currently no open vacancy"
    assert dpnc["require_zero_proof"]
    for slug in (
        "university-of-geneva-astronomy-eas-jobs",
        "university-of-geneva-astronomy-eas-phd",
    ):
        rich_rows = boards[slug]["monitor_config"]["rich_rows"]
        assert rich_rows["row_required_selector"] == "a[href*='unige.ch']"
        assert rich_rows["row_text_pattern"]
        assert "allow_filtered_empty" not in rich_rows
