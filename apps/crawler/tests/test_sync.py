from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from src.sync import (
    _LOCATION_MACRO_ALIASES,
    _REALIGN_BOARD_POSTING_COMPANIES_LOCAL,
    _REALIGN_BOARD_POSTING_COMPANIES_SUPA,
    _REALIGN_RENAMED_BOARD_URLS_SUPA,
    _UPSERT_BOARDS_SUPA,
    _UPSERT_COMPANIES,
    _UPSERT_OCCUPATION_DOMAIN_NAMES,
    _UPSERT_OCCUPATION_DOMAINS,
    _UPSERT_OCCUPATION_NAMES,
    _UPSERT_OCCUPATIONS,
    _fetch_active_facet_counts,
    _is_trivial_watchlist,
    _load_boards,
    _load_companies,
    _populate_locations_if_empty,
    refresh_typesense_counts,
    run_sync,
    sync_boards,
    sync_companies,
    sync_companies_typesense,
    sync_locations_typesense,
    sync_lookup_tables_local,
    sync_occupation_domains,
    sync_occupations,
    sync_watchlists_typesense,
)

_COMPANY_COLS = ["slug", "name", "website", "logo_url", "icon_url", "logo_type"]
_COMPANY_SCHEMA = {c: pl.Utf8 for c in _COMPANY_COLS}

_BOARD_COLS = [
    "company_slug",
    "board_slug",
    "board_url",
    "monitor_type",
    "monitor_config",
    "scraper_type",
    "scraper_config",
]
_BOARD_SCHEMA = {c: pl.Utf8 for c in _BOARD_COLS}


class TestLoadCompanies:
    def test_loads_csv(self, tmp_path, monkeypatch):
        csv_content = "slug,name,website,logo_url,icon_url,logo_type\nacme,Acme Corp,https://acme.com,https://acme.com/logo.png,https://acme.com/icon.png,wordmark\n"
        csv_file = tmp_path / "companies.csv"
        csv_file.write_text(csv_content)
        monkeypatch.setattr("src.sync.DATA_DIR", tmp_path)

        df = _load_companies()
        assert isinstance(df, pl.DataFrame)
        assert len(df) == 1
        assert df["slug"][0] == "acme"
        assert df["name"][0] == "Acme Corp"
        assert df["website"][0] == "https://acme.com"

    def test_columns(self, tmp_path, monkeypatch):
        csv_content = (
            "slug,name,website,logo_url,icon_url,logo_type\nacme,Acme Corp,https://acme.com,,,\n"
        )
        csv_file = tmp_path / "companies.csv"
        csv_file.write_text(csv_content)
        monkeypatch.setattr("src.sync.DATA_DIR", tmp_path)

        df = _load_companies()
        expected_columns = {"slug", "name", "website", "logo_url", "icon_url", "logo_type"}
        assert set(df.columns) == expected_columns


class TestLoadBoards:
    def test_loads_csv(self, tmp_path, monkeypatch):
        csv_content = (
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            'acme,acme-careers,https://acme.com/careers,greenhouse,"{}",,""\n'
        )
        csv_file = tmp_path / "boards.csv"
        csv_file.write_text(csv_content)
        monkeypatch.setattr("src.sync.DATA_DIR", tmp_path)

        df = _load_boards()
        assert isinstance(df, pl.DataFrame)
        assert len(df) == 1
        assert df["company_slug"][0] == "acme"
        assert df["board_url"][0] == "https://acme.com/careers"
        assert df["monitor_type"][0] == "greenhouse"

    def test_columns(self, tmp_path, monkeypatch):
        csv_content = (
            "company_slug,board_slug,board_url,monitor_type,monitor_config,scraper_type,scraper_config\n"
            'acme,acme-careers,https://acme.com/careers,greenhouse,"{}",,""\n'
        )
        csv_file = tmp_path / "boards.csv"
        csv_file.write_text(csv_content)
        monkeypatch.setattr("src.sync.DATA_DIR", tmp_path)

        df = _load_boards()
        expected_columns = set(_BOARD_COLS)
        assert set(df.columns) == expected_columns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.execute = AsyncMock()
    conn.transaction.return_value.__aenter__ = AsyncMock()
    conn.transaction.return_value.__aexit__ = AsyncMock(return_value=False)
    return conn


@pytest.fixture
def sample_companies():
    return pl.DataFrame(
        {
            "slug": ["acme", "globex"],
            "name": ["Acme Corp", "Globex Inc"],
            "website": ["https://acme.com", "https://globex.com"],
            "logo_url": ["", "https://globex.com/logo.png"],
            "icon_url": ["", ""],
            "logo_type": ["", "wordmark+icon"],
        },
        schema_overrides=_COMPANY_SCHEMA,
    )


@pytest.fixture
def sample_boards():
    return pl.DataFrame(
        {
            "company_slug": ["acme"],
            "board_slug": ["acme-careers"],
            "board_url": ["https://acme.com/careers"],
            "monitor_type": ["greenhouse"],
            "monitor_config": ['{"token": "acme"}'],
            "scraper_type": [""],
            "scraper_config": [""],
        },
        schema_overrides=_BOARD_SCHEMA,
    )


# ---------------------------------------------------------------------------
# TestSyncOccupationDomains
# ---------------------------------------------------------------------------


class TestSyncOccupationDomains:
    async def test_upserts_domains(self, mock_conn):
        """Domains -> upsert slugs + upsert names."""
        df = pl.DataFrame(
            {
                "slug": ["software-engineering", "data-ai"],
                "en": ["Software Engineering", "Data & AI"],
                "de": ["Softwareentwicklung", "Daten & KI"],
                "fr": ["Génie logiciel", "Données & IA"],
                "it": ["Ingegneria del software", "Dati & IA"],
            },
            schema_overrides={c: pl.Utf8 for c in ["slug", "en", "de", "fr", "it"]},
        )
        await sync_occupation_domains(mock_conn, df, dry_run=False)

        assert mock_conn.execute.call_count == 2
        # First call: upsert slugs
        call0 = mock_conn.execute.call_args_list[0][0]
        assert call0[0] == _UPSERT_OCCUPATION_DOMAINS
        assert call0[1] == ["software-engineering", "data-ai"]
        # Second call: upsert names
        call1 = mock_conn.execute.call_args_list[1][0]
        assert call1[0] == _UPSERT_OCCUPATION_DOMAIN_NAMES

    async def test_dry_run_skips_sql(self, mock_conn):
        df = pl.DataFrame(
            {"slug": ["test"], "en": ["Test"], "de": [""], "fr": [""], "it": [""]},
            schema_overrides={c: pl.Utf8 for c in ["slug", "en", "de", "fr", "it"]},
        )
        await sync_occupation_domains(mock_conn, df, dry_run=True)
        mock_conn.execute.assert_not_called()

    async def test_empty_dataframe(self, mock_conn):
        await sync_occupation_domains(mock_conn, pl.DataFrame(), dry_run=False)
        mock_conn.execute.assert_not_called()


# ---------------------------------------------------------------------------
# TestSyncOccupations
# ---------------------------------------------------------------------------


class TestSyncOccupations:
    async def test_upserts_all_occupation_locale_columns(self, mock_conn):
        df = pl.DataFrame(
            {
                "slug": ["software-engineer"],
                "parent": [""],
                "domain": ["software-engineering"],
                "en": ["Software Engineer"],
                "de": ["Softwareingenieur"],
                "fr": ["Ingénieur logiciel"],
                "it": ["Ingegnere del software"],
                "pl": ["Inżynier oprogramowania"],
                "es": ["Ingeniero de software"],
                "aliases": ["Developer|Desarrollador de software"],
            },
            schema_overrides={
                c: pl.Utf8
                for c in ["slug", "parent", "domain", "en", "de", "fr", "it", "pl", "es", "aliases"]
            },
        )

        await sync_occupations(mock_conn, df, dry_run=False)

        assert mock_conn.execute.call_args_list[0][0][0] == _UPSERT_OCCUPATIONS
        name_call = mock_conn.execute.call_args_list[1][0]
        assert name_call[0] == _UPSERT_OCCUPATION_NAMES

        name_rows = set(zip(name_call[1], name_call[2], name_call[3], name_call[4], strict=True))
        assert ("software-engineer", "pl", "Inżynier oprogramowania", True) in name_rows
        assert ("software-engineer", "es", "Ingeniero de software", True) in name_rows
        assert ("software-engineer", "*", "Desarrollador de software", False) in name_rows


