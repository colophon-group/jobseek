from __future__ import annotations

import csv
import json
from collections.abc import Mapping
from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from src.core.scrapers import scraper_needs_browser
from src.inspect import validate_csvs
from src.lightpanda.census import CensusError, build_manifest
from src.lightpanda.routing import (
    RenderAssignmentError,
    has_render_assignment,
    resolve_render_assignment,
)
from src.shared.constants import get_data_dir

_COLUMNS = (
    "company_slug",
    "board_slug",
    "board_url",
    "monitor_type",
    "monitor_config",
    "scraper_type",
    "scraper_config",
)


def _valid_config() -> dict[str, object]:
    return {
        "browser_backend": "lightpanda",
        "defaults": {
            "locations": ["Zurich, Switzerland"],
            "metadata": {"source": {"kind": "structured-data"}},
        },
        "defaults_by_url": {"https://example.test/jobs/1": {"employment_type": "full_time"}},
        "enrich": ["description", "locations"],
        "ignore_address_region": False,
        "ignore_date_posted": True,
        "ignore_locations": False,
        "ignore_valid_through": True,
        "render": True,
        "routing_revision": "route-2026.09.10+canary",
        "timeout": 30_000,
        "wait": "load",
        "wait_fallback": None,
    }


def _write_boards(path: Path, configs: list[dict[str, object]]) -> Path:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=_COLUMNS)
        writer.writeheader()
        for index, config in enumerate(configs):
            writer.writerow(
                {
                    "company_slug": "test",
                    "board_slug": f"test-careers-{index}",
                    "board_url": f"https://example.test/boards/{index}",
                    "monitor_type": "greenhouse",
                    "monitor_config": "{}",
                    "scraper_type": "json-ld",
                    "scraper_config": json.dumps(config, separators=(",", ":")),
                }
            )
    return path


def _write_inspect_data(path: Path, config: dict[str, object]) -> None:
    (path / "companies.csv").write_text(
        "slug,name,website\ntest,Test,https://example.test\n",
        encoding="utf-8",
    )
    _write_boards(path / "boards.csv", [config])


def test_exact_assignment_is_bound_as_a_deep_immutable_snapshot() -> None:
    config = _valid_config()

    assignment = resolve_render_assignment("json-ld", config)

    assert assignment is not None
    assert assignment.browser_backend == "lightpanda"
    assert assignment.routing_revision == "route-2026.09.10+canary"
    assert assignment.scraper_step == 0
    assert assignment.timeout_ms == 30_000
    assert len(assignment.config_digest_sha256) == 64
    assert not hasattr(assignment, "authorized")
    assert not hasattr(assignment, "service_lane")
    assert not hasattr(assignment, "queue")
    defaults = assignment.config["defaults"]
    assert isinstance(defaults, Mapping)
    assert defaults["locations"] == ("Zurich, Switzerland",)
    metadata = defaults["metadata"]
    assert isinstance(metadata, Mapping)
    source = metadata["source"]
    assert isinstance(source, Mapping)
    assert source["kind"] == "structured-data"

    config["routing_revision"] = "mutated"
    config["defaults"]["locations"].append("Mutation")  # type: ignore[index,union-attr]
    config["defaults"]["metadata"]["source"]["kind"] = "mutated"  # type: ignore[index]

    assert assignment.routing_revision == "route-2026.09.10+canary"
    assert assignment.config["routing_revision"] == "route-2026.09.10+canary"
    assert defaults["locations"] == ("Zurich, Switzerland",)
    assert source["kind"] == "structured-data"
    with pytest.raises(TypeError):
        assignment.config["routing_revision"] = "route-2"  # type: ignore[index]
    with pytest.raises(TypeError):
        defaults["locations"] = ()  # type: ignore[index]
    with pytest.raises(FrozenInstanceError):
        assignment.routing_revision = "route-2"  # type: ignore[misc]


def test_binding_is_deterministic_and_preserves_browser_queue_classification() -> None:
    first = _valid_config()
    second = dict(reversed(first.items()))

    first_assignment = resolve_render_assignment("json-ld", first)
    second_assignment = resolve_render_assignment("json-ld", second)

    assert first_assignment is not None
    assert second_assignment is not None
    assert first_assignment.config_digest_sha256 == second_assignment.config_digest_sha256
    assert scraper_needs_browser("json-ld", first) is True


