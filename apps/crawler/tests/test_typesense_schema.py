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

from src.typesense_schema import (
    COLLECTIONS,
    _index_drift,
    _patch_missing_fields,
    _warn_field_drift,
)


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


# ---------------------------------------------------------------------------
# COLLECTIONS schema invariants — guard the bug we just fixed in #2931 from
# silently regressing if a future PR adds back `index: false` to a slug field
# that callers filter on.
# ---------------------------------------------------------------------------


def _company_field(name: str) -> dict:
    company = next(c for c in COLLECTIONS if c["name"] == "company")
    return next(f for f in company["fields"] if f["name"] == name)


def test_company_slug_is_indexed() -> None:
    """`apps/web/src/lib/actions/company.ts::_fetchCompanyBySlugFromTypesense`
    issues `filter_by: slug:=<slug>` for every company-detail page render.
    Typesense rejects filter clauses on non-indexed fields with
    "Cannot filter on non-indexed field", so flipping this back to
    `index: false` would re-introduce #2931 (every company lookup falls
    through to Postgres)."""
    field = _company_field("slug")
    # `index: true` is Typesense's default — accept either explicit True or
    # the omitted-key form. Reject explicit False.
    assert field.get("index", True) is True


def test_company_slug_field_type_is_string() -> None:
    """Belt + braces: the filter `slug:=<slug>` only works against a string
    field. Cheap sanity to catch a clumsy refactor that retypes it."""
    assert _company_field("slug")["type"] == "string"


# ---------------------------------------------------------------------------
# _index_drift — pure comparator, default-aware.
# ---------------------------------------------------------------------------


def test_index_drift_both_explicit_true_silent() -> None:
    assert _index_drift({"index": True}, {"index": True}) is False


def test_index_drift_both_explicit_false_silent() -> None:
    assert _index_drift({"index": False}, {"index": False}) is False


def test_index_drift_live_false_desired_true_via_default() -> None:
    """The bug case for #2931: live cluster has `index: false`, the desired
    schema omits the key (so it defaults to True). Drift should fire so the
    patcher schedules a drop+re-add."""
    assert _index_drift({"index": False}, {"name": "slug", "type": "string"}) is True


def test_index_drift_live_true_via_default_desired_explicit_true() -> None:
    """Typesense's retrieve() always carries `index` explicitly. Belt + braces
    for the unlikely future where it doesn't."""
    assert _index_drift({}, {"index": True}) is False


def test_index_drift_live_explicit_true_desired_explicit_false() -> None:
    """Operator decided to mark a field as non-filterable in the spec — the
    patcher should re-create it as `index: false`."""
    assert _index_drift({"index": True}, {"index": False}) is True


# ---------------------------------------------------------------------------
# _patch_missing_fields — auto-repairs `index` drift via drop + re-add.
# ---------------------------------------------------------------------------


def test_patch_rebuilds_field_when_index_flipped_from_false_to_true() -> None:
    """The #2931 fix: a live `slug` field with `index: false` against a
    desired `index: true` (default) must be dropped and re-added in a
    single PATCH so the next deploy auto-repairs production schema."""
    client, collection = _stub_client(
        retrieve_fields=[
            {"name": "slug", "type": "string", "index": False, "facet": False},
        ],
    )
    _patch_missing_fields(
        client,
        "company",
        desired_fields=[
            {"name": "slug", "type": "string"},  # index defaults to True
        ],
    )

    collection.update.assert_called_once()
    payload = collection.update.call_args.args[0]["fields"]
    # First entry: drop. Second entry: re-add with the desired shape.
    assert payload == [
        {"name": "slug", "drop": True},
        {"name": "slug", "type": "string"},
    ]


def test_patch_no_rebuild_when_index_matches_default() -> None:
    """No drift: live `index: true` (Typesense default returned explicitly)
    matches an omitted desired `index`. Patcher must short-circuit, not
    issue a no-op PATCH (which Typesense would also reject)."""
    client, collection = _stub_client(
        retrieve_fields=[
            {"name": "slug", "type": "string", "index": True, "facet": False},
        ],
    )
    _patch_missing_fields(
        client,
        "company",
        desired_fields=[{"name": "slug", "type": "string"}],
    )
    collection.update.assert_not_called()


def test_patch_combines_index_rebuild_and_field_addition_in_one_payload() -> None:
    """A real-world deploy carrying both kinds of work — flipping `index`
    on an existing field AND adding a brand-new field — must fit into a
    single PATCH so the deploy stays atomic and we don't half-apply on
    network failure between two requests."""
    client, collection = _stub_client(
        retrieve_fields=[
            {"name": "slug", "type": "string", "index": False, "facet": False},
            {"name": "name", "type": "string", "index": True, "facet": False},
        ],
    )
    _patch_missing_fields(
        client,
        "company",
        desired_fields=[
            {"name": "id", "type": "string"},  # implicit; must be ignored
            {"name": "name", "type": "string"},  # unchanged
            {"name": "slug", "type": "string"},  # rebuild via drop + re-add
            {"name": "logo", "type": "string", "index": False, "optional": True},
        ],
    )

    collection.update.assert_called_once()
    payload = collection.update.call_args.args[0]["fields"]
    payload_names = [(f["name"], f.get("drop", False)) for f in payload]
    # Order matches the desired_fields iteration order: slug rebuild
    # (drop+add) then logo addition. `id` and `name` are skipped.
    assert payload_names == [
        ("slug", True),
        ("slug", False),
        ("logo", False),
    ]


def test_patch_skips_id_field_even_when_index_would_drift() -> None:
    """`id` is special-cased to never appear in PATCH payloads. Even if a
    future spec adds an explicit `index` to the `id` declaration, the
    patcher must not emit a drop+re-add — Typesense returns 400."""
    client, collection = _stub_client(
        retrieve_fields=[],  # `id` never appears in retrieve()['fields']
    )
    _patch_missing_fields(
        client,
        "company",
        desired_fields=[{"name": "id", "type": "string", "index": False}],
    )
    collection.update.assert_not_called()
