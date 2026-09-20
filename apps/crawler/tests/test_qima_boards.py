from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def _monitor_config(board_slug: str) -> dict:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == board_slug)
    return json.loads(row["monitor_config"])


def test_cis_uses_structural_job_boundaries() -> None:
    config = _monitor_config("qima-cis")

    assert config["item_boundary"] == {"tag": "div", "attr": "class=name"}
    assert config["steps"][0] == {
        "tag": "div",
        "attr": "class=name",
        "field": "title",
    }
    assert config["steps"][1] == {
        "tag": "div",
        "attr": "class=location",
        "field": "location",
    }


def test_nyce_uses_structural_job_boundaries() -> None:
    config = _monitor_config("qima-nyce")

    assert config["item_boundary"] == {"tag": "dt", "attr": "class=faq_header"}
    assert config["steps"][0] == {
        "tag": "dt",
        "attr": "class=faq_header",
        "field": "title",
    }
