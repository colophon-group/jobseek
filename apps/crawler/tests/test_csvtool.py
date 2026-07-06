from __future__ import annotations

import ast
from pathlib import Path

import pytest
import structlog

from src.csvtool import (
    _read_csv,
    board_add,
    board_del,
    company_add,
    company_del,
    company_description_set,
)
from src.workspace.errors import (
    BoardNotFoundError,
    InvalidSlugError,
    MissingRequiredFieldError,
    NothingToUpdateError,
    SlugNotFoundError,
)

COMPANIES_HEADER = "slug,name,website,logo_url,icon_url,logo_type\n"
BOARDS_HEADER = (
    "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
)


class TestCompanyAdd:
    def _setup(self, tmp_path, monkeypatch, companies="", boards=""):
        (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
        (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.csvtool.get_data_dir", lambda: tmp_path)

    def test_add_stub(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        company_add("test-co")
        _, rows = _read_csv(tmp_path / "companies.csv")
        assert len(rows) == 1
        assert rows[0]["slug"] == "test-co"
        assert rows[0]["name"] == ""

    def test_add_with_fields(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        company_add("test-co", name="Test", website="https://test.com")
        _, rows = _read_csv(tmp_path / "companies.csv")
        assert rows[0]["name"] == "Test"
        assert rows[0]["website"] == "https://test.com"

    def test_update_existing(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,,,,\n")
        company_add("test-co", name="Test Company", website="https://test.com")
        _, rows = _read_csv(tmp_path / "companies.csv")
        assert len(rows) == 1
        assert rows[0]["name"] == "Test Company"
        assert rows[0]["website"] == "https://test.com"

    def test_update_preserves_untouched_fields(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,https://test.com,,\n")
        company_add("test-co", logo_url="https://test.com/logo.svg")
        _, rows = _read_csv(tmp_path / "companies.csv")
        assert rows[0]["name"] == "Test"
        assert rows[0]["website"] == "https://test.com"
        assert rows[0]["logo_url"] == "https://test.com/logo.svg"

    def test_sets_logo_type(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        company_add("test-co", logo_type="wordmark")
        _, rows = _read_csv(tmp_path / "companies.csv")
        assert rows[0]["logo_type"] == "wordmark"

    def test_invalid_slug(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(InvalidSlugError):
            company_add("INVALID")

    def test_existing_no_updates(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,,,\n")
        with pytest.raises(NothingToUpdateError):
            company_add("test-co")

    def test_preserves_other_companies(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="existing,Existing,https://existing.com,,\n")
        company_add("new-co", name="New")
        _, rows = _read_csv(tmp_path / "companies.csv")
        assert len(rows) == 2
        assert rows[0]["slug"] == "existing"
        assert rows[1]["slug"] == "new-co"


class TestCompanyDel:
    def _setup(self, tmp_path, monkeypatch, companies="", boards=""):
        (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
        (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.csvtool.get_data_dir", lambda: tmp_path)

    def test_delete_company(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,,,\n")
        company_del("test-co")
        _, rows = _read_csv(tmp_path / "companies.csv")
        assert len(rows) == 0

    def test_cascade_boards(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            companies="test-co,Test,,,\n",
            boards="test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n",
        )
        company_del("test-co")
        _, co_rows = _read_csv(tmp_path / "companies.csv")
        _, bd_rows = _read_csv(tmp_path / "boards.csv")
        assert len(co_rows) == 0
        assert len(bd_rows) == 0

    def test_cascade_multiple_boards(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            companies="test-co,Test,,,\n",
            boards=(
                "test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n"
                "test-co,test-co-eng,https://test.com/careers,lever,,,\n"
            ),
        )
        company_del("test-co")
        _, bd_rows = _read_csv(tmp_path / "boards.csv")
        assert len(bd_rows) == 0

    def test_nonexistent(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(SlugNotFoundError):
            company_del("nonexistent")

    def test_preserves_other_companies(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            companies="test-co,Test,,,\nother,Other,,,\n",
            boards=(
                "test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n"
                "other,other-careers,https://other.com/jobs,lever,,,\n"
            ),
        )
        company_del("test-co")
        _, co_rows = _read_csv(tmp_path / "companies.csv")
        _, bd_rows = _read_csv(tmp_path / "boards.csv")
        assert len(co_rows) == 1
        assert co_rows[0]["slug"] == "other"
        assert len(bd_rows) == 1
        assert bd_rows[0]["company_slug"] == "other"


class TestBoardAdd:
    def _setup(self, tmp_path, monkeypatch, companies="", boards=""):
        (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
        (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.csvtool.get_data_dir", lambda: tmp_path)

    def test_add_board(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,,,\n")
        board_add(
            "test-co",
            board_slug="test-co-careers",
            board_url="https://test.com/jobs",
            monitor_type="greenhouse",
        )
        _, rows = _read_csv(tmp_path / "boards.csv")
        assert len(rows) == 1
        assert rows[0]["company_slug"] == "test-co"
        assert rows[0]["board_slug"] == "test-co-careers"
        assert rows[0]["board_url"] == "https://test.com/jobs"
        assert rows[0]["monitor_type"] == "greenhouse"

    def test_add_board_all_fields(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,,,\n")
        board_add(
            "test-co",
            board_slug="test-co-careers",
            board_url="https://test.com/jobs",
            monitor_type="sitemap",
            monitor_config='{"include": "/jobs/"}',
            scraper_type="json-ld",
            scraper_config='{"timeout": 10}',
        )
        _, rows = _read_csv(tmp_path / "boards.csv")
        assert rows[0]["monitor_config"] == '{"include": "/jobs/"}'
        assert rows[0]["scraper_type"] == "json-ld"
        assert rows[0]["scraper_config"] == '{"timeout": 10}'

    def test_update_existing_board(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            companies="test-co,Test,,,\n",
            boards="test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n",
        )
        board_add("test-co", board_url="https://test.com/jobs", scraper_type="json-ld")
        _, rows = _read_csv(tmp_path / "boards.csv")
        assert len(rows) == 1
        assert rows[0]["monitor_type"] == "greenhouse"
        assert rows[0]["scraper_type"] == "json-ld"

    def test_company_not_found(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(SlugNotFoundError):
            board_add("nonexistent", board_url="https://test.com/jobs", monitor_type="greenhouse")

    def test_missing_board_url_on_create(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,,,\n")
        with pytest.raises(MissingRequiredFieldError):
            board_add("test-co", monitor_type="greenhouse")

    def test_existing_no_updates(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            companies="test-co,Test,,,\n",
            boards="test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n",
        )
        with pytest.raises(NothingToUpdateError):
            board_add("test-co", board_url="https://test.com/jobs")

    def test_preserves_other_boards(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            companies="test-co,Test,,,\n",
            boards="test-co,test-co-old,https://test.com/old,greenhouse,,,\n",
        )
        board_add(
            "test-co",
            board_slug="test-co-new",
            board_url="https://test.com/new",
            monitor_type="lever",
        )
        _, rows = _read_csv(tmp_path / "boards.csv")
        assert len(rows) == 2


class TestBoardDel:
    def _setup(self, tmp_path, monkeypatch, companies="", boards=""):
        (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
        (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.csvtool.get_data_dir", lambda: tmp_path)

    def test_delete_specific_board(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            boards="test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n",
        )
        board_del("test-co", board_url="https://test.com/jobs")
        _, rows = _read_csv(tmp_path / "boards.csv")
        assert len(rows) == 0

    def test_delete_all_boards_for_slug(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            boards=(
                "test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n"
                "test-co,test-co-eng,https://test.com/careers,lever,,,\n"
            ),
        )
        board_del("test-co")
        _, rows = _read_csv(tmp_path / "boards.csv")
        assert len(rows) == 0

    def test_specific_board_not_found(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(BoardNotFoundError):
            board_del("test-co", board_url="https://nonexistent.com")

    def test_no_boards_for_slug(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(BoardNotFoundError):
            board_del("nonexistent")

    def test_preserves_other_boards(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            boards=(
                "test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n"
                "other,other-careers,https://other.com/jobs,lever,,,\n"
            ),
        )
        board_del("test-co", board_url="https://test.com/jobs")
        _, rows = _read_csv(tmp_path / "boards.csv")
        assert len(rows) == 1
        assert rows[0]["company_slug"] == "other"


class TestStructuredLogging:
    def _setup(self, tmp_path, monkeypatch, companies="", boards=""):
        (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
        (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.csvtool.get_data_dir", lambda: tmp_path)

    def test_csv_mutations_emit_structured_logs_without_stdout(self, tmp_path, monkeypatch, capsys):
        self._setup(tmp_path, monkeypatch)

        with structlog.testing.capture_logs() as logs:
            company_add("test-co", name="Test", website="https://test.com")
            company_description_set("test-co", "en", "A test company")
            board_add(
                "test-co",
                board_slug="test-co-careers",
                board_url="https://test.com/jobs",
                monitor_type="greenhouse",
            )
            board_del("test-co", board_url="https://test.com/jobs")
            company_del("test-co")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

        events = {entry["event"]: entry for entry in logs}
        assert events["csvtool.company.added"]["slug"] == "test-co"
        assert events["csvtool.company.added"]["fields"] == ["name", "website"]
        assert events["csvtool.company_description.set"]["locale"] == "en"
        assert events["csvtool.board.added"]["board_url"] == "https://test.com/jobs"
        assert events["csvtool.board.removed"]["removed"] == 1
        assert events["csvtool.company.removed"]["removed_boards"] == 0


def _print_call_lines(path: Path) -> list[int]:
    tree = ast.parse(path.read_text())
    lines: list[int] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ):
            lines.append(node.lineno)
    return sorted(lines)


def test_logging_sensitive_entrypoints_do_not_call_print() -> None:
    """Deploy/container log paths should stay parseable by structlog JSON processors."""
    source_root = Path(__file__).resolve().parents[1] / "src"
    targets = [
        source_root / "typesense_schema.py",
        source_root / "cli.py",
        source_root / "csvtool.py",
    ]

    print_calls = {
        str(path.relative_to(source_root)): lines
        for path in targets
        if (lines := _print_call_lines(path))
    }
    assert print_calls == {}
