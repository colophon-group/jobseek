"""Stable board contracts for Vertiv and its acquired businesses."""

from __future__ import annotations

import csv
import json
from pathlib import Path

_BOARDS = Path(__file__).parents[1] / "data" / "boards.csv"


def test_strategic_thermal_filters_placeholder_content_not_future_job_titles():
    with _BOARDS.open(newline="") as handle:
        row = next(
            row for row in csv.DictReader(handle) if row["board_slug"] == "vertiv-strategic-thermal"
        )
    metadata = json.loads(row["monitor_config"])

    assert "exclude_titles" not in metadata
    assert metadata["exclude_description_regex"] == r"^More details to come[.]"