# ---------------------------------------------------------------------------
# TestSyncCompanies
# ---------------------------------------------------------------------------


class TestSyncCompanies:
    async def test_upserts_companies(self, mock_conn, sample_companies):
        """Two companies -> single batch execute call."""
        await sync_companies(mock_conn, sample_companies, dry_run=False)

        mock_conn.execute.assert_called_once()
        call_args = mock_conn.execute.call_args[0]
        assert call_args[0] == _UPSERT_COMPANIES
        assert call_args[1] == ["acme", "globex"]  # slugs
        assert call_args[2] == ["Acme Corp", "Globex Inc"]  # names

    async def test_dry_run_skips_sql(self, mock_conn, sample_companies):
        """dry_run=True -> execute NOT called."""
        await sync_companies(mock_conn, sample_companies, dry_run=True)
        mock_conn.execute.assert_not_called()

    async def test_empty_dataframe(self, mock_conn):
        """0 rows -> execute NOT called."""
        empty = pl.DataFrame(
            {
                "slug": [],
                "name": [],
                "website": [],
                "logo_url": [],
                "icon_url": [],
                "logo_type": [],
            },
            schema_overrides=_COMPANY_SCHEMA,
        )

        await sync_companies(mock_conn, empty, dry_run=False)
        mock_conn.execute.assert_not_called()

    async def test_empty_strings_become_none(self, mock_conn):
        """logo_url="" -> None in the arrays passed to execute."""
        df = pl.DataFrame(
            {
                "slug": ["acme"],
                "name": ["Acme Corp"],
                "website": ["https://acme.com"],
                "logo_url": [""],
                "icon_url": [""],
                "logo_type": [""],
            },
            schema_overrides=_COMPANY_SCHEMA,
        )

        await sync_companies(mock_conn, df, dry_run=False)

        call_args = mock_conn.execute.call_args[0]
        assert call_args[4] == [None]  # logos
        assert call_args[5] == [None]  # icons
        assert call_args[6] == [None]  # logo_types


# ---------------------------------------------------------------------------
# TestSyncBoards
# ---------------------------------------------------------------------------


