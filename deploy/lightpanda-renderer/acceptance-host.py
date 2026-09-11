#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import pwd
import re
import secrets
import shlex
import socket
import stat
import subprocess
import sys
from pathlib import Path
from typing import Any, NoReturn

POLICY = Path("/usr/local/libexec/jobseek-lightpanda-network-policy")
INVENTORY = Path("/etc/jobseek-lightpanda-network/inventory.json")
UNIT = Path("/etc/systemd/system/jobseek-lightpanda-network.service")
RECEIPT_ROOT = Path("/var/lib/jobseek-lightpanda-acceptance")
RECEIPT = RECEIPT_ROOT / "phase-a.json"
ACTIVE = Path("/home/deploy/.local/share/jobseek-lightpanda/active")
CONTAINER = "jobseek-lightpanda-renderer"
NETWORKS = ("jobseek-lightpanda-renderer", "jobseek-lightpanda-egress")
PROTECTED = ("deploy-cloudflared-1", "deploy-murmur-1")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
IMAGE_RE = re.compile(r"^ghcr\.io/colophon-group/jobseek-lightpanda-renderer@sha256:[0-9a-f]{64}$")
BOOT_RE = re.compile(r"^[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}$")


class AcceptanceError(RuntimeError):
    pass


def fail(message: str) -> NoReturn:
    raise AcceptanceError(message)


def run(arguments: list[str], *, timeout: int = 90) -> str:
    return subprocess.run(
        arguments, check=True, capture_output=True, text=True, timeout=timeout
    ).stdout.strip()


def digest(value: object) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def stable_host_config(value: dict[str, Any]) -> dict[str, Any]:
    """Normalize Docker's restart-only representation changes, not policy values."""
    result = dict(value)
    for name in ("DnsOptions", "DnsSearch"):
        if result.get(name) is None:
            result[name] = []
    mounts = result.get("Mounts")
    if isinstance(mounts, list):
        result["Mounts"] = sorted(
            mounts,
            key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
        )
    return result


def stable_mounts(value: object) -> object:
    if not isinstance(value, list):
        return value
    return sorted(
        value,
        key=lambda item: json.dumps(item, sort_keys=True, separators=(",", ":")),
    )


def file_digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink() or path.stat().st_size > 512 * 1024:
        fail("acceptance input is invalid")
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        fail("acceptance input is not an object")
    return value


