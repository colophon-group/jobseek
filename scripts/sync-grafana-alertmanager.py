#!/usr/bin/env python3
"""Transactionally install Jobseek's Grafana-managed production paging path."""

from __future__ import annotations

import argparse
import copy
import os
import re
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
_UNOBSERVED = object()
BRIDGE_EXPRESSION = (
    'sum(ALERTS{alertstate="firing",page="production",deadman!="notification-route"}) or vector(0)'
)


class AlertmanagerSyncError(RuntimeError):
    """Grafana-managed paging could not be validated or synchronized."""


class GrafanaRequestError(AlertmanagerSyncError):
    """A bounded, sanitized Grafana API failure."""

    def __init__(self, method: str, path: str, status_code: int, detail: str = "") -> None:
        self.status_code = status_code
        self.detail = detail
        suffix = f": {detail}" if detail else ""
        super().__init__(f"Grafana {method} {path} returned HTTP {status_code}{suffix}")

    @property
    def is_optimistic_conflict(self) -> bool:
        if self.status_code != 409:
            return False
        normalized = self.detail.casefold()
        return any(
            marker in normalized
            for marker in (
                "optimistic",
                "version conflict",
                "concurrent update",
                "conflict while updating",
            )
        )


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
    queries = {str(item.get("refId")): item for item in data if isinstance(item, dict)}
    query = queries.get("A", {})
    condition = queries.get("B", {})
    query_model = query.get("model") or {}
    condition_model = condition.get("model") or {}
    return {
        "uid": rule.get("uid"),
        "title": rule.get("title"),
        "ruleGroup": rule.get("ruleGroup"),
        "folderUID": rule.get("folderUID"),
        "orgID": rule.get("orgID"),
        "condition": rule.get("condition"),
        "query": {
            "relativeTimeRange": query.get("relativeTimeRange"),
            "datasourceUid": query.get("datasourceUid"),
            "expr": query_model.get("expr"),
            "instant": query_model.get("instant"),
            "range": query_model.get("range"),
        },
        "condition_query": {
            "relativeTimeRange": condition.get("relativeTimeRange"),
            "datasourceUid": condition.get("datasourceUid"),
            "conditions": condition_model.get("conditions"),
            "expression": condition_model.get("expression"),
            "type": condition_model.get("type"),
        },
        "noDataState": rule.get("noDataState"),
        "execErrState": rule.get("execErrState"),
        "for": rule.get("for"),
        "annotations": rule.get("annotations"),
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


def _disable_provenance_for(resource: dict[str, Any] | None) -> bool:
    """Keep the resource's existing provenance mode on provisioning writes."""

    if resource is None:
        return True
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
    detail = " ".join(detail.split())
    return detail[:300]


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
            raise AlertmanagerSyncError(
                f"Grafana {method} {path.split('?', 1)[0]} failed: {type(exc).__name__}"
            ) from exc
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

    def put_contact(
        self, contact: dict[str, Any], *, exists: bool, disable_provenance: bool = True
    ) -> None:
        if exists:
            self.request(
                "PUT",
                f"/api/v1/provisioning/contact-points/{quote(EMAIL_CONTACT, safe='')}",
                json_body=contact,
                disable_provenance=disable_provenance,
            )
        else:
            self.request(
                "POST",
                "/api/v1/provisioning/contact-points",
                json_body=contact,
                disable_provenance=disable_provenance,
            )

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

    def folder(self) -> dict[str, Any] | None:
        status, payload = self.request(
            "GET", f"/api/folders/{quote(FOLDER_UID, safe='')}", allow_not_found=True
        )
        if status == 404:
            return None
        if not isinstance(payload, dict):
            raise AlertmanagerSyncError("Grafana folder response is not a mapping")
        return payload

    def folder_exists(self) -> bool:
        return self.folder() is not None

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

    def put_rule(
        self,
        rule: dict[str, Any],
        *,
        exists: bool,
        disable_provenance: bool = True,
    ) -> bool:
        """Write a rule, accepting raced convergence and one explicit lock retry."""

        uid = str(rule["uid"])
        item_path = f"/api/v1/provisioning/alert-rules/{quote(uid, safe='')}"
        for attempt in range(2):
            method = "PUT" if exists else "POST"
            path = item_path if exists else "/api/v1/provisioning/alert-rules"
            try:
                self.request(
                    method,
                    path,
                    json_body=rule,
                    disable_provenance=disable_provenance,
                )
                return True
            except GrafanaRequestError as exc:
                if exc.status_code != 409:
                    raise
                observed = self.rule(uid)
                if _rule_signature(observed) == _rule_signature(rule):
                    return False
                if attempt == 0 and exc.is_optimistic_conflict:
                    exists = observed is not None
                    disable_provenance = _disable_provenance_for(observed)
                    continue
                raise
        raise AssertionError("bounded Grafana rule retry exhausted")

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
    previous_folder = client.folder()
    observed_rules = {uid: client.rule(uid) for uid in OWNED_RULE_UIDS}
    previous_rules = {uid: _writable_rule(rule) for uid, rule in observed_rules.items()}
    desired_policy = merge_owned_policy(previous_policy)
    desired_rules = _desired_rules()
    mutations: list[tuple[str, str | None, Any]] = []

    try:
        if _contact_signature(previous_contact) != _contact_signature(desired_contact):
            client.put_contact(
                desired_contact,
                exists=previous_contact is not None,
                disable_provenance=_disable_provenance_for(previous_contact),
            )
            mutations.append(("contact", None, _UNOBSERVED))
            mutations[-1] = ("contact", None, client.contact())
        if _owned_policy_signature(previous_policy) != _owned_policy_signature(desired_policy):
            client.put_policy(
                desired_policy,
                disable_provenance=_disable_provenance_for(previous_policy),
            )
            mutations.append(("policy", None, _UNOBSERVED))
            mutations[-1] = ("policy", None, client.policy())
        if previous_folder is None:
            client.create_folder()
            mutations.append(("folder", None, _UNOBSERVED))
            mutations[-1] = ("folder", None, client.folder())
        for uid, rule in desired_rules.items():
            observed = observed_rules[uid]
            if _rule_signature(observed) == _rule_signature(rule):
                continue
            changed = client.put_rule(
                rule,
                exists=observed is not None,
                disable_provenance=_disable_provenance_for(observed),
            )
            if changed:
                mutations.append(("rule", uid, _UNOBSERVED))
                mutations[-1] = ("rule", uid, client.rule(uid))
        for _ in range(12):
            if _verified(client, desired_contact, desired_policy):
                return
            time.sleep(5)
        raise AlertmanagerSyncError("Grafana did not expose the expected paging resources")
    except Exception as sync_error:
        rollback_failed = False
        rollback_conflicted = False
        for kind, uid, expected in reversed(mutations):
            try:
                if kind == "rule":
                    assert uid is not None
                    current_rule = client.rule(uid)
                    if current_rule != expected:
                        rollback_conflicted = True
                        continue
                    previous_rule = previous_rules[uid]
                    if _rule_signature(current_rule) == _rule_signature(previous_rule):
                        continue
                    if previous_rule is None:
                        client.delete_rule(uid)
                    else:
                        client.put_rule(
                            previous_rule,
                            exists=current_rule is not None,
                            disable_provenance=_disable_provenance_for(current_rule),
                        )
                elif kind == "folder":
                    current_folder = client.folder()
                    if current_folder != expected:
                        rollback_conflicted = True
                        continue
                    if current_folder is not None:
                        client.delete_folder()
                elif kind == "policy":
                    current_policy = client.policy()
                    if current_policy != expected:
                        rollback_conflicted = True
                        continue
                    if current_policy != previous_policy:
                        client.put_policy(
                            previous_policy,
                            disable_provenance=_disable_provenance_for(current_policy),
                        )
                elif kind == "contact":
                    current_contact = client.contact()
                    if current_contact != expected:
                        rollback_conflicted = True
                        continue
                    if _contact_signature(current_contact) == _contact_signature(previous_contact):
                        continue
                    if previous_contact is None:
                        client.delete_contact()
                    else:
                        client.put_contact(
                            previous_contact,
                            exists=current_contact is not None,
                            disable_provenance=_disable_provenance_for(current_contact),
                        )
                else:
                    raise AssertionError(f"unknown rollback resource: {kind}")
            except Exception:
                rollback_failed = True
        if rollback_failed:
            raise AlertmanagerSyncError(
                f"paging sync failed ({type(sync_error).__name__}) and rollback also failed"
            ) from sync_error
        if rollback_conflicted:
            raise AlertmanagerSyncError(
                f"paging sync failed ({type(sync_error).__name__}) and rollback preserved "
                "resources whose post-write state could not be proven"
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
