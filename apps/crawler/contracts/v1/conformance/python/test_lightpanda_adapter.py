from __future__ import annotations

import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

V1 = Path(__file__).resolve().parents[2]
MANIFEST = V1 / "fixtures" / "lightpanda_adapter" / "manifest.json"
DIGEST = MANIFEST.with_name("manifest.sha256")
BINDING = V1 / "python" / "jobseek_runtime_v1" / "runtime_pb2.py"

_SPEC = importlib.util.spec_from_file_location("runtime_v1_lightpanda_adapter_pb2", BINDING)
assert _SPEC is not None and _SPEC.loader is not None
runtime_pb2: Any = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = runtime_pb2
_SPEC.loader.exec_module(runtime_pb2)

FORMAT = "jobseek.lightpanda-adapter-b1/v1"
REQUIRED_CASE_IDS = (
    "accept_render_only",
    "accept_render_evaluate",
    "reject_assignment_before_unsupported",
    "reject_duplicate_capability",
    "reject_missing_render",
    "reject_evaluate_without_plan",
    "reject_plan_without_evaluate",
    "reject_non_load_wait",
    "reject_adjacent_action",
    "unsupported_features_are_sorted",
)

CAPABILITY_NAMES = {
    runtime_pb2.BROWSER_CAPABILITY_RENDER: "render",
    runtime_pb2.BROWSER_CAPABILITY_EVALUATE: "evaluate",
    runtime_pb2.BROWSER_CAPABILITY_ACTIONS: "actions",
    runtime_pb2.BROWSER_CAPABILITY_PAGINATION: "pagination",
    runtime_pb2.BROWSER_CAPABILITY_RESPONSE_CAPTURE: "response_capture",
    runtime_pb2.BROWSER_CAPABILITY_REQUEST_INTERCEPTION: "request_interception",
    runtime_pb2.BROWSER_CAPABILITY_FRAMES: "frames",
    runtime_pb2.BROWSER_CAPABILITY_PERSISTENT_SESSION: "persistent_session",
    runtime_pb2.BROWSER_CAPABILITY_HEADFUL_IDENTITY: "headful_identity",
    runtime_pb2.BROWSER_CAPABILITY_PROXY: "proxy",
    runtime_pb2.BROWSER_CAPABILITY_TRANSPORT_OVERRIDES: "transport_overrides",
}


def _load_manifest() -> dict[str, Any]:
    raw = MANIFEST.read_bytes()
    document = json.loads(raw)
    canonical = (json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n").encode(
        "ascii"
    )
    assert raw == canonical
    assert DIGEST.read_text(encoding="ascii") == (
        f"{hashlib.sha256(raw).hexdigest()}  manifest.json\n"
    )
    assert isinstance(document, dict)
    return document


def _base_input() -> dict[str, Any]:
    return {
        "backend": runtime_pb2.BROWSER_BACKEND_LIGHTPANDA,
        "capability_class": runtime_pb2.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION,
        "service_lane": runtime_pb2.BROWSER_SERVICE_LANE_LIGHTPANDA,
        "capabilities": [
            runtime_pb2.BROWSER_CAPABILITY_EVALUATE,
            runtime_pb2.BROWSER_CAPABILITY_RENDER,
        ],
        "evaluation_count": 1,
        "wait_until": runtime_pb2.WAIT_CONDITION_LOAD,
        "action_count": 0,
    }


def _mutate(value: dict[str, Any], mutation: str) -> None:
    if mutation == "none":
        return
    if mutation == "render_only":
        value["capabilities"] = [runtime_pb2.BROWSER_CAPABILITY_RENDER]
        value["evaluation_count"] = 0
    elif mutation == "wrong_backend_with_frames":
        value["backend"] = runtime_pb2.BROWSER_BACKEND_CHROMIUM
        value["capabilities"].append(runtime_pb2.BROWSER_CAPABILITY_FRAMES)
    elif mutation == "duplicate_render":
        value["capabilities"].append(runtime_pb2.BROWSER_CAPABILITY_RENDER)
    elif mutation == "evaluate_only":
        value["capabilities"] = [runtime_pb2.BROWSER_CAPABILITY_EVALUATE]
    elif mutation == "remove_evaluation":
        value["evaluation_count"] = 0
    elif mutation == "remove_evaluate_capability":
        value["capabilities"] = [runtime_pb2.BROWSER_CAPABILITY_RENDER]
    elif mutation == "dom_content_loaded":
        value["wait_until"] = runtime_pb2.WAIT_CONDITION_DOM_CONTENT_LOADED
    elif mutation == "add_action":
        value["action_count"] = 1
    elif mutation == "add_unsorted_unsupported":
        value["capabilities"].extend(
            [
                runtime_pb2.BROWSER_CAPABILITY_PROXY,
                runtime_pb2.BROWSER_CAPABILITY_ACTIONS,
                runtime_pb2.BROWSER_CAPABILITY_FRAMES,
            ]
        )
    else:
        raise AssertionError(f"unknown mutation {mutation!r}")


def _decision(value: dict[str, Any]) -> dict[str, Any]:
    # Assignment is validated before capability classification.
    if (
        value["backend"] != runtime_pb2.BROWSER_BACKEND_LIGHTPANDA
        or value["capability_class"] != runtime_pb2.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION
        or value["service_lane"] != runtime_pb2.BROWSER_SERVICE_LANE_LIGHTPANDA
    ):
        return {"code": "invalid_config", "status": "error"}

    capabilities = value["capabilities"]
    if (
        not capabilities
        or len(capabilities) != len(set(capabilities))
        or any(capability not in CAPABILITY_NAMES for capability in capabilities)
        or runtime_pb2.BROWSER_CAPABILITY_RENDER not in capabilities
    ):
        return {"code": "invalid_config", "status": "error"}
    available = {
        runtime_pb2.BROWSER_CAPABILITY_RENDER,
        runtime_pb2.BROWSER_CAPABILITY_EVALUATE,
    }
    unsupported = sorted(set(capabilities) - available)
    if unsupported:
        return {
            "capabilities": [CAPABILITY_NAMES[capability] for capability in unsupported],
            "status": "unsupported",
        }

    has_evaluate = runtime_pb2.BROWSER_CAPABILITY_EVALUATE in capabilities
    if (
        value["evaluation_count"] not in {0, 1}
        or has_evaluate != (value["evaluation_count"] == 1)
        or value["wait_until"] != runtime_pb2.WAIT_CONDITION_LOAD
        or value["action_count"] != 0
    ):
        return {"code": "invalid_config", "status": "error"}
    return {"status": "success"}


def test_lightpanda_adapter_corpus_is_closed_and_matches_runtime_v1() -> None:
    manifest = _load_manifest()
    assert manifest["format"] == FORMAT
    assert tuple(manifest["required_case_ids"]) == REQUIRED_CASE_IDS
    assert tuple(case["id"] for case in manifest["cases"]) == REQUIRED_CASE_IDS
    for case in manifest["cases"]:
        value = _base_input()
        _mutate(value, case["mutation"])
        assert _decision(value) == case["expected"], case["id"]
