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
MANIFEST_PATH = V1 / "fixtures" / "browser_executor" / "manifest.json"
DIGEST_PATH = MANIFEST_PATH.with_name("manifest.sha256")
BINDING_PATH = V1 / "python" / "jobseek_runtime_v1" / "runtime_pb2.py"

_SPEC = importlib.util.spec_from_file_location("runtime_v1_browser_assignment_pb2", BINDING_PATH)
assert _SPEC is not None and _SPEC.loader is not None
runtime_pb2: Any = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runtime_pb2
_SPEC.loader.exec_module(runtime_pb2)

FORMAT = "jobseek.browser-executor-boundary/v1"
ROUTING_REVISION = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._+-]{0,63}$", re.ASCII)

REQUIRED_CASE_IDS = (
    "accept_chromium_navigation_success",
    "accept_chromium_success",
    "accept_lightpanda_interaction_success",
    "accept_lightpanda_success",
    "accept_typed_provider_error",
    "accept_typed_unsupported_preflight",
    "reject_assignment_changed",
    "reject_capability_class_mismatch",
    "reject_duplicate_plan_capability",
    "reject_duplicate_provider_capability",
    "reject_error_partial_output",
    "reject_fallback_chromium_to_lightpanda",
    "reject_fallback_lightpanda_to_chromium",
    "reject_invalid_routing_revision",
    "reject_missing_provider_invocation",
    "reject_null_assignment",
    "reject_null_assignment_backend",
    "reject_null_assignment_capability_class",
    "reject_null_assignment_routing_revision",
    "reject_null_assignment_service_lane",
    "reject_null_authoritative_partial_output",
    "reject_null_origin_before_assignment",
    "reject_null_origin_operations",
    "reject_null_plan_capabilities",
    "reject_null_provider_capabilities",
    "reject_null_provider_invocations",
    "reject_null_result",
    "reject_null_result_backend",
    "reject_null_result_outcome",
    "reject_null_unsupported_capabilities",
    "reject_origin_before_assignment",
    "reject_oversized_routing_revision",
    "reject_provider_backend_mismatch",
    "reject_result_backend_mismatch",
    "reject_retry_same_backend",
    "reject_service_lane_mismatch",
    "reject_success_missing_origin",
    "reject_success_with_error",
    "reject_success_with_unsupported",
    "reject_unknown_assignment_backend",
    "reject_unknown_capability_class",
    "reject_unknown_input_field",
    "reject_unknown_plan_capability",
    "reject_unknown_provider_capability",
    "reject_unknown_result_outcome",
    "reject_unknown_service_lane",
    "reject_unspecified_assignment_backend",
    "reject_unspecified_capability_class",
    "reject_unspecified_plan_capability",
    "reject_unspecified_service_lane",
    "reject_untyped_error",
    "reject_unexpected_unsupported",
    "reject_unsupported_extra_capability",
    "reject_unsupported_missing_capability",
    "reject_unsupported_origin_operation",
    "reject_unsupported_partial_output",
    "reject_unsupported_provider_invocation",
)

