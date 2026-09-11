#!/usr/bin/env python3
"""Build and atomically install immutable Lightpanda claimant credentials."""

from __future__ import annotations

import argparse
import hashlib
import io
import os
import re
import shutil
import stat
import sys
import tarfile
import tempfile
from pathlib import Path
from typing import Final

_FILES: Final = {
    "ca.pem": 0o444,
    "client.pem": 0o444,
    "client-key.pem": 0o400,
    "ca.sha256": 0o444,
    "server-leaf.sha256": 0o444,
    "server-spki.sha256": 0o444,
}
_PEM_FILES: Final = frozenset({"ca.pem", "client.pem", "client-key.pem"})
_PIN_FILES: Final = frozenset(_FILES) - _PEM_FILES
_REVISION_RE: Final = re.compile(r"^[0-9a-f]{40}$")
_DEFAULT_ROOT: Final = Path("/home/deploy/.local/share/jobseek-lightpanda-claimant/credentials")


class BundleError(RuntimeError):
    """The credential bundle is structurally or cryptographically unsafe."""


def build_bundle(
    *,
    ca_path: Path,
    server_path: Path,
    client_path: Path,
    client_key_path: Path,
    service_host: str,
    output: Path,
) -> None:
    """Validate source PKI and write one deterministic transport archive."""

    crawler_root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(crawler_root))
    from src.lightpanda.credentials import (  # noqa: PLC0415
        validate_claimant_credential_material,
        validate_server_certificate,
    )

    try:
        ca, _client, _key = validate_claimant_credential_material(
            ca_path=ca_path,
            client_path=client_path,
            client_key_path=client_key_path,
        )
        server_leaf, server_spki = validate_server_certificate(
            server_path=server_path,
            ca=ca,
            service_host=service_host,
        )
    except (OSError, ValueError) as exc:
        raise BundleError("claimant credential source failed validation") from exc

    from cryptography.hazmat.primitives import serialization  # noqa: PLC0415

    payloads = {
        "ca.pem": ca_path.read_bytes(),
        "client.pem": client_path.read_bytes(),
        "client-key.pem": client_key_path.read_bytes(),
        "ca.sha256": (
            hashlib.sha256(ca.public_bytes(serialization.Encoding.DER)).hexdigest() + "\n"
        ).encode("ascii"),
        "server-leaf.sha256": f"{server_leaf}\n".encode("ascii"),
        "server-spki.sha256": f"{server_spki}\n".encode("ascii"),
    }
    _validate_payloads(payloads)
    output.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary_descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{output.name}.", dir=output.parent
    )
    os.close(temporary_descriptor)
    temporary = Path(temporary_name)
    try:
        with tarfile.open(temporary, "w", format=tarfile.USTAR_FORMAT) as archive:
            for name in sorted(_FILES):
                payload = payloads[name]
                member = tarfile.TarInfo(name)
                member.size = len(payload)
                member.mode = _FILES[name]
                member.uid = 0
                member.gid = 0
                member.mtime = 0
                archive.addfile(member, io.BytesIO(payload))
        os.chmod(temporary, 0o600)
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def prepare_generation(*, archive: Path, revision: str, root: Path) -> Path:
    """Validate and atomically install one content-addressed generation."""

    if _REVISION_RE.fullmatch(revision) is None:
        raise BundleError("claimant credential revision is invalid")
    archive_metadata = _regular_file_metadata(archive, "credential archive")
    if stat.S_IMODE(archive_metadata.st_mode) != 0o600:
        raise BundleError("claimant credential archive mode is unsafe")
    if not 0 < archive_metadata.st_size <= 256 * 1024:
        raise BundleError("claimant credential archive has an invalid size")
    try:
        payloads = _read_archive(archive)
    except (OSError, tarfile.TarError) as exc:
        raise BundleError("claimant credential archive is unreadable") from exc
    _validate_payloads(payloads)
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    generation_name = f"sha-{revision}-{digest[:16]}"

    if root.exists() or root.is_symlink():
        root_metadata = root.lstat()
        if root.is_symlink() or not stat.S_ISDIR(root_metadata.st_mode):
            raise BundleError("claimant credential root is unsafe")
    else:
        root.mkdir(mode=0o700, parents=True)
    os.chmod(root, 0o700)
    generation = root / generation_name
    if generation.exists() or generation.is_symlink():
        _verify_generation(generation, payloads)
        return generation

    candidate = Path(tempfile.mkdtemp(prefix=".candidate-", dir=root))
    try:
        os.chmod(candidate, 0o711)
        for name, mode in _FILES.items():
            target = candidate / name
            descriptor = os.open(
                target,
                os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW,
                mode,
            )
            try:
                os.write(descriptor, payloads[name])
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            os.chmod(target, mode)
        _fsync_directory(candidate)
        os.replace(candidate, generation)
        _fsync_directory(root)
    finally:
        if candidate.exists():
            shutil.rmtree(candidate)
    _verify_generation(generation, payloads)
    return generation


