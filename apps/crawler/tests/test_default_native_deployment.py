from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest
import yaml

CRAWLER = Path(__file__).resolve().parents[1]
DEPLOY = (CRAWLER / "deploy.sh").read_text()
TOOLS = (CRAWLER / "scripts/native-deploy-tools.sh").read_text()
IMAGE = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "a" * 64
BASH = "/opt/homebrew/bin/bash" if Path("/opt/homebrew/bin/bash").exists() else "bash"


def _function(name: str) -> str:
    start = DEPLOY.index(name + "() {")
    return DEPLOY[start : DEPLOY.index("\n}\n", start) + 3]


@pytest.mark.parametrize(
    ("capability", "status", "expected"),
    [
        ("v1", 0, "go"),
        ("<no value>", 0, "legacy"),
        ("", 0, "legacy"),
        ("v2", 0, None),
        ("v1", 1, None),
    ],
)
def test_runtime_topology_requires_known_immutable_image_capability(
    capability: str,
    status: int,
    expected: str | None,
) -> None:
    script = (
        TOOLS
        + "\n"
        + (
            'docker() { printf "%s\\n" "$CAPABILITY"; return "$STATUS"; }\n'
            'crawler_image_runtime_backend "$IMAGE"\n'
        )
    )
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CAPABILITY": capability, "STATUS": str(status), "IMAGE": IMAGE},
    )
    if expected is None:
        assert result.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert result.stdout.strip() == expected
    mutable = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "CAPABILITY": capability, "STATUS": "0", "IMAGE": "crawler:latest"},
    )
    assert mutable.returncode != 0
    assert not mutable.stdout


@pytest.mark.parametrize(
    ("previous", "response", "expected_epoch", "expected_command"),
    [
        ("legacy", '{"routing_epoch":457}', "457", "reserve-epoch"),
        ("go", '{"current":true,"routing_epoch":456}', "456", "attest-epoch"),
        ("go", '{"current":false,"routing_epoch":456}', None, "attest-epoch"),
        ("go", '{"current":true,"routing_epoch":457}', None, "attest-epoch"),
        ("legacy", '{"routing_epoch":0}', None, "reserve-epoch"),
    ],
)
def test_default_epoch_uses_original_allocator_and_preserves_go_rollback_fence(
    tmp_path: Path,
    previous: str,
    response: str,
    expected_epoch: str | None,
    expected_command: str,
) -> None:
    prior, incoming, calls = (tmp_path / x for x in ("prior.env", "incoming.env", "calls"))
    prior.write_text(f"CRAWLER_IMAGE_REF={IMAGE}\nORDINARY_OWNERSHIP_ROUTING_EPOCH=456\n")
    incoming.write_text("ORDINARY_OWNERSHIP_ROUTING_EPOCH=\nOTHER=retained\n")
    script = "\n".join(
        [
            "set -euo pipefail",
            TOOLS,
            _function("read_exact_release_value"),
            _function("prepare_default_go_epoch"),
            "CRAWLER_RUNTIME_BACKEND=go",
            "MAINTENANCE_PROVENANCE_LABELS=()",
            'docker() { if [[ "$1" == image ]]; then '
            '[[ "$PREVIOUS" != go ]] || printf "v1\\n"; '
            'else printf "%s\\n" "$*" >>"$CALLS"; printf "%s\\n" "$RESPONSE"; fi; }',
            "prepare_default_go_epoch",
        ]
    )
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        check=False,
        env={
            **os.environ,
            "ROLLBACK_ENV_FILE": str(prior),
            "ENV_FILE": str(incoming),
            "CRAWLER_IMAGE_REF": IMAGE,
            "PREVIOUS": previous,
            "RESPONSE": response,
            "CALLS": str(calls),
        },
    )
    assert calls.exists(), result.stderr
    assert expected_command in calls.read_text()
    if previous == "go":
        assert "reserve-epoch" not in calls.read_text()
    if expected_epoch is None:
        assert result.returncode != 0
        assert incoming.read_text() == "ORDINARY_OWNERSHIP_ROUTING_EPOCH=\nOTHER=retained\n"
    else:
        assert result.returncode == 0, result.stderr
        assert (
            incoming.read_text()
            == f"ORDINARY_OWNERSHIP_ROUTING_EPOCH={expected_epoch}\nOTHER=retained\n"
        )
        assert incoming.stat().st_mode & 0o777 == 0o600