class TestSyncBoards:
    async def test_upserts_boards(self, mock_conn, sample_boards):
        """Upserts boards to Supabase, no local writes without local_conn."""
        await sync_boards(mock_conn, sample_boards, dry_run=False)

        # Realign stale URLs + Supabase upsert + posting rehome + disable queries
        assert mock_conn.execute.call_count == 4

    async def test_invalid_json_skips_row(self, mock_conn):
        """monitor_config has invalid JSON -> row skipped, valid rows still collected."""
        boards = pl.DataFrame(
            {
                "company_slug": ["acme", "globex"],
                "board_slug": ["acme-careers", "globex-jobs"],
                "board_url": ["https://acme.com/careers", "https://globex.com/jobs"],
                "monitor_type": ["greenhouse", "lever"],
                "monitor_config": ["{invalid json}", "{}"],
                "scraper_type": ["", ""],
                "scraper_config": ["", ""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        await sync_boards(mock_conn, boards, dry_run=False)

        # Valid row (globex) collected, so realign + upsert + rehome + disable called.
        assert mock_conn.execute.call_count == 4

    async def test_all_invalid_json_skips_upsert(self, mock_conn):
        """All rows have invalid JSON -> no upsert, no disable."""
        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ["{bad}"],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        await sync_boards(mock_conn, boards, dry_run=False)

        # No board_urls collected -> no execute calls
        mock_conn.execute.assert_not_called()

    async def test_valid_json_parsed(self, mock_conn):
        """monitor_config='{"key":"value"}' -> parsed and upserted to Supabase."""
        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ['{"key": "value"}'],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        await sync_boards(mock_conn, boards, dry_run=False)

        # Realign stale URLs + Supabase upsert + posting rehome + disable queries
        assert mock_conn.execute.call_count == 4

    async def test_scraper_fields_embedded_in_metadata(self, mock_conn):
        """scraper_type + scraper_config parsed and upserted to Supabase."""
        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["dom"],
                "monitor_config": ['{"url_filter": "/jobs/"}'],
                "scraper_type": ["dom"],
                "scraper_config": ['{"render": true}'],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        await sync_boards(mock_conn, boards, dry_run=False)

        # Realign stale URLs + Supabase upsert + posting rehome + disable queries
        assert mock_conn.execute.call_count == 4

    async def test_invalid_scraper_json_skips_row(self, mock_conn):
        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["dom"],
                "monitor_config": ["{}"],
                "scraper_type": ["dom"],
                "scraper_config": ["{bad}"],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        await sync_boards(mock_conn, boards, dry_run=False)

        mock_conn.execute.assert_not_called()

    async def test_dry_run_skips_sql(self, mock_conn, sample_boards):
        """dry_run=True -> execute NOT called."""
        await sync_boards(mock_conn, sample_boards, dry_run=True)
        mock_conn.execute.assert_not_called()

    async def test_realign_runs_before_upsert_with_slug_url_only(self, mock_conn):
        """The pre-UPSERT realign step gets (company_slugs, board_slugs, board_urls)
        — not the full metadata tuple — so renaming a ``board_url`` while keeping
        the slug stable no longer trips the ``board_slug`` unique constraint.
        """
        boards = pl.DataFrame(
            {
                "company_slug": ["apartmentiq"],
                "board_slug": ["apartmentiq-greenhouse"],
                "board_url": ["https://job-boards.greenhouse.io/apartmentiq"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ['{"token": "apartmentiq"}'],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        await sync_boards(mock_conn, boards, dry_run=False)

        calls = mock_conn.execute.call_args_list
        # Realign is call #0, upsert call #1, posting rehome call #2,
        # disable call #3.
        assert calls[0].args[0] == _REALIGN_RENAMED_BOARD_URLS_SUPA
        assert calls[0].args[1] == ["apartmentiq"]
        assert calls[0].args[2] == ["apartmentiq-greenhouse"]
        assert calls[0].args[3] == ["https://job-boards.greenhouse.io/apartmentiq"]
        # No metadata/crawler_type passed to realign — just the 3-tuple.
        assert len(calls[0].args) == 4
        assert calls[1].args[0] == _UPSERT_BOARDS_SUPA

    async def test_rehomes_existing_postings_after_board_company_change(
        self,
        mock_conn,
        sample_boards,
    ):
        """If a CSV row moves an existing board URL to another company,
        postings already tied to that board must move with it.
        """
        await sync_boards(mock_conn, sample_boards, dry_run=False)

        calls = mock_conn.execute.call_args_list
        assert calls[2].args[0] == _REALIGN_BOARD_POSTING_COMPANIES_SUPA
        assert calls[2].args[1] == ["https://acme.com/careers"]

    async def test_disables_removed_boards(self, mock_conn):
        """Boards upserted and removed boards disabled on Supabase."""
        boards = pl.DataFrame(
            {
                "company_slug": ["acme", "acme"],
                "board_slug": ["acme-careers", "acme-internships"],
                "board_url": ["https://acme.com/careers", "https://acme.com/internships"],
                "monitor_type": ["greenhouse", "lever"],
                "monitor_config": ["", ""],
                "scraper_type": ["", ""],
                "scraper_config": ["", ""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        await sync_boards(mock_conn, boards, dry_run=False)

        # Realign stale URLs + Supabase upsert + posting rehome + disable queries
        assert mock_conn.execute.call_count == 4

    @patch("src.sync.remove_monitor", new_callable=AsyncMock)
    @patch("src.sync.enqueue_monitor", new_callable=AsyncMock)
    async def test_local_path_purges_redis_for_disabled_boards(
        self,
        mock_enqueue,
        mock_remove,
        mock_conn,
    ):
        """When local_conn is provided, sync fetches every disabled/gone board
        and calls remove_monitor so the Redis queue doesn't keep probing dead
        URLs after a CSV removal.
        """
        import uuid

        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ["{}"],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        # Supabase connection returns a resolved (board_id, company_id) for the
        # upserted row so the local-DB branch executes.
        board_id = uuid.uuid4()
        company_id = uuid.uuid4()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": board_id,
                    "company_id": company_id,
                    "board_url": "https://acme.com/careers",
                }
            ]
        )

        mock_local_conn = MagicMock()
        mock_local_conn.execute = AsyncMock()
        # Two orphan rows: one from a just-disabled board, one that was already
        # disabled in a previous sync (covers the historical-orphan case).
        stale_rows = [
            {"board_id": "orphan-lever", "throttle_key": "lever"},
            {"board_id": "orphan-greenhouse", "throttle_key": "greenhouse"},
            # Missing throttle_key must be skipped — no queue to remove from.
            {"board_id": "orphan-no-domain", "throttle_key": None},
        ]
        mock_local_conn.fetch = AsyncMock(return_value=stale_rows)

        await sync_boards(mock_conn, boards, dry_run=False, local_conn=mock_local_conn)

        # Only the two orphans with a throttle_key should be purged from Redis.
        assert mock_remove.await_count == 2
        purged_args = {call.args for call in mock_remove.await_args_list}
        assert ("lever", "orphan-lever") in purged_args
        assert ("greenhouse", "orphan-greenhouse") in purged_args

    @patch("src.sync.remove_monitor", new_callable=AsyncMock)
    @patch("src.sync.enqueue_monitor", new_callable=AsyncMock)
    async def test_local_path_drops_stale_slug_rows_before_upsert(
        self,
        mock_enqueue,
        mock_remove,
        mock_conn,
    ):
        """Before per-board upsert, purge local ``job_board`` rows whose
        ``board_slug`` matches a row we're about to insert but whose ``id``
        is not the Supabase-assigned one — otherwise the unique-slug
        violation rolls back the whole outer Supabase transaction and
        strands new companies in local-only state.
        """
        import uuid

        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ["{}"],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        supa_board_id = uuid.uuid4()
        company_id = uuid.uuid4()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": supa_board_id,
                    "company_id": company_id,
                    "board_url": "https://acme.com/careers",
                }
            ]
        )

        mock_local_conn = MagicMock()
        mock_local_conn.execute = AsyncMock()
        mock_local_conn.fetch = AsyncMock(return_value=[])

        await sync_boards(mock_conn, boards, dry_run=False, local_conn=mock_local_conn)

        # First execute on local_conn should be the defensive DELETE.
        assert mock_local_conn.execute.await_count >= 1
        first_call = mock_local_conn.execute.await_args_list[0]
        sql = first_call.args[0]
        assert "DELETE FROM job_board" in sql
        assert "board_slug = ANY" in sql
        assert "id != ALL" in sql
        assert first_call.args[1] == ["acme-careers"]
        assert first_call.args[2] == [str(supa_board_id)]

    @patch("src.sync.remove_monitor", new_callable=AsyncMock)
    @patch("src.sync.enqueue_monitor", new_callable=AsyncMock)
    async def test_local_path_rehomes_postings_and_touches_export_cursor(
        self,
        mock_enqueue,
        mock_remove,
        mock_conn,
    ):
        """Local posting ownership updates must bump updated_at so the
        exporter re-sends corrected company ids to Supabase and Typesense.
        """
        import uuid

        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ["{}"],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )

        board_id = uuid.uuid4()
        company_id = uuid.uuid4()
        mock_conn.fetch = AsyncMock(
            return_value=[
                {
                    "id": board_id,
                    "company_id": company_id,
                    "board_url": "https://acme.com/careers",
                }
            ]
        )

        mock_local_conn = MagicMock()
        mock_local_conn.execute = AsyncMock()
        mock_local_conn.fetch = AsyncMock(return_value=[])

        await sync_boards(mock_conn, boards, dry_run=False, local_conn=mock_local_conn)

        rehome_calls = [
            call
            for call in mock_local_conn.execute.await_args_list
            if call.args[0] == _REALIGN_BOARD_POSTING_COMPANIES_LOCAL
        ]
        assert len(rehome_calls) == 1
        assert rehome_calls[0].args[1] == ["https://acme.com/careers"]
        assert "updated_at = now()" in rehome_calls[0].args[0]


# ---------------------------------------------------------------------------
# TestRunSync
# ---------------------------------------------------------------------------


class TestRunSync:
    @patch("src.sync.setup_logging")
    @patch("src.sync._load_boards")
    @patch("src.sync._load_company_descriptions")
    @patch("src.sync._load_companies")
    @patch("src.sync._load_industries")
    @patch("src.sync._load_technologies")
    @patch("src.sync._load_seniority")
    @patch("src.sync._load_occupations")
    @patch("src.sync._load_occupation_domains")
    @patch("src.sync.create_pool")
    async def test_empty_csvs_returns_early(
        self,
        mock_create_pool,
        mock_load_occupation_domains,
        mock_load_occupations,
        mock_load_seniority,
        mock_load_technologies,
        mock_load_industries,
        mock_load_companies,
        mock_load_company_descriptions,
        mock_load_boards,
        mock_setup_logging,
    ):
        """Both CSVs empty -> pool not created."""
        mock_load_occupation_domains.return_value = pl.DataFrame()
        mock_load_occupations.return_value = pl.DataFrame()
        mock_load_seniority.return_value = pl.DataFrame()
        mock_load_technologies.return_value = pl.DataFrame()
        mock_load_industries.return_value = pl.DataFrame()
        mock_load_company_descriptions.return_value = pl.DataFrame()
        mock_load_companies.return_value = pl.DataFrame(
            {
                "slug": [],
                "name": [],
                "website": [],
                "logo_url": [],
                "icon_url": [],
                "logo_type": [],
            },
            schema_overrides=_COMPANY_SCHEMA,
        )
        mock_load_boards.return_value = pl.DataFrame(
            {c: [] for c in _BOARD_COLS},
            schema_overrides=_BOARD_SCHEMA,
        )

        await run_sync(dry_run=False)

        mock_create_pool.assert_not_called()

    @patch("src.sync.setup_logging")
    @patch("src.sync._load_boards")
    @patch("src.sync._load_company_descriptions")
    @patch("src.sync._load_companies")
    @patch("src.sync._load_industries")
    @patch("src.sync._load_technologies")
    @patch("src.sync._load_seniority")
    @patch("src.sync._load_occupations")
    @patch("src.sync._load_occupation_domains")
    @patch("src.sync.close_redis")
    @patch("src.sync.close_all_pools")
    @patch("src.sync.create_local_pool")
    @patch("src.sync.create_pool")
    @patch("src.sync.resolve_pending_misses")
    @patch("src.sync.sync_boards")
    @patch("src.sync.sync_company_descriptions")
    @patch("src.sync.sync_companies")
    @patch("src.sync._mirror_companies_to_supabase", new_callable=AsyncMock)
    @patch("src.sync._mirror_companies_to_local", new_callable=AsyncMock)
    @patch("src.sync.sync_industries")
    @patch("src.sync.sync_technologies")
    @patch("src.sync.sync_seniority")
    @patch("src.sync.sync_occupations")
    @patch("src.sync.sync_occupation_domains")
    async def test_normal_flow(
        self,
        mock_sync_occupation_domains,
        mock_sync_occupations,
        mock_sync_seniority,
        mock_sync_technologies,
        mock_sync_industries,
        _mock_mirror_to_local,
        _mock_mirror_to_supa,
        mock_sync_companies,
        mock_sync_company_descriptions,
        mock_sync_boards,
        mock_resolve_pending_misses,
        mock_create_pool,
        mock_create_local_pool,
        mock_close_all_pools,
        mock_close_redis,
        mock_load_occupation_domains,
        mock_load_occupations,
        mock_load_seniority,
        mock_load_technologies,
        mock_load_industries,
        mock_load_companies,
        mock_load_company_descriptions,
        mock_load_boards,
        mock_setup_logging,
    ):
        """Calls all sync functions in order within a transaction."""
        occupation_domains_df = pl.DataFrame()
        occupations_df = pl.DataFrame()
        seniority_df = pl.DataFrame()
        technologies_df = pl.DataFrame()
        industries_df = pl.DataFrame()
        company_descs_df = pl.DataFrame()
        companies_df = pl.DataFrame(
            {
                "slug": ["acme"],
                "name": ["Acme Corp"],
                "website": ["https://acme.com"],
                "logo_url": [""],
                "icon_url": [""],
                "logo_type": [""],
            },
            schema_overrides=_COMPANY_SCHEMA,
        )
        boards_df = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ["{}"],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides=_BOARD_SCHEMA,
        )
        mock_load_occupation_domains.return_value = occupation_domains_df
        mock_load_occupations.return_value = occupations_df
        mock_load_seniority.return_value = seniority_df
        mock_load_technologies.return_value = technologies_df
        mock_load_industries.return_value = industries_df
        mock_load_companies.return_value = companies_df
        mock_load_company_descriptions.return_value = company_descs_df
        mock_load_boards.return_value = boards_df

        # Set up Supabase pool + connection mock with proper async context managers
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_conn.fetch = AsyncMock(return_value=[])
        mock_txn_cm = AsyncMock()
        mock_conn.transaction.return_value = mock_txn_cm

        mock_acquire_cm = AsyncMock()
        mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire_cm.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_acquire_cm
        mock_create_pool.return_value = mock_pool

        # Set up local pool mock — acquire() is used in two modes:
        #   1. conn = await pool.acquire()  (sync_boards path)
        #   2. async with pool.acquire() as conn:  (lookup tables path)
        # In asyncpg, pool.acquire() returns a PoolAcquireContext which
        # supports both modes. We simulate this with a helper class.
        mock_local_conn = MagicMock()
        mock_local_conn.execute = AsyncMock()
        mock_local_conn.copy_records_to_table = AsyncMock()
        mock_local_conn.fetchval = AsyncMock(return_value=0)

        class _FakeAcquireCtx:
            """Simulates asyncpg PoolAcquireContext: awaitable + async CM."""

            def __await__(self):
                async def _aw():
                    return mock_local_conn

                return _aw().__await__()

            async def __aenter__(self):
                return mock_local_conn

            async def __aexit__(self, *a):
                pass

        mock_local_pool = MagicMock()
        mock_local_pool.acquire.return_value = _FakeAcquireCtx()
        mock_local_pool.release = AsyncMock()
        mock_create_local_pool.return_value = mock_local_pool

        await run_sync(dry_run=False)

        # Supabase: lookup tables + company data
        # occupation_domains/occupations/seniority: called once on Supabase;
        # local sync skips them because DataFrames are empty.
        assert mock_sync_occupation_domains.call_count == 1
        assert mock_sync_occupations.call_count == 1
        assert mock_sync_seniority.call_count == 1
        # technologies/industries: called twice (supa + local) regardless
        assert mock_sync_technologies.call_count == 2
        assert mock_sync_industries.call_count == 2
        # companies: called once on local (local-first flow)
        assert mock_sync_companies.call_count == 1
        mock_sync_company_descriptions.assert_called_once_with(mock_conn, company_descs_df, False)

        # Boards: called with supa_conn + local_conn kwarg
        mock_sync_boards.assert_called_once()
        board_call_args = mock_sync_boards.call_args
        assert board_call_args[0][0] == mock_conn  # supa_conn
        assert board_call_args[0][1] is boards_df
        assert board_call_args[0][2] is False  # dry_run

        mock_resolve_pending_misses.assert_called_once_with(mock_conn)
        mock_close_all_pools.assert_called_once()
        mock_close_redis.assert_called_once()

    @patch("src.sync.setup_logging")
    @patch("src.sync._load_boards")
    @patch("src.sync._load_company_descriptions")
    @patch("src.sync._load_companies")
    @patch("src.sync._load_industries")
    @patch("src.sync._load_technologies")
    @patch("src.sync._load_seniority")
    @patch("src.sync._load_occupations")
    @patch("src.sync._load_occupation_domains")
    @patch("src.sync.close_redis")
    @patch("src.sync.close_all_pools")
    @patch("src.sync.create_local_pool")
    @patch("src.sync.create_pool")
    @patch("src.sync.sync_occupation_domains")
    @patch("src.sync.sync_occupations")
    @patch("src.sync.sync_seniority")
    @patch("src.sync.sync_technologies")
    @patch("src.sync.sync_industries")
    @patch("src.sync._mirror_companies_to_supabase", new_callable=AsyncMock)
    @patch("src.sync._mirror_companies_to_local", new_callable=AsyncMock)
    @patch("src.sync.sync_companies")
    async def test_closes_pool_on_error(
        self,
        mock_sync_companies,
        _mock_mirror_to_local,
        _mock_mirror_to_supa,
        mock_sync_industries,
        mock_sync_technologies,
        mock_sync_seniority,
        mock_sync_occupations,
        mock_sync_occupation_domains,
        mock_create_pool,
        mock_create_local_pool,
        mock_close_all_pools,
        mock_close_redis,
        mock_load_occupation_domains,
        mock_load_occupations,
        mock_load_seniority,
        mock_load_technologies,
        mock_load_industries,
        mock_load_companies,
        mock_load_company_descriptions,
        mock_load_boards,
        mock_setup_logging,
    ):
        """sync_companies raises -> close_all_pools + close_redis still called."""
        mock_load_occupation_domains.return_value = pl.DataFrame()
        mock_load_occupations.return_value = pl.DataFrame()
        mock_load_seniority.return_value = pl.DataFrame()
        mock_load_technologies.return_value = pl.DataFrame()
        mock_load_industries.return_value = pl.DataFrame()
        mock_load_company_descriptions.return_value = pl.DataFrame()
        mock_load_companies.return_value = pl.DataFrame(
            {
                "slug": ["acme"],
                "name": ["Acme Corp"],
                "website": ["https://acme.com"],
                "logo_url": [""],
                "icon_url": [""],
                "logo_type": [""],
            },
            schema_overrides=_COMPANY_SCHEMA,
        )
        mock_load_boards.return_value = pl.DataFrame(
            {c: ["x"] for c in _BOARD_COLS},
            schema_overrides=_BOARD_SCHEMA,
        )

        # Set up Supabase pool + connection mock
        mock_conn = MagicMock()
        mock_conn.execute = AsyncMock()
        mock_txn_cm = AsyncMock()
        mock_conn.transaction.return_value = mock_txn_cm

        mock_acquire_cm = AsyncMock()
        mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire_cm.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_acquire_cm
        mock_create_pool.return_value = mock_pool

        # Set up local pool mock
        mock_local_pool = MagicMock()
        mock_local_pool.acquire.return_value = AsyncMock()
        mock_local_pool.release = AsyncMock()
        mock_create_local_pool.return_value = mock_local_pool

        mock_sync_companies.side_effect = RuntimeError("DB connection failed")

        with pytest.raises(RuntimeError, match="DB connection failed"):
            await run_sync(dry_run=False)

        mock_close_all_pools.assert_called_once()
        mock_close_redis.assert_called_once()


class TestIsTrivialWatchlist:
    def test_no_companies_no_filters_is_trivial(self):
        assert _is_trivial_watchlist({}, 0) is True
        assert _is_trivial_watchlist(None, 0) is True

    def test_any_company_and_currency_alone_are_trivial(self):
        # Defaults/prefs don't count as meaningful.
        assert _is_trivial_watchlist({"anyCompany": True}, 0) is True
        assert _is_trivial_watchlist({"salaryCurrency": "USD"}, 0) is True
        assert _is_trivial_watchlist({"anyCompany": True, "salaryCurrency": "USD"}, 0) is True

    def test_companies_make_non_trivial(self):
        assert _is_trivial_watchlist({}, 1) is False
        assert _is_trivial_watchlist({"anyCompany": True}, 3) is False

    @pytest.mark.parametrize(
        "filters",
        [
            {"keywords": ["python"]},
            {"locationSlugs": ["zurich"]},
            {"occupationSlugs": ["engineer"]},
            {"senioritySlugs": ["senior"]},
            {"technologySlugs": ["react"]},
            {"workMode": ["remote"]},
            {"employmentType": ["full_time"]},
            {"salaryMin": 100000},
            {"salaryMax": 200000},
            {"experienceMin": 2},
            {"experienceMax": 10},
            {"experienceMin": 0},
            {"salaryMin": 0},
        ],
    )
    def test_meaningful_filters_make_non_trivial(self, filters):
        assert _is_trivial_watchlist(filters, 0) is False

    @pytest.mark.parametrize(
        "filters",
        [
            {"keywords": []},
            {"locationSlugs": []},
            {"occupationSlugs": []},
            {"senioritySlugs": []},
            {"technologySlugs": []},
        ],
    )
    def test_empty_filter_arrays_are_trivial(self, filters):
        assert _is_trivial_watchlist(filters, 0) is True


class TestSyncLookupTablesLocal:
    async def test_aligned_ids_skip_job_posting_constraint_rebuild(self):
        """Routine deploy sync must not take ACCESS EXCLUSIVE locks on
        job_posting when local lookup IDs already match Supabase.
        """

        domain_rows = [_StubRecord(id=7, slug="engineering")]
        occupation_rows = [_StubRecord(id=36, slug="account-executive")]
        seniority_rows = [_StubRecord(id=4, slug="senior")]

        async def _supa_fetch(sql, *args):
            if "FROM occupation_domain" in sql:
                return domain_rows
            if "FROM occupation" in sql:
                return occupation_rows
            if "FROM seniority" in sql:
                return seniority_rows
            raise AssertionError(f"unexpected Supabase query: {sql} {args}")

        async def _local_fetch(sql, *args):
            if "FROM occupation_domain" in sql:
                return domain_rows
            if "FROM occupation" in sql:
                return occupation_rows
            if "FROM seniority" in sql:
                return seniority_rows
            raise AssertionError(f"unexpected local query: {sql} {args}")

        supa_conn = AsyncMock()
        supa_conn.fetch = AsyncMock(side_effect=_supa_fetch)
        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(side_effect=_local_fetch)
        local_conn.execute = AsyncMock()

        with (
            patch("src.sync.sync_occupation_domains", new_callable=AsyncMock),
            patch("src.sync.sync_occupations", new_callable=AsyncMock),
            patch("src.sync.sync_seniority", new_callable=AsyncMock),
            patch("src.sync.sync_technologies", new_callable=AsyncMock),
            patch("src.sync.sync_industries", new_callable=AsyncMock),
            patch("src.sync._populate_locations_if_empty", new_callable=AsyncMock),
            patch("src.sync._populate_currency_rates_if_empty", new_callable=AsyncMock),
        ):
            await sync_lookup_tables_local(
                supa_conn,
                local_conn,
                pl.DataFrame({"slug": ["engineering"]}),
                pl.DataFrame({"slug": ["account-executive"]}),
                pl.DataFrame({"slug": ["senior"]}),
                pl.DataFrame({"slug": ["python"]}),
                pl.DataFrame({"id": [1]}),
                dry_run=False,
            )

        executed_sql = [call.args[0] for call in local_conn.execute.await_args_list]
        assert not any("ALTER TABLE job_posting" in sql for sql in executed_sql)
        assert not any(sql.startswith("DELETE FROM ") for sql in executed_sql)
        assert not any("INSERT INTO occupation" in sql for sql in executed_sql)

    async def test_id_drift_uses_constraint_rebuild_repair_path(self):
        """If an existing local slug has the wrong ID, keep the repair path
        that drops/re-adds FKs before replacing lookup rows.
        """

        domain_rows = [_StubRecord(id=7, slug="engineering")]
        occupation_rows = [_StubRecord(id=36, slug="account-executive")]
        seniority_rows = [_StubRecord(id=4, slug="senior")]

        async def _supa_fetch(sql, *args):
            if "FROM occupation_domain" in sql:
                return domain_rows
            if "FROM occupation" in sql:
                return occupation_rows
            if "FROM seniority" in sql:
                return seniority_rows
            raise AssertionError(f"unexpected Supabase query: {sql} {args}")

        async def _local_fetch(sql, *args):
            if "FROM occupation_domain" in sql:
                return domain_rows
            if "FROM occupation" in sql:
                return [_StubRecord(id=999, slug="account-executive")]
            if "FROM seniority" in sql:
                return seniority_rows
            raise AssertionError(f"unexpected local query: {sql} {args}")

        supa_conn = AsyncMock()
        supa_conn.fetch = AsyncMock(side_effect=_supa_fetch)
        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(side_effect=_local_fetch)
        local_conn.execute = AsyncMock()

        with (
            patch("src.sync.sync_occupation_domains", new_callable=AsyncMock),
            patch("src.sync.sync_occupations", new_callable=AsyncMock),
            patch("src.sync.sync_seniority", new_callable=AsyncMock),
            patch("src.sync.sync_technologies", new_callable=AsyncMock),
            patch("src.sync.sync_industries", new_callable=AsyncMock),
            patch("src.sync._populate_locations_if_empty", new_callable=AsyncMock),
            patch("src.sync._populate_currency_rates_if_empty", new_callable=AsyncMock),
        ):
            await sync_lookup_tables_local(
                supa_conn,
                local_conn,
                pl.DataFrame({"slug": ["engineering"]}),
                pl.DataFrame({"slug": ["account-executive"]}),
                pl.DataFrame({"slug": ["senior"]}),
                pl.DataFrame({"slug": ["python"]}),
                pl.DataFrame({"id": [1]}),
                dry_run=False,
            )

        executed_sql = [call.args[0] for call in local_conn.execute.await_args_list]
        assert any(
            "DROP CONSTRAINT IF EXISTS job_posting_occupation_id_fkey" in sql
            for sql in executed_sql
        )
        assert any(
            "DROP CONSTRAINT IF EXISTS job_posting_seniority_id_fkey" in sql for sql in executed_sql
        )
        assert "DELETE FROM occupation" in executed_sql
        assert any("ADD CONSTRAINT job_posting_occupation_id_fkey" in sql for sql in executed_sql)
        assert any("ADD CONSTRAINT job_posting_seniority_id_fkey" in sql for sql in executed_sql)


class TestSyncWatchlistsTypesenseLocalTaxonomy:
    async def test_any_company_filters_are_indexed_with_resolved_ids(self):
        watchlist_id = "4ce80d85-2631-47e9-922e-e345e5551afe"
        created_at = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)

        async def supa_fetch(query: str, *_args):
            if "FROM watchlist w" in query:
                return [
                    _StubRecord(
                        id=watchlist_id,
                        slug="enterprise-sales-in-switzerland",
                        title="Enterprise Sales in Switzerland",
                        description=None,
                        is_public=True,
                        created_at=created_at,
                        filters={
                            "anyCompany": True,
                            "locationSlugs": ["switzerland"],
                            "occupationSlugs": ["account-executive", "sales-manager"],
                        },
                        owner_name="Public User",
                        owner_username="public-user",
                    ),
                ]
            if "FROM watchlist_company" in query:
                return []
            if "source_watchlist_id" in query:
                return []
            raise AssertionError(f"unexpected Supabase query: {query}")

        in_local_fetch = False

        async def local_fetch(query: str, slugs):
            nonlocal in_local_fetch
            if "FROM location" in query:
                assert not in_local_fetch
                in_local_fetch = True
                await asyncio.sleep(0)
                in_local_fetch = False
                return [_StubRecord(slug="switzerland", id=2658434)]
            if "FROM occupation" in query:
                assert not in_local_fetch
                in_local_fetch = True
                await asyncio.sleep(0)
                in_local_fetch = False
                return [
                    _StubRecord(slug="account-executive", id=36),
                    _StubRecord(slug="sales-manager", id=105),
                ]
            if "FROM seniority" in query or "FROM technology" in query:
                assert not in_local_fetch
                in_local_fetch = True
                await asyncio.sleep(0)
                in_local_fetch = False
                return []
            raise AssertionError(f"unexpected local query: {query} {slugs}")

        supa_conn = AsyncMock()
        supa_conn.fetch = AsyncMock(side_effect=supa_fetch)
        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(side_effect=local_fetch)

        captured_docs: list[dict] = []

        def _capture_upsert(_client, _collection, docs, *_args, **_kwargs):
            captured_docs.extend(docs)

        client = MagicMock()
        with (
            patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert),
            patch("src.sync._ts_bulk_delete_ids"),
        ):
            await sync_watchlists_typesense(supa_conn, local_conn, client)

        assert len(captured_docs) == 1
        doc = captured_docs[0]
        assert doc["company_count"] == 0
        assert doc["active_job_count"] == 0

        filters_payload = json.loads(doc["filters_json"])
        assert filters_payload == {
            "anyCompany": True,
            "locationIds": [2658434],
            "locationSlugs": ["switzerland"],
            "occupationIds": [36, 105],
            "occupationSlugs": ["account-executive", "sales-manager"],
        }

    async def test_filter_id_resolution_falls_back_to_supabase_when_local_is_empty(self):
        watchlist_id = "4ce80d85-2631-47e9-922e-e345e5551afe"
        created_at = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)

        async def supa_fetch(query: str, *args):
            if "FROM watchlist w" in query:
                return [
                    _StubRecord(
                        id=watchlist_id,
                        slug="enterprise-sales-in-switzerland",
                        title="Enterprise Sales in Switzerland",
                        description=None,
                        is_public=True,
                        created_at=created_at,
                        filters={
                            "anyCompany": True,
                            "locationSlugs": ["switzerland"],
                            "occupationSlugs": [
                                "account-executive",
                                "sales-manager",
                                "sales-engineer",
                            ],
                        },
                        owner_name="Public User",
                        owner_username="public-user",
                    ),
                ]
            if "FROM location WHERE slug" in query:
                return [_StubRecord(slug="switzerland", id=2658434)]
            if "FROM occupation WHERE slug" in query:
                return [
                    _StubRecord(slug="account-executive", id=36),
                    _StubRecord(slug="sales-manager", id=105),
                    _StubRecord(slug="sales-engineer", id=24),
                ]
            if "FROM watchlist_company" in query:
                return []
            if "source_watchlist_id" in query:
                return []
            raise AssertionError(f"unexpected Supabase query: {query} {args}")

        async def local_fetch(query: str, *_args):
            if any(
                table in query
                for table in (
                    "FROM location",
                    "FROM occupation",
                    "FROM seniority",
                    "FROM technology",
                )
            ):
                return []
            raise AssertionError(f"unexpected local query: {query}")

        supa_conn = AsyncMock()
        supa_conn.fetch = AsyncMock(side_effect=supa_fetch)
        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(side_effect=local_fetch)

        captured_docs: list[dict] = []

        def _capture_upsert(_client, _collection, docs, *_args, **_kwargs):
            captured_docs.extend(docs)

        client = MagicMock()
        with (
            patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert),
            patch("src.sync._ts_bulk_delete_ids"),
        ):
            await sync_watchlists_typesense(supa_conn, local_conn, client)

        assert len(captured_docs) == 1
        filters_payload = json.loads(captured_docs[0]["filters_json"])
        assert filters_payload["locationIds"] == [2658434]
        assert filters_payload["occupationIds"] == [36, 105, 24]


