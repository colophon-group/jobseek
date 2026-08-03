#!/usr/bin/env python3
"""Transactionally install Jobseek's Grafana-managed production paging path."""

from __future__ import annotations

import argparse
import copy
import os
import time
from email.utils import parseaddr
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

EMAIL_CONTACT = "jobseek-production-email"
FOLDER_UID = "jobseek-observability"
FOLDER_TITLE = "Jobseek Observability"
RULE_GROUP = "jobseek-production-paging"
BRIDGE_RULE_UID = "jobseek-production-page-bridge"
DEADMAN_RULE_UID = "jobseek-paging-route-deadman"
OWNED_RULE_UIDS = (BRIDGE_RULE_UID, DEADMAN_RULE_UID)
PROMETHEUS_UID = "grafanacloud-prom"
BRIDGE_EXPRESSION = (
    'sum(ALERTS{alertstate="firing",page="production",deadman!="notification-route"}) or vector(0)'
)


class AlertmanagerSyncError(RuntimeError):
    """Grafana-managed paging could not be validated or synchronized."""


def _validate_email(value: str) -> str:
    email = value.strip()
    parsed = parseaddr(email)[1]
    if not email or parsed != email or "\n" in email or "\r" in email or "@" not in email:
        raise AlertmanagerSyncError("a single valid alert email address is required")
    return email


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


def _owned_routes() -> list[dict[str, Any]]:
    return [
        {
            "receiver": EMAIL_CONTACT,
            "object_matchers": [["deadman", "=", "notification-route"]],
            "group_by": ["alertname"],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "24h",
            "continue": True,
        },
        {
            "receiver": EMAIL_CONTACT,
            "object_matchers": [
                ["page", "=", "production"],
                ["deadman", "!=", "notification-route"],
            ],
            "group_by": ["alertname", "service", "host_role", "target"],
            "group_wait": "30s",
            "group_interval": "5m",
            "repeat_interval": "30m",
            "continue": True,
        },
    ]


def merge_owned_policy(previous: dict[str, Any]) -> dict[str, Any]:
    desired = copy.deepcopy(previous)
    routes = desired.setdefault("routes", [])
    if not isinstance(routes, list) or not all(isinstance(item, dict) for item in routes):
        raise AlertmanagerSyncError("Grafana notification policy routes must be mappings")
    desired["routes"] = _owned_routes() + [
        item for item in routes if item.get("receiver") != EMAIL_CONTACT
    ]
    return desired


def _contact_point(email: str) -> dict[str, Any]:
    return {
        "uid": EMAIL_CONTACT,
        "name": EMAIL_CONTACT,
        "type": "email",
        "settings": {
            "addresses": _validate_email(email),
            "singleEmail": True,
            "subject": (
                "[Jobseek production] {{ .Status | toUpper }} {{ .CommonLabels.alertname }}"
            ),
        },
        "disableResolveMessage": False,
    }


def _query_rule(
    *, uid: str, title: str, expression: str, labels: dict[str, str], summary: str
) -> dict[str, Any]:
    return {
        "uid": uid,
        "title": title,
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
        "execErrState": "Alerting",
        "for": "0s",
        "annotations": {
            "summary": summary,
            "description": (
                "This Grafana-managed rule is the independent handoff for Jobseek "
                "production alerts. Inspect the source Mimir alerts and service runbook."
            ),
            "runbook": (
                "https://github.com/colophon-group/jobseek/blob/main/"
                "docs/16-hetzner-maintenance.md#independent-production-paging"
            ),
        },
        "labels": labels,
        "isPaused": False,
    }


def _desired_rules() -> dict[str, dict[str, Any]]:
    common = {
        "severity": "critical",
        "service": "production-alerting",
        "owner": "codex-error-review",
        "route": "codex-daily",
        "page": "production",
    }
    return {
        BRIDGE_RULE_UID: _query_rule(
            uid=BRIDGE_RULE_UID,
            title="ProductionCriticalAlertBridge",
            expression=BRIDGE_EXPRESSION,
            labels=common,
            summary="One or more Jobseek production alerts require an independent page",
        ),
        DEADMAN_RULE_UID: _query_rule(
            uid=DEADMAN_RULE_UID,
            title="PagingRouteDeadman",
            expression="vector(1)",
            labels={**common, "deadman": "notification-route", "synthetic": "true"},
            summary="Jobseek production paging route is alive",
        ),
    }


