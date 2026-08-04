"""Tests for permanent removal of Jobseek's production paging resources."""

from __future__ import annotations

import copy
import importlib.util
import sys
from pathlib import Path

import httpx
import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "sync-grafana-alertmanager.py"
SPEC = importlib.util.spec_from_file_location("sync_grafana_alertmanager", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
paging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(paging)


def test_remove_policy_preserves_root_and_unowned_routes() -> None:
    unowned = {"receiver": "legacy", "object_matchers": [["team", "=", "data"]]}
    previous = {
        "receiver": "legacy",
        "group_by": ["alertname"],
        "routes": [
            {"receiver": paging.EMAIL_CONTACT, "object_matchers": [["page", "=", "old"]]},
            unowned,
        ],
    }

    removed = paging.remove_owned_policy(previous)

    assert removed == {"receiver": "legacy", "group_by": ["alertname"], "routes": [unowned]}
    assert previous["routes"][0]["receiver"] == paging.EMAIL_CONTACT


@pytest.mark.parametrize("routes", ["invalid", ["invalid"], [None]])
def test_remove_policy_rejects_malformed_routes(routes) -> None:
    with pytest.raises(paging.AlertmanagerSyncError, match="routes must be mappings"):
        paging.remove_owned_policy({"routes": routes})


@pytest.mark.parametrize(
    "url",
    (
        "http://grafana.example.com",
        "https://grafana.example.com/api",
        "https://grafana.example.com?token=secret",
        "not-a-url",
    ),
)
def test_grafana_url_is_an_https_origin(url: str) -> None:
    with pytest.raises(paging.AlertmanagerSyncError, match="HTTPS origin"):
        paging.GrafanaClient(url, "secret")


@pytest.mark.parametrize("key", ("", "line1\nline2", "line1\rline2"))
def test_grafana_api_key_validation_rejects_empty_or_injected_values(key: str) -> None:
    with pytest.raises(paging.AlertmanagerSyncError, match="API key"):
        paging.GrafanaClient("https://grafana.example.com", key)


class FakeClient:
    def __init__(self, *, contact=None, policy=None, rules=None) -> None:
        self.contact_value = copy.deepcopy(contact)
        self.policy_value = copy.deepcopy(policy or {"receiver": "legacy", "routes": []})
        self.rules = copy.deepcopy(rules or {})
        self.operations: list[tuple] = []

    def contact(self):
        return copy.deepcopy(self.contact_value)

    def delete_contact(self):
        self.operations.append(("delete_contact",))
        self.contact_value = None

    def policy(self):
        return copy.deepcopy(self.policy_value)

    def put_policy(self, policy, *, disable_provenance=True):
        self.operations.append(("put_policy", disable_provenance))
        self.policy_value = copy.deepcopy(policy)

    def rule(self, uid):
        return copy.deepcopy(self.rules.get(uid))

    def delete_rule(self, uid):
        self.operations.append(("delete_rule", uid))
        self.rules.pop(uid, None)


def test_disable_removes_owned_routes_rules_contact_and_cancelled_test_rule() -> None:
    unowned_route = {"receiver": "unrelated", "object_matchers": [["team", "=", "other"]]}
    owned_route = {"receiver": paging.EMAIL_CONTACT, "object_matchers": [["page", "=", "old"]]}
    unowned_rule = {"uid": "unrelated-rule", "title": "Keep me"}
    owned_rules = {uid: {"uid": uid} for uid in paging.PAGING_RULE_UIDS}
    client = FakeClient(
        contact={"uid": paging.EMAIL_CONTACT},
        policy={
            "receiver": "legacy",
            "routes": [unowned_route, owned_route],
            "provenance": "api",
        },
        rules={**owned_rules, "unrelated-rule": unowned_rule},
    )

    paging.disable_config(client)

    assert client.policy_value["routes"] == [unowned_route]
    assert client.policy_value["provenance"] == "api"
    assert client.contact_value is None
    assert client.rules == {"unrelated-rule": unowned_rule}
    assert client.operations == [
        ("put_policy", False),
        *(("delete_rule", uid) for uid in paging.PAGING_RULE_UIDS),
        ("delete_contact",),
    ]


def test_disable_is_idempotent_when_paging_is_absent() -> None:
    client = FakeClient()

    paging.disable_config(client)

    assert client.operations == []


def test_disable_stops_before_other_cleanup_when_routes_cannot_be_removed() -> None:
    class StuckPolicyClient(FakeClient):
        def put_policy(self, policy, *, disable_provenance=True):
            self.operations.append(("put_policy", disable_provenance))

    owned_route = {"receiver": paging.EMAIL_CONTACT}
    client = StuckPolicyClient(
        contact={"uid": paging.EMAIL_CONTACT},
        policy={"routes": [owned_route]},
        rules={paging.BRIDGE_RULE_UID: {"uid": paging.BRIDGE_RULE_UID}},
    )

    with pytest.raises(paging.AlertmanagerSyncError, match="routes remain configured"):
        paging.disable_config(client)

    assert client.operations == [("put_policy", True)] * 3


def test_disable_reports_cleanup_failure_after_unrouting() -> None:
    class FailedRuleDeleteClient(FakeClient):
        def delete_rule(self, uid):
            self.operations.append(("delete_rule", uid))
            if uid == paging.BRIDGE_RULE_UID:
                raise paging.AlertmanagerSyncError("injected failure")
            self.rules.pop(uid, None)

    client = FailedRuleDeleteClient(
        contact={"uid": paging.EMAIL_CONTACT},
        policy={"routes": [{"receiver": paging.EMAIL_CONTACT}]},
        rules={uid: {"uid": uid} for uid in paging.PAGING_RULE_UIDS},
    )

    with pytest.raises(paging.AlertmanagerSyncError, match="cleanup is incomplete") as caught:
        paging.disable_config(client)

    remaining_receivers = {route["receiver"] for route in client.policy_value["routes"]}
    assert paging.EMAIL_CONTACT not in remaining_receivers
    assert client.contact_value is None
    assert "injected failure" not in str(caught.value)
    assert "AlertmanagerSyncError" in str(caught.value)


def test_request_diagnostics_are_bounded_and_redacted(monkeypatch) -> None:
    key = "super-secret-api-key"
    email = "operator@example.com"
    client = paging.GrafanaClient("https://grafana.example.com", key)
    request = httpx.Request("PUT", "https://grafana.example.com/api/test")
    response = httpx.Response(
        409,
        json={"message": f"conflict for {email} using {key} " + ("x" * 500)},
        request=request,
    )
    monkeypatch.setattr(paging.httpx, "request", lambda *_args, **_kwargs: response)

    with pytest.raises(paging.GrafanaRequestError) as caught:
        client.request("PUT", "/api/test")

    message = str(caught.value)
    assert key not in message
    assert email not in message
    assert "[redacted]" in message
    assert "[redacted-email]" in message
    assert len(caught.value.detail) <= 300


def test_cli_rejects_the_removed_activation_mode(monkeypatch) -> None:
    monkeypatch.setattr(sys, "argv", [str(SCRIPT)])

    with pytest.raises(SystemExit, match="activation has been removed"):
        paging.main()


def test_module_contains_no_activation_implementation() -> None:
    assert not hasattr(paging, "sync_config")
    assert not hasattr(paging, "merge_owned_policy")
    assert not hasattr(paging, "_contact_point")
    assert not hasattr(paging, "_desired_rules")


def test_observability_workflow_enforces_disabled_paging_before_rule_sync() -> None:
    workflow = (ROOT / ".github/workflows/deploy-hetzner-observability.yml").read_text()

    assert not (ROOT / ".github/workflows/test-production-paging.yml").exists()
    assert not (ROOT / "scripts/test-grafana-paging.py").exists()
    assert not (ROOT / "apps/crawler/tests/test_grafana_paging_e2e.py").exists()
    assert "GRAFANA_URL: ${{ secrets.GRAFANA_URL }}" in workflow
    assert "GRAFANA_API_KEY: ${{ secrets.GRAFANA_API_KEY }}" in workflow
    assert "scripts/sync-grafana-alertmanager.py --disable" in workflow
    assert workflow.count("scripts/sync-grafana-alertmanager.py --disable") == 1
    assert "disable-paging:\n    needs: validate" in workflow
    assert "needs: [disable-paging, verify-ingestion]" in workflow
    assert "test-grafana-paging.py" not in workflow
    assert "GRAFANA_ALERT_EMAIL" not in workflow
    assert "GRAFANA_ALERTMANAGER_PASSWORD" not in workflow
    assert workflow.index("scripts/sync-grafana-alertmanager.py") < workflow.rindex(
        "scripts/sync-grafana-rules.py"
    )
