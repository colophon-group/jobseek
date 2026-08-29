from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import polars as pl

from src.sync import _UPSERT_BOARD_LOCAL, sync_boards


async def test_sync_routes_supported_scraper_fallback_chains_to_browser() -> None:
    board_urls = [
        "https://example.test/kpmg-like",
        "https://example.test/chained-browser",
        "https://example.test/static-chain",
        "https://example.test/browser-primary",
    ]
    scraper_configs = [
        '{"fallback":{"type":"dom","config":{"render":true}}}',
        (
            '{"fallback":{"type":"dom","config":{"render":false,'
            '"fallback":{"type":"nextdata","config":{"render":true}}}}}'
        ),
        (
            '{"fallback":{"type":"dom","config":{"render":false,'
            '"fallback":{"type":"embedded","config":{"render":false}}}}}'
        ),
        '{"render":true}',
    ]
    boards = pl.DataFrame(
        {
            "company_slug": ["acme"] * len(board_urls),
            "board_slug": ["kpmg-like", "chained-browser", "static-chain", "browser-primary"],
            "board_url": board_urls,
            "monitor_type": ["greenhouse"] * len(board_urls),
            "monitor_config": ["{}"] * len(board_urls),
            "scraper_type": ["json-ld", "json-ld", "json-ld", "dom"],
            "scraper_config": scraper_configs,
        }
    )
    local_conn = AsyncMock()

    async def fetch(sql: str, *args: object) -> list[dict[str, object]]:
        if sql != _UPSERT_BOARD_LOCAL:
            return []
        company_slugs = args[0]
        urls = args[2]
        assert isinstance(company_slugs, list)
        assert isinstance(urls, list)
        return [
            {
                "board_id": str(uuid.uuid5(uuid.NAMESPACE_URL, board_url)),
                "company_id": str(uuid.uuid5(uuid.NAMESPACE_DNS, company_slug)),
                "board_url": board_url,
                "metadata": {},
            }
            for company_slug, board_url in zip(company_slugs, urls, strict=True)
        ]

    local_conn.fetch = AsyncMock(side_effect=fetch)
    local_conn.execute = AsyncMock()

    effects = await sync_boards(local_conn, boards, dry_run=False)

    upsert_call = next(
        call for call in local_conn.fetch.await_args_list if call.args[0] == _UPSERT_BOARD_LOCAL
    )
    assert upsert_call.args[10] == [True, True, False, True]
    assert [schedule.config["scraper_needs_browser"] for schedule in effects.schedules] == [
        "1",
        "1",
        "0",
        "1",
    ]
