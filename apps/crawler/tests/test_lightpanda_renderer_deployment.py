from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy/lightpanda-renderer"
WORKFLOW = ROOT / ".github/workflows/deploy-lightpanda-renderer.yml"
BOOTSTRAP_WORKFLOW = ROOT / ".github/workflows/bootstrap-lightpanda-renderer-host.yml"


def load_module(name: str, path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


verify = load_module("lightpanda_renderer_verify", DEPLOY / "verify.py")
validate_pki = load_module("lightpanda_renderer_pki", DEPLOY / "validate_pki.py")
network_policy = load_module("lightpanda_renderer_network_policy", DEPLOY / "network-policy.py")


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
        "NETWORK_POLICY_SHA256": "2" * 64,
        "NETWORK_INVENTORY_SHA256": "3" * 64,
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
    try:
        compose_plugin = verify.compose_plugin_path()
    except verify.VerificationError:
        pytest.skip("trusted system Docker Compose plugin is unavailable")
    env = release_env()
    environment = tmp_path / "release.env"
    environment.write_text(
        "".join(f"{key}={value}\n" for key, value in env.items()), encoding="utf-8"
    )
    result = subprocess.run(
        [
            str(compose_plugin),
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


def install_test_compose_plugin(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    path.chmod(0o755)


def test_compose_system_search_order_is_exact() -> None:
    assert (
        Path("/usr/local/lib/docker/cli-plugins"),
        Path("/usr/local/libexec/docker/cli-plugins"),
        Path("/usr/lib/docker/cli-plugins"),
        Path("/usr/libexec/docker/cli-plugins"),
    ) == verify.SYSTEM_COMPOSE_PLUGIN_DIRECTORIES


def test_compose_resolver_uses_first_trusted_system_candidate(tmp_path: Path) -> None:
    first = tmp_path / "first/cli-plugins/docker-compose"
    second = tmp_path / "second/cli-plugins/docker-compose"
    install_test_compose_plugin(first)
    install_test_compose_plugin(second)

    assert (
        verify.compose_plugin_path(
            (first.parent, second.parent),
            trusted_uid=os.getuid(),
            trusted_gid=os.getgid(),
            trust_boundary=tmp_path,
        )
        == first
    )


def test_compose_resolver_rejects_absent_candidate(tmp_path: Path) -> None:
    with pytest.raises(verify.VerificationError, match="plugin is absent"):
        verify.compose_plugin_path(
            (tmp_path / "missing",),
            trusted_uid=os.getuid(),
            trusted_gid=os.getgid(),
            trust_boundary=tmp_path,
        )


@pytest.mark.parametrize("invalid_kind", ("symlink", "owner", "writable", "not-executable"))
def test_compose_resolver_rejects_untrusted_candidate(tmp_path: Path, invalid_kind: str) -> None:
    candidate = tmp_path / "system/cli-plugins/docker-compose"
    if invalid_kind == "symlink":
        target = tmp_path / "real-compose"
        install_test_compose_plugin(target)
        candidate.parent.mkdir(parents=True)
        candidate.symlink_to(target)
    else:
        install_test_compose_plugin(candidate)
        if invalid_kind == "writable":
            candidate.chmod(0o775)
        elif invalid_kind == "not-executable":
            candidate.chmod(0o644)
    trusted_uid = os.getuid() + 1 if invalid_kind == "owner" else os.getuid()

    with pytest.raises(verify.VerificationError, match="plugin is not trusted"):
        verify.compose_plugin_path(
            (candidate.parent,),
            trusted_uid=trusted_uid,
            trusted_gid=os.getgid(),
            trust_boundary=tmp_path,
        )


def test_compose_resolver_rejects_writable_parent(tmp_path: Path) -> None:
    candidate = tmp_path / "system/cli-plugins/docker-compose"
    install_test_compose_plugin(candidate)
    candidate.parent.chmod(0o775)

    with pytest.raises(verify.VerificationError, match="plugin parent is not trusted"):
        verify.compose_plugin_path(
            (candidate.parent,),
            trusted_uid=os.getuid(),
            trusted_gid=os.getgid(),
            trust_boundary=tmp_path,
        )


@pytest.mark.parametrize("module", (verify, network_policy))
def test_default_route_is_bound_to_observed_egress_mac(module: ModuleType) -> None:
    observed_interfaces: list[str] = []

    def read_mac(interface: str) -> str:
        observed_interfaces.append(interface)
        return "02:42:ac:1e:5e:0a\n"

    module.verify_egress_default_route(
        ["eth7 00000000 095E1EAC 0003 0 0 0 00000000 0 0 0"],
        "172.30.94.9",
        "02:42:AC:1E:5E:0A",
        read_mac,
    )
    assert observed_interfaces == ["eth7"]


@pytest.mark.parametrize(
    ("route_lines", "endpoint_mac", "observed_mac"),
    (
        ([], "02:42:ac:1e:5e:0a", "02:42:ac:1e:5e:0a"),
        (
            [
                "eth0 00000000 095E1EAC 0003",
                "eth1 00000000 095E1EAC 0003",
            ],
            "02:42:ac:1e:5e:0a",
            "02:42:ac:1e:5e:0a",
        ),
        (["eth0 00000000 01020304 0003"], "02:42:ac:1e:5e:0a", "02:42:ac:1e:5e:0a"),
        (["../bad 00000000 095E1EAC 0003"], "02:42:ac:1e:5e:0a", "02:42:ac:1e:5e:0a"),
        (["eth0 00000000 095E1EAC 0003"], "02:42:ac:1e:5e:0a", "02:42:ac:1e:5e:0b"),
    ),
)
@pytest.mark.parametrize(
    ("module", "error_type"),
    (
        (verify, verify.VerificationError),
        (network_policy, network_policy.PolicyError),
    ),
)
def test_default_route_rejects_gateway_interface_or_mac_drift(
    route_lines: list[str],
    endpoint_mac: str,
    observed_mac: str,
    module: ModuleType,
    error_type: type[Exception],
) -> None:
    with pytest.raises(error_type):
        module.verify_egress_default_route(
            route_lines,
            "172.30.94.9",
            endpoint_mac,
            lambda _interface: observed_mac,
        )


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
        "RestartPolicy": {"Name": "no", "MaximumRetryCount": 0},
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


def test_inventory_is_exact_canonical_and_controlled(tmp_path: Path) -> None:
    inventory = verify.load_inventory(DEPLOY / "inventory.json")
    assert set(inventory) == verify.INVENTORY_KEYS
    assert inventory["renderer_network"] == "172.30.94.0/29"
    assert inventory["egress_network"] == "172.30.94.8/29"
    assert inventory["egress_address"] == "172.30.94.10"
    assert inventory["published_address"] == "10.0.0.5"
    assert inventory["published_port"] == 9443

    changed = copy.deepcopy(inventory)
    changed["renderer_network"] = "172.30.94.0/28"
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(changed), encoding="utf-8")
    with pytest.raises(verify.VerificationError):
        verify.load_inventory(path)


def test_nonproduction_inventory_allows_only_explicit_legacy_bridge_fixture(
    tmp_path: Path,
) -> None:
    inventory = verify.load_inventory(DEPLOY / "inventory.json")
    inventory["renderer_bridge_name"] = "br-0123456789ab"
    path = tmp_path / "inventory.json"
    path.write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(verify.VerificationError):
        verify.load_inventory(path)
    inventory["ci_test_only"] = True
    path.write_text(json.dumps(inventory), encoding="utf-8")
    assert verify.load_inventory(path)["renderer_bridge_name"] == "br-0123456789ab"
    assert network_policy.Inventory.load(path).internal_bridge == "br-0123456789ab"
    inventory["ci_test_only"] = False
    path.write_text(json.dumps(inventory), encoding="utf-8")
    with pytest.raises(verify.VerificationError):
        verify.load_inventory(path)
    with pytest.raises(network_policy.PolicyError):
        network_policy.Inventory.load(path)


def test_legacy_fixture_keeps_the_exact_pre_egress_inventory_schema() -> None:
    legacy = json.loads((DEPLOY / "testdata/legacy-inventory.json").read_text(encoding="utf-8"))
    assert set(legacy) == verify.LEGACY_INVENTORY_KEYS
    assert "egress_network" not in legacy
    assert "renderer_bridge_name" not in legacy
    assert verify.load_legacy_inventory(DEPLOY / "testdata/legacy-inventory.json") == legacy


def test_egress_kernel_routes_use_the_modern_highest_address_broadcast() -> None:
    raw = verify.load_inventory(DEPLOY / "inventory.json")
    policy_inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    expected = network_policy.expected_egress_route_identities(policy_inventory)
    assert expected == verify.expected_egress_route_identities(raw)
    assert (
        "local",
        "broadcast",
        "172.30.94.15",
        "link",
        "br-jlp-egress",
        "kernel",
        "172.30.94.9",
        "",
    ) in expected
    assert all(route[2] != "172.30.94.8" for route in expected)


def modern_egress_routes() -> list[dict[str, str]]:
    return [
        {
            "dst": "172.30.94.8/29",
            "dev": "br-jlp-egress",
            "protocol": "kernel",
            "scope": "link",
            "prefsrc": "172.30.94.9",
        },
        {
            "type": "local",
            "dst": "172.30.94.9",
            "dev": "br-jlp-egress",
            "table": "local",
            "protocol": "kernel",
            "scope": "host",
            "prefsrc": "172.30.94.9",
        },
        {
            "type": "broadcast",
            "dst": "172.30.94.15",
            "dev": "br-jlp-egress",
            "table": "local",
            "protocol": "kernel",
            "scope": "link",
            "prefsrc": "172.30.94.9",
        },
    ]


def assert_both_egress_route_verifiers_reject(routes: list[dict[str, str]]) -> None:
    raw = verify.load_inventory(DEPLOY / "inventory.json")
    policy_inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    with pytest.raises(network_policy.PolicyError):
        network_policy.verify_egress_routes(routes, policy_inventory, network_exists=True)
    with pytest.raises(verify.VerificationError):
        verify.verify_egress_kernel_routes(routes, raw)


def test_egress_kernel_routes_reject_obsolete_lowest_address_broadcast() -> None:
    raw = verify.load_inventory(DEPLOY / "inventory.json")
    policy_inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    routes = modern_egress_routes()

    network_policy.verify_egress_routes(routes, policy_inventory, network_exists=True)
    verify.verify_egress_kernel_routes(routes, raw)

    routes.append(
        {
            "type": "broadcast",
            "dst": "172.30.94.8",
            "dev": "br-jlp-egress",
            "table": "local",
            "protocol": "kernel",
            "scope": "link",
            "prefsrc": "172.30.94.9",
        }
    )
    assert_both_egress_route_verifiers_reject(routes)


@pytest.mark.parametrize(
    ("route_index", "field", "value"),
    [
        (0, "table", "100"),
        (0, "type", "blackhole"),
        (0, "dst", "172.30.94.10"),
        (0, "scope", "host"),
        (0, "dev", "br-wrong"),
        (0, "protocol", "static"),
        (0, "prefsrc", "172.30.94.10"),
        (0, "gateway", "172.30.94.1"),
    ],
)
def test_egress_kernel_routes_reject_identity_field_drift(
    route_index: int, field: str, value: str
) -> None:
    routes = modern_egress_routes()
    routes[route_index][field] = value
    assert_both_egress_route_verifiers_reject(routes)


def test_egress_kernel_routes_reject_missing_duplicate_or_absent_network_routes() -> None:
    policy_inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    routes = modern_egress_routes()

    assert_both_egress_route_verifiers_reject(routes[:-1])
    assert_both_egress_route_verifiers_reject([*routes, dict(routes[-1])])
    with pytest.raises(network_policy.PolicyError):
        network_policy.verify_egress_routes(routes, policy_inventory, network_exists=False)


def test_cold_candidate_auth_uses_static_ipam_without_live_endpoints() -> None:
    inventory = verify.load_inventory(DEPLOY / "inventory.json")
    bindings = {
        "9443/tcp": [
            {
                "HostIp": inventory["published_address"],
                "HostPort": str(inventory["published_port"]),
            }
        ]
    }
    settings = {
        "Ports": {"9443/tcp": None},
        "Networks": {
            verify.NETWORK: {
                "IPAMConfig": {"IPv4Address": inventory["renderer_address"]},
                "NetworkID": "",
                "EndpointID": "",
                "IPAddress": "",
            },
            verify.EGRESS_NETWORK: {
                "IPAMConfig": {"IPv4Address": inventory["egress_address"]},
                "NetworkID": "",
                "EndpointID": "",
                "IPAddress": "",
            },
        },
    }
    verify.validate_cold_network_settings(
        settings,
        inventory,
        bindings,
        require_all_network_intents=True,
    )
    stopped_after_quarantine = copy.deepcopy(settings)
    del stopped_after_quarantine["Networks"][verify.EGRESS_NETWORK]  # type: ignore[index]
    verify.validate_cold_network_settings(
        stopped_after_quarantine,
        inventory,
        bindings,
        require_all_network_intents=False,
    )
    settings["Networks"][verify.EGRESS_NETWORK]["IPAMConfig"]["IPv4Address"] = (  # type: ignore[index]
        "172.30.94.11"
    )
    with pytest.raises(verify.VerificationError, match="static network intent"):
        verify.validate_cold_network_settings(
            settings,
            inventory,
            bindings,
            require_all_network_intents=True,
        )


def test_release_policy_binding_rejects_a_stale_expected_digest() -> None:
    policy_digest = "a" * 64
    inventory_digest = "b" * 64
    marker = {
        "policy_sha256": policy_digest,
        "inventory_sha256": inventory_digest,
    }
    network_policy.verify_release_binding(
        marker,
        policy_digest,
        inventory_digest,
        policy_digest,
        inventory_digest,
    )
    with pytest.raises(network_policy.PolicyError, match="artifact digest drifted"):
        network_policy.verify_release_binding(
            marker,
            policy_digest,
            inventory_digest,
            "c" * 64,
            inventory_digest,
        )


def test_compose_model_is_exactly_one_controlled_egress_renderer(
    rendered_compose_model: dict[str, object],
) -> None:
    model = rendered_compose_model
    inventory = verify.load_inventory(DEPLOY / "inventory.json")
    verify.validate_compose_model(model, release_env(), inventory)
    service = model["services"]["renderer"]  # type: ignore[index]
    assert service["ports"] == [  # type: ignore[index]
        {
            "name": "private-mtls",
            "target": 9443,
            "published": "9443",
            "host_ip": "10.0.0.5",
            "protocol": "tcp",
            "app_protocol": "tls",
            "mode": "host",
        }
    ]
    assert set(model["networks"]) == {"renderer", "egress"}  # type: ignore[arg-type]
    assert model["networks"]["renderer"]["external"] is True  # type: ignore[index]
    assert model["networks"]["egress"]["external"] is True  # type: ignore[index]
    assert service["networks"]["egress"]["gw_priority"] == 1  # type: ignore[index]
    assert all(  # type: ignore[union-attr]
        "interface_name" not in network
        for network in service["networks"].values()  # type: ignore[union-attr]
    )


def test_compose_render_failure_retains_bounded_flat_diagnostic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    environment = tmp_path / "release.env"
    environment.write_text(
        "".join(f"{key}={value}\n" for key, value in release_env().items()),
        encoding="utf-8",
    )

    def fail_render(_arguments: list[str]) -> object:
        raise subprocess.CalledProcessError(
            125,
            ["/usr/libexec/docker/cli-plugins/docker-compose"],
            stderr="first line\nsecond line " + "x" * 2048,
        )

    monkeypatch.setattr(
        verify,
        "compose_plugin_path",
        lambda: Path("/usr/libexec/docker/cli-plugins/docker-compose"),
    )
    monkeypatch.setattr(verify, "run_json", fail_render)
    monkeypatch.setattr(
        verify.subprocess,
        "run",
        lambda *_args, **_kwargs: subprocess.CompletedProcess(
            ["/usr/libexec/docker/cli-plugins/docker-compose", "version"],
            0,
            "Docker Compose version v2.39.4\n",
            "",
        ),
    )
    with pytest.raises(
        verify.VerificationError,
        match=(
            r"^Docker Compose model render failed: first line second line x+; "
            r"compose-path=/usr/libexec/docker/cli-plugins/docker-compose; "
            r"compose-version-status=0 compose-version=Docker Compose version v2\.39\.4\Z"
        ),
    ) as raised:
        verify.verify_compose(DEPLOY / "compose.yml", environment, DEPLOY / "inventory.json")
    assert "\n" not in str(raised.value)
    assert len(str(raised.value)) < 1200


@pytest.mark.parametrize("accepted_bind", ({}, {"create_host_path": False}))
def test_compose_verifier_accepts_safe_bind_normalizations(
    rendered_compose_model: dict[str, object],
    accepted_bind: dict[str, object],
) -> None:
    model = copy.deepcopy(rendered_compose_model)
    volumes = model["services"]["renderer"]["volumes"]  # type: ignore[index]
    for volume in volumes:  # type: ignore[union-attr]
        volume["bind"] = accepted_bind
    verify.validate_compose_model(
        model, release_env(), verify.load_inventory(DEPLOY / "inventory.json")
    )


@pytest.mark.parametrize(
    "rejected_bind",
    (
        {"create_host_path": True},
        {"create_host_path": 0},
        {"create_host_path": 0.0},
        {"create_host_path": None},
        {"create_host_path": "false"},
        {"create_host_path": False, "propagation": "rshared"},
    ),
)
def test_compose_verifier_rejects_unsafe_bind_normalizations(
    rendered_compose_model: dict[str, object], rejected_bind: object
) -> None:
    model = copy.deepcopy(rendered_compose_model)
    volumes = model["services"]["renderer"]["volumes"]  # type: ignore[index]
    volumes[0]["bind"] = rejected_bind  # type: ignore[index]
    with pytest.raises(verify.VerificationError, match="credential mount keys"):
        verify.validate_compose_model(
            model, release_env(), verify.load_inventory(DEPLOY / "inventory.json")
        )


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


@pytest.mark.parametrize(
    "capabilities",
    (
        [],
        ["KILL", "SETGID"],
        ["KILL", "SETGID", "SETUID", "NET_ADMIN"],
        ["SETUID", "SETGID", "KILL"],
        ["KILL", "SETGID", "SETUID", "SETUID"],
    ),
)
def test_compose_verifier_rejects_capability_drift(
    rendered_compose_model: dict[str, object], capabilities: list[str]
) -> None:
    model = copy.deepcopy(rendered_compose_model)
    model["services"]["renderer"]["cap_add"] = capabilities  # type: ignore[index]
    with pytest.raises(verify.VerificationError, match="added-capability"):
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
        "Name": "no",
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


def test_idle_process_verifier_accepts_only_root_init_and_uid_10001_controller(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    command = verify.expected_service_command(
        release_env(), verify.load_inventory(DEPLOY / "inventory.json")
    )
    output = "\n".join(
        (
            "PID UID GID COMMAND COMMAND",
            "101 0 0 docker-init "
            + " ".join(["/sbin/docker-init", "--", *verify.BOOTSTRAP_ENTRYPOINT, *command]),
            "102 10001 10001 go-lightpanda " + " ".join(["/usr/local/bin/go-lightpanda", *command]),
        )
    )
    monkeypatch.setattr(verify, "run_text", lambda _: output)
    verify.verify_idle_process_boundary(command)

    monkeypatch.setattr(
        verify, "run_text", lambda _: output + "\n103 10002 10002 lightpanda lightpanda"
    )
    with pytest.raises(verify.VerificationError, match="process count"):
        verify.verify_idle_process_boundary(command)


def test_deploy_is_renderer_scoped_and_host_policy_is_read_only() -> None:
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
    assert "docker network rm" not in scripts
    assert 'install -m 0400 "$STAGE/pki/server-key.pem"' in scripts
    assert "-ceu 'chown 10001:10001 /server-key.pem'" in scripts
    assert "--cap-add FOWNER" not in scripts
    assert 'up --detach --no-deps "$SERVICE"' in scripts
    assert 'sudo -n "$POLICY" verify-ready' in scripts
    assert 'sudo -n "$POLICY" verify-running-ready' in scripts
    deploy_remote = (DEPLOY / "deploy-remote.sh").read_text(encoding="utf-8")
    assert 'sha256sum "$artifact_root/network-policy.py"' in deploy_remote
    assert "NETWORK_POLICY_SHA256=$network_policy_sha256" in deploy_remote
    assert "NETWORK_INVENTORY_SHA256=$network_inventory_sha256" in deploy_remote
    assert "assert-protected" in scripts
    assert "/home/deploy/.local/share/jobseek-lightpanda" in scripts
    assert "os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_CLOEXEC | os.O_NOFOLLOW" in scripts
    assert "os.fchmod(fd, 0o600)" in scripts
    assert "except FileExistsError:" in scripts
    assert "/proc/$$/fd/9" in scripts
    assert "JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE=after-active-switch" not in (
        DEPLOY / "deploy-remote.sh"
    ).read_text(encoding="utf-8")
    assert "env -u JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE -u CI -u GITHUB_ACTIONS" in scripts
    assert "trap rollback EXIT\n" in scripts
    assert "trap rollback EXIT HUP INT TERM" not in scripts
    for signal, status in (("HUP", 129), ("INT", 130), ("TERM", 143)):
        assert f"trap 'exit {status}' {signal}" in scripts


def test_bootstrap_is_monotonic_and_never_manages_protected_containers() -> None:
    bootstrap = (DEPLOY / "bootstrap-host.sh").read_text(encoding="utf-8")
    policy = (DEPLOY / "network-policy.py").read_text(encoding="utf-8")
    unit = (DEPLOY / "jobseek-lightpanda-network.service").read_text(encoding="utf-8")
    sudoers = (DEPLOY / "jobseek-lightpanda-network.sudoers").read_text(encoding="utf-8")
    assert 'exec 9<"$HOST_LOCK"' in bootstrap
    assert 'exec 8<"$RENDERER_LOCK"' in bootstrap
    assert 'install -d -o root -g root -m 0755 "$LOCAL_LIBEXEC"' in bootstrap
    assert "root:root:755" in bootstrap
    assert bootstrap.index("flock -w 900 9") < bootstrap.index("flock -w 300 8")
    assert '"$previous_generation/verify.py" running' in bootstrap
    assert '"$STAGE/verify.py" owned-predecessor' in bootstrap
    assert 'runuser -u deploy -- python3 "$STAGE/verify.py" owned-predecessor' not in bootstrap
    assert 'python3 "$STAGE/verify.py" owned-predecessor' in bootstrap
    assert 'docker stop --time 30 "$previous_id"' in bootstrap
    assert 'docker rm --force "$previous_id"' in bootstrap
    assert "docker start" not in bootstrap
    assert "docker compose" not in bootstrap
    assert "transactions" not in bootstrap
    assert "docker network rm" not in policy
    assert "deploy-murmur-1" not in policy
    assert "deploy-cloudflared-1" not in policy
    assert "ExecStop=" not in unit
    assert "/home/deploy/.local/share/jobseek-lightpanda/renderer.lock" in unit
    assert "/run/lock/jobseek-lightpanda-network.lock" not in unit
    assert " verify-ready" in sudoers
    assert " verify-running-ready" in sudoers
    assert " quarantine" in sudoers


def test_boot_lock_is_recreated_exactly_and_bootstrap_handoff_has_no_self_deadlock() -> None:
    bootstrap = (DEPLOY / "bootstrap-host.sh").read_text(encoding="utf-8")
    tmpfiles = (DEPLOY / "jobseek-lightpanda-network.tmpfiles").read_text(encoding="utf-8")
    unit = (DEPLOY / "jobseek-lightpanda-network.service").read_text(encoding="utf-8")
    assert "f /run/lock/jobseek-lightpanda-network.lock 0640 root deploy -" in tmpfiles
    assert 'systemd-tmpfiles --create "$TMPFILES"' in bootstrap
    assert "os.O_NOFOLLOW" in bootstrap
    assert "os.fchmod(descriptor, mode)" in bootstrap
    assert "os.fchown(descriptor, uid, gid)" in bootstrap
    restart = bootstrap.index("systemctl restart jobseek-lightpanda-network.service")
    renderer_unlock = bootstrap.index("flock -u 8", restart - 600)
    renderer_relock = bootstrap.index('exec 8<"$RENDERER_LOCK"', restart)
    assert renderer_unlock < restart < renderer_relock
    assert "flock -u 9" not in bootstrap
    assert "After=docker.service systemd-tmpfiles-setup.service" in unit
    enable = bootstrap.index("systemctl enable jobseek-lightpanda-network.service")
    enabled = bootstrap.index("systemctl is-enabled --quiet", enable)
    marker = bootstrap.index('python3 - "$MARKER"', enabled)
    assert enabled < bootstrap.index('python3 - "$ENABLEMENT"', enabled) < marker
    assert "for path in (wants_directory, systemd_root):" in bootstrap
    assert "after-enable-before-marker" in bootstrap


def test_deploy_cold_replacement_and_stale_candidate_recovery_are_exact() -> None:
    deploy = (DEPLOY / "install-host.sh").read_text(encoding="utf-8")
    ownership = deploy.index('existing_release_id="$(docker container inspect')
    running_gate = deploy.index('sudo -n "$POLICY" verify-running-ready', ownership)
    stop = deploy.index('docker stop --time 30 "$existing_id"', running_gate)
    remove = deploy.index('docker rm --force "$existing_id"', stop)
    empty_gate = deploy.index('sudo -n "$POLICY" verify-ready', remove)
    compose = deploy.index('"$COMPOSE_PLUGIN" --project-name "$PROJECT"', empty_gate)
    assert ownership < running_gate < stop < remove < empty_gate < compose
    static_inventory = deploy.index('inventory-file "$STAGE/inventory.json"')
    live_inventory = deploy.index('inventory "$STAGE/inventory.json"', remove)
    assert static_inventory < ownership < remove < live_inventory < compose
    drain = deploy.index("stable_empty_renderer_networks", stop)
    assert stop < drain < remove
    assert '"$(readlink -f "$existing_generation")" == "$existing_generation"' in deploy
    assert '"$(stat -c \'%U:%G:%a\' "$existing_generation")" == deploy:deploy:711' in deploy
    assert 'python3 "$existing_generation/verify.py" owned' in deploy
    assert 'sudo -n "$POLICY" quarantine' in deploy
    assert "before-compose" in deploy
    assert "during-compose" in deploy
    assert "crash-after-predecessor-stop" in deploy
    assert "crash-after-compose-create" in deploy
    assert 'create "$SERVICE"' in deploy
    assert 'create --no-deps "$SERVICE"' not in deploy
    assert "docker ps --all --no-trunc --quiet" in deploy
    assert '"$existing_generation/verify.py" owned-created' in deploy
    assert 'policy-digests "$STAGE/release.env"' in deploy
    assert '"$EXPECTED_POLICY_SHA256" "$EXPECTED_INVENTORY_SHA256"' in deploy
    assert "crash-after-candidate" in deploy
    assert "docker start" not in deploy


def test_installed_policy_and_release_artifacts_are_fsynced_before_commit() -> None:
    bootstrap = (DEPLOY / "bootstrap-host.sh").read_text(encoding="utf-8")
    deploy = (DEPLOY / "install-host.sh").read_text(encoding="utf-8")
    for token in (
        '"$GENERATION/network-policy.py"',
        '"$GENERATION/inventory.json"',
        '"$UNIT"',
        '"$SUDOERS"',
        '"$TMPFILES"',
        '"$RELEASE_ROOT"',
    ):
        assert token in bootstrap
    assert bootstrap.index("os.fsync(descriptor)") < bootstrap.index('"$POLICY" verify-ready')
    assert "fsync_files \\\n" in deploy
    assert 'exec 10<"$GENERATION/pki/server-key.pem"' in deploy
    assert deploy.count("stat -Lc '%d:%i' /proc/$$/fd/10") == 2
    assert (
        deploy.index('exec 10<"$GENERATION/pki/server-key.pem"')
        < deploy.index("-ceu 'chown 10001:10001 /server-key.pem'")
        < deploy.index("os.fsync(int(sys.argv[1]))")
        < deploy.index("exec 10<&-")
    )
    fsync_block = deploy[deploy.index("fsync_files \\\n") : deploy.index("fsync_directories")]
    assert '"$GENERATION/pki/server-key.pem"' not in fsync_block
    assert 'fsync_directories "$GENERATION/pki" "$GENERATION" "$RELEASE_ROOT"' in deploy


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
    assert "controlled-egress" in workflow
    assert "crawler run-lightpanda" not in workflow
    assert workflow.count("packages: write") == 1
    assert "needs: publish" in workflow
    assert "JOBSEEK_LIGHTPANDA_CI_FAILURE_MODE: disabled" in workflow
    assert '{{index .Config.Labels \\"' not in workflow


def test_bootstrap_workflow_is_manual_exact_main_and_root_scoped() -> None:
    workflow = BOOTSTRAP_WORKFLOW.read_text(encoding="utf-8")
    assert "workflow_dispatch:" in workflow
    assert "pull_request:" not in workflow
    assert "\n  push:\n" not in workflow
    assert 'test "$DISPATCH_REF" = refs/heads/main' in workflow
    assert "bootstrap-remote.sh" in workflow
    assert workflow.count("group: deploy-murmur-shim") == 1
    assert "cancel-in-progress: false" in workflow


def test_ci_smoke_exercises_legacy_bootstrap_and_cold_rollback() -> None:
    smoke = (DEPLOY / "ci-smoke.sh").read_text(encoding="utf-8")
    bootstrap = (DEPLOY / "bootstrap-host.sh").read_text(encoding="utf-8")
    install = (DEPLOY / "install-host.sh").read_text(encoding="utf-8")
    assert "testdata/legacy-compose.yml" in smoke
    assert "testdata/legacy-verify.py" in smoke
    assert 'bash "$root_stage/bootstrap-host.sh"' in smoke
    assert "after-stop-before-remove" in smoke
    assert "stopped-bootstrap-retry-with-ambiguous-status" in smoke
    assert "durable-enablement-before-marker-fault" in smoke
    assert "durable-enablement-replay-success" in smoke
    assert "empty-policy-replay" in smoke
    assert ".ci_test_only = true" in smoke
    assert 'legacy_bridge="br-${legacy_network_id:0:12}"' in smoke
    assert "reboot-lock-recreation" in smoke
    assert "deterministic-compose-authority" in smoke
    assert (
        'compose_python_version="$(python3 deploy/lightpanda-renderer/verify.py compose-version)"'
    ) in smoke
    assert "safe-lock-metadata-repair" in smoke
    assert "after-candidate-remove-ambiguous" in smoke
    assert "active-switch-cold-rollback" in smoke
    assert "prior-cold-removal-before-compose-failure" in smoke
    assert "candidate-cleanup-during-compose-failure" in smoke
    assert "second-controlled-release-success" in smoke
    assert "stopped-controlled-predecessor" in smoke
    assert "stopped-replay-to-compose-created-candidate" in smoke
    assert "compose-created-candidate-replay-success" in smoke
    assert (
        '[[ "$(sudo -u deploy readlink -f "$ROOT/active")" == '
        '"$CREATED_RECOVERY_RELEASE" ]]' in smoke
    )
    assert "stale-policy-digest-rejection" in smoke
    assert "stale-uncommitted-candidate" in smoke
    assert "stale-candidate-recovery-success" in smoke
    assert "malformed-impostor-rejection" in smoke
    assert 'if docker container inspect "$CONTAINER"' in smoke
    assert 'docker inspect "$CONTAINER"' not in smoke
    assert 'docker inspect "$CONTAINER"' not in bootstrap
    assert 'docker inspect "$CONTAINER"' not in install
    assert "verify-running-ready" in smoke
    assert "private-mtls-ingress" in smoke
    assert "systemd-unit-restart-with-committed-renderer" in smoke
    assert "candidate_transaction_installer" not in smoke


def test_lock_helper_is_fail_closed_without_caller_errexit() -> None:
    lock = (DEPLOY / "lock.sh").read_text(encoding="utf-8")
    assert "python3 - \"$lock_path\" <<'PY' || return 1" in lock
    assert 'exec 9<>"$lock_path" || return 1' in lock
    assert 'fd_identity="$(stat -Lc' in lock
    assert 'path_identity="$(stat -Lc' in lock
    assert lock.count(')" || return 1') == 2


def test_compose_source_has_exact_private_publication_and_external_networks() -> None:
    compose = (DEPLOY / "compose.yml").read_text(encoding="utf-8")
    for token in (
        "network_mode:",
        "extra_hosts:",
        "environment:",
        "env_file:",
        "privileged:",
        "9222",
        "/var/run/docker.sock",
    ):
        assert token not in compose
    assert "host_ip: 10.0.0.5" in compose
    assert 'published: "9443"' in compose
    assert compose.count("external: true") == 2
    assert "185.12.64.1" in compose and "185.12.64.2" in compose
    assert "dormant-controlled-egress" in compose
    assert "cgroup: private" in compose
    assert "cap_drop:\n      - ALL" in compose
    assert "cap_add:\n      - KILL\n      - SETGID\n      - SETUID" in compose
    assert "uid=10002,gid=10002,mode=0700" in compose
    assert 'restart: "on-failure:3"' in compose
    assert "max-size: 10m" in compose
    assert compose.count("create_host_path: false") == 3


def test_policy_rules_have_exact_dns_https_denies_and_terminal_drops() -> None:
    inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    rules = network_policy.policy_rules(inventory, inventory.internal_bridge)
    egress = rules["iptables"][network_policy.V4_EGRESS]
    assert (
        "-d",
        "185.12.64.1/32",
        "-p",
        "udp",
        "-m",
        "udp",
        "--dport",
        "53",
        "-j",
        "ACCEPT",
    ) in egress
    assert ("-p", "tcp", "-m", "tcp", "--dport", "443", "-j", "ACCEPT") in egress
    assert ("-d", "10.0.0.0/8", "-j", "DROP") in egress
    assert ("-d", "178.105.51.62/32", "-j", "DROP") in egress
    assert rules["iptables"][network_policy.V4_EGRESS][-1] == ("-j", "DROP")
    assert rules["iptables"][network_policy.V4_INGRESS][-1] == ("-j", "DROP")


def test_policy_rules_use_iptables_nft_canonical_selector_order() -> None:
    inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    rules = network_policy.policy_rules(inventory, inventory.internal_bridge)["iptables"]

    assert rules[network_policy.V4_INGRESS][1][:6] == (
        "-s",
        "10.0.0.4/32",
        "-d",
        "172.30.94.10/32",
        "-i",
        "enp7s0",
    )
    assert rules[network_policy.V4_FORWARD][0][:4] == (
        "-s",
        "172.30.94.10/32",
        "-i",
        "br-jlp-egress",
    )
    assert rules[network_policy.V4_FORWARD][2][:4] == (
        "-d",
        "172.30.94.10/32",
        "-o",
        "br-jlp-egress",
    )
    assert rules[network_policy.V4_HOST][2][:6] == (
        "-s",
        "10.0.0.4/32",
        "-d",
        "10.0.0.5/32",
        "-i",
        "enp7s0",
    )
    assert rules[network_policy.V4_OUTPUT][0][:4] == (
        "-d",
        "172.30.94.10/32",
        "-o",
        "br-jlp-egress",
    )


def test_verify_ready_requires_fixed_marker_and_both_networks_stably_empty(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    calls: list[object] = []
    monkeypatch.setattr(network_policy.Inventory, "load", lambda _: inventory)
    monkeypatch.setattr(network_policy, "verify_policy", lambda _: {"schema_version": 1})
    monkeypatch.setattr(
        network_policy,
        "verify_bootstrap_marker",
        lambda path, policy, inventory_digest: calls.append((path, policy, inventory_digest)),
    )
    monkeypatch.setattr(
        network_policy,
        "require_networks_stably_empty",
        lambda *names: calls.append(names),
    )
    network_policy.execute_guarded_command(
        argparse.Namespace(
            inventory=network_policy.INVENTORY_PATH,
            command="verify-ready",
            expected_policy_sha256="a" * 64,
            expected_inventory_sha256="b" * 64,
        )
    )
    assert calls == [
        (network_policy.INVENTORY_PATH, "a" * 64, "b" * 64),
        (network_policy.INTERNAL_NETWORK, network_policy.EGRESS_NETWORK),
    ]


def test_policy_container_lookups_cannot_resolve_the_same_named_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def absent_container(
        arguments: list[str], *, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        del check
        calls.append(arguments)
        return subprocess.CompletedProcess(arguments, 1, "", "not found")

    monkeypatch.setattr(network_policy, "run", absent_container)
    assert network_policy.inspect_container(network_policy.CONTAINER) is None
    inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    assert network_policy.exact_running_renderer_id(inventory) is None
    assert calls == [
        ["docker", "container", "inspect", network_policy.CONTAINER],
        ["docker", "container", "inspect", network_policy.CONTAINER],
    ]


@pytest.mark.parametrize(
    ("backend", "server_version", "chain_status", "forward", "accepted"),
    [
        ({"Driver": "iptables"}, "29.0.1", 0, [("-j", "DOCKER-USER")], True),
        (None, "28.5.1", 0, [("-j", "DOCKER-USER")], True),
        ({"Driver": "nftables"}, "29.0.1", 0, [], False),
        ({}, "29.0.1", 0, [], False),
        ("iptables", "29.0.1", 0, [], False),
        (None, "28.5.1", 1, [("-j", "DOCKER-USER")], False),
        (None, "29.0.1", 0, [("-j", "DOCKER-USER")], False),
        (None, "invalid", 0, [("-j", "DOCKER-USER")], False),
        (None, "28.5.1", 0, [], False),
        (None, "28.5.1", 0, [("-j", "OTHER"), ("-j", "DOCKER-USER")], False),
        (
            None,
            "28.5.1",
            0,
            [("-j", "DOCKER-USER"), ("-j", "DOCKER-USER")],
            False,
        ),
    ],
)
def test_backend_preflight_requires_explicit_or_observed_iptables_behavior(
    monkeypatch: pytest.MonkeyPatch,
    backend: object,
    server_version: str,
    chain_status: int,
    forward: list[tuple[str, ...]],
    accepted: bool,
) -> None:
    monkeypatch.setattr(network_policy.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        network_policy,
        "run_json",
        lambda _: {
            "LiveRestoreEnabled": False,
            "FirewallBackend": backend,
            "ServerVersion": server_version,
        },
    )

    def observed(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
        del check
        status = chain_status if "-L" in arguments else 0
        stdout = "iptables v1.8.11 (nf_tables)" if "--version" in arguments else ""
        return subprocess.CompletedProcess(arguments, status, stdout, "")

    monkeypatch.setattr(network_policy, "run", observed)
    monkeypatch.setattr(network_policy, "observed_rules", lambda *_: forward)
    if accepted:
        network_policy.backend_preflight()
    else:
        with pytest.raises(network_policy.PolicyError):
            network_policy.backend_preflight()


def test_running_endpoint_attestation_rejects_extra_routed_identity() -> None:
    inventory = network_policy.Inventory.load(DEPLOY / "inventory.json")
    renderer_id = "a" * 64
    exact = {
        "Containers": {
            renderer_id: {
                "Name": network_policy.CONTAINER,
                "IPv4Address": "172.30.94.10/29",
            }
        }
    }
    network_policy.verify_exact_egress_endpoint(exact, renderer_id, inventory)
    exact["Containers"]["b" * 64] = {  # type: ignore[index]
        "Name": "unexpected",
        "IPv4Address": "172.30.94.11/29",
    }
    with pytest.raises(network_policy.PolicyError, match="not the exact renderer"):
        network_policy.verify_exact_egress_endpoint(exact, renderer_id, inventory)


def test_service_builder_is_patch_and_digest_pinned() -> None:
    dockerfile = (ROOT / "pilots/go-lightpanda/Dockerfile").read_text(encoding="utf-8")
    assert re.search(
        r"^ARG GO_IMAGE=golang:1\.24\.7-alpine3\.22@sha256:[0-9a-f]{64}$",
        dockerfile,
        re.MULTILINE,
    )
    assert 'USER 0:0\nENTRYPOINT ["/usr/bin/setpriv"' in dockerfile
    assert '"--inh-caps=+kill,+setgid,+setuid"' in dockerfile
    assert '"--ambient-caps=+kill,+setgid,+setuid"' in dockerfile
    assert 'CMD ["--runtime-v1-service"]' in dockerfile


def test_service_child_identity_boundary_is_fixed_and_fail_closed() -> None:
    harness = (ROOT / "pilots/go-lightpanda/harness.go").read_text(encoding="utf-8")
    attributes = (ROOT / "pilots/go-lightpanda/process_attributes_linux.go").read_text(
        encoding="utf-8"
    )
    isolation = (ROOT / "pilots/go-lightpanda/child_isolation_linux.go").read_text(encoding="utf-8")
    assert 'lightpandaPrivilegeTrampoline = "/usr/bin/setpriv"' in harness
    assert '"--inh-caps=-all"' in harness
    assert '"--ambient-caps=-all"' in harness
    assert "lightpandaChildUID      = 10002" in attributes
    assert "Groups: []uint32{lightpandaChildGID}" in attributes
    assert 'controllerCapabilitiesHex        = "00000000000000e0"' in isolation
    assert 'fmt.Sprintf("/proc/%d/mem", parentPID)' in isolation
    assert 'fmt.Sprintf("/proc/%d/root%s", parentPID, args[0])' in isolation
    assert "attestNoInheritedFileDescriptors()" in isolation
    assert "flags&syscall.FD_CLOEXEC == 0" in isolation


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
