from __future__ import annotations

import argparse
import asyncio
import hashlib
import http.client
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

from admission.native_lane import controller, driver, legacy_browser
from admission.native_lane.fixture_server import FixtureState, tls_server
from admission.native_lane.resources import allocation, role, sample


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
            "PRIVATE_SHOULD_NOT_ECHO",
            "ordinary worker startup stage: ownership\nR2_BUCKET PRIVATE_SHOULD_NOT_ECHO",
        ),
    )
    rows = controller.startup_diagnostics({}, ["ordinary"])
    assert rows[0]["exit_code"] == 1
    assert rows[0]["worker_startup_stages"] == ["ownership"]
    assert rows[0]["mentioned_settings"] == ["R2_BUCKET"]
    assert "operation not permitted" in rows[0]["error_categories"]
    assert "PRIVATE_SHOULD_NOT_ECHO" not in json.dumps(rows)


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


def test_renderer_keeps_the_production_privilege_drop_and_child_process_contract() -> None:
    root = Path(__file__).resolve().parents[4]
    production = yaml.safe_load((root / "deploy/lightpanda-renderer/compose.yml").read_text())
    fixture = yaml.safe_load((Path(__file__).parent / "compose.yml").read_text())
    for field in ("user", "cap_add", "cap_drop", "read_only", "pids_limit"):
        assert fixture["services"]["renderer"][field] == production["services"]["renderer"][field]


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
    assert all(stat.S_IMODE(path.stat().st_mode) == 0o600 for path in directory.iterdir())


def test_description_sink_verifies_fixture_tls_identity_and_preserves_bytes(
    tmp_path, monkeypatch
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
    context = ssl.create_default_context(cafile=str(directory / "ca-bundle.pem"))
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