BACKENDS = {
    "unspecified": runtime_pb2.BROWSER_BACKEND_UNSPECIFIED,
    "chromium": runtime_pb2.BROWSER_BACKEND_CHROMIUM,
    "lightpanda": runtime_pb2.BROWSER_BACKEND_LIGHTPANDA,
}
CAPABILITY_CLASSES = {
    "unspecified": runtime_pb2.BROWSER_CAPABILITY_CLASS_UNSPECIFIED,
    "navigation_evaluation": runtime_pb2.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION,
    "interaction_capture": runtime_pb2.BROWSER_CAPABILITY_CLASS_INTERACTION_CAPTURE,
    "identity_transport": runtime_pb2.BROWSER_CAPABILITY_CLASS_IDENTITY_TRANSPORT,
}
SERVICE_LANES = {
    "unspecified": runtime_pb2.BROWSER_SERVICE_LANE_UNSPECIFIED,
    "chromium": runtime_pb2.BROWSER_SERVICE_LANE_CHROMIUM,
    "lightpanda": runtime_pb2.BROWSER_SERVICE_LANE_LIGHTPANDA,
}
CAPABILITIES = {
    "unspecified": runtime_pb2.BROWSER_CAPABILITY_UNSPECIFIED,
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
ERROR_CODES = {
    "tdm_reserved": runtime_pb2.ERROR_CODE_TDM_RESERVED,
    "provider_gone": runtime_pb2.ERROR_CODE_PROVIDER_GONE,
    "permanent_gone": runtime_pb2.ERROR_CODE_PERMANENT_GONE,
    "http_status": runtime_pb2.ERROR_CODE_HTTP_STATUS,
    "timeout": runtime_pb2.ERROR_CODE_TIMEOUT,
    "transport": runtime_pb2.ERROR_CODE_TRANSPORT,
    "anti_bot": runtime_pb2.ERROR_CODE_ANTI_BOT,
    "invalid_config": runtime_pb2.ERROR_CODE_INVALID_CONFIG,
    "empty_result": runtime_pb2.ERROR_CODE_EMPTY_RESULT,
    "internal": runtime_pb2.ERROR_CODE_INTERNAL,
    "target_lost": runtime_pb2.ERROR_CODE_TARGET_LOST,
    "session_lost": runtime_pb2.ERROR_CODE_SESSION_LOST,
    "resource_limit": runtime_pb2.ERROR_CODE_RESOURCE_LIMIT,
    "cancelled": runtime_pb2.ERROR_CODE_CANCELLED,
    "ambiguous_origin": runtime_pb2.ERROR_CODE_AMBIGUOUS_ORIGIN,
    "navigation": runtime_pb2.ERROR_CODE_NAVIGATION,
}

INPUT_KEYS = {
    "assignment",
    "assignment_after_bind",
    "origin_before_assignment",
    "origin_operations",
    "plan_capabilities",
    "provider_capabilities",
    "provider_invocations",
    "result",
}
ASSIGNMENT_KEYS = {"backend", "capability_class", "routing_revision", "service_lane"}
RESULT_KEYS = {
    "backend",
    "error_code",
    "outcome",
    "partial_output_present",
    "unsupported_capabilities",
}


def _manifest() -> dict[str, Any]:
    raw = MANIFEST_PATH.read_bytes()
    document = json.loads(raw)
    canonical = (json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "ascii"
    )
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


def _decision(status: str, code: str) -> dict[str, str]:
    return {"code": code, "status": status}


def _parse_capabilities(values: Any, *, allow_empty: bool) -> list[int] | None:
    if not isinstance(values, list) or (not allow_empty and not values):
        return None
    parsed: list[int] = []
    for value in values:
        if not isinstance(value, str):
            return None
        capability = CAPABILITIES.get(value)
        if capability is None or capability == runtime_pb2.BROWSER_CAPABILITY_UNSPECIFIED:
            return None
        if capability in parsed:
            return None
        parsed.append(capability)
    return parsed


def _parse_assignment(value: Any) -> Any | None:
    if not isinstance(value, dict) or set(value) != ASSIGNMENT_KEYS:
        return None
    if not all(isinstance(value[name], str) for name in ASSIGNMENT_KEYS):
        return None
    backend = BACKENDS.get(value["backend"])
    capability_class = CAPABILITY_CLASSES.get(value["capability_class"])
    service_lane = SERVICE_LANES.get(value["service_lane"])
    if (
        backend in {None, runtime_pb2.BROWSER_BACKEND_UNSPECIFIED}
        or capability_class in {None, runtime_pb2.BROWSER_CAPABILITY_CLASS_UNSPECIFIED}
        or service_lane in {None, runtime_pb2.BROWSER_SERVICE_LANE_UNSPECIFIED}
    ):
        return None
    return runtime_pb2.BrowserAssignment(
        backend=backend,
        capability_class=capability_class,
        service_lane=service_lane,
        routing_revision=value["routing_revision"],
    )


def _required_assignment_shape(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and set(value) == ASSIGNMENT_KEYS
        and all(isinstance(value[name], str) for name in ASSIGNMENT_KEYS)
    )


def _required_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _required_input_types(value: dict[str, Any]) -> bool:
    if not _required_assignment_shape(value["assignment"]):
        return False
    after = value["assignment_after_bind"]
    if after is not None and not _required_assignment_shape(after):
        return False
    origin_operations = value["origin_operations"]
    if (
        not isinstance(value["origin_before_assignment"], bool)
        or isinstance(origin_operations, bool)
        or not isinstance(origin_operations, int)
        or not _required_string_list(value["plan_capabilities"])
        or not _required_string_list(value["provider_capabilities"])
        or not _required_string_list(value["provider_invocations"])
    ):
        return False
    result = value["result"]
    if not isinstance(result, dict) or set(result) != RESULT_KEYS:
        return False
    error_code = result["error_code"]
    return (
        isinstance(result["backend"], str)
        and (error_code is None or isinstance(error_code, str))
        and isinstance(result["outcome"], str)
        and isinstance(result["partial_output_present"], bool)
        and _required_string_list(result["unsupported_capabilities"])
    )


def _derived_capability_class(capabilities: list[int]) -> int:
    identity_transport = {
        runtime_pb2.BROWSER_CAPABILITY_FRAMES,
        runtime_pb2.BROWSER_CAPABILITY_PERSISTENT_SESSION,
        runtime_pb2.BROWSER_CAPABILITY_HEADFUL_IDENTITY,
        runtime_pb2.BROWSER_CAPABILITY_PROXY,
        runtime_pb2.BROWSER_CAPABILITY_TRANSPORT_OVERRIDES,
    }
    interaction_capture = {
        runtime_pb2.BROWSER_CAPABILITY_ACTIONS,
        runtime_pb2.BROWSER_CAPABILITY_PAGINATION,
        runtime_pb2.BROWSER_CAPABILITY_RESPONSE_CAPTURE,
        runtime_pb2.BROWSER_CAPABILITY_REQUEST_INTERCEPTION,
    }
    if any(capability in identity_transport for capability in capabilities):
        return runtime_pb2.BROWSER_CAPABILITY_CLASS_IDENTITY_TRANSPORT
    if any(capability in interaction_capture for capability in capabilities):
        return runtime_pb2.BROWSER_CAPABILITY_CLASS_INTERACTION_CAPTURE
    return runtime_pb2.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION


def _expected_lane(backend: int) -> int:
    if backend == runtime_pb2.BROWSER_BACKEND_LIGHTPANDA:
        return runtime_pb2.BROWSER_SERVICE_LANE_LIGHTPANDA
    return runtime_pb2.BROWSER_SERVICE_LANE_CHROMIUM


def _build_result(raw: Any) -> Any | None:
    if not isinstance(raw, dict) or set(raw) != RESULT_KEYS:
        return None
    backend = BACKENDS.get(raw["backend"])
    if backend in {None, runtime_pb2.BROWSER_BACKEND_UNSPECIFIED}:
        return None
    result = runtime_pb2.BrowserResult(contract_version="crawler.runtime/v1", backend=backend)
    if raw["outcome"] == "success":
        result.success.SetInParent()
    elif raw["outcome"] == "error":
        if not isinstance(raw["error_code"], str) or raw["error_code"] not in ERROR_CODES:
            return None
        result.error.error.code = ERROR_CODES[raw["error_code"]]
        result.error.error.disposition = runtime_pb2.ERROR_DISPOSITION_FAIL_CLOSED_POLICY
    elif raw["outcome"] == "unsupported":
        capabilities = _parse_capabilities(raw["unsupported_capabilities"], allow_empty=True)
        if capabilities is None:
            return None
        result.unsupported.capabilities.extend(capabilities)
    else:
        return None
    return result


def evaluate_browser_boundary(input_value: dict[str, Any]) -> dict[str, str]:
    if set(input_value) != INPUT_KEYS:
        return _decision("rejected", "invalid_input")
    if not _required_input_types(input_value):
        return _decision("rejected", "invalid_input")
    result_raw = input_value["result"]
    origin_operations = input_value["origin_operations"]
    if isinstance(origin_operations, bool) or not isinstance(origin_operations, int):
        return _decision("rejected", "invalid_input")
    if origin_operations < 0 or not isinstance(input_value["origin_before_assignment"], bool):
        return _decision("rejected", "invalid_input")

    assignment = _parse_assignment(input_value["assignment"])
    if assignment is None:
        return _decision("rejected", "invalid_assignment")
    plan_capabilities = _parse_capabilities(input_value["plan_capabilities"], allow_empty=False)
    if plan_capabilities is None:
        return _decision("rejected", "invalid_capabilities")
    plan = runtime_pb2.BrowserPlan(
        contract_version="crawler.runtime/v1", required_capabilities=plan_capabilities
    )
    bound_input = runtime_pb2.BrowserExecutionInput(plan=plan, assignment=assignment)
    if assignment.capability_class != _derived_capability_class(list(plan.required_capabilities)):
        return _decision("rejected", "capability_class_mismatch")
    if assignment.service_lane != _expected_lane(assignment.backend):
        return _decision("rejected", "service_lane_mismatch")
    if ROUTING_REVISION.fullmatch(assignment.routing_revision) is None:
        return _decision("rejected", "routing_revision_invalid")
    after_value = input_value["assignment_after_bind"]
    if after_value is not None:
        after = _parse_assignment(after_value)
        if after is None or bound_input.assignment != after:
            return _decision("rejected", "assignment_changed")
    if input_value["origin_before_assignment"]:
        return _decision("rejected", "origin_before_assignment")

    provider_capabilities = _parse_capabilities(
        input_value["provider_capabilities"], allow_empty=True
    )
    if provider_capabilities is None:
        return _decision("rejected", "invalid_provider_capabilities")
    result = _build_result(result_raw)
    if result is None:
        if result_raw["outcome"] == "error":
            return _decision("rejected", "error_result_invalid")
        return _decision("rejected", "invalid_result")
    if result.backend != assignment.backend:
        return _decision("rejected", "backend_mismatch")

    missing = sorted(set(plan_capabilities) - set(provider_capabilities))
    invocations = input_value["provider_invocations"]
    if not isinstance(invocations, list) or not all(isinstance(item, str) for item in invocations):
        return _decision("rejected", "invalid_input")
    if missing:
        if invocations:
            return _decision("rejected", "unsupported_execution_forbidden")
        if origin_operations != 0:
            return _decision("rejected", "unsupported_origin_forbidden")
        if (
            result.WhichOneof("outcome") != "unsupported"
            or result_raw["error_code"] is not None
            or result_raw["partial_output_present"] is not False
            or sorted(result.unsupported.capabilities) != missing
        ):
            return _decision("rejected", "unsupported_result_invalid")
        return _decision("unsupported", "unsupported_capability")

    if not invocations:
        return _decision("rejected", "provider_invocation_missing")
    if len(invocations) > 1:
        parsed = [BACKENDS.get(item) for item in invocations]
        if any(backend != assignment.backend for backend in parsed):
            return _decision("rejected", "fallback_forbidden")
        return _decision("rejected", "retry_forbidden")
    if BACKENDS.get(invocations[0]) != assignment.backend:
        return _decision("rejected", "backend_mismatch")

    if result_raw["outcome"] == "success":
        if origin_operations == 0:
            return _decision("rejected", "origin_operation_missing")
        if (
            result_raw["error_code"] is not None
            or result_raw["unsupported_capabilities"]
            or result_raw["partial_output_present"] is not False
            or result.WhichOneof("outcome") != "success"
        ):
            return _decision("rejected", "success_result_invalid")
        return _decision("accepted", "success")
    if result_raw["outcome"] == "error":
        if (
            result_raw["error_code"] is None
            or result_raw["unsupported_capabilities"]
            or result_raw["partial_output_present"] is not False
            or result.WhichOneof("outcome") != "error"
        ):
            return _decision("rejected", "error_result_invalid")
        return _decision("accepted", "provider_error")
    if result_raw["outcome"] == "unsupported":
        return _decision("rejected", "unexpected_unsupported")
    return _decision("rejected", "invalid_result")


def test_required_ids_are_independently_hard_coded_and_complete() -> None:
    manifest = _manifest()
    assert set(manifest) == {"cases", "defaults", "format", "required_case_ids"}
    assert manifest["format"] == FORMAT
    assert tuple(manifest["required_case_ids"]) == REQUIRED_CASE_IDS
    ids = tuple(case["id"] for case in manifest["cases"])
    assert ids == REQUIRED_CASE_IDS
    assert len(ids) == len(set(ids)) == 57
    assert all(set(case) == {"expected", "id", "input"} for case in manifest["cases"])


@pytest.mark.parametrize("case_id", REQUIRED_CASE_IDS)
def test_every_browser_boundary_case_matches_exact_decision(case_id: str) -> None:
    manifest = _manifest()
    fixture = next(case for case in manifest["cases"] if case["id"] == case_id)
    merged = _deep_merge(manifest["defaults"], fixture["input"])
    assert evaluate_browser_boundary(merged) == fixture["expected"]


def test_browser_assignment_is_part_of_generated_execution_input() -> None:
    original = runtime_pb2.BrowserExecutionInput(
        plan=runtime_pb2.BrowserPlan(
            contract_version="crawler.runtime/v1",
            required_capabilities=[runtime_pb2.BROWSER_CAPABILITY_RENDER],
        ),
        assignment=runtime_pb2.BrowserAssignment(
            backend=runtime_pb2.BROWSER_BACKEND_CHROMIUM,
            capability_class=runtime_pb2.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION,
            service_lane=runtime_pb2.BROWSER_SERVICE_LANE_CHROMIUM,
            routing_revision="route-fixture-1",
        ),
    )
    restored = runtime_pb2.BrowserExecutionInput.FromString(original.SerializeToString())
    assert restored == original