# ---------------------------------------------------------------------------
# TestSyncLocationsTypesense
# ---------------------------------------------------------------------------


class _StubRecord(dict):
    """asyncpg.Record-compatible stub usable as a dict (``r["key"]``)."""


class TestSyncWatchlistsTypesense:
    async def test_any_company_filters_json_is_self_contained_without_companies(self):
        filters = {
            "anyCompany": True,
            "locationSlugs": ["switzerland"],
            "occupationSlugs": ["account-executive", "sales-manager"],
            "workMode": ["remote"],
        }

        async def _supa_fetch(sql, *args):
            if "FROM watchlist w" in sql:
                return [
                    {
                        "id": "4ce80d85-2631-47e9-922e-e345e5551afe",
                        "slug": "enterprise-sales-in-switzerland",
                        "title": "Enterprise Sales in Switzerland",
                        "description": None,
                        "is_public": True,
                        "created_at": datetime(2026, 7, 6, tzinfo=UTC),
                        "filters": filters,
                        "owner_name": "Colophon Group",
                        "owner_username": "colophongroup",
                    }
                ]
            if "FROM location WHERE slug" in sql:
                return [{"slug": "switzerland", "id": 30}]
            if "FROM occupation WHERE slug" in sql:
                return [
                    {"slug": "account-executive", "id": 101},
                    {"slug": "sales-manager", "id": 102},
                ]
            if "FROM watchlist_company" in sql:
                # Regression fixture: anyCompany watchlists intentionally
                # have no join rows, but still need a usable Discover count.
                return []
            if "source_watchlist_id" in sql:
                return []
            raise AssertionError(f"unexpected SQL: {sql}")

        supa_conn = AsyncMock()
        supa_conn.fetch = AsyncMock(side_effect=_supa_fetch)
        client = MagicMock()

        captured: list[tuple[str, list[dict]]] = []

        def _capture_upsert(_client, collection, docs, *_a, **_kw):
            captured.append((collection, list(docs)))

        with (
            patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert),
            patch("src.sync._ts_bulk_delete_ids"),
        ):
            await sync_watchlists_typesense(supa_conn, None, client)

        docs = next((docs for collection, docs in captured if collection == "watchlist"), [])
        assert len(docs) == 1
        doc = docs[0]
        assert doc["company_count"] == 0
        assert doc["active_job_count"] == 0

        payload = json.loads(doc["filters_json"])
        assert payload["anyCompany"] is True
        assert payload["locationSlugs"] == ["switzerland"]
        assert payload["locationIds"] == [30]
        assert payload["occupationSlugs"] == ["account-executive", "sales-manager"]
        assert payload["occupationIds"] == [101, 102]
        assert payload["workMode"] == ["remote"]


