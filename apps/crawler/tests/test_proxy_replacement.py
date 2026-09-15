from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from src.proxy_audit import ProxyAuditError
from src.proxy_replacement import load_operator_proxy_inputs, replace_webshare_pool

POOL_URLS = (
    "http://pool-user-a:pool-secret-a@p.webshare.io:10000",
    "http://pool-user-b:pool-secret-b@p.webshare.io:10001",
)
OLD_ADDRESSES = ["192.0.2.10", "192.0.2.11"]
NEW_ADDRESSES = ["198.51.100.20", "198.51.100.21"]
NOW = datetime(2026, 9, 15, 12, tzinfo=UTC)


def _proxy_list(addresses: list[str], *, backbone: bool = False) -> dict[str, object]:
    results = []
    for index, address in enumerate(addresses):
        results.append(
            {
                "proxy_address": address,
                "port": 10000 + index,
                "username": f"pool-user-{chr(ord('a') + index)}",
                "password": f"pool-secret-{chr(ord('a') + index)}",
                "valid": True,
            }
        )
    return {"count": len(results), "next": None, "results": results}


def _replacement(
    replacement_id: int,
    *,
    dry_run: bool,
    state: str,
    addresses: list[str] | None = None,
    dry_run_completed_at: str | None = None,
) -> dict[str, object]:
    selected = addresses or OLD_ADDRESSES
    terminal = state in {"validated", "completed"}
    return {
        "id": replacement_id,
        "to_replace": {"type": "ip_address", "ip_addresses": selected},
        "replace_with": [{"type": "any", "count": len(selected)}],
        "dry_run": dry_run,
        "state": state,
        "proxies_removed": len(selected) if terminal else None,
        "proxies_added": len(selected) if terminal else None,
        "reason": "",
        "error": None,
        "error_code": None,
        "dry_run_completed_at": (
            dry_run_completed_at
            if dry_run_completed_at is not None
            else NOW.isoformat()
            if dry_run and state == "validated"
            else None
        ),
    }


def _common_response(request: httpx.Request) -> httpx.Response | None:
    if request.url.path == "/api/v2/subscription/":
        return httpx.Response(
            200,
            json={"plan": 42, "paused": False, "throttled": False},
            request=request,
        )
    if request.url.path == "/api/v2/subscription/plan/42/":
        return httpx.Response(
            200,
            json={
                "status": "active",
                "proxy_count": 2,
                "proxy_replacements_available": 10,
            },
            request=request,
        )
    return None


async def test_dry_run_validates_exact_pool_and_never_emits_secrets_or_addresses():
    status_polls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal status_polls
        assert request.headers["authorization"] == "Token operator-api-secret"
        if response := _common_response(request):
            return response
        if request.url.path == "/api/v2/proxy/list/":
            mode = request.url.params["mode"]
            payload = (
                _proxy_list(OLD_ADDRESSES, backbone=True)
                if mode == "backbone"
                else _proxy_list(OLD_ADDRESSES)
            )
            return httpx.Response(200, json=payload, request=request)
        if request.method == "POST" and request.url.path == "/api/v3/proxy/replace/":
            assert json.loads(request.content) == {
                "to_replace": {
                    "type": "ip_address",
                    "ip_addresses": OLD_ADDRESSES,
                },
                "replace_with": [{"type": "any", "count": 2}],
                "dry_run": True,
            }
            return httpx.Response(
                201, json=_replacement(501, dry_run=True, state="validating"), request=request
            )
        if request.url.path == "/api/v3/proxy/replace/501/":
            status_polls += 1
            state = "validating" if status_polls == 1 else "validated"
            return httpx.Response(
                200, json=_replacement(501, dry_run=True, state=state), request=request
            )
        return httpx.Response(404, request=request)

    report = await replace_webshare_pool(
        api_key="operator-api-secret",
        configured_pool_urls=POOL_URLS,
        transport=httpx.MockTransport(handler),
        poll_interval=0,
        now=NOW,
    )

    assert report == {
        "status": "validated",
        "mode": "dry-run",
        "validation_id": 501,
        "validation_expires_at": "2026-09-15T12:15:00Z",
        "validation_ttl_seconds": 900,
        "pool_size": 2,
        "replacements_available_before": 10,
        "proxies_to_remove": 2,
        "proxies_to_add": 2,
        "runtime_pool_matches_before": True,
    }
    encoded = json.dumps(report)
    for sensitive in (
        "operator-api-secret",
        "pool-user-a",
        "pool-secret-a",
        *OLD_ADDRESSES,
    ):
        assert sensitive not in encoded