def test_unassigned_configs_remain_outside_the_inactive_contract() -> None:
    config = {"render": True, "proxy": True, "actions": [{"action": "click"}]}

    assert has_render_assignment(config) is False
    assert resolve_render_assignment("json-ld", config) is None


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"browser_backend": "chromium"}, "browser_backend"),
        ({"browser_backend": None}, "browser_backend"),
        ({"routing_revision": "route/unsafe"}, "routing_revision"),
        ({"routing_revision": ""}, "routing_revision"),
        ({"routing_revision": "r" * 65}, "routing_revision"),
        ({"routing_revision": None}, "routing_revision"),
        ({"render": False}, "render must be true"),
        ({"render": 1}, "render must be true"),
        ({"wait": "domcontentloaded"}, "wait must be 'load'"),
        ({"wait_fallback": "load"}, "wait_fallback must be explicitly null"),
        ({"timeout": True}, "timeout must be an integer"),
        ({"timeout": 0}, "timeout must be an integer"),
        ({"timeout": 120_001}, "timeout must be an integer"),
        ({"timeout": 1.5}, "timeout must be an integer"),
    ],
)
def test_assignment_rejects_bad_required_values(patch: dict[str, object], message: str) -> None:
    config = {**_valid_config(), **patch}

    with pytest.raises(RenderAssignmentError, match=message):
        resolve_render_assignment("json-ld", config)


@pytest.mark.parametrize(
    "missing",
    ["browser_backend", "render", "routing_revision", "wait", "wait_fallback", "timeout"],
)
def test_assignment_rejects_missing_required_shape(missing: str) -> None:
    config = _valid_config()
    config.pop(missing)

    with pytest.raises(RenderAssignmentError, match=f"missing required keys:.*{missing}"):
        resolve_render_assignment("json-ld", config)


@pytest.mark.parametrize(
    "key",
    [
        "actions",
        "browser_expression",
        "channel",
        "fallback",
        "headers",
        "interception",
        "persistent_context",
        "proxy",
        "request_headers",
        "resource_policy",
        "session",
        "skip_ssl",
        "stealth",
    ],
)
def test_assignment_rejects_browser_authority_and_unknown_keys(key: str) -> None:
    config = {**_valid_config(), key: True}

    with pytest.raises(RenderAssignmentError, match=f"forbidden or unknown keys:.*{key}"):
        resolve_render_assignment("json-ld", config)


def test_backend_and_revision_fail_independently_when_other_fields_are_absent() -> None:
    with pytest.raises(RenderAssignmentError, match="browser_backend"):
        resolve_render_assignment("json-ld", {"browser_backend": "chromium"})
    with pytest.raises(RenderAssignmentError, match="routing_revision"):
        resolve_render_assignment("json-ld", {"routing_revision": "route/unsafe"})


def test_assignment_rejects_non_json_and_cyclic_nested_values() -> None:
    non_json = _valid_config()
    non_json["defaults"] = {"metadata": {"opaque": object()}}
    with pytest.raises(RenderAssignmentError, match="finite JSON"):
        resolve_render_assignment("json-ld", non_json)

    non_finite = _valid_config()
    non_finite["defaults"] = {"metadata": {"value": float("nan")}}
    with pytest.raises(RenderAssignmentError, match="finite JSON values"):
        resolve_render_assignment("json-ld", non_finite)

    cycle: dict[str, object] = {}
    cycle["self"] = cycle
    cyclic = _valid_config()
    cyclic["defaults"] = {"metadata": cycle}
    with pytest.raises(RenderAssignmentError, match="finite JSON"):
        resolve_render_assignment("json-ld", cyclic)

    non_string_key = _valid_config()
    non_string_key["defaults"] = {"metadata": {1: "integer-key"}}
    with pytest.raises(RenderAssignmentError, match="mapping keys must be strings"):
        resolve_render_assignment("json-ld", non_string_key)

    string_key = _valid_config()
    string_key["defaults"] = {"metadata": {"1": "string-key"}}
    assert resolve_render_assignment("json-ld", string_key) is not None

    list_value = _valid_config()
    list_value["defaults"] = {"locations": ["Zurich, Switzerland"]}
    assert resolve_render_assignment("json-ld", list_value) is not None
    tuple_value = _valid_config()
    tuple_value["defaults"] = {"locations": ("Zurich, Switzerland",)}
    with pytest.raises(RenderAssignmentError, match="only finite JSON values"):
        resolve_render_assignment("json-ld", tuple_value)


