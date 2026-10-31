from __future__ import annotations

import os
import shlex
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


def test_current_compose_has_no_python_processors_and_retains_native_sync_carrier() -> None:
    services = yaml.safe_load((CRAWLER / "docker-compose.yml").read_text())["services"]
    assert not {"worker-2", "worker-3", "browser-1"} & services.keys()
    carrier = services["worker-1"]
    assert carrier["profiles"] == ["maintenance"]
    assert carrier["restart"] == "no"
    assert carrier["command"] == ["/usr/local/bin/go-typesense-exporter", "--owner"]
    assert carrier["healthcheck"] == {"disable": True}
    assert "worker-1" not in DEPLOY.partition("CRAWLER_STACK_SERVICES=(")[2].partition(")")[0]
    for service in services.values():
        command = service.get("command", [])
        text = command if isinstance(command, str) else " ".join(command)
        assert not any(word in text for word in ("uv run", "python", "playwright", "chromium"))


@pytest.mark.parametrize("selected", [None, "go", "legacy"])
def test_incoming_deploy_is_native_only_while_reversal_remains_separate(
    selected: str | None,
) -> None:
    start = DEPLOY.index('CRAWLER_RUNTIME_BACKEND="${JOBSEEK_CRAWLER_RUNTIME_BACKEND:')
    configuration = DEPLOY[start : DEPLOY.index("MAINTENANCE_OPERATION=", start)]
    environment = {"PATH": os.environ["PATH"]}
    if selected is not None:
        environment["JOBSEEK_CRAWLER_RUNTIME_BACKEND"] = selected
    result = subprocess.run(
        [BASH, "-c", configuration + '\nprintf "%s\\n" "${CRAWLER_STACK_SERVICES[@]}"'],
        env=environment,
        capture_output=True,
        text=True,
    )
    assert (result.returncode == 0) == (selected != "legacy")
    if selected != "legacy":
        assert result.stdout.splitlines() == [
            "redis",
            "ordinary-go",
            "lease-reaper",
            "exporter",
            "drain",
            "alloy",
        ]


@pytest.mark.parametrize(
    ("services", "needs_override"),
    [
        ("ordinary-go\nlease-reaper\nexporter\ndrain\nworker-1", False),
        ("worker-1\nworker-2\nworker-3\nbrowser-1\nexporter\ndrain", True),
        ("ordinary-go\nworker-1\nworker-2\nbrowser-1\nexporter\ndrain", True),
    ],
)
def test_rollback_pool_override_follows_restored_service_inventory(
    tmp_path: Path,
    services: str,
    needs_override: bool,
) -> None:
    environment, compose, pool, calls = (
        tmp_path / x for x in ("runtime.env", "docker-compose.yml", "pool.yml", "calls")
    )
    environment.write_text("RETAINED=fixture\n")
    compose.write_text("services: {}\n")
    pool.write_text("services: {}\n")
    binary = tmp_path / "docker"
    binary.write_text(
        "#!/bin/sh\n"
        'case " $* " in\n'
        f"  *' config --services '*) printf '%s\\n' {shlex.quote(services)};;\n"
        f"  *) printf '%s\\n' \"$@\" > {shlex.quote(str(calls))};;\n"
        "esac\n"
    )
    binary.chmod(0o700)
    script = "\n".join(
        [
            "set -euo pipefail",
            _function("configure_rollback_compose_contract"),
            _function("rollback_compose"),
            "configure_rollback_compose_contract",
            "rollback_compose config --images",
        ]
    )
    result = subprocess.run(
        [BASH, "-c", script],
        capture_output=True,
        text=True,
        env={
            "PATH": str(tmp_path) + ":" + os.environ["PATH"],
            "HOME": str(tmp_path),
            "COMPOSE_PROJECT_NAME": "retirement-fixture",
            "DEPLOY_DIR": str(tmp_path),
            "ENV_FILE": str(environment),
            "ROLLBACK_POOL_OVERRIDE": str(pool),
        },
    )
    assert result.returncode == 0, result.stderr
    expected = str(compose) + (":" + str(pool) if needs_override else "")
    assert environment.read_text() == "RETAINED=fixture\nCOMPOSE_FILE=" + expected + "\n"
    assert (str(pool) in calls.read_text().splitlines()) == needs_override


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