def _duration(value: Any) -> str:
    return "24h" if value == "1d" else str(value)


def _route_signature(route: dict[str, Any]) -> dict[str, Any]:
    matchers = route.get("object_matchers") or []
    return {
        "receiver": route.get("receiver"),
        "object_matchers": sorted(tuple(str(part) for part in item) for item in matchers),
        "group_by": sorted(route.get("group_by") or []),
        "group_wait": _duration(route.get("group_wait")),
        "group_interval": _duration(route.get("group_interval")),
        "repeat_interval": _duration(route.get("repeat_interval")),
        "continue": route.get("continue"),
    }


def _owned_policy_signature(policy: dict[str, Any]) -> list[dict[str, Any]]:
    routes = policy.get("routes") or []
    return sorted(
        (_route_signature(item) for item in routes if item.get("receiver") == EMAIL_CONTACT),
        key=lambda item: repr(item["object_matchers"]),
    )


def _contact_signature(contact: dict[str, Any] | None) -> dict[str, Any] | None:
    if contact is None:
        return None
    settings = contact.get("settings") or {}
    return {
        "uid": contact.get("uid"),
        "name": contact.get("name"),
        "type": contact.get("type"),
        "disableResolveMessage": contact.get("disableResolveMessage", False),
        "addresses": settings.get("addresses"),
        "singleEmail": settings.get("singleEmail"),
        "subject": settings.get("subject"),
    }


def _rule_signature(rule: dict[str, Any] | None) -> dict[str, Any] | None:
    if rule is None:
        return None
    data = rule.get("data") or []
    query = next((item for item in data if item.get("refId") == "A"), {})
    return {
        "uid": rule.get("uid"),
        "title": rule.get("title"),
        "ruleGroup": rule.get("ruleGroup"),
        "folderUID": rule.get("folderUID"),
        "condition": rule.get("condition"),
        "expression": (query.get("model") or {}).get("expr"),
        "noDataState": rule.get("noDataState"),
        "execErrState": rule.get("execErrState"),
        "for": rule.get("for"),
        "labels": rule.get("labels"),
        "isPaused": rule.get("isPaused", False),
    }


