"""Guarded operator workflow for replacing a blocked Webshare proxy pool.

Replacement is an external mutation, so this module deliberately separates a
provider dry run from apply.  Apply accepts only the identifier of a completed
dry run for the exact current direct-IP pool and rechecks plan capacity plus
runtime backbone credentials before creating a real replacement.  Reports and
errors never include credentials or proxy addresses.
"""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlparse

import httpx
from dotenv import dotenv_values

from src.proxy_audit import ProxyAuditError

_API_BASE_URL = "https://proxy.webshare.io"
_DIRECT_LIST_PATH = "/api/v2/proxy/list/"
_REPLACEMENT_PATH = "/api/v3/proxy/replace/"
_MAX_POOL_SIZE = 64
_PENDING_STATES = frozenset({"validating", "processing"})
_SAFE_ERROR_CODE = re.compile(r"^[a-z0-9_]{1,80}$")
_VALIDATION_TTL = timedelta(minutes=15)
_CLOCK_SKEW_TOLERANCE = timedelta(seconds=30)


def _json_object(response: httpx.Response, operation: str) -> dict[str, Any]:
    if response.status_code >= 400:
        raise ProxyAuditError(f"Webshare {operation} returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProxyAuditError(f"Webshare {operation} returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProxyAuditError(f"Webshare {operation} returned an unexpected JSON shape")
    return payload


async def _request_object(
    client: httpx.AsyncClient,
    method: str,
    path: str,
    operation: str,
    *,
    params: Mapping[str, str | int] | None = None,
    body: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.request(method, path, params=params, json=body)
    except httpx.HTTPError as exc:
        raise ProxyAuditError(
            f"Webshare {operation} request failed ({type(exc).__name__})"
        ) from exc
    return _json_object(response, operation)


def _rows(payload: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    rows = payload.get("results")
    count = payload.get("count")
    if (
        not isinstance(rows, list)
        or any(not isinstance(row, dict) for row in rows)
        or not isinstance(count, int)
        or count != len(rows)
    ):
        raise ProxyAuditError(f"Webshare {operation} returned an incomplete proxy list")
    return rows


def _direct_addresses(payload: dict[str, Any]) -> frozenset[str]:
    addresses: set[str] = set()
    for row in _rows(payload, "direct proxy list"):
        address = row.get("proxy_address")
        if not isinstance(address, str) or not address or row.get("valid") is not True:
            raise ProxyAuditError("Webshare returned an invalid direct proxy entry")
        try:
            addresses.add(str(ip_address(address)))
        except ValueError as exc:
            raise ProxyAuditError("Webshare returned an invalid direct proxy entry") from exc
    if not addresses or len(addresses) > _MAX_POOL_SIZE:
        raise ProxyAuditError("Webshare returned an invalid direct proxy pool size")
    if len(addresses) != len(payload["results"]):
        raise ProxyAuditError("Webshare returned duplicate direct proxy entries")
    return frozenset(addresses)


def _backbone_signature_from_url(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.scheme not in {"http", "https"}
        or parsed.hostname != "p.webshare.io"
        or port is None
        or not parsed.username
        or parsed.password is None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        return None
    return unquote(parsed.username), unquote(parsed.password), port


def _backbone_signatures_from_rows(
    payload: dict[str, Any],
) -> frozenset[tuple[str, str, int]]:
    signatures: set[tuple[str, str, int]] = set()
    for row in _rows(payload, "backbone proxy list"):
        username = row.get("username")
        password = row.get("password")
        port = row.get("port")
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(password, str)
            or not password
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            or row.get("valid") is not True
        ):
            raise ProxyAuditError("Webshare returned an invalid backbone proxy entry")
        signatures.add((username, password, port))
    if not signatures or len(signatures) != len(payload["results"]):
        raise ProxyAuditError("Webshare returned an invalid backbone proxy pool")
    return frozenset(signatures)


def _configured_signatures(
    configured_pool_urls: Iterable[str],
) -> frozenset[tuple[str, str, int]]:
    urls = tuple(configured_pool_urls)
    signatures = tuple(_backbone_signature_from_url(url) for url in urls)
    if (
        not urls
        or len(urls) > _MAX_POOL_SIZE
        or any(signature is None for signature in signatures)
        or len(set(signatures)) != len(urls)
    ):
        raise ProxyAuditError("configured Webshare backbone pool is invalid")
    return frozenset(signature for signature in signatures if signature is not None)


def load_operator_proxy_inputs(env_file: Path) -> tuple[str, tuple[str, ...]]:
    """Load mutation credentials without changing process environment."""

    expanded = env_file.expanduser()
    if expanded.is_symlink():
        raise ProxyAuditError("proxy env path must be an existing regular file")
    try:
        resolved = expanded.resolve(strict=True)
    except OSError as exc:
        raise ProxyAuditError("proxy env path must be an existing regular file") from exc
    if not resolved.is_file():
        raise ProxyAuditError("proxy env path must be an existing regular file")
    try:
        values = dotenv_values(resolved)
    except (OSError, UnicodeError) as exc:
        raise ProxyAuditError("proxy env file could not be read") from exc

    api_key = values.get("WEBSHARE_API_KEY")
    raw_urls = values.get("WEBSHARE_PROXY_URLS")
    if not isinstance(api_key, str) or not api_key:
        raise ProxyAuditError("WEBSHARE_API_KEY is not configured in the operator env file")
    if not isinstance(raw_urls, str):
        raise ProxyAuditError("WEBSHARE_PROXY_URLS is not configured in the operator env file")
    try:
        decoded = json.loads(raw_urls)
    except json.JSONDecodeError as exc:
        raise ProxyAuditError("WEBSHARE_PROXY_URLS is not valid JSON") from exc
    if not isinstance(decoded, list) or any(not isinstance(url, str) for url in decoded):
        raise ProxyAuditError("WEBSHARE_PROXY_URLS must be a JSON string array")
    urls = tuple(decoded)
    _configured_signatures(urls)
    return api_key, urls


def _nonnegative_int(payload: Mapping[str, object], key: str, operation: str) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ProxyAuditError(f"Webshare {operation} omitted {key}")
    return value


def _replacement_id(payload: Mapping[str, object]) -> int:
    value = payload.get("id")
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ProxyAuditError("Webshare replacement omitted its identifier")
    return value


def _validation_expiry(payload: Mapping[str, object], *, now: datetime) -> datetime:
    raw_completed_at = payload.get("dry_run_completed_at")
    if not isinstance(raw_completed_at, str) or not raw_completed_at:
        raise ProxyAuditError("Webshare dry-run validation omitted its completion time")
    try:
        completed_at = datetime.fromisoformat(raw_completed_at.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ProxyAuditError("Webshare dry-run validation has an invalid completion time") from exc
    if completed_at.tzinfo is None or completed_at.utcoffset() is None:
        raise ProxyAuditError("Webshare dry-run validation has an invalid completion time")
    completed_at = completed_at.astimezone(UTC)
    if completed_at > now + _CLOCK_SKEW_TOLERANCE:
        raise ProxyAuditError("Webshare dry-run validation completion time is in the future")
    expires_at = completed_at + _VALIDATION_TTL
    if now > expires_at:
        raise ProxyAuditError("Webshare dry-run validation has expired")
    return expires_at


def _validate_replacement_shape(
    payload: Mapping[str, object],
    *,
    expected_addresses: frozenset[str],
    dry_run: bool,
) -> None:
    to_replace = payload.get("to_replace")
    replace_with = payload.get("replace_with")
    selected_addresses = to_replace.get("ip_addresses") if isinstance(to_replace, dict) else None
    if (
        not isinstance(to_replace, dict)
        or to_replace.get("type") != "ip_address"
        or not isinstance(selected_addresses, list)
        or any(not isinstance(address, str) for address in selected_addresses)
        or set(selected_addresses) != set(expected_addresses)
        or len(selected_addresses) != len(expected_addresses)
        or replace_with != [{"type": "any", "count": len(expected_addresses)}]
        or payload.get("dry_run") is not dry_run
    ):
        raise ProxyAuditError("Webshare replacement does not match the exact current pool")


async def _poll_replacement(
    client: httpx.AsyncClient,
    *,
    plan_id: int,
    replacement_id: int,
    terminal_state: str,
    poll_interval: float,
    max_polls: int,
) -> dict[str, Any]:
    for attempt in range(max_polls):
        payload = await _request_object(
            client,
            "GET",
            f"{_REPLACEMENT_PATH}{replacement_id}/",
            "replacement status",
            params={"plan_id": plan_id},
        )
        if _replacement_id(payload) != replacement_id:
            raise ProxyAuditError("Webshare replacement status returned a different identifier")
        state = payload.get("state")
        if state == terminal_state:
            return payload
        if state == "failed":
            error_code = payload.get("error_code")
            safe_code = (
                error_code
                if isinstance(error_code, str) and _SAFE_ERROR_CODE.fullmatch(error_code)
                else "unknown"
            )
            raise ProxyAuditError(f"Webshare replacement failed ({safe_code})")
        if state not in _PENDING_STATES:
            raise ProxyAuditError("Webshare replacement returned an unexpected state")
        if attempt + 1 < max_polls and poll_interval:
            await asyncio.sleep(poll_interval)
    raise ProxyAuditError("Webshare replacement did not finish within the polling budget")


async def replace_webshare_pool(
    *,
    api_key: str,
    configured_pool_urls: Iterable[str],
    apply_validation_id: int | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
    poll_interval: float = 1.0,
    max_polls: int = 120,
    now: datetime | None = None,
) -> dict[str, object]:
    """Validate or apply a whole-pool replacement and return sanitized facts."""

    if not api_key:
        raise ProxyAuditError("WEBSHARE_API_KEY is not configured")
    if apply_validation_id is not None and apply_validation_id < 1:
        raise ProxyAuditError("apply_validation_id must be a positive integer")
    if poll_interval < 0 or poll_interval > 10:
        raise ProxyAuditError("poll_interval must be between 0 and 10 seconds")
    if max_polls < 1 or max_polls > 600:
        raise ProxyAuditError("max_polls must be between 1 and 600")
    if now is not None and (now.tzinfo is None or now.utcoffset() is None):
        raise ProxyAuditError("now must include a timezone")

    def current_time() -> datetime:
        return now.astimezone(UTC) if now is not None else datetime.now(UTC)

    configured_signatures = _configured_signatures(configured_pool_urls)
    headers = {
        "Authorization": f"Token {api_key}",
        "Accept": "application/json",
        "User-Agent": "jobseek-proxy-replacement/1",
    }
    async with httpx.AsyncClient(
        base_url=_API_BASE_URL,
        headers=headers,
        timeout=httpx.Timeout(20.0),
        follow_redirects=False,
        transport=transport,
    ) as client:
        subscription = await _request_object(client, "GET", "/api/v2/subscription/", "subscription")
        plan_id = subscription.get("plan")
        if not isinstance(plan_id, int) or isinstance(plan_id, bool) or plan_id < 1:
            raise ProxyAuditError("Webshare subscription omitted its active plan")
        plan = await _request_object(
            client,
            "GET",
            f"/api/v2/subscription/plan/{plan_id}/",
            "plan",
        )
        if (
            plan.get("status") != "active"
            or subscription.get("paused") is True
            or subscription.get("throttled") is True
        ):
            raise ProxyAuditError("Webshare subscription is not active")

        direct_before = await _request_object(
            client,
            "GET",
            _DIRECT_LIST_PATH,
            "direct proxy list",
            params={"mode": "direct", "page": 1, "page_size": 100},
        )
        addresses_before = _direct_addresses(direct_before)
        backbone_before = await _request_object(
            client,
            "GET",
            _DIRECT_LIST_PATH,
            "backbone proxy list",
            params={"mode": "backbone", "page": 1, "page_size": 100},
        )
        backbone_signatures_before = _backbone_signatures_from_rows(backbone_before)
        if configured_signatures != backbone_signatures_before:
            raise ProxyAuditError(
                "configured runtime pool does not match Webshare before replacement"
            )

        available_before = _nonnegative_int(plan, "proxy_replacements_available", "plan")
        pool_size = len(addresses_before)
        if _nonnegative_int(plan, "proxy_count", "plan") != pool_size:
            raise ProxyAuditError("Webshare plan proxy count does not match the current pool")
        if available_before < pool_size:
            raise ProxyAuditError("Webshare plan lacks capacity for a whole-pool replacement")

        request_body: dict[str, object] = {
            "to_replace": {
                "type": "ip_address",
                "ip_addresses": sorted(addresses_before),
            },
            "replace_with": [{"type": "any", "count": pool_size}],
            "dry_run": apply_validation_id is None,
        }

        if apply_validation_id is None:
            created = await _request_object(
                client,
                "POST",
                _REPLACEMENT_PATH,
                "dry-run replacement",
                params={"plan_id": plan_id},
                body=request_body,
            )
            replacement_id = _replacement_id(created)
            _validate_replacement_shape(created, expected_addresses=addresses_before, dry_run=True)
            validated = await _poll_replacement(
                client,
                plan_id=plan_id,
                replacement_id=replacement_id,
                terminal_state="validated",
                poll_interval=poll_interval,
                max_polls=max_polls,
            )
            _validate_replacement_shape(
                validated, expected_addresses=addresses_before, dry_run=True
            )
            removed = _nonnegative_int(validated, "proxies_removed", "dry-run replacement")
            added = _nonnegative_int(validated, "proxies_added", "dry-run replacement")
            if removed != pool_size or added != pool_size:
                raise ProxyAuditError("Webshare dry run did not validate a whole-pool replacement")
            validation_expires_at = _validation_expiry(validated, now=current_time())
            return {
                "status": "validated",
                "mode": "dry-run",
                "validation_id": replacement_id,
                "validation_expires_at": validation_expires_at.isoformat().replace("+00:00", "Z"),
                "validation_ttl_seconds": int(_VALIDATION_TTL.total_seconds()),
                "pool_size": pool_size,
                "replacements_available_before": available_before,
                "proxies_to_remove": removed,
                "proxies_to_add": added,
                "runtime_pool_matches_before": True,
            }

        validation = await _request_object(
            client,
            "GET",
            f"{_REPLACEMENT_PATH}{apply_validation_id}/",
            "dry-run validation",
            params={"plan_id": plan_id},
        )
        if _replacement_id(validation) != apply_validation_id:
            raise ProxyAuditError("Webshare dry-run validation returned a different identifier")
        if validation.get("state") != "validated":
            raise ProxyAuditError("Webshare dry-run validation is not complete")
        # Check freshness immediately before the only mutating request. With a
        # real clock this excludes time spent on all preceding preflight I/O.
        _validation_expiry(validation, now=current_time())
        _validate_replacement_shape(validation, expected_addresses=addresses_before, dry_run=True)
        if (
            _nonnegative_int(validation, "proxies_removed", "dry-run validation") != pool_size
            or _nonnegative_int(validation, "proxies_added", "dry-run validation") != pool_size
        ):
            raise ProxyAuditError("Webshare dry run did not validate a whole-pool replacement")

        created = await _request_object(
            client,
            "POST",
            _REPLACEMENT_PATH,
            "replacement apply",
            params={"plan_id": plan_id},
            body=request_body,
        )
        replacement_id = _replacement_id(created)
        _validate_replacement_shape(created, expected_addresses=addresses_before, dry_run=False)
        completed = await _poll_replacement(
            client,
            plan_id=plan_id,
            replacement_id=replacement_id,
            terminal_state="completed",
            poll_interval=poll_interval,
            max_polls=max_polls,
        )
        _validate_replacement_shape(completed, expected_addresses=addresses_before, dry_run=False)
        removed = _nonnegative_int(completed, "proxies_removed", "replacement apply")
        added = _nonnegative_int(completed, "proxies_added", "replacement apply")

        direct_after = await _request_object(
            client,
            "GET",
            _DIRECT_LIST_PATH,
            "post-replacement direct proxy list",
            params={"mode": "direct", "page": 1, "page_size": 100},
        )
        addresses_after = _direct_addresses(direct_after)
        backbone_after = await _request_object(
            client,
            "GET",
            _DIRECT_LIST_PATH,
            "post-replacement backbone proxy list",
            params={"mode": "backbone", "page": 1, "page_size": 100},
        )
        backbone_signatures_after = _backbone_signatures_from_rows(backbone_after)
        plan_after = await _request_object(
            client,
            "GET",
            f"/api/v2/subscription/plan/{plan_id}/",
            "post-replacement plan",
        )
        available_after = _nonnegative_int(
            plan_after, "proxy_replacements_available", "post-replacement plan"
        )
        capacity_decremented = available_after == available_before - pool_size
        exact_replacement = (
            removed == pool_size
            and added == pool_size
            and len(addresses_after) == pool_size
            and addresses_before.isdisjoint(addresses_after)
        )
        runtime_match = configured_signatures == backbone_signatures_after
        return {
            "status": (
                "completed"
                if exact_replacement and runtime_match and capacity_decremented
                else "attention"
            ),
            "mode": "apply",
            "validation_id": apply_validation_id,
            "replacement_id": replacement_id,
            "pool_size": pool_size,
            "replacements_available_before": available_before,
            "replacements_available_after": available_after,
            "replacement_capacity_decremented": capacity_decremented,
            "proxies_removed": removed,
            "proxies_added": added,
            "direct_pool_fully_changed": addresses_before.isdisjoint(addresses_after),
            "direct_pool_size_preserved": len(addresses_after) == pool_size,
            "runtime_pool_matches_after": runtime_match,
        }
