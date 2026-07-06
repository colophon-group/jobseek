from __future__ import annotations

import ast
from pathlib import Path

import src.bootstrap as bootstrap
import src.indexnow as indexnow

SQL_METHODS = {"execute", "executemany", "fetch", "fetchrow", "fetchval"}
SQL_TEMPLATE_SUFFIXES = ("_SQL", "_QUERY")
SQL_WORDS = (
    "SELECT",
    "INSERT",
    "UPDATE",
    "DELETE",
    "CREATE TEMP TABLE",
    "ON CONFLICT",
)


def _source_root() -> Path:
    return Path(__file__).resolve().parents[1] / "src"


def _joined_literal_text(node: ast.JoinedStr) -> str:
    parts = [
        part.value
        for part in node.values
        if isinstance(part, ast.Constant) and isinstance(part.value, str)
    ]
    return "".join(parts).upper()


def _contains_sql_literal(node: ast.JoinedStr) -> bool:
    text = _joined_literal_text(node)
    return any(word in text for word in SQL_WORDS)


def _assignment_names(node: ast.Assign | ast.AnnAssign) -> list[str]:
    targets: list[ast.expr] = (
        [node.target] if isinstance(node, ast.AnnAssign) else list(node.targets)
    )

    names: list[str] = []
    for target in targets:
        if isinstance(target, ast.Name):
            names.append(target.id)
    return names


def test_crawler_sql_calls_do_not_use_f_string_templates():
    violations: list[str] = []
    for path in (
        _source_root() / "exporter.py",
        _source_root() / "bootstrap.py",
        _source_root() / "indexnow.py",
    ):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Attribute) or node.func.attr not in SQL_METHODS:
                continue
            first_arg = node.args[0]
            if isinstance(first_arg, ast.JoinedStr) and _contains_sql_literal(first_arg):
                violations.append(str(path.relative_to(_source_root())) + ":" + str(node.lineno))

    assert violations == []


def test_sql_template_constants_do_not_use_f_strings():
    violations: list[str] = []
    for path in (
        _source_root() / "exporter.py",
        _source_root() / "bootstrap.py",
        _source_root() / "indexnow.py",
    ):
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Assign | ast.AnnAssign):
                continue
            value = node.value
            if not isinstance(value, ast.JoinedStr) or not _contains_sql_literal(value):
                continue
            for name in _assignment_names(node):
                if name.endswith(SQL_TEMPLATE_SUFFIXES):
                    violations.append(
                        str(path.relative_to(_source_root())) + ":" + str(node.lineno)
                    )

    assert violations == []


def test_bootstrap_sql_templates_match_column_contracts():
    board_columns = ", ".join(bootstrap._BOARD_COLUMNS)
    posting_columns = ", ".join(bootstrap._POSTING_COLUMNS_SUPA)

    assert "SELECT " + board_columns + " FROM job_board ORDER BY id" == bootstrap._BOARD_SELECT_SQL
    assert "INSERT INTO job_board (" + board_columns + ")" in bootstrap._IMPORT_BOARDS_UPSERT_SQL
    assert "SELECT " + board_columns + " FROM _import_boards" in bootstrap._IMPORT_BOARDS_UPSERT_SQL
    assert "id = EXCLUDED.id" not in bootstrap._IMPORT_BOARDS_UPSERT_SQL

    assert (
        "SELECT " + posting_columns + " FROM job_posting ORDER BY id OFFSET $1 LIMIT $2"
    ) == bootstrap._POSTING_SELECT_BATCH_SQL
    assert (
        "INSERT INTO job_posting (" + posting_columns + ")" in bootstrap._IMPORT_POSTINGS_UPSERT_SQL
    )
    assert (
        "SELECT " + posting_columns + " FROM _import_postings"
        in bootstrap._IMPORT_POSTINGS_UPSERT_SQL
    )
    assert "id = EXCLUDED.id" not in bootstrap._IMPORT_POSTINGS_UPSERT_SQL


def test_indexnow_company_select_template_matches_hash_fields():
    assert (
        tuple("c." + field for field in indexnow._COMPANY_STABLE_FIELDS)
        == indexnow._COMPANY_SELECT_COLUMNS
    )
    assert (
        "SELECT c.id::text AS id, c.slug, "
        + ", ".join(indexnow._COMPANY_SELECT_COLUMNS)
        + " FROM company c"
    ) == indexnow._COMPANY_SELECT_SQL
