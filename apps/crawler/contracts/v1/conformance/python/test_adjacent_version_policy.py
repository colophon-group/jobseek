from __future__ import annotations

import importlib.util
import json
import shutil
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

V1 = Path(__file__).resolve().parents[2]
POLICY = V1 / "fixtures" / "compatibility" / "adjacent_version_policy"

_SPEC = importlib.util.spec_from_file_location(
    "runtime_v1_adjacent_policy_checker", V1 / "tools" / "check_compatibility.py"
)
assert _SPEC is not None and _SPEC.loader is not None
compatibility = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = compatibility
_SPEC.loader.exec_module(compatibility)


def _write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_policy(tmp_path: Path) -> Path:
    destination = tmp_path / "adjacent_version_policy"
    shutil.copytree(POLICY, destination)
    return destination


def _mutate_json(
    directory: Path,
    filename: str,
    mutation: Callable[[dict[str, Any]], None],
) -> None:
    path = directory / filename
    value = json.loads(path.read_text(encoding="utf-8"))
    mutation(value)
    _write_json(path, value)


def _manifest_production_true(value: dict[str, Any]) -> None:
    value["production"] = True


def _manifest_production_missing(value: dict[str, Any]) -> None:
    value.pop("production")


def _manifest_wrong_package(value: dict[str, Any]) -> None:
    value["to"]["package"] = "jobseek.crawler.runtime.v18"


def _manifest_nonadjacent(value: dict[str, Any]) -> None:
    value["to"]["version"] = 19
    value["to"]["package"] = "jobseek.crawler.runtime.policytest.v19"


def _manifest_missing_go(value: dict[str, Any]) -> None:
    value["converters"].pop("go")


def _manifest_one_way(value: dict[str, Any]) -> None:
    value["converters"]["python"]["directions"] = ["old_to_new"]


def _manifest_missing_runner(value: dict[str, Any]) -> None:
    value["converters"]["go"]["path"] = "not_executed.go"


def _mutate_proto(directory: Path, old: str, new: str) -> None:
    path = directory / "v18.proto"
    source = path.read_text(encoding="utf-8")
    assert source.count(old) == 1, old
    path.write_text(source.replace(old, new, 1), encoding="utf-8")


def _empty_corpus(value: dict[str, Any]) -> None:
    value["cases"] = []


def _identity_only_corpus(value: dict[str, Any]) -> None:
    value["cases"] = [case for case in value["cases"] if case["reversible"]]


def _false_loss(value: dict[str, Any]) -> None:
    case = value["cases"][0]
    case["expected"]["losses"] = [{"path": "$.legacy_note", "reason": "fabricated loss"}]
    case["reversible"] = False


def _case(value: dict[str, Any], case_id: str) -> dict[str, Any]:
    return next(case for case in value["cases"] if case["id"] == case_id)


def _make_case_reversible(value: dict[str, Any], case_id: str) -> None:
    case = _case(value, case_id)
    for loss in case["expected"]["losses"]:
        field = loss["path"].removeprefix("$.")
        case["expected"]["payload"][field] = case["input"][field]
    case["expected"]["losses"] = []
    case["reversible"] = True


def _configure_additive_only(directory: Path) -> None:
    _mutate_proto(
        directory,
        """  reserved 2;
  reserved "POLICY_MODE_RETIRED";

  POLICY_MODE_UNSPECIFIED = 0;
  POLICY_MODE_READY = 1;
  POLICY_MODE_ACTIVE = 1;""",
        """  POLICY_MODE_UNSPECIFIED = 0;
  POLICY_MODE_READY = 1;
  POLICY_MODE_ACTIVE = 1;
  POLICY_MODE_RETIRED = 2;""",
    )
    _mutate_proto(
        directory,
        """  reserved 6;
  reserved "legacy_note";
""",
        "  optional string legacy_note = 6;\n",
    )
    _mutate_json(
        directory,
        "vectors.json",
        lambda value: _make_case_reversible(value, "old-genuine-loss"),
    )


