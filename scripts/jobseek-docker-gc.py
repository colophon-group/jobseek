#!/usr/bin/env python3
"""Bound Docker storage on the Hetzner fleet without pruning containers or volumes."""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
import pwd
import re
import shutil
import stat
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

GIB_KB = 1024 * 1024
LOCK_PATH = Path("/run/jobseek-docker-gc.lock")
CRAWLER_MUTATION_LOCK_PATH = Path("/run/lock/jobseek-crawler-mutation.lock")
ACTIVE_RELEASE_ROOT = Path("/home/deploy/.crawler-release-generations")
ACTIVE_RELEASE_POINTER = Path("/home/deploy/.crawler-active-release")
VALID_ROLES = frozenset({"crawler", "postgresql", "typesense"})
CRAWLER_REPOSITORIES = (
    "ghcr.io/colophon-group/jobseek-crawler",
    "ghcr.io/colophon-group/jobseek-crawler-browser",
)
AGE_RE = re.compile(r"^[1-9][0-9]*[smhd]$")
IMAGE_ID_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
RELEASE_NAME_RE = re.compile(
    r"(?:(?:release|data)-[A-Za-z0-9._-]+|(?:murmur|legacy)\.[A-Za-z0-9._-]+)"
)


class GarbageCollectionError(RuntimeError):
    """Docker storage could not be classified or reclaimed safely."""


@dataclass(frozen=True)
class Config:
    role: str
    min_free_kb: int
    crawler_keep: int
    builder_until: str | None


@dataclass(frozen=True)
class Image:
    image_id: str
    created: str
    references: frozenset[str] = frozenset()


def _log(message: str) -> None:
    print(f"[jobseek-docker-gc] {message}", flush=True)


def _run(args: list[str]) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=120,
        )
    except subprocess.TimeoutExpired:
        return subprocess.CompletedProcess(args, 124, "", "command timed out")
    except OSError as exc:
        return subprocess.CompletedProcess(args, 126, "", str(exc))


def _docker_output(*args: str) -> str:
    result = _run(["docker", *args])
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip().splitlines()
        suffix = f": {detail[-1]}" if detail else ""
        raise GarbageCollectionError(f"docker {' '.join(args[:3])} failed{suffix}")
    return result.stdout


def _positive_int(name: str, default: int) -> int:
    raw = os.environ.get(name, str(default))
    try:
        value = int(raw)
    except ValueError as exc:
        raise GarbageCollectionError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise GarbageCollectionError(f"{name} must be a positive integer")
    return value


def _age(name: str, default: str) -> str:
    value = os.environ.get(name, default)
    if not AGE_RE.fullmatch(value):
        raise GarbageCollectionError(f"{name} must be an integer followed by s, m, h, or d")
    return value


def _config_from_env() -> Config:
    role = os.environ.get("JOBSEEK_HOST_ROLE", "")
    if role not in VALID_ROLES:
        raise GarbageCollectionError("JOBSEEK_HOST_ROLE must be crawler, postgresql, or typesense")
    default_floor = 15 * GIB_KB if role == "crawler" else 5 * GIB_KB
    builder_default = "24h"
    builder_until = os.environ.get("JOBSEEK_DOCKER_GC_BUILDER_UNTIL")
    if builder_until is not None:
        builder_until = _age("JOBSEEK_DOCKER_GC_BUILDER_UNTIL", builder_default)
    elif role != "crawler":
        builder_until = builder_default
    return Config(
        role=role,
        min_free_kb=_positive_int("JOBSEEK_DOCKER_GC_MIN_FREE_KB", default_floor),
        crawler_keep=_positive_int("JOBSEEK_DOCKER_GC_CRAWLER_KEEP", 2),
        builder_until=builder_until,
    )


def _free_kb() -> int:
    return shutil.disk_usage("/").free // 1024


def _canonical_image_id(value: str) -> str:
    candidate = value.strip()
    if not candidate.startswith("sha256:") and re.fullmatch(r"[0-9a-f]{64}", candidate):
        candidate = f"sha256:{candidate}"
    if not IMAGE_ID_RE.fullmatch(candidate):
        raise GarbageCollectionError("Docker returned a malformed image identity")
    return candidate


def _chunks(values: list[str], size: int = 100) -> list[list[str]]:
    return [values[index : index + size] for index in range(0, len(values), size)]


