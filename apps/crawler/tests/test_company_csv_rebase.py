from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / ".github/scripts/merge_company_csv_rebase.py"
SPEC = importlib.util.spec_from_file_location("merge_company_csv_rebase", SCRIPT)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)
merge_csv_text = MODULE.merge_csv_text


def test_merges_only_feature_row_changes_and_sorts() -> None:
    header = "slug,name,website\n"
    parent = header + "alpha,Alpha,https://alpha.example\ncharlie,Charlie,https://charlie.example\n"
    current = parent + "bravo,Bravo,https://bravo.example\n"
    feature = parent + "delta,Delta,https://delta.example\n"

    merged = merge_csv_text(current, parent, feature, "apps/crawler/data/companies.csv")

    assert merged.splitlines()[1:] == [
        "alpha,Alpha,https://alpha.example",
        "bravo,Bravo,https://bravo.example",
        "charlie,Charlie,https://charlie.example",
        "delta,Delta,https://delta.example",
    ]


def test_reorder_only_commit_still_normalizes_current_rows() -> None:
    header = "slug,name\n"
    parent = header + "bravo,Bravo\nalpha,Alpha\n"
    feature = header + "alpha,Alpha\nbravo,Bravo\n"
    current = header + "charlie,Charlie\nbravo,Bravo\nalpha,Alpha\n"

    merged = merge_csv_text(current, parent, feature, "apps/crawler/data/companies.csv")

    assert merged.splitlines()[1:] == ["alpha,Alpha", "bravo,Bravo", "charlie,Charlie"]


def test_preserves_independent_updates() -> None:
    header = "slug,name,website\n"
    parent = (
        header + "alpha,Alpha,https://old-alpha.example\nbravo,Bravo,https://old-bravo.example\n"
    )
    current = (
        header + "bravo,Bravo,https://new-bravo.example\nalpha,Alpha,https://old-alpha.example\n"
    )
    feature = (
        header + "bravo,Bravo,https://old-bravo.example\nalpha,Alpha,https://new-alpha.example\n"
    )

    merged = merge_csv_text(current, parent, feature, "apps/crawler/data/companies.csv")

    assert "alpha,Alpha,https://new-alpha.example" in merged
    assert "bravo,Bravo,https://new-bravo.example" in merged


def test_rejects_same_row_conflict() -> None:
    header = "slug,name\n"
    parent = header + "alpha,Old\n"
    current = header + "alpha,Upstream\n"
    feature = header + "alpha,Feature\n"

    with pytest.raises(ValueError, match="both sides changed row"):
        merge_csv_text(current, parent, feature, "apps/crawler/data/companies.csv")


def test_sorts_boards_by_company_and_board_slug() -> None:
    header = "company_slug,board_slug,board_url\n"
    parent = header + "zeta,zeta-main,https://zeta.example\n"
    current = parent + "alpha,alpha-z,https://alpha.example/z\n"
    feature = parent + "alpha,alpha-a,https://alpha.example/a\n"

    merged = merge_csv_text(current, parent, feature, "apps/crawler/data/boards.csv")

    assert merged.splitlines()[1:] == [
        "alpha,alpha-a,https://alpha.example/a",
        "alpha,alpha-z,https://alpha.example/z",
        "zeta,zeta-main,https://zeta.example",
    ]
