"""Tests for the owned Grafana-managed production paging configuration."""

from __future__ import annotations

import copy
import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "sync-grafana-alertmanager.py"
SPEC = importlib.util.spec_from_file_location("sync_grafana_alertmanager", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
paging = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(paging)


def test_merge_preserves_unowned_notification_routes() -> None:
    previous = {
        "receiver": "legacy",
        "group_by": ["alertname"],
        "routes": [
            {
                "receiver": paging.EMAIL_CONTACT,
                "object_matchers": [["page", "=", "old"]],
            },
            {"receiver": "legacy", "object_matchers": [["team", "=", "data"]]},
        ],
    }

    merged = paging.merge_owned_policy(previous)

    assert merged["receiver"] == "legacy"
    assert merged["group_by"] == ["alertname"]
    assert merged["routes"] == [
        *paging._owned_routes(),
        {"receiver": "legacy", "object_matchers": [["team", "=", "data"]]},
    ]


def test_contact_point_uses_cloud_email_and_resolved_notifications() -> None:
    contact = paging._contact_point("operator@example.com")

    assert contact == {
        "uid": paging.EMAIL_CONTACT,
        "name": paging.EMAIL_CONTACT,
        "type": "email",
        "settings": {
            "addresses": "operator@example.com",
            "singleEmail": True,
            "subject": (
                "[Jobseek production] {{ .Status | toUpper }} {{ .CommonLabels.alertname }}"
            ),
        },
        "disableResolveMessage": False,
    }


@pytest.mark.parametrize(
    "email",
    ("", "not-an-email", "a@example.com,b@example.com", "a@example.com\nBcc:x@example.com"),
)
def test_email_validation_rejects_missing_multiple_or_injected_addresses(email: str) -> None:
    with pytest.raises(paging.AlertmanagerSyncError, match="single valid"):
        paging._contact_point(email)


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


def test_owned_rules_bridge_mimir_critical_alerts_and_emit_daily_deadman() -> None:
    rules = paging._desired_rules()
    bridge = rules[paging.BRIDGE_RULE_UID]
    deadman = rules[paging.DEADMAN_RULE_UID]

    assert bridge["data"][0]["model"]["expr"] == paging.BRIDGE_EXPRESSION
    assert 'ALERTS{alertstate="firing",page="production"' in paging.BRIDGE_EXPRESSION
    assert bridge["labels"]["page"] == "production"
    assert bridge["for"] == "0s"
    assert bridge["execErrState"] == "Alerting"
    assert deadman["data"][0]["model"]["expr"] == "vector(1)"
    assert deadman["labels"]["deadman"] == "notification-route"
    assert deadman["labels"]["synthetic"] == "true"


def test_rule_rollback_payload_omits_read_only_fields() -> None:
    remote = {
        **paging._desired_rules()[paging.BRIDGE_RULE_UID],
        "id": 123,
        "updated": "2026-08-03T12:00:00Z",
        "keep_firing_for": "0s",
    }

    writable = paging._writable_rule(remote)

    assert writable is not None
    assert "id" not in writable
    assert "updated" not in writable
    assert writable["keep_firing_for"] == "0s"


def test_policy_signature_accepts_grafana_duration_and_matcher_canonicalization() -> None:
    expected = {"receiver": "empty", "routes": paging._owned_routes()}
    canonical = copy.deepcopy(expected)
    canonical["routes"][0]["repeat_interval"] = "1d"
    canonical["routes"][1]["object_matchers"].reverse()

    assert paging._owned_policy_signature(canonical) == paging._owned_policy_signature(expected)


class FakeClient:
    def __init__(
        self,
        *,
        contact=None,
        policy=None,
        folder: bool = False,
        rules=None,
        fail_verify: bool = False,
        fail_rollback: bool = False,
    ) -> None:
        self.contact_value = copy.deepcopy(contact)
        self.policy_value = copy.deepcopy(policy or {"receiver": "legacy", "routes": []})
        self.folder_value = folder
        self.rules = copy.deepcopy(rules or {})
        self.fail_verify = fail_verify
        self.fail_rollback = fail_rollback
        self.written = False

    def contact(self):
        if self.fail_verify and self.written:
            return {"uid": "unexpected"}
        return copy.deepcopy(self.contact_value)

    def put_contact(self, contact, *, exists):
        del exists
        self.contact_value = copy.deepcopy(contact)
        self.written = True

    def delete_contact(self):
        self.contact_value = None

    def policy(self):
        return copy.deepcopy(self.policy_value)

    def put_policy(self, policy):
        if self.fail_rollback and self.fail_verify and self.written:
            raise paging.AlertmanagerSyncError("injected rollback failure")
        self.policy_value = copy.deepcopy(policy)
        self.written = True

    def folder_exists(self):
        return self.folder_value

    def create_folder(self):
        self.folder_value = True
        self.written = True

    def delete_folder(self):
        self.folder_value = False

    def rule(self, uid):
        return copy.deepcopy(self.rules.get(uid))

    def put_rule(self, rule, *, exists):
        del exists
        self.rules[rule["uid"]] = copy.deepcopy(rule)
        self.written = True

    def delete_rule(self, uid):
        if self.fail_rollback and self.fail_verify and self.written:
            raise paging.AlertmanagerSyncError("injected rollback failure")
        self.rules.pop(uid, None)


def test_sync_verifies_contact_policy_folder_and_rules() -> None:
    client = FakeClient()

    paging.sync_config(client, "operator@example.com")

    assert paging._contact_signature(client.contact_value) == paging._contact_signature(
        paging._contact_point("operator@example.com")
    )
    assert paging._owned_policy_signature(client.policy_value) == paging._owned_policy_signature(
        {"routes": paging._owned_routes()}
    )
    assert client.folder_value is True
    assert set(client.rules) == set(paging.OWNED_RULE_UIDS)


def test_sync_rolls_back_existing_resources_after_verification_failure(monkeypatch) -> None:
    previous_contact = paging._contact_point("previous@example.com")
    previous_policy = {"receiver": "legacy", "routes": [{"receiver": "legacy"}]}
    previous_rules = {
        uid: {**rule, "title": f"previous-{uid}"} for uid, rule in paging._desired_rules().items()
    }
    client = FakeClient(
        contact=previous_contact,
        policy=previous_policy,
        folder=True,
        rules=previous_rules,
        fail_verify=True,
    )
    monkeypatch.setattr(paging.time, "sleep", lambda _seconds: None)

    with pytest.raises(paging.AlertmanagerSyncError, match="did not expose"):
        paging.sync_config(client, "operator@example.com")

    assert client.contact_value == previous_contact
    assert client.policy_value == previous_policy
    assert client.rules == previous_rules
    assert client.folder_value is True


def test_sync_removes_new_resources_after_verification_failure(monkeypatch) -> None:
    previous_policy = {"receiver": "legacy", "routes": []}
    client = FakeClient(policy=previous_policy, fail_verify=True)
    monkeypatch.setattr(paging.time, "sleep", lambda _seconds: None)

    with pytest.raises(paging.AlertmanagerSyncError, match="did not expose"):
        paging.sync_config(client, "operator@example.com")

    assert client.contact_value is None
    assert client.policy_value == previous_policy
    assert client.rules == {}
    assert client.folder_value is False


def test_sync_reports_rollback_failure_without_secrets(monkeypatch) -> None:
    client = FakeClient(fail_verify=True, fail_rollback=True)
    monkeypatch.setattr(paging.time, "sleep", lambda _seconds: None)

    with pytest.raises(paging.AlertmanagerSyncError) as caught:
        paging.sync_config(client, "operator@example.com")

    message = str(caught.value)
    assert "rollback also failed" in message
    assert "operator@example.com" not in message


def test_observability_workflow_uses_grafana_managed_paging_secrets() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/deploy-hetzner-observability.yml").read_text()

    assert "GRAFANA_URL: ${{ secrets.GRAFANA_URL }}" in workflow
    assert "GRAFANA_API_KEY: ${{ secrets.GRAFANA_API_KEY }}" in workflow
    assert "GRAFANA_ALERT_EMAIL: ${{ secrets.GRAFANA_ALERT_EMAIL }}" in workflow
    assert "GRAFANA_ALERTMANAGER_PASSWORD" not in workflow
    assert workflow.index("scripts/sync-grafana-alertmanager.py") < workflow.rindex(
        "scripts/sync-grafana-rules.py"
    )
