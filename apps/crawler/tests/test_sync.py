from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import polars as pl
import pytest

from src.sync import (
    _DISABLE_REMOVED_BOARDS,
    _GET_COMPANY_ID,
    _UPSERT_BOARD,
    _load_boards,
    _load_companies,
    run_sync,
    sync_boards,
    sync_companies,
)

_COMPANY_COLS = ["slug", "name", "website", "logo_url", "icon_url"]
_COMPANY_SCHEMA = {c: pl.Utf8 for c in _COMPANY_COLS}


class TestLoadCompanies:
    def test_loads_csv(self, tmp_path, monkeypatch):
        csv_content = "slug,name,website,logo_url,icon_url\nacme,Acme Corp,https://acme.com,https://acme.com/logo.png,https://acme.com/icon.png\n"
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
        csv_content = "slug,name,website,logo_url,icon_url\nacme,Acme Corp,https://acme.com,,\n"
        csv_file = tmp_path / "companies.csv"
        csv_file.write_text(csv_content)
        monkeypatch.setattr("src.sync.DATA_DIR", tmp_path)

        df = _load_companies()
        expected_columns = {"slug", "name", "website", "logo_url", "icon_url"}
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
        expected_columns = {
            "company_slug",
            "board_slug",
            "board_url",
            "monitor_type",
            "monitor_config",
            "scraper_type",
            "scraper_config",
        }
        assert set(df.columns) == expected_columns


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_conn():
    conn = AsyncMock()
    conn.fetchrow = AsyncMock()
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
        schema_overrides={
            c: pl.Utf8
            for c in [
                "company_slug",
                "board_slug",
                "board_url",
                "monitor_type",
                "monitor_config",
                "scraper_type",
                "scraper_config",
            ]
        },
    )


# ---------------------------------------------------------------------------
# TestSyncCompanies
# ---------------------------------------------------------------------------


class TestSyncCompanies:
    async def test_upserts_companies(self, mock_conn, sample_companies):
        """Two companies -> fetchrow called twice, returns correct slug->id mapping."""
        mock_conn.fetchrow.side_effect = [
            {"slug": "acme", "id": "uuid-1"},
            {"slug": "globex", "id": "uuid-2"},
        ]

        result = await sync_companies(mock_conn, sample_companies, dry_run=False)

        assert mock_conn.fetchrow.call_count == 2
        assert result == {"acme": "uuid-1", "globex": "uuid-2"}

    async def test_dry_run_skips_sql(self, mock_conn, sample_companies):
        """dry_run=True -> fetchrow NOT called, returns empty dict."""
        result = await sync_companies(mock_conn, sample_companies, dry_run=True)

        mock_conn.fetchrow.assert_not_called()
        assert result == {}

    async def test_empty_dataframe(self, mock_conn):
        """0 rows -> fetchrow NOT called, returns empty dict."""
        empty = pl.DataFrame(
            {"slug": [], "name": [], "website": [], "logo_url": [], "icon_url": []},
            schema_overrides=_COMPANY_SCHEMA,
        )

        result = await sync_companies(mock_conn, empty, dry_run=False)

        mock_conn.fetchrow.assert_not_called()
        assert result == {}

    async def test_empty_strings_become_none(self, mock_conn):
        """logo_url="" -> None passed to fetchrow."""
        df = pl.DataFrame(
            {
                "slug": ["acme"],
                "name": ["Acme Corp"],
                "website": ["https://acme.com"],
                "logo_url": [""],
                "icon_url": [""],
            },
            schema_overrides=_COMPANY_SCHEMA,
        )
        mock_conn.fetchrow.return_value = {"slug": "acme", "id": "uuid-1"}

        await sync_companies(mock_conn, df, dry_run=False)

        call_args = mock_conn.fetchrow.call_args
        # positional args: (sql, slug, name, website, logo_url, icon_url)
        assert call_args[0][4] is None  # logo_url
        assert call_args[0][5] is None  # icon_url


