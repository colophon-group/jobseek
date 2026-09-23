"""Run and decide the deliberately small #8648 whole-lane admission test."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import socket
import stat
import statistics
import subprocess
import tempfile
import threading
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

HERE = Path(__file__).resolve().parent
COMPOSE = HERE / "compose.yml"
WORKLOAD = HERE / "workload.v2.json"
POSTGRES_IMAGE = (
    "postgres:17-alpine@sha256:742f40ea20b9ff2ff31db5458d127452988a2164df9e17441e191f3b72252193"
)
REDIS_IMAGE = (
    "redis:8-alpine@sha256:978f0e01593e65eed801f2402944efcd936d43b5027e4908a7897baf88ed6241"
)
PYTHON_IMAGE = (
    "python:3.13.15-slim-trixie@sha256:"
    "7ce4b6dfe35e55397b7cda544f8a13f191b7ae28dc5aad71fe664dbc9bc2623f"
)
WORKLOAD_SHA256 = "4be1503fef65b7ac74f1085f168cd7d2e9db19060fdd8271abb7b9ca42ef5390"
CA_DER_SHA256 = "3241f0b0b433641e35eb277bd957bf25a750ffa64a7b8160724f4ec714dffc35"
CA_SCALAR = 25261520469523560364590841614277305820204912636566536599750116611056381316436
CA_PEM = b"""-----BEGIN CERTIFICATE-----
MIIBfTCCASSgAwIBAgIISk9CU0VFSwIwCgYIKoZIzj0EAwIwMTEvMC0GA1UEAwwm
Sm9ic2VlayBMYW5lIEFkbWlzc2lvbiB2MiBCZW5jaG1hcmsgQ0EwHhcNMjYwMTAx
MDAwMDAwWhcNMjkwMTAxMDAwMDAwWjAxMS8wLQYDVQQDDCZKb2JzZWVrIExhbmUg
QWRtaXNzaW9uIHYyIEJlbmNobWFyayBDQTBZMBMGByqGSM49AgEGCCqGSM49AwEH
A0IABDnhzaYZYXDCiHnLZouRivAEQXDURaxwm1SIaSyhpTA/EKshCwOyp37SBb94
p/vGgsJ8d38+Kf2a2ldq4W2eeT6jJjAkMBIGA1UdEwEB/wQIMAYBAf8CAQAwDgYD
VR0PAQH/BAQDAgEGMAoGCCqGSM49BAMCA0cAMEQCICx5irSI6iMbuPji+Q+kORHI
DuwKs5bnwE5roEeureyXAiA73vWM/QiN25uO9nWeh9+DOytETqNMUNrY8DgUzAPA
bg==
-----END CERTIFICATE-----
"""
IMMUTABLE = re.compile(r"^[^@\s]+@sha256:[0-9a-f]{64}$")
LOCAL_IMAGE_ID = re.compile(r"^sha256:[0-9a-f]{64}$")
SCHEDULE = tuple(
    (f"c1-p{pair}", 1, lane)
    for pair in range(1, 4)
    for lane in (("candidate", "control") if pair % 2 else ("control", "candidate"))
) + tuple(
    (f"c4-p{pair}", 4, lane)
    for pair in range(1, 6)
    for lane in (("candidate", "control") if pair % 2 else ("control", "candidate"))
)


class AdmissionError(RuntimeError):
    """A containment, execution, or admission gate failed."""


def _run(command: list[str], *, env: dict[str, str] | None = None, timeout: float = 60) -> str:
    try:
        result = subprocess.run(command, capture_output=True, text=True, env=env, timeout=timeout)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise AdmissionError(f"command failed to run: {command[:3]}") from exc
    if len(result.stdout) + len(result.stderr) > 2 * 1024 * 1024:
        raise AdmissionError("subprocess transcript exceeded 2 MiB")
    if result.returncode:
        tail = (result.stdout + result.stderr)[-4000:]
        raise AdmissionError(f"command failed ({result.returncode}): {tail}")
    return result.stdout


def _write(path: Path, payload: bytes, mode: int = 0o444) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, mode)
    try:
        os.write(descriptor, payload)
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _atomic_evidence(path: Path, document: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.parent / f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}"
    payload = json.dumps(document, sort_keys=True, separators=(",", ":")).encode("ascii") + b"\n"
    try:
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            view = memoryview(payload)
            while view:
                view = view[os.write(descriptor, view) :]
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise


def _leaf(  # noqa: E501
    ca: x509.Certificate, key: ec.EllipticCurvePrivateKey, name: str, sans: list[x509.GeneralName], usage: x509.ObjectIdentifier,  # noqa: E501
) -> tuple[x509.Certificate, ec.EllipticCurvePrivateKey]:  # fmt: skip
    leaf_key = ec.generate_private_key(ec.SECP256R1())
    now = datetime.now(UTC)
    leaf = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)]))
        .issuer_name(ca.subject)
        .public_key(leaf_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(minutes=5))
        .not_valid_after(now + timedelta(days=179))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(True, False, False, False, False, False, False, False, False),
            critical=True,
        )  # noqa: E501
        .add_extension(x509.SubjectAlternativeName(sans), critical=False)
        .add_extension(x509.ExtendedKeyUsage([usage]), critical=False)
        .sign(key, hashes.SHA256())
    )
    return leaf, leaf_key


def generate_pki(output: Path) -> dict[str, str]:
    ca = x509.load_pem_x509_certificate(CA_PEM)
    if hashlib.sha256(ca.public_bytes(serialization.Encoding.DER)).hexdigest() != CA_DER_SHA256:
        raise AdmissionError("embedded benchmark CA digest changed")
    ca_key = ec.derive_private_key(CA_SCALAR, ec.SECP256R1())
    public = ca.public_key()
    if (
        not isinstance(public, ec.EllipticCurvePublicKey)
        or public.public_numbers() != ca_key.public_key().public_numbers()
    ):
        raise AdmissionError("embedded benchmark CA key mismatch")
    output.mkdir(mode=0o755)
    server, server_key = _leaf(
        ca,
        ca_key,
        "b0-admission-renderer",
        [x509.IPAddress(ipaddress.ip_address("172.30.250.2"))],
        ExtendedKeyUsageOID.SERVER_AUTH,
    )
    client, client_key = _leaf(
        ca,
        ca_key,
        "b0-admission-supervisor",
        [x509.UniformResourceIdentifier("spiffe://jobseek/crawler/lightpanda-b0")],
        ExtendedKeyUsageOID.CLIENT_AUTH,
    )
    fixture, fixture_key = _leaf(
        ca,
        ca_key,
        "b0-admission-fixture",
        [x509.DNSName(f"origin-{index}.lane.bench.test") for index in range(8)],
        ExtendedKeyUsageOID.SERVER_AUTH,
    )
    _write(output / "ca.pem", CA_PEM)
    for name, certificate, private_key in (
        ("server", server, server_key),
        ("client", client, client_key),
        ("fixture", fixture, fixture_key),
    ):
        _write(output / f"{name}.pem", certificate.public_bytes(serialization.Encoding.PEM))
        _write(
            output / f"{name}-key.pem",
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            mode=0o400 if name == "server" else 0o444,
        )

    def certificate_digest(certificate: x509.Certificate) -> str:
        return hashlib.sha256(certificate.public_bytes(serialization.Encoding.DER)).hexdigest()

    def spki_digest(certificate: x509.Certificate) -> str:
        return hashlib.sha256(
            certificate.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).hexdigest()

    pins = {
        "ca": CA_DER_SHA256,
        "server_leaf": certificate_digest(server),
        "server_spki": spki_digest(server),
        "client_leaf": certificate_digest(client),
        "client_spki": spki_digest(client),
    }
    for label in ("ca", "server-leaf", "server-spki"):
        _write(output / f"{label}.sha256", (pins[label.replace("-", "_")] + "\n").encode("ascii"))
    return pins


def _compose(env: dict[str, str], *arguments: str, timeout: float = 60) -> str:
    return _run(["docker", "compose", "-f", str(COMPOSE), *arguments], env=env, timeout=timeout)


def _container_id(env: dict[str, str], service: str) -> str:
    identifier = _compose(env, "ps", "-q", service).strip()
    if not re.fullmatch(r"[0-9a-f]{12,64}", identifier):
        raise AdmissionError(f"{service} has no exact running container")
    return identifier


def attest_network(env: dict[str, str]) -> dict[str, Any]:
    project = env["ADMISSION_PROJECT"]
    allowed = {f"{project}_claim", f"{project}_origin"}
    config = json.loads(_compose(env, "config", "--format", "json"))
    for service in config["services"].values():
        networks = service.get("networks")
        if (networks is None and service.get("network_mode") != "none") or (
            networks is not None and not set(networks) <= {"claim", "origin"}
        ):
            raise AdmissionError("a service can attach to an undeclared/default network")
    raw = json.loads(_run(["docker", "network", "inspect", f"{project}_origin"]))[0]
    expected_internal = True
    ipam = raw.get("IPAM", {}).get("Config", [])
    if (
        raw.get("Internal") is not expected_internal
        or len(ipam) != 1
        or ipam[0].get("Subnet") != "11.252.0.0/24"
    ):
        raise AdmissionError("origin network internal flag or subnet differs from policy")
    proof: dict[str, Any] = {"internal": raw["Internal"], "subnet": "11.252.0.0/24"}
    if expected_internal:
        fixture = _inspect(_container_id(env, "fixture"))
        endpoint = fixture["NetworkSettings"]["Networks"][f"{project}_origin"]
        origin_aliases = sorted(
            alias for alias in endpoint["Aliases"] if alias.endswith(".lane.bench.test")
        )  # noqa: E501
        expected = [f"origin-{index}.lane.bench.test" for index in range(4)]
        if endpoint["IPAddress"] != "11.252.0.2" or origin_aliases != expected:
            raise AdmissionError("fixture address or reserved .test aliases differ from policy")
        proof["fixture_aliases"] = origin_aliases
    for identifier in _compose(env, "ps", "-q").split():
        if not set(_inspect(identifier)["NetworkSettings"]["Networks"]) <= allowed:
            raise AdmissionError("running admission container attached to a default network")
    return proof


def _inspect(identifier: str) -> dict[str, Any]:
    value = json.loads(_run(["docker", "inspect", identifier]))
    if not isinstance(value, list) or len(value) != 1:
        raise AdmissionError("docker inspect shape changed")
    return value[0]


def load_deploy_provenance(path: Path, images: dict[str, str], source_sha: str) -> dict[str, Any]:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(descriptor)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or metadata.st_mode & 0o022
            or metadata.st_nlink != 1
            or metadata.st_size > 256 * 1024
        ):
            raise AdmissionError("deploy provenance file metadata is unsafe")
        raw = b""
        while chunk := os.read(descriptor, 64 * 1024):
            raw += chunk
            if len(raw) > 256 * 1024:
                raise AdmissionError("deploy provenance grew while reading")
        after = os.fstat(descriptor)
        if (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) != (
            metadata.st_dev,
            metadata.st_ino,
            metadata.st_size,
            metadata.st_mtime_ns,
        ) or len(raw) != metadata.st_size:
            raise AdmissionError("deploy provenance changed while reading")
    finally:
        os.close(descriptor)
    required = {"JOBSEEK_DEPLOY_REVISION", "CRAWLER_IMAGE_REF", "BROWSER_IMAGE_REF"}
    values: dict[str, str] = {}
    for line in raw.decode("utf-8", errors="strict").splitlines():
        key, separator, value = line.partition("=")
        if separator and key in required:
            if key in values:
                raise AdmissionError("deploy provenance contains duplicate identity keys")
            values[key] = value
    if values != {
        "JOBSEEK_DEPLOY_REVISION": source_sha,
        "CRAWLER_IMAGE_REF": images["candidate"],
        "BROWSER_IMAGE_REF": images["browser"],
    }:
        raise AdmissionError("deploy provenance does not bind crawler images to source")
    return {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(raw).hexdigest(),
        "mode": oct(stat.S_IMODE(metadata.st_mode)),
        "uid": metadata.st_uid,
        "source_sha": source_sha,
        "candidate": images["candidate"],
        "browser": images["browser"],
    }


def attest_images(
    images: dict[str, str],
    source_sha: str,
    renderer_source_sha: str,
    deploy: dict[str, Any] | None = None,
) -> dict[str, dict[str, Any]]:
    labels = {
        "candidate": "org.opencontainers.image.revision",
        "browser": "org.opencontainers.image.revision",
        "renderer": "org.jobseek.lightpanda.source-commit",
    }
    proof: dict[str, dict[str, Any]] = {}
    for role, reference in images.items():
        expected_source_sha = renderer_source_sha if role == "renderer" else source_sha
        local_image = LOCAL_IMAGE_ID.fullmatch(reference) is not None
        if not local_image:
            _run(["docker", "pull", reference], timeout=300)
        raw = json.loads(_run(["docker", "image", "inspect", reference]))
        if not isinstance(raw, list) or len(raw) != 1:
            raise AdmissionError(f"{role} image inspect shape changed")
        item = raw[0]
        image_id = item.get("Id")
        repo_digests = item.get("RepoDigests")
        image_config = item.get("Config")
        if not isinstance(image_config, dict):
            raise AdmissionError(f"{role} image config has an invalid shape")
        image_labels = image_config.get("Labels") or {}
        if not isinstance(image_labels, dict):
            raise AdmissionError(f"{role} image labels have an invalid shape")
        actual_label = image_labels.get(labels[role])
        digest_valid = (
            isinstance(image_id, str)
            and re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None
            and (
                image_id == reference
                if local_image
                else isinstance(repo_digests, list)
                and all(isinstance(value, str) for value in repo_digests)
                and reference in repo_digests
            )
        )
        fallback = (
            role in {"candidate", "browser"}
            and (actual_label is None or actual_label == "")
            and deploy is not None
            and deploy.get(role) == reference
            and deploy.get("source_sha") == source_sha
        )
        if not digest_valid or (actual_label != expected_source_sha and not fallback):
            raise AdmissionError(f"{role} image digest/source provenance mismatch")
        proof[role] = {
            "reference": reference,
            "image_id": image_id,
            "repo_digests": sorted(repo_digests or []),
            "source_label": labels[role],
            "source_sha": expected_source_sha,
            "observed_source_label": actual_label,
            "method": "image_label" if actual_label == expected_source_sha else "deploy_env",
            "deploy_provenance_sha256": deploy.get("sha256") if fallback and deploy else None,
        }
    return proof


def _cgroup(pid: int) -> Path:
    rows = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines()
    memberships = [row.split("::", 1)[1] for row in rows if row.startswith("0::")]
    if len(memberships) != 1 or not Path("/sys/fs/cgroup/cgroup.controllers").is_file():
        raise AdmissionError("cgroup v2 membership is unavailable")
    root = Path("/sys/fs/cgroup").resolve()
    result = (root / memberships[0].lstrip("/")).resolve()
    result.relative_to(root)
    return result


def _integer(path: Path) -> int:
    value = path.read_text(encoding="ascii").strip()
    if not value.isdigit():
        raise AdmissionError(f"non-integer cgroup value: {path.name}")
    return int(value)


def _memory_events(path: Path) -> dict[str, int]:
    values = {
        key: int(value)
        for key, value in (
            row.split() for row in (path / "memory.events").read_text(encoding="ascii").splitlines()
        )
    }
    swap_path = path / "memory.swap.events"
    if swap_path.is_file():
        values.update(
            {
                f"swap_{key}": int(value)
                for key, value in (
                    row.split() for row in swap_path.read_text(encoding="ascii").splitlines()
                )
            }
        )
    return values


def _cpu_usage(path: Path) -> int:
    fields = dict(
        row.split() for row in (path / "cpu.stat").read_text(encoding="ascii").splitlines()
    )
    value = fields.get("usage_usec", "")
    if not value.isdigit():
        raise AdmissionError("cgroup CPU usage is unavailable")
    return int(value)


def attest_measured(
    env: dict[str, str], services: list[str], images: dict[str, dict[str, Any]]
) -> list[dict[str, Any]]:
    expected = {  # fmt: skip
        "renderer": (1024**3, 2_000_000_000),
        "producer": (32 * 1024**2, 250_000_000),
        "supervisor": (96 * 1024**2, 250_000_000),
        "executor": (384 * 1024**2, 500_000_000),
        "control": (1536 * 1024**2, 3_000_000_000),  # noqa: E501
    }
    rows: list[dict[str, Any]] = []
    project = env["ADMISSION_PROJECT"]
    expected_networks = {
        "renderer": {f"{project}_claim", f"{project}_origin"},
        "producer": {f"{project}_claim"},
        "supervisor": {f"{project}_claim"},
        "executor": {f"{project}_claim"},
        "control": {f"{project}_claim", f"{project}_origin"},
    }
    for service in services:
        identifier = _container_id(env, service)
        item = _inspect(identifier)
        host, state = item["HostConfig"], item["State"]
        limit, cpu = expected[service]
        role = (
            "renderer"
            if service == "renderer"
            else "browser"
            if service == "control"
            else "candidate"
        )
        if (
            host["Memory"] != limit
            or host["MemorySwap"] != limit
            or host["NanoCpus"] != cpu
            or not state["Running"]
            or item.get("Image") != images[role]["image_id"]
            or item.get("Config", {}).get("Image") != images[role]["reference"]
        ):
            raise AdmissionError(f"{service} resource envelope differs from admission policy")
        attached_networks = set(item["NetworkSettings"]["Networks"])
        if attached_networks != expected_networks[service]:
            raise AdmissionError(f"{service} runtime network attachments differ from policy")
        path = _cgroup(int(state["Pid"]))
        if _integer(path / "memory.max") != limit or _integer(path / "memory.swap.max") != 0:
            raise AdmissionError(f"{service} cgroup memory/swap limit differs from policy")
        procs = {int(value) for value in (path / "cgroup.procs").read_text().split()}
        if int(state["Pid"]) not in procs:
            raise AdmissionError(f"{service} init PID is outside its measured cgroup")
        events = _memory_events(path)
        rows.append(
            {  # fmt: skip
                "service": service,
                "id": identifier,
                "path": path,
                "memory_max": limit,
                "swap_max": 0,
                "events_before": events,  # noqa: E501
                "image_id": item["Image"],
                "networks": sorted(attached_networks),
                "memory_peak_before": _integer(path / "memory.peak"),
                "cpu_before_usec": _cpu_usage(path),
            }
        )
    if sum(row["memory_max"] for row in rows) != 1536 * 1024**2:
        raise AdmissionError("candidate/control measured envelope is not exactly 1.5 GiB")
    return rows


def _sample(rows: list[dict[str, Any]], stop: threading.Event, output: dict[str, Any]) -> None:
    peak = 0
    count = 0
    current = 0
    while not stop.is_set():
        current = sum(_integer(row["path"] / "memory.current") for row in rows)
        peak = max(peak, current)
        count += 1
        stop.wait(0.1)
    output.update(
        sampled_peak_bytes=peak,
        retained_bytes=current,
        samples=count,
        cpu_seconds=sum(
            (_cpu_usage(row["path"]) - row["cpu_before_usec"]) / 1_000_000 for row in rows
        ),
    )


def _number(metrics: str, name: str, labels: dict[str, str] | None = None) -> float:
    labels = labels or {}
    total = 0.0
    for line in metrics.splitlines():
        if line.startswith("#") or " " not in line:
            continue
        metric, raw = line.rsplit(" ", 1)
        if metric.split("{", 1)[0] != name:
            continue
        if all(f'{key}="{value}"' in metric for key, value in labels.items()):
            total += float(raw)
    return total


def _metrics(env: dict[str, str], service: str, port: int) -> str:
    script = f"import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:{port}/metrics',timeout=3).read().decode())"
    return _compose(env, "exec", "-T", service, "python", "-c", script)


def _metric_evidence(lane: str, metrics: str, expected: int) -> dict[str, Any]:
    if lane == "candidate":
        specs = {  # fmt: skip
            "claimed": (
                "jobseek_lightpanda_b0_queue_transitions_total",
                {"operation": "claim_next", "outcome": "accepted"},
            ),  # noqa: E501
            "render_success": ("jobseek_lightpanda_b0_render_total", {"outcome": "success"}),
            "render_failure": ("jobseek_lightpanda_b0_render_total", {"outcome": "failure"}),
            "executor_committed": (
                "jobseek_lightpanda_b0_executor_total",
                {"outcome": "committed"},
            ),  # noqa: E501
            "executor_failure": ("jobseek_lightpanda_b0_executor_total", {"outcome": "failure"}),
        }
        values = {key: _number(metrics, *spec) for key, spec in specs.items()}
        for key, name in {
            "failed_rescheduled": "jobseek_lightpanda_b0_failed_rescheduled_total",
            "reaped": "jobseek_lightpanda_b0_reaped_total",
            "ready": "jobseek_lightpanda_b0_queue_ready",
            "inflight": "jobseek_lightpanda_b0_queue_inflight",
            "dead": "jobseek_lightpanda_b0_queue_dead",
        }.items():
            values[key] = _number(metrics, name)
        zero = set(values) - {"claimed", "render_success", "executor_committed"}
        values["exact"] = (
            values["claimed"] == expected
            and values["render_success"] == expected
            and values["executor_committed"] == expected
            and all(values[key] == 0 for key in zero)
        )
        return values
    claimed = _number(metrics, "crawler_tasks_total", {"kind": "scrape", "status": "succeeded"})
    other_status = sum(
        _number(metrics, "crawler_tasks_total", {"kind": "scrape", "status": status})
        for status in (
            "failed",
            "stale_config",
            "rerouted_to_browser",
            "skipped_missing",
            "skipped_tombstoned",
            "skipped_invalid_id",
            "skipped_rich",
        )
    )
    retries = sum(
        _number(metrics, name)
        for name in (
            "crawler_browser_navigation_network_retry_total",
            "crawler_browser_content_retry_total",
            "crawler_browser_navigate_fallback_total",
            "crawler_inflight_reaped_total",
        )
    )
    return {
        "claimed": claimed,
        "other_status": other_status,
        "retries_or_fallback": retries,
        "exact": claimed == expected and other_status == 0 and retries == 0,
    }


def _parse_marker(raw: str, marker: str) -> dict[str, Any]:
    rows = [line[len(marker) :] for line in raw.splitlines() if line.startswith(marker)]
    if len(rows) != 1:
        raise AdmissionError(f"expected one {marker} marker")
    value = json.loads(rows[0])
    if not isinstance(value, dict):
        raise AdmissionError(f"{marker} is not an object")
    return value


def cleanup_project(env: dict[str, str]) -> dict[str, Any]:
    errors: list[str] = []
    try:
        _compose(env, "down", "-v", "--remove-orphans", timeout=90)
    except Exception as exc:  # noqa: BLE001 - preserve cleanup failure as evidence
        errors.append(str(exc))
    leftovers: dict[str, list[str]] = {}
    commands = {
        "containers": ["docker", "ps", "-aq"],
        "networks": ["docker", "network", "ls", "-q"],
        "volumes": ["docker", "volume", "ls", "-q"],
    }
    name_commands = {
        "containers": (
            ["docker", "ps", "-a", "--format", "{{.Names}}"],
            f"{env['ADMISSION_PROJECT']}-",
        ),
        "networks": (
            ["docker", "network", "ls", "--format", "{{.Name}}"],
            f"{env['ADMISSION_PROJECT']}_",
        ),
        "volumes": (
            ["docker", "volume", "ls", "--format", "{{.Name}}"],
            f"{env['ADMISSION_PROJECT']}_",
        ),
    }
    for kind, command in commands.items():
        try:
            raw = _run(
                [
                    *command,
                    "--filter",
                    f"label=com.docker.compose.project={env['ADMISSION_PROJECT']}",
                ]
            )
            name_command, prefix = name_commands[kind]
            named = [name for name in _run(name_command).splitlines() if name.startswith(prefix)]
            leftovers[kind] = sorted(set(raw.split()) | set(named))
        except Exception as exc:  # noqa: BLE001 - absence query must also fail closed
            errors.append(f"{kind}: {exc}")
            leftovers[kind] = ["query_failed"]
    return {
        "project": env["ADMISSION_PROJECT"],
        "exact": not errors and not any(leftovers.values()),
        "errors": errors,
        **leftovers,
    }


def run_arm(
    base_env: dict[str, str],
    images: dict[str, dict[str, Any]],
    pair: str,
    concurrency: int,
    lane: str,
    ordinal: int,
) -> dict[str, Any]:
    project = f"b0admit-{os.getpid()}-{ordinal}-{secrets.token_hex(3)}"
    env = dict(
        base_env,
        ADMISSION_PROJECT=project,
        ADMISSION_CONCURRENCY=str(concurrency),
        ADMISSION_LANE=lane,
        ADMISSION_COHORT="c1" if concurrency == 1 else "c4",
        ADMISSION_PRODUCER_MODE="off",
        ADMISSION_PHASE="reserve",
        ADMISSION_DUE="0",
    )
    measured = (
        ["producer", "renderer", "supervisor", "executor"] if lane == "candidate" else ["control"]
    )
    result: dict[str, Any] = {
        "pair": pair,
        "lane": lane,
        "concurrency": concurrency,
        "project": project,
    }
    try:
        _compose(env, "up", "-d", "--wait", "postgres", "redis", "fixture", timeout=90)
        result["network"] = attest_network(env)
        _compose(env, "run", "--rm", "--no-deps", "migrate", timeout=120)
        reserve = _parse_marker(
            _compose(env, "run", "--rm", "--no-deps", "runner", timeout=30),
            "ADMISSION_SETUP=",
        )
        if reserve != {"phase": "reserve", "routing_epoch": 2}:
            raise AdmissionError("disposable PostgreSQL routing epoch was not reserved")
        if lane == "candidate":
            _compose(env, "up", "-d", "--wait", "executor", "renderer", "producer", timeout=120)
            for attempt in range(30):
                try:
                    with socket.create_connection(("172.30.250.2", 9443), timeout=1):
                        break
                except OSError as exc:
                    if attempt == 29:
                        raise AdmissionError("renderer did not open its service socket") from exc
                    time.sleep(0.1)
        else:
            _compose(env, "up", "-d", "--wait", "control", timeout=120)
        env["ADMISSION_PHASE"] = "seed"
        seed = _parse_marker(
            _compose(env, "run", "--rm", "--no-deps", "runner", timeout=30),
            "ADMISSION_SETUP=",
        )
        expected = 4 * concurrency
        if (
            seed.get("phase") != "seed"
            or seed.get("feed") != expected
            or not isinstance(seed.get("due"), float)
        ):
            raise AdmissionError("candidate/control fixture seed is incomplete")
        env["ADMISSION_DUE"] = str(seed["due"])
        if lane == "candidate":
            env["ADMISSION_PHASE"] = "transfer"
            env["ADMISSION_PRODUCER_MODE"] = "enabled"
            transfer = _parse_marker(
                _compose(env, "run", "--rm", "--no-deps", "runner", timeout=30),
                "ADMISSION_SETUP=",
            )
            if transfer != {"phase": "transfer", "activated": expected}:
                raise AdmissionError("Go producer did not transfer the complete fixture cohort")
            _compose(env, "up", "-d", "--no-deps", "--wait", "supervisor", timeout=60)
        env["ADMISSION_PHASE"] = "collect"
        rows = attest_measured(env, measured, images)
        sampled: dict[str, Any] = {}
        retention_observed = 0.0
        stop = threading.Event()
        thread = threading.Thread(target=_sample, args=(rows, stop, sampled), daemon=True)
        thread.start()
        try:
            runner_raw = _compose(env, "run", "--rm", "--no-deps", "runner", timeout=210)
            result["arm"] = _parse_marker(runner_raw, "ADMISSION_ARM=")
            if not result["arm"].get("error"):
                retention_started = time.monotonic()
                time.sleep(5)
                retention_observed = time.monotonic() - retention_started
        finally:
            stop.set()
            thread.join(timeout=2)
        metrics_raw = _metrics(
            env,
            "supervisor" if lane == "candidate" else "control",
            9101 if lane == "candidate" else 9091,
        )
        expected = 4 * concurrency
        result["metrics"] = _metric_evidence(lane, metrics_raw, expected)
        _compose(env, "wait", "fixture", timeout=20)
        result["fixture"] = _parse_marker(
            _compose(env, "logs", "--no-color", "--no-log-prefix", "fixture"),
            "ADMISSION_FIXTURE=",
        )
        after: list[dict[str, Any]] = []
        for row in rows:
            item = _inspect(row["id"])
            state = item["State"]
            healthcheck = item.get("Config", {}).get("Healthcheck")
            healthcheck_present = isinstance(healthcheck, dict) and bool(healthcheck.get("Test"))
            health = state.get("Health")
            health_status = health.get("Status") if isinstance(health, dict) else None
            attached_networks = sorted(item["NetworkSettings"]["Networks"])
            if (
                state.get("Running") is not True
                or state.get("Status") != "running"
                or (healthcheck_present and health_status != "healthy")
                or attached_networks != row["networks"]
            ):
                raise AdmissionError(
                    f"{row['service']} liveness or network changed after retention"
                )
            events = _memory_events(row["path"])
            memory_peak = _integer(row["path"] / "memory.peak")
            delta = {
                key: events.get(key, 0) - row["events_before"].get(key, 0)
                for key in set(events) | set(row["events_before"])
            }
            after.append(
                {
                    "service": row["service"],
                    "restart_count": item["RestartCount"],
                    "oom_killed": item["State"]["OOMKilled"],
                    "memory_events_delta": delta,
                    "memory_peak_bytes": memory_peak,
                    "memory_peak_before": row["memory_peak_before"],
                    "memory_max": row["memory_max"],
                    "image_id": row["image_id"],
                    "networks": attached_networks,
                    "running": state["Running"],
                    "state_status": state["Status"],
                    "healthcheck_present": healthcheck_present,
                    "health_status": health_status,
                }
            )
        result["resource"] = {
            **sampled,
            # Density compares only synchronized sums of memory.current. Per-service
            # memory.peak values below are safety gates for sub-sample excursions.
            "peak_bytes": sampled["sampled_peak_bytes"],
            "peak_policy": "synchronized_sum_memory_current",
            "service_peak_policy": "safety_only_not_density",
            "retention_seconds": retention_observed,
            "limit_bytes": 1536 * 1024**2,
            "services": after,
        }
    except Exception as exc:  # noqa: BLE001 - partial arm becomes rejection evidence
        result["error"] = f"{type(exc).__name__}: {exc}"
        diagnostics: dict[str, str] = {}
        for service in ("renderer", "producer", "executor", "supervisor", "fixture", "control"):
            try:
                logs = _compose(env, "logs", "--no-color", "--tail", "40", service, timeout=10)
            except Exception as log_exc:  # noqa: BLE001 - diagnostic must not hide the failure
                logs = f"{type(log_exc).__name__}: {log_exc}"
            if logs.strip():
                diagnostics[service] = logs[-8000:]
        result["diagnostics"] = diagnostics
    finally:
        result["cleanup"] = cleanup_project(env)
    return result


def evaluate(document: dict[str, Any]) -> dict[str, Any]:
    reasons: list[str] = []
    arms = document.get("arms", [])
    if document.get("failure"):
        reasons.append("execution_failure")
    checkout = document.get("checkout", {})
    if (
        not isinstance(checkout, dict)
        or checkout.get("head") != document.get("source_sha")
        or checkout.get("postflight_head") != document.get("source_sha")
        or checkout.get("clean") is not True
        or checkout.get("postflight_clean") is not True
    ):
        reasons.append("checkout_provenance")
    provenance = document.get("image_provenance", {})
    deploy_document = document.get("deploy_provenance", {})
    if not isinstance(deploy_document, dict):
        deploy_document = {}
    if (
        not isinstance(provenance, dict)
        or re.fullmatch(r"[0-9a-f]{40}", str(document.get("source_sha"))) is None
        or re.fullmatch(r"[0-9a-f]{40}", str(document.get("renderer_source_sha"))) is None
        or set(provenance)
        != {
            "candidate",
            "browser",
            "renderer",
        }
        or any(
            not isinstance(row, dict)
            or row.get("source_sha")
            != (
                document.get("renderer_source_sha")
                if role == "renderer"
                else document.get("source_sha")
            )
            or row.get("reference") != document.get("images", {}).get(role)
            or re.fullmatch(r"sha256:[0-9a-f]{64}", str(row.get("image_id"))) is None
            or (
                LOCAL_IMAGE_ID.fullmatch(str(row.get("reference"))) is None
                and row.get("reference") not in row.get("repo_digests", [])
            )
            or row.get("method") not in {"image_label", "deploy_env"}
            or (
                row.get("method") == "image_label"
                and row.get("observed_source_label")
                != (
                    document.get("renderer_source_sha")
                    if role == "renderer"
                    else document.get("source_sha")
                )
            )
            or (
                row.get("method") == "deploy_env"
                and (
                    role not in {"candidate", "browser"}
                    or row.get("observed_source_label") not in {None, ""}
                    or row.get("deploy_provenance_sha256") != deploy_document.get("sha256")
                    or deploy_document.get("source_sha") != document.get("source_sha")
                    or deploy_document.get(role) != row.get("reference")
                )
            )
            for role, row in provenance.items()
        )
    ):
        reasons.append("image_provenance")
    expected_order = SCHEDULE
    if [(arm.get("pair"), arm.get("concurrency"), arm.get("lane")) for arm in arms] != list(
        expected_order
    ):
        reasons.append("run_order")
    for arm in arms:
        arm_expected_count = 4 * arm.get("concurrency", 0)
        payload, resource = arm.get("arm", {}), arm.get("resource", {})
        cleanup = arm.get("cleanup", {})
        if arm.get("error"):
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:arm_error")
        if (
            not cleanup.get("exact")
            or cleanup.get("project") != arm.get("project")
            or cleanup.get("errors") != []
            or any(cleanup.get(kind) != [] for kind in ("containers", "networks", "volumes"))
        ):
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:cleanup")
        network = arm.get("network", {})
        if network.get("internal") is not True or network.get("subnet") != "11.252.0.0/24":
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:network")
        expected_aliases = [f"origin-{index}.lane.bench.test" for index in range(4)]
        if network.get("fixture_aliases") != expected_aliases:
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:network")
        if any(
            payload.get(key) != arm_expected_count
            for key in ("feed", "persisted", "terminal", "writes")
        ):
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:counts")
        if not payload.get("redis", {}).get("exact") or not arm.get("metrics", {}).get("exact"):
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:queue_or_metrics")
        aggregate_limit = 1536 * 1024**2
        if (
            resource.get("samples", 0) < 50
            or resource.get("retention_seconds", 0) < 5
            or resource.get("peak_bytes") != resource.get("sampled_peak_bytes")
            or resource.get("peak_policy") != "synchronized_sum_memory_current"
            or resource.get("service_peak_policy") != "safety_only_not_density"
            or resource.get("limit_bytes") != aggregate_limit
            or resource.get("peak_bytes", 2**63) >= aggregate_limit
            or resource.get("retained_bytes", 2**63) >= aggregate_limit
            or not isinstance(resource.get("cpu_seconds"), int | float)
            or resource.get("cpu_seconds", -1) < 0
        ):
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:memory")
        service_rows = resource.get("services", [])
        expected_service_caps = (
            {
                "renderer": 1024**3,
                "producer": 32 * 1024**2,
                "supervisor": 96 * 1024**2,
                "executor": 384 * 1024**2,
            }
            if arm.get("lane") == "candidate"
            else {"control": 1536 * 1024**2}
        )
        if (
            not isinstance(service_rows, list)
            or len(service_rows) != len(expected_service_caps)
            or {
                service.get("service"): service.get("memory_max")
                for service in service_rows
                if isinstance(service, dict)
            }
            != expected_service_caps
        ):
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:memory")
            service_rows = []
        for service in service_rows:
            delta = service.get("memory_events_delta", {})
            memory_peak = service.get("memory_peak_bytes")
            memory_peak_before = service.get("memory_peak_before")
            memory_max = service.get("memory_max")
            expected_networks = (
                [f"{arm.get('project')}_claim", f"{arm.get('project')}_origin"]
                if service.get("service") in {"renderer", "control"}
                else [f"{arm.get('project')}_claim"]
            )
            if (
                not all(
                    isinstance(value, int) and value >= 0
                    for value in (memory_peak, memory_peak_before, memory_max)
                )
                or not memory_max
                or memory_peak < memory_peak_before
                or memory_peak >= memory_max
            ):
                reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:memory")
            if (
                service.get("networks") != expected_networks
                or service.get("running") is not True
                or service.get("state_status") != "running"
                or (
                    service.get("healthcheck_present") is not True
                    and service.get("service") in {"producer", "supervisor", "executor", "control"}
                )
                or (
                    service.get("healthcheck_present") is True
                    and service.get("health_status") != "healthy"
                )
            ):
                reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:liveness_or_network")
            if (
                service.get("restart_count") != 0
                or service.get("oom_killed")
                or delta.get("oom", 0)
                or delta.get("oom_kill", 0)
                or any(value for key, value in delta.items() if key.startswith("swap_"))
            ):
                reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:restart_oom_swap")
        fixture = arm.get("fixture")
        if (
            not isinstance(fixture, dict)
            or fixture.get("requests") != arm_expected_count
            or fixture.get("max_global_in_flight") != arm.get("concurrency")
            or fixture.get("max_per_origin_in_flight") != 1
            or fixture.get("unexpected") != 0
        ):
            reasons.append(f"{arm.get('pair')}/{arm.get('lane')}:fixture")
    pairs: dict[str, dict[str, Any]] = {}
    for arm in arms:
        pairs.setdefault(str(arm.get("pair")), {})[str(arm.get("lane"))] = arm
    density: list[float] = []
    pair_metrics: list[dict[str, float | str]] = []
    for pair, lanes in pairs.items():
        if set(lanes) != {"candidate", "control"}:
            reasons.append(f"{pair}:missing_lane")
            continue
        candidate, control = lanes["candidate"], lanes["control"]
        candidate_arm, control_arm = candidate.get("arm", {}), control.get("arm", {})
        if candidate_arm.get("canonical_sha256") != control_arm.get(
            "canonical_sha256"
        ) or candidate_arm.get("per_task_sha256") != control_arm.get("per_task_sha256"):
            reasons.append(f"{pair}:canonical_parity")
        values = (
            candidate_arm.get("elapsed_ns"),
            control_arm.get("elapsed_ns"),
            candidate_arm.get("p99_ms"),
            control_arm.get("p99_ms"),
            candidate.get("resource", {}).get("peak_bytes"),
            control.get("resource", {}).get("peak_bytes"),
        )
        if not all(isinstance(value, int | float) and value > 0 for value in values):
            reasons.append(f"{pair}:measurements")
            continue
        throughput = values[1] / values[0]
        p99 = values[2] / max(values[3], 0.001)
        ratio = throughput * values[5] / values[4]
        density.append(ratio)
        candidate_cpu = candidate.get("resource", {}).get("cpu_seconds")
        control_cpu = control.get("resource", {}).get("cpu_seconds")
        if not all(
            isinstance(value, int | float) and value >= 0 for value in (candidate_cpu, control_cpu)
        ):
            reasons.append(f"{pair}:cpu_measurements")
            continue
        pair_metrics.append(
            {
                "pair": pair,
                "density_ratio": ratio,
                "elapsed_ratio": throughput,
                "p99_ratio": p99,
                "peak_rss_ratio": values[4] / values[5],
                "cpu_seconds_candidate": candidate_cpu,
                "cpu_seconds_control": control_cpu,
            }
        )
        if throughput <= 0 or p99 <= 0:
            reasons.append(f"{pair}:measurements")
    c1_density = [
        float(row["density_ratio"]) for row in pair_metrics if str(row["pair"]).startswith("c1-")
    ]
    c4_density = [
        float(row["density_ratio"]) for row in pair_metrics if str(row["pair"]).startswith("c4-")
    ]
    if (
        len(c1_density) != 3
        or len(c4_density) != 5
        or statistics.median(c1_density) <= 1
        or statistics.median(c4_density) <= 1
    ):
        reasons.append("density")
    return {
        "admitted": not reasons,
        "reasons": sorted(set(reasons)),
        "density_ratios": density,
        "pair_metrics": pair_metrics,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--candidate-image", required=True)
    parser.add_argument("--browser-image", required=True)
    parser.add_argument("--renderer-image", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--renderer-source-sha", required=True)
    parser.add_argument("--deploy-provenance", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    for reference in (args.candidate_image, args.browser_image, args.renderer_image):
        if IMMUTABLE.fullmatch(reference) is None and LOCAL_IMAGE_ID.fullmatch(reference) is None:
            parser.error("runtime images must be immutable local IDs or name@sha256 references")
    if re.fullmatch(r"[0-9a-f]{40}", args.source_sha) is None:
        parser.error("source SHA must be exact")
    if re.fullmatch(r"[0-9a-f]{40}", args.renderer_source_sha) is None:
        parser.error("renderer source SHA must be exact")
    input_path = WORKLOAD
    images = {
        "candidate": args.candidate_image,
        "browser": args.browser_image,
        "renderer": args.renderer_image,
    }
    order = SCHEDULE
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "mode": "synthetic",
        "source_sha": args.source_sha,
        "renderer_source_sha": args.renderer_source_sha,
        "images": images,
        "config_sha256": hashlib.sha256(COMPOSE.read_bytes()).hexdigest(),
        "workload_sha256": None,
        "ca_der_sha256": CA_DER_SHA256,
        "run_order": [
            {"pair": pair, "concurrency": concurrency, "lane": lane}
            for pair, concurrency, lane in order
        ],
        "arms": [],
    }
    try:
        head = _run(["git", "rev-parse", "HEAD"], timeout=5).strip()
        dirty = _run(["git", "status", "--porcelain=v1", "--untracked-files=all"], timeout=5)
        if head != args.source_sha or dirty:
            raise AdmissionError("checkout is dirty or differs from the claimed source SHA")
        evidence["checkout"] = {"head": head, "clean": True}
        evidence["workload_sha256"] = hashlib.sha256(input_path.read_bytes()).hexdigest()
        from src.lightpanda.admission import load_tasks

        load_tasks(input_path, 1)
        load_tasks(input_path, 4)
        deploy = None
        if args.deploy_provenance is not None:
            deploy = load_deploy_provenance(args.deploy_provenance, images, args.source_sha)
            evidence["deploy_provenance"] = deploy
        evidence["image_provenance"] = attest_images(
            images, args.source_sha, args.renderer_source_sha, deploy
        )
        _atomic_evidence(args.output, evidence)
        with tempfile.TemporaryDirectory(prefix="jobseek-b0-admission-") as temporary:
            pki = Path(temporary) / "pki"
            pins = generate_pki(pki)
            _run(
                [
                    "docker",
                    "run",
                    "--rm",
                    "--network",
                    "none",
                    "--user",
                    "0:0",
                    "--read-only",
                    "--mount",
                    f"type=bind,src={pki},dst=/pki",
                    "--entrypoint",
                    "/bin/chown",
                    args.candidate_image,
                    "10001:10001",
                    "/pki/server-key.pem",
                ],
                timeout=15,
            )
            base_env = {
                "PATH": os.environ.get("PATH", ""),
                "HOME": os.environ.get("HOME", "/tmp"),
                "ADMISSION_PKI_DIR": str(pki),
                "ADMISSION_INPUT": str(input_path),
                "ADMISSION_CA_PIN": pins["ca"],
                "ADMISSION_CLIENT_LEAF_PIN": pins["client_leaf"],
                "ADMISSION_CLIENT_SPKI_PIN": pins["client_spki"],
                "ADMISSION_TIMEOUT": "180",
                "POSTGRES_IMAGE": POSTGRES_IMAGE,
                "REDIS_IMAGE": REDIS_IMAGE,
                "PYTHON_IMAGE": PYTHON_IMAGE,
                "CANDIDATE_IMAGE": args.candidate_image,
                "BROWSER_IMAGE": args.browser_image,
                "RENDERER_IMAGE": args.renderer_image,
            }
            for index, (pair, concurrency, lane) in enumerate(order):
                arm = run_arm(
                    base_env, evidence["image_provenance"], pair, concurrency, lane, index
                )
                evidence["arms"].append(arm)
                _atomic_evidence(args.output, evidence)
                if arm.get("error") or not arm.get("cleanup", {}).get("exact"):
                    break
        postflight_head = _run(["git", "rev-parse", "HEAD"], timeout=5).strip()
        postflight_dirty = _run(
            ["git", "status", "--porcelain=v1", "--untracked-files=all"], timeout=5
        )
        if postflight_head != args.source_sha or postflight_dirty:
            raise AdmissionError("checkout changed during admission")
        evidence["checkout"].update(postflight_head=postflight_head, postflight_clean=True)
    except Exception as exc:  # noqa: BLE001 - always publish machine-readable failure
        evidence["failure"] = {"type": type(exc).__name__, "message": str(exc)}
    evidence["verdict"] = evaluate(evidence)
    _atomic_evidence(args.output, evidence)
    print(json.dumps(evidence["verdict"], sort_keys=True, separators=(",", ":")))
    return 0 if evidence["verdict"]["admitted"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
