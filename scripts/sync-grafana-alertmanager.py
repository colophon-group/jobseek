#!/usr/bin/env python3
"""Permanently remove Jobseek's Grafana-managed production paging path."""

from __future__ import annotations

import argparse
import copy
import os
import re
import time
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

EMAIL_CONTACT = "jobseek-production-email"
BRIDGE_RULE_UID = "jobseek-production-page-bridge"
DEADMAN_RULE_UID = "jobseek-paging-route-deadman"
SYNTHETIC_TEST_RULE_UID = "jobseek-paging-e2e-test"
PAGING_RULE_UIDS = (BRIDGE_RULE_UID, DEADMAN_RULE_UID, SYNTHETIC_TEST_RULE_UID)
GRAFANA_READ_ATTEMPTS = 9
GRAFANA_READ_RETRY_MAX_SECONDS = 30
GRAFANA_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})


class AlertmanagerSyncError(RuntimeError):
    """Grafana-managed paging removal could not be validated."""


class GrafanaRequestError(AlertmanagerSyncError):
    """A bounded, sanitized Grafana API failure."""

    def __init__(self, method: str, path: str, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"Grafana {method} {path} returned HTTP {status_code}{suffix}")


def _validate_base_url(value: str) -> str:
    normalized = value.strip().rstrip("/")
    parsed = urlsplit(normalized)
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.path
        or parsed.query
        or parsed.fragment
        or parsed.username
        or parsed.password
    ):
        raise AlertmanagerSyncError("Grafana URL must be an HTTPS origin without a path")
    return normalized


def _validate_api_key(value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise AlertmanagerSyncError("Grafana API key is required")
    return value


def remove_owned_policy(previous: dict[str, Any]) -> dict[str, Any]:
    """Remove only routes that target Jobseek's retired paging contact."""

    desired = copy.deepcopy(previous)
    routes = desired.setdefault("routes", [])
    if not isinstance(routes, list) or not all(isinstance(item, dict) for item in routes):
        raise AlertmanagerSyncError("Grafana notification policy routes must be mappings")
    desired["routes"] = [item for item in routes if item.get("receiver") != EMAIL_CONTACT]
    return desired


def _has_owned_routes(policy: dict[str, Any]) -> bool:
    routes = policy.get("routes") or []
    if not isinstance(routes, list) or not all(isinstance(item, dict) for item in routes):
        raise AlertmanagerSyncError("Grafana notification policy routes must be mappings")
    return any(item.get("receiver") == EMAIL_CONTACT for item in routes)


def _disable_provenance_for(resource: dict[str, Any]) -> bool:
    """Keep an existing resource's provenance mode on the removal write."""

    return not bool(resource.get("provenance"))


def _safe_error_detail(response: httpx.Response, *, secret: str) -> str:
    """Extract a small diagnostic without echoing credentials or addresses."""

    candidates: list[str] = []
    try:
        payload = response.json()
    except ValueError:
        payload = None
    if isinstance(payload, dict):
        for key in ("message", "error", "errorCode", "code"):
            value = payload.get(key)
            if isinstance(value, str):
                candidates.append(value)
    if not candidates and response.text:
        candidates.append(response.text)
    detail = " | ".join(candidates)
    if secret:
        detail = detail.replace(secret, "[redacted]")
    detail = re.sub(
        r"(?i)(?<![\w.+-])[\w.+-]+@[\w.-]+\.[a-z]{2,}(?![\w.-])",
        "[redacted-email]",
        detail,
    )
    return " ".join(detail.split())[:300]


class GrafanaClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = _validate_base_url(base_url)
        self.api_key = _validate_api_key(api_key)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        allow_not_found: bool = False,
        disable_provenance: bool = False,
    ) -> tuple[int, Any]:
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if disable_provenance:
            headers["X-Disable-Provenance"] = "true"
        method = method.upper()
        max_attempts = GRAFANA_READ_ATTEMPTS if method == "GET" else 1
        response: httpx.Response | None = None
        for attempt in range(max_attempts):
            try:
                response = httpx.request(
                    method,
                    f"{self.base_url}{path}",
                    json=json_body,
                    headers=headers,
                    timeout=30,
                    follow_redirects=False,
                )
            except httpx.HTTPError as exc:
                if attempt + 1 < max_attempts:
                    time.sleep(min(2**attempt, GRAFANA_READ_RETRY_MAX_SECONDS))
                    continue
                raise AlertmanagerSyncError(
                    f"Grafana {method} {path.split('?', 1)[0]} failed: {type(exc).__name__}"
                ) from exc
            if response.status_code in GRAFANA_RETRYABLE_STATUSES and attempt + 1 < max_attempts:
                time.sleep(min(2**attempt, GRAFANA_READ_RETRY_MAX_SECONDS))
                continue
            break
        assert response is not None
        if allow_not_found and response.status_code == 404:
            return response.status_code, None
        if response.is_error:
            safe_path = path.split("?", 1)[0]
            raise GrafanaRequestError(
                method,
                safe_path,
                response.status_code,
                _safe_error_detail(response, secret=self.api_key),
            )
        if not response.content:
            return response.status_code, None
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise AlertmanagerSyncError("Grafana returned invalid JSON") from exc

    def contacts(self) -> list[dict[str, Any]]:
        _, payload = self.request("GET", "/api/v1/provisioning/contact-points")
        if not isinstance(payload, list):
            raise AlertmanagerSyncError("Grafana contact-point response is not a list")
        return payload

    def contact(self) -> dict[str, Any] | None:
        return next((item for item in self.contacts() if item.get("uid") == EMAIL_CONTACT), None)

    def delete_contact(self) -> None:
        self.request(
            "DELETE",
            f"/api/v1/provisioning/contact-points/{quote(EMAIL_CONTACT, safe='')}",
            allow_not_found=True,
        )

    def policy(self) -> dict[str, Any]:
        _, payload = self.request("GET", "/api/v1/provisioning/policies")
        if not isinstance(payload, dict):
            raise AlertmanagerSyncError("Grafana notification policy is not a mapping")
        return payload

    def put_policy(self, policy: dict[str, Any], *, disable_provenance: bool = True) -> None:
        self.request(
            "PUT",
            "/api/v1/provisioning/policies",
            json_body=policy,
            disable_provenance=disable_provenance,
        )

    def rule(self, uid: str) -> dict[str, Any] | None:
        status, payload = self.request(
            "GET",
            f"/api/v1/provisioning/alert-rules/{quote(uid, safe='')}",
            allow_not_found=True,
        )
        if status == 404:
            return None
        if not isinstance(payload, dict):
            raise AlertmanagerSyncError("Grafana alert-rule response is not a mapping")
        return payload

    def delete_rule(self, uid: str) -> None:
        self.request(
            "DELETE",
            f"/api/v1/provisioning/alert-rules/{quote(uid, safe='')}",
            allow_not_found=True,
        )


