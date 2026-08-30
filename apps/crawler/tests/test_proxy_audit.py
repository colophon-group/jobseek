from __future__ import annotations

import json
from datetime import UTC, datetime
from urllib.parse import parse_qs

import httpx
import pytest

from src.proxy_audit import ProxyAuditError, _next_activity_cursor, audit_webshare

NOW = datetime(2026, 8, 30, 12, tzinfo=UTC)
POOL_URL = "http://pool-user:pool-secret@p.webshare.io:10000"
LEGACY_URL = "http://direct-user:direct-secret@198.51.100.20:7000"


def _transport(*, activity_rows: list[dict], activity_count: int | None = None):
    async def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        query = parse_qs(request.url.query.decode())
        if path == "/api/v2/subscription/":
            payload = {
                "plan": 42,
                "term": "monthly",
                "start_date": "2026-08-01T00:00:00Z",
                "end_date": "2026-09-01T00:00:00Z",
                "renewals_enabled": True,
                "paused": False,
                "throttled": False,
            }
        elif path == "/api/v2/subscription/plan/42/":
            payload = {
                "status": "active",
                "proxy_type": "shared",
                "proxy_subtype": "isp",
                "proxy_count": 1,
                "bandwidth_limit": 1000,
                "automatic_refresh_frequency": 2_592_000,
                "automatic_refresh_last_at": "2026-08-01T00:00:00Z",
                "automatic_refresh_next_at": "2026-09-29T00:00:00Z",
            }
        elif path == "/api/v2/proxy/list/" and query.get("mode") == ["direct"]:
            payload = {
                "count": 1,
                "results": [
                    {
                        "proxy_address": "198.51.100.20",
                        "port": 7000,
                        "username": "direct-user",
                        "password": "direct-secret",
                        "valid": True,
                    }
                ],
            }
        elif path == "/api/v2/proxy/list/" and query.get("mode") == ["backbone"]:
            payload = {
                "count": 1,
                "results": [
                    {
                        "proxy_address": "unused-by-backbone.example",
                        "port": 10000,
                        "username": "pool-user",
                        "password": "pool-secret",
                        "valid": True,
                    }
                ],
            }
        elif path == "/api/v2/stats/aggregate/":
            payload = {
                "bandwidth_total": 250_000_000_000,
                "requests_total": 500,
                "requests_successful": 490,
                "requests_failed": 10,
            }
        elif path == "/api/v2/proxy/activity/":
            payload = {
                "count": activity_count if activity_count is not None else len(activity_rows),
                "next": None,
                "results": activity_rows,
            }
        else:  # pragma: no cover - makes an unexpected provider path obvious
            return httpx.Response(404, request=request)
        return httpx.Response(200, json=payload, request=request)

    return httpx.MockTransport(handler)


async def test_audit_reports_matching_pool_without_emitting_sensitive_values():
    report = await audit_webshare(
        api_key="operator-api-secret",
        configured_pool_urls=[POOL_URL],
        legacy_proxy_url=LEGACY_URL,
        expected_client_ips=["192.0.2.10"],
        since_hours=24,
        now=NOW,
        transport=_transport(activity_rows=[{"client_address": "192.0.2.10"}]),
    )

    assert report["status"] == "inconclusive"
    assert report["pool"] == {
        "direct_count": 1,
        "direct_valid_count": 1,
        "backbone_count": 1,
        "configured_backbone_count": 1,
        "configured_backbone_match_count": 1,
        "legacy_direct_configured": True,
        "legacy_direct_matches_current_list": True,
    }
    assert report["client_sources"]["assessment"] == "expected_only"

    encoded = json.dumps(report, sort_keys=True)
    for sensitive in (
        "operator-api-secret",
        "pool-user",
        "pool-secret",
        "direct-user",
        "direct-secret",
        "192.0.2.10",
        "198.51.100.20",
        "unused-by-backbone.example",
    ):
        assert sensitive not in encoded


async def test_positive_unknown_source_evidence_alerts_even_when_truncated():
    report = await audit_webshare(
        api_key="operator-api-secret",
        configured_pool_urls=[POOL_URL],
        legacy_proxy_url="",
        expected_client_ips=["192.0.2.10"],
        since_hours=24,
        max_activity_records=1,
        now=NOW,
        transport=_transport(
            activity_rows=[{"client_address": "203.0.113.50"}],
            activity_count=100,
        ),
    )

    assert report["status"] == "alert"
    assert "unexpected_client_sources" in report["problems"]
    assert report["client_sources"]["unknown_source_count"] == 1
    assert "203.0.113.50" not in json.dumps(report)


def test_activity_cursor_rejects_provider_redirect_to_untrusted_host():
    with pytest.raises(ProxyAuditError, match="unexpected next URL"):
        _next_activity_cursor(
            {"next": ("https://attacker.example/api/v2/proxy/activity/?starting_after=secret")}
        )
