from __future__ import annotations

import hashlib
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
from cryptography import x509
from cryptography.hazmat.primitives import serialization

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


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _transport_payloads(path: Path) -> dict[str, bytes]:
    with tarfile.open(path, "r:") as transport:
        return {
            member.name: transport.extractfile(member).read()  # type: ignore[union-attr]
            for member in transport.getmembers()
        }


def _write_transport(path: Path, payloads: dict[str, bytes]) -> None:
    import io

    with tarfile.open(path, "w", format=tarfile.USTAR_FORMAT) as transport:
        for name in sorted(bundle._TRANSPORT_FILES):
            payload = payloads[name]
            member = tarfile.TarInfo(name)
            member.size = len(payload)
            member.mode = bundle._TRANSPORT_FILES[name]
            member.uid = 0
            member.gid = 0
            member.mtime = 0
            transport.addfile(member, io.BytesIO(payload))
    path.chmod(0o600)


def _invalidate_certificate_signature(payload: bytes) -> bytes:
    certificate = x509.load_pem_x509_certificate(payload)
    encoded = bytearray(certificate.public_bytes(serialization.Encoding.DER))
    encoded[-1] ^= 1
    invalid = x509.load_der_x509_certificate(bytes(encoded))
    return invalid.public_bytes(serialization.Encoding.PEM)


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
        expected_sha256=_sha256(archive),
        revision="a" * 40,
        root=tmp_path / "generations",
        service_host="10.0.0.5",
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
    assert set(members) == set(bundle._TRANSPORT_FILES)
    assert {
        name: stat.S_IMODE(member.mode) for name, member in members.items()
    } == bundle._TRANSPORT_FILES

    generation = bundle.prepare_generation(
        archive=archive,
        expected_sha256=_sha256(archive),
        revision="b" * 40,
        root=tmp_path / "generations",
        service_host="10.0.0.5",
    )
    assert generation == bundle.prepare_generation(
        archive=archive,
        expected_sha256=_sha256(archive),
        revision="b" * 40,
        root=tmp_path / "generations",
        service_host="10.0.0.5",
    )
    assert generation.name == f"sha-{'b' * 40}-{_sha256(archive)}"
    assert stat.S_IMODE(generation.stat().st_mode) == 0o711
    assert stat.S_IMODE((generation / "client-key.pem").stat().st_mode) == 0o400
    assert {path.name for path in generation.iterdir()} == set(bundle._FILES)
    assert not (generation / "server.pem").exists()