def _configure_removal_only(directory: Path) -> None:
    _mutate_proto(directory, "  optional string future_hint = 9;\n", "")
    _mutate_json(
        directory,
        "vectors.json",
        lambda value: _make_case_reversible(value, "new-genuine-loss"),
    )


def _configure_delta(directory: Path, delta: str) -> None:
    if delta == "additive":
        _configure_additive_only(directory)
    elif delta == "removal":
        _configure_removal_only(directory)
    else:
        assert delta == "mixed"


_VALIDATION_FAILURES = [
    ("manifest.json", _manifest_production_true, "production must be exactly false"),
    ("manifest.json", _manifest_production_missing, "unexpected or missing keys"),
    ("manifest.json", _manifest_wrong_package, "package is not test-only"),
    ("manifest.json", _manifest_missing_go, "requires Python and Go"),
    ("manifest.json", _manifest_one_way, "declare both directions"),
    ("manifest.json", _manifest_missing_runner, "regular fixture file"),
    ("vectors.json", _empty_corpus, "corpus must be nonempty"),
    ("vectors.json", _identity_only_corpus, "does not match the descriptor delta"),
    ("vectors.json", _false_loss, "loss is false"),
]

_PROTO_FAILURES = [
    ("  reserved 6;\n", "", "removed field must reserve both name and number"),
    ('  reserved "legacy_note";\n', "", "removed field must reserve both name and number"),
    ("  optional string future_hint = 9;", "  optional string future_hint = 6;", "reserved"),
    (
        '  reserved "legacy_note";',
        '  reserved "legacy_note";\n  optional string legacy_note = 11;',
        "reserved",
    ),
    ("map<string, string> labels", "map<uint64, string> labels", "field or map shape changed"),
    ("map<string, string> labels", "map<string, uint64> labels", "field or map shape changed"),
    ("oneof source {", "oneof origin {", "oneof identity or membership changed"),
    (
        """  oneof source {
    string source_url = 4;
    string inline_source = 5;
  }""",
        """  oneof source {
    string source_url = 4;
  }
  string inline_source = 5;""",
        "oneof identity or membership changed",
    ),
    ("  reserved 2;\n", "", "removed enum value must reserve both name and number"),
    (
        '  reserved "POLICY_MODE_RETIRED";\n',
        "",
        "removed enum value must reserve both name and number",
    ),
    (
        "  POLICY_MODE_ACTIVE = 1;",
        "  POLICY_MODE_ACTIVE = 1;\n  POLICY_MODE_REPLACEMENT = 2;",
        "reserved",
    ),
    ("  option allow_alias = true;\n\n", "", "uses the same enum value"),
    (
        "  POLICY_MODE_ACTIVE = 1;",
        "  POLICY_MODE_ACTIVE = 3;\n  POLICY_MODE_ENABLED = 1;",
        "enum alias or value changed",
    ),
]


def test_adjacent_version_policy_executes_python_go_and_mixed_round_trips() -> None:
    compatibility.check_adjacent_version_policy(V1)