def _make_loc_row(
    *,
    id: int,
    slug: str,
    type: str,
    lat: float | None = None,
    lng: float | None = None,
    population: int | None = None,
    parent_name: str | None = None,
) -> _StubRecord:
    return _StubRecord(
        id=id,
        slug=slug,
        type=type,
        lat=lat,
        lng=lng,
        population=population,
        parent_name=parent_name,
    )


def _make_name_row(*, location_id: int, locale: str, name: str) -> _StubRecord:
    return _StubRecord(location_id=location_id, locale=locale, name=name)


class TestSyncLocationsTypesense:
    """``sync_locations_typesense`` builds Typesense docs from Postgres rows.

    The behaviour under test is the macro-region alias enrichment from
    issue #2939: macro rows whose slug is in ``_LOCATION_MACRO_ALIASES``
    must carry the ``aliases`` array; non-macro rows must not.
    """

    async def test_macro_rows_get_aliases(self):
        loc_rows = [
            _make_loc_row(id=4, slug="eu", type="macro"),
            _make_loc_row(id=1, slug="emea", type="macro"),
            _make_loc_row(id=5, slug="dach", type="macro"),
            _make_loc_row(
                id=100,
                slug="berlin",
                type="city",
                lat=52.52,
                lng=13.405,
                population=3_700_000,
                parent_name="Germany",
            ),
        ]
        name_rows = [
            _make_name_row(location_id=4, locale="en", name="EU"),
            _make_name_row(location_id=1, locale="en", name="EMEA"),
            _make_name_row(location_id=5, locale="en", name="DACH"),
            _make_name_row(location_id=100, locale="en", name="Berlin"),
            _make_name_row(location_id=100, locale="de", name="Berlin"),
        ]

        supa_conn = AsyncMock()
        # Two ``fetch`` calls in order: location rows, then name rows.
        supa_conn.fetch = AsyncMock(side_effect=[loc_rows, name_rows])

        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(return_value=[])

        captured_docs: list[dict] = []

        def _capture_upsert(_client, _collection, docs, *_args, **_kwargs):
            captured_docs.extend(docs)

        client = MagicMock()
        with patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert):
            await sync_locations_typesense(supa_conn, local_conn, client)

        by_slug = {d["slug"]: d for d in captured_docs}
        # All four locations were indexed.
        assert set(by_slug) == {"eu", "emea", "dach", "berlin"}

        # The EU macro row carries the suggested aliases verbatim.
        assert by_slug["eu"]["aliases"] == [
            "European Union",
            "Europe",
            "EEA",
            "Schengen",
        ]
        # EMEA + DACH carry their respective alias bundles.
        assert "Europe Middle East Africa" in by_slug["emea"]["aliases"]
        assert "Germany Austria Switzerland" in by_slug["dach"]["aliases"]
        # The non-macro Berlin row has no aliases attached — those rows
        # are reachable via their own canonical name.
        assert "aliases" not in by_slug["berlin"]

    async def test_macro_alias_map_covers_seeded_macros(self):
        """The 9 macros currently in the live Typesense index must all
        have alias bundles. Drift between the alias map and the macro
        seed list would silently degrade the typeahead.
        """
        seeded_macro_slugs = {
            "eu",
            "emea",
            "dach",
            "apac",
            "americas",
            "latam",
            "nordics",
            "mena",
            "worldwide",
        }
        missing = seeded_macro_slugs - set(_LOCATION_MACRO_ALIASES)
        assert not missing, f"macro slugs missing aliases: {missing}"
        # Each bundle is non-empty and has only stripped strings.
        for slug, aliases in _LOCATION_MACRO_ALIASES.items():
            assert aliases, f"empty alias bundle for {slug}"
            for alias in aliases:
                assert alias and alias.strip() == alias

    async def test_unknown_macro_slug_skips_aliases(self):
        """A macro row whose slug is NOT in the hard-coded map should be
        indexed without an ``aliases`` field (rather than crash or
        invent one).
        """
        loc_rows = [
            _make_loc_row(id=42, slug="oceania", type="macro"),
        ]
        name_rows = [
            _make_name_row(location_id=42, locale="en", name="Oceania"),
        ]
        supa_conn = AsyncMock()
        supa_conn.fetch = AsyncMock(side_effect=[loc_rows, name_rows])

        captured_docs: list[dict] = []

        def _capture_upsert(_client, _collection, docs, *_args, **_kwargs):
            captured_docs.extend(docs)

        client = MagicMock()
        with patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert):
            await sync_locations_typesense(supa_conn, None, client)

        assert len(captured_docs) == 1
        assert captured_docs[0]["slug"] == "oceania"
        assert "aliases" not in captured_docs[0]


