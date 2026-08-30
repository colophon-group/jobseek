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


class TestNavigationTimeoutBoardMigrations:
    """Boards from #5708 that serve complete HTML must stay off Playwright."""

    def test_server_rendered_boards_use_static_http(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {row["board_slug"]: row for row in rows}
        slugs = (
            "airbus-careers-bank",
            "china-railway-group-careers",
            "coop-careers-transgourmet-fr",
            "msf-careers-talentsoft-ch",
        )

        for slug in slugs:
            row = by_slug[slug]
            monitor_config = json.loads(row.get("monitor_config") or "{}")
            scraper_config = json.loads(row.get("scraper_config") or "{}")
            assert monitor_config.get("render") is not True
            assert scraper_config.get("render") is not True

        crec = by_slug["china-railway-group-careers"]
        crec_monitor = json.loads(crec["monitor_config"])
        assert crec_monitor["pagination"] == {
            "url_template": ("https://www.crec.cn/web/rlzy65/rczp11/469ad9a7-{page}.html"),
            "max_pages": 4,
        }

        transgourmet = by_slug["coop-careers-transgourmet-fr"]
        assert "liste-toutes-offres.aspx" in transgourmet["board_url"]
        transgourmet_monitor = json.loads(transgourmet["monitor_config"])
        assert transgourmet_monitor["pagination"] == {
            "param_name": "page",
            "max_pages": 15,
        }

        msf = by_slug["msf-careers-talentsoft-ch"]
        msf_scraper = json.loads(msf["scraper_config"])
        assert [step.get("field") for step in msf_scraper["steps"]] == [
            "title",
            "description",
            "responsibilities",
            "qualifications",
        ]


class TestApiSnifferLegacyUrlMigrations:
    """Direct API configs must not silently fall back to browser discovery."""

    def test_unitree_uses_proxy_api_with_explicit_job_urls(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(row for row in rows if row["board_slug"] == "unitree-robotics-careers")
        config = json.loads(row["monitor_config"])

        assert config["api_url"] == "https://api.unitree.com/website/job/list?perPage=50"
        assert config["json_path"] == "data.items"
        assert config["total_path"] == "data.count"
        assert config["url_template"] == "https://www.unitree.com/position/{id}"
        assert config["proxy"] is True
        assert row["scraper_type"] == "skip"
        assert row["scraper_config"] == ""

    def test_sullivan_cromwell_uses_direct_florecruit_api(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            row for row in rows if row["board_slug"] == "sullivan-cromwell-careers-florecruit"
        )
        config = json.loads(row["monitor_config"])

        assert config["api_url"].endswith("/public-jobs/sullcrom/career-page-jobs")
        assert config["json_path"] == ""
        assert config["url_field"] == "applyUrl"


class TestSwissFootballAssociationSportjobsConfig:
    """The Cloudflare-blocked first-party board uses a filtered public feed."""

    def test_feed_keeps_only_exact_multilingual_association_names(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            row for row in rows if row["board_slug"] == "swiss-football-association-sportjobs"
        )
        config = json.loads(row["monitor_config"])

        assert row["board_url"] == ("https://org.football.ch/ueber-uns/offene-stellen.aspx")
        assert row["monitor_type"] == "api_sniffer"
        assert row["scraper_type"] == "skip"
        assert config["api_url"] == (
            "https://www.swissolympic.ch/so-admin-rest-public/api/sj/sportjobs/live"
        )
        assert config["total_path"] == "totalElements"
        assert config["pagination"]["start_value"] == 1
        assert config["item_filter"] == {
            "include": {
                "contactCompany": [
                    "Schweizerischer Fussballverband",
                    "Association Suisse de Football",
                    "Associazione Svizzera di Football",
                    "Swiss Football Association",
                ]
            }
        }
        assert config["fields"]["title"] == "title"
        assert config["fields"]["description"] == "text"
        assert config["fields"]["locations"] == "workPlace"
        assert config["fields"]["date_posted"] == "createDate"


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

    @pytest.mark.parametrize(
        ("companies", "boards", "descriptions", "expected"),
        [
            (
                "slug,name,website\nzeta,Zeta,https://zeta.example\nalpha,Alpha,https://alpha.example\n",
                "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
                "alpha,alpha-careers,https://alpha.example/jobs,greenhouse,,,\n"
                "zeta,zeta-careers,https://zeta.example/jobs,greenhouse,,,\n",
                None,
                "companies.csv: Rows are not sorted by slug",
            ),
            (
                "slug,name,website\nalpha,Alpha,https://alpha.example\nzeta,Zeta,https://zeta.example\n",
                "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
                "zeta,zeta-careers,https://zeta.example/jobs,greenhouse,,,\n"
                "alpha,alpha-careers,https://alpha.example/jobs,greenhouse,,,\n",
                None,
                "boards.csv: Rows are not sorted by company_slug and board_slug",
            ),
            (
                "slug,name,website\nalpha,Alpha,https://alpha.example\nzeta,Zeta,https://zeta.example\n",
                "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
                "alpha,alpha-careers,https://alpha.example/jobs,greenhouse,,,\n"
                "zeta,zeta-careers,https://zeta.example/jobs,greenhouse,,,\n",
                "slug,en\nzeta,Zeta description.\nalpha,Alpha description.\n",
                "company_descriptions.csv: Rows are not sorted by slug",
            ),
        ],
    )
    def test_rejects_noncanonical_company_registry_order(
        self, tmp_path, monkeypatch, companies, boards, descriptions, expected
    ):
        self._write_csvs(tmp_path, companies, boards)
        if descriptions is not None:
            (tmp_path / "company_descriptions.csv").write_text(descriptions)
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()

        assert expected in {str(error) for error in errors}

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

    @pytest.mark.parametrize(
        ("scraper_type", "scraper_config", "is_valid"),
        [
            ("", "", False),
            ("skip", "", False),
            ("json-ld", "", False),
            ("json-ld", '"{""enrich"": [""title""]}"', False),
            ("json-ld", '"{""enrich"": [""description""]}"', True),
        ],
    )
    def test_dom_rich_rows_requires_description_enrichment(
        self,
        tmp_path,
        monkeypatch,
        scraper_type,
        scraper_config,
        is_valid,
    ):
        monitor_config = (
            '"{""rich_rows"": {""row_selector"": "".job"", ""link_selector"": "".job a""}}"'
        )
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,dom,{monitor_config},{scraper_type},"
            f"{scraper_config}\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()
        rich_rows_errors = [
            error for error in errors if "DOM monitor rich_rows requires" in str(error)
        ]

        assert bool(rich_rows_errors) is not is_valid
        if is_valid:
            assert errors == []
        else:
            assert not any("use 'skip'" in str(error) for error in errors)

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

    def test_skip_scraper_allowed_for_smartrecruiters_job_id_mode(self, tmp_path, monkeypatch):
        cfg = (
            '"{""token"": ""HMGroup"", '
            '""canonical_job_id_url_template"": '
            '""https://career.hm.com/job/{job_id}/""}"'
        )
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,smartrecruiters,{cfg},skip,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()

        assert not any("scraper_type='skip' is invalid" in str(e) for e in errors)

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

    def test_api_sniffer_rejects_ignored_legacy_url_key(self, tmp_path, monkeypatch):
        cfg = '"{""url"": ""https://api.example.com/jobs"", ""fields"": {""title"": ""title""}}"'
        self._write_csvs(
            tmp_path,
            "slug,name,website,logo_url,icon_url,logo_type\ntest,Test,https://test.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,api_sniffer,{cfg},skip,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()

        assert any(
            "'url' in api_sniffer monitor_config is ignored; use 'api_url'" in str(error)
            for error in errors
        )

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
            "meta,Meta,https://meta.com,,\n"
            "stripe,Stripe,https://stripe.com,,\n",
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            "meta,meta-careers,https://meta.com/careers,sitemap,,json-ld,\n"
            "stripe,stripe-careers,https://boards.greenhouse.io/stripe,greenhouse,,,\n",
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
            + "No company website was found.",
            "Test is a company listed on Greenhouse with limited publicly available information.",
            "Test operates through the Greenhouse job board under the token "
            + "test. Limited public information is available.",
            "Test recruits through Greenhouse under the token test.",
            "This is a Greenhouse system test board used for posting jobs "
            + "exclusively to external aggregators and is not an actual company.",
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

    def test_invalid_browser_resource_config_rejected(self, tmp_path, monkeypatch):
        monitor = (
            '"{""proxy"": true, ""resource_policy"": ""aggressive"", '
            '""block_resource_types"": [""video""]}"'
        )
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,dom,{monitor},json-ld,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()

        assert any("Invalid browser resource config in monitor_config" in str(e) for e in errors)

    def test_valid_browser_resource_config_accepted(self, tmp_path, monkeypatch):
        scraper = '"{""resource_policy"": ""auto"", ""bot_protection"": false}"'
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,greenhouse,,json-ld,{scraper}\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()

        assert not any("browser resource config" in str(e) for e in errors)

    def test_invalid_bot_protection_recon_value_rejected(self, tmp_path, monkeypatch):
        monitor = '"{""resource_policy"": ""auto"", ""bot_protection"": ""unknown""}"'
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,dom,{monitor},json-ld,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()

        assert any("bot_protection must be a boolean" in str(e) for e in errors)

    def test_non_string_resource_policy_is_reported_not_raised(self, tmp_path, monkeypatch):
        monitor = '"{""resource_policy"": []}"'
        self._write_csvs(
            tmp_path,
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            f"test,test-careers,https://example.com,dom,{monitor},json-ld,\n",
        )
        monkeypatch.setattr("src.shared.constants.get_data_dir", lambda: tmp_path)
        monkeypatch.setattr("src.inspect.get_data_dir", lambda: tmp_path)

        errors = validate_csvs()

        assert any("resource_policy must be a string" in str(e) for e in errors)


class TestMigratedBoardsHaveProxy:
    """The 9 active boards migrated off Lightpanda CDP MUST keep ``proxy: true``.

    The old source of truth was ``data/cdp_routes.csv`` (one CSV, one
    place); the new source is scattered across 9 rows in
    ``data/boards.csv``. If a future bulk-edit drops the flag, these
    boards silently go back to WAF captcha — we want CI to catch that.
    """

    MIGRATED_BOARD_SLUGS = (
        "citigroup-eightfold",
        "eaton-eightfold",
        "kering-careers",
        "lam-research-eightfold",
        "northrop-grumman-eightfold",
        "qualcomm-eightfold",
        "starbucks-eightfold",
        "tailored-brands-eightfold",
        "vodafone-jobs",
    )

    def test_all_active_migrations_have_proxy_true_in_monitor_and_scraper(self):
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


class TestReweGroupBoardConfig:
    """REWE Group's Austrian portal needs cumulative API pagination."""

    def test_austria_uses_cumulative_limit_and_canonical_urls(self):
        import json

        from src.core.scrapers.dom import parse_html
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        rewe_rows = {r["board_slug"]: r for r in rows if r["company_slug"] == "rewe-group"}

        assert set(rewe_rows) == {"rewe-group-austria", "rewe-group-careers"}
        assert rewe_rows["rewe-group-careers"]["monitor_type"] == "rss"

        austria = rewe_rows["rewe-group-austria"]
        assert austria["board_url"] == "https://rewe-group.jobs/de/jobs"
        assert austria["monitor_type"] == "api_sniffer"
        assert austria["scraper_type"] == "dom"

        monitor_config = json.loads(austria["monitor_config"])
        assert monitor_config["browser"] is True
        assert monitor_config["json_path"] == "jobs"
        assert monitor_config["pagination"] == {
            "param_name": "limit",
            "style": "cumulative_limit",
            "start_value": 15,
            "increment": 15,
            "location": "body",
            "max_pages": 200,
        }
        assert monitor_config["url_template"].endswith("/de/jobs/{jobId}")
        assert "description" not in monitor_config["fields"]

        scraper_config = json.loads(austria["scraper_config"])
        assert scraper_config["enrich"] == ["description"]
        sample_html = """
        <main>
          <h1>Store Manager</h1>
          <p>Join our Austrian retail team.</p>
          <h2>Stellenbeschreibung</h2>
          <ul><li>Lead the store team.</li><li>Manage daily operations.</li></ul>
          <h2>Qualifikationen</h2>
          <ul><li>Retail leadership experience.</li></ul>
          <h2>Zusätzliche Informationen</h2>
          <p>Employee discounts are available.</p>
        </main>
        """
        content = parse_html(sample_html, scraper_config)
        assert "Lead the store team" in content.description
        assert "Retail leadership experience" in content.description
        assert "Employee discounts" not in content.description
        assert "Lead the store team" in content.extras["responsibilities"][0]
        assert "Retail leadership experience" in content.extras["qualifications"][0]


class TestSafranBoardConfig:
    """Safran's global board and BambooHR subsidiary need pinned transports."""

    def test_global_board_uses_partitioned_talentsoft_and_extracts_details(self):
        import json

        from src.core.scrapers.dom import parse_html
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next((r for r in rows if r["board_slug"] == "safran-global"), None)
        assert row is not None, "safran-global row missing from boards.csv"
        assert row["monitor_type"] == "dom"
        assert row["scraper_type"] == "dom"

        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        assert row["board_url"].startswith("https://careers.safran-group.com/job/")
        assert monitor_config["render"] is False
        assert scraper_config["render"] is False
        assert monitor_config["pagination"]["max_pages"] >= 1000
        assert monitor_config["pagination"]["partition_validate_total"] is True
        assert monitor_config["pagination"]["partition_stateless"] is True

        sample_html = """
        <h1 class="ts-offer-page__title">Conformity Inspector</h1>
        <div id="fldjobdescription_contract">Permanent</div>
        <div id="fldlocation_location_geographicalareacollection">
          Everett, Washington, United States
        </div>
        <h2 class="JobDescription">Job Description</h2>
        <p>Inspect aircraft interiors and verify conformity.</p>
        <h2 class="ApplicantCriteria">Applicant criteria</h2>
        <p>Bachelor's degree and quality inspection experience.</p>
        <h2>Next section</h2>
        """
        content = parse_html(sample_html, scraper_config)
        assert content.title == "Conformity Inspector"
        assert content.locations == ["Everett, Washington, United States"]
        assert content.employment_type == "Permanent"
        assert "Inspect aircraft interiors" in content.description
        assert "quality inspection experience" in content.extras["qualifications"][0]

    def test_federal_systems_uses_public_bamboohr_detail_api(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            (r for r in rows if r["board_slug"] == "safran-federal-systems"),
            None,
        )
        assert row is not None, "safran-federal-systems row missing from boards.csv"
        assert row["monitor_type"] == "api_sniffer"
        assert row["scraper_type"] == "api_sniffer"

        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        assert monitor_config["api_url"].endswith("/careers/list")
        assert monitor_config["url_template"].endswith("/careers/{id}")
        assert scraper_config["api_url"].endswith("/careers/{id}/detail")
        assert scraper_config["json_path"] == "result.jobOpening"
        assert set(scraper_config["enrich"]) == {
            "description",
            "locations",
            "date_posted",
        }


class TestBnpParibasBoardConfig:
    """BNP Paribas's global WordPress listing is WAF-gated on Hetzner."""

    def test_global_board_uses_fail_closed_proxy_pagination_and_dom_details(self):
        import json

        from src.core.scrapers.dom import parse_html
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next((r for r in rows if r["board_slug"] == "bnp-paribas-global"), None)
        assert row is not None, "bnp-paribas-global row missing from boards.csv"
        assert row["board_url"] == ("https://group.bnpparibas/en/careers/all-job-offers")
        assert row["monitor_type"] == "dom"
        assert row["scraper_type"] == "dom"

        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        assert monitor_config["render"] is False
        assert monitor_config["proxy"] is True
        assert monitor_config["rescrape_policy"] == "never"
        assert monitor_config["pagination"] == {
            "param_name": "page",
            "start": 1,
            "increment": 1,
            "max_pages": 1000,
            "transient_403": True,
        }
        assert scraper_config["render"] is False
        assert scraper_config["proxy"] is True

        sample_html = """
        <main>
          <h1>London - Long Internship 2026 - ABS/CLO Trading</h1>
          <dl>
            <dt>Brand</dt><dd>BNP Paribas CIB</dd>
            <dt>Schedule</dt><dd>Full-Time/Part-Time</dd>
            <dt>Location</dt><dd>London, England, United Kingdom</dd>
          </dl>
          <p>Last update 13.08.2026</p>
          <h2>Business Area</h2>
          <p>BNP Paribas is a leading bank in Europe with an international reach.</p>
          <h2>Job Purpose</h2>
          <p>Support the ABS/CLO trading team and improve desk infrastructure.</p>
          <h2>Requirements</h2>
          <ul><li>Good understanding of financial markets.</li></ul>
          <h2>Offers you may be interested in</h2>
          <p>Another role</p>
        </main>
        """
        content = parse_html(sample_html, scraper_config)
        assert content.title == "London - Long Internship 2026 - ABS/CLO Trading"
        assert content.employment_type is None
        assert content.locations == ["London, England, United Kingdom"]
        assert content.date_posted == "13.08.2026"
        assert "Support the ABS/CLO trading team" in content.description
        assert "Good understanding of financial markets" in content.description
        assert "Another role" not in content.description


class TestLgtGroupBoardConfig:
    def test_three_distinct_sources_and_same_origin_rexx_filter(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {row["board_slug"]: row for row in rows if row["company_slug"] == "lgt-group"}
        assert set(by_slug) == {
            "lgt-group-capital-partners-workday",
            "lgt-group-global",
            "lgt-group-venture-philanthropy",
        }

        global_board = by_slug["lgt-group-global"]
        assert global_board["monitor_type"] == "workday"
        assert json.loads(global_board["monitor_config"]) == {
            "company": "lgt",
            "wd_instance": "wd3",
            "site": "lgtcurrentvacancies",
        }

        capital_board = by_slug["lgt-group-capital-partners-workday"]
        assert capital_board["monitor_type"] == "workday"
        assert json.loads(capital_board["monitor_config"]) == {
            "company": "lgtcp",
            "wd_instance": "wd502",
            "site": "lgtcpcurrentvacancies",
        }

        venture_board = by_slug["lgt-group-venture-philanthropy"]
        assert venture_board["monitor_type"] == "dom"
        assert venture_board["scraper_type"] == "json-ld"
        assert json.loads(venture_board["monitor_config"]) == {
            "url_filter": (
                r"^https://talent\.lgtvp\.com/(?:[^/?#]+/)*"
                r"(?:[^/?#]+-j\d+\.html|job-offer\.html\?yid=\d+)(?:[&#].*)?$"
            )
        }


class TestFidelityInternationalBoardConfig:
    def test_distinct_workday_and_origin_bound_talentlink_sources(self):
        import json

        from src.core.scrapers.dom import parse_html
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {
            row["board_slug"]: row
            for row in rows
            if row["company_slug"] == "fidelity-international"
        }
        assert set(by_slug) == {
            "fidelity-international-early-careers",
            "fidelity-international-professionals",
        }

        professional = by_slug["fidelity-international-professionals"]
        assert professional["monitor_type"] == "workday"
        assert json.loads(professional["monitor_config"]) == {
            "company": "fil",
            "wd_instance": "wd3",
            "site": "001",
        }

        early = by_slug["fidelity-international-early-careers"]
        assert early["monitor_type"] == "dom"
        assert json.loads(early["monitor_config"]) == {
            "url_filter": (
                r"^https://fidelityinternational\.tal\.net/"
                r"[^?#]*/opp/[^?#]+(?:[?#].*)?$"
            )
        }

        scraper_config = json.loads(early["scraper_config"])
        sample_html = """
        <h1 class="section">J65768 - Wholesale Internship Programme 2026 - Milan</h1>
        <span>Job description</span>
        <p>Support Italian wholesale and institutional clients.</p>
        <div data-item_id="69123">Business Unit</div>
        <span>Sales and Marketing</span>
        <div data-item_id="17048">Programme</div>
        <span>Internship</span>
        """
        content = parse_html(sample_html, scraper_config)
        assert content.title == "Wholesale Internship Programme 2026"
        assert content.locations == ["Milan"]
        assert content.employment_type == "Internship"
        assert content.metadata == {"business_unit": "Sales and Marketing"}
        assert "Support Italian wholesale" in content.description


class TestSygnumBankBoardConfig:
    def test_salesforce_board_uses_fail_closed_replacement_pagination(self):
        import json
        import re

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(r for r in rows if r["board_slug"] == "sygnum-bank-salesforce")
        assert row["monitor_type"] == "dom"
        assert row["scraper_type"] == "json-ld"

        config = json.loads(row["monitor_config"])
        assert config["render"] is True
        assert config["wait"] == "domcontentloaded"
        assert config["actions"] == [
            {
                "action": "paginate_collect",
                "next_selector": 'a.link-pagination:has-text("Next")',
                "wait_ms": 1000,
                "max_pages": 10,
            }
        ]

        matcher = re.compile(config["url_filter"])
        assert matcher.search(
            "https://sygnumpeopleportal.my.salesforce-sites.com/"
            "recruit/fRecruit__ApplyJob?vacancyNo=VN192"
        )
        assert not matcher.search(
            "https://attacker.example/recruit/fRecruit__ApplyJob?vacancyNo=VN192"
        )
        scraper_config = json.loads(row["scraper_config"])
        assert scraper_config["fallback"]["type"] == "dom"
        assert scraper_config["fallback"]["fields"] == ["description"]
        from src.core.scrapers.dom import parse_html

        sample_html = """
        <th>About Sygnum</th><td>Sygnum is a global digital asset banking group.</td>
        <th>About the role</th><td>Build secure digital asset banking systems.</td>
        <th>Our ideal candidate</th><td>Write reliable and well-tested code.</td>
        <th>Employment Type</th><td>Permanent</td>
        """
        content = parse_html(sample_html, scraper_config["fallback"]["config"])
        assert "Build secure digital asset" in content.description
        assert "Write reliable" in content.description
        assert "Permanent" not in content.description


class TestSlaughterAndMayBoardConfig:
    def test_page_size_controls_are_native_and_fail_closed(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(r for r in rows if r["board_slug"] == "slaughter-and-may-careers")
        config = json.loads(row["monitor_config"])

        actions = config["actions"]
        assert actions[:2] == [
            {
                "action": "click",
                "selector": "[id$='PageSizeComboBox_Arrow']",
                "required": True,
            },
            {
                "action": "click",
                "selector": "[id$='PageSizeComboBox_DropDown'] li.rcbItem:last-child",
                "required": True,
            },
        ]
        assert "$find" not in json.dumps(actions)
        assert actions[-1]["action"] == "evaluate"
        assert actions[-1]["required"] is True


class TestHasbroBoardConfig:
    """Hasbro's retired Eightfold board returns 404; keep it on Greenhouse."""

    def test_hasbro_uses_greenhouse_board(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        hasbro_rows = [r for r in rows if r["company_slug"] == "hasbro"]
        by_slug = {r["board_slug"]: r for r in hasbro_rows}

        assert "hasbro-eightfold" not in by_slug, (
            "hasbro-eightfold points at retired https://hasbro.eightfold.ai/careers "
            "and causes recurring sitemap discovery failures. Use the active "
            "Greenhouse board instead."
        )

        row = by_slug.get("hasbro-greenhouse")
        assert row is not None, "hasbro-greenhouse row missing from boards.csv"
        assert row["board_url"] == "https://job-boards.greenhouse.io/hasbro"
        assert row["monitor_type"] == "greenhouse"
        assert json.loads(row["monitor_config"]) == {"token": "hasbro"}
        assert row["scraper_type"] == "skip"
        assert row["scraper_config"] == ""


class TestNetJetsMixedTenantConfig:
    """NetJets' SAP feed also publishes independently recruiting brands (#6880)."""

    def test_regional_boards_fetch_and_filter_legal_employer(self):
        import json
        import re

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {row["board_slug"]: row for row in rows}

        europe = by_slug["netjets-europe"]
        us = by_slug["netjets-us"]
        for row, path in ((europe, "/europe/job/"), (us, "/us/job/")):
            assert row["monitor_type"] == "rss"
            assert row["scraper_type"] == "skip"
            config = json.loads(row["monitor_config"])
            assert config["preset"] == "successfactors"
            assert config["fetch_company"] is True
            assert config["url_filter"] == path

        europe_exclude = re.compile(json.loads(europe["monitor_config"])["job_filter"]["exclude"])
        us_exclude = re.compile(json.loads(us["monitor_config"])["job_filter"]["exclude"])
        assert europe_exclude.search("Executive Jet Management (Europe) Limited")
        assert europe_exclude.search("Praetor 600 (EJME)")
        assert not europe_exclude.search("NetJets Management Limited")
        assert us_exclude.search("QS Security Services LLC")
        assert not us_exclude.search("NetJets Aviation, Inc.")


class TestNokiaSitemapFilter:
    """Nokia's Oracle HCM sitemap contains localized non-job pages (#4964)."""

    def test_nokia_sitemap_only_emits_job_detail_urls(self):
        import json
        import re

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("nokia-careers")
        assert row is not None, "nokia-careers row missing from boards.csv"
        assert row["monitor_type"] == "sitemap"
        assert row["scraper_type"] == "oracle_hcm"

        monitor_config = json.loads(row["monitor_config"])
        assert monitor_config["sitemap_url"] == "https://jobs.nokia.com/sitemaps/sitemapIndex"

        url_filter = re.compile(monitor_config["url_filter"])
        valid_urls = [
            "https://jobs.nokia.com/en/job/36037",
            "https://jobs.nokia.com/de/job/20886",
            "https://jobs.nokia.com/pt-BR/job/31878",
            "https://jobs.nokia.com/zh-CN/job/23161?src=JB-10040",
        ]
        invalid_urls = [
            "https://jobs.nokia.com/en/sites/CX_1",
            "https://jobs.nokia.com/en/sites/CX_1/jobs",
            "https://jobs.nokia.com/en/sites/CX_1/join-talent-community",
            "https://jobs.nokia.com/de/sites/CX_1",
            "https://jobs.nokia.com/de/sites/CX_1/jobs",
            "https://jobs.nokia.com/de/sites/CX_1/join-talent-community",
            "https://jobs.nokia.com/fr/sites/CX_1",
            "https://jobs.nokia.com/fr/sites/CX_1/jobs",
            "https://jobs.nokia.com/fr/sites/CX_1/join-talent-community",
            "https://jobs.nokia.com/pt-BR/sites/CX_1",
            "https://jobs.nokia.com/pt-BR/sites/CX_1/jobs",
            "https://jobs.nokia.com/pt-BR/sites/CX_1/join-talent-community",
            "https://jobs.nokia.com/zh-CN/sites/CX_1",
            "https://jobs.nokia.com/zh-CN/sites/CX_1/jobs",
            "https://jobs.nokia.com/zh-CN/sites/CX_1/join-talent-community",
        ]

        assert all(url_filter.search(url) for url in valid_urls)
        assert not any(url_filter.search(url) for url in invalid_urls)


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


class TestCaterpillarRateLimitConfig:
    """Caterpillar detail pages rate-limit plain HTTP scrapes (#4965).

    The sitemap monitor is healthy, but detail pages are protected by
    Cloudflare/Radancy and have produced recurring 429s from crawler egress.
    Browser rendering can extract the JobPosting JSON-LD, and
    ``rescrape_policy=never`` prevents already-filled postings from entering
    the daily refresh tail that caused most of the scrape pressure.
    """

    def test_caterpillar_uses_browser_scrape_and_one_shot_rescrape_policy(self):
        import json

        from src.core.scrapers import scraper_needs_browser
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("caterpillar-careers")
        assert row is not None, "caterpillar-careers row missing from boards.csv"

        assert row.get("monitor_type") == "sitemap"
        mc = json.loads(row.get("monitor_config") or "{}")
        assert mc.get("url_filter") == "/en/jobs/r"
        assert mc.get("rescrape_policy") == "never", (
            "Caterpillar should not periodically re-scrape filled postings; "
            "the Cloudflare/Radancy detail pages rate-limit crawler egress."
        )

        assert row.get("scraper_type") == "json-ld"
        sc = json.loads(row.get("scraper_config") or "{}")
        assert sc.get("render") is True
        assert sc.get("wait") == "load"
        assert scraper_needs_browser("json-ld", sc) is True


class TestDepictInlineCareersConfig:
    """Depict replaced its Teamtailor RSS feed with a custom careers app (#7792)."""

    def test_depict_renders_embedded_openings_with_inline_monitor(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {r["board_slug"]: r for r in rows}

        row = by_slug.get("depict-careers")
        assert row is not None, "depict-careers row missing from boards.csv"
        assert row.get("monitor_type") == "inline"
        assert row.get("scraper_type") == "skip"

        config = json.loads(row.get("monitor_config") or "{}")
        assert config.get("render") is True
        assert config.get("fetch_contains") == "jobseek-openings"
        assert config.get("actions", [{}])[0].get("action") == "evaluate"
        assert config.get("actions", [{}])[0].get("required") is True
        assert any(step.get("field") == "description" for step in config.get("steps", []))


class TestSophiaGeneticsRateLimitConfig:
    """SOPHiA detail pages return empty 202 responses under refresh load (#7791)."""

    def test_sophia_keeps_first_scrapes_but_disables_refresh_tail(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            (r for r in rows if r["board_slug"] == "sophia-genetics-careers"),
            None,
        )
        assert row is not None, "sophia-genetics-careers row missing from boards.csv"
        assert row.get("monitor_type") == "sitemap"

        monitor_config = json.loads(row.get("monitor_config") or "{}")
        assert monitor_config.get("rescrape_policy") == "never"

        assert row.get("scraper_type") == "dom"
        scraper_config = json.loads(row.get("scraper_config") or "{}")
        assert scraper_config.get("render") is True
        assert scraper_config.get("scope") == ".job-description-container"


class TestGrooveQuantumSiteGroundConfig:
    """Groove Quantum must bypass SiteGround's crawler-IP challenge (#4224)."""

    def test_monitor_and_scraper_use_proxy_backed_real_browser(self):
        import json

        from src.core.scrapers.dom import parse_html
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next((r for r in rows if r["board_slug"] == "groove-quantum-careers"), None)
        assert row is not None, "groove-quantum-careers row missing from boards.csv"

        assert row["monitor_type"] == "dom"
        assert row["scraper_type"] == "dom"

        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        for config in (monitor_config, scraper_config):
            assert config["render"] is True
            assert config["proxy"] is True
            assert config["persistent_context"] is True
            assert config["channel"] == "chrome"
            assert config["headless"] is False

        assert monitor_config["rescrape_policy"] == "never"
        assert monitor_config["url_filter"] == "/job/"

        sample_html = """
        <h1>Quantum Measurement Engineer</h1>
        <div>Location: Delft Type: Full-time Posted on: 14 Jan 2026</div>
        <h4>Description</h4>
        <p>Build and operate scalable quantum systems.</p>
        <h4>Application procedure</h4>
        """
        content = parse_html(sample_html, scraper_config)
        assert content.title == "Quantum Measurement Engineer"
        assert content.locations == ["Delft"]
        assert content.employment_type == "Full-time"
        assert content.date_posted == "14 Jan 2026"
        assert "Build and operate scalable quantum systems" in content.description


class TestMetalysisSiteGroundConfig:
    """Metalysis must bypass SiteGround's crawler-IP challenge (#4351)."""

    def test_monitor_and_scraper_use_proxy_backed_real_browser(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next((r for r in rows if r["board_slug"] == "metalysis-careers"), None)
        assert row is not None, "metalysis-careers row missing from boards.csv"

        assert row["monitor_type"] == "dom"
        assert row["scraper_type"] == "json-ld"

        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        for config in (monitor_config, scraper_config):
            assert config["render"] is True
            assert config["proxy"] is True
            assert config["persistent_context"] is True
            assert config["channel"] == "chrome"
            assert config["headless"] is False

        assert monitor_config["rescrape_policy"] == "never"
        assert monitor_config["url_filter"] == "/job/"


class TestOrangeQuantumSystemsSiteGroundConfig:
    """OrangeQS must bypass SiteGround and retain complete job content (#4444)."""

    def test_proxy_backed_real_browser_and_dom_extraction(self):
        import json

        from src.core.scrapers.dom import parse_html
        from src.processing.scrape import _apply_defaults
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            (r for r in rows if r["board_slug"] == "orange-quantum-systems-careers"),
            None,
        )
        assert row is not None, "orange-quantum-systems-careers row missing from boards.csv"

        assert row["monitor_type"] == "dom"
        assert row["scraper_type"] == "dom"

        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        for config in (monitor_config, scraper_config):
            assert config["render"] is True
            assert config["proxy"] is True
            assert config["persistent_context"] is True
            assert config["channel"] == "chrome"
            assert config["headless"] is False

        assert monitor_config["rescrape_policy"] == "never"
        assert monitor_config["url_filter"] == r"/career/[^/?#]+/?$"
        assert monitor_config["wait"] == "commit"
        assert monitor_config["timeout"] == 60000
        assert monitor_config["actions"] == [
            {
                "action": "wait_for",
                "selector": "a[href*='/career/']",
                "state": "attached",
                "timeout": 45,
            }
        ]
        assert scraper_config["wait"] == "commit"
        assert scraper_config["timeout"] == 60000
        assert scraper_config["actions"] == [
            {"action": "wait_for", "selector": "h2", "timeout": 45}
        ]
        assert scraper_config["defaults"]["locations"] == ["Delft, Netherlands"]

        sample_html = """
        <h1>Career</h1>
        <h2>Quantum Project Manager (full-time)</h2>
        <p>About OrangeQS: We develop quantum chip testing systems.</p>
        <h3>Role</h3>
        <p>Coordinate complex technical projects and cross-functional teams.</p>
        <p>This post was published on: Jan 13, 2026</p>
        <h3>Full-time positions</h3>
        <p>Send open applications to recruitment@example.com.</p>
        """
        content = _apply_defaults(parse_html(sample_html, scraper_config), scraper_config)
        assert content.title == "Quantum Project Manager (full-time)"
        assert content.locations == ["Delft, Netherlands"]
        assert content.employment_type == "full-time"
        assert content.date_posted == "Jan 13, 2026"
        assert "Coordinate complex technical projects" in content.description
        assert "Send open applications" not in content.description


class TestVatGroupConfig:
    """VAT's apprenticeship board must replace Yousty's malformed location."""

    def test_yousty_jsonld_location_override(self):
        import json

        from src.core.scrapers.jsonld import parse_html
        from src.processing.scrape import _apply_defaults
        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(
            (r for r in rows if r["board_slug"] == "vat-group-apprenticeships-ch"),
            None,
        )
        assert row is not None
        assert row["monitor_type"] == "dom"
        assert row["scraper_type"] == "json-ld"
        assert row["board_url"] == "https://www.yousty.ch/de-CH/lehrstellen/firmen/944-vat-group"

        monitor_config = json.loads(row["monitor_config"])
        scraper_config = json.loads(row["scraper_config"])
        assert monitor_config["link_selector"] == "a[href*='/lehrstellen/profile/']"
        assert scraper_config["ignore_locations"] is True
        assert scraper_config["defaults"]["locations"] == ["Haag, Switzerland"]
        assert scraper_config["defaults"]["job_location_type"] == "onsite"

        sample_html = """<script type="application/ld+json">
        {"@type":"JobPosting","title":"Lehrstelle als Polymechaniker/in EFZ",
         "description":"<p>Vierjährige Ausbildung bei VAT.</p>",
         "jobLocation":{"name":"VAT Group"}}
        </script>"""
        content = _apply_defaults(parse_html(sample_html, scraper_config), scraper_config)
        assert content.title == "Lehrstelle als Polymechaniker/in EFZ"
        assert content.locations == ["Haag, Switzerland"]
        assert content.job_location_type == "onsite"


class TestOverwolfComeetDescriptionCoverage:
    """Overwolf must use Comeet's rich source directly (#5807).

    The legacy api_sniffer row omitted ``details=true`` and then sent every
    posting through a rendered detail scrape. The shared Comeet monitor
    returns the first-party details payload in the listing cycle, so keeping
    this configuration pinned prevents another 0%-description cohort.
    """

    def test_overwolf_uses_rich_comeet_monitor_without_detail_scraping(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next((r for r in rows if r["board_slug"] == "overwolf-careers"), None)
        assert row is not None, "overwolf-careers row missing from boards.csv"

        assert row["monitor_type"] == "comeet"
        assert json.loads(row.get("monitor_config") or "{}") == {}
        assert row["scraper_type"] == "skip"
        assert json.loads(row.get("scraper_config") or "{}") == {}


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


class TestOlamEmployerBoardConfig:
    def test_sources_preserve_current_separate_employer_identities(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {
            row["board_slug"]: row
            for row in rows
            if row["company_slug"] in {"ofi", "olam-agri", "olam-group"}
        }
        expected_companies = {
            "ofi-brazil": "ofi",
            "ofi-global": "ofi",
            "ofi-north-america": "ofi",
            "olam-agri-americas": "olam-agri",
            "olam-agri-brazil": "olam-agri",
            "olam-agri-global": "olam-agri",
            "olam-group-global": "olam-group",
        }
        assert {slug: row["company_slug"] for slug, row in by_slug.items()} == (expected_companies)

        talemetry = by_slug["ofi-north-america"]
        assert talemetry["monitor_type"] == "talemetry"
        assert json.loads(talemetry["monitor_config"]) == {"proxy": True}
        assert talemetry["scraper_type"] == "json-ld"
        assert json.loads(talemetry["scraper_config"]) == {"proxy": True}

        paylocity = by_slug["olam-agri-americas"]
        assert paylocity["monitor_type"] == "paylocity"
        assert json.loads(paylocity["monitor_config"]) == {"proxy": True}
        assert paylocity["scraper_type"] == "paylocity"
        assert json.loads(paylocity["scraper_config"]) == {
            "enrich": ["description", "employment_type", "job_location_type"],
            "proxy": True,
        }

        rss_slugs = {
            "olam-group-global",
            "ofi-global",
            "olam-agri-global",
        }
        for slug in rss_slugs:
            row = by_slug[slug]
            assert row["monitor_type"] == "rss"
            assert json.loads(row["monitor_config"])["preset"] == "successfactors"
            assert row["scraper_type"] == "skip"

        gupy_slugs = {"ofi-brazil", "olam-agri-brazil"}
        for slug in gupy_slugs:
            row = by_slug[slug]
            assert row["monitor_type"] == "nextdata"
            assert json.loads(row["monitor_config"])["path"] == "props.pageProps.jobs"
            assert row["scraper_type"] == "json-ld"


class TestPilatusBoardConfig:
    def test_global_boards_include_seek_graphql_details(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        by_slug = {row["board_slug"]: row for row in rows if row["company_slug"] == "pilatus"}
        assert set(by_slug) == {
            "pilatus-careers",
            "pilatus-careers-australia",
            "pilatus-careers-europe",
        }

        australia = by_slug["pilatus-careers-australia"]
        assert australia["monitor_type"] == "dom"
        monitor_config = json.loads(australia["monitor_config"])
        assert monitor_config["render"] is True
        assert "job-list-view-job-link" in monitor_config["link_selector"]

        assert australia["scraper_type"] == "api_sniffer"
        scraper_config = json.loads(australia["scraper_config"])
        assert scraper_config["api_url"] == "https://au.seek.com/graphql"
        assert scraper_config["json_path"] == "data.jobDetails.job"
        assert scraper_config["fields"]["description"] == "content"
        assert scraper_config["fields"]["locations"] == "location.label"


class TestBucherIndustriesConfig:
    """Preserve the live provider-quality corrections from PR #7736."""

    def test_rich_provider_fields_and_asset_hashes_are_pinned(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        data_dir = get_data_dir()
        _, rows = read_csv(data_dir / "boards.csv")
        by_slug = {
            row["board_slug"]: row for row in rows if row["company_slug"] == "bucher-industries"
        }

        hydraulics = json.loads(by_slug["bucher-industries-hydraulics"]["scraper_config"])
        assert hydraulics["enrich"] == ["description"]

        kuhn_fields = json.loads(by_slug["bucher-industries-kuhn"]["monitor_config"])["fields"]
        assert kuhn_fields["description"] == [
            "definitions.definition",
            "definitions.bottomLeftText",
            "definitions.bottomRightText",
            "definitions.additionalInfos",
        ]
        assert kuhn_fields["employment_type"]["map"] == {
            "Alternance": "internship",
            "Emploi (temps plein)": "full_time",
            "Emploi(temps partiel)": "part_time",
            "Stage": "internship",
            "VIE": "internship",
        }

        municipal = json.loads(by_slug["bucher-industries-municipal"]["scraper_config"])
        assert municipal == {"ignore_address_region": True}

        _, companies = read_csv(data_dir / "companies.csv")
        company = next(row for row in companies if row["slug"] == "bucher-industries")
        assert company["logo_url"] == (
            "https://jobseek-assets.colophon-group.org/companies/bucher-industries/"
            "logo-72077144ff385ec47fc3ff19d3109fd6bd244c8033c741a7124c348aa35c0b06.svg"
        )
        assert company["icon_url"] == (
            "https://jobseek-assets.colophon-group.org/companies/bucher-industries/"
            "icon-6ba9159d5542e9990aa091cedb1a7cdb9bca9a79fd9e3f3e01df6ef0cee65b25.webp"
        )


class TestAmmannBoardConfig:
    """Keep ABG's generic unsolicited application out of the live inventory."""

    def test_abg_dualoo_filter_excludes_generic_application(self):
        import json

        from src.shared.constants import get_data_dir
        from src.shared.csv_io import read_csv

        _, rows = read_csv(get_data_dir() / "boards.csv")
        row = next(item for item in rows if item["board_slug"] == "ammann-abg")
        config = json.loads(row["monitor_config"])

        assert config["dualoo_portal"] == "fyuan4bk"
        assert config["require_jsonld_jobposting"] is True
        assert config["url_filter"]["exclude"] == ("/502f2f7b-72a8-4ddf-939c-72981563028c/")
