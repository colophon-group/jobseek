#!/usr/bin/env python3
"""Audit or enforce Jobseek's narrow Hetzner resource lifecycle baseline.

The resource allowlist is deliberately compiled into this program.  It does
not accept provider IDs or names from the command line, and dry-run is the
default.  Human-readable output contains allowlisted names and conformance
booleans only; provider IDs, addresses, labels outside the managed contract,
and credentials are never rendered.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

API_ROOT = "https://api.hetzner.cloud/v1"
MANAGED_LABELS = {
    "environment": "production",
    "project": "jobseek",
    "owner": "jobseek-operations",
}
DEFAULT_ACTION_ATTEMPTS = 60
DEFAULT_ACTION_INTERVAL_SECONDS = 1.0


class FleetError(RuntimeError):
    """The desired fleet state could not be proven or safely applied."""


@dataclass(frozen=True)
class DesiredResource:
    kind: str
    collection: str
    name: str
    role: str
    rebuild_protection: bool = False

    @property
    def labels(self) -> dict[str, str]:
        return {**MANAGED_LABELS, "role": self.role}

    @property
    def protection(self) -> dict[str, bool]:
        result = {"delete": True}
        if self.kind == "server":
            result["rebuild"] = self.rebuild_protection
        return result


DESIRED_RESOURCES = (
    DesiredResource("server", "servers", "jobseek-crawler", "crawler", True),
    DesiredResource(
        "server",
        "servers",
        "jobseek-postings-postgresql",
        "postgresql",
        True,
    ),
    DesiredResource("server", "servers", "jobseek-typesense", "typesense", True),
    DesiredResource("server", "servers", "murmur-server", "murmur", True),
    DesiredResource("volume", "volumes", "jobseek-postings-postgresql", "postgresql"),
    DesiredResource("volume", "volumes", "murmur-volume", "murmur"),
    DesiredResource("network", "networks", "jobseek-network", "private-network"),
)


class Transport(Protocol):
    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]: ...


def _redacted_api_path(path: str) -> str:
    """Keep an operation diagnosable without exposing provider identifiers."""
    return re.sub(r"/(?P<id>[0-9]+)(?=/|$)", "/{id}", path)


class HttpTransport:
    """Small standard-library Hetzner API transport."""

    def __init__(self, token: str) -> None:
        if not token or any(character.isspace() for character in token):
            raise FleetError("HETZNER_API_KEY is missing or invalid")
        self._token = token
        self._context = _trusted_ssl_context()

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        request = urllib.request.Request(
            API_ROOT + path,
            data=data,
            method=method,
            headers={
                "Authorization": "Bearer " + self._token,
                "Content-Type": "application/json",
                "User-Agent": "jobseek-hetzner-fleet-lifecycle/1",
            },
        )
        try:
            with urllib.request.urlopen(
                request,
                timeout=30,
                context=self._context,
            ) as response:
                payload = response.read()
        except urllib.error.HTTPError as exc:
            raise FleetError(
                f"Hetzner {method} {_redacted_api_path(path)} returned HTTP {exc.code}"
            ) from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise FleetError(f"Hetzner {method} {_redacted_api_path(path)} failed") from exc
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except json.JSONDecodeError as exc:
            raise FleetError("Hetzner returned invalid JSON") from exc
        if not isinstance(parsed, dict):
            raise FleetError("Hetzner returned an unexpected response")
        return parsed


def _trusted_ssl_context() -> ssl.SSLContext:
    """Use verified platform roots, including framework Python on macOS."""
    try:
        context = ssl.create_default_context()
    except (OSError, ssl.SSLError) as exc:
        raise FleetError("the operating system TLS trust store is unavailable") from exc
    paths = ssl.get_default_verify_paths()
    if paths.cafile is not None or paths.capath is not None:
        return context
    for candidate in (Path("/etc/ssl/cert.pem"), Path("/etc/ssl/certs/ca-certificates.crt")):
        if candidate.is_file():
            try:
                return ssl.create_default_context(cafile=str(candidate))
            except (OSError, ssl.SSLError):
                continue
    raise FleetError("the operating system TLS trust store is unavailable")


@dataclass(frozen=True)
class ResourceState:
    desired: DesiredResource
    provider_id: int
    current_labels: dict[str, str]
    current_protection: dict[str, bool]

    @property
    def labels_match(self) -> bool:
        return all(
            self.current_labels.get(key) == value for key, value in self.desired.labels.items()
        )

    @property
    def protection_matches(self) -> bool:
        return all(
            self.current_protection.get(key) is value
            for key, value in self.desired.protection.items()
        )

    @property
    def compliant(self) -> bool:
        return self.labels_match and self.protection_matches

    @property
    def merged_labels(self) -> dict[str, str]:
        return {**self.current_labels, **self.desired.labels}


def _inventory_path(desired: DesiredResource) -> str:
    query = urllib.parse.urlencode({"name": desired.name, "per_page": 50})
    return f"/{desired.collection}?{query}"


def _validate_resource(
    desired: DesiredResource,
    resource: object,
) -> ResourceState:
    if not isinstance(resource, dict) or resource.get("name") != desired.name:
        raise FleetError(f"invalid {desired.kind} inventory for {desired.name}")
    provider_id = resource.get("id")
    if not isinstance(provider_id, int) or isinstance(provider_id, bool) or provider_id <= 0:
        raise FleetError(f"invalid {desired.kind} inventory for {desired.name}")
    labels = resource.get("labels")
    protection = resource.get("protection")
    if not isinstance(labels, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in labels.items()
    ):
        raise FleetError(f"invalid {desired.kind} labels for {desired.name}")
    if not isinstance(protection, dict):
        raise FleetError(f"invalid {desired.kind} protection for {desired.name}")
    for field in desired.protection:
        if not isinstance(protection.get(field), bool):
            raise FleetError(f"invalid {desired.kind} protection for {desired.name}")
    return ResourceState(
        desired=desired,
        provider_id=provider_id,
        current_labels=dict(labels),
        current_protection={field: protection[field] for field in desired.protection},
    )


def discover(transport: Transport) -> list[ResourceState]:
    """Resolve every allowlisted name exactly once before any mutation."""
    states: list[ResourceState] = []
    seen_ids: dict[str, set[int]] = {}
    for desired in DESIRED_RESOURCES:
        payload = transport.request("GET", _inventory_path(desired))
        resources = payload.get(desired.collection)
        if not isinstance(resources, list):
            raise FleetError(f"{desired.kind} inventory is unavailable")
        meta = payload.get("meta") or {}
        if not isinstance(meta, dict):
            raise FleetError(f"invalid {desired.kind} inventory for {desired.name}")
        pagination = meta.get("pagination") or {}
        if not isinstance(pagination, dict):
            raise FleetError(f"invalid {desired.kind} inventory for {desired.name}")
        if pagination.get("next_page") is not None:
            raise FleetError(f"ambiguous {desired.kind} inventory for {desired.name}")
        if len(resources) != 1:
            raise FleetError(f"expected exactly one {desired.kind} named {desired.name}")
        state = _validate_resource(desired, resources[0])
        kind_ids = seen_ids.setdefault(desired.kind, set())
        if state.provider_id in kind_ids:
            raise FleetError(f"{desired.kind} inventory is not one-to-one")
        kind_ids.add(state.provider_id)
        states.append(state)
    return states


def _wait_action(
    transport: Transport,
    action: object,
    *,
    attempts: int,
    interval_seconds: float,
) -> None:
    if not isinstance(action, dict):
        raise FleetError("Hetzner protection response omitted its action")
    action_id = action.get("id")
    if not isinstance(action_id, int) or isinstance(action_id, bool) or action_id <= 0:
        raise FleetError("Hetzner protection response omitted its action")
    status = action.get("status")
    if status == "success":
        return
    if status == "error":
        raise FleetError("Hetzner lifecycle protection action failed")
    for attempt in range(attempts):
        current = transport.request("GET", f"/actions/{action_id}").get("action")
        if not isinstance(current, dict):
            raise FleetError("Hetzner action status is unavailable")
        status = current.get("status")
        if status == "success":
            return
        if status == "error":
            raise FleetError("Hetzner lifecycle protection action failed")
        if attempt + 1 < attempts and interval_seconds:
            time.sleep(interval_seconds)
    raise FleetError("Hetzner lifecycle protection action timed out")


def _resource_path(state: ResourceState) -> str:
    return f"/{state.desired.collection}/{state.provider_id}"


def apply(
    transport: Transport,
    states: list[ResourceState],
    *,
    action_attempts: int = DEFAULT_ACTION_ATTEMPTS,
    action_interval_seconds: float = DEFAULT_ACTION_INTERVAL_SECONDS,
) -> list[ResourceState]:
    """Apply only safe additive labels and protection, then re-read all state."""
    if action_attempts <= 0 or action_interval_seconds < 0:
        raise FleetError("invalid action polling bounds")
    for state in states:
        path = _resource_path(state)
        if not state.labels_match:
            transport.request("PUT", path, body={"labels": state.merged_labels})
        if not state.protection_matches:
            response = transport.request(
                "POST",
                path + "/actions/change_protection",
                body=state.desired.protection,
            )
            _wait_action(
                transport,
                response.get("action"),
                attempts=action_attempts,
                interval_seconds=action_interval_seconds,
            )
    verified = discover(transport)
    if not all(state.compliant for state in verified):
        raise FleetError("post-apply lifecycle verification failed")
    return verified


def summary(states: list[ResourceState], *, mode: str) -> dict[str, Any]:
    """Return safe evidence without raw provider identifiers or addresses."""
    return {
        "mode": mode,
        "compliant": all(state.compliant for state in states),
        "resource_count": len(states),
        "resources": [
            {
                "kind": state.desired.kind,
                "name": state.desired.name,
                "labels_compliant": state.labels_match,
                "delete_protection": state.current_protection["delete"],
                **(
                    {"rebuild_protection": state.current_protection["rebuild"]}
                    if state.desired.kind == "server"
                    else {}
                ),
                "planned_actions": [
                    action
                    for action, needed in (
                        ("merge_managed_labels", not state.labels_match),
                        ("enable_lifecycle_protection", not state.protection_matches),
                    )
                    if needed
                ],
            }
            for state in states
        ],
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Audit Jobseek's fixed Hetzner lifecycle baseline (dry-run by default)."
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="merge managed labels and enable lifecycle protection",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        transport = HttpTransport(os.environ.get("HETZNER_API_KEY", ""))
        states = discover(transport)
        if args.apply:
            states = apply(transport, states)
        evidence = summary(states, mode="apply" if args.apply else "dry-run")
        print(json.dumps(evidence, sort_keys=True))
        return 0 if evidence["compliant"] else 2
    except FleetError as exc:
        print(
            json.dumps(
                {"compliant": False, "error": str(exc), "mode": "failed"},
                sort_keys=True,
            ),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
