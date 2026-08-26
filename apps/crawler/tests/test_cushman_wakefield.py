from __future__ import annotations

import csv
import json
import re

from src.shared.constants import DATA_DIR


def _boards() -> dict[str, dict[str, dict]]:
    with (DATA_DIR / "boards.csv").open(newline="") as handle:
        rows = [row for row in csv.DictReader(handle) if row["company_slug"] == "cushman-wakefield"]
    return {
        row["board_slug"]: {
            "monitor": json.loads(row["monitor_config"] or "{}"),
            "scraper": json.loads(row["scraper_config"] or "{}"),
        }
        for row in rows
    }


def test_workday_uses_the_live_proven_exhaustive_country_partition() -> None:
    board = _boards()["cushman-wakefield-careers"]

    assert board["monitor"]["split_facet"] == "Location_Country"


def test_waf_gated_regional_sources_route_monitor_and_scraper_through_proxy() -> None:
    boards = _boards()
    proxied = {
        "cushman-wakefield-argentina",
        "cushman-wakefield-chile",
        "cushman-wakefield-colombia",
        "cushman-wakefield-costa-rica",
        "cushman-wakefield-mexico",
        "cushman-wakefield-peru",
    }

    for slug in proxied:
        assert boards[slug]["monitor"]["proxy"] is True
        assert boards[slug]["scraper"]["proxy"] is True


def test_computrabajo_rank_fragments_are_not_job_identity() -> None:
    boards = _boards()
    ranked_sources = {
        "cushman-wakefield-argentina",
        "cushman-wakefield-colombia",
        "cushman-wakefield-costa-rica",
    }
    decorated = "https://example.com/job/provider-id#lc=CompanyListOffers-Score-7"

    for slug in ranked_sources:
        transform = boards[slug]["monitor"]["url_transform"]
        assert re.sub(transform["find"], transform["replace"], decorated) == (
            "https://example.com/job/provider-id"
        )


def test_every_regional_listing_authenticates_total_and_currentness() -> None:
    boards = _boards()

    for slug, board in boards.items():
        if slug == "cushman-wakefield-careers":
            continue
        monitor = board["monitor"]
        assert set(monitor["advertised_total"]) == {"selector", "regex"}
        assert monitor["require_jsonld_jobposting"] is True


def test_every_paginated_pandape_listing_fails_closed_on_403() -> None:
    boards = _boards()
    paginated = {
        "cushman-wakefield-brazil",
        "cushman-wakefield-chile",
        "cushman-wakefield-mexico",
        "cushman-wakefield-peru",
    }

    for slug in paginated:
        pagination = boards[slug]["monitor"]["pagination"]
        assert pagination["transient_403"] is True
        assert pagination["max_pages"] == 100
