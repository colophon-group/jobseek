"""Bind native rich monitor statements to the production Python writer."""

from __future__ import annotations

import re
from pathlib import Path

from src.processing.scrape import _UPSERT_DESCRIPTION
from src.queries.monitor import (
    _BATCH_UPDATE_RICH_CONTENT,
    _DIFF_BATCH,
    _INSERT_RICH_JOB,
)

CRAWLER = Path(__file__).resolve().parents[1]


def test_native_rich_monitor_diff_and_insert_are_exact_python_statements():
    native = CRAWLER / "go/ordinary-queue"
    assert (native / "rich_monitor_diff.sql").read_text() == _DIFF_BATCH
    assert (native / "rich_monitor_insert.sql").read_text() == _INSERT_RICH_JOB
    assert (native / "rich_monitor_description.sql").read_text() == _UPSERT_DESCRIPTION


def _assignments(statement: str) -> dict[str, str]:
    """Split the SQL SET list while retaining CASE and COALESCE commas."""
    body = statement.split("SET ", 1)[1]
    body = re.split(r"\n(?:FROM|WHERE) ", body, maxsplit=1)[0]
    assignments = []
    depth = 0
    start = 0
    for i, char in enumerate(body):
        if char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
        elif char == "," and depth == 0:
            assignments.append(body[start:i])
            start = i + 1
    assignments.append(body[start:])
    return {
        column.strip(): " ".join(expression.split())
        for column, expression in (assignment.split("=", 1) for assignment in assignments)
    }


def test_native_rich_refresh_retains_exact_python_column_expressions():
    source = (CRAWLER / "go/ordinary-queue/rich_monitor.go").read_text()
    native = source.split("const refreshRichMonitorSQL = `", 1)[1].split("`", 1)[0]
    expected = _BATCH_UPDATE_RICH_CONTENT.replace("jp.", "")
    # The single-row native statement uses shared ContentFields argument order
    # instead of the Python COPY table, without changing replacement/NULL rules.
    columns = [
        "employment_type",
        "titles",
        "locales",
        "location_ids",
        "location_types",
        "technology_ids",
        "salary_min",
        "salary_max",
        "salary_currency",
        "salary_period",
        "salary_eur",
        "experience_min",
        "experience_max",
        "occupation_id",
        "seniority_id",
    ]
    for number, column in enumerate(columns, 2):
        expected = re.sub(rf"\bu\.{column}\b", f"${number}", expected)
    # Explicit casts are needed for pgx to describe standalone nullable params.
    expected = expected.replace("$13 IS NULL", "$13::numeric IS NULL")
    expected = expected.replace("$14 IS NULL", "$14::numeric IS NULL")
    assert _assignments(native) == _assignments(expected)