def test_production_build_and_explicit_reversal_use_complete_release_workflow() -> None:
    workflow = yaml.safe_load(
        (CRAWLER.parents[1] / ".github/workflows/deploy-crawler-browser.yml").read_text()
    )
    jobs = workflow["jobs"]
    builds = [x for x in jobs["build"]["steps"] if "docker/build-push-action@" in x.get("uses", "")]
    assert len(builds) == 1
    assert builds[0]["with"]["target"] == "native"
    assert "jobseek-crawler-browser:" in builds[0]["with"]["tags"]
    assert jobs["build"]["outputs"]["slim_digest"] == jobs["build"]["outputs"]["browser_digest"]
    deploy = next(x for x in jobs["deploy"]["steps"] if x.get("name") == "Deploy via SSH")
    assert deploy["env"]["JOBSEEK_CRAWLER_RUNTIME_BACKEND"] == "go"
    assert "JOBSEEK_EXERCISE_COLD_ROLLBACK" in deploy["with"]["envs"].split(",")
    assert jobs["promote"]["if"] == "${{ !inputs.exercise_cold_rollback }}"
    exercise = DEPLOY.index('if [[ "$EXERCISE_COLD_ROLLBACK" == true ]]; then')
    assert DEPLOY.rindex("\nverify_deployed_image_identity\n") < exercise
    assert exercise < DEPLOY.rindex("\npublish_active_deploy_release ")
    assert "rollback_deploy 0" in DEPLOY[exercise : exercise + 250]
    syntax = subprocess.run(["bash", "-n", str(CRAWLER / "deploy.sh")], capture_output=True)
    assert syntax.returncode == 0


@pytest.mark.parametrize("owner", ["python", "go", "unknown"])
def test_native_exporter_requires_go_owner(tmp_path: Path, owner: str) -> None:
    binary = tmp_path / "go-typesense-exporter"
    binary.write_text(
        "#!/bin/sh\n"
        'case "$1" in\n'
        '  --owner) printf "%s\\n" "$TEST_OWNER" ;;\n'
        '  --run) printf "enabled=%s\\n" "$GO_TYPESENSE_EXPORTER_ENABLE" ;;\n'
        "  *) exit 99 ;;\n"
        "esac\n"
    )
    binary.chmod(0o700)
    source = (CRAWLER / "scripts/native-exporter-entrypoint.sh").read_text()
    source = source.replace("/usr/local/bin/go-typesense-exporter", str(binary))
    try:
        result = subprocess.run(
            ["sh", "-c", source],
            env={"PATH": os.environ["PATH"], "TEST_OWNER": owner},
            capture_output=True,
            text=True,
            check=False,
        )
        if owner == "go":
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == "enabled=1"
        else:
            assert result.returncode == 2
            assert not result.stdout
    finally:
        binary.unlink()


def test_native_entrypoint_and_preflight_are_in_the_build_context() -> None:
    dockerfile = (CRAWLER / "Dockerfile").read_text()
    ignores = (CRAWLER / ".dockerignore").read_text().splitlines()
    assert "!scripts/native-exporter-entrypoint.sh" in ignores
    native = dockerfile.split(" AS native\n", 1)[1].split("\nFROM ", 1)[0]
    assert "COPY --chmod=0755 scripts/native-exporter-entrypoint.sh" in native
    assert "COPY --from=lightpanda-b0-build /out/lightpanda-b0-supervisor" in native
    assert "lightpanda-b0-supervisor --validate-dark" in DEPLOY
