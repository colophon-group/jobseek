from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
import ipaddress
import json
import os
import socket
import ssl
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from admission.native_lane import controller, driver, legacy_browser, render_diagnostic
from admission.native_lane.fixture_server import FixtureState, tls_server
from admission.native_lane.resources import allocation, role, sample


def test_render_probe_uses_fixed_fixture_and_emits_only_typed_codes(monkeypatch) -> None:
    framing, pb = render_diagnostic.bindings()
    value = pb.BrowserResult(
        contract_version="crawler.runtime/v1", backend=pb.BROWSER_BACKEND_LIGHTPANDA
    )
    value.error.error.code = pb.ERROR_CODE_INVALID_CONFIG
    value.error.error.disposition = pb.ERROR_DISPOSITION_INVALID_CONFIG_POLICY
    calls = []

    def execute(args, **kwargs):
        calls.append((args, kwargs))
        request = pb.BrowserExecutionInput.FromString(framing.decode_record(kwargs["input"], 65536))
        assert request.plan.target_url == "https://origin-2.lane.bench.test/listing"
        assert request.plan.navigation.timeout_ms == 30000
        assert len(request.plan.origin_operations) == 1
        return subprocess.CompletedProcess(
            args,
            0,
            framing.encode_record(value.SerializeToString(), 65536),
            b"certificate PRIVATE_SHOULD_NOT_ECHO",
        )

    monkeypatch.setattr(render_diagnostic.subprocess, "run", execute)
    result = render_diagnostic.probe("a" * 64, render_diagnostic.ENTRYPOINT)
    assert result == {
        "exit_code": 0,
        "error_categories": ["certificate"],
        "outcome": "error",
        "code": "ERROR_CODE_INVALID_CONFIG",
        "disposition": "ERROR_DISPOSITION_INVALID_CONFIG_POLICY",
    }
    assert calls[0][0][-1] == "--runtime-v1-stdio"
    assert calls[0][1]["timeout"] == 45
    assert "PRIVATE_SHOULD_NOT_ECHO" not in json.dumps(result)


def test_render_probe_rejects_foreign_entrypoint_without_execution(monkeypatch) -> None:
    def reject(*args, **kwargs):
        raise AssertionError("must not execute")

    monkeypatch.setattr(render_diagnostic.subprocess, "run", reject)
    assert (
        render_diagnostic.probe("a" * 64, ["PRIVATE_COMMAND"])["state"]
        == "invalid_container_or_entrypoint"
    )