@pytest.mark.parametrize(
    ("patch", "message"),
    [
        ({"ignore_locations": 1}, "ignore_locations must be boolean"),
        ({"defaults": []}, "defaults must be an object"),
        ({"defaults": {"unknown": "value"}}, "defaults has unknown fields"),
        ({"defaults_by_url": []}, "defaults_by_url must be an object"),
        ({"defaults_by_url": {"https://example.test": []}}, "map URL strings to objects"),
        (
            {"defaults_by_url": {"https://example.test": {"unknown": "value"}}},
            "unknown fields",
        ),
        ({"enrich": "description"}, "enrich must be a list"),
        ({"enrich": ["unknown"]}, "enrich has unknown field"),
    ],
)
def test_assignment_validates_parser_only_fields(patch: dict[str, object], message: str) -> None:
    config = {**_valid_config(), **patch}

    with pytest.raises(RenderAssignmentError, match=message):
        resolve_render_assignment("json-ld", config)


def test_assignment_rejects_non_jsonld_and_chained_scraper_steps() -> None:
    config = _valid_config()

    with pytest.raises(RenderAssignmentError, match="type 'json-ld'"):
        resolve_render_assignment("dom", config)
    for step in (False, 0.0, 1):
        with pytest.raises(RenderAssignmentError, match="only valid at scraper step 0"):
            resolve_render_assignment("json-ld", config, scraper_step=step)  # type: ignore[arg-type]


def test_assignment_binding_performs_no_environment_filesystem_or_origin_io(monkeypatch) -> None:
    def unexpected(*_args, **_kwargs):
        raise AssertionError("unexpected I/O")

    monkeypatch.setattr("builtins.open", unexpected)
    monkeypatch.setattr("os.getenv", unexpected)
    monkeypatch.setattr("socket.socket", unexpected)

    assert resolve_render_assignment("json-ld", _valid_config()) is not None


def test_inspect_and_census_use_the_same_assignment_validator(tmp_path: Path, monkeypatch) -> None:
    valid = _valid_config()
    invalid = {**valid, "proxy": True}

    valid_dir = tmp_path / "valid"
    valid_dir.mkdir()
    _write_inspect_data(valid_dir, valid)
    monkeypatch.setattr("src.inspect.get_data_dir", lambda: valid_dir)
    assert validate_csvs() == []
    valid_manifest = build_manifest(valid_dir / "boards.csv")
    assert valid_manifest["summary"]["browser_board_count"] == 1
    assert valid_manifest["summary"]["browser_required_step_count"] == 1

    invalid_dir = tmp_path / "invalid"
    invalid_dir.mkdir()
    _write_inspect_data(invalid_dir, invalid)
    monkeypatch.setattr("src.inspect.get_data_dir", lambda: invalid_dir)
    errors = validate_csvs()
    assert any("Invalid Lightpanda render assignment" in str(error) for error in errors)
    with pytest.raises(CensusError, match="forbidden or unknown keys: proxy"):
        build_manifest(invalid_dir / "boards.csv")


def test_census_classifies_routing_literals_without_disclosing_parser_values(
    tmp_path: Path,
) -> None:
    first = _valid_config()
    first["defaults"] = {"metadata": {"secret": "must-not-appear"}}
    second = _valid_config()
    second["routing_revision"] = "route-2"
    boards = _write_boards(tmp_path / "boards.csv", [first, second])

    manifest = build_manifest(boards)
    configured = [
        record
        for record in manifest["records"]
        if record["profile_kind"] == "configured" and record["surface"] == "scraper"
    ]

    assert len(configured) == 2
    assert len({record["config_shape_sha256"] for record in configured}) == 2
    assert "must-not-appear" not in json.dumps(manifest)


def test_committed_boards_have_only_fixed_b0_render_assignments() -> None:
    with (get_data_dir() / "boards.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))

    assigned = []
    for row in rows:
        config = json.loads(row.get("scraper_config") or "{}")
        while isinstance(config, dict):
            if has_render_assignment(config):
                assigned.append(row["board_slug"])
            fallback = config.get("fallback")
            config = fallback.get("config") if isinstance(fallback, dict) else None

    assert assigned == [
        "browser-use-careers",
        "eclypsium-careers",
        "kandou-ai-careers",
        "poke-and-wiggle-careers",
    ]
