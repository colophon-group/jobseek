from __future__ import annotations

import pytest

from src.inspect import ValidationError, validate_csvs


def test_real_csvs_validate():
    """Run ``validate_csvs()`` against the real ``apps/crawler/data/`` files.

    The rest of the tests in this module use ``tmp_path`` fixtures to exercise
    the validator in isolation, which means duplicate board_slugs and similar
    regressions in the committed ``boards.csv`` can slip past CI (and did —
    see issue #2550). This test closes that gap by running the validator
    against the actual data directory with no monkeypatching.
    """
    errors = validate_csvs()
    assert errors == [], "\n".join(str(e) for e in errors)


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
            "slug,name,website,logo_url,icon_url,logo_type\nstripe,Stripe,https://stripe.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "stripe,stripe-careers,https://boards.greenhouse.io/stripe,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert len(errors) == 0

    def test_missing_companies_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert len(errors) == 1
        assert "File not found" in str(errors[0])

    def test_missing_boards_file(self, tmp_path, monkeypatch):
        (tmp_path / "companies.csv").write_text("slug,name,website\ntest,Test,https://test.com\n")
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert len(errors) == 1
        assert "boards.csv" in str(errors[0])

    def test_invalid_slug_format(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\nINVALID_SLUG,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid slug format" in str(e) for e in errors)

    def test_duplicate_slug(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\ntest,Test2,https://test2.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Duplicate slug" in str(e) for e in errors)

    def test_empty_slug(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\n,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Empty slug" in str(e) for e in errors)

    def test_empty_name(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Empty name" in str(e) for e in errors)

    def test_invalid_website_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,not-a-url,,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid URL" in str(e) for e in errors)

    def test_invalid_logo_type(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,,lockup\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid logo_type" in str(e) for e in errors)

    def test_valid_logo_type(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,,wordmark+icon\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert len(errors) == 0

    def test_board_references_missing_company(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\nstripe,Stripe,https://stripe.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "nonexistent,nonexistent-careers,https://example.com,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("not in companies.csv" in str(e) for e in errors)

    def test_invalid_monitor_type(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,unknown_type,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid monitor_type" in str(e) for e in errors)

    def test_url_only_monitor_requires_scraper(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,sitemap,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("requires a scraper_type" in str(e) for e in errors)

    def test_dom_monitor_requires_scraper(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,dom,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("requires a scraper_type" in str(e) for e in errors)

    def test_invalid_scraper_type(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,sitemap,,bad_scraper,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid scraper_type" in str(e) for e in errors)

    @pytest.mark.parametrize("monitor_type", ["personio", "umantis", "notion"])
    def test_non_auto_scraper_monitor_requires_scraper_type(
        self, tmp_path, monkeypatch, monitor_type
    ):
        """Monitors without auto-scraper resolution must set scraper_type.

        Regression guard for issue #2186: a personio board with empty
        scraper_type let the runtime fall back to using the monitor type as
        the scraper name, which crashed ("Unknown scraper type: 'personio'").
        """
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,{monitor_type},,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any(
            f"monitor_type {monitor_type!r} requires explicit scraper_type" in str(e)
            for e in errors
        )

    def test_auto_scraper_monitor_allows_empty_scraper_type(self, tmp_path, monkeypatch):
        """Monitors that auto-configure a scraper (e.g. greenhouse) may leave
        scraper_type empty — the runtime resolves it via auto_scraper_type."""
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://boards.greenhouse.io/test,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert not any("requires explicit scraper_type" in str(e) for e in errors)

    @pytest.mark.parametrize("scraper_type", ["skip", "workday"])
    def test_registered_scraper_types_are_valid(self, tmp_path, monkeypatch, scraper_type):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,greenhouse,,{scraper_type},\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert not any("Invalid scraper_type" in str(e) for e in errors)

    @pytest.mark.parametrize("monitor_type", ["dom", "workday", "sitemap", "smartrecruiters"])
    def test_skip_scraper_rejected_for_url_only_monitor(self, tmp_path, monkeypatch, monitor_type):
        """scraper_type=skip is only valid when the monitor returns rich data.

        Regression guard for issue #2637 ("Broken descriptions from lazy
        scraper configurers"): URL-only monitors paired with skip leave
        descriptions silently empty in production.
        """
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,{monitor_type},,skip,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any(
            f"scraper_type='skip' is invalid for monitor_type {monitor_type!r}" in str(e)
            for e in errors
        )

    def test_skip_scraper_rejected_for_api_sniffer_without_fields(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,api_sniffer,,skip,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any(
            "scraper_type='skip' is invalid for monitor_type 'api_sniffer'" in str(e)
            for e in errors
        )

    def test_skip_scraper_allowed_for_api_sniffer_with_fields(self, tmp_path, monkeypatch):
        cfg = '"{""api_url"": ""https://x"", ""fields"": {""title"": ""title""}}"'
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,api_sniffer,{cfg},skip,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert not any("scraper_type='skip' is invalid" in str(e) for e in errors)

    @pytest.mark.parametrize(
        "monitor_type", ["greenhouse", "lever", "ashby", "recruitee", "personio"]
    )
    def test_skip_scraper_allowed_for_rich_monitors(self, tmp_path, monkeypatch, monitor_type):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,{monitor_type},,skip,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert not any("scraper_type='skip' is invalid" in str(e) for e in errors)

    def test_invalid_monitor_config_json(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,greenhouse,not-json,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid monitor_config JSON" in str(e) for e in errors)

    def test_invalid_scraper_config_json(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,sitemap,,json-ld,not-json\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid scraper_config JSON" in str(e) for e in errors)

    def test_duplicate_board_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,greenhouse,,,\n"
            "test,test-eng,https://example.com,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Duplicate board_url" in str(e) for e in errors)

    def test_empty_board_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Empty board_url" in str(e) for e in errors)

    def test_invalid_board_url(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,not-a-url,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid board_url" in str(e) for e in errors)

    def test_valid_json_config(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            'test,test-careers,https://example.com,greenhouse,"{""token"":""test""}",,\n',
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert len(errors) == 0

    def test_multiple_companies_and_boards(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\n"
            "stripe,Stripe,https://stripe.com,,\n"
            "meta,Meta,https://meta.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "stripe,stripe-careers,https://boards.greenhouse.io/stripe,greenhouse,,,\n"
            "meta,meta-careers,https://meta.com/careers,sitemap,,json-ld,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert len(errors) == 0

    def test_empty_board_slug(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,,https://example.com,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Empty board_slug" in str(e) for e in errors)

    def test_invalid_board_slug_format(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,INVALID_SLUG,https://example.com,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Invalid board_slug format" in str(e) for e in errors)

    def test_duplicate_board_slug(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,greenhouse,,,\n"
            "test,test-careers,https://example2.com,greenhouse,,,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Duplicate board_slug" in str(e) for e in errors)


class TestRejectLazyDescriptions:
    """Reject auto-generated boilerplate company descriptions.

    Issue #2637: a class of bug where the configurer (LLM agent) had no
    info about the company and emitted boilerplate naming the ATS or
    admitting failure (e.g. "Recruitee-based career board operating under
    the X token", "limited public information", "system test board").
    """

    def _write_descs(self, path, descriptions_csv):
        (path / "companies.csv").write_text(
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n"
        )
        (path / "boards.csv").write_text(
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "test,test-careers,https://example.com,greenhouse,,skip,\n"
        )
        (path / "company_descriptions.csv").write_text(descriptions_csv)

    @pytest.mark.parametrize(
        "lazy_en",
        [
            "Recruitee-based career board operating under the Test token. "
            "No company website was found.",
            "Test is a company listed on Greenhouse with limited publicly available information.",
            "Test operates through the Greenhouse job board under the token "
            "test. Limited public information is available.",
            "Test recruits through Greenhouse under the token test.",
            "This is a Greenhouse system test board used for posting jobs "
            "exclusively to external aggregators and is not an actual company.",
            "No company website or identifying information was found during automated discovery.",
        ],
    )
    def test_lazy_description_rejected(self, tmp_path, monkeypatch, lazy_en):
        self._write_descs(
            tmp_path,
            f"slug,en,de,fr,it\ntest,{lazy_en},de,fr,it\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("Lazy auto-generated description" in str(e) for e in errors), (
            f"expected lazy detection for: {lazy_en!r}\nerrors: {[str(e) for e in errors]}"
        )

    def test_factual_description_accepted(self, tmp_path, monkeypatch):
        good = (
            "Test is a Vancouver-based digital experience consultancy that "
            "delivers product design and engineering for enterprise clients."
        )
        self._write_descs(
            tmp_path,
            f"slug,en,de,fr,it\ntest,{good},de,fr,it\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert not any("Lazy auto-generated description" in str(e) for e in errors)


class TestValidateProxyFlag:
    """The ``proxy`` JSON key must be a bool — anywhere it appears.

    Non-bool values coerce truthy at runtime (``bool("false") is True``),
    which would silently turn proxy on for a board the operator tried to
    turn it off for.
    """

    def _write_csvs(self, path, boards_csv):
        (path / "companies.csv").write_text(
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n"
        )
        (path / "boards.csv").write_text(boards_csv)

    def test_monitor_config_non_bool_proxy_rejected(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            'test,test-careers,https://example.com,greenhouse,"{""proxy"": ""yes""}",skip,\n',
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("'proxy' in monitor_config must be bool" in str(e) for e in errors)

    def test_scraper_config_non_bool_proxy_rejected(self, tmp_path, monkeypatch):
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            'test,test-careers,https://example.com,greenhouse,,json-ld,"{""proxy"": 1}"\n',
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("'proxy' in scraper_config must be bool" in str(e) for e in errors)

    def test_fallback_config_non_bool_proxy_rejected(self, tmp_path, monkeypatch):
        fallback = '"{""fallback"": {""type"": ""dom"", ""config"": {""proxy"": ""no""}}}"'
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,greenhouse,,json-ld,{fallback}\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        assert any("'proxy' in fallback config must be bool" in str(e) for e in errors)

    def test_bool_proxy_accepted_in_all_three_places(self, tmp_path, monkeypatch):
        mon = '"{""proxy"": true}"'
        scr = (
            '"{""proxy"": true, ""fallback"": {""type"": ""dom"", ""config"": {""proxy"": false}}}"'
        )
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,greenhouse,{mon},json-ld,{scr}\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)
        errors = validate_csvs()
        # No errors *about proxy* — other errors (like missing companies cols) are fine
        assert not any("'proxy'" in str(e) for e in errors)


class TestMigratedBoardsHaveProxy:
    """The 10 boards migrated off Lightpanda CDP MUST keep ``proxy: true``.

    The old source of truth was ``data/cdp_routes.csv`` (one CSV, one
    place); the new source is scattered across 10 rows in
    ``data/boards.csv``. If a future bulk-edit drops the flag, these
    boards silently go back to WAF captcha — we want CI to catch that.
    """

    MIGRATED_BOARD_SLUGS = (
        "citigroup-eightfold",
        "eaton-eightfold",
        "kering-careers",
        "lam-research-eightfold",
        "micron-eightfold",
        "northrop-grumman-eightfold",
        "qualcomm-eightfold",
        "starbucks-eightfold",
        "tailored-brands-eightfold",
        "vodafone-jobs",
    )

    def test_all_ten_have_proxy_true_in_monitor_and_scraper(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        missing: list[str] = []
        for slug in self.MIGRATED_BOARD_SLUGS:
            row = by_slug.get(slug)
            assert row is not None, f"migrated board {slug!r} not found in boards.csv"
            mc = json.loads(row.get("monitor_config") or "{}")
            sc = json.loads(row.get("scraper_config") or "{}")
            if mc.get("proxy") is not True:
                missing.append(f"{slug}: monitor_config.proxy != True")
            if sc.get("proxy") is not True:
                missing.append(f"{slug}: scraper_config.proxy != True")

        assert not missing, (
            "These boards lost the proxy flag — they were WAF-blocked from Hetzner "
            'and rely on the proxy layer to get data. Re-add "proxy": true to both '
            "monitor_config and scraper_config:\n  - " + "\n  - ".join(missing)
        )


class TestTeslaScraperHasEnrich:
    """Tesla's api_sniffer detail scraper MUST declare ``enrich`` (#2952).

    The Tesla monitor delivers ``title``, ``locations``, ``employment_type``,
    and metadata from the cua-api listing payload — making it a "rich"
    monitor (``result.jobs_by_url is not None``). Without an ``enrich`` list
    on the scraper config, ``_board_has_enrich`` returns None and
    ``is_rich_no_scrape = is_rich and not enrich_fields`` evaluates True.
    Postings are then inserted via ``_INSERT_RICH_JOB`` (which doesn't set
    ``next_scrape_at``) and the scrape is never enqueued — leaving 6,099
    active Tesla postings with ``description_r2_hash IS NULL`` indefinitely.

    This test pins the ``enrich`` declaration so a future bulk-edit can't
    silently revert the fix. Mirrors the Netflix-careers pattern (also a
    rich api_sniffer monitor with an XHR-capture detail scraper).
    """

    def test_tesla_detail_scraper_declares_enrich(self):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("tesla-careers")
        assert row is not None, "tesla-careers row missing from boards.csv"

        sc = json.loads(row.get("scraper_config") or "{}")
        enrich = sc.get("enrich")
        assert isinstance(enrich, list) and "description" in enrich, (
            "tesla-careers scraper_config must declare 'enrich': ['description'] "
            "so its rich-monitor postings get next_scrape_at = now() and the "
            "browser-capture scraper actually runs. See #2952."
        )

        # Also exercise the production guard: the metadata that sync writes
        # would yield a non-None enrich list from _board_has_enrich.
        metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
        assert _board_has_enrich(metadata) == enrich


class TestDidiGlobalScraperHasEnrich:
    """Regression guard for #2952: Didi Global postings stuck with empty
    descriptions because the api_sniffer (rich) monitor returned full job
    metadata but ``scraper_type=skip`` with no ``enrich`` declaration meant
    the detail scraper never ran.

    Without ``scraper_config.enrich``, ``_board_has_enrich`` returns None,
    ``is_rich_no_scrape`` evaluates True, and the rich-monitor branch
    inserts via ``_INSERT_RICH_JOB`` (no ``next_scrape_at``) instead of
    ``_INSERT_RICH_JOB_ENRICH``. Postgres confirmed all 1,979 Didi postings
    sat with NULL next_scrape_at + NULL last_scraped_at + 0 scrape_failures
    + NULL description_r2_hash - the scheduler never queued them.

    Mirrors PR #2954 (tesla). Extra wrinkle: the original CSV row had
    ``scraper_type=skip`` while carrying dom-format ``steps``. The fix
    flips the type to ``dom`` AND adds ``enrich``.
    """

    def test_didi_global_declares_enrich_description(self):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            (r for r in rows if r["board_slug"] == "didi-global-careers-intl"),
            None,
        )
        assert row is not None, "didi-global-careers-intl row missing from boards.csv"

        assert row["scraper_type"] == "dom", (
            "didi-global-careers-intl scraper_type must be 'dom'. The "
            "original 'skip' value made _is_skip_no_scrape return True so "
            "the scrape pipeline was bypassed and 1,979 postings sat with "
            "description_r2_hash = NULL. See #2952."
        )

        scraper_config = json.loads(row.get("scraper_config") or "{}")
        assert "description" in (scraper_config.get("enrich") or []), (
            "didi-global-careers-intl scraper_config must declare "
            '"enrich": ["description"] - without it, _board_has_enrich '
            "returns None, is_rich_no_scrape becomes True, and 1,979 "
            "postings get next_scrape_at = NULL. See PR #2954 (tesla)."
        )

        metadata = {
            "scraper_type": row["scraper_type"],
            "scraper_config": scraper_config,
        }
        assert _board_has_enrich(metadata) == ["description"]


class TestDidiGlobalDomScraper:
    """Functional check: Didi dom config extracts title/locations/description
    from a captured fixture of careers.didiglobal.com (#2952)."""

    def test_didi_global_dom_extracts_description(self):
        import json
        from pathlib import Path

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv
        from src.shared.extract import flatten, walk_steps

        fixture = Path(__file__).parent / "fixtures" / "didi_global_jobdetail.html"
        html = fixture.read_text()

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(r for r in rows if r["board_slug"] == "didi-global-careers-intl")
        config = json.loads(row["scraper_config"])
        steps = config["steps"]

        elements = flatten(html)
        fields, _ = walk_steps(elements, steps)

        assert fields.get("title") == ("Estágio em operações (Engagement Channels)")
        assert fields.get("locations") == "Sao Paulo - Brazil"

        desc = fields.get("description") or ""
        assert len(desc) > 1000, (
            f"description too short ({len(desc)} chars) - extraction is "
            "broken; the fixture's About-the-company range is ~3.9KB"
        )
        assert "<h4>About the company</h4>" in desc
        assert "<li>" in desc


class TestDecathlonScraperHasEnrich:
    """Decathlon's talentclue dom scraper MUST declare ``enrich`` (#2952).

    The talentclue api_sniffer monitor returns ``title``, ``locations``,
    and metadata.* from the public job-list JSON — making it a "rich"
    monitor (``result.jobs_by_url is not None``). Without an ``enrich``
    list on the scraper config, ``_board_has_enrich`` returns None and
    ``is_rich_no_scrape = is_rich and not enrich_fields`` evaluates True.
    Postings are inserted via ``_INSERT_RICH_JOB`` (which doesn't set
    ``next_scrape_at``) and the dom detail scrape is never enqueued —
    which is what left 557 active Decathlon postings with
    ``description_r2_hash IS NULL`` in local Postgres and
    ``has_content=false`` in Typesense.

    Mirrors the Tesla / Infineon enrich guards above.
    """

    def test_decathlon_detail_scraper_declares_enrich(self):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("decathlon-es-talentclue")
        assert row is not None, "decathlon-es-talentclue row missing from boards.csv"

        sc = json.loads(row.get("scraper_config") or "{}")
        enrich = sc.get("enrich")
        assert isinstance(enrich, list) and "description" in enrich, (
            "decathlon-es-talentclue scraper_config must declare "
            "'enrich': ['description'] so its rich-monitor postings get "
            "next_scrape_at = now() and the dom detail scraper actually runs. "
            "See #2952."
        )

        # Also exercise the production guard: the metadata that sync writes
        # would yield a non-None enrich list from _board_has_enrich.
        metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
        assert _board_has_enrich(metadata) == enrich


class TestInfineonScraperHasEnrich:
    """Regression guard for #2952: Infineon postings stuck with empty
    descriptions because the eightfold (rich) monitor returned full job
    metadata but the detail scraper had no ``enrich`` declaration.

    Without ``scraper_config.enrich``, ``_board_has_enrich`` returns None,
    which sets ``is_rich_no_scrape = True`` in ``processing.board`` —
    rich-monitor postings are then inserted with ``next_scrape_at = NULL``
    and never enter the scrape pipeline. Postgres confirmed
    1152/1153 active Infineon postings sat with NULL next_scrape_at +
    NULL last_scraped_at + 0 scrape_failures (scheduler never queued them).

    The fix mirrors PR #2954 (tesla) and the 15 other eightfold boards
    documented in apps/crawler/AGENTS.md: declare
    ``scraper_config: {"enrich": ["description"]}`` so PCSX-rich postings
    get a one-shot detail scrape that fills ``description``.
    """

    def test_infineon_declares_enrich_description(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            (r for r in rows if r["board_slug"] == "infineon-careers"),
            None,
        )
        assert row is not None, "infineon-careers row missing from boards.csv"

        # Eightfold monitor + eightfold scraper (matches the canonical
        # pattern used by kering, citigroup, qualcomm, microsoft, etc.)
        assert row["monitor_type"] == "eightfold"
        assert row["scraper_type"] == "eightfold"

        scraper_config = json.loads(row.get("scraper_config") or "{}")
        assert "description" in (scraper_config.get("enrich") or []), (
            "infineon-careers must declare scraper_config.enrich = "
            '["description"] — without it, _board_has_enrich returns None, '
            "is_rich_no_scrape becomes True, and 1152+ postings get "
            "next_scrape_at = NULL and never enter the scrape pipeline. "
            "See PR #2954 (tesla) for the same scheduler failure mode."
        )


class TestApiSnifferRichBoardsHaveEnrich:
    """api_sniffer rich-monitor boards MUST declare enrich on the detail
    scraper (#2963).

    Audit #2963 found 5 boards with the same pattern as Tesla #2954 and
    Decathlon #2962: an api_sniffer monitor with ``fields`` configured
    (or auto-detected at runtime) so the monitor returns ``DiscoveredJob``
    items, paired with a json-ld / nextdata / dom secondary scraper —
    but no ``enrich`` list on ``scraper_config``. Without the enrich
    declaration ``_board_has_enrich`` returns None and
    ``processing/board.py`` picks ``_INSERT_RICH_JOB`` (no
    ``next_scrape_at``) over ``_INSERT_RICH_JOB_ENRICH``, leaving every
    posting permanently unscraped.

    Aggregate impact across the four boards covered here was ~5,000
    active postings stuck with ``description_r2_hash IS NULL``:
    hitachi-energy-careers (2,224), goldman-sachs-careers (~1,450),
    haier-group-careers-cn (1,094), continental-careers (100).

    The fifth board flagged by the audit (``alibaba-careers-lazada``)
    is intentionally NOT in this test — it has no ``scraper_type`` /
    ``scraper_config`` at all and tracks as a separate-scope follow-up
    (Lazada's detail endpoint is JS-rendered with no usable static
    JSON-LD or nextdata, so picking a scraper config requires its own
    investigation).

    One test per board, mirroring TestTeslaScraperHasEnrich, so a
    future bulk-edit cannot silently revert any single fix.
    """

    @staticmethod
    def _assert_enrich(slug: str, expected_scraper_type: str) -> None:
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get(slug)
        assert row is not None, f"{slug!r} row missing from boards.csv"

        assert row.get("monitor_type") == "api_sniffer", (
            f"{slug} should remain api_sniffer-monitored — the rich/no-scrape "
            "scheduling bug only affects rich api_sniffer monitors."
        )
        assert row.get("scraper_type") == expected_scraper_type, (
            f"{slug} must use scraper_type={expected_scraper_type!r} for "
            "description enrichment. See #2963."
        )

        sc = json.loads(row.get("scraper_config") or "{}")
        enrich = sc.get("enrich")
        assert isinstance(enrich, list) and "description" in enrich, (
            f"{slug} scraper_config must declare 'enrich': ['description'] "
            "so its rich-monitor postings get next_scrape_at = now() and "
            "the detail scraper actually runs. See #2963."
        )

        metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
        assert _board_has_enrich(metadata) == enrich

    def test_hitachi_energy_careers_declares_enrich(self):
        """hitachi-energy-careers: api_sniffer (rich) + json-ld enrich."""
        self._assert_enrich("hitachi-energy-careers", "json-ld")

    def test_goldman_sachs_careers_declares_enrich(self):
        """goldman-sachs-careers: api_sniffer (rich) + nextdata enrich."""
        self._assert_enrich("goldman-sachs-careers", "nextdata")

    def test_haier_group_careers_cn_declares_enrich(self):
        """haier-group-careers-cn: api_sniffer (rich) + dom enrich."""
        self._assert_enrich("haier-group-careers-cn", "dom")

    def test_continental_careers_declares_enrich(self):
        """continental-careers: api_sniffer (URL-only declared, fields
        auto-detected at runtime) + json-ld enrich.

        The CSV monitor_config has no explicit ``fields``, but the
        api_sniffer monitor calls ``auto_map_fields(items)`` on the
        listing payload (``api_sniffer.py:938``) and the Continental API
        returns enough metadata for that call to succeed — flipping the
        monitor to rich-mode at runtime. The DB confirmed 100/100 active
        postings with ``next_scrape_at IS NULL``, identical to the
        statically-rich boards. The fix is the same: declare enrich so
        ``_INSERT_RICH_JOB_ENRICH`` is used and json-ld runs on the
        detail page.
        """
        self._assert_enrich("continental-careers", "json-ld")


class TestTalentclueSiblingsHaveEnrich:
    """The talentclue sibling cluster of Decathlon (#2962) — barcelona-activa
    and ayuda-en-accion — share the same root cause: rich api_sniffer
    monitor + dom scraper with no ``enrich`` declaration, so postings
    were inserted via ``_INSERT_RICH_JOB`` and the dom detail scrape was
    never enqueued. 171 + 130 active postings sat with
    ``has_content=false`` in Typesense before this fix (#2963).

    Mirrors ``TestDecathlonScraperHasEnrich`` and the Tesla / Infineon
    enrich guards above — pinning the ``enrich`` declaration so a future
    bulk-edit can't silently revert it.
    """

    @pytest.mark.parametrize(
        "board_slug,active_rows",
        [
            ("barcelona-activa-talentclue", 171),
            ("ayuda-en-accion-talentclue", 130),
        ],
    )
    def test_talentclue_sibling_declares_enrich(self, board_slug, active_rows):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get(board_slug)
        assert row is not None, f"{board_slug!r} row missing from boards.csv"

        sc = json.loads(row.get("scraper_config") or "{}")
        enrich = sc.get("enrich")
        assert isinstance(enrich, list) and "description" in enrich, (
            f"{board_slug} scraper_config must declare 'enrich': ['description'] "
            "so its rich-monitor postings get next_scrape_at = now() and the "
            f"dom detail scraper actually runs (was {active_rows} active rows "
            "with has_content=false). See #2963."
        )

        # Also exercise the production guard: the metadata that sync writes
        # would yield a non-None enrich list from _board_has_enrich.
        metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
        assert _board_has_enrich(metadata) == enrich


class TestTerveystaloJobylonHasEnrich:
    """Terveystalo's jobylon monitor MUST pair with the json-ld enrich scrape.

    The Jobylon monitor (``src/core/monitors/jobylon.py``) returns
    ``DiscoveredJob`` rows with ``description=None`` — descriptions are
    expressly left to an enrichment scraper (see the module docstring).
    Without ``scraper_config: {"enrich": ["description"]}``, the
    rich-monitor branch in ``processing/board.py`` picks
    ``_INSERT_RICH_JOB`` (no ``next_scrape_at``) over
    ``_INSERT_RICH_JOB_ENRICH``, so the json-ld scraper that fills
    description on the detail page never runs. Audit #2963 reported
    134/134 active Terveystalo postings with ``has_content=false`` for
    exactly this reason.

    This test pins the enrich declaration on the terveystalo-jobylon row
    so a future bulk-edit can't silently revert the fix.
    """

    def test_terveystalo_jobylon_declares_enrich(self):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("terveystalo-jobylon")
        assert row is not None, "terveystalo-jobylon row missing from boards.csv"

        assert row.get("scraper_type") == "json-ld", (
            "terveystalo-jobylon must use the json-ld scraper — Jobylon detail "
            "pages serve a JobPosting JSON-LD block with the description."
        )

        sc = json.loads(row.get("scraper_config") or "{}")
        enrich = sc.get("enrich")
        assert isinstance(enrich, list) and "description" in enrich, (
            "terveystalo-jobylon scraper_config must declare "
            "'enrich': ['description'] so its rich-monitor postings get "
            "next_scrape_at = now() and the json-ld scraper actually runs. "
            "See #2963."
        )

        metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
        assert _board_has_enrich(metadata) == enrich


class TestZteMokahrHasMokahrScraperAndEnrich:
    """ZTE's mokahr boards MUST use the mokahr scraper with enrich (#2963).

    The Mokahr listing API (``/api/outer/ats-apply/website/jobs/v2``)
    returns metadata only — title, locations, commitment, dates — but
    NOT the ``jobDescription`` field. The dedicated detail endpoint
    (``/api/outer/ats-apply/website/job``, POST, AES-128-CBC encrypted)
    is the only source for descriptions, and it's only consulted by the
    new ``mokahr`` scraper added alongside this fix.

    Two breakages combine on the ZTE rows before this PR:

    1. ``scraper_type=skip`` skipped any scrape pipeline call. Because
       the mokahr monitor IS rich (returns title + locations +
       employment_type + metadata.department), processing/board.py
       drives the rich path with ``enrich_fields=None``, which picks
       ``_INSERT_RICH_JOB`` (no ``next_scrape_at``) and never queues
       a scrape.

    2. The listing API for ZTE in particular omits ``jobDescription``
       (verified empirically against the live API), so even if the
       monitor's description-extraction path had been wired the field
       would still be empty without a separate detail call.

    Pinning ``scraper_type=mokahr`` plus
    ``scraper_config.enrich = ["description"]`` flips the rich-path
    SQL to ``_INSERT_RICH_JOB_ENRICH`` (next_scrape_at = now()) AND
    routes the queued scrape through the new mokahr scraper — which
    decrypts the detail endpoint and returns ``description``.
    """

    _ZTE_BOARDS = ("zte-campus", "zte-careers")

    def test_zte_mokahr_boards_use_mokahr_scraper_with_enrich(self):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        for slug in self._ZTE_BOARDS:
            row = by_slug.get(slug)
            assert row is not None, f"{slug!r} row missing from boards.csv"

            assert row.get("monitor_type") == "mokahr", (
                f"{slug} should remain a mokahr-monitored board"
            )
            assert row.get("scraper_type") == "mokahr", (
                f"{slug} must use scraper_type=mokahr — the listing API does "
                "not return jobDescription, so a detail scrape is required. "
                "See #2963."
            )

            sc = json.loads(row.get("scraper_config") or "{}")
            enrich = sc.get("enrich")
            assert isinstance(enrich, list) and "description" in enrich, (
                f"{slug} scraper_config must declare 'enrich': ['description'] "
                "so the rich-monitor branch picks _INSERT_RICH_JOB_ENRICH and "
                "queues the scrape. See #2963."
            )

            metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
            assert _board_has_enrich(metadata) == enrich

    def test_mokahr_scraper_is_registered(self):
        """The CSV references scraper_type=mokahr — the registry must accept it."""
        from src.core.scrapers import get_scraper_type

        scraper = get_scraper_type("mokahr")
        assert scraper is not None, (
            "scraper_type=mokahr in boards.csv requires a registered mokahr "
            "scraper in src/core/scrapers/."
        )
        # Pure HTTP — no Playwright dependency, must run on slim workers.
        assert scraper.needs_browser is False


class TestPeopleStrongScrapersHaveEnrich:
    """Regression guard for #2995: PeopleStrong (Bajaj Finserv, L&T)
    rich-monitor postings stuck with NULL next_scrape_at because their
    dom detail scraper didn't declare ``enrich``.

    PR #2953 wired a working render:true dom scraper for both peoplestrong
    boards (extraction confirmed live: Bajaj 1850-byte HTML description,
    title "Cluster Manager - Operations and Service" on JR00209059) but
    omitted ``enrich``. The api_sniffer monitor returns rich data
    (jobTitle, locationHierarchy, jobPostedDate), so
    ``result.jobs_by_url is not None`` and ``is_rich = True``. Without
    ``_board_has_enrich`` returning a non-None list, ``is_rich_no_scrape``
    is True and postings are inserted via ``_INSERT_RICH_JOB`` (no
    ``next_scrape_at``) — the dom scrape is never enqueued. Postgres on
    2026-05-10 confirmed 6,322 / 6,322 Bajaj and 1,184 / 1,185 L&T
    peoplestrong postings stuck with NULL next_scrape_at.

    Mirrors PR #2954 (tesla), #2964 (didi-global), and the existing
    enrich-guard suites above.
    """

    def test_bajaj_finserv_peoplestrong_declares_enrich(self):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("bajaj-finserv-careers-ps-jobs")
        assert row is not None, "bajaj-finserv-careers-ps-jobs row missing from boards.csv"

        sc = json.loads(row.get("scraper_config") or "{}")
        enrich = sc.get("enrich")
        assert isinstance(enrich, list) and "description" in enrich, (
            "bajaj-finserv-careers-ps-jobs scraper_config must declare "
            "'enrich': ['description'] — its api_sniffer monitor returns "
            "rich (jobTitle/locationHierarchy/jobPostedDate), so without "
            "enrich the rich-monitor branch picks _INSERT_RICH_JOB and "
            "leaves next_scrape_at = NULL on all 6,322 active postings. "
            "See #2995."
        )

        metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
        assert _board_has_enrich(metadata) == enrich

    def test_larsen_toubro_peoplestrong_declares_enrich(self):
        import json

        from src.processing.scrape import _board_has_enrich
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("larsen-toubro-careers")
        assert row is not None, "larsen-toubro-careers row missing from boards.csv"

        sc = json.loads(row.get("scraper_config") or "{}")
        enrich = sc.get("enrich")
        assert isinstance(enrich, list) and "description" in enrich, (
            "larsen-toubro-careers scraper_config must declare "
            "'enrich': ['description'] — its api_sniffer monitor returns "
            "rich (jobTitle/locationHierarchyComplete/jobPostedDate/"
            "employment_type), so without enrich the rich-monitor branch "
            "picks _INSERT_RICH_JOB and leaves next_scrape_at = NULL. "
            "See #2995."
        )

        metadata = {"scraper_type": row.get("scraper_type"), "scraper_config": sc}
        assert _board_has_enrich(metadata) == enrich
