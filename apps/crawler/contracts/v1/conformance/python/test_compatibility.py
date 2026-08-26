from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

V1 = Path(__file__).resolve().parents[2]

_SPEC = importlib.util.spec_from_file_location(
    "runtime_v1_check_compatibility", V1 / "tools" / "check_compatibility.py"
)
assert _SPEC is not None and _SPEC.loader is not None
compatibility = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = compatibility
_SPEC.loader.exec_module(compatibility)


def _replace_once(source: str, old: str, new: str) -> str:
    assert source.count(old) == 1, old
    return source.replace(old, new, 1)


def _mutate(source: str, mutation: str) -> str:
    if mutation == "field_name":
        return _replace_once(
            source,
            "  uint32 additional_frames = 2;",
            "  uint32 additional_frame_credits = 2;",
        )
    if mutation == "field_number":
        return _replace_once(
            source,
            "  uint32 additional_frames = 2;",
            "  uint32 additional_frames = 20;",
        )
    if mutation == "field_type":
        return _replace_once(
            source,
            "  uint32 additional_frames = 2;",
            "  uint64 additional_frames = 2;",
        )
    if mutation == "field_cardinality":
        return _replace_once(
            source,
            "  repeated string supported_contract_versions = 1;",
            "  string supported_contract_versions = 1;",
        )
    if mutation == "field_presence":
        return _replace_once(
            source,
            "  optional string traceparent = 7;",
            "  string traceparent = 7;",
        )
    if mutation == "field_json_name":
        return _replace_once(
            source,
            "  uint32 additional_frames = 2;",
            '  uint32 additional_frames = 2 [json_name = "additionalFrameCredits"];',
        )
    if mutation == "field_oneof_membership":
        return _replace_once(
            source,
            """  oneof input {
    MonitorInput monitor = 10;
    ScrapeInput scrape = 11;
    BrowserExecutionInput browser = 13;
  }""",
            """  MonitorInput monitor = 10;
  oneof input {
    ScrapeInput scrape = 11;
    BrowserExecutionInput browser = 13;
  }""",
        )
    if mutation == "oneof_declaration_name":
        return _replace_once(source, "  oneof storage {", "  oneof body_storage {")
    if mutation == "enum_name":
        return _replace_once(
            source,
            "  EXECUTION_KIND_MONITOR = 1;",
            "  EXECUTION_KIND_MONITOR_JOB = 1;",
        )
    if mutation == "enum_number":
        return _replace_once(
            source,
            "  EXECUTION_KIND_MONITOR = 1;",
            "  EXECUTION_KIND_MONITOR = 4;",
        )
    if mutation == "enum_removal_unreserved":
        return _replace_once(source, "  EXECUTION_KIND_MONITOR = 1;\n", "")
    if mutation == "enum_removal_reserved":
        return _replace_once(
            source,
            "  EXECUTION_KIND_MONITOR = 1;",
            '  reserved 1;\n  reserved "EXECUTION_KIND_MONITOR";',
        )
    if mutation == "message_name":
        assert source.count("RuntimeError") > 1
        return source.replace("RuntimeError", "RuntimeFailure")
    if mutation == "package":
        return _replace_once(
            source,
            "package jobseek.crawler.runtime.v1;",
            "package jobseek.crawler.runtime.changed;",
        )
    if mutation == "dependency":
        return _replace_once(
            source,
            'syntax = "proto3";',
            'syntax = "proto3";\n\nimport "dependency.proto";',
        )
    if mutation == "file_options":
        return _replace_once(
            source,
            'option go_package = "github.com/colophon-group/jobseek/apps/crawler/'
            'contracts/v1/gen/go;runtimev1";',
            'option go_package = "example.invalid/changed;runtimev1";',
        )
    if mutation == "message_options":
        return _replace_once(
            source,
            "message WindowUpdate {",
            "message WindowUpdate {\n  option deprecated = true;",
        )
    if mutation == "field_options":
        return _replace_once(
            source,
            "  uint32 additional_frames = 2;",
            "  uint32 additional_frames = 2 [deprecated = true];",
        )
    if mutation == "enum_options":
        source = _replace_once(
            source,
            "enum ExecutionKind {",
            "enum ExecutionKind {\n  option allow_alias = true;",
        )
        return _replace_once(
            source,
            "  EXECUTION_KIND_BROWSER = 3;",
            "  EXECUTION_KIND_BROWSER = 3;\n  EXECUTION_KIND_BROWSER_ALIAS = 3;",
        )
    if mutation == "unsupported_service":
        return source + "\nservice UnsupportedRuntimeService {}\n"
    if mutation == "field_removal_unreserved":
        return _replace_once(source, "  uint64 max_retry_after_ms = 16;\n", "")
    if mutation == "field_removal_reserved":
        return _replace_once(
            source,
            "  uint64 max_retry_after_ms = 16;",
            '  reserved 16;\n  reserved "max_retry_after_ms";',
        )
    if mutation == "combined_additions":
        source = _replace_once(
            source,
            "  uint64 max_retry_after_ms = 16;",
            "  uint64 max_retry_after_ms = 16;\n  optional string additive_note = 100;",
        )
        source = _replace_once(
            source,
            "  ERROR_CODE_NAVIGATION = 17;",
            "  ERROR_CODE_NAVIGATION = 17;\n  ERROR_CODE_ADDITIVE_DIAGNOSTIC = 100;",
        )
        return _replace_once(
            source,
            "message ConformanceCase {",
            """message AdditiveMessage {
  oneof value {
    string note = 1;
  }
}

message ConformanceCase {""",
        )
    raise AssertionError(f"unknown mutation fixture: {mutation}")