async def test_apply_requires_validation_then_proves_full_rotation_and_runtime_continuity():
    direct_reads = 0
    backbone_reads = 0
    plan_reads = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal direct_reads, backbone_reads, plan_reads
        if request.url.path == "/api/v2/subscription/plan/42/":
            plan_reads += 1
            available = 10 if plan_reads == 1 else 8
            return httpx.Response(
                200,
                json={
                    "status": "active",
                    "proxy_count": 2,
                    "proxy_replacements_available": available,
                },
                request=request,
            )
        if response := _common_response(request):
            return response
        if request.url.path == "/api/v2/proxy/list/":
            if request.url.params["mode"] == "direct":
                direct_reads += 1
                addresses = OLD_ADDRESSES if direct_reads == 1 else NEW_ADDRESSES
                return httpx.Response(200, json=_proxy_list(addresses), request=request)
            backbone_reads += 1
            return httpx.Response(
                200, json=_proxy_list(OLD_ADDRESSES, backbone=True), request=request
            )
        if request.method == "GET" and request.url.path == "/api/v3/proxy/replace/501/":
            return httpx.Response(
                200,
                json=_replacement(501, dry_run=True, state="validated"),
                request=request,
            )
        if request.method == "POST" and request.url.path == "/api/v3/proxy/replace/":
            body = json.loads(request.content)
            assert body["dry_run"] is False
            return httpx.Response(
                201,
                json=_replacement(502, dry_run=False, state="processing"),
                request=request,
            )
        if request.url.path == "/api/v3/proxy/replace/502/":
            return httpx.Response(
                200,
                json=_replacement(502, dry_run=False, state="completed"),
                request=request,
            )
        return httpx.Response(404, request=request)

    report = await replace_webshare_pool(
        api_key="operator-api-secret",
        configured_pool_urls=POOL_URLS,
        apply_validation_id=501,
        transport=httpx.MockTransport(handler),
        poll_interval=0,
        now=NOW,
    )

    assert report == {
        "status": "completed",
        "mode": "apply",
        "validation_id": 501,
        "replacement_id": 502,
        "pool_size": 2,
        "replacements_available_before": 10,
        "replacements_available_after": 8,
        "replacement_capacity_decremented": True,
        "proxies_removed": 2,
        "proxies_added": 2,
        "direct_pool_fully_changed": True,
        "direct_pool_size_preserved": True,
        "runtime_pool_matches_after": True,
    }
    assert direct_reads == 2
    assert backbone_reads == 2
    assert plan_reads == 2


async def test_apply_rejects_stale_validation_without_posting_mutation():
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if response := _common_response(request):
            return response
        if request.url.path == "/api/v2/proxy/list/":
            return httpx.Response(
                200, json=_proxy_list(OLD_ADDRESSES, backbone=True), request=request
            )
        if request.method == "GET" and request.url.path == "/api/v3/proxy/replace/501/":
            return httpx.Response(
                200,
                json=_replacement(
                    501,
                    dry_run=True,
                    state="validated",
                    addresses=[OLD_ADDRESSES[0]],
                ),
                request=request,
            )
        if request.method == "POST":
            post_count += 1
        return httpx.Response(404, request=request)

    with pytest.raises(ProxyAuditError, match="exact current pool"):
        await replace_webshare_pool(
            api_key="operator-api-secret",
            configured_pool_urls=POOL_URLS,
            apply_validation_id=501,
            transport=httpx.MockTransport(handler),
            poll_interval=0,
            now=NOW,
        )

    assert post_count == 0


