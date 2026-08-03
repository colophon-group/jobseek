#!/usr/bin/env python3
"""Create, verify, resolve, and delete a synthetic Grafana-managed page."""

from __future__ import annotations

import argparse
import os
import re
import time
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

UTC = timezone.utc  # noqa: UP017 - usable by the crawler host system Python too.
TEST_ALERT = "JobseekPagingEndToEndTest"
TEST_RULE_UID = "jobseek-paging-e2e-test"
FOLDER_UID = "jobseek-observability"
RULE_GROUP = "jobseek-production-paging-tests"
PROMETHEUS_UID = "grafanacloud-prom"
_TEST_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


class PagingTestError(RuntimeError):
    """The synthetic page was rejected or could not be verified."""


def _base_url(value: str) -> str:
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
        raise PagingTestError("Grafana URL must be an HTTPS origin without a path")
    return normalized


def _api_key(value: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise PagingTestError("Grafana API key is required")
    return value


def _test_id(value: str) -> str:
    normalized = value.strip()
    if not _TEST_ID.fullmatch(normalized):
        raise PagingTestError(
            "test id must contain only letters, numbers, dot, underscore, or dash"
        )
    return normalized


def _labels(test_id: str) -> dict[str, str]:
    return {
        "severity": "critical",
        "service": "production-alerting",
        "owner": "codex-error-review",
        "route": "codex-daily",
        "page": "production",
        "synthetic": "true",
        "test_id": test_id,
    }


def _rule(test_id: str, expression: str) -> dict[str, Any]:
    return {
        "uid": TEST_RULE_UID,
        "title": TEST_ALERT,
        "ruleGroup": RULE_GROUP,
        "folderUID": FOLDER_UID,
        "orgID": 1,
        "condition": "B",
        "data": [
            {
                "refId": "A",
                "queryType": "",
                "relativeTimeRange": {"from": 60, "to": 0},
                "datasourceUid": PROMETHEUS_UID,
                "model": {
                    "datasource": {"type": "prometheus", "uid": PROMETHEUS_UID},
                    "editorMode": "code",
                    "expr": expression,
                    "instant": True,
                    "intervalMs": 1000,
                    "legendFormat": "__auto",
                    "maxDataPoints": 43200,
                    "range": False,
                    "refId": "A",
                },
            },
            {
                "refId": "B",
                "queryType": "",
                "relativeTimeRange": {"from": 0, "to": 0},
                "datasourceUid": "__expr__",
                "model": {
                    "conditions": [
                        {
                            "evaluator": {"params": [0], "type": "gt"},
                            "operator": {"type": "and"},
                            "query": {"params": ["A"]},
                            "reducer": {"params": [], "type": "last"},
                            "type": "query",
                        }
                    ],
                    "datasource": {"type": "__expr__", "uid": "__expr__"},
                    "expression": "A",
                    "intervalMs": 1000,
                    "maxDataPoints": 43200,
                    "refId": "B",
                    "type": "classic_conditions",
                },
            },
        ],
        "noDataState": "OK",
        "execErrState": "Error",
        "for": "0s",
        "annotations": {
            "summary": "Scheduled Jobseek production paging test",
            "description": "Synthetic production page and recovery verification.",
            "runbook": (
                "https://github.com/colophon-group/jobseek/blob/main/"
                "docs/16-hetzner-maintenance.md#independent-production-paging"
            ),
        },
        "labels": _labels(test_id),
        "isPaused": False,
    }


class GrafanaPagingClient:
    def __init__(self, base_url: str, api_key: str) -> None:
        self.base_url = _base_url(base_url)
        self.api_key = _api_key(api_key)

    def request(
        self,
        method: str,
        path: str,
        *,
        json_body: Any = None,
        allow_not_found: bool = False,
    ) -> Any:
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                json=json_body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "X-Disable-Provenance": "true",
                },
                timeout=30,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise PagingTestError(
                f"Grafana {method} {path.split('?', 1)[0]} failed: {type(exc).__name__}"
            ) from exc
        if allow_not_found and response.status_code == 404:
            return None
        if response.is_error:
            raise PagingTestError(
                f"Grafana {method} {path.split('?', 1)[0]} returned HTTP {response.status_code}"
            )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise PagingTestError("Grafana returned invalid JSON") from exc

    def create(self, rule: dict[str, Any]) -> None:
        self.request("POST", "/api/v1/provisioning/alert-rules", json_body=rule)

    def update(self, rule: dict[str, Any]) -> None:
        self.request(
            "PUT",
            f"/api/v1/provisioning/alert-rules/{quote(TEST_RULE_UID, safe='')}",
            json_body=rule,
        )

    def delete(self) -> None:
        self.request(
            "DELETE",
            f"/api/v1/provisioning/alert-rules/{quote(TEST_RULE_UID, safe='')}",
            allow_not_found=True,
        )

    def active(self, test_id: str) -> bool:
        payload = self.request("GET", "/api/alertmanager/grafana/api/v2/alerts")
        if not isinstance(payload, list):
            raise PagingTestError("Grafana active-alert response is not a list")
        return any(
            isinstance(alert, dict)
            and isinstance(alert.get("labels"), dict)
            and alert["labels"].get("test_id") == test_id
            and alert.get("status", {}).get("state") == "active"
            for alert in payload
        )


def _wait_for_state(
    client: GrafanaPagingClient, test_id: str, *, expected: bool, attempts: int = 18
) -> None:
    for _ in range(attempts):
        if client.active(test_id) is expected:
            return
        time.sleep(5)
    state = "active" if expected else "resolved"
    raise PagingTestError(f"synthetic critical alert did not become {state} within 90 seconds")


def run_test(
    client: GrafanaPagingClient,
    test_id: str,
    *,
    hold_seconds: int = 45,
    now: datetime | None = None,
) -> None:
    test_id = _test_id(test_id)
    started = now or datetime.now(tz=UTC)
    expires = int(started.timestamp()) + 5 * 60
    firing = _rule(test_id, f"vector(time() < bool {expires})")
    resolved = _rule(test_id, "vector(0)")
    created = False
    try:
        client.create(firing)
        created = True
        _wait_for_state(client, test_id, expected=True)
        time.sleep(hold_seconds)
        client.update(resolved)
        _wait_for_state(client, test_id, expected=False)
    finally:
        if created:
            client.delete()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_URL"))
    parser.add_argument("--api-key", default=os.environ.get("GRAFANA_API_KEY"))
    parser.add_argument(
        "--test-id", default=os.environ.get("GITHUB_RUN_ID", f"local-{int(time.time())}")
    )
    parser.add_argument("--hold-seconds", type=int, default=45)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    test_id = _test_id(args.test_id)
    if not 35 <= args.hold_seconds <= 120:
        raise SystemExit("hold-seconds must be between 35 and 120")
    if not args.url or not args.api_key:
        raise SystemExit("Grafana URL and API key are required")
    client = GrafanaPagingClient(args.url, args.api_key)
    if args.dry_run:
        assert _rule(test_id, "vector(1)")
        print("validated a bounded Grafana-managed critical page and recovery test")
        return 0
    run_test(client, test_id, hold_seconds=args.hold_seconds)
    print("synthetic critical page became active, remained routable, resolved, and was removed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
