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
import math
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
MAX_API_BODY_BYTES = 1_048_576


class FleetError(RuntimeError):
    """The desired fleet state could not be proven or safely applied."""


class _RejectRedirects(urllib.request.HTTPRedirectHandler):
    """Never forward the provider credential to a redirected origin."""

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> None:
        return None


@dataclass(frozen=True)
class DesiredResource:
    kind: str
    collection: str
    name: str
    role: str

    @property
    def labels(self) -> dict[str, str]:
        return {**MANAGED_LABELS, "role": self.role}

    @property
    def protection(self) -> dict[str, bool]:
        result = {"delete": True}
        if self.kind == "server":
            result["rebuild"] = True
        return result


DESIRED_RESOURCES = (
    DesiredResource("server", "servers", "jobseek-crawler", "crawler-browser"),
    DesiredResource(
        "server",
        "servers",
        "jobseek-postings-postgresql",
        "postgresql",
    ),
    DesiredResource("server", "servers", "jobseek-typesense", "typesense"),
    DesiredResource("server", "servers", "murmur-server", "murmur"),
    DesiredResource("volume", "volumes", "jobseek-postings-postgresql", "postgresql"),
    DesiredResource("volume", "volumes", "murmur-volume", "murmur"),
    DesiredResource("network", "networks", "jobseek-network", "private-network"),
)


def _validate_request_target(method: str, path: str) -> None:
    """Constrain transport use to the fixed read, label, and protection surface."""
    parsed = urllib.parse.urlsplit(path)
    if parsed.scheme or parsed.netloc or parsed.fragment:
        raise FleetError("unsupported Hetzner lifecycle request")
    parts = parsed.path.strip("/").split("/")
    collections = {desired.collection for desired in DESIRED_RESOURCES}

    if method == "GET" and len(parts) == 1 and parts[0] in collections:
        try:
            query = urllib.parse.parse_qs(parsed.query, strict_parsing=True)
        except ValueError as exc:
            raise FleetError("unsupported Hetzner lifecycle request") from exc
        allowed_names = {
            desired.name for desired in DESIRED_RESOURCES if desired.collection == parts[0]
        }
        if (
            set(query) == {"name", "page", "per_page"}
            and len(query["name"]) == 1
            and query["name"][0] in allowed_names
            and query["page"] == ["1"]
            and query["per_page"] == ["50"]
        ):
            return

    positive_id = len(parts) >= 2 and re.fullmatch(r"[1-9][0-9]*", parts[1]) is not None
    no_query = not parsed.query
    if (
        method == "GET"
        and no_query
        and positive_id
        and len(parts) == 2
        and (parts[0] in collections or parts[0] == "actions")
    ):
        return
    if method == "PUT" and no_query and positive_id and len(parts) == 2 and parts[0] in collections:
        return
    if (
        method == "POST"
        and no_query
        and positive_id
        and len(parts) == 4
        and parts[0] in collections
        and parts[2:] == ["actions", "change_protection"]
    ):
        return
    raise FleetError("unsupported Hetzner lifecycle request")


