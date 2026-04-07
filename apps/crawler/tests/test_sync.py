from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from src.sync import (
    _UPSERT_COMPANIES,
    _UPSERT_OCCUPATION_DOMAIN_NAMES,
    _UPSERT_OCCUPATION_DOMAINS,
    _load_boards,
    _load_companies,
    run_sync,
    sync_boards,
    sync_companies,
    sync_occupation_domains,
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

        # Supabase upsert + disable queries
        assert mock_conn.execute.call_count == 2

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

        # Valid row (globex) collected, so Supabase upsert + disable called
        assert mock_conn.execute.call_count == 2

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

        # Supabase upsert + disable queries
        assert mock_conn.execute.call_count == 2

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

        # Supabase upsert + disable queries
        assert mock_conn.execute.call_count == 2

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

        # Supabase upsert + disable queries
        assert mock_conn.execute.call_count == 2


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

            def __await__(self_inner):
                async def _aw():
                    return mock_local_conn

                return _aw().__await__()

            async def __aenter__(self_inner):
                return mock_local_conn

            async def __aexit__(self_inner, *a):
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
        # companies: called twice (supa + local)
        assert mock_sync_companies.call_count == 2
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
    @patch("src.sync.sync_companies")
    async def test_closes_pool_on_error(
        self,
        mock_sync_companies,
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
