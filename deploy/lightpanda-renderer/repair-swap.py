#!/usr/bin/env python3
"""Repair one exact running renderer cgroup swap leaf under both host locks.

The operator supplies observed identities from a separate read-only packet.
This command changes only an existing `memory.swap.max` leaf from `max` to `0`.
It never restarts a container or retries an uncertain write.
"""

from __future__ import annotations

import base64
import fcntl
import hashlib
import json
import os
import pwd
import re
import select
import selectors
import signal
import stat
import subprocess
import sys
import time
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from pathlib import Path
from typing import Any, cast

CONTAINER = "jobseek-lightpanda-renderer"
ROOT = Path("/home/deploy/.local/share/jobseek-lightpanda")
ACTIVE = ROOT / "active"
HOST_LOCK = Path("/run/lock/jobseek-lightpanda-network.lock")
RENDERER_LOCK = ROOT / "renderer.lock"
CGROUP_ROOT = Path("/sys/fs/cgroup")
MEMORY_MAX = "1073741824"
COMMAND_CAP = 8192


class RepairRefused(Exception):
    """A failed identity, capability, or health guard."""


def require(condition: bool, category: str) -> None:
    if not condition:
        raise RepairRefused(category)


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def read_exact_file(path: Path, *, limit: int, modes: tuple[int, ...], uid: int, gid: int) -> bytes:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        require(
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and metadata.st_uid == uid
            and metadata.st_gid == gid
            and stat.S_IMODE(metadata.st_mode) in modes
            and 0 < metadata.st_size <= limit,
            "file_metadata_drift",
        )
        data = os.read(fd, limit + 1)
        require(len(data) == metadata.st_size, "file_size_drift")
        path_metadata = os.stat(path, follow_symlinks=False)
        require(
            (metadata.st_dev, metadata.st_ino) == (path_metadata.st_dev, path_metadata.st_ino),
            "file_identity_drift",
        )
        return data
    finally:
        os.close(fd)


def acquire_existing_lock(path: Path, *, uid: int, gid: int, mode: int) -> int:
    fd = os.open(path, os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW)
    try:
        metadata = os.fstat(fd)
        require(
            stat.S_ISREG(metadata.st_mode)
            and metadata.st_nlink == 1
            and metadata.st_uid == uid
            and metadata.st_gid == gid
            and stat.S_IMODE(metadata.st_mode) == mode,
            "lock_metadata_drift",
        )
        deadline = time.monotonic() + 60
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                require(time.monotonic() < deadline, "lock_busy")
                time.sleep(0.2)
        path_metadata = os.stat(path, follow_symlinks=False)
        require(
            (metadata.st_dev, metadata.st_ino) == (path_metadata.st_dev, path_metadata.st_ino),
            "lock_identity_drift",
        )
        return fd
    except BaseException:
        os.close(fd)
        raise


@dataclass(frozen=True)
class CommandResult:
    code: int
    stdout: bytes
    stderr: bytes


def run_bounded(arguments: list[str], *, timeout: float, cap: int = COMMAND_CAP) -> CommandResult:
    child = subprocess.Popen(
        arguments,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        start_new_session=True,
    )
    assert child.stdout is not None and child.stderr is not None
    selector = selectors.DefaultSelector()
    output = {"stdout": bytearray(), "stderr": bytearray()}
    deadline = time.monotonic() + timeout
    try:
        selector.register(child.stdout, selectors.EVENT_READ, "stdout")
        selector.register(child.stderr, selectors.EVENT_READ, "stderr")
        while selector.get_map():
            remaining = deadline - time.monotonic()
            require(remaining > 0, "command_timeout")
            for key, _ in selector.select(min(remaining, 0.25)):
                chunk = os.read(key.fd, min(4096, cap + 1))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                require(
                    len(output["stdout"]) + len(output["stderr"]) + len(chunk) <= cap,
                    "command_output_overflow",
                )
                output[key.data].extend(chunk)
        remaining = deadline - time.monotonic()
        require(remaining > 0, "command_timeout")
        code = child.wait(timeout=remaining)
        return CommandResult(code, bytes(output["stdout"]), bytes(output["stderr"]))
    except BaseException:
        with suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()
        raise
    finally:
        selector.close()
        child.stdout.close()
        child.stderr.close()