def _container_image_ids() -> set[str]:
    container_ids = [
        line.strip()
        for line in _docker_output("container", "ls", "--all", "--quiet", "--no-trunc").splitlines()
        if line.strip()
    ]
    images: set[str] = set()
    for chunk in _chunks(container_ids):
        output = _docker_output("container", "inspect", "--format", "{{.Image}}", *chunk)
        images.update(_canonical_image_id(line) for line in output.splitlines() if line.strip())
    return images


def _inspect_images(raw_ids: str) -> list[Image]:
    image_ids = sorted({_canonical_image_id(line) for line in raw_ids.splitlines() if line.strip()})
    images: list[Image] = []
    for chunk in _chunks(image_ids):
        output = _docker_output(
            "image",
            "inspect",
            "--format",
            "{{.Id}}\t{{.Created}}\t{{json .RepoDigests}}",
            *chunk,
        )
        for line in output.splitlines():
            fields = line.split("\t")
            if len(fields) != 3 or not fields[1].strip():
                raise GarbageCollectionError("Docker returned malformed image creation metadata")
            try:
                raw_references = json.loads(fields[2])
            except json.JSONDecodeError as exc:
                raise GarbageCollectionError("Docker returned malformed image references") from exc
            if raw_references is None:
                raw_references = []
            if not isinstance(raw_references, list) or not all(
                isinstance(reference, str) for reference in raw_references
            ):
                raise GarbageCollectionError("Docker returned malformed image references")
            images.append(
                Image(
                    _canonical_image_id(fields[0]),
                    fields[1].strip(),
                    frozenset(raw_references),
                )
            )
    if {image.image_id for image in images} != set(image_ids):
        raise GarbageCollectionError("Docker image inventory changed while it was inspected")
    return images


def _repository_images(repository: str) -> list[Image]:
    return _inspect_images(_docker_output("image", "ls", "--no-trunc", "--quiet", repository))


def _all_images() -> list[Image]:
    return _inspect_images(_docker_output("image", "ls", "--all", "--no-trunc", "--quiet"))


def _image_generation(image_id: str) -> str:
    """Return a stable pseudonym without disclosing the Docker image identity."""
    return hashlib.sha256(image_id.encode("ascii")).hexdigest()[:16]


def _exact_values(path: Path) -> dict[str, str]:
    try:
        metadata = path.lstat()
    except FileNotFoundError as exc:
        raise GarbageCollectionError("release evidence is unavailable") from exc
    if not stat.S_ISREG(metadata.st_mode) or stat.S_ISLNK(metadata.st_mode):
        raise GarbageCollectionError("release evidence is not an exact regular file")
    if metadata.st_size > 64 * 1024:
        raise GarbageCollectionError("release evidence exceeds its size bound")
    values: dict[str, list[str]] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if not separator:
            raise GarbageCollectionError("release evidence is malformed")
        values.setdefault(key, []).append(value)
    if any(len(items) != 1 for items in values.values()):
        raise GarbageCollectionError("release evidence contains duplicate keys")
    return {key: items[0] for key, items in values.items()}


def _verified_release_refs(release: Path) -> dict[str, str]:
    manifest = _exact_values(release / "release.manifest")
    success_path = release / "success.env"
    success = _exact_values(success_path)
    expected_digest = manifest.get("SUCCESS_SHA256", "")
    actual_digest = hashlib.sha256(success_path.read_bytes()).hexdigest()
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or expected_digest != actual_digest:
        raise GarbageCollectionError("release success evidence failed verification")
    refs: dict[str, str] = {}
    for repository, key in zip(
        CRAWLER_REPOSITORIES,
        ("CRAWLER_IMAGE_REF", "BROWSER_IMAGE_REF"),
        strict=True,
    ):
        value = success.get(key, "")
        if value and not re.fullmatch(rf"{re.escape(repository)}@sha256:[0-9a-f]{{64}}", value):
            raise GarbageCollectionError("release evidence contains an invalid image reference")
        if value:
            refs[repository] = value
    return refs


