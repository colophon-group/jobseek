from __future__ import annotations

import hashlib
import importlib.util
import shutil
import subprocess
from pathlib import Path

import pytest

CRAWLER = Path(__file__).resolve().parents[1]


def deployment_reference():
    spec = importlib.util.spec_from_file_location(
        "native_claimant_deployment_reference",
        CRAWLER / "tests/test_lightpanda_claimant_deployment.py",
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def original_pki(tmp_path: Path) -> dict[str, Path]:
    factory = deployment_reference().claimant_pki.__wrapped__
    return factory(tmp_path)


@pytest.fixture(scope="session")
def native_installer(tmp_path_factory: pytest.TempPathFactory) -> Path:
    go = shutil.which("go")
    if go is None:
        pytest.skip("native credential equivalence requires Go")
    assert go is not None
    binary = tmp_path_factory.mktemp("native-claimant") / "installer"
    subprocess.run(
        [go, "build", "-o", str(binary), "./cmd/credentials"],
        cwd=CRAWLER / "go/lightpanda-b0-supervisor",
        check=True,
        capture_output=True,
    )
    return binary


def reference_bundle():
    spec = importlib.util.spec_from_file_location(
        "native_claimant_reference", CRAWLER / "scripts/lightpanda-claimant-credentials.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("failure", [None, "digest", "host", "mode", "tampered_pin"])
def test_native_prepare_matches_original_bundle_contract(
    tmp_path: Path,
    original_pki: dict[str, Path],
    native_installer: Path,
    failure: str | None,
) -> None:
    reference = reference_bundle()
    archive = tmp_path / "transport.tar"
    reference.build_bundle(
        ca_path=original_pki["ca"],
        server_path=original_pki["server"],
        client_path=original_pki["client"],
        client_key_path=original_pki["client_key"],
        service_host="10.0.0.5",
        output=archive,
    )
    if failure == "tampered_pin":
        oracle = deployment_reference()
        payloads = oracle._transport_payloads(archive)
        payloads["server-leaf.sha256"] = b"0" * 64 + b"\n"
        oracle._write_transport(archive, payloads)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    host = "10.0.0.5"
    if failure == "digest":
        digest = "0" * 64
    elif failure == "host":
        host = "10.0.0.6"
    elif failure == "mode":
        archive.chmod(0o644)
    arguments = [
        str(native_installer),
        "prepare",
        "--archive",
        str(archive),
        "--archive-sha256",
        digest,
        "--revision",
        "a" * 40,
        "--root",
        str(tmp_path / "native"),
        "--service-host",
        host,
    ]
    result = subprocess.run(arguments, check=False, capture_output=True, text=True)
    if failure:
        with pytest.raises(reference.BundleError):
            reference.prepare_generation(
                archive=archive,
                expected_sha256=digest,
                revision="a" * 40,
                root=tmp_path / "reference",
                service_host=host,
            )
        assert result.returncode != 0 and not result.stdout
        assert not (tmp_path / "native").exists()
        return
    assert result.returncode == 0, result.stderr
    native = Path(result.stdout.strip())
    original = reference.prepare_generation(
        archive=archive,
        expected_sha256=digest,
        revision="a" * 40,
        root=tmp_path / "reference",
        service_host=host,
    )
    assert native.name == original.name
    assert {p.name for p in native.iterdir()} == set(reference._FILES)
    assert native.stat().st_mode & 0o777 == 0o711
    for name, mode in reference._FILES.items():
        path = native / name
        assert path.read_bytes() == (original / name).read_bytes()
        assert path.stat().st_mode & 0o777 == mode
    retry = subprocess.run(arguments, check=True, capture_output=True, text=True)
    assert retry.stdout == result.stdout
    assert (native / "client-key.pem").stat().st_uid == (original / "client-key.pem").stat().st_uid


def test_production_prepare_keeps_isolation_and_uses_native_image_binary() -> None:
    source = (CRAWLER / "deploy.sh").read_text()
    body = source.split("prepare_claimant_credential_generation() {", 1)[1].split("\n}\n", 1)[0]
    assert "--entrypoint /usr/local/bin/go-lightpanda-claimant-credentials" in body
    for constraint in (
        "--network none",
        "--read-only",
        "--cap-drop ALL",
        "--security-opt no-new-privileges:true",
        '--user "${deploy_uid}:${deploy_gid}"',
    ):
        assert constraint in body
    assert "/installer.py" not in body and "/app/.venv/bin/python" not in body
    dockerfile = (CRAWLER / "Dockerfile").read_text()
    assert "-o /out/go-lightpanda-claimant-credentials ./cmd/credentials" in dockerfile
    assert (
        "COPY --from=lightpanda-b0-build /out/go-lightpanda-claimant-credentials "
        "/usr/local/bin/go-lightpanda-claimant-credentials" in dockerfile
    )
