"""A fixed-origin diagnostic after an isolated comparison arm has already failed."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from admission.native_lane.workload import ORIGINS

ENTRYPOINT = [
    "/usr/bin/setpriv",
    "--reuid=10001",
    "--regid=10001",
    "--clear-groups",
    "--inh-caps=+kill,+setgid,+setuid",
    "--ambient-caps=+kill,+setgid,+setuid",
    "--nnp",
    "--",
    "/usr/local/bin/go-lightpanda",
]


def bindings() -> tuple[Any, Any]:
    root = Path(__file__).resolve().parents[2] / "contracts/v1"
    result = []
    for name, relative in (
        ("native_lane_framing", "framing/codec.py"),
        ("native_lane_runtime_pb2", "python/jobseek_runtime_v1/runtime_pb2.py"),
    ):
        if name not in sys.modules:
            spec = importlib.util.spec_from_file_location(name, root / relative)
            assert spec is not None and spec.loader is not None
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
        result.append(sys.modules[name])
    return result[0], result[1]


def navigation_request() -> bytes:
    framing, pb = bindings()
    url = "https://" + ORIGINS[2] + "/listing"
    fingerprint = hashlib.sha256(
        json.dumps(
            {"body": "", "headers": [], "method": "GET", "url": url},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    request = pb.BrowserExecutionInput()
    request.assignment.backend = pb.BROWSER_BACKEND_LIGHTPANDA
    request.assignment.capability_class = pb.BROWSER_CAPABILITY_CLASS_NAVIGATION_EVALUATION
    request.assignment.service_lane = pb.BROWSER_SERVICE_LANE_LIGHTPANDA
    request.assignment.routing_revision = "native-lane-diagnostic"
    request.plan.contract_version = "crawler.runtime/v1"
    request.plan.target_url = url
    request.plan.required_capabilities.append(pb.BROWSER_CAPABILITY_RENDER)
    request.plan.navigation.wait_until = pb.WAIT_CONDITION_LOAD
    request.plan.navigation.timeout_ms = 30000
    request.plan.navigation.origin_request_id = "native-lane-diagnostic"
    operation = request.plan.origin_operations.add()
    operation.origin_request_id = "native-lane-diagnostic"
    operation.operation_sequence = 1
    operation.role = "navigation"
    operation.request_fingerprint = fingerprint
    return framing.encode_record(request.SerializeToString(deterministic=True), 65536)


def probe(identifier: str, entrypoint: list[str]) -> dict[str, Any]:
    # The caller has already attested the internal fixture networks. Reuse the
    # exact privilege-drop entrypoint and existing cgroup limits, after failure
    # and measurement termination. Never accept an upstream target argument.
    if not re.fullmatch(r"[a-f0-9]{12,64}", identifier) or entrypoint != ENTRYPOINT:
        return {"state": "invalid_container_or_entrypoint"}
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "-i",
                "--user",
                "0:0",
                identifier,
                *ENTRYPOINT,
                "--runtime-v1-stdio",
            ],
            input=navigation_request(),
            capture_output=True,
            timeout=45,
        )
        output: dict[str, Any] = {"exit_code": result.returncode}
        text = result.stderr[:131072].decode(errors="replace").lower()
        output["error_categories"] = [
            word
            for word in (
                "certificate",
                "permission denied",
                "operation not permitted",
                "resolve",
                "connection",
                "unsupported",
                "no such file",
            )
            if word in text
        ]
        if len(result.stdout) > 8 * 1024 * 1024:
            return {**output, "state": "oversized_result"}
        framing, pb = bindings()
        value = pb.BrowserResult.FromString(framing.decode_record(result.stdout, 8 * 1024 * 1024))
        if (
            value.contract_version != "crawler.runtime/v1"
            or value.backend != pb.BROWSER_BACKEND_LIGHTPANDA
        ):
            return {**output, "state": "invalid_result_identity"}
        output["outcome"] = value.WhichOneof("outcome")
        if output["outcome"] == "error":
            output["code"] = pb.ErrorCode.Name(value.error.error.code)
            output["disposition"] = pb.ErrorDisposition.Name(value.error.error.disposition)
        elif output["outcome"] == "success":
            output["html_bytes"] = value.success.html.total_size_bytes
        return output
    except Exception as error:
        return {"diagnostic_failure_class": type(error).__name__}