class TestFetchActiveFacetCounts:
    """Tests for the Typesense facet-count helper used by both
    ``sync_locations_typesense`` and ``refresh_typesense_counts`` to read
    post-ancestor-expansion counts (issue #2978).
    """

    def test_extracts_facet_counts_for_field(self):
        client = MagicMock()
        client.collections["job_posting"].documents.search.return_value = {
            "facet_counts": [
                {
                    "field_name": "location_ids",
                    "counts": [
                        {"value": "30", "count": 2416},
                        {"value": "10", "count": 1086},
                        {"value": "4", "count": 14523},
                    ],
                }
            ]
        }
        out = _fetch_active_facet_counts(client, "location_ids")
        assert out == {"30": 2416, "10": 1086, "4": 14523}
        # Sanity-check the request shape — must include facet_by + a
        # large max_facet_values + the web's POSTING_BASE_FILTER
        # (issue #3238: facet counts must equal what users see when
        # filtering by the doc).
        params = client.collections["job_posting"].documents.search.call_args[0][0]
        assert params["facet_by"] == "location_ids"
        assert params["filter_by"] == "is_active:true && has_content:!=false"
        assert params["max_facet_values"] >= 10000
        assert params["per_page"] == 0

    def test_empty_response_returns_empty_dict(self):
        client = MagicMock()
        client.collections["job_posting"].documents.search.return_value = {"facet_counts": []}
        assert _fetch_active_facet_counts(client, "location_ids") == {}

    def test_missing_facet_counts_returns_empty_dict(self):
        client = MagicMock()
        client.collections["job_posting"].documents.search.return_value = {}
        assert _fetch_active_facet_counts(client, "location_ids") == {}


