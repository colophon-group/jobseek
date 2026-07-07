from __future__ import annotations

import json

from src.core.monitor import MonitorResult
from src.core.scrapers import JobContent
from src.processing.board import dry_run_single_board


class FakePool:
    def __init__(self, row):
        self.row = row

    async def fetchrow(self, query, board_slug):
        return self.row


class FakeHttpClient:
    def __init__(self, name: str = "default"):
        self.name = name
        self.closed = False

    async def aclose(self):
        self.closed = True


def _board_row(metadata: dict):
    return {
        "board_url": "https://www.mcdonalds.com.ph/careers",
        "crawler_type": "dom",
        "metadata": json.dumps(metadata),
    }


async def test_dry_run_monitor_uses_proxy_client(monkeypatch):
    default_http = FakeHttpClient()
    created: list[tuple[FakeHttpClient, bool, bool]] = []
    monitor_clients: list[FakeHttpClient] = []

    def create_http_client(*, verify=True, use_proxy=False):
        client = FakeHttpClient("owned")
        created.append((client, verify, use_proxy))
        return client

    async def monitor_one(board_url, crawler_type, metadata, http, pw=None):
        monitor_clients.append(http)
        return MonitorResult(urls=set())

    monkeypatch.setattr("src.shared.http.create_http_client", create_http_client)
    monkeypatch.setattr("src.batch.monitor_one", monitor_one)

    await dry_run_single_board(
        FakePool(_board_row({"url_filter": "/career/", "proxy": True})),
        default_http,
        "mcdonalds-ph",
    )

    assert len(created) == 1
    owned_client, verify, use_proxy = created[0]
    assert verify is True
    assert use_proxy is True
    assert monitor_clients == [owned_client]
    assert owned_client.closed is True
    assert default_http.closed is False


async def test_dry_run_scraper_uses_proxy_client(monkeypatch):
    default_http = FakeHttpClient()
    created: list[tuple[FakeHttpClient, bool, bool]] = []
    monitor_clients: list[FakeHttpClient] = []
    scrape_clients: list[FakeHttpClient] = []

    def create_http_client(*, verify=True, use_proxy=False):
        client = FakeHttpClient("owned")
        created.append((client, verify, use_proxy))
        return client

    async def monitor_one(board_url, crawler_type, metadata, http, pw=None):
        monitor_clients.append(http)
        return MonitorResult(urls={"https://www.mcdonalds.com.ph/career/manager-trainee-ncr"})

    async def scrape_one(url, scraper_type, scraper_config, http, pw=None):
        scrape_clients.append(http)
        return JobContent(title="Manager Trainee", description="<p>Work with us.</p>")

    monkeypatch.setattr("src.shared.http.create_http_client", create_http_client)
    monkeypatch.setattr("src.batch.monitor_one", monitor_one)
    monkeypatch.setattr("src.batch.scrape_one", scrape_one)

    await dry_run_single_board(
        FakePool(
            _board_row(
                {
                    "url_filter": "/career/",
                    "scraper_type": "dom",
                    "scraper_config": {"proxy": True},
                }
            )
        ),
        default_http,
        "mcdonalds-ph",
    )

    assert len(created) == 1
    owned_client, verify, use_proxy = created[0]
    assert verify is True
    assert use_proxy is True
    assert monitor_clients == [default_http]
    assert scrape_clients == [owned_client]
    assert owned_client.closed is True
    assert default_http.closed is False
