"""Regression coverage for serving renderer deployment and native maintenance consumers."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
import sys
import time
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "serving_verify", Path(__file__).with_name("verify.py")
)
assert SPEC and SPEC.loader
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)
COMMAND = [
    "--runtime-v1-service",
    "--deployment-deny-cidr",
    "192.0.2.1/32",
    "--deployment-deny-cidr",
    "2001:db8::/32",
]
BASELINE = re.search(
    r'const baselineBlockedCIDRs = "([^"]+)"',
    (ROOT / "apps/lightpanda/egress_policy.go").read_text(),
)[1]


@pytest.mark.parametrize(
    ("stderr", "expected"),
    [
        (
            "runtime-v1 probe listener startup timed out\n",
            "runtime-v1 probe listener startup timed out",
        ),
        (
            "runtime-v1 service accepted a client without a certificate\n",
            "runtime-v1 service accepted a client without a certificate",
        ),
        ("private-address credential-data\nsecond line", "unclassified failure"),
    ],
)
def test_no_client_probe_failure_is_closed_and_sanitized(
    monkeypatch: pytest.MonkeyPatch, stderr: str, expected: str
) -> None:
    def failed(_arguments: list[str]) -> str:
        raise subprocess.CalledProcessError(1, ["private-command"], stderr=stderr)

    monkeypatch.setattr(verify, "run_text", failed)
    with pytest.raises(verify.VerificationError) as raised:
        verify.verify_no_client_probe(["private-command"])
    assert str(raised.value) == f"renderer no-client TLS probe failed: {expected}"


def child_arguments(*, proxy: bool = False) -> list[str]:
    args = [
        "/usr/local/bin/lightpanda",
        "serve",
        "--host",
        "127.0.0.1",
        "--port",
        "14000",
        "--log-level",
        "error",
        "--cdp-max-connections",
        "2",
        "--cdp-max-pending-connections",
        "1",
        "--http-max-concurrent",
        "8",
        "--v8-max-heap-mb",
        "128",
        "--http-max-host-open",
        "4",
        "--http-connect-timeout",
        "5000",
        "--http-max-response-size",
        "8388608",
        "--ws-max-concurrent",
        "1",
        "--block-private-networks",
        "--block-cidrs",
        "0.0.0.0/0,::/0,-127.0.0.1/32" if proxy else BASELINE,
    ]
    if proxy:
        args += ["--http-proxy", "http://127.0.0.1:15000"]
    return args


def process_output(children: list[str]) -> str:
    return "\n".join(
        [
            "PID PPID UID GID COMMAND COMMAND",
            "101 1 0 0 docker-init "
            + " ".join(["/sbin/docker-init", "--", *verify.BOOTSTRAP_ENTRYPOINT, *COMMAND]),
            "102 101 10001 10001 go-lightpanda "
            + " ".join(["/usr/local/bin/go-lightpanda", *COMMAND]),
            *children,
        ]
    )


def test_active_renderer_keeps_exact_controller_and_bounded_children(monkeypatch):
    for count in range(5):
        children = [
            f"{103 + i} 102 10002 10002 lightpanda " + " ".join(child_arguments())
            for i in range(count)
        ]
        monkeypatch.setattr(verify, "run_text", lambda _, c=children: process_output(c))
        assert verify.verify_serving_process_boundary(COMMAND) is (count == 0)


@pytest.mark.parametrize(
    "replacement",
    [
        "103 999 10002 10002 lightpanda ",
        "103 102 10001 10001 lightpanda ",
        "103 102 10002 10002 sh ",
        "102 102 10002 10002 lightpanda ",
    ],
)
def test_active_renderer_rejects_wrong_identity_parent_or_executable(monkeypatch, replacement):
    monkeypatch.setattr(
        verify,
        "run_text",
        lambda _: process_output([replacement + " ".join(child_arguments())]),
    )
    with pytest.raises(verify.VerificationError):
        verify.verify_serving_process_boundary(COMMAND)


def test_active_renderer_rejects_excess_children(monkeypatch):
    children = [
        f"{103 + i} 102 10002 10002 lightpanda " + " ".join(child_arguments()) for i in range(5)
    ]
    monkeypatch.setattr(verify, "run_text", lambda _: process_output(children))
    with pytest.raises(verify.VerificationError):
        verify.verify_serving_process_boundary(COMMAND)


@pytest.mark.parametrize("proxy", [False, True])
def test_child_arguments_match_direct_and_pinned_proxy_launches(proxy):
    args = child_arguments(proxy=proxy)
    verify.validate_render_child_arguments(args, COMMAND)
    trampoline = [
        "/usr/bin/setpriv",
        "--inh-caps=-all",
        "--ambient-caps=-all",
        "--nnp",
        "--",
    ]
    verify.validate_render_child_arguments(trampoline + args, COMMAND)
    if proxy:
        verify.validate_render_child_arguments(
            [
                "/usr/local/bin/go-lightpanda",
                "--runtime-v1-pinned-proxy-child",
                "15000",
                *args,
            ],
            COMMAND,
        )


@pytest.mark.parametrize(
    "index,value",
    [
        (0, "/bin/sh"),
        (3, "0.0.0.0"),
        (5, "65536"),
        (15, "256"),
        (21, "16777216"),
        (26, "192.0.2.1/32,2001:db8::/32"),
    ],
)
def test_child_resource_and_egress_changes_are_rejected(index, value):
    args = child_arguments()
    args[index] = value
    with pytest.raises(verify.VerificationError):
        verify.validate_render_child_arguments(args, COMMAND)


def test_proxy_bypass_or_wrapper_port_change_is_rejected():
    args = child_arguments(proxy=True)
    for altered in [
        args[:-1] + ["http://example.com:15000"],
        args[:26] + ["0.0.0.0/0,::/0,-0.0.0.0/0"] + args[27:],
        [
            "/usr/local/bin/go-lightpanda",
            "--runtime-v1-pinned-proxy-child",
            "15001",
            *args,
        ],
    ]:
        with pytest.raises(verify.VerificationError):
            verify.validate_render_child_arguments(altered, COMMAND)


def test_verifier_baseline_is_bound_to_pinned_controller_source():
    tree = ast.parse(Path(verify.__file__).read_text())
    constants = [
        ast.literal_eval(node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == "baseline" for t in node.targets)
    ]
    assert constants == [BASELINE]


@pytest.mark.parametrize("name", ["sync-data.yml", "refresh-currency-rates.yml"])
@pytest.mark.parametrize(
    "capability,expected",
    [
        ("v1", ["ordinary-go", "lease-reaper"]),
        ("", ["worker-1", "worker-2", "worker-3", "browser-1"]),
        ("<no value>", ["worker-1", "worker-2", "worker-3", "browser-1"]),
        ("v2", None),
    ],
)
def test_maintenance_consumers_select_actual_committed_runtime(name, capability, expected):
    workflow = yaml.safe_load((ROOT / ".github/workflows" / name).read_text())
    script = next(
        s["with"]["script"]
        for job in workflow["jobs"].values()
        for s in job["steps"]
        if "with" in s and "script" in s["with"]
    )
    start = script.index("maintenance_services=(")
    end = script.index("done", start) + 4
    selection = script[start:end]
    command = (
        'docker() { printf "%s\\n" "$CAPABILITY"; }; '
        "image_refs=(ghcr.io/colophon-group/jobseek-crawler@sha256:"
        + "a" * 64
        + "); "
        + selection
        + '; printf "%s\\n" "${maintenance_expectations[@]}"'
    )
    result = subprocess.run(
        ["bash", "-eu", "-c", command],
        env={"PATH": "/usr/bin:/bin", "CAPABILITY": capability},
        capture_output=True,
        text=True,
        check=False,
    )
    if expected is None:
        assert result.returncode != 0
    else:
        assert result.returncode == 0
        assert result.stdout.splitlines() == [
            part
            for service in ["redis", "exporter", "drain", "alloy", *expected]
            for part in ["--expect-service", service]
        ]
    assert '"${maintenance_expectations[@]}" \\' in script


SWAP_SPEC = importlib.util.spec_from_file_location(
    "renderer_swap_repair", Path(__file__).with_name("repair-swap.py")
)
assert SWAP_SPEC and SWAP_SPEC.loader
swap_repair = importlib.util.module_from_spec(SWAP_SPEC)
sys.modules[SWAP_SPEC.name] = swap_repair
SWAP_SPEC.loader.exec_module(swap_repair)


def test_zero_swap_repair_requires_exact_target_inputs():
    valid = [
        "a" * 40,
        "b" * 64,
        "sha-" + "c" * 40 + "-r123a1",
        "sha256:" + "d" * 64,
        "e" * 64,
        "2417",
        "f" * 64,
        "1" * 64,
        "2" * 64,
    ]
    assert swap_repair.parse_expected(valid)["release_id"] == valid[2]
    for index, replacement in ((2, "../../other"), (4, "e" * 63), (5, "0")):
        changed = valid.copy()
        changed[index] = replacement
        with pytest.raises(swap_repair.RepairRefused):
            swap_repair.parse_expected(changed)


def test_zero_swap_repair_accepts_only_exact_docker_cgroup_leaf(monkeypatch):
    container_id = "a" * 64
    text = f"0::/system.slice/docker-{container_id}.scope\n"
    mountinfo = "37 25 0:32 / /sys/fs/cgroup rw - cgroup2 cgroup rw\n"

    def read_text(path, **_kwargs):
        if str(path) == "/proc/self/mountinfo":
            return mountinfo
        return text

    monkeypatch.setattr(swap_repair.Path, "read_text", read_text)
    assert swap_repair.cgroup_relative_path(2417, container_id) == text[3:].strip()
    text = f"0::/system.slice/docker-{container_id}.scope/other\n"
    with pytest.raises(swap_repair.RepairRefused, match="cgroup_leaf_identity_drift"):
        swap_repair.cgroup_relative_path(2417, container_id)
    mountinfo = "37 25 0:32 /other /sys/fs/cgroup rw - cgroup2 cgroup rw\n"
    with pytest.raises(swap_repair.RepairRefused, match="cgroup_v2_mount_required"):
        swap_repair.cgroup_relative_path(2417, container_id)


def test_zero_swap_repair_rejects_image_reference_drift_before_leaf(monkeypatch):
    expected = {
        "machine_sha": swap_repair.sha256(b"machine-id\n"),
        "release_id": "sha-" + "a" * 40 + "-r123a1",
        "container_id": "c" * 64,
        "image_id": "sha256:" + "d" * 64,
        "pid": "2417",
        "cgroup_sha": swap_repair.sha256(b"/system.slice/docker-c.scope"),
    }
    image_ref = "ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:" + "e" * 64
    monkeypatch.setattr(swap_repair, "read_exact_file", lambda *_args, **_kwargs: b"machine-id\n")
    monkeypatch.setattr(swap_repair, "release_identity", lambda *_args: image_ref)
    monkeypatch.setattr(
        swap_repair,
        "docker_identity",
        lambda: {
            "id": expected["container_id"],
            "image": expected["image_id"],
            "pid": 2417,
            "running": True,
            "memory": 1073741824,
            "memory_swap": 1073741824,
            "cgroupns": "private",
            "config_image": "ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:" + "f" * 64,
            "labels": {},
        },
    )
    monkeypatch.setattr(
        swap_repair,
        "cgroup_relative_path",
        lambda *_args: pytest.fail("cgroup leaf must not be reached after image drift"),
    )
    with pytest.raises(swap_repair.RepairRefused, match="container_identity_drift"):
        swap_repair.check_identity(expected, 1000, 1000)


@pytest.mark.parametrize(
    ("before_memory", "before_swap", "current_identity", "expected_writes"),
    [
        ("1073741824", "max", ("/changed", "12"), 0),
        ("1073741824", "0", ("/docker", "12"), 0),
        ("max", "max", ("/docker", "12"), 0),
        ("1073741824", "max", ("/docker", "12"), 1),
    ],
)
def test_zero_swap_repair_has_one_guarded_write(
    monkeypatch, before_memory, before_swap, current_identity, expected_writes
):
    values = {11: before_memory, 12: before_swap}
    writes = []
    monkeypatch.setattr(swap_repair, "assert_leaf_identity", lambda *_args: None)
    monkeypatch.setattr(swap_repair, "require_pidfd_alive", lambda *_args: None)
    monkeypatch.setattr(swap_repair, "read_scalar", lambda fd: values[fd])
    monkeypatch.setattr(swap_repair.os, "lseek", lambda *_args: 0)

    def write(fd, data):
        writes.append((fd, data))
        values[fd] = "0"
        return len(data)

    monkeypatch.setattr(swap_repair.os, "write", write)
    receipt = {"writeAttempted": False}
    if expected_writes:
        assert swap_repair.write_leaf_once(
            10, 11, 12, 13, ("/docker", "12"), lambda: current_identity, receipt
        ) == ("1073741824", "0")
        assert receipt["writeCompleted"] is True
        assert receipt["hostSwapMax"] == "0"
    else:
        with pytest.raises(swap_repair.RepairRefused):
            swap_repair.write_leaf_once(
                10, 11, 12, 13, ("/docker", "12"), lambda: current_identity, receipt
            )
        assert receipt["writeAttempted"] is False
    assert writes == ([(12, b"0\n")] if expected_writes else [])


def test_zero_swap_repair_never_retries_an_uncertain_write(monkeypatch):
    values = {11: "1073741824", 12: "max"}
    attempts = []
    monkeypatch.setattr(swap_repair, "assert_leaf_identity", lambda *_args: None)
    monkeypatch.setattr(swap_repair, "require_pidfd_alive", lambda *_args: None)
    monkeypatch.setattr(swap_repair, "read_scalar", lambda fd: values[fd])
    monkeypatch.setattr(swap_repair.os, "lseek", lambda *_args: 0)

    def failed_write(fd, data):
        attempts.append((fd, data))
        raise OSError("uncertain kernel write")

    monkeypatch.setattr(swap_repair.os, "write", failed_write)
    receipt = {"writeAttempted": False}
    with pytest.raises(OSError):
        swap_repair.write_leaf_once(
            10, 11, 12, 13, ("/docker", "12"), lambda: ("/docker", "12"), receipt
        )
    assert attempts == [(12, b"0\n")]
    assert receipt["writeAttempted"] is True
    assert "writeCompleted" not in receipt


@pytest.mark.parametrize("drift", ["identity", "pidfd", "none"])
def test_zero_swap_repair_guards_final_write_boundary(monkeypatch, drift):
    events = []
    values = {11: "1073741824", 12: "max"}
    monkeypatch.setattr(swap_repair, "assert_leaf_identity", lambda *_args: events.append("leaf"))
    monkeypatch.setattr(swap_repair, "read_scalar", lambda fd: values[fd])
    monkeypatch.setattr(swap_repair.os, "lseek", lambda *_args: events.append("seek"))

    def current_identity():
        events.append("identity")
        return ("/changed", "12") if drift == "identity" else ("/docker", "12")

    def pidfd_alive(_fd):
        events.append("pidfd")
        if drift == "pidfd":
            raise swap_repair.RepairRefused("renderer_pid_exited")

    monkeypatch.setattr(swap_repair, "require_pidfd_alive", pidfd_alive)

    def write(_fd, _data):
        events.append("write")
        values[12] = "0"
        return 2

    monkeypatch.setattr(swap_repair.os, "write", write)
    receipt = {"writeAttempted": False}
    if drift == "none":
        assert swap_repair.write_leaf_once(
            10, 11, 12, 13, ("/docker", "12"), current_identity, receipt
        ) == ("1073741824", "0")
        assert events[events.index("identity") : events.index("write") + 1] == [
            "identity",
            "leaf",
            "pidfd",
            "write",
        ]
        assert receipt["writeAttempted"] is True
    else:
        with pytest.raises(swap_repair.RepairRefused):
            swap_repair.write_leaf_once(
                10, 11, 12, 13, ("/docker", "12"), current_identity, receipt
            )
        assert "write" not in events
        assert receipt["writeAttempted"] is False
    assert events.index("seek") < events.index("identity")


@pytest.mark.parametrize("poll_events", [[], [(13, 1)]])
def test_zero_swap_repair_pidfd_liveness_guard(monkeypatch, poll_events):
    class Poller:
        def register(self, _fd, _events):
            pass

        def poll(self, timeout):
            assert timeout == 0
            return poll_events

    monkeypatch.setattr(swap_repair.select, "poll", Poller)
    if poll_events:
        with pytest.raises(swap_repair.RepairRefused, match="renderer_pid_exited"):
            swap_repair.require_pidfd_alive(13)
    else:
        swap_repair.require_pidfd_alive(13)


def test_zero_swap_repair_bounds_subprocess_output_and_timeout():
    command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'A' * 10000)",
    ]
    with pytest.raises(swap_repair.RepairRefused, match="command_output_overflow"):
        swap_repair.run_bounded(command, timeout=5, cap=8192)
    start = time.monotonic()
    with pytest.raises(swap_repair.RepairRefused, match="command_timeout"):
        swap_repair.run_bounded([sys.executable, "-c", "import time; time.sleep(5)"], timeout=0.1)
    assert time.monotonic() - start < 2


@pytest.mark.parametrize(
    ("before", "drift_at", "readback", "passes", "writes"),
    [
        ("max", 0, True, True, 1),
        ("0", 0, True, True, 0),
        ("1", 0, True, False, 0),
        ("max", 2, True, False, 0),
        ("max", 3, True, False, 1),
        ("max", 0, False, False, 1),
    ],
)
def test_candidate_swap_enforcement(monkeypatch, before, drift_at, readback, passes, writes):
    from types import SimpleNamespace

    monkeypatch.setattr(swap_repair.os, "geteuid", lambda: 0)
    monkeypatch.setattr(
        swap_repair.pwd, "getpwnam", lambda _: SimpleNamespace(pw_uid=1000, pw_gid=1000)
    )
    monkeypatch.setattr(
        swap_repair,
        "docker_identity",
        lambda: {
            "id": "c" * 64,
            "running": True,
            "pid": 2417,
            "image": "sha256:" + "d" * 64,
        },
    )
    monkeypatch.setattr(swap_repair, "read_exact_file", lambda *_a, **_k: b"bound bytes")
    monkeypatch.setattr(swap_repair, "cgroup_relative_path", lambda *_a: "/docker")
    calls = []

    def identity(*_args, **kwargs):
        assert kwargs == {"candidate": True}
        calls.append(True)
        return ("/changed" if len(calls) == drift_at else "/docker", "12")

    monkeypatch.setattr(swap_repair, "check_identity", identity)
    monkeypatch.setattr(swap_repair.os, "pidfd_open", lambda _pid: 13, raising=False)
    monkeypatch.setattr(swap_repair.os, "close", lambda _fd: None)
    monkeypatch.setattr(swap_repair, "require_pidfd_alive", lambda _fd: None)
    monkeypatch.setattr(swap_repair, "open_cgroup_leaf", lambda _: (10, 11, 12))
    monkeypatch.setattr(swap_repair, "assert_leaf_identity", lambda *_a: None)
    values = {11: "1073741824", 12: before}
    monkeypatch.setattr(swap_repair, "read_scalar", lambda fd: values[fd])
    monkeypatch.setattr(swap_repair.os, "lseek", lambda *_a: 0)
    attempts = []

    def write(fd, value):
        attempts.append((fd, value))
        values[fd] = "0"
        return len(value)

    monkeypatch.setattr(swap_repair.os, "write", write)

    def command(argv, **_kwargs):
        assert argv[:6] == ["docker", "exec", "--user", "10001:10001", "c" * 64, "cat"]
        body = b"1073741824\n" if argv[-1].endswith("memory.max") else b"0\n"
        return swap_repair.CommandResult(0 if readback else 1, body, b"")

    monkeypatch.setattr(swap_repair, "run_bounded", command)
    if passes:
        result = swap_repair.enforce_candidate_zero_swap("sha-" + "a" * 40 + "-r123a1", "c" * 64)
        assert result["status"] == "passed" and result["hostSwapMax"] == "0"
        assert result["writeAttempted"] == bool(writes)
    else:
        with pytest.raises(swap_repair.RepairRefused):
            swap_repair.enforce_candidate_zero_swap("sha-" + "a" * 40 + "-r123a1", "c" * 64)
    assert attempts == [(12, b"0\n")] * writes


def test_candidate_release_identity_does_not_adopt_active_generation(monkeypatch, tmp_path):
    import os

    release_id = "sha-" + "a" * 40 + "-r123a1"
    generation = tmp_path / "releases" / release_id
    generation.mkdir(parents=True)
    env = generation / "release.env"
    env.write_text(
        f"SOURCE_COMMIT={'a' * 40}\nRELEASE_ID={release_id}\nRENDERER_RELEASE_DIR={generation}\n"
        f"RENDERER_IMAGE_REF=ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:{'b' * 64}\n"
    )
    env.chmod(0o600)
    verifier = generation / "verify.py"
    verifier.write_text("not executed\n")
    verifier.chmod(0o555)
    expected = {
        "release_id": release_id,
        "env_sha": swap_repair.sha256(env.read_bytes()),
        "verifier_sha": swap_repair.sha256(verifier.read_bytes()),
    }
    monkeypatch.setattr(swap_repair, "ROOT", tmp_path)
    monkeypatch.setattr(swap_repair, "ACTIVE", tmp_path / "absent-active")
    assert swap_repair.release_identity(
        expected, os.getuid(), os.getgid(), candidate=True
    ).endswith("b" * 64)
    with pytest.raises(FileNotFoundError):
        swap_repair.release_identity(expected, os.getuid(), os.getgid())
    env.write_text(env.read_text().replace("b" * 64, "c" * 64))
    with pytest.raises(swap_repair.RepairRefused, match="release_environment_drift"):
        swap_repair.release_identity(expected, os.getuid(), os.getgid(), candidate=True)


def test_installer_enforces_each_new_leaf_before_verification():
    source = Path(__file__).with_name("install-host.sh").read_text()
    first = source.index('sudo -n "$POLICY" enforce-candidate-zero-swap')
    assert source.index('up --detach --no-deps "$SERVICE"') < first
    assert first < source.index('first_verified_id="$(python3')
    restart = source.index('docker restart --time 30 "$candidate_container_id"')
    second = source.index('sudo -n "$POLICY" enforce-candidate-zero-swap', restart)
    assert restart < second < source.index("sleep 20", second)
    assert source.index("flock -w 900 7") < first
    assert source.index('acquire_renderer_lock "$ROOT/renderer.lock"') < first
    assert first < source.index('mv -Tf "$active_candidate" "$ACTIVE"')


def test_candidate_policy_pins_root_owned_helper():
    import hashlib

    policy = Path(__file__).with_name("network-policy.py").read_text()
    digest = hashlib.sha256(Path(__file__).with_name("repair-swap.py").read_bytes()).hexdigest()
    assert f'SWAP_HELPER_SHA256 = "{digest}"' in policy
    bootstrap = Path(__file__).with_name("bootstrap-host.sh").read_text()
    assert 'install -o root -g root -m 0555 "$STAGE/repair-swap.py"' in bootstrap


CANDIDATE_POLICY_SPEC = importlib.util.spec_from_file_location(
    "candidate_network_policy", Path(__file__).with_name("network-policy.py")
)
assert CANDIDATE_POLICY_SPEC and CANDIDATE_POLICY_SPEC.loader
candidate_policy = importlib.util.module_from_spec(CANDIDATE_POLICY_SPEC)
sys.modules[CANDIDATE_POLICY_SPEC.name] = candidate_policy
CANDIDATE_POLICY_SPEC.loader.exec_module(candidate_policy)


@pytest.mark.parametrize("drift", ["none", "owner", "mode", "digest"])
def test_candidate_policy_loads_only_pinned_root_helper(monkeypatch, tmp_path, drift):
    import hashlib
    import stat
    from types import SimpleNamespace

    helper = tmp_path / "repair-swap.py"
    data = b"def enforce_candidate_zero_swap(release, container):\n return [release, container]\n"
    helper.write_bytes(data)
    monkeypatch.setattr(candidate_policy, "__file__", str(tmp_path / "network-policy.py"))
    digest = hashlib.sha256(data).hexdigest() if drift != "digest" else "a" * 64
    monkeypatch.setattr(candidate_policy, "SWAP_HELPER_SHA256", digest)
    monkeypatch.setattr(
        candidate_policy.os,
        "fstat",
        lambda _fd: SimpleNamespace(
            st_mode=stat.S_IFREG | (0o777 if drift == "mode" else 0o555),
            st_uid=1000 if drift == "owner" else 0,
            st_gid=0,
            st_nlink=1,
            st_size=len(data),
        ),
    )
    if drift == "none":
        assert candidate_policy.enforce_candidate_zero_swap("container", "release") == [
            "release",
            "container",
        ]
    else:
        with pytest.raises(candidate_policy.PolicyError):
            candidate_policy.enforce_candidate_zero_swap("container", "release")


def test_candidate_policy_attests_binding_and_network_before_enforcement(monkeypatch, capsys):
    events = []
    monkeypatch.setattr(candidate_policy.Inventory, "load", lambda _: object())
    monkeypatch.setattr(candidate_policy, "verify_policy", lambda _: events.append("policy") or {})
    monkeypatch.setattr(
        candidate_policy, "verify_bootstrap_marker", lambda *_: events.append("binding")
    )
    monkeypatch.setattr(candidate_policy, "verify_container", lambda *_: events.append("network"))
    monkeypatch.setattr(
        candidate_policy,
        "enforce_candidate_zero_swap",
        lambda *_: events.append("swap") or {"status": "passed"},
    )
    args = candidate_policy.parser().parse_args(
        [
            "enforce-candidate-zero-swap",
            "a" * 64,
            "b" * 64,
            "c" * 64,
            "sha-" + "d" * 40 + "-r1a1",
        ]
    )
    candidate_policy.execute_guarded_command(args)
    assert events == ["policy", "binding", "network", "swap"]
    assert '"candidateSwap":{"status":"passed"}' in capsys.readouterr().out
