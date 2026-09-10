from __future__ import annotations

import copy
import importlib.util
import json
import re
import shutil
import subprocess
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/lightpanda-renderer"
WORKFLOW = ROOT / ".github/workflows/deploy-lightpanda-renderer.yml"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


verify = load_module("lightpanda_renderer_verify", DEPLOY / "verify.py")
validate_pki = load_module("lightpanda_renderer_pki", DEPLOY / "validate_pki.py")


def release_env() -> dict[str, str]:
    return {
        "RENDERER_IMAGE_REF": "ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:"
        + "a" * 64,
        "RENDERER_RELEASE_DIR": "/home/deploy/.local/share/jobseek-lightpanda/releases/"
        + "sha-"
        + "b" * 40
        + "-r1a1",
        "SOURCE_COMMIT": "b" * 40,
        "RELEASE_ID": "sha-" + "b" * 40 + "-r1a1",
        "CA_DER_SHA256": "c" * 64,
        "SERVER_LEAF_SHA256": "d" * 64,
        "SERVER_SPKI_SHA256": "e" * 64,
        "CLIENT_LEAF_SHA256": "f" * 64,
        "CLIENT_SPKI_SHA256": "1" * 64,
    }


@pytest.fixture
def rendered_compose_model(tmp_path: Path) -> dict[str, object]:
    if shutil.which("docker") is None:
        pytest.skip("Docker Compose is unavailable")
    env = release_env()
    environment = tmp_path / "release.env"
    environment.write_text(
        "".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8"
    )
    result = subprocess.run(
        [
            "docker",
            "compose",
            "--project-name",
            "jobseek-lightpanda",
            "--env-file",
            str(environment),
            "--file",
            str(DEPLOY / "compose.yml"),
            "config",
            "--format",
            "json",
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)  # type: ignore[no-any-return]


def protected_inspect(name: str, service: str) -> dict[str, object]:
    config = {
        "Image": f"example/{service}@sha256:" + "a" * 64,
        "Env": ["SECRET=not-snapshotted"],
        "Labels": {
            "com.docker.compose.project": "deploy",
            "com.docker.compose.service": service,
        },
    }
    host_config = {
        "NetworkMode": "deploy_default",
        "RestartPolicy": {"Name": "unless-stopped", "MaximumRetryCount": 0},
        "Memory": 123,
    }
    return {
        "Name": f"/{name}",
        "Id": ("1" if service == "murmur" else "2") * 64,
        "Image": ("3" if service == "murmur" else "4") * 64,
        "Config": config,
        "HostConfig": host_config,
        "Created": "2026-01-01T00:00:00Z",
        "RestartCount": 0,
        "State": {
            "Running": False,
            "Status": "exited",
            "ExitCode": 0,
            "OOMKilled": False,
            "StartedAt": "2026-01-01T00:00:01Z",
            "FinishedAt": "2026-09-10T00:00:00Z",
        },
        "Mounts": [],
        "NetworkSettings": {"Networks": {}},
    }


def test_inventory_is_exact_canonical_and_dormant(tmp_path: Path) -> None:
    inventory = verify.load_inventory(DEPLOY / "inventory.json")
    assert set(inventory) == verify.INVENTORY_KEYS
    assert inventory["renderer_network"] == "172.30.94.0/29"
    assert "published_port" not in inventory

    changed = copy.deepcopy(inventory)
    changed["renderer_network"] = "172.30.94.0/28"
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(verify.VerificationError):
        verify.load_inventory(path)


def test_compose_model_is_exactly_one_no_egress_renderer(
    rendered_compose_model: dict[str, object],
) -> None:
    model = rendered_compose_model
    inventory = verify.load_inventory(DEPLOY / "inventory.json")
    verify.validate_compose_model(model, release_env(), inventory)
    service = model["services"]["renderer"]  # type: ignore[index]
    assert "ports" not in service
    assert model["networks"]["renderer"]["internal"] is True  # type: ignore[index]


@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("privileged", True),
        ("cap_add", ["NET_ADMIN"]),
        ("network_mode", "host"),
        ("pid", "host"),
        ("environment", {"TOKEN": "secret"}),
        ("env_file", ["/home/deploy/app/.env"]),
        ("devices", ["/dev/kvm"]),
        ("use_api_socket", True),
        ("ports", [{"target": 9443, "published": "9443"}]),
    ],
)
def test_compose_verifier_rejects_extra_authority(
    rendered_compose_model: dict[str, object], key: str, value: object
) -> None:
    model = copy.deepcopy(rendered_compose_model)
    model["services"]["renderer"][key] = value  # type: ignore[index]
    with pytest.raises(verify.VerificationError):
        verify.validate_compose_model(
            model, release_env(), verify.load_inventory(DEPLOY / "inventory.json")
        )