def test_specimen_is_explicitly_nonproduction_and_does_not_claim_v2() -> None:
    manifest = json.loads((POLICY / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["production"] is False
    assert manifest["fixture_only"] is True
    assert set(manifest["converters"]) == {"python", "go"}
    assert all(".policytest.v" in manifest[endpoint]["package"] for endpoint in ("from", "to"))
    assert not (V1.parent / "v2").exists()


@pytest.mark.parametrize(
    ("filename", "mutation", "expected"),
    _VALIDATION_FAILURES,
    ids=[mutation.__name__.removeprefix("_") for _, mutation, _ in _VALIDATION_FAILURES],
)
def test_policy_validation_fails_closed(
    tmp_path: Path,
    filename: str,
    mutation: Callable[[dict[str, Any]], None],
    expected: str,
) -> None:
    directory = _copy_policy(tmp_path)
    _mutate_json(directory, filename, mutation)
    with pytest.raises(compatibility.CompatibilityError, match=expected):
        compatibility.validate_adjacent_version_policy(directory)


@pytest.mark.parametrize(
    ("old", "new", "expected"),
    _PROTO_FAILURES,
    ids=[f"proto-policy-{index}" for index in range(len(_PROTO_FAILURES))],
)
def test_protobuf_reservation_and_identity_policy_fails_closed(
    tmp_path: Path, old: str, new: str, expected: str
) -> None:
    directory = _copy_policy(tmp_path)
    _mutate_proto(directory, old, new)
    with pytest.raises(compatibility.CompatibilityError, match=expected):
        compatibility.validate_adjacent_version_policy(directory)


def test_nonadjacent_version_evidence_fails(tmp_path: Path) -> None:
    directory = _copy_policy(tmp_path)
    _mutate_json(directory, "manifest.json", _manifest_nonadjacent)
    with pytest.raises(compatibility.CompatibilityError, match="not numerically adjacent"):
        compatibility.validate_adjacent_version_policy(directory)


@pytest.mark.parametrize("delta", ["additive", "removal", "mixed"])
def test_descriptor_delta_accepts_its_required_loss_directions(tmp_path: Path, delta: str) -> None:
    directory = _copy_policy(tmp_path)
    _configure_delta(directory, delta)
    compatibility.validate_adjacent_version_policy(directory)


@pytest.mark.parametrize(
    ("delta", "missing_case"),
    [
        ("additive", "new-genuine-loss"),
        ("removal", "old-genuine-loss"),
        ("mixed", "old-genuine-loss"),
    ],
)
def test_descriptor_delta_requires_each_genuine_loss_direction(
    tmp_path: Path, delta: str, missing_case: str
) -> None:
    directory = _copy_policy(tmp_path)
    _configure_delta(directory, delta)
    _mutate_json(
        directory,
        "vectors.json",
        lambda value: _make_case_reversible(value, missing_case),
    )
    with pytest.raises(
        compatibility.CompatibilityError,
        match="lossy evidence does not match the descriptor delta",
    ):
        compatibility.validate_adjacent_version_policy(directory)


def _replace_python_runner(directory: Path, body: str) -> None:
    (directory / "python_converter.py").write_text(body, encoding="utf-8")


def test_nondeterministic_converter_output_fails(tmp_path: Path) -> None:
    directory = _copy_policy(tmp_path)
    _replace_python_runner(
        directory,
        """import json, os, sys
document = json.load(sys.stdin)
results = [
    {"id": case["id"], "losses": [], "payload": {"nonce": os.urandom(8).hex()}}
    for case in document["cases"]
]
sys.stdout.write(json.dumps({"results": results}, separators=(",", ":"), sort_keys=True) + "\\n")
""",
    )
    with pytest.raises(compatibility.CompatibilityError, match="nondeterministic"):
        compatibility.check_adjacent_version_policy(V1, policy_directory=directory)


@pytest.mark.parametrize("behavior", ["echo", "constant"])
def test_identity_or_constant_converter_output_fails(tmp_path: Path, behavior: str) -> None:
    directory = _copy_policy(tmp_path)
    payload_expression = "case['payload']" if behavior == "echo" else "{'constant': True}"
    _replace_python_runner(
        directory,
        f"""import json, sys
document = json.load(sys.stdin)
results = [
    {{"id": case["id"], "losses": [], "payload": {payload_expression}}}
    for case in document["cases"]
]
sys.stdout.write(json.dumps({{"results": results}}, separators=(",", ":"), sort_keys=True) + "\\n")
""",
    )
    with pytest.raises(compatibility.CompatibilityError, match="disagrees with shared vectors"):
        compatibility.check_adjacent_version_policy(V1, policy_directory=directory)
