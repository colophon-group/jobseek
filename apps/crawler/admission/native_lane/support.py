"""Shared fixture TLS and resource accounting for native lane tests."""

from __future__ import annotations

import json
import os
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import NameOID

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


class AdmissionError(RuntimeError):
    """A containment, execution, or admission gate failed."""


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