# ---------------------------------------------------------------------------
# TestSyncBoards
# ---------------------------------------------------------------------------


class TestSyncBoards:
    async def test_upserts_boards(self, mock_conn, sample_boards):
        """Board with company in slug_to_id -> fetchrow called with correct args."""
        slug_to_id = {"acme": "uuid-1"}
        mock_conn.fetchrow.return_value = {
            "id": "board-uuid-1",
            "board_url": "https://acme.com/careers",
        }

        await sync_boards(mock_conn, sample_boards, slug_to_id, dry_run=False)

        # Should have called fetchrow once for the upsert (not for company lookup)
        assert mock_conn.fetchrow.call_count == 1
        call_args = mock_conn.fetchrow.call_args[0]
        assert call_args[0] == _UPSERT_BOARD
        assert call_args[1] == "uuid-1"  # company_id
        assert call_args[2] == "acme-careers"  # board_slug
        assert call_args[3] == "https://acme.com/careers"  # board_url
        assert call_args[4] == "greenhouse"  # monitor_type
        assert json.loads(call_args[5]) == {"token": "acme"}  # metadata (re-serialized JSON)

        # Should have called execute for _DISABLE_REMOVED_BOARDS
        mock_conn.execute.assert_called_once_with(
            _DISABLE_REMOVED_BOARDS, ["https://acme.com/careers"]
        )

    async def test_company_lookup_from_db(self, mock_conn, sample_boards):
        """company_slug not in slug_to_id -> falls back to conn.fetchrow(_GET_COMPANY_ID)."""
        slug_to_id = {}  # company not in mapping
        mock_conn.fetchrow.side_effect = [
            # First call: _GET_COMPANY_ID lookup
            {"id": "uuid-from-db"},
            # Second call: _UPSERT_BOARD
            {"id": "board-uuid-1", "board_url": "https://acme.com/careers"},
        ]

        await sync_boards(mock_conn, sample_boards, slug_to_id, dry_run=False)

        # First call should be the company lookup
        first_call = mock_conn.fetchrow.call_args_list[0]
        assert first_call[0][0] == _GET_COMPANY_ID
        assert first_call[0][1] == "acme"

        # Second call should be the board upsert with the looked-up company_id
        second_call = mock_conn.fetchrow.call_args_list[1]
        assert second_call[0][0] == _UPSERT_BOARD
        assert second_call[0][1] == "uuid-from-db"

    async def test_missing_company_skips(self, mock_conn, sample_boards):
        """Company not in mapping and not in DB -> logs error, skips."""
        slug_to_id = {}
        # Company lookup returns None (not found)
        mock_conn.fetchrow.return_value = None

        await sync_boards(mock_conn, sample_boards, slug_to_id, dry_run=False)

        # fetchrow called once for the company lookup, NOT for upsert
        assert mock_conn.fetchrow.call_count == 1
        assert mock_conn.fetchrow.call_args[0][0] == _GET_COMPANY_ID

        # _DISABLE_REMOVED_BOARDS should still be called (board_url was appended before skip)
        mock_conn.execute.assert_called_once_with(
            _DISABLE_REMOVED_BOARDS, ["https://acme.com/careers"]
        )

    async def test_invalid_json_skips(self, mock_conn):
        """monitor_config has invalid JSON -> logs error, skips."""
        boards = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ["{invalid json}"],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides={
                c: pl.Utf8
                for c in [
                    "company_slug",
                    "board_slug",
                    "board_url",
                    "monitor_type",
                    "monitor_config",
                    "scraper_type",
                    "scraper_config",
                ]
            },
        )
        slug_to_id = {"acme": "uuid-1"}

        await sync_boards(mock_conn, boards, slug_to_id, dry_run=False)

        # No upsert call (only the disable call)
        # fetchrow should not be called since company was in slug_to_id
        # and the invalid JSON causes a skip before the upsert
        mock_conn.fetchrow.assert_not_called()

        # _DISABLE_REMOVED_BOARDS still called (url was appended before JSON parsing)
        mock_conn.execute.assert_called_once()

    async def test_valid_json_parsed(self, mock_conn):
        """monitor_config='{"key":"value"}' -> parsed and re-serialized to metadata."""
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
            schema_overrides={
                c: pl.Utf8
                for c in [
                    "company_slug",
                    "board_slug",
                    "board_url",
                    "monitor_type",
                    "monitor_config",
                    "scraper_type",
                    "scraper_config",
                ]
            },
        )
        slug_to_id = {"acme": "uuid-1"}
        mock_conn.fetchrow.return_value = {
            "id": "board-uuid-1",
            "board_url": "https://acme.com/careers",
        }

        await sync_boards(mock_conn, boards, slug_to_id, dry_run=False)

        call_args = mock_conn.fetchrow.call_args[0]
        metadata = call_args[5]
        assert json.loads(metadata) == {"key": "value"}

    async def test_dry_run_skips_sql(self, mock_conn, sample_boards):
        """dry_run=True -> upsert NOT called (but board_url still appended)."""
        slug_to_id = {"acme": "uuid-1"}

        await sync_boards(mock_conn, sample_boards, slug_to_id, dry_run=True)

        # No fetchrow calls (company was in slug_to_id, and dry_run skips upsert)
        mock_conn.fetchrow.assert_not_called()
        # No execute calls (dry_run skips _DISABLE_REMOVED_BOARDS)
        mock_conn.execute.assert_not_called()

    async def test_disables_removed_boards(self, mock_conn):
        """After upserting, _DISABLE_REMOVED_BOARDS called with all URLs."""
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
            schema_overrides={
                c: pl.Utf8
                for c in [
                    "company_slug",
                    "board_slug",
                    "board_url",
                    "monitor_type",
                    "monitor_config",
                    "scraper_type",
                    "scraper_config",
                ]
            },
        )
        slug_to_id = {"acme": "uuid-1"}
        mock_conn.fetchrow.return_value = {"id": "board-uuid", "board_url": "x"}

        await sync_boards(mock_conn, boards, slug_to_id, dry_run=False)

        mock_conn.execute.assert_called_once_with(
            _DISABLE_REMOVED_BOARDS,
            ["https://acme.com/careers", "https://acme.com/internships"],
        )