def test_compose_verifier_rejects_release_pin_drift(
    rendered_compose_model: dict[str, object],
) -> None:
    model = copy.deepcopy(rendered_compose_model)
    command = model["services"]["renderer"]["command"]  # type: ignore[index]
    pin_index = command.index("--client-spki-sha256") + 1  # type: ignore[union-attr]
    command[pin_index] = "9" * 64  # type: ignore[index]
    with pytest.raises(verify.VerificationError, match="runtime command|startup command"):
        verify.validate_compose_model(
            model, release_env(), verify.load_inventory(DEPLOY / "inventory.json")
        )


def test_protected_snapshot_is_stopped_exact_and_secret_safe() -> None:
    inspects = [
        protected_inspect("deploy-murmur-1", "murmur"),
        protected_inspect("deploy-cloudflared-1", "cloudflared"),
    ]
    snapshot = verify.protected_snapshot_from_inspects(inspects)
    encoded = json.dumps(snapshot)
    assert "SECRET=not-snapshotted" not in encoded
    murmur = snapshot["containers"]["deploy-murmur-1"]
    assert murmur["restart_policy"] == {
        "Name": "unless-stopped",
        "MaximumRetryCount": 0,
    }
    assert murmur["config_sha256"]
    assert murmur["host_config_sha256"]

    drifted = copy.deepcopy(inspects)
    drifted[0]["HostConfig"]["Memory"] = 456  # type: ignore[index]
    assert verify.protected_snapshot_from_inspects(drifted) != snapshot
    running = copy.deepcopy(inspects)
    running[0]["State"]["Running"] = True  # type: ignore[index]
    running[0]["State"]["Status"] = "running"  # type: ignore[index]
    with pytest.raises(verify.VerificationError):
        verify.protected_snapshot_from_inspects(running)


def test_transaction_is_renderer_scoped_and_contains_no_global_mutation() -> None:
    scripts = "\n".join(
        (DEPLOY / name).read_text(encoding="utf-8")
        for name in ("deploy-remote.sh", "install-host.sh", "lock.sh")
    )
    forbidden = (
        "docker compose down",
        "--remove-orphans",
        "docker system prune",
        "docker image prune",
        "docker network prune",
        "docker volume prune",
        "docker stop deploy-",
        "docker start deploy-",
        "docker rm deploy-",
        "--network host",
        "/var/run/docker.sock",
        "/opt:/",
    )
    for token in forbidden:
        assert token not in scripts
    assert 'docker rm --force "$candidate_container_id"' in scripts
    assert 'docker restart --time 30 "$candidate_container_id"' in scripts
    assert 'docker network rm "$candidate_network_id"' in scripts
    assert 'up --detach --no-deps "$SERVICE"' in scripts
    assert "assert-protected" in scripts
    assert "/home/deploy/.local/share/jobseek-lightpanda" in scripts
    assert "set -o noclobber" in scripts
    assert "/proc/$$/fd/9" in scripts
    assert "JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE=after-active-switch" not in (
        DEPLOY / "deploy-remote.sh"
    ).read_text(encoding="utf-8")
    assert "env -u JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE -u CI -u GITHUB_ACTIONS" in scripts
    assert "trap rollback EXIT\n" in scripts
    assert "trap rollback EXIT HUP INT TERM" not in scripts
    for signal, status in (("HUP", 129), ("INT", 130), ("TERM", 143)):
        assert f"trap 'exit {status}' {signal}" in scripts


def test_workflow_is_manual_exact_main_deploy_with_pr_validation_only() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" in workflow
    assert "\n  push:\n" not in workflow
    assert workflow.count("group: deploy-murmur-shim") == 1
    assert "cancel-in-progress: false" in workflow
    assert "if: github.event_name == 'workflow_dispatch'" in workflow
    assert 'test "$DISPATCH_REF" = refs/heads/main' in workflow
    assert workflow.count("ref: ${{ github.sha }}") == 2
    assert "publish revision differs from the validated dispatch revision" in workflow
    assert "platforms: linux/arm64" in workflow
    assert "HETZNER_MURMUR_KNOWN_HOSTS" in workflow
    assert "LIGHTPANDA_B0_CLIENT_KEY_PEM" not in workflow
    assert "LIGHTPANDA_B0_CLIENT_CERT_PEM" in workflow
    assert "no-egress" in workflow
    assert "crawler run-lightpanda" not in workflow
    assert workflow.count("packages: write") == 1
    assert "needs: publish" in workflow
    assert "JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE: disabled" in workflow
    assert '{{index .Config.Labels \\"' not in workflow