def test_prepare_completes_legal_short_writes(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "claimant.tar"
    root = tmp_path / "generations"
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    archive_sha256 = _sha256(archive)
    original_write = os.write
    write_calls = 0

    def short_write(descriptor: int, payload: bytes | memoryview) -> int:
        nonlocal write_calls
        write_calls += 1
        length = max(1, len(payload) // 2)
        return original_write(descriptor, payload[:length])

    monkeypatch.setattr(bundle.os, "write", short_write)
    generation = bundle.prepare_generation(
        archive=archive,
        expected_sha256=archive_sha256,
        revision="7" * 40,
        root=root,
        service_host="10.0.0.5",
    )

    assert write_calls > len(bundle._FILES)
    bundle._verify_generation(generation, _transport_payloads(archive))


def test_failed_short_write_publishes_nothing_and_allows_retry(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "claimant.tar"
    root = tmp_path / "generations"
    revision = "8" * 40
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    archive_sha256 = _sha256(archive)
    generation = root / f"sha-{revision}-{archive_sha256}"
    original_write = os.write
    write_calls = 0

    def stalled_write(descriptor: int, payload: bytes | memoryview) -> int:
        nonlocal write_calls
        write_calls += 1
        if write_calls == 1:
            length = max(1, len(payload) // 2)
            return original_write(descriptor, payload[:length])
        return 0

    with monkeypatch.context() as stalled:
        stalled.setattr(bundle.os, "write", stalled_write)
        with pytest.raises(bundle.BundleError, match="made no progress"):
            bundle.prepare_generation(
                archive=archive,
                expected_sha256=archive_sha256,
                revision=revision,
                root=root,
                service_host="10.0.0.5",
            )

    assert not generation.exists()
    assert not any(root.glob(".candidate-*"))
    assert generation == bundle.prepare_generation(
        archive=archive,
        expected_sha256=archive_sha256,
        revision=revision,
        root=root,
        service_host="10.0.0.5",
    )


def test_candidate_verification_prevents_truncated_generation_publication(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "claimant.tar"
    root = tmp_path / "generations"
    revision = "6" * 40
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    archive_sha256 = _sha256(archive)
    generation = root / f"sha-{revision}-{archive_sha256}"
    original_write_all = bundle._write_all

    def truncated_write(descriptor: int, payload: bytes) -> None:
        original_write_all(descriptor, payload[:-1])

    with monkeypatch.context() as truncated:
        truncated.setattr(bundle, "_write_all", truncated_write)
        with pytest.raises(bundle.BundleError, match="generation changed"):
            bundle.prepare_generation(
                archive=archive,
                expected_sha256=archive_sha256,
                revision=revision,
                root=root,
                service_host="10.0.0.5",
            )

    assert not generation.exists()
    assert not any(root.glob(".candidate-*"))
    assert generation == bundle.prepare_generation(
        archive=archive,
        expected_sha256=archive_sha256,
        revision=revision,
        root=root,
        service_host="10.0.0.5",
    )


def test_prepare_rejects_post_validation_archive_tampering_before_publication(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
) -> None:
    archive = tmp_path / "claimant.tar"
    root = tmp_path / "generations"
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    validated_sha256 = _sha256(archive)
    archive.write_bytes(archive.read_bytes() + b"post-validation-tampering")

    with pytest.raises(bundle.BundleError, match="does not match CI validation"):
        bundle.prepare_generation(
            archive=archive,
            expected_sha256=validated_sha256,
            revision="e" * 40,
            root=root,
            service_host="10.0.0.5",
        )

    assert not root.exists()


def test_prepare_revalidates_full_pki_and_pins_before_publication(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
) -> None:
    valid = tmp_path / "valid.tar"
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=valid,
    )
    original = _transport_payloads(valid)
    invalid_payloads = {
        "ca-profile": {**original, "ca.pem": original["client.pem"]},
        "client-signature": {
            **original,
            "client.pem": _invalidate_certificate_signature(original["client.pem"]),
        },
        "client-key-match": {
            **original,
            "client-key.pem": claimant_pki["server_key"].read_bytes(),
        },
        "server-pin": {**original, "server-leaf.sha256": b"0" * 64 + b"\n"},
    }

    for case, payloads in invalid_payloads.items():
        archive = tmp_path / f"{case}.tar"
        root = tmp_path / f"{case}-generations"
        _write_transport(archive, payloads)
        with pytest.raises(bundle.BundleError, match="cryptographic validation"):
            bundle.prepare_generation(
                archive=archive,
                expected_sha256=_sha256(archive),
                revision="9" * 40,
                root=root,
                service_host="10.0.0.5",
            )
        assert not root.exists()


def test_validator_root_resolution_supports_exact_container_installer_path(
    tmp_path: Path,
) -> None:
    image_root = tmp_path / "app"
    validator = image_root / "src/lightpanda/credentials.py"
    validator.parent.mkdir(parents=True)
    validator.write_text("# pinned image validator\n", encoding="utf-8")

    assert (
        bundle._resolve_validator_root(
            Path("/installer.py"),
            image_root=image_root,
        )
        == image_root
    )


def test_prepare_retry_accepts_only_sealed_claimant_key_ownership(
    tmp_path: Path,
    claimant_pki: dict[str, Path],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    archive = tmp_path / "claimant.tar"
    root = tmp_path / "generations"
    bundle.build_bundle(
        ca_path=claimant_pki["ca"],
        server_path=claimant_pki["server"],
        client_path=claimant_pki["client"],
        client_key_path=claimant_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    archive_sha256 = _sha256(archive)
    generation = bundle.prepare_generation(
        archive=archive,
        expected_sha256=archive_sha256,
        revision="f" * 40,
        root=root,
        service_host="10.0.0.5",
    )

    # Model finalize's ownership transfer on platforms where an unprivileged
    # test process cannot chown to UID 10001: the inode's real owner becomes
    # the fixed claimant identity and the retry runs as a distinct deploy pair.
    claimant_uid = (generation / "client-key.pem").stat().st_uid
    claimant_gid = (generation / "client-key.pem").stat().st_gid
    monkeypatch.setattr(bundle, "_CLAIMANT_UID", claimant_uid)
    monkeypatch.setattr(bundle, "_CLAIMANT_GID", claimant_gid)
    monkeypatch.setattr(bundle.os, "geteuid", lambda: claimant_uid + 1)
    monkeypatch.setattr(bundle.os, "getegid", lambda: claimant_gid + 1)
    original_read_bytes = Path.read_bytes

    def read_bytes(path: Path) -> bytes:
        if path == generation / "client-key.pem":
            raise PermissionError("sealed claimant key")
        return original_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", read_bytes)

    assert generation == bundle.prepare_generation(
        archive=archive,
        expected_sha256=archive_sha256,
        revision="f" * 40,
        root=root,
        service_host="10.0.0.5",
    )


def test_prepare_retry_rejects_an_unrecognized_private_key_owner(
    installed_generation: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = installed_generation / "client-key.pem"
    metadata = key.stat()
    monkeypatch.setattr(bundle.os, "geteuid", lambda: metadata.st_uid + 1)
    monkeypatch.setattr(bundle.os, "getegid", lambda: metadata.st_gid + 1)
    monkeypatch.setattr(bundle, "_CLAIMANT_UID", metadata.st_uid + 2)
    monkeypatch.setattr(bundle, "_CLAIMANT_GID", metadata.st_gid + 2)

    with pytest.raises(bundle.BundleError, match="private-key ownership changed"):
        bundle._verify_generation(
            installed_generation,
            {path.name: path.read_bytes() for path in installed_generation.iterdir()},
        )


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
        for name, mode in bundle._TRANSPORT_FILES.items():
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
            expected_sha256=_sha256(archive),
            revision="c" * 40,
            root=tmp_path / "generations",
            service_host="10.0.0.5",
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
            expected_sha256="a" * 64,
            revision="d" * 40,
            root=tmp_path / "generations",
            service_host="10.0.0.5",
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
            expected_sha256=_sha256(archive),
            revision="d" * 40,
            root=tmp_path / "generations",
            service_host="10.0.0.5",
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


def test_compose_claimant_is_go_dark_networkless_secretless_and_bounded() -> None:
    model = _compose_model()
    service = model["services"]["lightpanda-claimant"]  # type: ignore[index]
    assert service["network_mode"] == "none"  # type: ignore[index]
    assert service["command"] == ["/usr/local/bin/lightpanda-b0-supervisor"]  # type: ignore[index]
    assert service["user"] == "10001:10001"  # type: ignore[index]
    assert service["read_only"] is True  # type: ignore[index]
    assert service["cap_drop"] == ["ALL"]  # type: ignore[index]
    assert service["security_opt"] == ["no-new-privileges:true"]  # type: ignore[index]
    assert int(service["mem_limit"]) == 128 * 1024 * 1024  # type: ignore[index]
    assert int(service["memswap_limit"]) == 128 * 1024 * 1024  # type: ignore[index]
    assert service["pids_limit"] == 32  # type: ignore[index]
    assert "ports" not in service  # type: ignore[operator]
    assert "depends_on" not in service  # type: ignore[operator]
    environment = service["environment"]  # type: ignore[index]
    assert environment == {"LIGHTPANDA_B0_SUPERVISOR_MODE": "dark"}
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
    assert "volumes" not in service
    assert service["healthcheck"]["test"] == [  # type: ignore[index]
        "CMD",
        "/usr/local/bin/lightpanda-b0-supervisor",
        "--healthcheck",
    ]
    source = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))
    assert "volumes" not in source["services"]["lightpanda-claimant"]


def test_image_and_deploy_validate_the_exact_go_dark_executable() -> None:
    dockerfile = (CRAWLER / "Dockerfile").read_text(encoding="utf-8")
    deploy = DEPLOY.read_text(encoding="utf-8")

    assert "go/lightpanda-b0-supervisor/ go/lightpanda-b0-supervisor/" in dockerfile
    assert "go test ./..." in dockerfile
    assert (
        "COPY --from=lightpanda-b0-build /out/lightpanda-b0-supervisor "
        "/usr/local/bin/lightpanda-b0-supervisor"
    ) in dockerfile
    assert "/usr/local/bin/lightpanda-b0-supervisor --validate-dark" in deploy
    assert "/app/.venv/bin/lightpanda-claimant --validate-only" not in deploy


def test_ci_owns_the_production_go_supervisor_module() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    job = workflow[
        workflow.index("  test-go-b0-supervisor:") : workflow.index("\n  crawler-image:")
    ]

    assert "if: needs.changes.outputs.crawler_code == 'true'" in job
    assert "apps/crawler/go/lightpanda-b0-supervisor/go.mod" in job
    for gate in ("go test ./...", "go test -race ./...", "go vet ./...", "go mod tidy -diff"):
        assert gate in job
    assert 'test -z "$(gofmt -l .)"' in job


def test_credentials_module_imports_without_optional_cryptography_runtime() -> None:
    child = "\n".join(
        (
            "import sys",
            "from pathlib import Path",
            "class BlockCryptography:",
            "    def find_spec(self, fullname, path=None, target=None):",
            "        if fullname == 'cryptography' or fullname.startswith('cryptography.'):",
            "            raise ModuleNotFoundError(f'blocked optional dependency: {fullname}')",
            "        return None",
            "sys.meta_path.insert(0, BlockCryptography())",
            "from src.lightpanda.credentials import (",
            "    ClaimantCredentialPaths, validate_claimant_credential_material,",
            ")",
            "assert not any(name == 'cryptography' or name.startswith('cryptography.')",
            "               for name in sys.modules)",
            "paths = ClaimantCredentialPaths(*(Path(name) for name in (",
            "    'ca.pem', 'client.pem', 'client-key.pem', 'ca.sha256',",
            "    'server-leaf.sha256', 'server-spki.sha256',",
            ")))",
            "assert paths.ca_certificate == Path('ca.pem')",
            "try:",
            "    validate_claimant_credential_material(",
            "        ca_path=Path('ca.pem'), client_path=Path('client.pem'),",
            "        client_key_path=Path('client-key.pem'),",
            "    )",
            "except ModuleNotFoundError as exc:",
            "    assert 'blocked optional dependency' in str(exc)",
            "else:",
            "    raise SystemExit('validation ran without cryptography')",
        )
    )
    result = subprocess.run(
        [sys.executable, "-c", child],
        cwd=CRAWLER,
        check=False,
        capture_output=True,
        text=True,
    )

    assert result.returncode == 0, result.stderr


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


def test_remote_archive_cleanup_is_armed_for_preflight_signals_and_rollback(
    tmp_path: Path,
) -> None:
    revision = "1" * 40
    run_identity = "123-4"
    archive_name = f"lightpanda-claimant-credentials-{revision}-{run_identity}.tar"
    staged_script = tmp_path / "deploy.sh"
    shutil.copy2(DEPLOY, staged_script)
    archive = tmp_path / archive_name
    archive.write_bytes(b"private-key-transport")
    environment = {
        "PATH": os.environ["PATH"],
        "JOBSEEK_DEPLOY_REVISION": revision,
        "JOBSEEK_CLAIMANT_CREDENTIAL_RUN_ID": run_identity,
        "JOBSEEK_CLAIMANT_CREDENTIAL_ARCHIVE_SHA256": "a" * 64,
    }

    preflight = subprocess.run(
        ["bash", str(staged_script)],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert preflight.returncode != 0
    assert not archive.exists()

    source = DEPLOY.read_text(encoding="utf-8")
    prologue = source[
        source.index("# Arm private-key transport cleanup") : source.index(
            "# Serialize deploys with host-scheduled data maintenance"
        )
    ].replace(
        'INCOMING_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"',
        'INCOMING_DIR="$TEST_INCOMING_DIR"',
    )
    archive.write_bytes(b"private-key-transport")
    interrupted = subprocess.run(
        ["bash", "-c", f"set -euo pipefail\n{prologue}\nkill -TERM $$"],
        check=False,
        capture_output=True,
        text=True,
        env={**environment, "TEST_INCOMING_DIR": str(tmp_path)},
    )
    assert interrupted.returncode == 143
    assert not archive.exists()

    rollback = source[source.index("rollback_deploy() {") : source.index("arm_deploy_rollback() {")]
    assert rollback.index("cleanup_claimant_credential_archive") < rollback.index(
        'echo "Deploy failed'
    )


def test_remote_prepare_uses_pinned_networkless_validator_before_publication() -> None:
    source = DEPLOY.read_text(encoding="utf-8")
    prepare = source[
        source.index("prepare_claimant_credential_generation() {") : source.index(
            "select_claimant_credential_generation() {"
        )
    ]
    assert "--network none \\\n" in prepare
    assert "--read-only \\\n" in prepare
    assert "--cap-drop ALL \\\n" in prepare
    assert "--security-opt no-new-privileges:true \\\n" in prepare
    assert '--user "${deploy_uid}:${deploy_gid}" \\\n' in prepare
    assert '"$CRAWLER_IMAGE_REF" \\\n' in prepare
    assert '--mount "type=bind,source=$CLAIMANT_CREDENTIAL_ARCHIVE' in prepare
    assert "target=/transport/claimant-credentials.tar,readonly" in prepare
    assert "--service-host 10.0.0.5" in prepare

    pull = source.index("\npull_deploy_images\n")
    validate_and_publish = source.index("\nprepare_claimant_credential_generation\n", pull)
    finalize = source.index("\nfinalize_claimant_credential_generation\n", validate_and_publish)
    start = source.index('docker compose up -d "${CRAWLER_STACK_SERVICES[@]}"', finalize)
    assert pull < validate_and_publish < finalize < start


def test_workflow_always_removes_exact_run_scoped_remote_archive() -> None:
    workflow = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    steps = workflow["jobs"]["deploy"]["steps"]
    copy = next(step for step in steps if step.get("name") == "Copy claimant credential bundle")
    cleanup = next(
        step for step in steps if step.get("name") == "Remove remote claimant credential bundle"
    )
    deploy = next(step for step in steps if step.get("name") == "Deploy via SSH")

    assert (
        "${{ github.sha }}-${{ github.run_id }}-${{ github.run_attempt }}" in copy["with"]["source"]
    )
    assert cleanup["if"] == "always()"
    assert 'rm -f -- "$archive"' in cleanup["with"]["script"]
    assert (
        deploy["env"]["JOBSEEK_CLAIMANT_CREDENTIAL_ARCHIVE_SHA256"]
        == "${{ steps.claimant-bundle.outputs.archive_sha256 }}"
    )


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
