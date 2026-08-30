from __future__ import annotations

import json
import stat
from datetime import UTC, datetime

import httpx

from src.proxy_config import fetch_webshare_backbone_urls, update_proxy_env_file


async def test_fetch_builds_month_refresh_safe_backbone_urls():
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "proxy.webshare.io"
        assert request.url.path == "/api/v2/proxy/list/"
        assert request.headers["authorization"] == "Token operator-secret"
        return httpx.Response(
            200,
            json={
                "count": 2,
                "next": None,
                "results": [
                    {
                        "username": "pool-user-a",
                        "password": "secret/a",
                        "port": 10000,
                        "valid": True,
                    },
                    {
                        "username": "pool-user-b",
                        "password": "secret:b",
                        "port": 10001,
                        "valid": True,
                    },
                ],
            },
            request=request,
        )

    urls = await fetch_webshare_backbone_urls(
        "operator-secret",
        transport=httpx.MockTransport(handler),
    )

    assert urls == (
        "http://pool-user-a:secret%2Fa@p.webshare.io:10000",
        "http://pool-user-b:secret%3Ab@p.webshare.io:10001",
    )


def test_env_update_always_backs_up_is_atomic_and_retires_decodo(tmp_path):
    env_file = tmp_path / ".env.local"
    original = (
        "# Proxy settings\n"
        "PROXY_PROVIDER=none\n"
        "WEBSHARE_PROXY_URL=http://legacy:secret@direct.example:7000\n"
        "DECODO_PROXY_URL=http://retired:secret@retired.example:10001\n"
        "UNRELATED=value\n"
    )
    env_file.write_text(original, encoding="utf-8")
    env_file.chmod(0o644)
    urls = (
        "http://pool-user-a:secret-a@p.webshare.io:10000",
        "http://pool-user-b:secret-b@p.webshare.io:10001",
    )

    result = update_proxy_env_file(
        env_file,
        webshare_urls=urls,
        expected_client_ips=("192.0.2.10",),
        now=datetime(2026, 8, 30, 12, tzinfo=UTC),
    )

    backup = tmp_path / ".env.local.backup-20260830T120000.000000Z"
    assert backup.read_text(encoding="utf-8") == original
    assert stat.S_IMODE(backup.stat().st_mode) == 0o600
    assert stat.S_IMODE(env_file.stat().st_mode) == 0o600

    updated = env_file.read_text(encoding="utf-8")
    assert "PROXY_PROVIDER=webshare\n" in updated
    assert f"WEBSHARE_PROXY_URLS={json.dumps(list(urls), separators=(',', ':'))}\n" in updated
    assert 'WEBSHARE_EXPECTED_CLIENT_IPS=["192.0.2.10"]\n' in updated
    assert "DECODO_PROXY_URL" not in updated
    assert "WEBSHARE_PROXY_URL=http://legacy:secret@direct.example:7000" in updated
    assert "UNRELATED=value" in updated
    assert result == {
        "status": "updated",
        "env_file": str(env_file),
        "backup_file": str(backup),
        "pool_size": 2,
        "expected_source_count": 1,
        "retired_keys_removed": ["DECODO_PROXY_URL"],
    }
