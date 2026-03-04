from __future__ import annotations

import pytest

from src.csvtool import _read_csv, board_add, board_del, company_add, company_del

COMPANIES_HEADER = "slug,name,website,logo_url,icon_url\n"
BOARDS_HEADER = (
    "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
)


class TestCompanyAdd:
    def _setup(self, tmp_path, monkeypatch, companies="", boards=""):
        (tmp_path / "companies.csv").write_text(COMPANIES_HEADER + companies)
        (tmp_path / "boards.csv").write_text(BOARDS_HEADER + boards)
        monkeypatch.setattr("src.csvtool.DATA_DIR", tmp_path)

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

    def test_invalid_slug(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
            company_add("INVALID")

    def test_existing_no_updates(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,,,\n")
        with pytest.raises(SystemExit):
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
        monkeypatch.setattr("src.csvtool.DATA_DIR", tmp_path)

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
        with pytest.raises(SystemExit):
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
        monkeypatch.setattr("src.csvtool.DATA_DIR", tmp_path)

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
        with pytest.raises(SystemExit):
            board_add("nonexistent", board_url="https://test.com/jobs", monitor_type="greenhouse")

    def test_missing_board_url_on_create(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch, companies="test-co,Test,,,\n")
        with pytest.raises(SystemExit):
            board_add("test-co", monitor_type="greenhouse")

    def test_existing_no_updates(self, tmp_path, monkeypatch):
        self._setup(
            tmp_path,
            monkeypatch,
            companies="test-co,Test,,,\n",
            boards="test-co,test-co-careers,https://test.com/jobs,greenhouse,,,\n",
        )
        with pytest.raises(SystemExit):
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
        monkeypatch.setattr("src.csvtool.DATA_DIR", tmp_path)

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
        with pytest.raises(SystemExit):
            board_del("test-co", board_url="https://nonexistent.com")

    def test_no_boards_for_slug(self, tmp_path, monkeypatch):
        self._setup(tmp_path, monkeypatch)
        with pytest.raises(SystemExit):
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
