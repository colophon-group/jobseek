"""Safety contracts for the production maintenance wrapper."""

from __future__ import annotations

import argparse
import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT_PATH = ROOT / "scripts/jobseek-maintenance.py"
SPEC = importlib.util.spec_from_file_location("jobseek_maintenance", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
maintenance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = maintenance
SPEC.loader.exec_module(maintenance)


def _provenance():
    return maintenance.Provenance(
        operation="repair-relisted-cdc",
        issue=6016,
        revision="a" * 40,
        budget_seconds=1800,
    )


def test_oneoff_command_injects_exact_validated_labels():
    command, name = maintenance.build_oneoff_command(
        [
            "docker",
            "run",
            "--rm",
            "--name",
            "repair-relisted",
            "--env-file",
            "/run/maintenance.env",
            "crawler-image",
            "crawler",
            "repair-relisted-cdc",
        ],
        _provenance(),
    )

    assert name == "repair-relisted"
    assert command[:2] == ["docker", "run"]
    labels = [command[index + 1] for index, token in enumerate(command) if token == "--label"]
    assert labels == [
        "com.docker.compose.project=deploy",
        "com.docker.compose.service=repair-relisted-cdc",
        "com.docker.compose.container-number=1",
        "com.docker.compose.oneoff=True",
        "jobseek.maintenance.operation=repair-relisted-cdc",
        "jobseek.maintenance.issue=6016",
        f"jobseek.maintenance.revision={'a' * 40}",
        "jobseek.maintenance.budget-seconds=1800",
    ]


@pytest.mark.parametrize(
    "command,error",
    [
        (["bash", "repair.sh"], "explicit docker run"),
        (["docker", "run", "--name", "repair", "image"], "must begin with --rm"),
        (["docker", "run", "--rm", "image"], "stable-name"),
        (
            ["docker", "run", "image", "--rm", "--name", "container-argument"],
            "must begin with --rm",
        ),
        (
            [
                "docker",
                "run",
                "--rm",
                "--name",
                "repair",
                "--label",
                "jobseek.maintenance.issue=999",
                "image",
            ],
            "wrapper-owned",
        ),
        (
            [
                "docker",
                "run",
                "--rm",
                "--name",
                "repair",
                "--label=com.docker.compose.project=other",
                "image",
            ],
            "wrapper-owned",
        ),
    ],
)
def test_oneoff_contract_rejects_unbounded_or_spoofed_commands(command, error):
    with pytest.raises(maintenance.MaintenanceError, match=error):
        maintenance.build_oneoff_command(command, _provenance())


def test_invalid_provenance_fails_before_docker_access():
    args = argparse.Namespace(
        operation="Repair With Spaces",
        issue=6016,
        revision="short",
        budget_seconds=1800,
    )

    with pytest.raises(maintenance.MaintenanceError, match="lowercase maintenance slug"):
        maintenance._validate_provenance(args)


def test_wrapper_summary_never_prints_the_command_or_environment(monkeypatch, capsys):
    args = argparse.Namespace(
        command=[
            "docker",
            "run",
            "--rm",
            "--name",
            "repair-relisted",
            "--env",
            "TOKEN=must-not-leak",
            "crawler-image",
            "crawler",
            "repair-relisted-cdc",
        ],
        grace_seconds=90,
        expect_service=["worker-1"],
    )
    monkeypatch.setattr(
        maintenance,
        "_run_bounded",
        lambda *_args, **_kwargs: maintenance.CommandResult(0, False, False, 12.5),
    )
    monkeypatch.setattr(maintenance, "_cleanup_container", lambda _name: None)
    monkeypatch.setattr(maintenance, "_restoration_failures", lambda _services: [])

    assert maintenance._run_oneoff(args, _provenance()) == 0

    output = capsys.readouterr().out
    assert "status=completed" in output
    assert "must-not-leak" not in output
    assert "crawler repair-relisted-cdc" not in output


def test_oneoff_cleanup_runs_when_the_bounded_command_fails_to_start(monkeypatch):
    args = argparse.Namespace(
        command=["docker", "run", "--rm", "--name", "repair-relisted", "crawler-image"],
        grace_seconds=90,
        expect_service=None,
    )
    cleaned = []
    monkeypatch.setattr(
        maintenance,
        "_run_bounded",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            maintenance.MaintenanceError("failed to start")
        ),
    )
    monkeypatch.setattr(maintenance, "_cleanup_container", cleaned.append)

    with pytest.raises(maintenance.MaintenanceError, match="failed to start"):
        maintenance._run_oneoff(args, _provenance())

    assert cleaned == ["repair-relisted"]


def test_window_marker_is_stopped_and_restoration_failure_is_nonzero(monkeypatch, capsys):
    args = argparse.Namespace(
        command=["/home/deploy/maintenance/repair.sh"],
        grace_seconds=90,
        expect_service=None,
    )
    stopped = []
    monkeypatch.setattr(maintenance, "_start_window_marker", lambda _provenance: "marker")
    monkeypatch.setattr(
        maintenance,
        "_run_bounded",
        lambda *_args, **_kwargs: maintenance.CommandResult(0, False, False, 5.0),
    )
    monkeypatch.setattr(maintenance, "_restoration_failures", lambda _services: ["worker-2"])
    monkeypatch.setattr(maintenance, "_stop_window_marker", stopped.append)

    assert maintenance._run_window(args, _provenance()) == 70
    assert stopped == ["marker"]
    assert "status=restoration-failed" in capsys.readouterr().out


def test_self_test_runs_without_docker_access():
    result = subprocess.run(
        [sys.executable, str(SCRIPT_PATH), "--self-test"],
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "maintenance provenance self-test passed"