def _writable_rule(rule: dict[str, Any] | None) -> dict[str, Any] | None:
    if rule is None:
        return None
    writable = {
        key: copy.deepcopy(rule[key])
        for key in (
            "uid",
            "title",
            "ruleGroup",
            "folderUID",
            "orgID",
            "condition",
            "data",
            "noDataState",
            "execErrState",
            "for",
            "annotations",
            "labels",
            "isPaused",
            "notification_settings",
            "record",
            "keep_firing_for",
        )
        if key in rule
    }
    return writable


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
    ) -> tuple[int, Any]:
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
            raise AlertmanagerSyncError(
                f"Grafana {method} {path.split('?', 1)[0]} failed: {type(exc).__name__}"
            ) from exc
        if allow_not_found and response.status_code == 404:
            return response.status_code, None
        if response.is_error:
            raise AlertmanagerSyncError(
                f"Grafana {method} {path.split('?', 1)[0]} returned HTTP {response.status_code}"
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

    def put_contact(self, contact: dict[str, Any], *, exists: bool) -> None:
        if exists:
            self.request(
                "PUT",
                f"/api/v1/provisioning/contact-points/{quote(EMAIL_CONTACT, safe='')}",
                json_body=contact,
            )
        else:
            self.request("POST", "/api/v1/provisioning/contact-points", json_body=contact)

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

    def put_policy(self, policy: dict[str, Any]) -> None:
        self.request("PUT", "/api/v1/provisioning/policies", json_body=policy)

    def folder_exists(self) -> bool:
        status, _ = self.request(
            "GET", f"/api/folders/{quote(FOLDER_UID, safe='')}", allow_not_found=True
        )
        return status != 404

    def create_folder(self) -> None:
        self.request("POST", "/api/folders", json_body={"uid": FOLDER_UID, "title": FOLDER_TITLE})

    def delete_folder(self) -> None:
        self.request("DELETE", f"/api/folders/{quote(FOLDER_UID, safe='')}", allow_not_found=True)

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

    def put_rule(self, rule: dict[str, Any], *, exists: bool) -> None:
        uid = str(rule["uid"])
        if exists:
            self.request(
                "PUT",
                f"/api/v1/provisioning/alert-rules/{quote(uid, safe='')}",
                json_body=rule,
            )
        else:
            self.request("POST", "/api/v1/provisioning/alert-rules", json_body=rule)

    def delete_rule(self, uid: str) -> None:
        self.request(
            "DELETE",
            f"/api/v1/provisioning/alert-rules/{quote(uid, safe='')}",
            allow_not_found=True,
        )


def _verified(client: GrafanaClient, contact: dict[str, Any], policy: dict[str, Any]) -> bool:
    if _contact_signature(client.contact()) != _contact_signature(contact):
        return False
    if _owned_policy_signature(client.policy()) != _owned_policy_signature(policy):
        return False
    return all(
        _rule_signature(client.rule(uid)) == _rule_signature(rule)
        for uid, rule in _desired_rules().items()
    )


def sync_config(client: GrafanaClient, email: str) -> None:
    desired_contact = _contact_point(email)
    previous_contact = client.contact()
    previous_policy = client.policy()
    previous_folder = client.folder_exists()
    previous_rules = {uid: _writable_rule(client.rule(uid)) for uid in OWNED_RULE_UIDS}
    desired_policy = merge_owned_policy(previous_policy)
    desired_rules = _desired_rules()

    try:
        client.put_contact(desired_contact, exists=previous_contact is not None)
        client.put_policy(desired_policy)
        if not previous_folder:
            client.create_folder()
        for uid, rule in desired_rules.items():
            client.put_rule(rule, exists=previous_rules[uid] is not None)
        for _ in range(12):
            if _verified(client, desired_contact, desired_policy):
                return
            time.sleep(5)
        raise AlertmanagerSyncError("Grafana did not expose the expected paging resources")
    except Exception as sync_error:
        rollback_failed = False
        for uid, previous in previous_rules.items():
            try:
                if previous is None:
                    client.delete_rule(uid)
                else:
                    client.put_rule(previous, exists=True)
            except Exception:
                rollback_failed = True
        try:
            client.put_policy(previous_policy)
        except Exception:
            rollback_failed = True
        try:
            if previous_contact is None:
                client.delete_contact()
            else:
                client.put_contact(previous_contact, exists=True)
        except Exception:
            rollback_failed = True
        if not previous_folder:
            try:
                client.delete_folder()
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise AlertmanagerSyncError(
                f"paging sync failed ({type(sync_error).__name__}) and rollback also failed"
            ) from sync_error
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_URL"))
    parser.add_argument("--api-key", default=os.environ.get("GRAFANA_API_KEY"))
    parser.add_argument("--email", default=os.environ.get("GRAFANA_ALERT_EMAIL"))
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    email = _validate_email(args.email or "")
    desired_policy = merge_owned_policy({"receiver": "empty", "routes": []})
    assert len(_owned_policy_signature(desired_policy)) == 2
    assert len(_desired_rules()) == 2
    if args.dry_run:
        if args.url:
            _validate_base_url(args.url)
        if args.api_key:
            _validate_api_key(args.api_key)
        print(
            "validated a Grafana-managed email contact, critical bridge, daily deadman, "
            "30-second group wait, 30-minute repeat, and 24-hour deadman repeat"
        )
        return 0
    if not args.url or not args.api_key:
        raise SystemExit("Grafana URL and API key are required")
    sync_config(GrafanaClient(args.url, args.api_key), email)
    print("synchronized and verified independent Grafana-managed production paging")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
