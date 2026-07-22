#!/usr/bin/env python3
"""Transactionally sync Jobseek's single Grafana Cloud Mimir rule group."""

from __future__ import annotations

import argparse
import json
import os
import time
import urllib.parse
from pathlib import Path
from typing import Any

import httpx
import yaml

DEFAULT_NAMESPACE = "jobseek_crawler_reliability"
DEFAULT_RULE_FILE = Path(__file__).resolve().parents[1] / "apps" / "crawler" / "alerts.yaml"


class RuleSyncError(RuntimeError):
    """The source rules or remote ruler state failed validation."""


def _ruler_base(url: str) -> str:
    normalized = url.strip().rstrip("/")
    if normalized.endswith("/push"):
        normalized = normalized[: -len("/push")]
    if not normalized.endswith("/api/prom"):
        raise RuleSyncError("Grafana URL must end in /api/prom or /api/prom/push")
    return normalized


def _load_group(path: Path) -> dict[str, Any]:
    try:
        document = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise RuleSyncError(f"cannot parse {path}") from exc
    groups = document.get("groups") if isinstance(document, dict) else None
    if not isinstance(groups, list) or len(groups) != 1 or not isinstance(groups[0], dict):
        raise RuleSyncError("rule file must contain exactly one rule group")
    group = groups[0]
    if not isinstance(group.get("name"), str) or not group["name"]:
        raise RuleSyncError("rule group name is required")
    rules = group.get("rules")
    if not isinstance(rules, list) or not rules:
        raise RuleSyncError("rule group must contain at least one rule")
    names: list[str] = []
    for rule in rules:
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
            runbook = str(annotations.get("runbook", "")) if isinstance(annotations, dict) else ""
            if not runbook.startswith("https://github.com/colophon-group/jobseek/"):
                raise RuleSyncError(f"alert {name} must have a repository runbook URL")
    if len(names) != len(set(names)):
        raise RuleSyncError("rule names must be unique")
    return group


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
            raise RuleSyncError(f"Mimir {method} {path} returned HTTP {response.status_code}")
        return response.status_code, response.content


def _yaml_group(payload: bytes, *, namespace: str) -> dict[str, Any] | None:
    if not payload:
        return None
    try:
        parsed = yaml.safe_load(payload)
    except yaml.YAMLError as exc:
        raise RuleSyncError("Mimir returned invalid rule YAML") from exc
    if isinstance(parsed, dict) and parsed.get("name"):
        return parsed
    if isinstance(parsed, dict):
        groups = parsed.get(namespace)
        if isinstance(groups, list) and groups and isinstance(groups[0], dict):
            return groups[0]
    return None


def _remote_rule_names(client: MimirClient, namespace: str, group_name: str) -> set[str]:
    query = urllib.parse.urlencode({"file": namespace, "rule_group": group_name})
    _, payload = client.request("GET", f"/api/v1/rules?{query}")
    try:
        response = json.loads(payload)
        groups = response["data"]["groups"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise RuleSyncError("Mimir returned invalid active-rule JSON") from exc
    names: set[str] = set()
    for group in groups:
        if group.get("name") != group_name:
            continue
        for rule in group.get("rules", []):
            if isinstance(rule.get("name"), str):
                names.add(rule["name"])
    return names


def _rule_group_signature(group: dict[str, Any]) -> dict[str, Any]:
    signatures: dict[str, Any] = {
        "name": str(group.get("name", "")),
        "interval": str(group.get("interval", "")),
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
            "for": str(rule.get("for", "")),
            "labels": rule.get("labels") or {},
            "annotations": rule.get("annotations") or {},
        }
    return signatures


def _remote_group(client: MimirClient, group_path: str, namespace: str) -> dict[str, Any] | None:
    status, payload = client.request("GET", group_path, allow_not_found=True)
    return _yaml_group(payload, namespace=namespace) if status != 404 else None


def sync_group(client: MimirClient, namespace: str, group: dict[str, Any]) -> None:
    encoded_namespace = urllib.parse.quote(namespace, safe="")
    encoded_group = urllib.parse.quote(str(group["name"]), safe="")
    group_path = f"/config/v1/rules/{encoded_namespace}/{encoded_group}"
    previous = _remote_group(client, group_path, namespace)
    new_payload = yaml.safe_dump(group, sort_keys=False).encode("utf-8")
    expected_names = {str(rule.get("alert") or rule.get("record")) for rule in group["rules"]}
    expected_signature = _rule_group_signature(group)

    try:
        response_status, _ = client.request(
            "POST",
            f"/config/v1/rules/{encoded_namespace}",
            body=new_payload,
            content_type="application/yaml",
        )
        if response_status != 202:
            raise RuleSyncError(f"Mimir rule update returned HTTP {response_status}, expected 202")
        for _ in range(12):
            stored = _remote_group(client, group_path, namespace)
            if (
                stored is not None
                and _rule_group_signature(stored) == expected_signature
                and _remote_rule_names(client, namespace, str(group["name"])) == expected_names
            ):
                return
            time.sleep(5)
        raise RuleSyncError("Mimir did not expose the exact expected rule set after update")
    except Exception:
        if previous is None:
            client.request("DELETE", group_path, allow_not_found=True)
        else:
            client.request(
                "POST",
                f"/config/v1/rules/{encoded_namespace}",
                body=yaml.safe_dump(previous, sort_keys=False).encode("utf-8"),
                content_type="application/yaml",
            )
        raise


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--file", type=Path, default=DEFAULT_RULE_FILE)
    parser.add_argument("--namespace", default=DEFAULT_NAMESPACE)
    parser.add_argument("--url", default=os.environ.get("GRAFANA_PROM_URL"))
    parser.add_argument("--username", default=os.environ.get("GRAFANA_PROM_USERNAME"))
    parser.add_argument("--password", default=os.environ.get("GRAFANA_PROM_PASSWORD"))
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _build_parser().parse_args()
    group = _load_group(args.file)
    if args.dry_run:
        names = [rule.get("alert") or rule.get("record") for rule in group["rules"]]
        print(json.dumps({"namespace": args.namespace, "group": group["name"], "rules": names}))
        return 0
    if not args.url or not args.username or not args.password:
        raise SystemExit("Grafana URL, username, and password are required")
    client = MimirClient(args.url, args.username, args.password)
    sync_group(client, args.namespace, group)
    print(f"synced namespace={args.namespace} group={group['name']} rules={len(group['rules'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
