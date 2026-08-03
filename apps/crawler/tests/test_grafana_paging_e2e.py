"""Tests for the scheduled Grafana-managed paging route probe."""

from __future__ import annotations

import importlib.util
from datetime import UTC, datetime
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "test-grafana-paging.py"
SPEC = importlib.util.spec_from_file_location("test_grafana_paging_script", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
paging_test = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(paging_test)


class FakeClient:
    def __init__(self, active_states: list[bool]):
        self.active_states = iter(active_states)
        self.created = []
        self.updated = []
        self.deleted = 0

    def create(self, rule):
        self.created.append(rule)

    def update(self, rule):
        self.updated.append(rule)

    def delete(self):
        self.deleted += 1

    def active(self, _test_id):
        return next(self.active_states)


def test_synthetic_rule_waits_for_route_resolves_and_is_deleted(monkeypatch) -> None:
    client = FakeClient([False, True, True, False])
    sleeps = []
    monkeypatch.setattr(paging_test.time, "sleep", sleeps.append)
    now = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

    paging_test.run_test(client, "run-123", hold_seconds=45, now=now)

    assert len(client.created) == 1
    assert len(client.updated) == 1
    assert client.deleted == 1
    assert client.created[0]["labels"]["test_id"] == "run-123"
    assert client.created[0]["data"][0]["model"]["expr"] == ("vector(time() < bool 1785758700)")
    assert client.updated[0]["data"][0]["model"]["expr"] == "vector(0)"
    assert sleeps == [5, 45, 5]


def test_synthetic_rule_is_deleted_when_alert_never_appears(monkeypatch) -> None:
    client = FakeClient([False] * 18)
    monkeypatch.setattr(paging_test.time, "sleep", lambda _seconds: None)

    with pytest.raises(paging_test.PagingTestError, match="did not become active"):
        paging_test.run_test(
            client,
            "run-123",
            now=datetime(2026, 8, 3, tzinfo=UTC),
        )

    assert len(client.created) == 1
    assert client.updated == []
    assert client.deleted == 1


@pytest.mark.parametrize("test_id", ("", "white space", "slash/value", "x" * 129))
def test_test_id_rejects_label_injection(test_id: str) -> None:
    with pytest.raises(paging_test.PagingTestError, match="test id"):
        paging_test._test_id(test_id)


def test_rule_labels_keep_daily_review_and_independent_page() -> None:
    assert paging_test._labels("run-123") == {
        "severity": "critical",
        "service": "production-alerting",
        "owner": "codex-error-review",
        "route": "codex-daily",
        "page": "production",
        "synthetic": "true",
        "test_id": "run-123",
    }


@pytest.mark.parametrize(
    "url",
    (
        "http://grafana.example.com",
        "https://grafana.example.com/api",
        "https://grafana.example.com?token=secret",
    ),
)
def test_grafana_url_must_be_an_https_origin(url: str) -> None:
    with pytest.raises(paging_test.PagingTestError, match="HTTPS origin"):
        paging_test.GrafanaPagingClient(url, "test-only")


def test_api_key_validation_does_not_accept_newlines() -> None:
    with pytest.raises(paging_test.PagingTestError, match="API key"):
        paging_test.GrafanaPagingClient("https://grafana.example.com", "line1\nline2")


def test_scheduled_workflow_uses_grafana_managed_rule_probe() -> None:
    root = Path(__file__).resolve().parents[3]
    workflow = (root / ".github/workflows/test-production-paging.yml").read_text()

    assert "GRAFANA_URL: ${{ secrets.GRAFANA_URL }}" in workflow
    assert "GRAFANA_API_KEY: ${{ secrets.GRAFANA_API_KEY }}" in workflow
    assert "GRAFANA_ALERTMANAGER_PASSWORD" not in workflow
    assert "cron: '17 7 * * 1'" in workflow
    assert "timeout-minutes: 5" in workflow
