from __future__ import annotations

from src.validate import validate_csvs, ValidationError


class TestValidationError:
    def test_str_with_row(self):
        err = ValidationError("file.csv", 5, "bad value")
        assert str(err) == "file.csv:5: bad value"

    def test_str_without_row(self):
        err = ValidationError("file.csv", None, "missing file")
        assert str(err) == "file.csv: missing file"


class TestValidateCsvs:
    def _write_csvs(self, path, companies_csv, boards_csv):
        (path / "companies.csv").write_text(companies_csv)
        (path / "boards.csv").write_text(boards_csv)

    def test_valid_csvs(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\nstripe,Stripe,https://stripe.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "stripe,https://boards.greenhouse.io/stripe,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert len(errors) == 0

    def test_missing_companies_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert len(errors) == 1
        assert "File not found" in str(errors[0])

    def test_missing_boards_file(self, tmp_path, monkeypatch):
        (tmp_path / "companies.csv").write_text("slug,name,website\ntest,Test,https://test.com\n")
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert len(errors) == 1
        assert "boards.csv" in str(errors[0])

    def test_invalid_slug_format(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\nINVALID_SLUG,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Invalid slug format" in str(e) for e in errors)

    def test_duplicate_slug(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\ntest,Test2,https://test2.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Duplicate slug" in str(e) for e in errors)

    def test_empty_slug(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\n,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Empty slug" in str(e) for e in errors)

    def test_empty_name(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Empty name" in str(e) for e in errors)

    def test_invalid_website_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,not-a-url,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Invalid URL" in str(e) for e in errors)

    def test_board_references_missing_company(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\nstripe,Stripe,https://stripe.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "nonexistent,https://example.com,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("not in companies.csv" in str(e) for e in errors)

    def test_invalid_monitor_type(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,https://example.com,unknown_type,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Invalid monitor_type" in str(e) for e in errors)

    def test_url_only_monitor_requires_scraper(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,https://example.com,sitemap,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("requires a scraper_type" in str(e) for e in errors)

    def test_discover_monitor_requires_scraper(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,https://example.com,discover,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("requires a scraper_type" in str(e) for e in errors)

    def test_invalid_scraper_type(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,https://example.com,sitemap,,bad_scraper,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Invalid scraper_type" in str(e) for e in errors)

    def test_invalid_monitor_config_json(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,https://example.com,greenhouse,not-json,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Invalid monitor_config JSON" in str(e) for e in errors)

    def test_invalid_scraper_config_json(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,https://example.com,sitemap,,json-ld,not-json\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Invalid scraper_config JSON" in str(e) for e in errors)

    def test_duplicate_board_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,https://example.com,greenhouse,,,\n"
            "test,https://example.com,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Duplicate board_url" in str(e) for e in errors)

    def test_empty_board_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Empty board_url" in str(e) for e in errors)

    def test_invalid_board_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,not-a-url,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert any("Invalid board_url" in str(e) for e in errors)

    def test_valid_json_config(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\ntest,Test,https://test.com,,\n",
            'company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n'
            'test,https://example.com,greenhouse,"{""token"":""test""}",,\n',
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert len(errors) == 0

    def test_multiple_companies_and_boards(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url\n"
            "stripe,Stripe,https://stripe.com,,\n"
            "meta,Meta,https://meta.com,,\n",
            "company_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "stripe,https://boards.greenhouse.io/stripe,greenhouse,,,\n"
            "meta,https://meta.com/careers,sitemap,,json-ld,\n",
        )
        monkeypatch.setattr("src.validate.DATA_DIR", tmp_path)
        errors = validate_csvs()
        assert len(errors) == 0