async def test_provider_failure_exposes_only_safe_error_code():
    async def handler(request: httpx.Request) -> httpx.Response:
        if response := _common_response(request):
            return response
        if request.url.path == "/api/v2/proxy/list/":
            return httpx.Response(
                200, json=_proxy_list(OLD_ADDRESSES, backbone=True), request=request
            )
        if request.method == "POST":
            return httpx.Response(
                201,
                json=_replacement(501, dry_run=True, state="validating"),
                request=request,
            )
        return httpx.Response(
            200,
            json={
                **_replacement(501, dry_run=True, state="failed"),
                "error_code": "no_proxies_to_be_replaced",
                "error": "sensitive provider detail 192.0.2.10 pool-secret-a",
            },
            request=request,
        )

    with pytest.raises(ProxyAuditError) as caught:
        await replace_webshare_pool(
            api_key="operator-api-secret",
            configured_pool_urls=POOL_URLS,
            transport=httpx.MockTransport(handler),
            poll_interval=0,
            now=NOW,
        )

    assert str(caught.value) == "Webshare replacement failed (no_proxies_to_be_replaced)"
    assert "192.0.2.10" not in str(caught.value)
    assert "pool-secret-a" not in str(caught.value)


@pytest.mark.parametrize(
    ("completed_at", "message"),
    [
        ((NOW - timedelta(minutes=16)).isoformat(), "has expired"),
        ((NOW + timedelta(minutes=1)).isoformat(), "in the future"),
        ("not-a-timestamp", "invalid completion time"),
        (NOW.replace(tzinfo=None).isoformat(), "invalid completion time"),
        ("", "omitted its completion time"),
    ],
)
async def test_apply_rejects_unfresh_validation_without_posting_mutation(
    completed_at: str, message: str
):
    post_count = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal post_count
        if response := _common_response(request):
            return response
        if request.url.path == "/api/v2/proxy/list/":
            return httpx.Response(
                200, json=_proxy_list(OLD_ADDRESSES, backbone=True), request=request
            )
        if request.method == "GET" and request.url.path == "/api/v3/proxy/replace/501/":
            payload = _replacement(
                501,
                dry_run=True,
                state="validated",
                dry_run_completed_at=completed_at,
            )
            if completed_at == "":
                payload["dry_run_completed_at"] = None
            return httpx.Response(200, json=payload, request=request)
        if request.method == "POST":
            post_count += 1
        return httpx.Response(404, request=request)

    with pytest.raises(ProxyAuditError, match=message):
        await replace_webshare_pool(
            api_key="operator-api-secret",
            configured_pool_urls=POOL_URLS,
            apply_validation_id=501,
            transport=httpx.MockTransport(handler),
            poll_interval=0,
            now=NOW,
        )

    assert post_count == 0


def test_operator_inputs_are_loaded_without_mutating_environment(tmp_path, monkeypatch):
    env_file = tmp_path / ".env.local"
    env_file.write_text(
        f"WEBSHARE_API_KEY=operator-api-secret\nWEBSHARE_PROXY_URLS={json.dumps(POOL_URLS)}\n",
        encoding="utf-8",
    )
    monkeypatch.delenv("WEBSHARE_API_KEY", raising=False)

    api_key, urls = load_operator_proxy_inputs(env_file)

    assert api_key == "operator-api-secret"
    assert urls == POOL_URLS


async def test_naive_test_clock_is_rejected_before_provider_requests():
    with pytest.raises(ProxyAuditError, match="now must include a timezone"):
        await replace_webshare_pool(
            api_key="operator-api-secret",
            configured_pool_urls=POOL_URLS,
            now=datetime(2026, 9, 15, 12),
        )
