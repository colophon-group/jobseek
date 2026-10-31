"""A fixed-origin diagnostic after an isolated comparison arm has already failed."""

from __future__ import annotations

import base64
import hashlib
import importlib.util
import json
import re
import socket
import ssl
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


def navigation_request(*, resident: bool = False) -> bytes:
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
    if resident:
        request.plan.navigation.transport_retries = 1
        request.plan.navigation.fallback.wait_until = pb.WAIT_CONDITION_DOM_CONTENT_LOADED
        request.plan.navigation.fallback.timeout_ms = 5000
        retry = request.plan.origin_operations.add()
        retry.origin_request_id = "native-lane-diagnostic:transport-retry-1"
        retry.operation_sequence = 2
        retry.role = "transport_retry"
        retry.parent_origin_request_id = "native-lane-diagnostic"
        retry.request_fingerprint = fingerprint
    return framing.encode_record(request.SerializeToString(deterministic=True), 65536)


def resident_probe() -> dict[str, Any]:
    """Use the real resident service, including its isolated browser child."""
    from cryptography import x509
    from cryptography.hazmat.primitives import serialization

    framing, pb = bindings()
    request = pb.BrowserExecutionInput.FromString(
        framing.decode_record(navigation_request(resident=True), 65536)
    )
    envelope = {
        "protocol": "jobseek.lightpanda.document-actions/v1",
        "request_id": "a" * 64,
        "config_fingerprint": "b" * 64,
        "input": base64.b64encode(request.SerializeToString(deterministic=True)).decode(),
        "actions": [],
        "large_document": True,
    }
    directory = Path("/run/lightpanda")
    stage = "tls"
    try:
        context = ssl.create_default_context(cafile=str(directory / "ca.pem"))
        context.minimum_version = context.maximum_version = ssl.TLSVersion.TLSv1_3
        context.set_alpn_protocols(["jobseek-lightpanda-b0/1"])
        context.load_cert_chain(directory / "client.pem", directory / "client-key.pem")
        with (
            socket.create_connection(("10.0.0.5", 9443), timeout=90) as raw,
            context.wrap_socket(raw, server_hostname="10.0.0.5") as connection,
        ):
            certificate = connection.getpeercert(binary_form=True)
            assert certificate is not None
            peer = x509.load_der_x509_certificate(certificate)
            pins = {
                "ca": x509.load_pem_x509_certificate(
                    (directory / "ca.pem").read_bytes()
                ).public_bytes(serialization.Encoding.DER),
                "server-leaf": certificate,
                "server-spki": peer.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                ),
            }
            if connection.selected_alpn_protocol() != "jobseek-lightpanda-b0/1" or any(
                hashlib.sha256(value).hexdigest()
                != (directory / (name + ".sha256")).read_text().strip()
                for name, value in pins.items()
            ):
                raise ValueError("renderer identity mismatch")
            with connection.makefile("rb") as stream:
                stage = "hello"
                hello = json.loads(framing.read_record(stream, 512))
                if hello != {
                    "protocol": "jobseek.lightpanda.service/v1",
                    "runtime_contract": "crawler.runtime/v1",
                    "mode": "b0",
                    "capacity": 4,
                    "memory_max_bytes": 1073741824,
                    "memory_swap_max_bytes": 0,
                }:
                    raise ValueError("renderer hello mismatch")
                stage = "document_actions"
                connection.sendall(
                    framing.encode_record(json.dumps(envelope).encode(), 128 * 1024) + b"\0"
                )
                response = json.loads(framing.read_record(stream, 8 * 1024 * 1024))
                if (
                    response.get("protocol") != envelope["protocol"]
                    or response.get("request_id") != envelope["request_id"]
                    or response.get("config_fingerprint") != envelope["config_fingerprint"]
                    or stream.read(1)
                ):
                    raise ValueError("renderer response binding mismatch")
                value = pb.BrowserResult.FromString(
                    base64.b64decode(response["result"], validate=True)
                )
                if (
                    value.contract_version != "crawler.runtime/v1"
                    or value.backend != pb.BROWSER_BACKEND_LIGHTPANDA
                ):
                    raise ValueError("renderer result mismatch")
                result = {"stage": stage, "outcome": value.WhichOneof("outcome")}
                if result["outcome"] == "error":
                    result["code"] = pb.ErrorCode.Name(value.error.error.code)
                    result["disposition"] = pb.ErrorDisposition.Name(value.error.error.disposition)
                elif result["outcome"] == "success":
                    result["html_bytes"] = value.success.html.total_size_bytes
                    result["policy_evidence_present"] = value.success.HasField("resource_policy")
                return result
    except Exception as error:
        return {"stage": stage, "diagnostic_failure_class": type(error).__name__}


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