def docker_identity() -> dict[str, Any]:
    template = (
        "{{json .Id}}|{{json .Image}}|{{json .State.Pid}}|"
        "{{json .State.Running}}|{{json .HostConfig.Memory}}|"
        "{{json .HostConfig.MemorySwap}}|{{json .HostConfig.CgroupnsMode}}|"
        "{{json .Config.Image}}|{{json .Config.Labels}}"
    )
    result = run_bounded(["docker", "inspect", "--format", template, CONTAINER], timeout=20)
    require(result.code == 0 and not result.stderr, "docker_inspect_failed")
    parts = result.stdout.strip().split(b"|", 8)
    require(len(parts) == 9, "docker_inspect_shape")
    try:
        values = [json.loads(part) for part in parts]
    except (ValueError, UnicodeDecodeError) as exc:
        raise RepairRefused("docker_inspect_shape") from exc
    return dict(
        zip(
            (
                "id",
                "image",
                "pid",
                "running",
                "memory",
                "memory_swap",
                "cgroupns",
                "config_image",
                "labels",
            ),
            values,
            strict=True,
        )
    )


def process_start(pid: int) -> str:
    data = Path(f"/proc/{pid}/stat").read_text(encoding="ascii")
    after_name = data.rsplit(") ", 1)[1].split()
    require(len(after_name) > 19, "process_stat_shape")
    return after_name[19]


def cgroup_relative_path(pid: int, container_id: str) -> str:
    mountinfo = Path("/proc/self/mountinfo").read_text(encoding="ascii").splitlines()
    cgroup_mounts = []
    for line in mountinfo:
        fields = line.split(" - ", 1)[0].split()
        if (
            " - cgroup2 " in line
            and len(fields) >= 5
            and fields[3] == "/"
            and fields[4] == str(CGROUP_ROOT)
        ):
            cgroup_mounts.append(line)
    require(len(cgroup_mounts) == 1, "cgroup_v2_mount_required")
    lines = Path(f"/proc/{pid}/cgroup").read_text(encoding="ascii").splitlines()
    require(len(lines) == 1 and lines[0].startswith("0::/"), "cgroup_v2_required")
    relative = lines[0][3:]
    components = relative[1:].split("/")
    require(
        all(
            re.fullmatch(r"[A-Za-z0-9_.-]+", part) and part not in (".", "..")
            for part in components
        )
        and components[-1] in (container_id, f"docker-{container_id}.scope"),
        "cgroup_leaf_identity_drift",
    )
    return relative