def _release_ref_history() -> tuple[dict[str, str], dict[str, list[str]]]:
    try:
        root_metadata = ACTIVE_RELEASE_ROOT.lstat()
        pointer_metadata = ACTIVE_RELEASE_POINTER.lstat()
    except FileNotFoundError as exc:
        raise GarbageCollectionError("crawler release-generation evidence is unavailable") from exc
    if not stat.S_ISDIR(root_metadata.st_mode) or stat.S_ISLNK(root_metadata.st_mode):
        raise GarbageCollectionError("crawler release-generation root is unsafe")
    if not stat.S_ISLNK(pointer_metadata.st_mode):
        raise GarbageCollectionError("crawler active-release pointer is unsafe")
    active = Path(os.readlink(ACTIVE_RELEASE_POINTER))
    if active.parent != ACTIVE_RELEASE_ROOT or not RELEASE_NAME_RE.fullmatch(active.name):
        raise GarbageCollectionError("crawler active-release pointer escapes its generation root")
    try:
        active_metadata = active.lstat()
    except FileNotFoundError as exc:
        raise GarbageCollectionError("crawler active release is unavailable") from exc
    if not stat.S_ISDIR(active_metadata.st_mode) or stat.S_ISLNK(active_metadata.st_mode):
        raise GarbageCollectionError("crawler active release is unsafe")
    active_refs = _verified_release_refs(active)
    if set(active_refs) != set(CRAWLER_REPOSITORIES):
        raise GarbageCollectionError("crawler active release lacks required image evidence")

    releases: list[tuple[int, Path]] = []
    for entry in ACTIVE_RELEASE_ROOT.iterdir():
        if entry == active or not RELEASE_NAME_RE.fullmatch(entry.name):
            continue
        metadata = entry.lstat()
        if stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode):
            releases.append((metadata.st_mtime_ns, entry))
    history = {repository: [] for repository in CRAWLER_REPOSITORIES}
    for _, release in sorted(releases, key=lambda item: (item[0], item[1].name), reverse=True):
        try:
            refs = _verified_release_refs(release)
        except GarbageCollectionError:
            # A failed publication may leave a partial non-active directory.
            # It is neither retention authority nor a reason to suppress safe
            # cleanup based on the separately verified active/rollback set.
            continue
        for repository, ref in refs.items():
            if ref not in history[repository]:
                history[repository].append(ref)
    return active_refs, history


def _reference_index(images: list[Image]) -> dict[str, str]:
    index: dict[str, str] = {}
    for image in images:
        for reference in image.references:
            previous = index.setdefault(reference, image.image_id)
            if previous != image.image_id:
                raise GarbageCollectionError(
                    "Docker returned an ambiguous immutable image reference"
                )
    return index


def _known_good_rollbacks(
    refs: list[str],
    *,
    reference_index: dict[str, str],
    excluded_images: set[str],
    count: int,
) -> set[str]:
    images: set[str] = set()
    for ref in refs:
        try:
            image_id = reference_index[ref]
        except KeyError as exc:
            raise GarbageCollectionError(
                "verified rollback image is not locally available"
            ) from exc
        if image_id in excluded_images or image_id in images:
            continue
        images.add(image_id)
        if len(images) == count:
            break
    if len(images) != count:
        raise GarbageCollectionError("required verified rollback image set is incomplete")
    return images


def _repository_retention(
    repository: str,
    *,
    container_images: set[str],
    rollback_images: set[str],
) -> tuple[set[str], set[str]]:
    """Classify repository images while retaining live and rollback generations."""
    images = sorted(
        _repository_images(repository),
        key=lambda image: (image.created, image.image_id),
    )
    repository_ids = {image.image_id for image in images}
    protected = repository_ids & container_images
    if not rollback_images <= repository_ids:
        raise GarbageCollectionError("release image is absent from its Docker repository inventory")
    protected.update(rollback_images)
    return protected, repository_ids - protected


def _remove_images(images: list[Image]) -> tuple[int, int]:
    removed = 0
    failures = 0
    for image in images:
        result = _run(["docker", "image", "rm", image.image_id])
        if result.returncode == 0:
            removed += 1
            _log(f"removed image_generation={_image_generation(image.image_id)}")
        else:
            failures += 1
            _log(f"remove_failed image_generation={_image_generation(image.image_id)}")
    return removed, failures


def _prune_crawler_images(config: Config) -> tuple[int, int]:
    """Bound crawler generations without touching unrelated image families."""
    container_images = _container_image_ids()
    active_refs, history = _release_ref_history()
    all_images = _all_images()
    reference_index = _reference_index(all_images)
    protected = set(container_images)
    managed: set[str] = set()
    immediately_removable: set[str] = set()
    for repository in CRAWLER_REPOSITORIES:
        try:
            active_image = reference_index[active_refs[repository]]
        except KeyError as exc:
            raise GarbageCollectionError("active release image is not locally available") from exc
        rollback_images = _known_good_rollbacks(
            history[repository],
            reference_index=reference_index,
            excluded_images=container_images | {active_image},
            count=config.crawler_keep,
        )
        retained, removable = _repository_retention(
            repository,
            container_images=container_images,
            rollback_images=rollback_images | {active_image},
        )
        protected.update(retained)
        managed.update(retained | removable)
        immediately_removable.update(removable)

    all_image_ids = {image.image_id for image in all_images}
    if not managed <= all_image_ids:
        raise GarbageCollectionError("Docker image inventory changed while retention was planned")
    candidates = [
        image
        for image in all_images
        if image.image_id in immediately_removable and image.image_id not in protected
    ]
    return _remove_images(candidates)


