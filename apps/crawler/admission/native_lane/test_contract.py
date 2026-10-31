from __future__ import annotations

import argparse
import asyncio
import json
import os
import stat
import subprocess
import sys
import threading
from pathlib import Path

import pytest
import yaml
from cryptography import x509
from cryptography.hazmat.primitives import serialization

from admission.native_lane import controller, driver
from admission.native_lane.resources import allocation, role, sample


@pytest.mark.parametrize("report_exists", [False, True])
def test_workflow_failure_annotation_excludes_raw_diagnostics(tmp_path, report_exists) -> None:
    workflow = (
        Path(__file__).resolve().parents[4] / ".github/workflows/crawler-native-whole-lane.yml"
    )
    steps = yaml.safe_load(workflow.read_text())["jobs"]["measure"]["steps"]
    script = next(
        step["run"] for step in steps if step.get("name") == "Expose bounded comparison failure"
    )
    report = tmp_path / "report.json"
    if report_exists:
        report.write_text(
            json.dumps(
                {
                    "failure_class": "RuntimeError",
                    "private": "PRIVATE_SHOULD_NOT_ECHO",
                    "arms": [
                        {
                            "lane": "native",
                            "cleanup": True,
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
    assert result.stdout.startswith("::error title=Native whole-lane comparison::")
    assert "PRIVATE_SHOULD_NOT_ECHO" not in result.stdout
    summary = json.loads(result.stdout.split("::", 2)[2])
    assert summary["steps"]["MEASUREMENT_OUTCOME"] == "failure"
    assert len(summary["arms"]) == int(report_exists)
    if report_exists:
        assert summary["arms"][0]["driver_lines"] == [42]


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


def test_pki_matches_the_installed_fixed_renderer_endpoint_and_private_modes(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.setattr(controller, "run", lambda *args, **kwargs: controller.CA_PEM.decode())
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
