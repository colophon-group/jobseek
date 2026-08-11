"""Tests for the narrow, fail-closed Hetzner fleet lifecycle manager."""

from __future__ import annotations

import copy
import importlib.util
import json
import sys
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts/manage-hetzner-fleet.py"
SPEC = importlib.util.spec_from_file_location("manage_hetzner_fleet", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
fleet = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = fleet
SPEC.loader.exec_module(fleet)


def _resource(
    desired: Any,
    provider_id: int,
    *,
    compliant: bool,
) -> dict[str, Any]:
    labels = (
        {**desired.labels, "billing": "preserve-me"}
        if compliant
        else {"legacy": "preserve-me", "owner": "previous-owner"}
    )
    protection = {key: compliant for key in desired.protection}
    result: dict[str, Any] = {
        "id": provider_id,
        "name": desired.name,
        "labels": labels,
        "protection": protection,
        "public_net": {"ipv4": {"ip": f"192.0.2.{provider_id % 200 + 1}"}},
    }
    return result


class FakeTransport:
    def __init__(self, *, compliant: bool = False) -> None:
        self.resources: dict[str, list[dict[str, Any]]] = {
            "servers": [],
            "volumes": [],
            "networks": [],
        }
        for offset, desired in enumerate(fleet.DESIRED_RESOURCES, start=1):
            self.resources[desired.collection].append(
                _resource(desired, 900_000 + offset, compliant=compliant)
            )
        self.calls: list[tuple[str, str, dict[str, Any] | None]] = []
        self.action_status = "success"
        self.mutate = True

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.calls.append((method, path, copy.deepcopy(body)))
        parsed = urllib.parse.urlsplit(path)
        parts = parsed.path.strip("/").split("/")
        collection = parts[0]
        if method == "GET" and collection == "actions":
            return {"action": {"id": int(parts[1]), "status": self.action_status}}
        if method == "GET" and len(parts) == 1:
            name = urllib.parse.parse_qs(parsed.query).get("name", [""])[0]
            matches = [
                copy.deepcopy(item) for item in self.resources[collection] if item["name"] == name
            ]
            return {
                collection: matches,
                "meta": {"pagination": {"next_page": None}},
            }
        provider_id = int(parts[1])
        resource = next(item for item in self.resources[collection] if item["id"] == provider_id)
        if method == "PUT" and len(parts) == 2:
            assert body is not None
            if self.mutate:
                resource["labels"] = copy.deepcopy(body["labels"])
            return {collection[:-1]: copy.deepcopy(resource)}
        if method == "POST" and parts[2:] == ["actions", "change_protection"]:
            assert body is not None
            if self.mutate and self.action_status == "success":
                resource["protection"].update(copy.deepcopy(body))
            return {"action": {"id": provider_id + 100_000, "status": "running"}}
        raise AssertionError(f"unexpected fake request: {method} {path}")


def _mutations(fake: FakeTransport) -> list[tuple[str, str, dict[str, Any] | None]]:
    return [call for call in fake.calls if call[0] in {"PUT", "POST"}]


def test_dry_run_reports_all_drift_without_mutating() -> None:
    transport = FakeTransport()

    states = fleet.discover(transport)
    evidence = fleet.summary(states, mode="dry-run")

    assert evidence["compliant"] is False
    assert evidence["resource_count"] == 7
    assert _mutations(transport) == []
    assert all(
        item["planned_actions"] == ["merge_managed_labels", "enable_lifecycle_protection"]
        for item in evidence["resources"]
    )


def test_apply_uses_exact_endpoints_payloads_and_preserves_unmanaged_labels() -> None:
    transport = FakeTransport()
    initial = fleet.discover(transport)

    verified = fleet.apply(
        transport,
        initial,
        action_attempts=2,
        action_interval_seconds=0,
    )

    assert all(state.compliant for state in verified)
    mutations = _mutations(transport)
    assert len(mutations) == 14
    for offset, desired in enumerate(fleet.DESIRED_RESOURCES, start=1):
        provider_id = 900_000 + offset
        assert (
            "PUT",
            f"/{desired.collection}/{provider_id}",
            {
                "labels": {
                    "legacy": "preserve-me",
                    **desired.labels,
                }
            },
        ) in mutations
        assert (
            "POST",
            f"/{desired.collection}/{provider_id}/actions/change_protection",
            desired.protection,
        ) in mutations


def test_apply_changes_only_drifted_fields() -> None:
    transport = FakeTransport(compliant=True)
    first = transport.resources["servers"][0]
    first["protection"]["delete"] = False

    verified = fleet.apply(
        transport,
        fleet.discover(transport),
        action_attempts=2,
        action_interval_seconds=0,
    )

    assert all(state.compliant for state in verified)
    assert _mutations(transport) == [
        (
            "POST",
            "/servers/900001/actions/change_protection",
            {"delete": True, "rebuild": True},
        )
    ]


@pytest.mark.parametrize("problem", ["missing", "duplicate"])
def test_missing_or_duplicate_resource_fails_before_any_mutation(problem: str) -> None:
    transport = FakeTransport()
    desired = fleet.DESIRED_RESOURCES[-1]
    resources = transport.resources[desired.collection]
    matching = next(item for item in resources if item["name"] == desired.name)
    if problem == "missing":
        resources.remove(matching)
    else:
        duplicate = copy.deepcopy(matching)
        duplicate["id"] += 50_000
        resources.append(duplicate)

    with pytest.raises(fleet.FleetError, match="expected exactly one"):
        states = fleet.discover(transport)
        fleet.apply(transport, states, action_interval_seconds=0)

    assert _mutations(transport) == []


def test_failed_action_stops_and_reports_no_raw_action_identifier() -> None:
    transport = FakeTransport(compliant=True)
    transport.resources["servers"][0]["protection"]["delete"] = False
    transport.action_status = "error"

    with pytest.raises(fleet.FleetError) as caught:
        fleet.apply(
            transport,
            fleet.discover(transport),
            action_attempts=2,
            action_interval_seconds=0,
        )

    message = str(caught.value)
    assert message == "Hetzner lifecycle protection action failed"
    assert "1000001" not in message
    assert any(call[0] == "GET" and call[1] == "/actions/1000001" for call in transport.calls)


def test_action_polling_is_bounded() -> None:
    transport = FakeTransport(compliant=True)
    transport.resources["servers"][0]["protection"]["delete"] = False
    transport.action_status = "running"

    with pytest.raises(fleet.FleetError, match="action timed out"):
        fleet.apply(
            transport,
            fleet.discover(transport),
            action_attempts=2,
            action_interval_seconds=0,
        )

    polls = [call for call in transport.calls if call[:2] == ("GET", "/actions/1000001")]
    assert len(polls) == 2


def test_post_apply_reread_must_prove_every_field() -> None:
    transport = FakeTransport()
    transport.mutate = False

    with pytest.raises(fleet.FleetError, match="post-apply lifecycle verification failed"):
        fleet.apply(
            transport,
            fleet.discover(transport),
            action_attempts=2,
            action_interval_seconds=0,
        )


def test_summary_never_exposes_provider_ids_addresses_tokens_or_unmanaged_labels() -> None:
    transport = FakeTransport()
    rendered = json.dumps(fleet.summary(fleet.discover(transport), mode="dry-run"))

    for provider_id in range(900_001, 900_008):
        assert str(provider_id) not in rendered
    assert "192.0.2." not in rendered
    assert "very-secret-token" not in rendered
    assert "preserve-me" not in rendered
    assert "previous-owner" not in rendered


def test_numeric_provider_paths_are_redacted_in_transport_errors() -> None:
    assert (
        fleet._redacted_api_path("/servers/900001/actions/change_protection")
        == "/servers/{id}/actions/change_protection"
    )
    assert fleet._redacted_api_path("/actions/1000001") == "/actions/{id}"


def test_command_is_dry_run_unless_apply_is_explicit() -> None:
    assert fleet._parser().parse_args([]).apply is False
    assert fleet._parser().parse_args(["--apply"]).apply is True
