from __future__ import annotations

import hashlib
import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

from src.core.monitors import all_monitor_types
from src.core.scrapers import all_scraper_types


def _load_validator() -> ModuleType:
    path = Path(__file__).resolve().parents[1] / "scripts" / "validate_data_csv.py"
    spec = importlib.util.spec_from_file_location("validate_data_csv", path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _load_validator()


def test_csv_validator_types_match_runtime_registries():
    assert all_monitor_types() == validator.ALLOWED_MONITOR_TYPES
    assert {"", *all_scraper_types()} == validator.ALLOWED_SCRAPER_TYPES


def test_occupation_header_accepts_extra_locale_columns():
    validator.validate_header(
        "occupations.csv",
        ["slug", "parent", "domain", "en", "de", "fr", "it", "pl", "es", "aliases"],
    )


def test_occupation_header_rejects_non_locale_extra_columns():
    with pytest.raises(validator.ValidationError, match="unexpected non-locale column"):
        validator.validate_header(
            "occupations.csv",
            ["slug", "parent", "domain", "en", "de", "fr", "it", "notes", "aliases"],
        )


def _write_census(path: Path, *, boards_sha256: object, boards_row_count: object) -> Path:
    path.write_text(
        json.dumps(
            {
                "input": {
                    "boards_row_count": boards_row_count,
                    "boards_sha256": boards_sha256,
                }
            }
        ),
        encoding="utf-8",
    )
    return path


def test_census_freshness_accepts_current_fixture(tmp_path: Path):
    boards = tmp_path / "boards.csv"
    boards.write_bytes(b"header\nrow-one\nrow-two\n")
    rows = [{"row": "one"}, {"row": "two"}]
    census = _write_census(
        tmp_path / "census.json",
        boards_sha256=hashlib.sha256(boards.read_bytes()).hexdigest(),
        boards_row_count=len(rows),
    )

    validator.validate_census_freshness(boards, rows, census)


def test_census_freshness_rejects_hash_mismatch(tmp_path: Path):
    boards = tmp_path / "boards.csv"
    boards.write_bytes(b"header\nrow\n")
    census = _write_census(
        tmp_path / "census.json",
        boards_sha256="0" * 64,
        boards_row_count=1,
    )

    with pytest.raises(validator.ValidationError, match=r"boards_sha256.*regenerate"):
        validator.validate_census_freshness(boards, [{"row": "one"}], census)


def test_census_freshness_rejects_row_count_mismatch(tmp_path: Path):
    boards = tmp_path / "boards.csv"
    boards.write_bytes(b"header\nrow\n")
    census = _write_census(
        tmp_path / "census.json",
        boards_sha256=hashlib.sha256(boards.read_bytes()).hexdigest(),
        boards_row_count=2,
    )

    with pytest.raises(validator.ValidationError, match=r"boards_row_count.*regenerate"):
        validator.validate_census_freshness(boards, [{"row": "one"}], census)


@pytest.mark.parametrize(
    "contents",
    [
        "not json",
        "[]",
        '{"input": []}',
        '{"input": {"boards_sha256": 1, "boards_row_count": 0}}',
        '{"input": {"boards_sha256": "' + "0" * 64 + '", "boards_row_count": true}}',
    ],
)
def test_census_freshness_rejects_malformed_shape(tmp_path: Path, contents: str):
    boards = tmp_path / "boards.csv"
    boards.write_bytes(b"header\n")
    census = tmp_path / "census.json"
    census.write_text(contents, encoding="utf-8")

    with pytest.raises(validator.ValidationError, match="regenerate"):
        validator.validate_census_freshness(boards, [], census)


def test_census_freshness_rejects_missing_fixture(tmp_path: Path):
    boards = tmp_path / "boards.csv"
    boards.write_bytes(b"header\n")

    with pytest.raises(validator.ValidationError, match="regenerate"):
        validator.validate_census_freshness(boards, [], tmp_path / "missing.json")
