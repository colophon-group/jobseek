"""Regression tests for the Typesense collection setup helpers.

These exercise ``_patch_missing_fields`` directly with a stub Typesense
client, since the real one requires a running server. Coverage focuses
on two deploy-time invariants:

* The implicit ``id`` field is never PATCHed (Typesense rejects that).
* Type drift between the spec and the live cluster is surfaced as a
  warning so an operator notices and can plan a manual recovery.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

import pytest

# Same env-stub pattern as test_exporter.py — src.config requires it at import.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from src.typesense_schema import _patch_missing_fields, _warn_field_drift


def _stub_client(retrieve_fields: list[dict]):
    """Build a typesense.Client lookalike whose retrieve() returns ``fields``.

    The real client uses ``client.collections[name]`` indexing, with
    ``.retrieve()`` and ``.update()`` methods on the result. MagicMock's
    auto-attribute behaviour gives us that for free; we only need to wire
    the retrieve return value.
    """
    client = MagicMock()
    collection = client.collections.__getitem__.return_value
    collection.retrieve.return_value = {"fields": retrieve_fields}
    return client, collection


# ---------------------------------------------------------------------------
# implicit ``id`` handling
# ---------------------------------------------------------------------------


def test_patch_skips_implicit_id_field() -> None:
    """Typesense's implicit ``id`` is never returned by retrieve()['fields']
    and cannot be PATCHed. Even when the spec declares it, the patcher must
    not include ``id`` in the update payload — otherwise Typesense returns
    400 ``Field `id` cannot be altered`` and the deploy aborts mid-way."""
    client, collection = _stub_client(
        retrieve_fields=[
            {"name": "name", "type": "string"},
        ]
    )

    desired = [
        {"name": "id", "type": "string"},  # implicit; must be ignored
        {"name": "name", "type": "string"},  # already present
        {"name": "logo", "type": "string", "optional": True},  # genuinely new
    ]

    _patch_missing_fields(client, "company", desired)

    collection.update.assert_called_once()
    payload_fields = collection.update.call_args.args[0]["fields"]
    payload_names = [f["name"] for f in payload_fields]
    assert "id" not in payload_names
    assert payload_names == ["logo"]


def test_patch_skips_when_only_id_would_be_missing() -> None:
    """If ``id`` is the only field the diff would flag, the function must
    short-circuit instead of PATCHing an empty list (which Typesense would
    also reject) or — worse — PATCHing ``id``."""
    client, collection = _stub_client(
        retrieve_fields=[
            {"name": "name", "type": "string"},
            {"name": "logo", "type": "string"},
        ]
    )

    desired = [
        {"name": "id", "type": "string"},
        {"name": "name", "type": "string"},
        {"name": "logo", "type": "string"},
    ]

    _patch_missing_fields(client, "company", desired)

    collection.update.assert_not_called()


def test_patch_adds_genuinely_new_fields() -> None:
    """Sanity check: when fields are actually missing, they are PATCHed."""
    client, collection = _stub_client(
        retrieve_fields=[
            {"name": "name", "type": "string"},
        ]
    )

    desired = [
        {"name": "name", "type": "string"},
        {"name": "logo", "type": "string", "optional": True},
        {"name": "founded_year", "type": "int32", "optional": True},
    ]

    _patch_missing_fields(client, "company", desired)

    collection.update.assert_called_once()
    payload_names = [f["name"] for f in collection.update.call_args.args[0]["fields"]]
    assert sorted(payload_names) == ["founded_year", "logo"]


# ---------------------------------------------------------------------------
# _warn_field_drift — pure function over field dicts
# ---------------------------------------------------------------------------


def _drift_log(capsys: pytest.CaptureFixture) -> str:
    """Concatenate stderr + stdout — structlog's destination depends on config,
    and the test only cares whether the event surfaces *somewhere*."""
    captured = capsys.readouterr()
    return captured.err + captured.out


def test_warn_field_drift_no_overlap_silent(capsys: pytest.CaptureFixture) -> None:
    """Fields present only on one side are not drift — they are missing/extra."""
    _warn_field_drift(
        "job_posting",
        live_fields=[{"name": "title", "type": "string"}],
        desired_fields=[{"name": "company_id", "type": "string"}],
    )
    assert "field_drift" not in _drift_log(capsys)


def test_warn_field_drift_matching_types_silent(capsys: pytest.CaptureFixture) -> None:
    _warn_field_drift(
        "job_posting",
        live_fields=[{"name": "title", "type": "string", "facet": True}],
        desired_fields=[{"name": "title", "type": "string", "facet": False}],
    )
    assert "field_drift" not in _drift_log(capsys)


def test_warn_field_drift_string_to_string_array(capsys: pytest.CaptureFixture) -> None:
    _warn_field_drift(
        "job_posting",
        live_fields=[{"name": "description", "type": "string"}],
        desired_fields=[{"name": "description", "type": "string[]"}],
    )
    log = _drift_log(capsys)
    assert "field_drift" in log
    assert "job_posting" in log
    assert "description" in log
    assert "string" in log
    assert "string[]" in log


def test_warn_field_drift_int_to_int64(capsys: pytest.CaptureFixture) -> None:
    _warn_field_drift(
        "job_posting",
        live_fields=[{"name": "first_seen_at", "type": "int32"}],
        desired_fields=[{"name": "first_seen_at", "type": "int64"}],
    )
    log = _drift_log(capsys)
    assert "int32" in log
    assert "int64" in log


def test_warn_field_drift_multiple_fields_one_warning_per_drift(
    capsys: pytest.CaptureFixture,
) -> None:
    _warn_field_drift(
        "job_posting",
        live_fields=[
            {"name": "title", "type": "string"},
            {"name": "salary", "type": "int32"},
        ],
        desired_fields=[
            {"name": "title", "type": "string"},  # OK
            {"name": "salary", "type": "int64"},  # drift
        ],
    )
    log = _drift_log(capsys)
    assert log.count("field_drift") == 1
    assert "salary" in log


# ---------------------------------------------------------------------------
# _patch_missing_fields integration: drift warning fires before / alongside
# the missing-fields PATCH.
# ---------------------------------------------------------------------------


def test_patch_warns_drift_and_adds_missing_in_one_pass(
    capsys: pytest.CaptureFixture,
) -> None:
    client, collection = _stub_client(
        retrieve_fields=[
            {"name": "title", "type": "string"},  # no drift
            {"name": "salary", "type": "int32"},  # drift to int64
        ]
    )
    _patch_missing_fields(
        client,
        "job_posting",
        desired_fields=[
            {"name": "title", "type": "string"},
            {"name": "salary", "type": "int64"},
            {"name": "remote", "type": "bool"},  # missing -> add
        ],
    )

    log = _drift_log(capsys)
    assert "field_drift" in log
    assert "salary" in log

    collection.update.assert_called_once()
    added = collection.update.call_args.args[0]["fields"]
    assert [f["name"] for f in added] == ["remote"]


def test_patch_warns_drift_even_when_no_fields_to_add(
    capsys: pytest.CaptureFixture,
) -> None:
    """Drift detection is independent of the missing-fields PATCH."""
    client, collection = _stub_client(
        retrieve_fields=[{"name": "title", "type": "string"}],
    )
    _patch_missing_fields(
        client,
        "job_posting",
        desired_fields=[{"name": "title", "type": "string[]"}],
    )

    log = _drift_log(capsys)
    assert "field_drift" in log
    collection.update.assert_not_called()