# ---------------------------------------------------------------------------
# TestRunSync
# ---------------------------------------------------------------------------


class TestRunSync:
    @patch("src.sync.setup_logging")
    @patch("src.sync._load_boards")
    @patch("src.sync._load_companies")
    @patch("src.sync.create_pool")
    async def test_empty_csvs_returns_early(
        self, mock_create_pool, mock_load_companies, mock_load_boards, mock_setup_logging
    ):
        """Both CSVs empty -> pool not created."""
        mock_load_companies.return_value = pl.DataFrame(
            {"slug": [], "name": [], "website": [], "logo_url": [], "icon_url": []},
            schema_overrides=_COMPANY_SCHEMA,
        )
        mock_load_boards.return_value = pl.DataFrame(
            {
                "company_slug": [],
                "board_slug": [],
                "board_url": [],
                "monitor_type": [],
                "monitor_config": [],
                "scraper_type": [],
                "scraper_config": [],
            },
            schema_overrides={
                c: pl.Utf8
                for c in [
                    "company_slug",
                    "board_slug",
                    "board_url",
                    "monitor_type",
                    "monitor_config",
                    "scraper_type",
                    "scraper_config",
                ]
            },
        )

        await run_sync(dry_run=False)

        mock_create_pool.assert_not_called()

    @patch("src.sync.setup_logging")
    @patch("src.sync._load_boards")
    @patch("src.sync._load_companies")
    @patch("src.sync.close_pool")
    @patch("src.sync.create_pool")
    @patch("src.sync.sync_boards")
    @patch("src.sync.sync_companies")
    async def test_normal_flow(
        self,
        mock_sync_companies,
        mock_sync_boards,
        mock_create_pool,
        mock_close_pool,
        mock_load_companies,
        mock_load_boards,
        mock_setup_logging,
    ):
        """Calls sync_companies then sync_boards in transaction."""
        companies_df = pl.DataFrame(
            {
                "slug": ["acme"],
                "name": ["Acme Corp"],
                "website": ["https://acme.com"],
                "logo_url": [""],
                "icon_url": [""],
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
            schema_overrides={
                c: pl.Utf8
                for c in [
                    "company_slug",
                    "board_slug",
                    "board_url",
                    "monitor_type",
                    "monitor_config",
                    "scraper_type",
                    "scraper_config",
                ]
            },
        )
        mock_load_companies.return_value = companies_df
        mock_load_boards.return_value = boards_df

        # Set up pool + connection mock with proper async context managers
        mock_conn = MagicMock()
        # conn.transaction() must return a sync value that is an async CM
        mock_txn_cm = AsyncMock()
        mock_conn.transaction.return_value = mock_txn_cm

        mock_acquire_cm = AsyncMock()
        mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire_cm.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_acquire_cm
        mock_create_pool.return_value = mock_pool

        mock_sync_companies.return_value = {"acme": "uuid-1"}

        await run_sync(dry_run=False)

        mock_sync_companies.assert_called_once_with(mock_conn, companies_df, False)
        mock_sync_boards.assert_called_once_with(mock_conn, boards_df, {"acme": "uuid-1"}, False)
        mock_close_pool.assert_called_once()

    @patch("src.sync.setup_logging")
    @patch("src.sync._load_boards")
    @patch("src.sync._load_companies")
    @patch("src.sync.close_pool")
    @patch("src.sync.create_pool")
    @patch("src.sync.sync_companies")
    async def test_closes_pool_on_error(
        self,
        mock_sync_companies,
        mock_create_pool,
        mock_close_pool,
        mock_load_companies,
        mock_load_boards,
        mock_setup_logging,
    ):
        """sync_companies raises -> close_pool still called."""
        mock_load_companies.return_value = pl.DataFrame(
            {
                "slug": ["acme"],
                "name": ["Acme Corp"],
                "website": ["https://acme.com"],
                "logo_url": [""],
                "icon_url": [""],
            },
            schema_overrides=_COMPANY_SCHEMA,
        )
        mock_load_boards.return_value = pl.DataFrame(
            {
                "company_slug": ["acme"],
                "board_slug": ["acme-careers"],
                "board_url": ["https://acme.com/careers"],
                "monitor_type": ["greenhouse"],
                "monitor_config": ["{}"],
                "scraper_type": [""],
                "scraper_config": [""],
            },
            schema_overrides={
                c: pl.Utf8
                for c in [
                    "company_slug",
                    "board_slug",
                    "board_url",
                    "monitor_type",
                    "monitor_config",
                    "scraper_type",
                    "scraper_config",
                ]
            },
        )

        # Set up pool + connection mock with proper async context managers
        mock_conn = MagicMock()
        # conn.transaction() must return a sync value that is an async CM
        mock_txn_cm = AsyncMock()
        mock_conn.transaction.return_value = mock_txn_cm

        mock_acquire_cm = AsyncMock()
        mock_acquire_cm.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_acquire_cm.__aexit__ = AsyncMock(return_value=False)

        mock_pool = MagicMock()
        mock_pool.acquire.return_value = mock_acquire_cm
        mock_create_pool.return_value = mock_pool

        # sync_companies raises an error
        mock_sync_companies.side_effect = RuntimeError("DB connection failed")

        with pytest.raises(RuntimeError, match="DB connection failed"):
            await run_sync(dry_run=False)

        # close_pool should still be called despite the error
        mock_close_pool.assert_called_once()
