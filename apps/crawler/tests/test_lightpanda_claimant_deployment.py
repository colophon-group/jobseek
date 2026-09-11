from __future__ import annotations

import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import time
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
CRAWLER = ROOT / "apps/crawler"
COMPOSE = CRAWLER / "docker-compose.yml"
DEPLOY = CRAWLER / "deploy.sh"
WORKFLOW = ROOT / ".github/workflows/deploy-crawler-browser.yml"
BUNDLE_SCRIPT = CRAWLER / "scripts/lightpanda-claimant-credentials.py"


def _load_bundle_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("claimant_credential_bundle", BUNDLE_SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


bundle = _load_bundle_module()


@pytest.fixture
def claimant_pki(tmp_path: Path) -> dict[str, Path]:
    ca_config = ROOT / "deploy/lightpanda-renderer/testdata/ca.cnf"
    leaf_config = ROOT / "deploy/lightpanda-renderer/testdata/leaf.cnf"
    ca_key = tmp_path / "ca-key.pem"
    ca = tmp_path / "ca.pem"
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
            str(ca),
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    result = {"ca": ca, "ca_key": ca_key}
    for name, section in (("server", "server"), ("client", "client")):
        key = tmp_path / f"{name}-key.pem"
        request = tmp_path / f"{name}.csr"
        certificate = tmp_path / f"{name}.pem"
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
                str(request),
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
                str(request),
                "-CA",
                str(ca),
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
                str(certificate),
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        result[f"{name}_key"] = key
        result[name] = certificate
    return result


@pytest.fixture
def installed_generation(tmp_path: Path, claimant_pki: dict[str, Path]) -> Path:
    archive = tmp_path / "claimant.tar"
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    return bundle.prepare_generation(
        archive=archive,
        revision="a" * 40,
        root=tmp_path / "generations",
    )


def test_bundle_is_exact_and_installs_one_immutable_generation(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
) -> None:
    archive = tmp_path / "claimant.tar"
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    with tarfile.open(archive, "r:") as transport:
        members = {member.name: member for member in transport.getmembers()}
    assert set(members) == set(bundle._FILES)
    assert {name: stat.S_IMODE(member.mode) for name, member in members.items()} == bundle._FILES

    generation = bundle.prepare_generation(
        archive=archive,
        revision="b" * 40,
        root=tmp_path / "generations",
    )
    assert generation == bundle.prepare_generation(
        archive=archive,
        revision="b" * 40,
        root=tmp_path / "generations",
    )
    assert generation.name.startswith(f"sha-{'b' * 40}-")
    assert stat.S_IMODE(generation.stat().st_mode) == 0o711
    assert stat.S_IMODE((generation / "client-key.pem").stat().st_mode) == 0o400


@pytest.mark.parametrize(
    ("member_name", "member_mode", "member_type"),
    [
        ("../client-key.pem", 0o400, tarfile.REGTYPE),
        ("client-key.pem", 0o444, tarfile.REGTYPE),
        ("client-key.pem", 0o400, tarfile.SYMTYPE),
    ],
)
def test_installer_rejects_unsafe_path_mode_and_symlink(
    tmp_path: Path,
    member_name: str,
    member_mode: int,
    member_type: bytes,
) -> None:
    archive = tmp_path / "unsafe.tar"
    with tarfile.open(archive, "w") as transport:
        for name, mode in bundle._FILES.items():
            payload = b"x" if name.endswith(".pem") else (b"a" * 64 + b"\n")
            member = tarfile.TarInfo(member_name if name == "client-key.pem" else name)
            member.mode = member_mode if name == "client-key.pem" else mode
            member.type = member_type if name == "client-key.pem" else tarfile.REGTYPE
            member.size = len(payload)
            if member.issym():
                member.linkname = "ca.pem"
                member.size = 0
                transport.addfile(member)
            else:
                import io

                transport.addfile(member, io.BytesIO(payload))
    archive.chmod(0o600)
    with pytest.raises(bundle.BundleError):
        bundle.prepare_generation(
            archive=archive,
            revision="c" * 40,
            root=tmp_path / "generations",
        )


def test_credential_validation_rejects_key_mismatch(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
) -> None:
    with pytest.raises(bundle.BundleError, match="source failed validation"):
        bundle.build_bundle(
            ca_path=claimant_pki["ca"],
            server_path=claimant_pki["server"],
            client_path=claimant_pki["client"],
            client_key_path=claimant_pki["server_key"],
            service_host="10.0.0.5",
            output=tmp_path / "invalid.tar",
        )


def test_installer_rejects_missing_and_exposed_transport(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
) -> None:
    missing = tmp_path / "missing.tar"
    with pytest.raises(bundle.BundleError, match="unavailable"):
        bundle.prepare_generation(
            archive=missing,
            revision="d" * 40,
            root=tmp_path / "generations",
        )

    archive = tmp_path / "claimant.tar"
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    archive.chmod(0o644)
    with pytest.raises(bundle.BundleError, match="mode is unsafe"):
        bundle.prepare_generation(
            archive=archive,
            revision="d" * 40,
            root=tmp_path / "generations",
        )


def _compose_model() -> dict[str, object]:
    docker = shutil.which("docker")
    if docker is None:
        pytest.skip("Docker Compose is unavailable")
    environment = {
        "PATH": os.environ["PATH"],
        "COMPOSE_PROJECT_NAME": "claimant-contract",
        "CRAWLER_IMAGE_REF": f"ghcr.io/colophon-group/jobseek-crawler@sha256:{'a' * 64}",
        "BROWSER_IMAGE_REF": f"ghcr.io/colophon-group/jobseek-crawler-browser@sha256:{'b' * 64}",
        "LIGHTPANDA_B0_CREDENTIAL_DIR": "/credentials",
        "LOCAL_DATABASE_URL": "postgresql://fixture.invalid/jobseek",
        "R2_ACCESS_KEY_ID": "fixture",
        "R2_SECRET_ACCESS_KEY": "fixture",
        "R2_ENDPOINT_URL": "https://fixture.invalid",
        "R2_DOMAIN_URL": "https://fixture.invalid",
        "R2_BUCKET": "fixture",
        "TYPESENSE_HOST": "fixture.invalid",
        "TYPESENSE_PORT": "8108",
        "TYPESENSE_PROTOCOL": "https",
        "TYPESENSE_OPERATIONS_KEY": "fixture",
        "GRAFANA_PROM_URL": "https://fixture.invalid",
        "GRAFANA_PROM_USERNAME": "fixture",
        "GRAFANA_PROM_PASSWORD": "fixture",
        "GRAFANA_LOKI_URL": "https://fixture.invalid",
        "GRAFANA_LOKI_USERNAME": "fixture",
        "GRAFANA_LOKI_PASSWORD": "fixture",
    }
    result = subprocess.run(
        [
            docker,
            "compose",
            "--env-file",
            os.devnull,
            "--project-directory",
            str(CRAWLER),
            "-f",
            str(COMPOSE),
            "config",
            "--format",
            "json",
        ],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_compose_claimant_is_networkless_secretless_and_bounded() -> None:
    model = _compose_model()
    service = model["services"]["lightpanda-claimant"]  # type: ignore[index]
    assert service["network_mode"] == "none"  # type: ignore[index]
    assert service["command"] == ["/app/.venv/bin/lightpanda-claimant"]  # type: ignore[index]
    assert service["user"] == "10001:10001"  # type: ignore[index]
    assert service["read_only"] is True  # type: ignore[index]
    assert service["cap_drop"] == ["ALL"]  # type: ignore[index]
    assert service["security_opt"] == ["no-new-privileges:true"]  # type: ignore[index]
    assert int(service["mem_limit"]) == 512 * 1024 * 1024  # type: ignore[index]
    assert service["pids_limit"] == 32  # type: ignore[index]
    assert "ports" not in service  # type: ignore[operator]
    assert "depends_on" not in service  # type: ignore[operator]
    environment = service["environment"]  # type: ignore[index]
    assert environment["LIGHTPANDA_B0_CLAIMANT_MODE"] == "dark"  # type: ignore[index]
    assert environment["CRAWLER_DB_POOL_MIN"] == "0"  # type: ignore[index]
    assert environment["CRAWLER_DB_POOL_MAX"] == "1"  # type: ignore[index]
    forbidden = (
        "LOCAL_DATABASE_URL",
        "REDIS_URL",
        "R2_",
        "TYPESENSE_",
        "PROXY_",
        "WEBSHARE_",
        "MURMUR_",
    )
    assert not any(
        key == value or key.startswith(value) for key in environment for value in forbidden
    )  # type: ignore[union-attr]
    volumes = service["volumes"]  # type: ignore[index]
    assert len(volumes) == 6  # type: ignore[arg-type]
    assert all(volume["read_only"] is True for volume in volumes)  # type: ignore[union-attr]
    assert all(
        volume.get("bind", {}).get("create_host_path") in (None, False) for volume in volumes
    )  # type: ignore[union-attr]
    source = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    source_volumes = source["services"]["lightpanda-claimant"]["volumes"]
    assert all(volume["bind"]["create_host_path"] is False for volume in source_volumes)


def test_direct_executable_dark_mode_starts_without_socket_or_authority_imports(
    installed_generation: Path,
    tmp_path: Path,
) -> None:
    for name in ("ca.pem", "client.pem", "ca.sha256", "server-leaf.sha256", "server-spki.sha256"):
        (installed_generation / name).chmod(0o444)
    (installed_generation / "client-key.pem").chmod(0o400)
    ready = tmp_path / "ready"
    child = "\n".join(
        (
            "import os, sys",
            "from pathlib import Path",
            "import src.lightpanda.credentials as credentials",
            "import src.lightpanda.entrypoint as entrypoint",
            "root = Path(os.environ['TEST_CREDENTIAL_DIR'])",
            "credentials.CLAIMANT_CA_PATH = root / 'ca.pem'",
            "credentials.CLAIMANT_CLIENT_CERTIFICATE_PATH = root / 'client.pem'",
            "credentials.CLAIMANT_CLIENT_PRIVATE_KEY_PATH = root / 'client-key.pem'",
            "credentials.CLAIMANT_CA_PIN_PATH = root / 'ca.sha256'",
            "credentials.CLAIMANT_SERVER_LEAF_PIN_PATH = root / 'server-leaf.sha256'",
            "credentials.CLAIMANT_SERVER_SPKI_PIN_PATH = root / 'server-spki.sha256'",
            "credentials.CLAIMANT_UID = os.getuid()",
            "credentials.CLAIMANT_GID = os.getgid()",
            "entrypoint._READY_FILE = Path(os.environ['TEST_READY_FILE'])",
            "def audit(event, args):",
            "    if event in {'socket.connect', 'socket.bind'}:",
            "        raise RuntimeError(f'network I/O: {event}')",
            "sys.addaudithook(audit)",
            "status = entrypoint.main()",
            "for name in ('src.lightpanda.claimant', 'src.db', 'src.redis_queue',",
            "             'src.lightpanda_queue', 'asyncpg', 'redis'):",
            "    if name in sys.modules:",
            "        raise SystemExit(f'forbidden import: {name}')",
            "raise SystemExit(status)",
        )
    )
    environment = {
        **os.environ,
        "TEST_CREDENTIAL_DIR": str(installed_generation),
        "TEST_READY_FILE": str(ready),
        "LIGHTPANDA_B0_CLAIMANT_MODE": "dark",
        "LIGHTPANDA_B0_SERVICE_HOST": "10.0.0.5",
        "LIGHTPANDA_B0_CA_CERTIFICATE": str(installed_generation / "ca.pem"),
        "LIGHTPANDA_B0_CLIENT_CERTIFICATE": str(installed_generation / "client.pem"),
        "LIGHTPANDA_B0_CLIENT_PRIVATE_KEY": str(installed_generation / "client-key.pem"),
        "LIGHTPANDA_B0_CA_SHA256_FILE": str(installed_generation / "ca.sha256"),
        "LIGHTPANDA_B0_SERVER_LEAF_SHA256_FILE": str(installed_generation / "server-leaf.sha256"),
        "LIGHTPANDA_B0_SERVER_SPKI_SHA256_FILE": str(installed_generation / "server-spki.sha256"),
        "LIGHTPANDA_B0_QUEUE_NAMESPACE": "production-b0",
        "LIGHTPANDA_B0_SHARD_ID": "lightpanda-b0",
        "LIGHTPANDA_B0_ROUTING_EPOCH": "1",
        "CRAWLER_DB_POOL_MIN": "0",
        "CRAWLER_DB_POOL_MAX": "1",
    }
    process = subprocess.Popen(
        [sys.executable, "-c", child],
        cwd=CRAWLER,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and not ready.exists() and process.poll() is None:
        time.sleep(0.05)
    assert ready.read_text(encoding="ascii") == "lightpanda-b0-dark-ready\n"
    process.terminate()
    stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, f"{stdout}\n{stderr}"
    assert not ready.exists()


def test_deploy_discovers_claimant_only_from_exact_rollback_compose() -> None:
    script = DEPLOY.read_text(encoding="utf-8")
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]
    stop = rollback.index("candidate_compose stop --timeout 30 lightpanda-claimant")
    restore = rollback.index("restore_previous_deploy_specs")
    discover = rollback.index("rollback_compose_defines_service lightpanda-claimant")
    start = rollback.index('rollback_compose up -d "${rollback_stack_services[@]}"')
    wait = rollback.index("wait_for_rollback_claimant")
    verify = rollback.index("verify_rollback_claimant_image")
    assert stop < restore < discover < start < wait < verify
    assert "rollback_stack_services+=(lightpanda-claimant)" in rollback
    assert "lightpanda-claimant" not in (CRAWLER / "rollback-pool-budget.override.yml").read_text()


def test_later_rollout_rollback_restores_claimant_when_old_compose_defines_it(
    tmp_path: Path,
) -> None:
    script = DEPLOY.read_text(encoding="utf-8")
    rollback = script[script.index("rollback_deploy() {") : script.index("arm_deploy_rollback() {")]
    harness = "\n".join(
        (
            "set -u",
            f'DEPLOY_DIR="{tmp_path}"',
            'ENV_FILE="$DEPLOY_DIR/.env"',
            'ROLLBACK_ENV_FILE="$DEPLOY_DIR/.env.rollback"',
            'ROLLBACK_SPEC_ARCHIVE="$DEPLOY_DIR/.deploy-spec.rollback.tar"',
            'ROLLBACK_ACTIVE_RELEASE_TARGET="$DEPLOY_DIR/old-release"',
            'ROLLBACK_ACTIVE_IMAGE_OVERRIDE=""',
            "ENV_FILE_WAS_PRESENT=1",
            "ROLLBACK_ARMED=1",
            "ROLLBACK_RUNNING=0",
            "MIGRATION_CUTOVER_REACHED=0",
            "FORWARD_SYNC_STARTED=0",
            'STAGED_BRIDGE_VERIFIER=""',
            'TEST_LOG="$DEPLOY_DIR/events.log"',
            "candidate_compose_defines_service() {",
            "  printf 'candidate-discover\\n' >>\"$TEST_LOG\"",
            "  return 0",
            "}",
            "candidate_compose() {",
            '  printf \'candidate:%s\\n\' "$*" >>"$TEST_LOG"',
            "}",
            "rollback_compose_defines_service() {",
            "  printf 'rollback-discover\\n' >>\"$TEST_LOG\"",
            "  return 0",
            "}",
            "rollback_compose() {",
            '  printf \'rollback:%s\\n\' "$*" >>"$TEST_LOG"',
            "}",
            "activate_release_generation() { :; }",
            "verify_active_deploy_snapshot() { :; }",
            "restore_previous_deploy_specs() { :; }",
            "configure_rollback_compose_contract() { :; }",
            "wait_for_rollback_core_services() { printf 'core-ready\\n' >>\"$TEST_LOG\"; }",
            "wait_for_rollback_claimant() { printf 'claimant-ready\\n' >>\"$TEST_LOG\"; }",
            "verify_rollback_claimant_image() { printf 'claimant-image\\n' >>\"$TEST_LOG\"; }",
            "remove_previously_absent_deploy_specs() { :; }",
            "publish_legacy_success_marker() { :; }",
            "stop_maintenance_window() { :; }",
            rollback,
            "rollback_deploy 23",
        )
    )
    (tmp_path / ".env").write_text("candidate\n", encoding="utf-8")
    (tmp_path / ".env.rollback").write_text("old\n", encoding="utf-8")
    result = subprocess.run(
        ["bash", "-c", harness],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 23, result.stderr
    events = (tmp_path / "events.log").read_text(encoding="utf-8").splitlines()
    assert events == [
        "candidate-discover",
        "candidate:stop --timeout 30 lightpanda-claimant",
        "candidate:stop --timeout 60 worker-1 worker-2 worker-3 browser-1 exporter drain",
        "rollback-discover",
        (
            "rollback:up -d redis worker-1 worker-2 worker-3 browser-1 exporter drain alloy "
            "lightpanda-claimant"
        ),
        "core-ready",
        "claimant-ready",
        "claimant-image",
    ]


def test_workflow_transports_bundle_not_claimant_credentials_to_ssh() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    ssh = next(step for step in steps if step.get("name") == "Deploy via SSH")
    assert not any("LIGHTPANDA" in name for name in ssh["with"]["envs"].split(","))
    deploy_source = DEPLOY.read_text(encoding="utf-8")
    marker = "# ── Write env file"
    start = deploy_source.index('cat > "$ENV_FILE" <<EOF', deploy_source.index(marker))
    persisted = deploy_source[start : deploy_source.index("\nEOF", start)]
    assert "_PEM" not in persisted
    assert "LIGHTPANDA_B0_CREDENTIAL_DIR" in persisted


def test_claimant_core_and_runtime_v1_contract_are_unchanged() -> None:
    diff = subprocess.run(
        [
            "git",
            "diff",
            "--name-only",
            "b79b5b0e8b0d822239cc09439594c36798397fd7",
            "--",
            "apps/crawler/src/lightpanda/claimant.py",
            "apps/crawler/contracts/v1",
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert diff.stdout == ""