def _compile_source(tmp_path: Path, source: str):
    proto = tmp_path / "runtime.proto"
    proto.write_text(source, encoding="utf-8")
    if 'import "dependency.proto";' in source:
        (tmp_path / "dependency.proto").write_text(
            'syntax = "proto3";\n',
            encoding="utf-8",
        )
    return compatibility.parse_descriptor_set(compatibility.compile_descriptor(proto))


def _baseline_shape():
    descriptor, _ = compatibility.load_frozen_baseline(V1)
    return compatibility.parse_descriptor_set(descriptor)


def test_current_descriptor_matches_frozen_introduction_and_git_base() -> None:
    compatibility.check(V1)


def test_introduction_manifest_is_pinned_to_the_reserved_base() -> None:
    _, manifest = compatibility.load_frozen_baseline(V1)
    assert manifest["introduction_base_sha"] == ("7c8556642b32ac78871cd015931b95d968e83e7d")


def test_final_v1_surface_contains_required_identity_and_rule_fields() -> None:
    shape = _baseline_shape()
    messages = compatibility._flatten_messages(shape)
    enums = compatibility._flatten_enums(shape)
    prefix = "jobseek.crawler.runtime.v1."

    required_messages = {
        "Limits",
        "ExecutionRequest",
        "FencingContext",
        "OriginOperationRef",
        "OriginOperationDeclared",
        "ExecutionFrame",
        "BrowserPlan",
        "PaginationAction",
        "ArtifactHandle",
        "ChunkManifest",
        "ProtocolTranscript",
        "CapturedExchange",
        "ProjectedEffects",
        "ProjectedTarget",
        "ReplayCase",
    }
    assert required_messages <= {
        name.removeprefix(prefix) for name in messages if name.startswith(prefix)
    }

    pagination = {field.name: field for field in messages[prefix + "PaginationAction"].fields}
    assert pagination["dynamic_origin_per_additional_page"].number == 4
    assert pagination["additional_page_origin_request_ids"].number == 5

    frame = {field.name: field for field in messages[prefix + "ExecutionFrame"].fields}
    assert frame["origin_operation_declared"].number == 17

    projection = {field.name: field for field in messages[prefix + "ProjectedEffects"].fields}
    assert {
        "request_id",
        "origin_request_id",
        "execution_kind",
        "target_url",
        "targets",
        "canonicalization_rule",
        "content_hash_rule",
    } <= projection.keys()
    target = {field.name for field in messages[prefix + "ProjectedTarget"].fields}
    assert target == {"url", "action", "content_sha256"}
    replay = {field.name for field in messages[prefix + "ReplayCase"].fields}
    assert "semantic_hash_rule" in replay
    assert prefix + "CanonicalizationRule" in enums
    assert prefix + "HashRule" in enums
    assert len(messages[prefix + "Limits"].fields) == 16


