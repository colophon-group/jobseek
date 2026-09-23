from __future__ import annotations

import csv
import json
from pathlib import Path

from src.core.jsonld import parse_rendered_html
from src.processing.scrape import _apply_defaults

BOARDS_CSV = Path(__file__).resolve().parents[1] / "data" / "boards.csv"


def _luxembourg_scraper_config() -> dict:
    with BOARDS_CSV.open(encoding="utf-8", newline="") as handle:
        row = next(
            row
            for row in csv.DictReader(handle)
            if row["board_slug"] == "fressnapf-maxizoo-luxembourg"
        )
    return json.loads(row["scraper_config"])


def test_luxembourg_locations_replace_incorrect_provider_country():
    config = _luxembourg_scraper_config()
    assert config["ignore_locations"] is True
    assert len(config["defaults_by_url"]) == 8
    url = (
        "https://jobs.fressnapf.lu/offer-redirect/"
        "?offerApiId=NjNiMTYwZTYtYTE0OC00YTY4LTg1NzctMzFkNTg1Njk3MmZi"
        "&showApplicationForm=false"
    )
    html = """<script type="application/ld+json">
    {"@type":"JobPosting","title":"Store role",
     "jobLocation":{"address":{"addressLocality":"Grevenmacher","addressCountry":"DE"}}}
    </script>"""

    content = parse_rendered_html(url, config, html)
    content = _apply_defaults(content, config)

    assert content.locations == ["Grevenmacher, LU"]


def test_luxembourg_unknown_future_url_gets_safe_country_default():
    config = _luxembourg_scraper_config()
    html = """<script type="application/ld+json">
    {"@type":"JobPosting","title":"Future role",
     "jobLocation":{"address":{"addressLocality":"Wrong","addressCountry":"DE"}}}
    </script>"""

    content = parse_rendered_html("https://jobs.fressnapf.lu/offer-redirect/?new", config, html)
    content = _apply_defaults(content, config)

    assert content.locations == ["Luxembourg, LU"]
