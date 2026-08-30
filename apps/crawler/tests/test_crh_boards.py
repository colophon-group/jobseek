from __future__ import annotations

import csv
import json
from pathlib import Path

import pytest

from src.shared.nextdata import extract_field

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def _danucem_employment_type_spec() -> dict[str, object]:
    with _BOARDS.open(newline="", encoding="utf-8") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "crh-careers-danucem"
        )
    config = json.loads(row["monitor_config"])
    return config["fields"]["employment_type"]


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Plný úväzok", "full_time"),
        ("Internship, stáž", "internship"),
        ("Full-time", "full_time"),
        ("Unknown provider value", None),
    ],
)
def test_danucem_employment_types_are_mapped_fail_closed(label: str, expected: str | None) -> None:
    item = {"employmentForms": [{"name": label}]}

    assert extract_field(item, _danucem_employment_type_spec()) == expected