def disable_config(client: GrafanaClient) -> None:
    """Remove every Jobseek-owned page source and route without restoring it.

    Policy removal happens first so even a later cleanup failure leaves alerts
    unrouted. Re-reading before each bounded write preserves unrelated routes
    that may have changed concurrently.
    """

    for _ in range(3):
        observed_policy = client.policy()
        if not _has_owned_routes(observed_policy):
            break
        client.put_policy(
            remove_owned_policy(observed_policy),
            disable_provenance=_disable_provenance_for(observed_policy),
        )
    if _has_owned_routes(client.policy()):
        raise AlertmanagerSyncError("Jobseek production paging routes remain configured")

    cleanup_errors: list[str] = []
    for uid in PAGING_RULE_UIDS:
        try:
            if client.rule(uid) is not None:
                client.delete_rule(uid)
        except Exception as exc:  # noqa: BLE001 - continue removing independent page sources.
            cleanup_errors.append(f"rule/{uid}:{type(exc).__name__}")
    try:
        if client.contact() is not None:
            client.delete_contact()
    except Exception as exc:  # noqa: BLE001 - report only bounded resource/type details.
        cleanup_errors.append(f"contact:{type(exc).__name__}")

    remaining_rules = {uid for uid in PAGING_RULE_UIDS if client.rule(uid) is not None}
    contact_remains = client.contact() is not None
    if contact_remains or remaining_rules or cleanup_errors:
        details = ", ".join(
            [*cleanup_errors, *(f"rule/{uid}:present" for uid in sorted(remaining_rules))]
        )
        if contact_remains:
            details = ", ".join(filter(None, (details, "contact:present")))
        raise AlertmanagerSyncError(
            f"production paging is unrouted but cleanup is incomplete: {details}"
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_URL"))
    parser.add_argument("--api-key", default=os.environ.get("GRAFANA_API_KEY"))
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--disable", action="store_true")
    mode.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    if args.dry_run:
        if args.url:
            _validate_base_url(args.url)
        if args.api_key:
            _validate_api_key(args.api_key)
        sample = {"receiver": "empty", "routes": [{"receiver": EMAIL_CONTACT}]}
        assert not _has_owned_routes(remove_owned_policy(sample))
        assert len(PAGING_RULE_UIDS) == 3
        print("validated permanent removal of all Jobseek-owned production paging resources")
        return 0
    if not args.disable:
        raise SystemExit("production paging activation has been removed; use --disable")
    if not args.url or not args.api_key:
        raise SystemExit("Grafana URL and API key are required")
    disable_config(GrafanaClient(args.url, args.api_key))
    print("disabled and verified all Jobseek-owned production paging resources")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
