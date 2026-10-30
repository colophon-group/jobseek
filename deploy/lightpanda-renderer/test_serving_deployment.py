"""Regression coverage for serving renderer deployment and native maintenance consumers."""

from __future__ import annotations

import ast
import importlib.util
import re
import subprocess
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
    (ROOT / "pilots/go-lightpanda/egress_policy.go").read_text(),
)[1]


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