def open_cgroup_leaf(relative: str) -> tuple[int, int, int]:
    directory = os.open(CGROUP_ROOT, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        for component in relative[1:].split("/"):
            child = os.open(
                component,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
            os.close(directory)
            directory = child
        memory_fd = os.open(
            "memory.max", os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=directory
        )
        try:
            swap_fd = os.open(
                "memory.swap.max",
                os.O_RDWR | os.O_NOFOLLOW | os.O_CLOEXEC,
                dir_fd=directory,
            )
        except BaseException:
            os.close(memory_fd)
            raise
        return directory, memory_fd, swap_fd
    except BaseException:
        os.close(directory)
        raise


def assert_leaf_identity(directory: int, memory_fd: int, swap_fd: int) -> None:
    for name, fd in (("memory.max", memory_fd), ("memory.swap.max", swap_fd)):
        held = os.fstat(fd)
        live = os.stat(name, dir_fd=directory, follow_symlinks=False)
        require(
            stat.S_ISREG(held.st_mode)
            and stat.S_ISREG(live.st_mode)
            and (held.st_dev, held.st_ino) == (live.st_dev, live.st_ino),
            "cgroup_leaf_fd_drift",
        )


def read_scalar(fd: int) -> str:
    os.lseek(fd, 0, os.SEEK_SET)
    data = os.read(fd, 32)
    require(data.endswith(b"\n") and len(data) <= 20, "cgroup_scalar_shape")
    return data.decode("ascii").strip()


def require_pidfd_alive(pid_fd: int) -> None:
    poller = select.poll()
    poller.register(pid_fd, select.POLLIN | select.POLLERR | select.POLLHUP)
    require(not poller.poll(0), "renderer_pid_exited")


def write_leaf_once(
    directory_fd: int,
    memory_fd: int,
    swap_fd: int,
    pid_fd: int,
    identity: tuple[str, str],
    current_identity: Callable[[], tuple[str, str]],
    receipt: dict[str, Any],
) -> tuple[str, str]:
    assert_leaf_identity(directory_fd, memory_fd, swap_fd)
    require(read_scalar(memory_fd) == MEMORY_MAX, "memory_limit_drift")
    require(read_scalar(swap_fd) == "max", "swap_leaf_not_max")
    os.lseek(swap_fd, 0, os.SEEK_SET)
    print("renderer-zero-swap write boundary reached", file=sys.stderr, flush=True)
    # All prewrite reads, seeks, and logging precede these final live guards.
    require(current_identity() == identity, "identity_changed_before_write")
    assert_leaf_identity(directory_fd, memory_fd, swap_fd)
    require_pidfd_alive(pid_fd)
    # The only host write follows the final pidfd poll; failed/short writes are
    # reported as attempted and never retried.
    try:
        written = os.write(swap_fd, b"0\n")
    finally:
        receipt["writeAttempted"] = True
    require(written == 2, "short_swap_write")
    receipt["writeCompleted"] = True
    host_memory = read_scalar(memory_fd)
    host_swap = read_scalar(swap_fd)
    assert_leaf_identity(directory_fd, memory_fd, swap_fd)
    receipt["hostMemoryMax"] = host_memory
    receipt["hostSwapMax"] = host_swap
    require(
        host_memory == MEMORY_MAX and host_swap == "0",
        "host_swap_readback_failed",
    )
    return host_memory, host_swap


def release_identity(
    expected: dict[str, str],
    deploy_uid: int,
    deploy_gid: int,
    *,
    candidate: bool = False,
) -> str:
    generation = ROOT / "releases" / expected["release_id"]
    if not candidate:
        active_metadata = os.lstat(ACTIVE)
        require(stat.S_ISLNK(active_metadata.st_mode), "active_release_drift")
        require(ACTIVE.resolve(strict=True) == generation, "active_release_drift")
    generation_metadata = os.lstat(generation)
    require(
        stat.S_ISDIR(generation_metadata.st_mode)
        and generation_metadata.st_uid == deploy_uid
        and generation_metadata.st_gid == deploy_gid,
        "release_metadata_drift",
    )
    env_bytes = read_exact_file(
        generation / "release.env",
        limit=65536,
        modes=(0o600,),
        uid=deploy_uid,
        gid=deploy_gid,
    )
    verify_bytes = read_exact_file(
        generation / "verify.py",
        limit=262144,
        modes=(0o555,),
        uid=deploy_uid,
        gid=deploy_gid,
    )
    require(sha256(env_bytes) == expected["env_sha"], "release_environment_drift")
    require(sha256(verify_bytes) == expected["verifier_sha"], "published_verifier_drift")
    env: dict[str, str] = {}
    for line in env_bytes.decode("utf-8").splitlines():
        require("=" in line, "release_environment_shape")
        key, value = line.split("=", 1)
        require(bool(key and value and key not in env), "release_environment_shape")
        env[key] = value
    source = expected["release_id"].split("-", 2)[1]
    require(
        env.get("SOURCE_COMMIT") == source
        and env.get("RELEASE_ID") == expected["release_id"]
        and env.get("RENDERER_RELEASE_DIR") == str(generation)
        and (
            re.fullmatch(
                r"ghcr\.io/colophon-group/jobseek-lightpanda-renderer@sha256:[0-9a-f]{64}",
                env.get("RENDERER_IMAGE_REF", ""),
            )
            is not None
            or candidate
            and "-ci-" in expected["release_id"]
            and env.get("RENDERER_IMAGE_REF") == "jobseek-lightpanda-renderer:pr"
        ),
        "release_environment_identity_drift",
    )
    return env["RENDERER_IMAGE_REF"]


def check_identity(
    expected: dict[str, str],
    deploy_uid: int,
    deploy_gid: int,
    *,
    candidate: bool = False,
) -> tuple[str, str]:
    machine = read_exact_file(
        Path("/etc/machine-id"), limit=128, modes=(0o444, 0o644), uid=0, gid=0
    )
    require(sha256(machine) == expected["machine_sha"], "host_identity_drift")
    image_ref = (
        release_identity(expected, deploy_uid, deploy_gid, candidate=True)
        if candidate
        else release_identity(expected, deploy_uid, deploy_gid)
    )
    inspect = docker_identity()
    require(
        inspect["id"] == expected["container_id"]
        and inspect["image"] == expected["image_id"]
        and inspect["pid"] == int(expected["pid"])
        and inspect["running"] is True
        and inspect["memory"] == int(MEMORY_MAX)
        and inspect["memory_swap"] == int(MEMORY_MAX)
        and inspect["cgroupns"] == "private"
        and inspect["config_image"] == image_ref,
        "container_identity_drift",
    )
    labels = inspect["labels"] or {}
    require(isinstance(labels, dict), "container_label_shape")
    require(
        labels.get("org.jobseek.lightpanda.source-commit")
        == expected["release_id"].split("-", 2)[1]
        and labels.get("org.jobseek.lightpanda.image-ref") == image_ref
        and labels.get("org.jobseek.lightpanda.release") == expected["release_id"],
        "container_label_drift",
    )
    pid = int(expected["pid"])
    relative = cgroup_relative_path(pid, expected["container_id"])
    require(sha256(relative.encode()) == expected["cgroup_sha"], "cgroup_path_drift")
    return relative, process_start(pid)


def enforce_candidate_zero_swap(release_id: str, container_id: str) -> dict[str, Any]:
    """Enforce the new leaf under the installer's existing lifecycle locks.

    This root-owned helper is called by the digest-bound host policy. It never
    activates a generation, starts a service or executes its deploy-owned files.
    The standalone active-release repair below retains its separate contract.
    """
    require(os.geteuid() == 0, "root_required")
    require(
        re.fullmatch(r"sha-[0-9a-f]{40}-(?:ci-)?r[0-9]+a[0-9]+", release_id) is not None,
        "release_id_shape",
    )
    require(re.fullmatch(r"[0-9a-f]{64}", container_id) is not None, "container_id_shape")
    deploy = pwd.getpwnam("deploy")
    inspect = docker_identity()
    require(inspect["id"] == container_id and inspect["running"] is True, "candidate_drift")
    require(
        isinstance(inspect["pid"], int)
        and inspect["pid"] > 0
        and re.fullmatch(r"sha256:[0-9a-f]{64}", str(inspect["image"])) is not None,
        "candidate_process_shape",
    )
    generation = ROOT / "releases" / release_id
    expected = {
        "release_id": release_id,
        "container_id": container_id,
        "image_id": inspect["image"],
        "pid": str(inspect["pid"]),
        "machine_sha": sha256(
            read_exact_file(Path("/etc/machine-id"), limit=128, modes=(0o444, 0o644), uid=0, gid=0)
        ),
        "env_sha": sha256(
            read_exact_file(
                generation / "release.env",
                limit=65536,
                modes=(0o600,),
                uid=deploy.pw_uid,
                gid=deploy.pw_gid,
            )
        ),
        "verifier_sha": sha256(
            read_exact_file(
                generation / "verify.py",
                limit=262144,
                modes=(0o555,),
                uid=deploy.pw_uid,
                gid=deploy.pw_gid,
            )
        ),
        "cgroup_sha": sha256(cgroup_relative_path(inspect["pid"], container_id).encode()),
    }

    def current() -> tuple[str, str]:
        return check_identity(expected, deploy.pw_uid, deploy.pw_gid, candidate=True)

    identity = current()
    pidfd_open = getattr(os, "pidfd_open", None)
    if not callable(pidfd_open):
        raise RepairRefused("pidfd_unavailable")
    pid_fd = cast(int, pidfd_open(inspect["pid"]))
    receipt: dict[str, Any] = {"writeAttempted": False, "writeCompleted": False}
    try:
        directory_fd, memory_fd, swap_fd = open_cgroup_leaf(identity[0])
        try:
            assert_leaf_identity(directory_fd, memory_fd, swap_fd)
            require(read_scalar(memory_fd) == MEMORY_MAX, "memory_limit_drift")
            before = read_scalar(swap_fd)
            require(before in ("0", "max"), "unexpected_swap_limit")
            if before == "max":
                write_leaf_once(
                    directory_fd, memory_fd, swap_fd, pid_fd, identity, current, receipt
                )
            require(current() == identity, "candidate_changed_after_enforcement")
            require_pidfd_alive(pid_fd)
            require(read_scalar(swap_fd) == "0", "host_swap_readback_failed")
            command = ["docker", "exec", "--user", "10001:10001", container_id, "cat"]
            memory = run_bounded([*command, "/sys/fs/cgroup/memory.max"], timeout=20)
            swap = run_bounded([*command, "/sys/fs/cgroup/memory.swap.max"], timeout=20)
            require(
                memory.code == 0
                and memory.stdout == b"1073741824\n"
                and not memory.stderr
                and swap.code == 0
                and swap.stdout == b"0\n"
                and not swap.stderr,
                "container_swap_readback_failed",
            )
            require(current() == identity, "candidate_changed_after_readback")
            require_pidfd_alive(pid_fd)
            assert_leaf_identity(directory_fd, memory_fd, swap_fd)
            return {"status": "passed", "hostSwapMax": "0", **receipt}
        finally:
            os.close(memory_fd)
            os.close(swap_fd)
            os.close(directory_fd)
    finally:
        os.close(pid_fd)


def parse_expected(arguments: list[str]) -> dict[str, str]:
    require(len(arguments) == 9, "argument_count")
    (
        source_commit,
        machine_sha,
        release_id,
        image_id,
        container_id,
        pid,
        cgroup_sha,
        verifier_sha,
        env_sha,
    ) = arguments
    require(re.fullmatch(r"[0-9a-f]{40}", source_commit) is not None, "source_commit_shape")
    require(
        re.fullmatch(r"sha-[0-9a-f]{40}-r[0-9]+a[0-9]+", release_id) is not None,
        "release_id_shape",
    )
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id) is not None, "image_id_shape")
    require(re.fullmatch(r"[0-9a-f]{64}", container_id) is not None, "container_id_shape")
    require(re.fullmatch(r"[1-9][0-9]*", pid) is not None, "pid_shape")
    for value in (machine_sha, cgroup_sha, verifier_sha, env_sha):
        require(re.fullmatch(r"[0-9a-f]{64}", value) is not None, "digest_shape")
    return {
        "source_commit": source_commit,
        "machine_sha": machine_sha,
        "release_id": release_id,
        "image_id": image_id,
        "container_id": container_id,
        "pid": pid,
        "cgroup_sha": cgroup_sha,
        "verifier_sha": verifier_sha,
        "env_sha": env_sha,
    }