_MATRIX = {
    "breaking": [
        {"id": "field_name", "expected": "field name changed"},
        {"id": "field_number", "expected": "field number changed"},
        {"id": "field_type", "expected": "field type changed"},
        {"id": "field_cardinality", "expected": "field cardinality changed"},
        {"id": "field_presence", "expected": "field presence changed"},
        {"id": "field_json_name", "expected": "field json_name changed"},
        {"id": "field_oneof_membership", "expected": "field oneof membership changed"},
        {"id": "oneof_declaration_name", "expected": "oneof declaration changed"},
        {"id": "enum_name", "expected": "enum name changed"},
        {"id": "enum_number", "expected": "enum number changed"},
        {
            "id": "enum_removal_unreserved",
            "expected": "removed enum value must reserve both name and number",
        },
        {"id": "enum_removal_reserved", "expected": "enum removal requires v2"},
        {"id": "message_name", "expected": "baseline message was removed or renamed"},
        {"id": "package", "expected": "protobuf package changed"},
        {"id": "syntax", "expected": "protobuf syntax changed"},
        {"id": "dependency", "expected": "protobuf dependencies changed"},
        {"id": "file_options", "expected": "protobuf file options changed"},
        {"id": "message_options", "expected": "message options changed"},
        {"id": "field_options", "expected": "field options changed"},
        {"id": "enum_options", "expected": "enum options changed"},
        {"id": "unsupported_service", "expected": "service declarations are unsupported"},
        {
            "id": "field_removal_unreserved",
            "expected": "removed field must reserve both name and number",
        },
        {"id": "field_removal_reserved", "expected": "field removal requires v2"},
    ],
    "compatible": [{"id": "combined_additions"}],
}


@pytest.mark.parametrize(
    ("mutation", "expected"),
    [(case["id"], case["expected"]) for case in _MATRIX["breaking"]],
    ids=[case["id"] for case in _MATRIX["breaking"]],
)
def test_structural_mutation_matrix_rejects_breaks(
    tmp_path: Path, mutation: str, expected: str
) -> None:
    baseline = _baseline_shape()
    if mutation == "syntax":
        current = replace(baseline, syntax="proto2")
    else:
        source = _mutate((V1 / "runtime.proto").read_text(encoding="utf-8"), mutation)
        if mutation == "unsupported_service":
            with pytest.raises(compatibility.CompatibilityError, match=expected):
                _compile_source(tmp_path, source)
            return
        current = _compile_source(tmp_path, source)
    with pytest.raises(compatibility.CompatibilityError, match=expected):
        compatibility.compare_descriptors(baseline, current)


def test_structural_additions_pass(tmp_path: Path) -> None:
    source = _mutate(
        (V1 / "runtime.proto").read_text(encoding="utf-8"),
        _MATRIX["compatible"][0]["id"],
    )
    compatibility.compare_descriptors(_baseline_shape(), _compile_source(tmp_path, source))


def _commit(repository: Path, message: str) -> str:
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(["git", "commit", "-q", "-m", message], cwd=repository, check=True)
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=repository,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _candidate_repository(tmp_path: Path) -> tuple[Path, Path, str]:
    root = tmp_path / "contracts" / "v1"
    shutil.copytree(V1 / "baseline", root / "baseline")
    shutil.copy2(V1 / "runtime.proto", root / "runtime.proto")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "compatibility@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Compatibility Test"],
        cwd=tmp_path,
        check=True,
    )
    base = _commit(tmp_path, "Introduce baseline")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", base],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "switch", "-q", "-c", "candidate"],
        cwd=tmp_path,
        check=True,
    )
    return tmp_path, root, base


def test_committed_self_regenerated_baseline_cannot_authenticate_itself(
    tmp_path: Path,
) -> None:
    repository, root, _ = _candidate_repository(tmp_path)
    source = _mutate(
        (root / "runtime.proto").read_text(encoding="utf-8"),
        "field_type",
    )
    (root / "runtime.proto").write_text(source, encoding="utf-8")
    descriptor = compatibility.compile_descriptor(root / "runtime.proto")
    (root / "baseline" / "runtime-v1.descriptor.b64").write_text(
        base64.b64encode(descriptor).decode("ascii") + "\n",
        encoding="ascii",
    )
    manifest_path = root / "baseline" / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["descriptor_sha256"] = hashlib.sha256(descriptor).hexdigest()
    manifest["introduction_proto_sha256"] = hashlib.sha256(source.encode("utf-8")).hexdigest()
    manifest_path.write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )
    _commit(repository, "Regenerate changed baseline")

    with pytest.raises(compatibility.CompatibilityError, match="trusted prior-main"):
        compatibility.check(root, base_ref="HEAD")
    with pytest.raises(compatibility.CompatibilityError, match="immutable relative"):
        compatibility.check(root)


