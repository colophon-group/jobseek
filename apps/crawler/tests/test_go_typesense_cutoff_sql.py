"""Keep the Go exporter on the same commit-safe CDC cutoff as Python."""

from __future__ import annotations

from pathlib import Path

from src.export_cursor_fence import _CDC_CUTOFF_SQL


def test_go_cutoff_matches_python_exporter() -> None:
    go_sql = Path(__file__).parents[1] / "go" / "typesense-exporter" / "cdc_cutoff.sql"
    assert go_sql.read_text() == _CDC_CUTOFF_SQL
