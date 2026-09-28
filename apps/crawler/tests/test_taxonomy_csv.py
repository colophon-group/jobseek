"""CSV edge cases shared by runtime resolvers and the slim workspace CLI."""

from __future__ import annotations

import csv

import pytest
from click.testing import CliRunner

from src.core import occupation_resolve, seniority_resolve, technology_resolve
from src.shared.csv_io import read_csv
from src.workspace.commands import help as help_command
from src.workspace.commands import taxonomy


@pytest.fixture
def csv_dir(tmp_path, monkeypatch):
    loaders = [
        occupation_resolve._load_aliases,
        occupation_resolve._load_token_aliases,
        seniority_resolve._load_seniority_slugs,
        technology_resolve._load_patterns,
        technology_resolve._load_patterns_with_keywords,
    ]
    for module in [occupation_resolve, seniority_resolve, technology_resolve, taxonomy]:
        monkeypatch.setattr(module, "get_data_dir", lambda: tmp_path)
    monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
    for loader in loaders:
        loader.cache_clear()
    yield tmp_path
    for loader in loaders:
        loader.cache_clear()


def write_fixture(path, headers, rows):
    with path.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.writer(file, lineterminator="\r\n")
        writer.writerow(headers)
        writer.writerows(rows)


def test_read_csv_preserves_unicode_quoted_newlines_and_empty_cells(csv_dir):
    path = csv_dir / "occupations.csv"
    write_fixture(path, ["slug", "en", "aliases"], [["engineer", 'Ingénieur, "R&D"\r\nII', ""]])

    assert read_csv(path) == (
        ["slug", "en", "aliases"],
        [{"slug": "engineer", "en": 'Ingénieur, "R&D"\r\nII', "aliases": ""}],
    )


def test_occupation_aliases_keep_normalization_and_last_row_precedence(csv_dir):
    write_fixture(
        csv_dir / "occupations.csv",
        ["slug", "parent", "domain", "en", "fr", "aliases"],
        [
            ["engineer", "", "", "Engineer", "Ingénieur", "shared|R&D, Engineer"],
            ["lead-engineer", "engineer", "", "Lead Engineer", "", "shared"],
        ],
    )

    assert occupation_resolve.match_occupation("Ingénieur") == "engineer"
    assert occupation_resolve.match_occupation("R&D, Engineer") == "engineer"
    assert occupation_resolve.match_occupation("shared") == "lead-engineer"


def test_seniority_preserves_csv_order(csv_dir):
    write_fixture(csv_dir / "seniority.csv", ["slug", "en"], [["senior", ""], ["entry", "Entry"]])

    assert seniority_resolve._load_seniority_slugs() == ["senior", "entry"]


def test_technology_patterns_keep_empty_rows_case_flags_and_order(csv_dir):
    write_fixture(
        csv_dir / "technologies.csv",
        ["slug", "patterns", "flags"],
        [["blank", "", ""], ["r", "R", "cs"], ["cpp", "C++|C plus plus", ""]],
    )

    assert technology_resolve.match_technologies("<p>C++ and R</p>") == ["r", "cpp"]
    assert technology_resolve.match_technologies("r and c PLUS plus") == ["cpp"]


@pytest.mark.parametrize("key, value", [("slug", "data-engineer"), ("id", "007")])
def test_taxonomy_search_and_validate_localized_csv(csv_dir, key, value):
    write_fixture(
        csv_dir / "sample.csv",
        [key, "en", "fr", "aliases"],
        [[value, "Data Engineer", "Ingénieur données", "ETL, specialist"]],
    )
    runner = CliRunner()

    search = runner.invoke(taxonomy.taxonomy_group, ["search", "sample", "ETL, specialist"])
    assert search.exit_code == 0, search.output
    assert value in search.output
    assert "[ 80]" in search.output
    valid = runner.invoke(taxonomy.taxonomy_group, ["validate", "sample"])
    assert valid.exit_code == 0, valid.output
    assert f"1 entries, format={'slug' if key == 'slug' else 'id'}" in valid.output


def test_taxonomy_validation_reports_duplicates_missing_names_and_aliases(csv_dir):
    write_fixture(
        csv_dir / "sample.csv",
        ["slug", "en", "aliases"],
        [["bad slug", "", "same"], ["bad slug", "Name", "same"]],
    )
    result = CliRunner().invoke(taxonomy.taxonomy_group, ["validate", "sample"])

    assert result.exit_code == 1
    assert "duplicate slug" in result.output
    assert "invalid slug format" in result.output
    assert "missing en translation" in result.output
    assert "Ambiguous alias" in result.output


def test_taxonomy_legacy_search_and_validation(csv_dir):
    write_fixture(
        csv_dir / "sample.csv",
        ["id", "name", "keywords"],
        [["001", "Engineering", "code, software"]],
    )
    runner = CliRunner()
    result = runner.invoke(taxonomy.taxonomy_group, ["search", "sample", "software"])

    assert result.exit_code == 0, result.output
    assert "001" in result.output
    assert "keyword: software" in result.output
    assert runner.invoke(taxonomy.taxonomy_group, ["validate", "sample"]).exit_code == 0


def test_taxonomy_help_uses_csv_reader(csv_dir, capsys):
    write_fixture(
        csv_dir / "occupations.csv", ["slug", "en", "aliases"], [["engineer", "Engineer", ""]]
    )
    write_fixture(csv_dir / "seniority.csv", ["slug", "en", "aliases"], [["senior", "Senior", ""]])
    write_fixture(csv_dir / "industries.csv", ["id", "en", "de"], [["01", "Tech", "Technologie"]])

    help_command._show_occupations()
    help_command._show_seniority()
    help_command._show_industries()

    output = capsys.readouterr().out
    assert "1 occupations total" in output
    assert "1 seniority levels total" in output
    assert "1 industries total" in output
    assert "Technologie" in output