def repair(expected: dict[str, str], receipt: dict[str, Any]) -> dict[str, Any]:
    require(os.geteuid() == 0, "root_required")
    deploy = pwd.getpwnam("deploy")
    host_fd = acquire_existing_lock(HOST_LOCK, uid=0, gid=deploy.pw_gid, mode=0o640)
    try:
        renderer_fd = acquire_existing_lock(
            RENDERER_LOCK, uid=deploy.pw_uid, gid=deploy.pw_gid, mode=0o600
        )
        try:
            relative, start = check_identity(expected, deploy.pw_uid, deploy.pw_gid)
            pidfd_open = getattr(os, "pidfd_open", None)
            if pidfd_open is None or not callable(pidfd_open):
                raise RepairRefused("pidfd_unavailable")
            pid_fd = cast(int, pidfd_open(int(expected["pid"])))
            try:
                directory_fd, memory_fd, swap_fd = open_cgroup_leaf(relative)
                try:
                    host_memory, host_swap = write_leaf_once(
                        directory_fd,
                        memory_fd,
                        swap_fd,
                        pid_fd,
                        (relative, start),
                        lambda: check_identity(expected, deploy.pw_uid, deploy.pw_gid),
                        receipt,
                    )
                finally:
                    os.close(memory_fd)
                    os.close(swap_fd)
                    os.close(directory_fd)
                relative_after, start_after = check_identity(expected, deploy.pw_uid, deploy.pw_gid)
                require(
                    relative_after == relative and start_after == start,
                    "identity_changed_after_write",
                )
                docker_exec = [
                    "docker",
                    "exec",
                    "--user",
                    "10001:10001",
                    expected["container_id"],
                ]
                memory = run_bounded([*docker_exec, "cat", "/sys/fs/cgroup/memory.max"], timeout=20)
                swap = run_bounded(
                    [*docker_exec, "cat", "/sys/fs/cgroup/memory.swap.max"], timeout=20
                )
                require(
                    memory.code == 0
                    and memory.stdout == b"1073741824\n"
                    and not memory.stderr
                    and swap.code == 0
                    and swap.stdout == b"0\n"
                    and not swap.stderr,
                    "container_swap_readback_failed",
                )
                generation = ROOT / "releases" / expected["release_id"]
                verifier = run_bounded(
                    [
                        "runuser",
                        "-u",
                        "deploy",
                        "--",
                        "python3",
                        str(generation / "verify.py"),
                        "running",
                        str(generation / "release.env"),
                        "--allow-active",
                        "--expected-id",
                        expected["container_id"],
                    ],
                    timeout=90,
                )
                receipt["verifierExit"] = verifier.code
                receipt["verifierStdoutBase64"] = base64.b64encode(verifier.stdout).decode()
                receipt["verifierStderrBase64"] = base64.b64encode(verifier.stderr).decode()
                require(
                    verifier.code == 0
                    and re.fullmatch(rb"[0-9a-f]{64}\n", verifier.stdout) is not None
                    and not verifier.stderr,
                    "published_verifier_failed",
                )
                relative_final, start_final = check_identity(expected, deploy.pw_uid, deploy.pw_gid)
                require(
                    relative_final == relative and start_final == start,
                    "identity_changed_after_verification",
                )
                return {
                    "status": "passed",
                    "policySourceCommit": expected["source_commit"],
                    "releaseId": expected["release_id"],
                    "containerId": expected["container_id"],
                    "imageId": expected["image_id"],
                    "pid": int(expected["pid"]),
                    "cgroupPathSha256": expected["cgroup_sha"],
                    "hostMemoryMax": host_memory,
                    "hostSwapMax": host_swap,
                    "containerMemoryMax": memory.stdout.decode().strip(),
                    "containerSwapMax": swap.stdout.decode().strip(),
                    "negativeTlsBoundaryIncluded": True,
                }
            finally:
                os.close(pid_fd)
        finally:
            os.close(renderer_fd)
    finally:
        os.close(host_fd)


def main() -> int:
    receipt: dict[str, Any] = {"status": "refused", "writeAttempted": False}
    try:
        expected = parse_expected(sys.argv[1:])
        receipt.update(
            policySourceCommit=expected["source_commit"],
            releaseId=expected["release_id"],
            containerId=expected["container_id"],
            imageId=expected["image_id"],
            pid=int(expected["pid"]),
            cgroupPathSha256=expected["cgroup_sha"],
        )
        result = repair(expected, receipt)
        receipt.update(result)
        status = 0
    except RepairRefused as exc:
        receipt["category"] = str(exc)
        status = 1
    except (
        OSError,
        subprocess.SubprocessError,
        ValueError,
        UnicodeError,
        KeyError,
        IndexError,
        TypeError,
    ) as exc:
        receipt["category"] = type(exc).__name__
        status = 1
    print(json.dumps(receipt, sort_keys=True, separators=(",", ":")), flush=True)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