class TestRefreshTypesenseCounts:
    """The location count source must be the Typesense ``location_ids``
    facet (post ancestor expansion), not ``unnest(local.location_ids)``
    which is leaf-only and silently diverged from filter results
    (issue #2978).
    """

    async def test_locations_counts_come_from_typesense_facet(self):
        # Local Postgres returns leaf-only data, but the function should
        # ignore it for locations and use the facet result instead.
        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(
            return_value=[
                # Companies query at the bottom of the function still
                # touches local_conn — we'll match its shape generically.
            ]
        )

        # Typesense facet response: country has its full descendant
        # roll-up (2416), city has its leaf count (1086), macro EU has
        # its country fan-in (14523). These are the numbers an operator
        # gets when filtering by id; without this fix, the displayed
        # ``active_posting_count`` was leaf-only (e.g. 447 for Austria).
        client = MagicMock()

        def _search(params):
            field = params.get("facet_by")
            if field == "location_ids":
                return {
                    "facet_counts": [
                        {
                            "field_name": "location_ids",
                            "counts": [
                                {"value": "30", "count": 2416},  # country
                                {"value": "10", "count": 1086},  # city
                                {"value": "4", "count": 14523},  # macro
                            ],
                        }
                    ]
                }
            if field == "occupation_ids":
                return {
                    "facet_counts": [
                        {
                            "field_name": "occupation_ids",
                            "counts": [
                                {"value": "100", "count": 50},
                                {"value": "200", "count": 90},  # parent
                            ],
                        }
                    ]
                }
            return {"facet_counts": []}

        client.collections["job_posting"].documents.search.side_effect = _search

        captured: list[tuple[str, list[dict]]] = []

        def _capture_upsert(_client, collection, docs, *_a, **_kw):
            captured.append((collection, list(docs)))

        with patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert):
            await refresh_typesense_counts(local_conn, client)

        # Locations: every facet entry produces an "update" doc with the
        # facet count.
        loc_docs = next((docs for c, docs in captured if c == "location"), [])
        loc_by_id = {d["id"]: d for d in loc_docs}
        assert loc_by_id["30"]["active_posting_count"] == 2416
        assert loc_by_id["10"]["active_posting_count"] == 1086
        assert loc_by_id["4"]["active_posting_count"] == 14523

        # Occupations: same field strategy; one row per locale.
        occ_docs = next((docs for c, docs in captured if c == "occupation"), [])
        # 2 occupation ids * 4 locales = 8 docs
        assert len(occ_docs) == 8
        # Parent occupation 200 carries the rolled-up count of 90 in every locale
        en_parent = next(d for d in occ_docs if d["id"] == "200-en")
        assert en_parent["active_posting_count"] == 90

    async def test_seniority_and_technology_counts_apply_has_content_filter(self):
        """Issue #3288: seniority and technology precomputed counts must
        match the web's ``POSTING_BASE_FILTER`` just like company,
        location, and occupation counts do.
        """
        captured_sql: list[str] = []

        async def _fetch(sql, *args, **kwargs):
            captured_sql.append(sql)
            return []

        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(side_effect=_fetch)

        client = MagicMock()
        client.collections["job_posting"].documents.search.return_value = {"facet_counts": []}

        with patch("src.sync._ts_bulk_upsert"):
            await refresh_typesense_counts(local_conn, client)

        sen_sql = next(s for s in captured_sql if "SELECT seniority_id" in s)
        tech_sql = next(s for s in captured_sql if "unnest(technology_ids)" in s)
        for sql in (sen_sql, tech_sql):
            assert "is_active" in sql
            assert "description_r2_hash IS NOT NULL" in sql, (
                f"missing description_r2_hash predicate in:\n{sql}"
            )
            assert "cardinality(titles) > 0" in sql, (
                f"missing titles cardinality predicate in:\n{sql}"
            )
            assert "length(trim(titles[1])) > 0" in sql, (
                f"missing titles non-blank predicate in:\n{sql}"
            )

    async def test_company_counts_apply_has_content_filter(self):
        """Issue #3009: the precomputed `company.active_posting_count` /
        `year_posting_count` numbers must use the same `has_content`
        filter as the web's `POSTING_BASE_FILTER` so the unfiltered
        `listTopCompanies` path can't structurally diverge from the
        filtered facet path. Without `has_content`, McDonald's reads
        55,591 from `company` collection vs ~44,161 from the live facet
        (12k delta = postings whose exporter set `has_content=false`).

        This test asserts:
        1. Both company SQL queries (active + year) include the
           has_content predicate equivalent to the exporter formula.
        2. The resulting Typesense upsert docs propagate per-company
           counts unchanged from whatever the SQL returns.
        """
        captured_sql: list[str] = []

        async def _fetch(sql, *args, **kwargs):
            captured_sql.append(sql)
            # Dispatch by the column shape of the query so seniority/
            # technology queries (also `WHERE is_active`) don't get
            # confused with the company queries.
            if "company_id::text" in sql and "first_seen_at" not in sql:
                return [
                    {"company_id": "co-mcdonalds", "cnt": 44161},
                    {"company_id": "co-accenture", "cnt": 52273},
                ]
            if "company_id::text" in sql and "first_seen_at" in sql:
                return [
                    {"company_id": "co-mcdonalds", "cnt": 55026},
                    {"company_id": "co-accenture", "cnt": 81971},
                ]
            # Seniority + technology queries also touch local_conn —
            # return empty for them.
            return []

        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(side_effect=_fetch)

        client = MagicMock()
        client.collections["job_posting"].documents.search.return_value = {"facet_counts": []}

        captured_upserts: list[tuple[str, list[dict]]] = []

        def _capture_upsert(_client, collection, docs, *_a, **_kw):
            captured_upserts.append((collection, list(docs)))

        with patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert):
            await refresh_typesense_counts(local_conn, client)

        # 1. Both company SQL queries must include the has_content predicate.
        company_sqls = [s for s in captured_sql if "company_id::text" in s]
        assert len(company_sqls) == 2, (
            f"expected active + year company queries, got {len(company_sqls)}"
        )
        for sql in company_sqls:
            assert "description_r2_hash IS NOT NULL" in sql, (
                f"missing description_r2_hash predicate in:\n{sql}"
            )
            assert "cardinality(titles) > 0" in sql, (
                f"missing titles cardinality predicate in:\n{sql}"
            )
            assert "length(trim(titles[1])) > 0" in sql, (
                f"missing titles non-blank predicate in:\n{sql}"
            )

        # 2. The active query keeps `is_active`; the year query keeps
        #    `first_seen_at` and drops `is_active` (flow filter parity
        #    with `POSTING_FLOW_FILTER` on the web side, issue #2965).
        active_sql = next(s for s in company_sqls if "is_active" in s and "first_seen_at" not in s)
        year_sql = next(s for s in company_sqls if "first_seen_at" in s)
        assert "is_active" in active_sql
        assert "is_active" not in year_sql, (
            "year_posting_count is a flow query — should not gate on is_active"
        )

        # 3. Resulting upsert docs reflect the SQL counts.
        company_docs = next((docs for c, docs in captured_upserts if c == "company"), [])
        by_id = {d["id"]: d for d in company_docs}
        assert by_id["co-mcdonalds"]["active_posting_count"] == 44161
        assert by_id["co-mcdonalds"]["year_posting_count"] == 55026
        assert by_id["co-accenture"]["active_posting_count"] == 52273
        assert by_id["co-accenture"]["year_posting_count"] == 81971


