"""End-to-end command-boundary tests for scheduled Typesense refreshes."""

from __future__ import annotations

import asyncio
from argparse import Namespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from src import cli


class _Acquire:
    def __init__(self, connection: AsyncMock) -> None:
        self._connection = connection

    async def __aenter__(self) -> AsyncMock:
        return self._connection

    async def __aexit__(self, *_args) -> None:
        return None


class _Pool:
    def __init__(self, connection: AsyncMock) -> None:
        self._connection = connection

    def acquire(self) -> _Acquire:
        return _Acquire(self._connection)


@pytest.mark.asyncio
async def test_rejected_count_import_records_failed_cron_and_skips_watchlist_stage(
    monkeypatch,
) -> None:
    """One rejected document must make the scheduled command unsuccessful."""
    monkeypatch.setenv("CRAWLER_PUSHGATEWAY_URL", "http://pushgateway.test:9091")
    local_conn = AsyncMock()
    local_conn.fetch.return_value = [
        {"collection": "location", "document_id": "10", "facet_value": "10"},
        {"collection": "occupation", "document_id": "10-en", "facet_value": "10"},
        {"collection": "seniority", "document_id": "10-en", "facet_value": "10"},
        {"collection": "technology", "document_id": "10", "facet_value": "10"},
        {"collection": "company", "document_id": "10", "facet_value": "10"},
    ]
    web_conn = AsyncMock()
    client = MagicMock()
    collections = {
        name: MagicMock()
        for name in ("location", "occupation", "seniority", "technology", "company")
    }
    client.collections.__getitem__.side_effect = collections.__getitem__
    collections["location"].documents.import_.return_value = [
        {"success": False, "error": "document contains private details"}
    ]

    captured_registries = []

    def _capture_registry(*_args, **kwargs) -> None:
        captured_registries.append(kwargs["registry"])

    def _facet_counts(_client, field, _filter_by=None):
        return {"10": 1} if field == "location_ids" else {}

    loop = asyncio.get_running_loop()
    with (
        patch.object(loop, "add_signal_handler"),
        patch("src.cli.parse_args", return_value=Namespace(command="refresh-typesense")),
        patch("src.cli.setup_logging"),
        patch("src.cli.create_local_pool", new=AsyncMock(return_value=_Pool(local_conn))),
        patch("src.cli.create_web_pool", new=AsyncMock(return_value=_Pool(web_conn))),
        patch("src.cli.close_all_pools", new=AsyncMock()) as close_pools,
        patch("src.typesense_client.get_typesense_client", return_value=client),
        patch("src.sync._fetch_facet_counts", side_effect=_facet_counts),
        patch("prometheus_client.push_to_gateway", side_effect=_capture_registry),
        pytest.raises(
            RuntimeError,
            match=(
                "collection=location, action=update, expected_count=1, "
                "acknowledged_count=1, successful_count=0"
            ),
        ),
    ):
        await cli.run()

    web_conn.fetch.assert_not_awaited()
    close_pools.assert_awaited_once()
    assert len(captured_registries) == 1
    status_samples = [
        sample
        for metric in captured_registries[0].collect()
        for sample in metric.samples
        if sample.name == "crawler_cron_last_run_status"
    ]
    assert len(status_samples) == 1
    assert status_samples[0].labels == {"job": "refresh-typesense"}
    assert status_samples[0].value == 0.0