def load_verifier(path: Path) -> Any:
    if not path.is_absolute() or not re.fullmatch(
        r"/tmp/jobseek-lightpanda-acceptance\.[A-Za-z0-9]+/verify\.py", str(path)
    ):
        fail("verifier path is not an acceptance stage")
    metadata = path.stat(follow_symlinks=False)
    if (
        path.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_uid != 0
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        fail("verifier metadata drifted")
    spec = importlib.util.spec_from_file_location("acceptance_verify", path)
    if spec is None or spec.loader is None:
        fail("verifier cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def component_digests(args: argparse.Namespace) -> dict[str, str]:
    expected = {
        "policy_sha256": args.policy_sha256,
        "inventory_sha256": args.inventory_sha256,
        "unit_sha256": args.unit_sha256,
    }
    if any(SHA_RE.fullmatch(value) is None for value in expected.values()):
        fail("component digest is invalid")
    actual = {
        "policy_sha256": file_digest(POLICY),
        "inventory_sha256": file_digest(INVENTORY),
        "unit_sha256": file_digest(UNIT),
    }
    if actual != expected:
        fail("installed host component differs from dispatched source")
    if (
        run(["systemctl", "is-active", UNIT.name]) != "active"
        or run(["systemctl", "is-enabled", UNIT.name]) != "enabled"
        or run(["systemctl", "show", "-p", "Result", "--value", UNIT.name]) != "success"
    ):
        fail("installed host policy unit is not durably active")
    return actual


def container_names(*, running: bool) -> list[str]:
    arguments = ["docker", "container", "ls"]
    if not running:
        arguments.append("--all")
    arguments.extend(("--format", "{{.Names}}"))
    names = run(arguments).splitlines()
    if len(names) != len(set(names)) or any(not name for name in names):
        fail("Docker container inventory is invalid")
    return sorted(names)


def network_counts() -> dict[str, int]:
    values = json.loads(run(["docker", "network", "inspect", *NETWORKS]))
    result = {str(item["Name"]): len(item.get("Containers") or {}) for item in values}
    if set(result) != set(NETWORKS):
        fail("renderer network inventory drifted")
    return result


def renderer_snapshot(verifier: Any, source: str, image: str, mode: str) -> dict[str, Any]:
    if (
        COMMIT_RE.fullmatch(source) is None
        or IMAGE_RE.fullmatch(image) is None
        or not ACTIVE.is_symlink()
    ):
        fail("renderer source or active release is invalid")
    release = ACTIVE.resolve(strict=True)
    environment = release / "release.env"
    env = verifier.read_env(environment)
    if env["SOURCE_COMMIT"] != source or env["RENDERER_IMAGE_REF"] != image:
        fail("renderer is not the dispatched renderer component")
    inspect = json.loads(run(["docker", "inspect", CONTAINER]))
    if len(inspect) != 1:
        fail("renderer identity is not exact")
    item = inspect[0]
    container_id = str(item.get("Id", ""))
    if mode == "running":
        verifier.verify_running(environment, expected_id=container_id)
    else:
        verifier.verify_owned(environment, expected_id=container_id)
    state = item.get("State") or {}
    host = item.get("HostConfig") or {}
    return {
        "container_id": container_id,
        "image_id": item.get("Image"),
        "image_ref": image,
        "source_commit": source,
        "release_id": env["RELEASE_ID"],
        "running": state.get("Running"),
        "status": state.get("Status"),
        "started_at": state.get("StartedAt"),
        "finished_at": state.get("FinishedAt"),
        "exit_code": state.get("ExitCode"),
        "oom_killed": state.get("OOMKilled"),
        "restart_count": item.get("RestartCount"),
        "restart_policy": host.get("RestartPolicy"),
        "config_sha256": digest(item.get("Config")),
        "host_config_sha256": digest(stable_host_config(host)),
        "mounts_sha256": digest(stable_mounts(item.get("Mounts"))),
    }


def snapshot(args: argparse.Namespace) -> dict[str, Any]:
    if os.geteuid() != 0:
        fail("host acceptance requires root")
    verifier = load_verifier(args.verifier)
    components = component_digests(args)
    protected = verifier.snapshot_protected()
    all_names = container_names(running=False)
    running_names = container_names(running=True)
    counts = network_counts()
    renderer = None
    receipt_sha256 = None
    policy_mode = "verify-ready"
    if args.mode == "empty":
        if all_names != sorted(PROTECTED) or running_names or any(counts.values()):
            fail("phase-A host is not exactly empty")
    else:
        expected_names = sorted((*PROTECTED, CONTAINER))
        if all_names != expected_names:
            fail("Murmur Docker allowlist drifted")
        verifier.verify_phase_a_receipt(
            RECEIPT,
            components["policy_sha256"],
            components["inventory_sha256"],
            components["unit_sha256"],
        )
        receipt_sha256 = file_digest(RECEIPT)
        renderer = renderer_snapshot(
            verifier, args.renderer_source, args.renderer_image_ref, args.mode
        )
        if args.mode == "running":
            policy_mode = "verify-running-ready"
            if running_names != [CONTAINER] or counts != {name: 1 for name in NETWORKS}:
                fail("running renderer host shape drifted")
        elif running_names or any(counts.values()):
            fail("cold renderer retained a running workload or endpoint")
    json.loads(
        run(
            [
                str(POLICY),
                policy_mode,
                components["policy_sha256"],
                components["inventory_sha256"],
            ],
            timeout=120,
        )
    )
    boot_id = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
    if BOOT_RE.fullmatch(boot_id) is None:
        fail("boot identity is invalid")
    return {
        "mode": args.mode,
        "boot_id": boot_id,
        "host_components": components,
        "protected_containers": protected,
        "container_names": all_names,
        "running_container_names": running_names,
        "network_endpoint_counts": counts,
        "phase_a_receipt_sha256": receipt_sha256,
        "renderer": renderer,
    }


def installed_counters() -> dict[tuple[str, ...], tuple[int, int]]:
    result = {}
    for line in run(["iptables-save", "-c", "-t", "filter"]).splitlines():
        tokens = shlex.split(line)
        match = re.fullmatch(r"\[(\d+):(\d+)\]", tokens[0]) if tokens else None
        if match is not None:
            rule = tuple(tokens[1:])
            if rule in result:
                fail("installed policy counter rule is duplicated")
            result[rule] = (int(match.group(1)), int(match.group(2)))
    return result


def exact_counter(
    counters: dict[tuple[str, ...], tuple[int, int]], chain: str, rule: tuple[str, ...]
) -> tuple[int, int]:
    try:
        return counters[("-A", chain, *rule)]
    except KeyError:
        fail("installed policy counter rule is not exact")


def traffic_counters() -> dict[str, Any]:
    inventory = json.loads(INVENTORY.read_text(encoding="utf-8"))
    counters = installed_counters()
    ingress_rule = tuple(
        f"-s {inventory['crawler_private_ipv4']} -d {inventory['egress_address']}/32 "
        f"-i {inventory['private_interface']} -p tcp -m tcp --dport {inventory['published_port']} "
        "-m conntrack --ctstate NEW -j ACCEPT".split()
    )
    ingress = exact_counter(counters, "JSLP4-INGRESS", ingress_rule)
    dns = [
        exact_counter(
            counters,
            "JSLP4-EGRESS",
            tuple(f"-d {resolver}/32 -p {protocol} -m {protocol} --dport 53 -j ACCEPT".split()),
        )
        for resolver in inventory["dns_resolvers"]
        for protocol in ("udp", "tcp")
    ]
    https = exact_counter(
        counters,
        "JSLP4-EGRESS",
        ("-p", "tcp", "-m", "tcp", "--dport", "443", "-j", "ACCEPT"),
    )
    drops = [
        {"rule": list(rule), "packets": value[0], "bytes": value[1]}
        for rule, value in sorted(counters.items())
        if len(rule) >= 4
        and rule[:2] in (("-A", "JSLP4-INGRESS"), ("-A", "JSLP4-EGRESS"))
        and rule[-2:] == ("-j", "DROP")
    ]
    if not drops:
        fail("installed policy DROP counters are absent")
    return {
        "schema_version": 1,
        "ingress_new": {"packets": ingress[0], "bytes": ingress[1]},
        "egress_dns": {
            "packets": sum(value[0] for value in dns),
            "bytes": sum(value[1] for value in dns),
        },
        "egress_https": {"packets": https[0], "bytes": https[1]},
        "drop_rules": drops,
    }


def traffic_deltas(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    expected = {"schema_version", "ingress_new", "egress_dns", "egress_https", "drop_rules"}
    if (
        before.get("schema_version") != 1
        or after.get("schema_version") != 1
        or set(before) != expected
        or set(after) != expected
    ):
        fail("traffic counter evidence is malformed")
    deltas = {}
    for name in ("ingress_new", "egress_dns", "egress_https"):
        prior, current = before.get(name), after.get(name)
        if (
            not isinstance(prior, dict)
            or not isinstance(current, dict)
            or set(prior) != {"packets", "bytes"}
            or set(current) != {"packets", "bytes"}
            or any(
                type(value) is not int or value < 0
                for value in (*prior.values(), *current.values())
            )
        ):
            fail("traffic counter evidence is malformed")
        delta = {key: current[key] - prior[key] for key in ("packets", "bytes")}
        if any(type(value) is not int or value <= 0 for value in delta.values()):
            fail("public render did not traverse every installed ACCEPT rule class")
        deltas[name] = delta
    prior_drops, current_drops = before["drop_rules"], after["drop_rules"]
    if (
        not isinstance(prior_drops, list)
        or not prior_drops
        or not isinstance(current_drops, list)
        or len(prior_drops) != len(current_drops)
    ):
        fail("traffic DROP counter evidence is malformed")
    drop_deltas = []
    seen_rules: set[tuple[str, ...]] = set()
    for prior, current in zip(prior_drops, current_drops, strict=True):
        if (
            not isinstance(prior, dict)
            or not isinstance(current, dict)
            or set(prior) != {"rule", "packets", "bytes"}
            or set(current) != set(prior)
            or prior["rule"] != current["rule"]
            or not isinstance(prior["rule"], list)
            or any(not isinstance(token, str) or not token for token in prior["rule"])
            or any(
                type(item[key]) is not int or item[key] < 0
                for item in (prior, current)
                for key in ("packets", "bytes")
            )
        ):
            fail("traffic DROP counter evidence is malformed")
        rule = tuple(prior["rule"])
        if rule in seen_rules:
            fail("traffic DROP counter evidence is duplicated")
        seen_rules.add(rule)
        delta = {key: current[key] - prior[key] for key in ("packets", "bytes")}
        if any(value != 0 for value in delta.values()):
            fail("public render traversed an installed DROP rule")
        drop_deltas.append({"rule": prior["rule"], **delta})
    deltas["drop_rules"] = drop_deltas
    return deltas


def connect_probe(host: str, port: int) -> dict[str, object]:
    try:
        with socket.create_connection((host, port), timeout=4):
            pass
    except OSError:
        return {"host": host, "port": port, "outcome": "blocked"}
    fail("forbidden target was reachable from the renderer namespace")


def negative_probes(args: argparse.Namespace) -> dict[str, Any]:
    state = snapshot(args)
    renderer = state["renderer"]
    inspect = json.loads(run(["docker", "inspect", renderer["container_id"]]))[0]
    pid = (inspect.get("State") or {}).get("Pid")
    if type(pid) is not int or pid <= 1:
        fail("renderer namespace is unavailable")
    results = []
    for host, port, cidr in (
        ("10.0.0.4", 22, "10.0.0.0/8"),
        ("169.254.169.254", 80, "169.254.0.0/16"),
    ):
        before = exact_counter(installed_counters(), "JSLP4-EGRESS", ("-d", cidr, "-j", "DROP"))
        output = run(
            [
                "nsenter",
                "--target",
                str(pid),
                "--net",
                "--",
                "python3",
                str(Path(__file__).resolve()),
                "connect",
                host,
                str(port),
            ],
            timeout=15,
        )
        result = json.loads(output)
        after = exact_counter(installed_counters(), "JSLP4-EGRESS", ("-d", cidr, "-j", "DROP"))
        if result.get("outcome") != "blocked" or after[0] <= before[0]:
            fail("installed policy did not account for the negative probe")
        results.append({**result, "policy_cidr": cidr, "packet_delta": after[0] - before[0]})
    after_state = snapshot(args)
    if (
        after_state["renderer"] != state["renderer"]
        or after_state["protected_containers"] != state["protected_containers"]
    ):
        fail("host identity changed during negative probes")
    return {"status": "accepted", "probes": results}


def atomic_receipt(payload: dict[str, Any]) -> None:
    deploy_gid = pwd.getpwnam("deploy").pw_gid
    try:
        os.mkdir(RECEIPT_ROOT, 0o750)
        os.chown(RECEIPT_ROOT, 0, deploy_gid)
        os.chmod(RECEIPT_ROOT, 0o750)
    except FileExistsError:
        pass
    root_metadata = RECEIPT_ROOT.stat(follow_symlinks=False)
    if (
        RECEIPT_ROOT.is_symlink()
        or not stat.S_ISDIR(root_metadata.st_mode)
        or (root_metadata.st_uid, root_metadata.st_gid) != (0, deploy_gid)
        or stat.S_IMODE(root_metadata.st_mode) != 0o750
    ):
        fail("phase-A receipt directory is unsafe")
    temporary = RECEIPT.parent / f".{RECEIPT.name}.{secrets.token_hex(8)}"
    descriptor = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_CLOEXEC | os.O_NOFOLLOW, 0o640
    )
    try:
        os.fchown(descriptor, 0, deploy_gid)
        os.fchmod(descriptor, 0o640)
        remaining = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        while remaining:
            written = os.write(descriptor, remaining)
            if written <= 0:
                fail("phase-A receipt write was incomplete")
            remaining = remaining[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    os.replace(temporary, RECEIPT)
    metadata = RECEIPT.stat(follow_symlinks=False)
    if (
        RECEIPT.is_symlink()
        or not stat.S_ISREG(metadata.st_mode)
        or (metadata.st_uid, metadata.st_gid) != (0, deploy_gid)
        or stat.S_IMODE(metadata.st_mode) != 0o640
        or metadata.st_nlink != 1
    ):
        fail("phase-A receipt publication metadata drifted")
    directory = os.open(RECEIPT.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)


def install_receipt(args: argparse.Namespace) -> dict[str, Any]:
    before, after = load_json(args.before), load_json(args.after)
    for item in (before, after):
        if item.get("mode") != "empty" or item.get("renderer") is not None:
            fail("phase-A receipt requires empty host snapshots")
    if (
        before["boot_id"] == after["boot_id"]
        or before["host_components"] != after["host_components"]
        or before["protected_containers"] != after["protected_containers"]
        or before["container_names"] != after["container_names"]
    ):
        fail("phase-A host identity changed across reboot")
    payload = {
        "schema_version": 1,
        "status": "accepted",
        "acceptance_source_commit": args.acceptance_source_commit,
        "host_components": after["host_components"],
        "before_boot_id": before["boot_id"],
        "after_boot_id": after["boot_id"],
        "protected_containers": after["protected_containers"],
    }
    atomic_receipt(payload)
    return payload


def phase_b_report(args: argparse.Namespace) -> dict[str, Any]:
    stages = [
        load_json(path)
        for path in (args.initial, args.after_probe, args.after_docker, args.after_reboot)
    ]
    if [item.get("mode") for item in stages] != ["running", "running", "cold", "cold"]:
        fail("renderer acceptance stage order drifted")
    first = stages[0]
    if (
        first["renderer"]["source_commit"] != args.renderer_source_commit
        or first["renderer"]["image_ref"] != args.renderer_image_ref
    ):
        fail("reported renderer identity differs from the explicit deployment")
    for item in stages[1:]:
        if (
            item["host_components"] != first["host_components"]
            or item["protected_containers"] != first["protected_containers"]
            or item["phase_a_receipt_sha256"] != first["phase_a_receipt_sha256"]
        ):
            fail("host identity changed during renderer acceptance")
    if (
        len({item["boot_id"] for item in stages[:3]}) != 1
        or stages[2]["boot_id"] == stages[3]["boot_id"]
    ):
        fail("renderer acceptance did not cross exactly one real reboot")
    immutable = (
        "container_id",
        "image_id",
        "image_ref",
        "source_commit",
        "release_id",
        "restart_policy",
        "config_sha256",
        "host_config_sha256",
        "mounts_sha256",
    )
    if any(
        {key: item["renderer"][key] for key in immutable}
        != {key: first["renderer"][key] for key in immutable}
        for item in stages[1:]
    ):
        fail("renderer identity changed across restart acceptance")
    crawler, probes = load_json(args.crawler), load_json(args.probes)
    traffic_before, traffic_after = load_json(args.traffic_before), load_json(args.traffic_after)
    traffic = traffic_deltas(traffic_before, traffic_after)
    if crawler.get("status") != "accepted" or probes.get("status") != "accepted":
        fail("functional acceptance evidence is incomplete")
    return {
        "schema_version": 1,
        "status": "accepted",
        "phase": "renderer-restarts",
        "acceptance_source_commit": args.acceptance_source_commit,
        "renderer_source_commit": args.renderer_source_commit,
        "renderer_image_ref": args.renderer_image_ref,
        "host_components": first["host_components"],
        "phase_a_receipt_sha256": first["phase_a_receipt_sha256"],
        "protected_containers": first["protected_containers"],
        "stages": stages,
        "crawler_mtls_public_render": crawler,
        "public_render_accept_rule_counters": {
            "before": traffic_before,
            "after": traffic_after,
            "deltas": traffic,
        },
        "installed_policy_negative_probes": probes,
    }


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser()
    sub = result.add_subparsers(dest="command", required=True)
    state = sub.add_parser("snapshot")
    state.add_argument("--mode", choices=("empty", "running", "cold"), required=True)
    state.add_argument("--verifier", type=Path, required=True)
    state.add_argument("--policy-sha256", required=True)
    state.add_argument("--inventory-sha256", required=True)
    state.add_argument("--unit-sha256", required=True)
    state.add_argument("--renderer-source", default="")
    state.add_argument("--renderer-image-ref", default="")
    sub.add_parser("negative-probes", parents=[state], add_help=False)
    connect = sub.add_parser("connect")
    connect.add_argument("host", choices=("10.0.0.4", "169.254.169.254"))
    connect.add_argument("port", type=int, choices=(22, 80))
    sub.add_parser("traffic-counters")
    receipt = sub.add_parser("install-receipt")
    receipt.add_argument("--before", type=Path, required=True)
    receipt.add_argument("--after", type=Path, required=True)
    receipt.add_argument("--acceptance-source-commit", required=True)
    report = sub.add_parser("report")
    for name in (
        "initial",
        "after-probe",
        "after-docker",
        "after-reboot",
        "crawler",
        "probes",
        "traffic-before",
        "traffic-after",
    ):
        report.add_argument(f"--{name}", type=Path, required=True)
    report.add_argument("--acceptance-source-commit", required=True)
    report.add_argument("--renderer-source-commit", required=True)
    report.add_argument("--renderer-image-ref", required=True)
    return result


def main() -> int:
    args = parser().parse_args()
    try:
        if args.command == "snapshot":
            payload = snapshot(args)
        elif args.command == "negative-probes":
            payload = negative_probes(args)
        elif args.command == "connect":
            payload = connect_probe(args.host, args.port)
        elif args.command == "traffic-counters":
            if os.geteuid() != 0:
                fail("traffic counter capture requires root")
            payload = traffic_counters()
        elif args.command == "install-receipt":
            if os.geteuid() != 0 or COMMIT_RE.fullmatch(args.acceptance_source_commit) is None:
                fail("receipt installation identity is invalid")
            payload = install_receipt(args)
        else:
            if (
                COMMIT_RE.fullmatch(args.acceptance_source_commit) is None
                or COMMIT_RE.fullmatch(args.renderer_source_commit) is None
                or IMAGE_RE.fullmatch(args.renderer_image_ref) is None
            ):
                fail("report source is invalid")
            payload = phase_b_report(args)
        print(json.dumps(payload, sort_keys=True, separators=(",", ":")))
        return 0
    except (
        AcceptanceError,
        KeyError,
        OSError,
        ValueError,
        json.JSONDecodeError,
        subprocess.SubprocessError,
    ) as error:
        print(f"Lightpanda host acceptance failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
