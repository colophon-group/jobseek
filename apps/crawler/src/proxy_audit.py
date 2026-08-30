"""Read-only, credential-safe Webshare plan and activity audit.

The account API key is an operator credential, not a crawler runtime secret.
This module talks only to fixed Webshare API paths and emits aggregate values;
proxy URLs, usernames, passwords, client IPs, exit IPs, and target hostnames are
never included in its report.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from ipaddress import ip_address
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx

_API_BASE_URL = "https://proxy.webshare.io"
_ACTIVITY_PATH = "/api/v2/proxy/activity/"
_MAX_PAGE_SIZE = 1_000
_MAX_PAGES = 20


class ProxyAuditError(RuntimeError):
    """The provider audit could not produce a trustworthy report."""


def _iso8601(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None


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


async def _get_object(
    client: httpx.AsyncClient,
    path: str,
    operation: str,
    *,
    params: Mapping[str, str | int] | None = None,
) -> dict[str, Any]:
    try:
        response = await client.get(path, params=params)
    except httpx.HTTPError as exc:
        raise ProxyAuditError(
            f"Webshare {operation} request failed ({type(exc).__name__})"
        ) from exc
    return _json_object(response, operation)


def _rows(payload: dict[str, Any], operation: str) -> list[dict[str, Any]]:
    rows = payload.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ProxyAuditError(f"Webshare {operation} omitted its results list")
    return rows


def _backbone_signature_from_url(url: str) -> tuple[str, str, int] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if (
        parsed.hostname != "p.webshare.io"
        or port is None
        or not parsed.username
        or parsed.password is None
    ):
        return None
    return unquote(parsed.username), unquote(parsed.password), port


def _backbone_signature_from_row(row: dict[str, Any]) -> tuple[str, str, int] | None:
    username = row.get("username")
    password = row.get("password")
    port = row.get("port")
    if not isinstance(username, str) or not isinstance(password, str) or not isinstance(port, int):
        return None
    return username, password, port


def _direct_signature_from_url(url: str) -> tuple[str, int, str, str] | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    if not parsed.hostname or port is None:
        return None
    return (
        parsed.hostname,
        port,
        unquote(parsed.username or ""),
        unquote(parsed.password or ""),
    )


def _direct_signature_from_row(row: dict[str, Any]) -> tuple[str, int, str, str] | None:
    address = row.get("proxy_address")
    port = row.get("port")
    username = row.get("username")
    password = row.get("password")
    if (
        not isinstance(address, str)
        or not isinstance(port, int)
        or not isinstance(username, str)
        or not isinstance(password, str)
    ):
        return None
    return address, port, username, password


def _next_activity_cursor(payload: dict[str, Any]) -> str | None:
    next_url = payload.get("next")
    if not isinstance(next_url, str) or not next_url:
        return None
    parsed = urlparse(next_url)
    if parsed.hostname not in {None, "proxy.webshare.io"} or parsed.path != _ACTIVITY_PATH:
        raise ProxyAuditError("Webshare activity pagination returned an unexpected next URL")
    values = parse_qs(parsed.query).get("starting_after", [])
    return values[0] if len(values) == 1 and values[0] else None


async def _activity_rows(
    client: httpx.AsyncClient,
    *,
    start: datetime,
    end: datetime,
    max_records: int,
) -> tuple[list[dict[str, Any]], int | None, bool]:
    records: list[dict[str, Any]] = []
    cursor: str | None = None
    total: int | None = None

    for _page_number in range(_MAX_PAGES):
        remaining = max_records - len(records)
        if remaining <= 0:
            break
        params: dict[str, str | int] = {
            "timestamp__gte": _iso8601(start),
            "timestamp__lte": _iso8601(end),
            "page_size": min(_MAX_PAGE_SIZE, remaining),
        }
        if cursor is not None:
            params["starting_after"] = cursor
        payload = await _get_object(
            client,
            _ACTIVITY_PATH,
            "activity",
            params=params,
        )
        page = _rows(payload, "activity")
        raw_total = payload.get("count")
        if isinstance(raw_total, int) and raw_total >= 0:
            total = raw_total
        records.extend(page[:remaining])
        if not page:
            break
        next_cursor = _next_activity_cursor(payload)
        if next_cursor is None:
            break
        if next_cursor == cursor:
            raise ProxyAuditError("Webshare activity pagination did not advance")
        cursor = next_cursor

    truncated = (total is not None and len(records) < total) or len(records) >= max_records
    return records, total, truncated


def _source_summary(
    records: Iterable[dict[str, Any]],
    *,
    expected_client_ips: frozenset[str],
    truncated: bool,
) -> dict[str, object]:
    known_records = 0
    unknown_records = 0
    missing_records = 0
    known_sources: set[str] = set()
    unknown_sources: set[str] = set()

    for record in records:
        raw = record.get("client_address")
        if not isinstance(raw, str):
            missing_records += 1
            continue
        try:
            normalized = str(ip_address(raw))
        except ValueError:
            missing_records += 1
            continue
        if normalized in expected_client_ips:
            known_records += 1
            known_sources.add(normalized)
        else:
            unknown_records += 1
            unknown_sources.add(normalized)

    if not expected_client_ips:
        assessment = "inconclusive_no_allowlist"
    elif unknown_records:
        # Truncation cannot invalidate positive evidence of an unknown source.
        # It only means there may be additional sources we did not inspect.
        assessment = "unexpected_sources"
    elif truncated:
        assessment = "inconclusive_truncated"
    elif missing_records:
        assessment = "inconclusive_missing_sources"
    else:
        assessment = "expected_only"

    return {
        "assessment": assessment,
        "expected_source_count": len(expected_client_ips),
        "known_source_count": len(known_sources),
        "unknown_source_count": len(unknown_sources),
        "known_records": known_records,
        "unknown_records": unknown_records,
        "missing_source_records": missing_records,
        "activity_truncated": truncated,
    }


async def audit_webshare(
    *,
    api_key: str,
    configured_pool_urls: Iterable[str],
    legacy_proxy_url: str,
    expected_client_ips: Iterable[str],
    since_hours: int = 24,
    max_activity_records: int = 10_000,
    now: datetime | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    """Return a sanitized Webshare plan, pool, usage, and source report."""

    if not api_key:
        raise ProxyAuditError("WEBSHARE_API_KEY is not configured")
    if since_hours < 1 or since_hours > 24 * 90:
        raise ProxyAuditError("since_hours must be between 1 and 2160")
    if max_activity_records < 1 or max_activity_records > _MAX_PAGE_SIZE * _MAX_PAGES:
        raise ProxyAuditError("max_activity_records must be between 1 and 20000")

    current = (now or datetime.now(UTC)).astimezone(UTC)
    requested_start = current - timedelta(hours=since_hours)
    headers = {
        "Authorization": f"Token {api_key}",
        "Accept": "application/json",
        "User-Agent": "jobseek-proxy-audit/1",
    }
    async with httpx.AsyncClient(
        base_url=_API_BASE_URL,
        headers=headers,
        timeout=httpx.Timeout(20.0),
        follow_redirects=False,
        transport=transport,
    ) as client:
        subscription = await _get_object(
            client,
            "/api/v2/subscription/",
            "subscription",
        )
        plan_id = subscription.get("plan")
        if not isinstance(plan_id, int):
            raise ProxyAuditError("Webshare subscription omitted its active plan")
        plan = await _get_object(
            client,
            f"/api/v2/subscription/plan/{plan_id}/",
            "plan",
        )

        subscription_start = _parse_datetime(subscription.get("start_date"))
        plan_usage_start = (
            max(requested_start, subscription_start) if subscription_start else requested_start
        )
        plan_time_params = {
            "timestamp__gte": _iso8601(plan_usage_start),
            "timestamp__lte": _iso8601(current),
        }
        window_time_params = {
            "timestamp__gte": _iso8601(requested_start),
            "timestamp__lte": _iso8601(current),
        }
        direct = await _get_object(
            client,
            "/api/v2/proxy/list/",
            "direct proxy list",
            params={"mode": "direct", "page": 1, "page_size": 100},
        )
        backbone = await _get_object(
            client,
            "/api/v2/proxy/list/",
            "backbone proxy list",
            params={"mode": "backbone", "page": 1, "page_size": 100},
        )
        plan_aggregate = await _get_object(
            client,
            "/api/v2/stats/aggregate/",
            "current-plan aggregate stats",
            params=plan_time_params,
        )
        window_aggregate = (
            plan_aggregate
            if plan_usage_start == requested_start
            else await _get_object(
                client,
                "/api/v2/stats/aggregate/",
                "requested-window aggregate stats",
                params=window_time_params,
            )
        )
        activity, activity_total, activity_truncated = await _activity_rows(
            client,
            # Activity lookup without an explicit plan id rejects timestamps
            # before the active plan. Keep current-plan source evidence valid
            # and mark the requested historical window as clipped below.
            start=plan_usage_start,
            end=current,
            max_records=max_activity_records,
        )

    direct_rows = _rows(direct, "direct proxy list")
    backbone_rows = _rows(backbone, "backbone proxy list")
    configured_pool = list(configured_pool_urls)
    configured_signatures = {
        signature
        for url in configured_pool
        if (signature := _backbone_signature_from_url(url)) is not None
    }
    api_backbone_signatures = {
        signature
        for row in backbone_rows
        if (signature := _backbone_signature_from_row(row)) is not None
    }
    legacy_signature = _direct_signature_from_url(legacy_proxy_url) if legacy_proxy_url else None
    api_direct_signatures = {
        signature
        for row in direct_rows
        if (signature := _direct_signature_from_row(row)) is not None
    }

    normalized_expected = frozenset(str(ip_address(value)) for value in expected_client_ips)
    activity_window_clipped = plan_usage_start > requested_start
    source = _source_summary(
        activity,
        expected_client_ips=normalized_expected,
        truncated=activity_truncated or activity_window_clipped,
    )
    source["window_clipped_to_current_plan"] = activity_window_clipped

    window_bandwidth_bytes = window_aggregate.get("bandwidth_total")
    if not isinstance(window_bandwidth_bytes, int) or window_bandwidth_bytes < 0:
        window_bandwidth_bytes = 0
    plan_bandwidth_bytes = plan_aggregate.get("bandwidth_total")
    if not isinstance(plan_bandwidth_bytes, int) or plan_bandwidth_bytes < 0:
        plan_bandwidth_bytes = 0
    bandwidth_limit_gb = plan.get("bandwidth_limit")
    usage_ratio = None
    if isinstance(bandwidth_limit_gb, (int, float)) and bandwidth_limit_gb > 0:
        usage_ratio = plan_bandwidth_bytes / (float(bandwidth_limit_gb) * 1_000_000_000)

    refresh_next = _parse_datetime(plan.get("automatic_refresh_next_at"))
    refresh_in_hours = None
    if refresh_next is not None:
        refresh_in_hours = max(0, math.floor((refresh_next - current).total_seconds() / 3600))

    problems: list[str] = []
    warnings: list[str] = []
    if plan.get("status") != "active" or subscription.get("paused") is True:
        problems.append("subscription_not_active")
    if subscription.get("throttled") is True:
        problems.append("subscription_throttled")
    if any(row.get("valid") is not True for row in direct_rows):
        problems.append("invalid_direct_proxy")
    if not configured_pool:
        problems.append("backbone_pool_not_configured")
    elif configured_signatures != api_backbone_signatures:
        problems.append("backbone_pool_mismatch")
    if source["assessment"] == "unexpected_sources":
        problems.append("unexpected_client_sources")
    elif str(source["assessment"]).startswith("inconclusive_"):
        warnings.append(str(source["assessment"]))
    if usage_ratio is not None and usage_ratio >= 0.8:
        problems.append("bandwidth_above_80_percent")
    if refresh_in_hours is not None and refresh_in_hours <= 7 * 24:
        warnings.append("automatic_refresh_within_7_days")
    if legacy_proxy_url:
        warnings.append("legacy_direct_proxy_configured")
    if activity_window_clipped:
        warnings.append("activity_window_clipped_to_current_plan")

    status = "alert" if problems else "inconclusive" if warnings else "ok"
    return {
        "status": status,
        "window": {
            "start": _iso8601(requested_start),
            "end": _iso8601(current),
            "requested_hours": since_hours,
            "spans_subscription_boundary": bool(
                subscription_start and requested_start < subscription_start
            ),
            "activity_start": _iso8601(plan_usage_start),
        },
        "subscription": {
            "term": subscription.get("term"),
            "start_date": subscription.get("start_date"),
            "end_date": subscription.get("end_date"),
            "renewals_enabled": subscription.get("renewals_enabled"),
            "paused": subscription.get("paused"),
            "throttled": subscription.get("throttled"),
        },
        "plan": {
            "status": plan.get("status"),
            "proxy_type": plan.get("proxy_type"),
            "proxy_subtype": plan.get("proxy_subtype"),
            "proxy_count": plan.get("proxy_count"),
            "bandwidth_limit_gb": bandwidth_limit_gb,
            "automatic_refresh_frequency_seconds": plan.get("automatic_refresh_frequency"),
            "automatic_refresh_last_at": plan.get("automatic_refresh_last_at"),
            "automatic_refresh_next_at": plan.get("automatic_refresh_next_at"),
            "automatic_refresh_in_hours": refresh_in_hours,
        },
        "pool": {
            "direct_count": direct.get("count"),
            "direct_valid_count": sum(row.get("valid") is True for row in direct_rows),
            "backbone_count": backbone.get("count"),
            "configured_backbone_count": len(configured_pool),
            "configured_backbone_match_count": len(configured_signatures & api_backbone_signatures),
            "legacy_direct_configured": bool(legacy_proxy_url),
            "legacy_direct_matches_current_list": (
                legacy_signature in api_direct_signatures if legacy_signature else None
            ),
        },
        "usage": {
            "window_bandwidth_bytes": window_bandwidth_bytes,
            "window_bandwidth_gib": round(window_bandwidth_bytes / (1024**3), 3),
            "window_requests_total": window_aggregate.get("requests_total"),
            "window_requests_successful": window_aggregate.get("requests_successful"),
            "window_requests_failed": window_aggregate.get("requests_failed"),
            "current_subscription_start": _iso8601(plan_usage_start),
            "current_subscription_bandwidth_bytes": plan_bandwidth_bytes,
            "current_subscription_bandwidth_gib": round(plan_bandwidth_bytes / (1024**3), 3),
            "current_subscription_bandwidth_limit_ratio": (
                round(usage_ratio, 6) if usage_ratio is not None else None
            ),
            "activity_total": activity_total,
            "activity_records_inspected": len(activity),
        },
        "client_sources": source,
        "problems": sorted(set(problems)),
        "warnings": sorted(set(warnings)),
    }
