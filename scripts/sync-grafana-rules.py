#!/usr/bin/env python3
"""Transactionally sync Jobseek's owned Grafana Cloud Mimir rule namespace."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
import urllib.parse
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml

DEFAULT_NAMESPACE = "jobseek_crawler_reliability"
DEFAULT_RULE_FILE = Path(__file__).resolve().parents[1] / "apps" / "crawler" / "alerts.yaml"
MAX_RULES_PER_GROUP = 20
DEFAULT_EVALUATION_WAIT_SECONDS = 360
RULE_EVALUATION_POLL_SECONDS = 5
_DURATION_PART = re.compile(r"(\d+)(ms|y|w|d|h|m|s)")
_DURATION_MS = {
    "ms": 1,
    "s": 1_000,
    "m": 60_000,
    "h": 3_600_000,
    "d": 86_400_000,
    "w": 604_800_000,
    "y": 31_536_000_000,
}


class RuleSyncError(RuntimeError):
    """The source rules or remote ruler state failed validation."""


def _ruler_base(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith("/push"):
        normalized = normalized[: -len("/push")]
    if not normalized.endswith("/api/prom"):
        raise RuleSyncError("Grafana URL must end in /api/prom or /api/prom/push")
    return normalized


def _load_groups(path: Path) -> list[dict[str, Any]]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuleSyncError(f"cannot parse {path}") from exc
    groups = document.get("groups") if isinstance(document, dict) else None
    if not isinstance(groups, list) or not groups:
        raise RuleSyncError("rule file must contain at least one rule group")
    group_names: list[str] = []
    names: list[str] = []
    for group in groups:
        if not isinstance(group, dict):
            raise RuleSyncError("every rule group must be a mapping")
        group_name = group.get("name")
        if not isinstance(group_name, str) or not group_name:
            raise RuleSyncError("rule group name is required")
        group_names.append(group_name)
        group_rules = group.get("rules")
        if not isinstance(group_rules, list) or not group_rules:
            raise RuleSyncError(f"rule group {group_name} must contain at least one rule")
        if len(group_rules) > MAX_RULES_PER_GROUP:
            raise RuleSyncError(
                f"rule group {group_name} exceeds the Grafana Cloud limit "
                f"of {MAX_RULES_PER_GROUP} rules (actual: {len(group_rules)})"
            )
        for rule in group_rules:
            if not isinstance(rule, dict) or not isinstance(rule.get("expr"), str):
                raise RuleSyncError("every rule must be a mapping with a string expression")
            name = rule.get("alert") or rule.get("record")
            if not isinstance(name, str) or not name:
                raise RuleSyncError("every rule must define alert or record")
            names.append(name)
            if rule.get("alert"):
                labels = rule.get("labels")
                annotations = rule.get("annotations")
                if not isinstance(labels, dict) or labels.get("owner") != "codex-error-review":
                    raise RuleSyncError(f"alert {name} must set owner=codex-error-review")
                if labels.get("route") != "codex-daily":
                    raise RuleSyncError(f"alert {name} must set route=codex-daily")
                if labels.get("severity") == "critical":
                    if labels.get("page") != "production":
                        raise RuleSyncError(f"critical alert {name} must set page=production")
                    pending = _duration_signature(rule.get("for"))
                    if not isinstance(pending, int):
                        raise RuleSyncError(
                            f"critical alert {name} must have a valid pending duration"
                        )
                    if pending > 3 * 60 * 1_000:
                        raise RuleSyncError(
                            f"critical alert {name} pending duration exceeds three minutes"
                        )
                runbook = (
                    str(annotations.get("runbook", "")) if isinstance(annotations, dict) else ""
                )
                if not runbook.startswith("https://github.com/colophon-group/jobseek/"):
                    raise RuleSyncError(f"alert {name} must have a repository runbook URL")
    if len(group_names) != len(set(group_names)):
        raise RuleSyncError("rule group names must be unique")
    if len(names) != len(set(names)):
        raise RuleSyncError("rule names must be unique")
    return groups


class MimirClient:
    def __init__(self, base_url: str, username: str, password: str) -> None:
        self.base_url = _ruler_base(base_url)
        self.auth = httpx.BasicAuth(username, password)

    def request(
        self,
        method: str,
        path: str,
        *,
        body: bytes | None = None,
        content_type: str | None = None,
        allow_not_found: bool = False,
    ) -> tuple[int, bytes]:
        headers: dict[str, str] = {}
        if content_type:
            headers["Content-Type"] = content_type
        try:
            response = httpx.request(
                method,
                f"{self.base_url}{path}",
                content=body,
                headers=headers,
                auth=self.auth,
                timeout=30,
                follow_redirects=False,
            )
        except httpx.HTTPError as exc:
            raise RuleSyncError(f"Mimir {method} {path} failed: {type(exc).__name__}") from exc
        if allow_not_found and response.status_code == 404:
            return response.status_code, b""
        if response.is_error:
            detail = " ".join(response.text.split())[:300]
            suffix = f": {detail}" if detail else ""
            raise RuleSyncError(
                f"Mimir {method} {path} returned HTTP {response.status_code}{suffix}"
            )
        return response.status_code, response.content


def _yaml_groups(payload: bytes, *, namespace: str) -> list[dict[str, Any]]:
    if not payload:
        return []
    try:
        parsed = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise RuleSyncError("Mimir returned invalid rule YAML") from exc
    if isinstance(parsed, dict) and parsed.get("name"):
        return [parsed]
    if isinstance(parsed, dict):
        groups = parsed.get(namespace)
        if isinstance(groups, list):
            return [group for group in groups if isinstance(group, dict)]
    return []


def _active_rule_groups(
    client: MimirClient,
    namespace: str,
    *,
    group_name: str | None = None,
) -> list[dict[str, Any]]:
    parameters = {"file": namespace}
    if group_name is not None:
        parameters["rule_group"] = group_name
    query = urllib.parse.urlencode(parameters)
    _, payload = client.request("GET", f"/api/v1/rules?{query}")
    try:
        response = json.loads(payload)
        groups = response["data"]["groups"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuleSyncError("Mimir returned invalid active-rule JSON") from exc
    if response.get("status") != "success" or not isinstance(groups, list):
        raise RuleSyncError("Mimir did not return active rule groups")
    return [group for group in groups if isinstance(group, dict)]


def _remote_rule_names(client: MimirClient, namespace: str, group_name: str) -> set[str]:
    names: set[str] = set()
    for group in _active_rule_groups(client, namespace, group_name=group_name):
        if group.get("name") != group_name:
            continue
        group_rules = group.get("rules")
        if not isinstance(group_rules, list):
            raise RuleSyncError("Mimir returned invalid active-rule JSON")
        for rule in group_rules:
            if isinstance(rule, dict) and isinstance(rule.get("name"), str):
                names.add(rule["name"])
    return names


def _active_rules(client: MimirClient, namespace: str) -> dict[str, dict[str, Any]]:
    active: dict[str, dict[str, Any]] = {}
    for group in _active_rule_groups(client, namespace):
        group_rules = group.get("rules")
        if not isinstance(group_rules, list):
            raise RuleSyncError("Mimir returned invalid active-rule JSON")
        for rule in group_rules:
            name = rule.get("name") if isinstance(rule, dict) else None
            if not isinstance(name, str) or not name:
                raise RuleSyncError("Mimir returned an active rule without a name")
            if name in active:
                raise RuleSyncError(f"Mimir returned duplicate active rule {name}")
            active[name] = rule
    return active


def _evaluation_time(value: Any) -> float | None:
    if not isinstance(value, str) or not value:
        return None
    normalized = value[:-1] + "+00:00" if value.endswith("Z") else value
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.timestamp()


def _duration_signature(value: Any) -> str | int:
    raw = str(value or "")
    if not raw:
        return ""
    position = 0
    total_ms = 0
    for match in _DURATION_PART.finditer(raw):
        if match.start() != position:
            return raw
        total_ms += int(match.group(1)) * _DURATION_MS[match.group(2)]
        position = match.end()
    return total_ms if position == len(raw) else raw


def _rule_group_signature(group: dict[str, Any]) -> dict[str, Any]:
    signatures: dict[str, Any] = {
        "name": str(group.get("name", "")),
        "interval": _duration_signature(group.get("interval")),
        "rules": {},
    }
    for rule in group.get("rules", []):
        if not isinstance(rule, dict):
            continue
        name = rule.get("alert") or rule.get("record")
        if not isinstance(name, str):
            continue
        signatures["rules"][name] = {
            "kind": "alert" if rule.get("alert") else "record",
            "expr": " ".join(str(rule.get("expr", "")).split()),
            "for": _duration_signature(rule.get("for")),
            "labels": rule.get("labels") or {},
            "annotations": rule.get("annotations") or {},
        }
    return signatures


def _group_path(namespace: str, group_name: str | None = None) -> str:
    encoded_namespace = urllib.parse.quote(namespace, safe="")
    path = f"/config/v1/rules/{encoded_namespace}"
    if group_name is not None:
        path += f"/{urllib.parse.quote(group_name, safe='')}"
    return path


def _remote_groups(client: MimirClient, namespace: str) -> dict[str, dict[str, Any]]:
    status, payload = client.request("GET", _group_path(namespace), allow_not_found=True)
    if status == 404:
        return {}
    groups = _yaml_groups(payload, namespace=namespace)
    result: dict[str, dict[str, Any]] = {}
    for group in groups:
        name = group.get("name")
        if not isinstance(name, str) or not name:
            raise RuleSyncError("Mimir returned a rule group without a name")
        if name in result:
            raise RuleSyncError(f"Mimir returned duplicate rule group {name}")
        result[name] = group
    return result


def _post_group(client: MimirClient, namespace: str, group: dict[str, Any]) -> None:
    response_status, _ = client.request(
        "POST",
        _group_path(namespace),
        body=yaml.safe_dump(group, sort_keys=False).encode("utf-8"),
        content_type="application/yaml",
    )
    if response_status != 202:
        raise RuleSyncError(f"Mimir rule update returned HTTP {response_status}, expected 202")


def _delete_group(client: MimirClient, namespace: str, group_name: str) -> None:
    client.request("DELETE", _group_path(namespace, group_name), allow_not_found=True)


def _expected_rule_names(group: dict[str, Any]) -> set[str]:
    return {str(rule.get("alert") or rule.get("record")) for rule in group["rules"]}


def _groups_match(
    client: MimirClient,
    namespace: str,
    expected: dict[str, dict[str, Any]],
    *,
    exact_names: bool,
) -> bool:
    stored = _remote_groups(client, namespace)
    if exact_names and set(stored) != set(expected):
        return False
    if not set(expected) <= set(stored):
        return False
    for name, group in expected.items():
        if _rule_group_signature(stored[name]) != _rule_group_signature(group):
            return False
        if _remote_rule_names(client, namespace, name) != _expected_rule_names(group):
            return False
    return True


def _wait_for_groups(
    client: MimirClient,
    namespace: str,
    expected: dict[str, dict[str, Any]],
    *,
    exact_names: bool,
) -> None:
    for _ in range(12):
        if _groups_match(client, namespace, expected, exact_names=exact_names):
            return
        time.sleep(5)
    qualifier = "exact " if exact_names else ""
    raise RuleSyncError(f"Mimir did not expose the {qualifier}expected rule groups")


def _evaluation_error(
    active: dict[str, dict[str, Any]],
    expected_names: set[str],
    previous_evaluations: dict[str, float | None],
) -> str | None:
    missing = sorted(expected_names - set(active))
    if missing:
        return "active rule inventory is missing " + ", ".join(missing[:8])

    unhealthy: list[str] = []
    unevaluated: list[str] = []
    for name in sorted(expected_names):
        rule = active[name]
        health = rule.get("health")
        if health != "ok":
            unhealthy.append(f"{name}={health if isinstance(health, str) else 'missing'}")
        evaluated_at = _evaluation_time(rule.get("lastEvaluation"))
        previous = previous_evaluations.get(name)
        if evaluated_at is None or (
            name in previous_evaluations
            and previous is not None
            and evaluated_at <= previous
        ):
            unevaluated.append(name)
    if unhealthy:
        return "deployed rules have non-OK health: " + ", ".join(unhealthy[:8])
    if unevaluated:
        return "deployed rules have not completed a post-sync evaluation: " + ", ".join(
            unevaluated[:8]
        )
    return None


def _wait_for_rule_evaluation(
    client: MimirClient,
    namespace: str,
    groups: list[dict[str, Any]],
    *,
    previous_evaluations: dict[str, float | None],
    wait_seconds: int,
) -> None:
    expected_names = {
        str(rule.get("alert") or rule.get("record"))
        for group in groups
        for rule in group["rules"]
    }
    deadline = time.monotonic() + wait_seconds
    last_error = "Mimir rule evaluation has not completed"
    while True:
        try:
            last_error = (
                _evaluation_error(
                    _active_rules(client, namespace),
                    expected_names,
                    previous_evaluations,
                )
                or ""
            )
            if not last_error:
                return
        except RuleSyncError as exc:
            last_error = str(exc)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuleSyncError(last_error)
        time.sleep(min(RULE_EVALUATION_POLL_SECONDS, remaining))


def _restore_namespace(
    client: MimirClient,
    namespace: str,
    previous: dict[str, dict[str, Any]],
) -> None:
    for group in previous.values():
        _post_group(client, namespace, group)
    current = _remote_groups(client, namespace)
    for stale_name in sorted(set(current) - set(previous)):
        _delete_group(client, namespace, stale_name)
    _wait_for_groups(client, namespace, previous, exact_names=True)


def sync_groups(
    client: MimirClient,
    namespace: str,
    groups: list[dict[str, Any]],
    *,
    evaluation_wait_seconds: int = DEFAULT_EVALUATION_WAIT_SECONDS,
) -> None:
    for group in groups:
        group_rules = group.get("rules")
        if (
            not isinstance(group_rules, list)
            or not group_rules
            or len(group_rules) > MAX_RULES_PER_GROUP
        ):
            raise RuleSyncError(
                f"rule group {group.get('name', '<unnamed>')} must contain between 1 and "
                f"{MAX_RULES_PER_GROUP} rules"
            )
    desired = {str(group["name"]): group for group in groups}
    if len(desired) != len(groups):
        raise RuleSyncError("rule group names must be unique")
    previous = _remote_groups(client, namespace)
    previous_evaluations = {
        name: _evaluation_time(rule.get("lastEvaluation"))
        for name, rule in _active_rules(client, namespace).items()
    }
    try:
        for group in groups:
            _post_group(client, namespace, group)
        _wait_for_groups(client, namespace, desired, exact_names=False)
        for stale_name in sorted(set(previous) - set(desired)):
            _delete_group(client, namespace, stale_name)
        _wait_for_groups(client, namespace, desired, exact_names=True)
        _wait_for_rule_evaluation(
            client,
            namespace,
            groups,
            previous_evaluations=previous_evaluations,
            wait_seconds=evaluation_wait_seconds,
        )
    except Exception as sync_error:
        try:
            _restore_namespace(client, namespace, previous)
        except Exception as rollback_error:
            raise RuleSyncError(
                f"rule sync failed ({type(sync_error).__name__}) and namespace rollback "
                f"also failed ({type(rollback_error).__name__})"
            ) from sync_error
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_RULE_FILE)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_PROM_URL"))
    parser.add_argument("--username", default=os.environ.get("GRAFANA_PROM_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("GRAFANA_PROM_PASSWORD"))
    parser.add_argument(
        "--evaluation-wait-seconds",
        type=int,
        default=DEFAULT_EVALUATION_WAIT_SECONDS,
    )
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    groups = _load_groups(args.file)
    if args.dry_run:
        summary = [
            {
                "group": group["name"],
                "rules": [rule.get("alert") or rule.get("record") for rule in group["rules"]],
            }
            for group in groups
        ]
        print(json.dumps({"namespace": args.namespace, "groups": summary}))
        return 0
    if not args.url or not args.username or not args.password:
        raise SystemExit("Grafana URL, username, and password are required")
    client = MimirClient(args.url, args.username, args.password)
    sync_groups(
        client,
        args.namespace,
        groups,
        evaluation_wait_seconds=args.evaluation_wait_seconds,
    )
    rule_count = sum(len(group["rules"]) for group in groups)
    print(f"synced namespace={args.namespace} groups={len(groups)} rules={rule_count}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
