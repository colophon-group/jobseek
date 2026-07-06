"""Regression checks for monitor/scraper registry tables in docs."""

from __future__ import annotations

from pathlib import Path

from src.core.monitors import all_monitor_types
from src.core.scrapers import _REGISTRY as scraper_registry

ROOT = Path(__file__).resolve().parents[3]


def _table_type_names(doc_path: str, heading: str) -> frozenset[str]:
    lines = (ROOT / doc_path).read_text().splitlines()
    try:
        heading_index = lines.index(heading)
    except ValueError as exc:
        raise AssertionError(f"{doc_path} is missing heading {heading!r}") from exc

    table_lines: list[str] = []
    for line in lines[heading_index + 1 :]:
        if not line.strip():
            if table_lines:
                break
            continue
        if not line.startswith("|"):
            if table_lines:
                break
            continue
        table_lines.append(line)

    rows = [
        row for row in table_lines if not row.startswith("|---") and not row.startswith("| Type")
    ]
    names: list[str] = []
    for row in rows:
        cells = [cell.strip() for cell in row.strip("|").split("|")]
        name_cell = next(
            (cell for cell in cells if cell.startswith("`") and cell.endswith("`")),
            None,
        )
        if name_cell is not None:
            names.append(name_cell.strip("`"))

    assert len(names) == len(set(names)), (
        f"{doc_path} {heading} table contains duplicate type names"
    )
    return frozenset(names)


def _assert_doc_table_matches_registry(
    doc_path: str, heading: str, expected: frozenset[str]
) -> None:
    actual = _table_type_names(doc_path, heading)
    assert actual == expected, (
        f"{doc_path} {heading} drifted from registry: "
        f"missing={sorted(expected - actual)}, extra={sorted(actual - expected)}"
    )


def test_monitor_tables_list_all_registered_monitors() -> None:
    expected = all_monitor_types()
    _assert_doc_table_matches_registry(
        "docs/04-monitors-and-scrapers.md", "### Monitor Types", expected
    )
    _assert_doc_table_matches_registry(
        "docs/07-system-design.md", "### All Monitor Types", expected
    )


def test_scraper_tables_list_all_registered_scrapers() -> None:
    expected = frozenset(scraper_registry)
    _assert_doc_table_matches_registry(
        "docs/04-monitors-and-scrapers.md", "### Scraper Types", expected
    )
    _assert_doc_table_matches_registry(
        "docs/07-system-design.md", "### All Scraper Types", expected
    )
