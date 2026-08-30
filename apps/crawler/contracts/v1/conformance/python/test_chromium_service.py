from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any

import pytest

V1 = Path(__file__).resolve().parents[2]
MANIFEST_PATH = V1 / "fixtures" / "chromium_service" / "manifest.json"
DIGEST_PATH = MANIFEST_PATH.with_name("manifest.sha256")
SCHEMA_PATH = V1 / "chromiumservice" / "config.schema.json"
BINDING_PATH = V1 / "python" / "jobseek_runtime_v1" / "runtime_pb2.py"

_SPEC = importlib.util.spec_from_file_location("runtime_v1_chromium_service_pb2", BINDING_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runtime_pb2: Any = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runtime_pb2
_SPEC.loader.exec_module(runtime_pb2)

FORMAT = "jobseek.chromium-service-boundary/v1"
IDENTITY = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$", re.ASCII)
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$", re.ASCII)
SOCKET = re.compile(r"^/run/jobseek/chromium/[0-9A-Za-z][0-9A-Za-z._-]{0,63}\.sock$", re.ASCII)

REQUIRED_CASE_IDS = (
    "accept_navigation_success",
    "accept_interaction_success",
    "accept_identity_success",
    "accept_caller_mutation_isolated",
    "accept_repeated_fresh_sessions",
    "unsupported_exact_preflight",
    "reject_null_assignment",
    "reject_lightpanda_backend",
    "reject_lightpanda_lane",
    "reject_unspecified_backend",
    "reject_unknown_backend",
    "reject_null_capability_class",
    "reject_capability_class_mismatch",
    "reject_null_service_lane",
    "reject_unknown_service_lane",
    "reject_empty_routing_revision",
    "reject_oversized_routing_revision",
    "reject_null_plan",
    "reject_empty_plan_capabilities",
    "reject_duplicate_plan_capability",
    "reject_unknown_plan_capability",
    "reject_wrong_contract_version",
    "reject_empty_target",
    "reject_origin_operation_limit",
    "reject_request_limit",
    "reject_unknown_provider_capability",
    "reject_duplicate_provider_capability",
    "reject_assignment_fingerprint_mismatch",
    "reject_error_partial_output",
    "reject_empty_provider_outcome",
    "reject_invalid_failure_pair",
    "error_open_timeout",
    "error_timeout",
    "error_cancelled",
    "error_target_lost",
    "error_session_lost",
    "error_process_crash",
    "error_protocol_failure",
    "error_resource_limit",
    "error_navigation",
    "error_cleanup_failure",
    "reject_process_down",
    "reject_pin_mismatch",
    "reject_process_unhealthy",
    "reject_process_age",
    "reject_rss_limit",
    "reject_session_leak",
    "reject_target_leak",
    "reject_pid_limit",
    "reject_file_descriptor_limit",
    "reject_socket_limit",
    "reject_pre_cancelled_context",
    "reject_after_shutdown",
    "reject_concurrency_saturation",
    "recycle_task_limit_between_tasks",
    "reject_config_unknown_field",
    "reject_config_mutable_image_tag",
    "reject_config_public_endpoint",
    "reject_config_zero_limit",
    "reject_config_root_uid",
    "reject_config_writable_root",
    "reject_config_new_privileges",
    "reject_config_retained_capabilities",
    "reject_config_seccomp",
    "reject_config_unbounded_tmpfs",
    "reject_config_secret_field",
    "reject_config_null_digest",
    "reject_open_error_with_session",
    "recycle_post_task_rss_limit",
)

CAPABILITIES = {
    "render": runtime_pb2.BROWSER_CAPABILITY_RENDER,
    "evaluate": runtime_pb2.BROWSER_CAPABILITY_EVALUATE,
    "actions": runtime_pb2.BROWSER_CAPABILITY_ACTIONS,
    "pagination": runtime_pb2.BROWSER_CAPABILITY_PAGINATION,
    "response_capture": runtime_pb2.BROWSER_CAPABILITY_RESPONSE_CAPTURE,
    "request_interception": runtime_pb2.BROWSER_CAPABILITY_REQUEST_INTERCEPTION,
    "frames": runtime_pb2.BROWSER_CAPABILITY_FRAMES,
    "persistent_session": runtime_pb2.BROWSER_CAPABILITY_PERSISTENT_SESSION,
    "headful_identity": runtime_pb2.BROWSER_CAPABILITY_HEADFUL_IDENTITY,
    "proxy": runtime_pb2.BROWSER_CAPABILITY_PROXY,
    "transport_overrides": runtime_pb2.BROWSER_CAPABILITY_TRANSPORT_OVERRIDES,
}

CONFIG_KEYS = {
    "active_session_ttl_ms",
    "backend",
    "browser_digest",
    "browser_version",
    "cdp_client_version",
    "drop_all_capabilities",
    "egress_policy_revision",
    "image_digest",
    "max_concurrency",
    "max_file_descriptors",
    "max_origin_operations",
    "max_pids",
    "max_process_age_ms",
    "max_requests",
    "max_rss_bytes",
    "max_sockets",
    "max_targets",
    "max_transfer_bytes",
    "no_new_privileges",
    "read_only_root",
    "recycle_after_tasks",
    "run_as_gid",
    "run_as_uid",
    "seccomp_profile",
    "service_lane",
    "shutdown_grace_ms",
    "socket_path",
    "writable_tmpfs_bytes",
}

LIMITS = {
    "active_session_ttl_ms": 86_400_000,
    "max_concurrency": 1_024,
    "max_file_descriptors": 1_000_000,
    "max_origin_operations": 100_000,
    "max_pids": 32_768,
    "max_process_age_ms": 604_800_000,
    "max_requests": 100_000,
    "max_rss_bytes": 68_719_476_736,
    "max_sockets": 65_535,
    "max_targets": 1_024,
    "max_transfer_bytes": 10_995_116_277_760,
    "recycle_after_tasks": 1_000_000,
    "shutdown_grace_ms": 60_000,
    "writable_tmpfs_bytes": 17_179_869_184,
}


def _manifest() -> dict[str, Any]:
    raw = MANIFEST_PATH.read_bytes()
    document = json.loads(raw)
    canonical = (
        json.dumps(document, ensure_ascii=True, separators=(",", ":"), sort_keys=True) + "\n"
    ).encode("ascii")
    assert raw == canonical
    assert DIGEST_PATH.read_text(encoding="ascii") == (
        f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n"
    )
    assert isinstance(document, dict)
    return document


def _deep_merge(base: Any, override: Any) -> Any:
    if not isinstance(base, dict) or not isinstance(override, dict):
        return copy.deepcopy(override)
    merged = copy.deepcopy(base)
    for key, value in override.items():
        merged[key] = _deep_merge(merged[key], value) if key in merged else copy.deepcopy(value)
    return merged


def _decision(**overrides: Any) -> dict[str, Any]:
    decision = {
        "close_calls": 1,
        "code": "success",
        "execute_calls": 1,
        "health_reason": "ready",
        "open_calls": 1,
        "origin_calls": 1,
        "outcome": "success",
        "ready": True,
        "recycle_calls": 0,
        "recycle_reason": "none",
    }
    decision.update(overrides)
    return decision


def _no_calls(**overrides: Any) -> dict[str, Any]:
    return _decision(close_calls=0, execute_calls=0, open_calls=0, origin_calls=0, **overrides)


def _config_error(code: str) -> dict[str, Any]:
    return _no_calls(
        code=code,
        health_reason="config_error",
        outcome="config_error",
        ready=False,
    )


def _validate_config(config: Any) -> str | None:
    if (
        not isinstance(config, dict)
        or set(config) != CONFIG_KEYS
        or any(value is None for value in config.values())
    ):
        return "invalid_json"
    if not all(
        isinstance(config[name], int) and not isinstance(config[name], bool) for name in LIMITS
    ):
        return "invalid_json"
    if not all(0 < config[name] <= maximum for name, maximum in LIMITS.items()):
        return "invalid_limit"
    if (
        not isinstance(config["run_as_uid"], int)
        or isinstance(config["run_as_uid"], bool)
        or not isinstance(config["run_as_gid"], int)
        or isinstance(config["run_as_gid"], bool)
    ):
        return "invalid_json"
    if (
        config["backend"] != "chromium"
        or config["service_lane"] != "chromium"
        or not all(
            isinstance(config[name], str) and IDENTITY.fullmatch(config[name]) is not None
            for name in ("browser_version", "cdp_client_version", "egress_policy_revision")
        )
    ):
        return "invalid_identity"
    if not all(
        isinstance(config[name], str) and DIGEST.fullmatch(config[name]) is not None
        for name in ("browser_digest", "image_digest")
    ):
        return "invalid_digest"
    socket = config["socket_path"]
    if not isinstance(socket, str) or SOCKET.fullmatch(socket) is None or ".." in socket:
        return "invalid_endpoint"
    if (
        not 0 < config["run_as_uid"] <= 65_535
        or not 0 < config["run_as_gid"] <= 65_535
        or config["read_only_root"] is not True
        or config["no_new_privileges"] is not True
        or config["drop_all_capabilities"] is not True
        or config["seccomp_profile"] != "runtime/default"
    ):
        return "invalid_security"
    return None


def _parse_capabilities(value: Any, *, allow_empty: bool) -> list[int] | None:
    if not isinstance(value, list) or (not allow_empty and not value):
        return None
    parsed: list[int] = []
    for name in value:
        if not isinstance(name, str) or name not in CAPABILITIES:
            return None
        capability = CAPABILITIES[name]
        if capability in parsed:
            return None
        parsed.append(capability)
    return sorted(parsed)


def _derived_class(capabilities: list[int]) -> str:
    identity = {
        runtime_pb2.BROWSER_CAPABILITY_FRAMES,
        runtime_pb2.BROWSER_CAPABILITY_PERSISTENT_SESSION,
        runtime_pb2.BROWSER_CAPABILITY_HEADFUL_IDENTITY,
        runtime_pb2.BROWSER_CAPABILITY_PROXY,
        runtime_pb2.BROWSER_CAPABILITY_TRANSPORT_OVERRIDES,
    }
    interaction = {
        runtime_pb2.BROWSER_CAPABILITY_ACTIONS,
        runtime_pb2.BROWSER_CAPABILITY_PAGINATION,
        runtime_pb2.BROWSER_CAPABILITY_RESPONSE_CAPTURE,
        runtime_pb2.BROWSER_CAPABILITY_REQUEST_INTERCEPTION,
    }
    if any(item in identity for item in capabilities):
        return "identity_transport"
    if any(item in interaction for item in capabilities):
        return "interaction_capture"
    return "navigation_evaluation"


def _snapshot_rejection(name: str) -> dict[str, Any] | None:
    cases = {
        "down": ("internal", "process_down", "process_crash"),
        "pins_mismatch": ("invalid_config", "pins_mismatch", "none"),
        "unhealthy": ("internal", "process_unhealthy", "failed_health"),
        "age": ("resource_limit", "resource_limit", "process_age"),
        "rss": ("resource_limit", "resource_limit", "rss_limit"),
        "session_leak": ("resource_limit", "resource_limit", "session_leak"),
        "target_leak": ("resource_limit", "resource_limit", "target_leak"),
        "pids": ("resource_limit", "resource_limit", "failed_health"),
        "file_descriptors": ("resource_limit", "resource_limit", "failed_health"),
        "sockets": ("resource_limit", "resource_limit", "failed_health"),
    }
    values = cases.get(name)
    if values is None:
        return None
    code, health, recycle = values
    return _no_calls(
        code=code,
        health_reason=health,
        outcome="error",
        ready=False,
        recycle_calls=0 if recycle == "none" else 1,
        recycle_reason=recycle,
    )


def evaluate(input_value: dict[str, Any]) -> dict[str, Any]:
    config = input_value.get("config")
    config_code = _validate_config(config)
    if config_code is not None:
        return _config_error(config_code)

    assignment = input_value.get("assignment")
    capabilities = _parse_capabilities(input_value.get("plan_capabilities"), allow_empty=False)
    if (
        not input_value.get("plan_present")
        or not isinstance(assignment, dict)
        or capabilities is None
        or input_value.get("plan_contract_version") != "crawler.runtime/v1"
        or input_value.get("target_url") == ""
        or assignment.get("backend") != "chromium"
        or assignment.get("service_lane") != "chromium"
        or assignment.get("capability_class") != _derived_class(capabilities)
        or not isinstance(assignment.get("routing_revision"), str)
        or IDENTITY.fullmatch(assignment["routing_revision"]) is None
        or not isinstance(input_value.get("origin_operations"), int)
        or isinstance(input_value.get("origin_operations"), bool)
        or input_value["origin_operations"] > config["max_origin_operations"]
        or input_value["origin_operations"] > config["max_requests"]
        or not isinstance(input_value.get("plan_operations"), int)
        or isinstance(input_value.get("plan_operations"), bool)
        or input_value["plan_operations"] > config["max_requests"]
    ):
        return _no_calls(code="invalid_config", outcome="error")

    if input_value.get("context_cancelled"):
        return _no_calls(code="cancelled", outcome="error")
    mode = input_value.get("mode")
    if mode == "shutdown":
        return _no_calls(
            code="cancelled", health_reason="shutting_down", outcome="error", ready=False
        )

    provider = input_value.get("provider")
    assert isinstance(provider, dict)
    snapshot = _snapshot_rejection(provider.get("snapshot_before"))
    if snapshot is not None:
        return snapshot
    if mode == "saturated":
        return _decision(code="resource_limit", outcome="error")

    provider_capabilities = _parse_capabilities(provider.get("capabilities"), allow_empty=True)
    if provider_capabilities is None:
        return _no_calls(
            code="internal",
            health_reason="recycle_pending",
            outcome="error",
            ready=False,
            recycle_calls=1,
            recycle_reason="protocol_failure",
        )
    missing = sorted(set(capabilities) - set(provider_capabilities))
    if missing:
        result = runtime_pb2.BrowserResult(
            contract_version="crawler.runtime/v1",
            backend=runtime_pb2.BROWSER_BACKEND_CHROMIUM,
        )
        result.unsupported.capabilities.extend(missing)
        assert list(result.unsupported.capabilities) == missing
        return _no_calls(code="unsupported_capability", outcome="unsupported")

    if provider.get("open") == "timeout":
        return _decision(
            close_calls=0,
            code="timeout",
            execute_calls=0,
            origin_calls=0,
            outcome="error",
        )
    if provider.get("open") == "error_with_session":
        return _decision(
            code="internal",
            execute_calls=0,
            health_reason="cleanup_failed",
            origin_calls=0,
            outcome="error",
            ready=False,
            recycle_calls=1,
            recycle_reason="protocol_failure",
        )

    if provider.get("cleanup") != "ok":
        return _decision(
            code="internal",
            health_reason="cleanup_failed",
            outcome="error",
            ready=False,
            recycle_calls=1,
            recycle_reason="cleanup_failure",
        )
    execute = provider.get("execute")
    if provider.get("fingerprint") != "match" or execute in {
        "partial_timeout",
        "empty",
        "invalid_pair",
    }:
        return _decision(
            code="internal",
            health_reason="recycle_pending",
            outcome="error",
            ready=False,
            recycle_calls=1,
            recycle_reason="protocol_failure",
        )
    typed = {
        "timeout": ("timeout", "none"),
        "cancelled": ("cancelled", "none"),
        "target_lost": ("target_lost", "protocol_failure"),
        "session_lost": ("session_lost", "session_leak"),
        "crash": ("transport", "process_crash"),
        "protocol": ("internal", "protocol_failure"),
        "resource": ("resource_limit", "none"),
        "navigation": ("navigation", "none"),
    }
    if execute in typed:
        code, recycle = typed[execute]
        if recycle == "none":
            return _decision(code=code, outcome="error")
        return _decision(
            code=code,
            health_reason="recycle_pending",
            outcome="error",
            ready=False,
            recycle_calls=1,
            recycle_reason=recycle,
        )

    if mode == "repeat":
        return _decision(close_calls=2, execute_calls=2, open_calls=2, origin_calls=2)
    if config["recycle_after_tasks"] == 1:
        return _decision(
            health_reason="recycle_pending",
            ready=False,
            recycle_calls=1,
            recycle_reason="task_limit",
        )
    if provider.get("snapshot_after") == "rss":
        return _decision(
            health_reason="resource_limit",
            ready=False,
            recycle_calls=1,
            recycle_reason="rss_limit",
        )
    return _decision(origin_calls=provider.get("origin_calls", 0))


def test_registry_digest_and_schema_are_independently_closed() -> None:
    manifest = _manifest()
    assert set(manifest) == {
        "cases",
        "defaults",
        "expected_defaults",
        "format",
        "required_case_ids",
    }
    assert manifest["format"] == FORMAT
    assert tuple(manifest["required_case_ids"]) == REQUIRED_CASE_IDS
    ids = tuple(case["id"] for case in manifest["cases"])
    assert ids == REQUIRED_CASE_IDS
    assert len(ids) == len(set(ids)) == 69

    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == CONFIG_KEYS
    assert set(schema["required"]) == CONFIG_KEYS


@pytest.mark.parametrize("case_id", REQUIRED_CASE_IDS)
def test_every_chromium_service_case_matches_exact_decision(case_id: str) -> None:
    manifest = _manifest()
    fixture = next(case for case in manifest["cases"] if case["id"] == case_id)
    merged_input = _deep_merge(manifest["defaults"], fixture["input"])
    expected = _deep_merge(manifest["expected_defaults"], fixture["expected"])
    assert evaluate(merged_input) == expected


def test_generated_browser_contract_is_consumed_without_new_idl() -> None:
    original = runtime_pb2.BrowserExecutionInput(
        plan=runtime_pb2.BrowserPlan(
            contract_version="crawler.runtime/v1",
            target_url="https://example.test/jobs",
            required_capabilities=[runtime_pb2.BROWSER_CAPABILITY_RENDER],
        ),
        assignment=runtime_pb2.BrowserAssignment(
            backend=runtime_pb2.BROWSER_BACKEND_CHROMIUM,
            capability_class=runtime_pb2.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION,
            service_lane=runtime_pb2.BROWSER_SERVICE_LANE_CHROMIUM,
            routing_revision="route-1",
        ),
    )
    restored = runtime_pb2.BrowserExecutionInput.FromString(original.SerializeToString())
    assert restored == original