class TestSyncCompaniesTypesense:
    """Issue #3238: ``sync_companies_typesense`` computes the initial
    ``active_posting_count`` / ``year_posting_count`` on company docs.
    Both queries must gate on the same ``has_content`` predicate as the
    web's ``POSTING_BASE_FILTER`` so a company card's badge cannot
    structurally exceed the filtered facet count the user sees on
    `/explore?company=<slug>`.
    """

    async def test_company_counts_apply_has_content_filter(self):
        """Both the active-count and year-count SQL must include the
        ``has_content`` predicate mirror of the exporter formula:
        ``description_r2_hash IS NOT NULL AND cardinality(titles) > 0
        AND length(trim(titles[1])) > 0``. The resulting company doc
        propagates only the gated count.
        """
        captured_sql: list[str] = []

        async def _local_fetch(sql, *args, **kwargs):
            captured_sql.append(sql)
            if "company_id::text" in sql and "first_seen_at" not in sql:
                # is_active branch — only gated rows show up.
                return [
                    {"company_id": "co-microsoft", "cnt": 1428},
                ]
            if "company_id::text" in sql and "first_seen_at" in sql:
                # year branch — same gating.
                return [
                    {"company_id": "co-microsoft", "cnt": 9000},
                ]
            return []

        local_conn = AsyncMock()
        local_conn.fetch = AsyncMock(side_effect=_local_fetch)

        supa_conn = AsyncMock()

        async def _supa_fetch(sql, *args, **kwargs):
            if "company" in sql and "industry" in sql:
                return [
                    _StubRecord(
                        id="co-microsoft",
                        name="Microsoft",
                        slug="microsoft",
                        icon=None,
                        logo=None,
                        website=None,
                        description=None,
                        industry=None,
                        employee_count_range=None,
                        founded_year=None,
                        industry_name=None,
                    )
                ]
            return []

        supa_conn.fetch = AsyncMock(side_effect=_supa_fetch)

        client = MagicMock()
        captured_upserts: list[tuple[str, list[dict]]] = []

        def _capture_upsert(_client, collection, docs, *_a, **_kw):
            captured_upserts.append((collection, list(docs)))

        with patch("src.sync._ts_bulk_upsert", side_effect=_capture_upsert):
            await sync_companies_typesense(supa_conn, local_conn, client)

        # 1. Both company count SQLs must include the has_content predicate.
        company_count_sqls = [s for s in captured_sql if "company_id::text" in s]
        assert len(company_count_sqls) == 2, (
            f"expected active + year company queries, got {len(company_count_sqls)}"
        )
        for sql in company_count_sqls:
            assert "description_r2_hash IS NOT NULL" in sql, (
                f"missing description_r2_hash predicate in:\n{sql}"
            )
            assert "cardinality(titles) > 0" in sql, (
                f"missing titles cardinality predicate in:\n{sql}"
            )
            assert "length(trim(titles[1])) > 0" in sql, (
                f"missing titles non-blank predicate in:\n{sql}"
            )

        # 2. The active SQL keeps `is_active`; the year SQL keeps
        #    `first_seen_at` (and intentionally drops `is_active` to
        #    measure activity over time — parity with the web's
        #    POSTING_FLOW_FILTER).
        active_sql = next(s for s in company_count_sqls if "is_active" in s)
        year_sql = next(s for s in company_count_sqls if "first_seen_at" in s)
        assert "is_active" in active_sql
        assert "first_seen_at" in year_sql

        # 3. The doc propagates the gated counts unchanged.
        company_docs = next((docs for c, docs in captured_upserts if c == "company"), [])
        by_id = {d["id"]: d for d in company_docs}
        assert by_id["co-microsoft"]["active_posting_count"] == 1428
        assert by_id["co-microsoft"]["year_posting_count"] == 9000


class TestPopulateLocationsIfEmpty:
    """Issue #2978: ``location_macro_member`` was never seeded into the
    Hetzner local Postgres because the original
    ``_populate_locations_if_empty`` only handled ``location`` and
    ``location_name``. We now seed ``location_macro_member`` from
    Supabase too — idempotently, every sync — so a fresh deploy or
    restored DB still gets the macro->country links.
    """

    async def test_seeds_macro_members_when_table_is_empty(self):
        """Empty location_macro_member -> seed from Supabase even when
        ``location`` is already populated (i.e. the production failure
        mode on Hetzner).
        """
        supa_conn = AsyncMock()
        local_conn = AsyncMock()

        # Local already has locations (so the location/location_name
        # branch short-circuits) but zero macro members.
        local_count_returns = iter(
            [
                5000,  # SELECT count(*) FROM location -> already populated
                0,  # SELECT count(*) FROM location_macro_member -> empty
                573,  # SELECT count(*) FROM location_macro_member -> after insert
            ]
        )
        local_conn.fetchval = AsyncMock(side_effect=lambda *_a, **_kw: next(local_count_returns))

        # Supabase returns the canonical macro members
        macro_rows = [
            {"macro_id": 4, "country_id": 2782113},  # EU -> Austria
            {"macro_id": 4, "country_id": 2921044},  # EU -> Germany
            {"macro_id": 5, "country_id": 2921044},  # DACH -> Germany
        ]
        supa_conn.fetch = AsyncMock(return_value=macro_rows)
        local_conn.executemany = AsyncMock()

        await _populate_locations_if_empty(supa_conn, local_conn)

        # We executed the bulk INSERT exactly once with all 3 rows.
        local_conn.executemany.assert_awaited_once()
        sql, rows = local_conn.executemany.await_args[0]
        assert "INSERT INTO location_macro_member" in sql
        assert "ON CONFLICT (macro_id, country_id) DO NOTHING" in sql
        assert rows == [(4, 2782113), (4, 2921044), (5, 2921044)]

    async def test_skips_when_already_in_sync(self):
        """Idempotency: if local already has every macro_member row,
        skip the executemany call (cheap fast-path on every sync)."""
        supa_conn = AsyncMock()
        local_conn = AsyncMock()

        local_count_returns = iter(
            [
                5000,  # location: populated
                573,  # macro_members: same as supabase
            ]
        )
        local_conn.fetchval = AsyncMock(side_effect=lambda *_a, **_kw: next(local_count_returns))
        rows_573 = [{"macro_id": 1, "country_id": i} for i in range(573)]
        supa_conn.fetch = AsyncMock(return_value=rows_573)
        local_conn.executemany = AsyncMock()

        await _populate_locations_if_empty(supa_conn, local_conn)

        # No insert needed — already in sync.
        local_conn.executemany.assert_not_called()

    async def test_warns_when_supabase_macro_table_empty(self):
        """If Supabase has nothing to seed from, log a warning and
        return without crashing — local stays empty and the exporter's
        own loud warning will surface this on the next refresh."""
        supa_conn = AsyncMock()
        local_conn = AsyncMock()

        local_count_returns = iter([5000, 0])
        local_conn.fetchval = AsyncMock(side_effect=lambda *_a, **_kw: next(local_count_returns))
        supa_conn.fetch = AsyncMock(return_value=[])
        local_conn.executemany = AsyncMock()

        await _populate_locations_if_empty(supa_conn, local_conn)

        local_conn.executemany.assert_not_called()