def _read_archive(path: Path) -> dict[str, bytes]:
    payloads: dict[str, bytes] = {}
    with tarfile.open(path, "r:") as archive:
        members = archive.getmembers()
        if len(members) != len(_FILES):
            raise BundleError("claimant credential archive member set is incomplete")
        for member in members:
            if member.name not in _FILES or member.name in payloads:
                raise BundleError("claimant credential archive member path is unsafe")
            if not member.isreg() or member.mode != _FILES[member.name]:
                raise BundleError("claimant credential archive member type or mode is unsafe")
            maximum = 32 * 1024 if member.name in _PEM_FILES else 65
            if not 0 < member.size <= maximum:
                raise BundleError("claimant credential archive member size is invalid")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise BundleError("claimant credential archive member is unreadable")
            payloads[member.name] = extracted.read(maximum + 1)
    if set(payloads) != set(_FILES):
        raise BundleError("claimant credential archive member set is incomplete")
    return payloads


def _validate_payloads(payloads: dict[str, bytes]) -> None:
    if set(payloads) != set(_FILES):
        raise BundleError("claimant credential bundle member set is incomplete")
    for name in _PEM_FILES:
        payload = payloads[name]
        if not 0 < len(payload) <= 32 * 1024 or b"\x00" in payload:
            raise BundleError("claimant credential PEM has an invalid size")
    for name in _PIN_FILES:
        payload = payloads[name]
        if (
            len(payload) != 65
            or payload[-1:] != b"\n"
            or any(byte not in b"0123456789abcdef" for byte in payload[:-1])
        ):
            raise BundleError("claimant credential pin is not canonical")


def _verify_generation(generation: Path, payloads: dict[str, bytes]) -> None:
    metadata = generation.lstat()
    if generation.is_symlink() or not stat.S_ISDIR(metadata.st_mode):
        raise BundleError("claimant credential generation is unsafe")
    if stat.S_IMODE(metadata.st_mode) != 0o711:
        raise BundleError("claimant credential generation mode is unsafe")
    if {path.name for path in generation.iterdir()} != set(_FILES):
        raise BundleError("claimant credential generation member set is invalid")
    for name, mode in _FILES.items():
        path = generation / name
        file_metadata = _regular_file_metadata(path, f"credential generation {name}")
        if stat.S_IMODE(file_metadata.st_mode) != mode or path.read_bytes() != payloads[name]:
            raise BundleError("claimant credential generation changed")


def _regular_file_metadata(path: Path, label: str) -> os.stat_result:
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BundleError(f"{label} is unavailable") from exc
    if path.is_symlink() or not stat.S_ISREG(metadata.st_mode):
        raise BundleError(f"{label} must be a regular non-symlink file")
    return metadata


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    build = subparsers.add_parser("build")
    build.add_argument("--ca", required=True, type=Path)
    build.add_argument("--server", required=True, type=Path)
    build.add_argument("--client", required=True, type=Path)
    build.add_argument("--client-key", required=True, type=Path)
    build.add_argument("--service-host", required=True)
    build.add_argument("--output", required=True, type=Path)
    prepare = subparsers.add_parser("prepare")
    prepare.add_argument("--archive", required=True, type=Path)
    prepare.add_argument("--revision", required=True)
    prepare.add_argument("--root", type=Path, default=_DEFAULT_ROOT)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        if args.command == "build":
            build_bundle(
                ca_path=args.ca,
                server_path=args.server,
                client_path=args.client,
                client_key_path=args.client_key,
                service_host=args.service_host,
                output=args.output,
            )
        else:
            generation = prepare_generation(
                archive=args.archive,
                revision=args.revision,
                root=args.root,
            )
            print(generation)
    except BundleError as exc:
        print(f"Lightpanda claimant credential installation failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
