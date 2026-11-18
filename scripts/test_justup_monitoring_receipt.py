"""Current monitoring requires a complete check on the approved scope revision."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from scripts.justup_monitoring_receipt import (
    ApprovedScope,
    monitoring_receipt,
    read_approved_scope,
)

SCOPE = ApprovedScope(UUID(int=1), UUID(int=2), UUID(int=3))
FINISHED = datetime(2026, 11, 18, 16, 0, tzinfo=UTC)


@pytest.fixture
def complete():
    return {
        "is_enabled": True,
        "board_status": "active",
        "check_interval_minutes": 60,
        "revision": SCOPE.revision,
        "tracked_since": FINISHED - timedelta(days=1),
        "config_bound": True,
        "started_at": FINISHED - timedelta(minutes=2),
        "finished_at": FINISHED,
        "next_due_at": FINISHED + timedelta(minutes=30),
        "outcome": "complete",
    }


def test_receipt_expires_at_next_check_plus_grace(complete):
    receipt = monitoring_receipt(SCOPE, FINISHED + timedelta(minutes=44), complete)
    assert receipt == {
        "schemaVersion": 1,
        "sourceVersion": "jobseek-monitoring-v1",
        "companyId": str(SCOPE.company),
        "boardId": str(SCOPE.board),
        "revision": str(SCOPE.revision),
        "observedAt": "2026-11-18T16:44:00.000Z",
        "state": "current",
        "reason": None,
        "verifiedAt": "2026-11-18T16:00:00.000Z",
        "expiresAt": "2026-11-18T16:45:00.000Z",
    }
    stale = monitoring_receipt(SCOPE, FINISHED + timedelta(minutes=45), complete)
    assert stale["state"] == "unavailable"
    assert stale["reason"] == "expired_check"
    assert stale["verifiedAt"] is None and stale["expiresAt"] is None


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"revision": UUID(int=4)}, "scope_changed"),
        ({"is_enabled": False}, "scope_inactive"),
        ({"outcome": "partial"}, "no_complete_check"),
        ({"outcome": "failed"}, "no_complete_check"),
        ({"outcome": None}, "no_complete_check"),
        ({"config_bound": False}, "no_complete_check"),
        ({"tracked_since": FINISHED}, "invalid_schedule"),
        ({"next_due_at": FINISHED + timedelta(days=2)}, "invalid_schedule"),
    ],
)
def test_unverified_scope_has_no_positive_evidence(complete, override, reason):
    receipt = monitoring_receipt(SCOPE, FINISHED, {**complete, **override})
    assert receipt["state"] == "unavailable"
    assert receipt["reason"] == reason
    assert receipt["verifiedAt"] is None and receipt["expiresAt"] is None


def test_absent_board_preserves_only_approved_identity():
    receipt = monitoring_receipt(SCOPE, FINISHED, None)
    assert receipt["state"] == "unavailable"
    assert receipt["boardId"] == str(SCOPE.board)
    assert receipt["reason"] == "scope_changed"


def test_approval_requires_exact_private_scope(tmp_path):
    approval = tmp_path.resolve() / "approval.json"
    data = {
        "schemaVersion": 1,
        "companyId": str(SCOPE.company),
        "boardId": str(SCOPE.board),
        "revision": str(SCOPE.revision),
    }
    approval.write_text(json.dumps(data))
    approval.chmod(0o600)
    assert read_approved_scope(approval) == SCOPE
    alias = approval.with_name("alias.json")
    alias.symlink_to(approval)
    with pytest.raises(ValueError):
        read_approved_scope(alias)
    approval.write_text(
        json.dumps(data).replace(
            '"schemaVersion": 1', '"schemaVersion": 1, "schemaVersion": 1'
        )
    )
    with pytest.raises(ValueError):
        read_approved_scope(approval)
    approval.write_text(json.dumps(data))
    approval.chmod(0o644)
    with pytest.raises(ValueError):
        read_approved_scope(approval)
