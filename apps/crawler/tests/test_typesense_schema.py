"""Regression tests for the Typesense collection setup helpers.

These exercise ``_patch_missing_fields`` directly with a stub Typesense
client, since the real one requires a running server. Coverage focuses on
the deploy-time invariants the function is meant to uphold.
"""

from __future__ import annotations

import os
from unittest.mock import MagicMock

# Same env-stub pattern as test_exporter.py — src.config requires it at import.
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")

from src.typesense_schema import _patch_missing_fields


def _stub_client(retrieve_fields: list[dict]) -> MagicMock:
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
