#!/usr/bin/env python3
"""Build an attested private copy of the active crawler CSV generation."""

from __future__ import annotations

import argparse
import hashlib
import os
import pathlib
import re
import shutil
import stat
import tempfile

_DIGEST_RE = re.compile(r"[0-9a-f]{64}")
_GENERATION_RE = re.compile(r"[A-Za-z0-9._-]+")
_RELATIVE_CSV_RE = re.compile(r"[A-Za-z0-9._/-]+\.csv")
_SNAPSHOT_RE = re.compile(r"run\.[A-Za-z0-9]+")


def _regular_file(path: pathlib.Path) -> bool:
    return path.exists() and not path.is_symlink() and stat.S_ISREG(path.stat().st_mode)


def _exact_value(path: pathlib.Path, key: str) -> str:
    if not _regular_file(path):
        raise RuntimeError(f"committed crawler evidence is unavailable: {path.name}")
    prefix = f"{key}="
    values = [
        line.removeprefix(prefix)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.startswith(prefix)
    ]
    if len(values) != 1 or not values[0]:
        raise RuntimeError(f"{key} must appear exactly once in {path.name}")
    return values[0]


def _resolve_generation(
    active_release_pointer: pathlib.Path,
    active_release_root: pathlib.Path,
) -> pathlib.Path:
    if (
        not active_release_root.is_dir()
        or active_release_root.is_symlink()
        or not active_release_pointer.is_symlink()
    ):
        raise RuntimeError("committed crawler release is unavailable or unsafe")
    target = pathlib.Path(os.readlink(active_release_pointer))
    if (
        target.parent != active_release_root
        or not _GENERATION_RE.fullmatch(target.name)
        or not target.is_dir()
        or target.is_symlink()
    ):
        raise RuntimeError("committed crawler release pointer is unsafe")
    return target


def _expected_tree(generation: pathlib.Path) -> dict[str, str]:
    release_manifest = generation / "release.manifest"
    files_manifest = generation / "data-files.sha256"
    if _exact_value(release_manifest, "RELEASE_FORMAT_VERSION") != "3":
        raise RuntimeError("ATS inventory requires a format-v3 crawler release")
    manifest_digest = _exact_value(release_manifest, "DATA_FILES_SHA256")
    data_contract = _exact_value(release_manifest, "DATA_CONTRACT_SHA256")
    if not _DIGEST_RE.fullmatch(manifest_digest) or data_contract != manifest_digest:
        raise RuntimeError("committed crawler data contract is invalid")
    if not _regular_file(files_manifest):
        raise RuntimeError("committed crawler CSV manifest is unavailable or unsafe")
    if hashlib.sha256(files_manifest.read_bytes()).hexdigest() != manifest_digest:
        raise RuntimeError("committed crawler CSV manifest failed verification")

    expected: dict[str, str] = {}
    for line in files_manifest.read_text(encoding="utf-8").splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9._/-]+\.csv)", line)
        if not match:
            raise RuntimeError("committed crawler CSV manifest contains an invalid row")
        digest, relative = match.groups()
        relative_path = pathlib.PurePosixPath(relative)
        if relative_path.is_absolute() or ".." in relative_path.parts or relative in expected:
            raise RuntimeError("committed crawler CSV manifest contains an unsafe path")
        expected[relative] = digest
    if not expected or not {"companies.csv", "boards.csv"}.issubset(expected):
        raise RuntimeError("committed crawler registry files are incomplete")
    return expected


def _actual_tree(data: pathlib.Path) -> dict[str, str]:
    if not data.is_dir() or data.is_symlink():
        raise RuntimeError("committed crawler CSV snapshot is unavailable or unsafe")
    actual: dict[str, str] = {}
    for directory, dirnames, filenames in os.walk(data, followlinks=False):
        directory_path = pathlib.Path(directory)
        for name in dirnames:
            if (directory_path / name).is_symlink():
                raise RuntimeError("unsafe symlink in committed crawler CSV snapshot")
        for name in filenames:
            source = directory_path / name
            if source.is_symlink() or not stat.S_ISREG(source.stat().st_mode):
                raise RuntimeError("unsafe file in committed crawler CSV snapshot")
            relative = source.relative_to(data).as_posix()
            if not _RELATIVE_CSV_RE.fullmatch(relative):
                raise RuntimeError("unexpected file in committed crawler CSV snapshot")
            actual[relative] = hashlib.sha256(source.read_bytes()).hexdigest()
    return actual


def _prune_stale_snapshots(snapshot_root: pathlib.Path) -> None:
    if not snapshot_root.is_dir() or snapshot_root.is_symlink():
        raise RuntimeError("ATS inventory registry snapshot root is unavailable or unsafe")
    for path in snapshot_root.iterdir():
        if _SNAPSHOT_RE.fullmatch(path.name) and path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)


def prepare_registry_snapshot(
    *,
    active_release_pointer: pathlib.Path,
    active_release_root: pathlib.Path,
    snapshot_root: pathlib.Path,
    expected_image: str,
    expected_revision: str,
) -> pathlib.Path:
    """Verify the active v3 release and copy its exact CSV tree.

    The caller holds the crawler mutation lock, so the active pointer and its
    immutable generation cannot be promoted or pruned during this operation.
    """

    generation = _resolve_generation(active_release_pointer, active_release_root)
    success = generation / "success.env"
    if _exact_value(success, "CRAWLER_IMAGE_REF") != expected_image:
        raise RuntimeError("ATS image does not match the active crawler release")
    if _exact_value(success, "JOBSEEK_DEPLOY_REVISION") != expected_revision:
        raise RuntimeError("ATS revision does not match the active crawler release")

    expected = _expected_tree(generation)
    data = generation / "data"
    if _actual_tree(data) != expected:
        raise RuntimeError("committed crawler CSV snapshot does not match its exact manifest")

    _prune_stale_snapshots(snapshot_root)
    snapshot = pathlib.Path(tempfile.mkdtemp(prefix="run.", dir=snapshot_root))
    try:
        for relative, digest in expected.items():
            target = snapshot / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(data / relative, target)
            if hashlib.sha256(target.read_bytes()).hexdigest() != digest:
                raise RuntimeError("ATS registry snapshot copy failed verification")
    except BaseException:
        shutil.rmtree(snapshot)
        raise
    return snapshot


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--active-release-pointer", type=pathlib.Path, required=True)
    parser.add_argument("--active-release-root", type=pathlib.Path, required=True)
    parser.add_argument("--snapshot-root", type=pathlib.Path, required=True)
    parser.add_argument("--expected-image", required=True)
    parser.add_argument("--expected-revision", required=True)
    args = parser.parse_args()
    snapshot = prepare_registry_snapshot(
        active_release_pointer=args.active_release_pointer,
        active_release_root=args.active_release_root,
        snapshot_root=args.snapshot_root,
        expected_image=args.expected_image,
        expected_revision=args.expected_revision,
    )
    print(snapshot)


if __name__ == "__main__":
    main()