def test_prior_main_addition_cannot_later_be_removed(tmp_path: Path) -> None:
    repository, root, _ = _candidate_repository(tmp_path)
    original = (root / "runtime.proto").read_text(encoding="utf-8")
    additive = _replace_once(
        original,
        "  uint64 max_retry_after_ms = 16;",
        "  uint64 max_retry_after_ms = 16;\n  optional string later_addition = 100;",
    )
    (root / "runtime.proto").write_text(additive, encoding="utf-8")
    additive_commit = _commit(repository, "Add compatible v1 field")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", additive_commit],
        cwd=repository,
        check=True,
    )
    (root / "runtime.proto").write_text(original, encoding="utf-8")
    _commit(repository, "Remove compatible v1 field")

    with pytest.raises(
        compatibility.CompatibilityError,
        match="removed field must reserve both name and number",
    ):
        compatibility.check(root)


@pytest.mark.parametrize(
    "missing",
    ["manifest.json", "runtime-v1.descriptor.b64"],
)
def test_deleted_current_baseline_fails_closed(tmp_path: Path, missing: str) -> None:
    root = tmp_path / "contracts" / "v1"
    shutil.copytree(V1 / "baseline", root / "baseline")
    (root / "baseline" / missing).unlink()
    with pytest.raises(
        compatibility.CompatibilityError, match="invalid frozen descriptor baseline"
    ):
        compatibility.load_frozen_baseline(root)


def test_incomplete_prior_main_baseline_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "contracts" / "v1"
    shutil.copytree(V1 / "baseline", root / "baseline")
    shutil.copy2(V1 / "runtime.proto", root / "runtime.proto")
    manifest = root / "baseline" / "manifest.json"
    manifest.unlink()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "config", "user.email", "compatibility@example.invalid"],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Compatibility Test"],
        cwd=tmp_path,
        check=True,
    )
    base = _commit(tmp_path, "Commit incomplete baseline")
    subprocess.run(
        ["git", "update-ref", "refs/remotes/origin/main", base],
        cwd=tmp_path,
        check=True,
    )
    subprocess.run(
        ["git", "switch", "-q", "-c", "candidate"],
        cwd=tmp_path,
        check=True,
    )
    shutil.copy2(V1 / "baseline" / "manifest.json", manifest)
    _commit(tmp_path, "Restore manifest only on candidate")

    with pytest.raises(compatibility.CompatibilityError, match="prior v1 baseline is incomplete"):
        compatibility.check(root)


def test_github_shallow_merge_authenticates_exact_event_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=source, check=True)
    subprocess.run(
        ["git", "config", "user.email", "compatibility@example.invalid"],
        cwd=source,
        check=True,
    )
    subprocess.run(
        ["git", "config", "user.name", "Compatibility Test"],
        cwd=source,
        check=True,
    )
    (source / "state").write_text("base\n", encoding="utf-8")
    base = _commit(source, "Base")
    subprocess.run(["git", "switch", "-q", "-c", "candidate"], cwd=source, check=True)
    (source / "state").write_text("candidate\n", encoding="utf-8")
    _commit(source, "Candidate")
    subprocess.run(["git", "switch", "-q", "main"], cwd=source, check=True)
    subprocess.run(
        ["git", "merge", "-q", "--no-ff", "candidate", "-m", "Merge candidate"],
        cwd=source,
        check=True,
    )

    shallow = tmp_path / "shallow"
    subprocess.run(
        ["git", "clone", "-q", "--depth=1", f"file://{source}", str(shallow)],
        check=True,
    )
    event = tmp_path / "event.json"
    event.write_text(
        json.dumps({"pull_request": {"base": {"sha": base}}}),
        encoding="utf-8",
    )
    monkeypatch.setenv("GITHUB_ACTIONS", "true")
    monkeypatch.setenv("GITHUB_WORKSPACE", str(shallow))
    monkeypatch.setenv("GITHUB_EVENT_PATH", str(event))

    assert compatibility.resolve_base_ref(shallow) == base
    with pytest.raises(
        compatibility.CompatibilityError,
        match="requested base ref does not match the trusted prior-main commit",
    ):
        compatibility.resolve_base_ref(shallow, explicit="HEAD")