def _prune(args: list[str], *, operation: str) -> bool:
    result = _run(["docker", *args])
    if result.returncode == 0:
        return True
    _log(f"operation_failed operation={operation} status={result.returncode}")
    return False


def _open_crawler_mutation_lock():
    """Open the shared lock without ever creating a root-owned first inode."""
    try:
        deploy = pwd.getpwnam("deploy")
    except KeyError as exc:
        raise GarbageCollectionError("deploy account is unavailable") from exc
    try:
        CRAWLER_MUTATION_LOCK_PATH.lstat()
    except FileNotFoundError:
        create = (
            "import os,sys; "
            "fd=os.open(sys.argv[1],os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o600); "
            "os.close(fd)"
        )
        # The deploy account performs O_EXCL creation, so the shared inode is
        # deploy-owned from its first visible instant after a reboot.
        _run(
            [
                "runuser",
                "-u",
                "deploy",
                "--",
                "python3",
                "-c",
                create,
                str(CRAWLER_MUTATION_LOCK_PATH),
            ]
        )
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(CRAWLER_MUTATION_LOCK_PATH, flags)
    except OSError as exc:
        raise GarbageCollectionError("crawler mutation lock is unavailable") from exc
    try:
        metadata = os.fstat(descriptor)
        path_metadata = CRAWLER_MUTATION_LOCK_PATH.lstat()
        if (
            not stat.S_ISREG(metadata.st_mode)
            or stat.S_ISLNK(path_metadata.st_mode)
            or (metadata.st_dev, metadata.st_ino) != (path_metadata.st_dev, path_metadata.st_ino)
            or stat.S_IMODE(metadata.st_mode) != 0o600
            or metadata.st_uid != deploy.pw_uid
            or metadata.st_gid != deploy.pw_gid
        ):
            raise GarbageCollectionError("crawler mutation lock ownership is unsafe")
        return os.fdopen(descriptor, "r", encoding="utf-8")
    except Exception:
        os.close(descriptor)
        raise


def run_gc(config: Config) -> int:
    before = _free_kb()
    removed = 0
    failures = 0
    _log(
        f"start role={config.role} free_kb={before} min_free_kb={config.min_free_kb} "
        f"builder_until={config.builder_until or 'all'}"
    )

    builder_args = ["builder", "prune", "--all", "--force"]
    if config.builder_until is not None:
        builder_args.extend(("--filter", f"until={config.builder_until}"))
    if not _prune(builder_args, operation="builder-prune"):
        failures += 1

    if config.role == "crawler":
        try:
            crawler_removed, crawler_failures = _prune_crawler_images(config)
        except GarbageCollectionError as exc:
            _log(f"inventory_failed error={exc}")
            failures += 1
        else:
            removed += crawler_removed
            failures += crawler_failures
    after = _free_kb()
    emergency = after < config.min_free_kb
    if emergency:
        _log(f"below_floor free_kb={after} min_free_kb={config.min_free_kb}")

    reclaimed_gib = (after - before) / GIB_KB
    _log(
        f"done free_kb={after} reclaimed_gib={reclaimed_gib:.2f} "
        f"repository_images_removed={removed} failures={failures} emergency={int(emergency)}"
    )
    if failures or after < config.min_free_kb:
        return 1
    return 0


def main() -> int:
    if shutil.which("docker") is None:
        _log("docker unavailable")
        return 1
    try:
        config = _config_from_env()
    except GarbageCollectionError as exc:
        _log(f"configuration_error error={exc}")
        return 2
    LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_PATH.open("a+", encoding="utf-8") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            _log("another collection is active")
            return 0
        if config.role != "crawler":
            return run_gc(config)
        try:
            mutation_lock = _open_crawler_mutation_lock()
        except GarbageCollectionError as exc:
            _log(f"mutation_lock_error error={exc}")
            return 1
        with mutation_lock:
            try:
                fcntl.flock(mutation_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                _log("crawler mutation is active; collection deferred")
                return 0
            return run_gc(config)


if __name__ == "__main__":
    sys.exit(main())