def test_ci_smoke_stages_lock_helper_for_deploy_user() -> None:
    smoke = (DEPLOY / "ci-smoke.sh").read_text(encoding="utf-8")
    assert 'lock_source="$(pwd)' not in smoke
    assert 'lock_source="$ROOT/lock-race-helper.sh"' in smoke
    assert 'deploy/lightpanda-renderer/lock.sh "$ROOT/lock-race-helper.sh"' in smoke
    assert 'sudo -u deploy install -m 0600 "deploy/lightpanda-renderer/' not in smoke
    assert '\npython3 "$PREVIOUS_RELEASE/verify.py"' not in smoke
    assert "\ndocker compose --project-name jobseek-lightpanda" not in smoke
    assert '$(readlink -f "$ROOT/active")' not in smoke
    assert '[[ -e "$first_lock_ready"' not in smoke
    assert '[[ ! -e "$first_lock_ready"' not in smoke
    assert '[[ ! -e "$ROOT"' not in smoke


def test_compose_source_has_no_host_publication_or_external_authority() -> None:
    compose = (DEPLOY / "compose.yml").read_text(encoding="utf-8")
    for token in (
        "ports:",
        "network_mode:",
        "extra_hosts:",
        "environment:",
        "env_file:",
        "privileged:",
        "cap_add:",
        "9222",
        "/var/run/docker.sock",
    ):
        assert token not in compose
    assert "internal: true" in compose
    assert "gateway_mode_ipv4: isolated" in compose
    assert "cgroup: private" in compose
    assert 'restart: "on-failure:3"' in compose
    assert "max-size: 10m" in compose
    assert compose.count("create_host_path: false") == 3


def test_service_builder_is_patch_and_digest_pinned() -> None:
    dockerfile = (ROOT / "pilots/go-lightpanda/Dockerfile").read_text(encoding="utf-8")
    assert re.search(
        r"^ARG GO_IMAGE=golang:1\.24\.7-alpine3\.22@sha256:[0-9a-f]{64}$",
        dockerfile,
        re.MULTILINE,
    )


def test_pki_validator_accepts_only_reviewed_profile(tmp_path: Path) -> None:
    ca_config = DEPLOY / "testdata/ca.cnf"
    leaf_config = DEPLOY / "testdata/leaf.cnf"
    ca_key, ca_cert = tmp_path / "ca-key.pem", tmp_path / "ca.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-new",
            "-x509",
            "-newkey",
            "ec",
            "-pkeyopt",
            "ec_paramgen_curve:P-256",
            "-pkeyopt",
            "ec_param_enc:named_curve",
            "-nodes",
            "-sha256",
            "-days",
            "1095",
            "-config",
            str(ca_config),
            "-keyout",
            str(ca_key),
            "-out",
            str(ca_cert),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    def make_leaf(name: str, section: str) -> tuple[Path, Path]:
        key, csr, cert = (
            tmp_path / f"{name}-key.pem",
            tmp_path / f"{name}.csr",
            tmp_path / f"{name}.pem",
        )
        subprocess.run(
            [
                "openssl",
                "req",
                "-new",
                "-newkey",
                "ec",
                "-pkeyopt",
                "ec_paramgen_curve:P-256",
                "-pkeyopt",
                "ec_param_enc:named_curve",
                "-nodes",
                "-subj",
                f"/CN={name}",
                "-keyout",
                str(key),
                "-out",
                str(csr),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        subprocess.run(
            [
                "openssl",
                "x509",
                "-req",
                "-in",
                str(csr),
                "-CA",
                str(ca_cert),
                "-CAkey",
                str(ca_key),
                "-CAcreateserial",
                "-days",
                "180",
                "-sha256",
                "-extfile",
                str(leaf_config),
                "-extensions",
                section,
                "-out",
                str(cert),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        return key, cert

    server_key, server_cert = make_leaf("server", "server")
    _, client_cert = make_leaf("client", "client")
    pins = validate_pki.validate(ca_cert, server_cert, server_key, client_cert)
    assert set(pins) == {
        "CA_DER_SHA256",
        "SERVER_LEAF_SHA256",
        "SERVER_SPKI_SHA256",
        "CLIENT_LEAF_SHA256",
        "CLIENT_SPKI_SHA256",
    }
    assert all(len(value) == 64 for value in pins.values())

    extra = tmp_path / "server-chain.pem"
    extra.write_bytes(server_cert.read_bytes() + ca_cert.read_bytes())
    with pytest.raises(validate_pki.PKIError):
        validate_pki.validate(ca_cert, extra, server_key, client_cert)