def test_render_probe_timeout_omits_subprocess_content(monkeypatch) -> None:
    def expired(*args, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE_COMMAND", 45, output=b"PRIVATE_BODY")

    monkeypatch.setattr(render_diagnostic.subprocess, "run", expired)
    result = render_diagnostic.probe("a" * 64, render_diagnostic.ENTRYPOINT)
    assert result == {"diagnostic_failure_class": "TimeoutExpired"}


def test_resident_probe_uses_worker_navigation_and_hides_tls_errors(monkeypatch) -> None:
    framing, pb = render_diagnostic.bindings()
    request = pb.BrowserExecutionInput.FromString(
        framing.decode_record(render_diagnostic.navigation_request(resident=True), 65536)
    )
    assert request.plan.navigation.transport_retries == 1
    assert request.plan.navigation.fallback.wait_until == pb.WAIT_CONDITION_DOM_CONTENT_LOADED
    assert request.plan.navigation.fallback.timeout_ms == 5000
    assert len(request.plan.origin_operations) == 2
    primary, retry = request.plan.origin_operations
    assert retry.parent_origin_request_id == primary.origin_request_id
    assert retry.request_fingerprint == primary.request_fingerprint
    assert retry.operation_sequence == 2
    assert retry.role == "transport_retry"

    def reject(*args, **kwargs):
        raise ssl.SSLError("PRIVATE_CERTIFICATE_AND_PATH")

    monkeypatch.setattr(render_diagnostic.ssl, "create_default_context", reject)
    assert render_diagnostic.resident_probe() == {
        "stage": "tls",
        "diagnostic_failure_class": "SSLError",
    }


def test_certificate_probe_compares_real_uids_and_trust_without_exposing_content(monkeypatch):
    calls = []

    def execute(command, **kwargs):
        calls.append(command)
        assert kwargs == {"capture_output": True, "timeout": 15}
        assert command[-1] == "https://origin-2.lane.bench.test/listing"
        return subprocess.CompletedProcess(
            command, 1, b"PRIVATE_BODY", b"PeerFailedVerification PRIVATE"
        )

    monkeypatch.setattr(render_diagnostic.subprocess, "run", execute)
    result = render_diagnostic.certificate_probe("a" * 64)
    assert [(row["uid"], row["trust"]) for row in result] == [
        (10001, "system"),
        (10001, "explicit"),
        (10002, "system"),
        (10002, "explicit"),
    ]
    assert [command[3] for command in calls] == ["10001:10001"] * 2 + ["10002:10002"] * 2
    assert ["--ca-cert" in command for command in calls] == [False, True, False, True]
    assert all(row["peer_verification_failed"] for row in result)
    assert "PRIVATE" not in json.dumps(result)


def test_certificate_probe_rejects_foreign_identifier_and_hides_timeout(monkeypatch):
    def expired(command, **kwargs):
        raise subprocess.TimeoutExpired("PRIVATE_COMMAND", 15, output=b"PRIVATE_BODY")

    monkeypatch.setattr(render_diagnostic.subprocess, "run", expired)
    assert render_diagnostic.certificate_probe("foreign") == [{"state": "invalid_container"}]
    result = render_diagnostic.certificate_probe("a" * 64)
    assert all(row["diagnostic_failure_class"] == "TimeoutExpired" for row in result)
    assert "PRIVATE" not in json.dumps(result)


def test_resident_probe_is_guarded_before_connecting(monkeypatch) -> None:
    monkeypatch.setenv("LOCAL_DATABASE_URL", "")
    monkeypatch.setenv("REDIS_URL", "")

    def reject():
        raise AssertionError("must reject nonfixture environment before rendering")

    monkeypatch.setattr(render_diagnostic, "resident_probe", reject)
    with pytest.raises(RuntimeError, match="literal disposable endpoints"):
        asyncio.run(driver.run(argparse.Namespace(phase="render-probe")))


def test_startup_diagnostics_keep_only_service_state_and_safe_codes(monkeypatch) -> None:
    monkeypatch.setattr(controller, "compose", lambda *a, **kw: "fixture-container")
    monkeypatch.setattr(
        controller,
        "inspect",
        lambda identifier: {
            "State": {
                "Running": False,
                "ExitCode": 1,
                "OOMKilled": False,
                "Error": "operation not permitted PRIVATE_SHOULD_NOT_ECHO",
                "Health": {"Status": "unhealthy"},
            },
            "RestartCount": 0,
            "Config": {"Env": ["PRIVATE_SHOULD_NOT_ECHO"]},
        },
    )
    monkeypatch.setattr(
        controller.subprocess,
        "run",
        lambda *a, **kw: subprocess.CompletedProcess(
            [],
            0,
            "PRIVATE_SHOULD_NOT_ECHO\n"
            "lightpanda navigation transport code=PeerFailedVerification\n"
            "lightpanda navigation transport code=PRIVATE_SHOULD_NOT_ECHO\n"
            "lightpanda navigation transport code=PeerFailedVerificationPRIVATE\n"
            "2026/10/31 04:00:00 ordinary rich monitor fetch: failed\n"
            "2026/10/31 04:00:00 ordinary rich monitor PRIVATE: failed\n"
            "2026/10/31 04:00:00 ordinary rich monitor fetch: PRIVATE\n",
            "ordinary worker startup stage: ownership\nR2_BUCKET PRIVATE_SHOULD_NOT_ECHO\n"
            + json.dumps(
                {
                    "phase": "detail_failure",
                    "kind": "failed",
                    "cause_phase": "detail_preparation",
                    "secret": "PRIVATE_SHOULD_NOT_ECHO",
                }
            ),
        ),
    )
    rows = controller.startup_diagnostics({}, ["ordinary"])
    assert rows[0]["exit_code"] == 1
    assert rows[0]["navigation_failure_codes"] == {"PeerFailedVerification": 1}
    assert rows[0]["worker_startup_stages"] == ["ownership"]
    assert rows[0]["mentioned_settings"] == ["R2_BUCKET"]
    assert "operation not permitted" in rows[0]["error_categories"]
    assert rows[0]["worker_diagnostic_counts"] == [
        {
            "phase": "detail_failure",
            "kind": "failed",
            "cause_phase": "detail_preparation",
            "count": 1,
        },
        {"phase": "fetch", "kind": "failed", "cause_phase": "", "count": 1},
    ]
    assert "PRIVATE_SHOULD_NOT_ECHO" not in json.dumps(rows)


def test_collect_progress_preserves_counts_without_source_content() -> None:
    expected = {
        "PRIVATE_URL_A": {"profile": "api_rich"},
        "PRIVATE_URL_B": {"profile": "jsonld_direct"},
    }
    result = driver.progress_counts(
        [
            {
                "source_url": "PRIVATE_URL_A",
                "titles": ["PRIVATE_TITLE"],
                "scrape_failures": 0,
                "tdm_reserved": False,
            }
        ],
        [{"source_url": "PRIVATE_URL_A", "r2_uploaded": True, "html": "PRIVATE_BODY"}],
        2,
        expected,
    )
    assert result["expected_postings"] == 2
    assert result["postings"] == result["titled_postings"] == 1
    assert result["description_rows"] == result["uploaded_description_rows"] == 1
    assert result["successful_monitors"] == 2
    assert result["profiles"]["api_rich"]["uploaded"] == 1
    assert result["profiles"]["jsonld_direct"]["postings"] == 0
    assert "PRIVATE_" not in json.dumps(result)


def test_comparison_failure_progress_rejects_extra_fields_and_invalid_counts() -> None:
    result = controller.ComparisonFailure(
        1,
        "NATIVE_LANE_PROGRESS="
        + json.dumps(
            {
                "postings": 12,
                "failed_postings": -1,
                "reserved_postings": True,
                "private": "PRIVATE_SHOULD_NOT_ECHO",
                "profiles": {"api_rich": {"postings": 12, "private": "PRIVATE_SHOULD_NOT_ECHO"}},
            }
        ),
    ).diagnostic
    assert result["progress"]["postings"] == 12
    assert "failed_postings" not in result["progress"]
    assert "reserved_postings" not in result["progress"]
    assert result["progress"]["profiles"]["api_rich"] == {"postings": 12}
    assert "PRIVATE_SHOULD_NOT_ECHO" not in json.dumps(result)


def test_progress_distinguishes_rendered_monitor_attempt_from_upstream_request() -> None:
    result = driver.progress_counts(
        [],
        [],
        0,
        {},
        [
            {
                "board_slug": "dom_rendered",
                "checked": True,
                "succeeded": False,
                "consecutive_failures": 1,
                "last_error": "PRIVATE_SHOULD_NOT_ECHO",
            }
        ],
        {
            "origin-2.lane.bench.test/listing": 1,
            "origin-2.lane.bench.test/jobs/dom_rendered/0": 2,
            "PRIVATE_SHOULD_NOT_ECHO": 10,
        },
    )
    counts = result["profiles"]["dom_rendered"]
    assert counts["monitor_checked"] == counts["monitor_failures"] == 1
    assert counts["monitor_succeeded"] == 0
    assert counts["listing_requests"] == 1
    assert counts["detail_requests"] == 2
    safe = controller.safe_progress("NATIVE_LANE_PROGRESS=" + json.dumps(result))
    assert safe is not None and safe["profiles"]["dom_rendered"] == counts
    assert "PRIVATE_SHOULD_NOT_ECHO" not in json.dumps(safe)


def test_legacy_comparator_trusts_only_fixture_ca_in_private_home(tmp_path, monkeypatch) -> None:
    calls = []
    monkeypatch.setattr(
        legacy_browser.subprocess, "run", lambda args, **kw: calls.append((args, kw))
    )
    home, certificate = tmp_path / "home", tmp_path / "ca.pem"
    legacy_browser.prepare_nss_database(home, certificate)
    database = home / ".pki/nssdb"
    assert stat.S_IMODE(database.stat().st_mode) == 0o700
    assert len(calls) == 2
    assert calls[0][0] == ["certutil", "-d", "sql:" + str(database), "-N", "--empty-password"]
    assert calls[1][0] == [
        "certutil",
        "-d",
        "sql:" + str(database),
        "-A",
        "-n",
        "native-lane-fixture",
        "-t",
        "C,,",
        "-i",
        str(certificate),
    ]
    assert all(
        call[1] == {"check": True, "capture_output": True, "timeout": 10, "umask": 0o077}
        for call in calls
    )


@pytest.mark.parametrize("mode", ["missing", "failure", "success"])
def test_workflow_annotation_exposes_safe_results_without_raw_diagnostics(tmp_path, mode) -> None:
    workflow = (
        Path(__file__).resolve().parents[4] / ".github/workflows/crawler-native-whole-lane.yml"
    )
    steps = yaml.safe_load(workflow.read_text())["jobs"]["measure"]["steps"]
    script = next(
        step["run"] for step in steps if step.get("name") == "Expose bounded comparison report"
    )
    report = tmp_path / "report.json"
    if mode != "missing":
        report.write_text(
            json.dumps(
                {
                    "complete": mode == "success",
                    "failure_class": "RuntimeError",
                    "private": "PRIVATE_SHOULD_NOT_ECHO",
                    "arms": [
                        {
                            "lane": "native",
                            "cleanup": True,
                            "output": {
                                "postings": 536,
                                "canonical_sha256": "1" * 64,
                                "private": "PRIVATE_SHOULD_NOT_ECHO",
                            },
                            "resource": {
                                "cpu_seconds": 12,
                                "duration_seconds": 40,
                                "groups": {
                                    "crawler": {
                                        "mean_cpu_cores": 0.3,
                                        "mean_working_memory_bytes": 1024,
                                        "private": "PRIVATE_SHOULD_NOT_ECHO",
                                    }
                                },
                            },
                            "failure": {
                                "stage": "bootstrap",
                                "class": "ComparisonFailure",
                                "message": "PRIVATE_SHOULD_NOT_ECHO",
                                "diagnostic": {
                                    "exit_code": 1,
                                    "driver_lines": [42],
                                    "error_types": ["RuntimeError"],
                                    "stderr": "PRIVATE_SHOULD_NOT_ECHO",
                                },
                            },
                        }
                    ],
                }
            )
        )
        report.chmod(0o600)
    environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    environment.update(
        REPORT_PATH=str(report),
        FIXTURE_OUTCOME="success",
        IMAGES_OUTCOME="success",
        MEASUREMENT_OUTCOME="failure",
    )
    result = subprocess.run(
        ["/bin/bash", "-e", "-c", script],
        env=environment,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert result.returncode == 0
    level = "notice" if mode == "success" else "error"
    assert result.stdout.startswith("::" + level + " title=Native whole-lane comparison::")
    assert "PRIVATE_SHOULD_NOT_ECHO" not in result.stdout
    summary = json.loads(result.stdout.split("::", 2)[2])
    assert summary["steps"]["MEASUREMENT_OUTCOME"] == "failure"
    assert summary["complete"] == (mode == "success")
    assert len(summary["arms"]) == int(mode != "missing")
    if mode != "missing":
        assert summary["arms"][0]["driver_lines"] == [42]
        assert summary["arms"][0]["postings"] == 536
        assert summary["arms"][0]["resource"]["cpu_seconds"] == 12
        assert summary["arms"][0]["groups"]["crawler"]["mean_cpu_cores"] == 0.3


def test_literal_fixture_environment_loads_real_queue_settings_without_connections() -> None:
    document = yaml.safe_load((Path(__file__).parent / "compose.yml").read_text())
    environment = {key: os.environ[key] for key in ("PATH", "HOME") if key in os.environ}
    environment.update({key: str(value) for key, value in document["x-env"].items()})
    result = subprocess.run(
        [sys.executable, "-c", "from src.redis_queue import close_redis"],
        env=environment,
        capture_output=True,
        timeout=20,
    )
    assert result.returncode == 0, "literal fixture settings could not load"


def test_subprocess_diagnostic_does_not_retain_arbitrary_error_text() -> None:
    text = (
        'Traceback File "/fixtures/admission/native_lane/driver.py", line 123\n'
        "UndefinedColumnError: private-body https://secret.invalid/job/hidden password=hidden"
    )
    error = controller.ComparisonFailure(1, text)
    assert error.diagnostic == {
        "exit_code": 1,
        "error_types": ["UndefinedColumnError"],
        "driver_lines": [123],
    }
    assert "secret" not in str(error)


@pytest.mark.parametrize(
    "dsn,redis",
    [
        ("", ""),
        (driver.DATABASE, "redis://localhost:6379/0"),
        ("postgresql://fixture:fixture@live.invalid/data", driver.REDIS),
    ],
)
def test_driver_rejects_nonfixture_endpoints_before_connecting(monkeypatch, dsn, redis) -> None:
    monkeypatch.setenv("LOCAL_DATABASE_URL", dsn)
    monkeypatch.setenv("REDIS_URL", redis)
    with pytest.raises(RuntimeError, match="literal disposable endpoints"):
        asyncio.run(driver.run(argparse.Namespace(phase="bootstrap", lane="native")))


def test_compose_contains_no_external_runtime_route_or_docker_socket() -> None:
    document = yaml.safe_load((Path(__file__).parent / "compose.yml").read_text())
    assert all(network["internal"] is True for network in document["networks"].values())
    for service in document["services"].values():
        assert "ports" not in service
        assert "env_file" not in service
        assert "network_mode" not in service
        assert all("docker.sock" not in volume for volume in service.get("volumes", []))
        assert service["mem_limit"] == service["memswap_limit"]
    services = document["services"]
    assert int(services["ordinary"]["environment"]["DISCOVERY_CONCURRENCY"]) == 4
    assert (
        sum(
            int(services[name]["environment"]["DISCOVERY_CONCURRENCY"])
            for name in ["legacy-simple", "legacy-browser"]
        )
        == 4
    )
    assert int(services["ordinary"]["environment"]["MONITOR_CONCURRENCY"]) == 2
    assert (
        sum(
            int(services[name]["environment"]["MONITOR_CONCURRENCY"])
            for name in ["legacy-simple", "legacy-browser"]
        )
        == 2
    )
    assert (
        services["ordinary"]["cpus"] + services["renderer"]["cpus"]
        == services["legacy-simple"]["cpus"] + services["legacy-browser"]["cpus"]
    )
    assert set(controller.SUPPORT) == {
        "postgres",
        "redis",
        "typesense",
        "fixture",
        "reaper",
        "exporter",
        "drain",
    }


def test_fixed_service_addresses_cannot_be_allocated_to_bootstrap_containers() -> None:
    document = yaml.safe_load((Path(__file__).parent / "compose.yml").read_text())
    for name, network in document["networks"].items():
        configuration = network["ipam"]["config"][0]
        subnet = ipaddress.IPv4Network(configuration["subnet"])
        automatic = ipaddress.IPv4Network(configuration["ip_range"])
        assert automatic.subnet_of(subnet)
        assert automatic.num_addresses > len(document["services"]) + 2
        for service in document["services"].values():
            connections = service.get("networks", {})
            if (
                isinstance(connections, dict)
                and (connection := connections.get(name))
                and (address := connection.get("ipv4_address"))
            ):
                fixed = ipaddress.IPv4Address(address)
                assert fixed in subnet and fixed not in automatic


def test_renderer_keeps_the_production_privilege_drop_and_child_process_contract() -> None:
    root = Path(__file__).resolve().parents[4]
    production = yaml.safe_load((root / "deploy/lightpanda-renderer/compose.yml").read_text())
    fixture = yaml.safe_load((Path(__file__).parent / "compose.yml").read_text())
    for field in ("user", "cap_add", "cap_drop", "read_only", "pids_limit", "tmpfs"):
        assert fixture["services"]["renderer"][field] == production["services"]["renderer"][field]
    assert (
        "${NATIVE_LANE_PKI:?}/fixture-ca.crt:/etc/ssl/certs/native-lane-fixture.crt:ro"
        in fixture["services"]["renderer"]["volumes"]
    )


def test_pki_matches_the_installed_fixed_renderer_endpoint_and_private_modes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(controller, "run", lambda *args, **kwargs: "")
    directory = tmp_path / "pki"
    pins = controller.pki(directory, "unused-test-image")
    certificate = x509.load_pem_x509_certificate((directory / "server.pem").read_bytes())
    names = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert [str(value) for value in names.get_values_for_type(x509.IPAddress)] == ["10.0.0.5"]
    import hashlib

    assert (
        hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()
        == pins["server-leaf"]
    )
    assert (directory / "server-leaf.sha256").read_text() == pins["server-leaf"] + "\n"
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    public_bundle = directory / "ca-bundle.pem"
    # The renderer mounts this public trust bundle at the OS CA path. Its
    # isolated browser UID differs from the parent credential owner (10001).
    assert public_bundle.stat().st_mode & stat.S_IROTH
    public_root = directory / "fixture-ca.crt"
    assert public_root.read_bytes() == (directory / "ca.pem").read_bytes()
    assert public_root.stat().st_mode & stat.S_IROTH
    root = x509.load_pem_x509_certificate(public_root.read_bytes())
    assert hashlib.sha256(root.public_bytes(serialization.Encoding.DER)).hexdigest() == pins["ca"]
    assert all(
        not path.stat().st_mode & (stat.S_IRGRP | stat.S_IROTH)
        for path in directory.glob("*-key.pem")
    )
    assert all(
        stat.S_IMODE(path.stat().st_mode)
        == {"server-key.pem": 0o400, "ca-bundle.pem": 0o444, "fixture-ca.crt": 0o444}.get(
            path.name, 0o600
        )
        for path in directory.iterdir()
    )


@pytest.mark.parametrize("ca_file", ["ca-bundle.pem", "fixture-ca.crt"])
def test_description_sink_verifies_fixture_tls_identity_and_preserves_bytes(
    tmp_path, monkeypatch, ca_file
) -> None:
    monkeypatch.setattr(controller, "run", lambda *args, **kwargs: "")
    directory = tmp_path / "pki"
    controller.pki(directory, "unused-test-image")
    service = tls_server(
        ("127.0.0.1", 0),
        FixtureState(),
        directory / "fixture.pem",
        directory / "fixture-key.pem",
        storage=True,
    )
    thread = threading.Thread(target=service.serve_forever, daemon=True)
    thread.start()
    context = ssl.create_default_context(cafile=str(directory / ca_file))
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    client = http.client.HTTPSConnection("fixture", service.server_port, context=context, timeout=3)
    monkeypatch.setattr(
        client,
        "_create_connection",
        lambda address, timeout, source_address: socket.create_connection(
            ("127.0.0.1", address[1]), timeout
        ),
    )
    try:
        body = b"<p>Native description over verified TLS.</p>"
        path = "/descriptions/job/fixture/en/latest.html"
        client.request("PUT", path, body)
        response = client.getresponse()
        assert response.status == 200
        response.read()
        client.request("GET", "/report")
        response = client.getresponse()
        assert response.status == 200
        assert json.loads(response.read())["uploaded_objects"] == {
            path: hashlib.sha256(body).hexdigest()
        }
        with (
            socket.create_connection(("127.0.0.1", service.server_port), timeout=3) as sock,
            pytest.raises(ssl.SSLCertVerificationError),
        ):
            context.wrap_socket(sock, server_hostname="untrusted.invalid")
    finally:
        client.close()
        service.shutdown()
        service.server_close()
        thread.join(timeout=3)


def test_resource_cost_uses_actual_completed_work_and_caps_role_share() -> None:
    group = {"crawler": {"mean_cpu_cores": 4, "mean_working_memory_bytes": 0}}
    assert allocation(group, 3600, 1000) == pytest.approx(0.0128)
    assert allocation(group, 3600, 2000) == pytest.approx(0.0064)
    assert allocation({"crawler": {"mean_cpu_cores": 100}}, 3600, 1000) == pytest.approx(0.0256)
    with pytest.raises(ValueError):
        allocation(group, 0, 1000)
    with pytest.raises(ValueError):
        role("undeclared-service")


def test_sampler_includes_support_services_and_excludes_inactive_file_memory(tmp_path) -> None:
    for name, value in {
        "memory.current": "1000",
        "memory.stat": "inactive_file 400\nanon 600\n",
        "cpu.stat": "usage_usec 900000\n",
    }.items():
        (tmp_path / name).write_text(value)
    stop = threading.Event()
    stop.set()
    output = {}
    sample([{"path": tmp_path, "service": "postgres", "cpu_before_usec": 400000}], stop, output)
    assert output["cpu_seconds"] == pytest.approx(0.5)
    assert output["groups"]["postgresql"]["mean_working_memory_bytes"] == pytest.approx(600)
    assert output["retained_working_memory_bytes"] == 600
    assert output["sampled_peak_working_memory_bytes"] == 600