def _validate_request_body(method: str, path: str, body: dict[str, Any] | None) -> None:
    """Reject payloads that could weaken protection or expand mutation scope."""
    collection = urllib.parse.urlsplit(path).path.strip("/").split("/")[0]
    if method == "GET":
        if body is None:
            return
    elif method == "PUT":
        if (
            isinstance(body, dict)
            and set(body) == {"labels"}
            and isinstance(body["labels"], dict)
            and all(
                isinstance(key, str) and isinstance(value, str)
                for key, value in body["labels"].items()
            )
        ):
            return
    elif method == "POST" and isinstance(body, dict):
        expected = (
            {"delete": True, "rebuild": True} if collection == "servers" else {"delete": True}
        )
        if body == expected:
            return
    raise FleetError("unsupported Hetzner lifecycle request body")


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
        self._opener = urllib.request.build_opener(
            urllib.request.HTTPSHandler(context=self._context),
            _RejectRedirects(),
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        _validate_request_target(method, path)
        _validate_request_body(method, path, body)
        try:
            data = json.dumps(body, separators=(",", ":")).encode() if body is not None else None
        except (TypeError, ValueError, UnicodeError) as exc:
            raise FleetError("Hetzner request body is invalid") from exc
        if data is not None and len(data) > MAX_API_BODY_BYTES:
            raise FleetError("Hetzner request body exceeds the safety limit")
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
            with self._opener.open(
                request,
                timeout=30,
            ) as response:
                payload = response.read(MAX_API_BODY_BYTES + 1)
        except urllib.error.HTTPError as exc:
            status = exc.code
            exc.close()
            raise FleetError(
                f"Hetzner {method} {_redacted_api_path(path)} returned HTTP {status}"
            ) from None
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            raise FleetError(f"Hetzner {method} {_redacted_api_path(path)} failed") from None
        if len(payload) > MAX_API_BODY_BYTES:
            raise FleetError("Hetzner response exceeds the safety limit")
        if not payload:
            return {}
        try:
            parsed = json.loads(payload)
        except ValueError as exc:
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
    if context.get_ca_certs():
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
    query = urllib.parse.urlencode({"name": desired.name, "page": 1, "per_page": 50})
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
        meta = payload.get("meta")
        if not isinstance(meta, dict):
            raise FleetError(f"invalid {desired.kind} inventory for {desired.name}")
        pagination = meta.get("pagination")
        if not isinstance(pagination, dict):
            raise FleetError(f"invalid {desired.kind} inventory for {desired.name}")
        required_pagination = {
            "page",
            "per_page",
            "previous_page",
            "next_page",
            "last_page",
            "total_entries",
        }
        if not required_pagination.issubset(pagination):
            raise FleetError(f"ambiguous {desired.kind} inventory for {desired.name}")
        page = pagination["page"]
        per_page = pagination["per_page"]
        last_page = pagination["last_page"]
        total_entries = pagination["total_entries"]
        if (
            not isinstance(page, int)
            or isinstance(page, bool)
            or page != 1
            or not isinstance(per_page, int)
            or isinstance(per_page, bool)
            or per_page != 50
            or pagination["previous_page"] is not None
            or pagination["next_page"] is not None
            or (
                last_page is not None
                and (
                    not isinstance(last_page, int) or isinstance(last_page, bool) or last_page != 1
                )
            )
            or (
                total_entries is not None
                and (
                    not isinstance(total_entries, int)
                    or isinstance(total_entries, bool)
                    or total_entries != len(resources)
                )
            )
        ):
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


def _reread_resource(transport: Transport, expected: ResourceState) -> ResourceState:
    payload = transport.request("GET", _resource_path(expected))
    resource = payload.get(expected.desired.kind)
    current = _validate_resource(expected.desired, resource)
    if current.provider_id != expected.provider_id:
        raise FleetError(f"{expected.desired.kind} identity changed during apply")
    return current


def _validate_apply_states(states: list[ResourceState]) -> None:
    if tuple(state.desired for state in states) != DESIRED_RESOURCES:
        raise FleetError("apply requires the complete fixed Hetzner allowlist")
    seen: set[tuple[str, int]] = set()
    for state in states:
        if (
            not isinstance(state.provider_id, int)
            or isinstance(state.provider_id, bool)
            or state.provider_id <= 0
        ):
            raise FleetError("apply received invalid Hetzner resource identity")
        identity = (state.desired.kind, state.provider_id)
        if identity in seen:
            raise FleetError("apply received duplicate Hetzner resource identity")
        seen.add(identity)


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
    if status not in {"running", "success", "error"}:
        raise FleetError("Hetzner protection response has an invalid status")
    if status == "success":
        return
    if status == "error":
        raise FleetError("Hetzner lifecycle protection action failed")
    for attempt in range(attempts):
        current = transport.request("GET", f"/actions/{action_id}").get("action")
        if not isinstance(current, dict):
            raise FleetError("Hetzner action status is unavailable")
        current_id = current.get("id")
        if (
            not isinstance(current_id, int)
            or isinstance(current_id, bool)
            or current_id != action_id
        ):
            raise FleetError("Hetzner action identity changed while polling")
        status = current.get("status")
        if status not in {"running", "success", "error"}:
            raise FleetError("Hetzner action status is invalid")
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
    if (
        not isinstance(action_attempts, int)
        or isinstance(action_attempts, bool)
        or not 1 <= action_attempts <= DEFAULT_ACTION_ATTEMPTS
        or not isinstance(action_interval_seconds, (int, float))
        or isinstance(action_interval_seconds, bool)
        or not math.isfinite(action_interval_seconds)
        or not 0 <= action_interval_seconds <= DEFAULT_ACTION_INTERVAL_SECONDS
    ):
        raise FleetError("invalid action polling bounds")

    _validate_apply_states(states)

    # Protection is monotonic and safety-critical. Complete this phase before
    # any label replacement so a label error cannot leave later resources
    # unnecessarily exposed. Protection is deliberately never rolled back.
    for state in states:
        current = _reread_resource(transport, state)
        if current.protection_matches:
            continue
        path = _resource_path(state)
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
        if not _reread_resource(transport, state).protection_matches:
            raise FleetError("Hetzner lifecycle protection could not be verified")

    # Hetzner label updates replace the entire map. Re-read each resource
    # immediately before the write, confirm the map is stable, merge only the
    # managed keys, and prove every observed label survived the replacement.
    for state in states:
        current = _reread_resource(transport, state)
        if current.labels_match:
            continue
        confirmed = _reread_resource(transport, state)
        if confirmed.current_labels != current.current_labels:
            raise FleetError("Hetzner labels changed concurrently during apply")
        expected_labels = confirmed.merged_labels
        response = transport.request(
            "PUT",
            _resource_path(state),
            body={"labels": expected_labels},
        )
        updated = _validate_resource(state.desired, response.get(state.desired.kind))
        if updated.provider_id != state.provider_id or any(
            updated.current_labels.get(key) != value for key, value in expected_labels.items()
        ):
            raise FleetError("Hetzner label update could not be verified")
        reread = _reread_resource(transport, state)
        if any(reread.current_labels.get(key) != value for key, value in expected_labels.items()):
            raise FleetError("Hetzner label update could not be verified")

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
