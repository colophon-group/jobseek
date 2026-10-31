"""Compare the actual ordinary worker composition on identical isolated inputs."""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.x509.oid import ExtendedKeyUsageOID

from admission.lightpanda_b0.controller import (
    CA_PEM,
    CA_SCALAR,
    POSTGRES_IMAGE,
    PYTHON_IMAGE,
    REDIS_IMAGE,
    _atomic_evidence,
    _cgroup,
    _cpu_usage,
    _integer,
    _leaf,
    _memory_events,
    _write,
)
from admission.native_lane.resources import PRICES, allocation, sample
from admission.native_lane.workload import ORIGINS, workload_sha256

HERE = Path(__file__).resolve().parent
SUPPORT = ["postgres", "redis", "typesense", "fixture", "reaper", "exporter", "drain"]
ORDER = ("native", "legacy", "legacy", "native", "native", "legacy")


class ComparisonFailure(RuntimeError):
    def __init__(self, code: int, stderr: str) -> None:
        super().__init__("comparison subprocess failed")
        self.diagnostic = {
            "exit_code": code,
            "error_types": sorted(set(re.findall(r"\b([A-Z][A-Za-z]+Error)\b", stderr)))[:8],
            "driver_lines": sorted(
                set(
                    int(value)
                    for value in re.findall(r'native_lane/driver\.py", line ([0-9]+)', stderr)
                )
            )[:8],
        }


def run(command: list[str], *, env: dict[str, str] | None = None, timeout: int = 60) -> str:
    result = subprocess.run(command, env=env, capture_output=True, text=True, timeout=timeout)
    if result.returncode or len(result.stdout) > 2 * 1024 * 1024:
        # Fixture failures are diagnosed through selected CI logs, never by
        # copying subprocess environments, bodies or arbitrary SQL into reports.
        raise ComparisonFailure(result.returncode, result.stderr)
    return result.stdout


def compose(env: dict[str, str], *args: str, timeout: int = 60) -> str:
    return run(
        ["docker", "compose", "-f", str(HERE / "compose.yml"), *args], env=env, timeout=timeout
    )


def inspect(identifier: str) -> dict[str, Any]:
    return json.loads(run(["docker", "inspect", identifier]))[0]


def startup_diagnostics(env: dict[str, str], services: list[str]) -> list[dict[str, Any]]:
    output = []
    for service in services:
        row: dict[str, Any] = {"service": service}
        try:
            identifier = compose(env, "ps", "-a", "-q", service).strip()
            if not identifier:
                row["state"] = "missing"
            else:
                item = inspect(identifier)
                state = item["State"]
                row.update(
                    running=bool(state["Running"]),
                    exit_code=int(state["ExitCode"]),
                    oom=bool(state["OOMKilled"]),
                    restarts=int(item["RestartCount"]),
                )
                health = state.get("Health", {}).get("Status", "none")
                row["health"] = (
                    health if health in ("none", "starting", "healthy", "unhealthy") else "unknown"
                )
                logs = subprocess.run(
                    ["docker", "logs", "--tail", "80", identifier],
                    capture_output=True,
                    text=True,
                    timeout=10,
                )
                text = (logs.stdout + "\n" + logs.stderr)[:131072]
                row["worker_startup_stages"] = sorted(
                    set(re.findall(r"ordinary worker startup stage: ([a-z_]{1,40})", text))
                )[:8]
                row["mentioned_settings"] = sorted(
                    set(
                        re.findall(
                            r"\b(?:R2_|ORDINARY_GO_|LIGHTPANDA_|TYPESENSE_|GO_TYPESENSE_)[A-Z0-9_]+\b",
                            text,
                        )
                    )
                )[:16]
                row["error_categories"] = [
                    word
                    for word in (
                        "permission denied",
                        "operation not permitted",
                        "executable file not found",
                        "stat /usr/bin/setpriv",
                        "address already in use",
                        "failed to set up container networking",
                        "no such file",
                        "certificate",
                        "configuration",
                        "ownership",
                        "lookup",
                        "connection",
                        "out of memory",
                    )
                    if word in (text + "\n" + str(state.get("Error", ""))[:8192]).lower()
                ]
        except Exception as error:
            row["diagnostic_failure_class"] = type(error).__name__
        output.append(row)
    return output


def key_identifiers(
    certificate: x509.Certificate, issuer: ec.EllipticCurvePrivateKey
) -> x509.Certificate:
    builder = (
        x509.CertificateBuilder()
        .subject_name(certificate.subject)
        .issuer_name(certificate.issuer)
        .public_key(certificate.public_key())
        .serial_number(certificate.serial_number)
        .not_valid_before(certificate.not_valid_before_utc)
        .not_valid_after(certificate.not_valid_after_utc)
    )
    for extension in certificate.extensions:
        builder = builder.add_extension(extension.value, extension.critical)
    return (
        builder.add_extension(
            x509.SubjectKeyIdentifier.from_public_key(certificate.public_key()), critical=False
        )
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(issuer.public_key()), critical=False
        )
        .sign(issuer, hashes.SHA256())
    )


def pki(directory: Path, native_image: str) -> dict[str, str]:
    directory.mkdir(mode=0o700)
    ca = x509.load_pem_x509_certificate(CA_PEM)
    key = ec.derive_private_key(CA_SCALAR, ec.SECP256R1())
    # Python 3.13's strict verifier requires these identifiers on the fixture chain.
    ca = key_identifiers(ca, key)
    ca_pem = ca.public_bytes(serialization.Encoding.PEM)
    pairs = {
        "server": _leaf(
            ca,
            key,
            "native-lane-renderer",
            [x509.IPAddress(ipaddress.ip_address("10.0.0.5"))],
            ExtendedKeyUsageOID.SERVER_AUTH,
        ),
        "client": _leaf(
            ca,
            key,
            "native-lane-client",
            [x509.UniformResourceIdentifier("spiffe://jobseek/crawler/lightpanda-b0")],
            ExtendedKeyUsageOID.CLIENT_AUTH,
        ),
        "fixture": _leaf(
            ca,
            key,
            "native-lane-fixture",
            [x509.DNSName(host) for host in (*ORIGINS, "fixture")],
            ExtendedKeyUsageOID.SERVER_AUTH,
        ),
    }
    pins = {"ca": hashlib.sha256(ca.public_bytes(serialization.Encoding.DER)).hexdigest()}
    _write(directory / "ca.pem", ca_pem, 0o600)
    root_bundle = run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "/bin/cat",
            native_image,
            "/etc/ssl/certs/ca-certificates.crt",
        ]
    )
    _write(directory / "ca-bundle.pem", root_bundle.encode() + b"\n" + ca_pem, 0o600)
    for name, (certificate, private_key) in pairs.items():
        certificate = key_identifiers(certificate, key)
        _write(
            directory / f"{name}.pem", certificate.public_bytes(serialization.Encoding.PEM), 0o600
        )
        _write(
            directory / f"{name}-key.pem",
            private_key.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            ),
            0o400 if name == "server" else 0o600,
        )
        pins[name + "-leaf"] = hashlib.sha256(
            certificate.public_bytes(serialization.Encoding.DER)
        ).hexdigest()
        pins[name + "-spki"] = hashlib.sha256(
            certificate.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
        ).hexdigest()
    for name in ("ca", "server-leaf", "server-spki"):
        _write(directory / (name + ".sha256"), (pins[name] + "\n").encode(), 0o600)
    # Only fixture credentials are passed to this networkless ownership helper.
    # The directory stays owned by the caller, so cleanup can unlink all files.
    names = sorted(path.name for path in directory.iterdir())
    run(
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
            f"type=bind,src={directory},dst=/pki",
            "--entrypoint",
            "/bin/chown",
            native_image,
            "10001:10001",
            *["/pki/" + name for name in names],
        ]
    )
    return pins


def runner(env: dict[str, str], phase: str, *, timeout: int = 60) -> dict[str, Any]:
    raw = compose(
        dict(env, NATIVE_LANE_PHASE=phase), "run", "--rm", "--no-deps", "runner", timeout=timeout
    )
    lines = [
        line.removeprefix("NATIVE_LANE=")
        for line in raw.splitlines()
        if line.startswith("NATIVE_LANE=")
    ]
    if len(lines) != 1:
        raise RuntimeError("comparison runner omitted its unique result")
    return json.loads(lines[0])


def measured(env: dict[str, str], services: list[str]) -> list[dict[str, Any]]:
    networks = json.loads(
        run(
            [
                "docker",
                "network",
                "inspect",
                env["NATIVE_LANE_PROJECT"] + "_claim",
                env["NATIVE_LANE_PROJECT"] + "_origin",
            ]
        )
    )
    if len(networks) != 2 or not all(network["Internal"] for network in networks):
        raise RuntimeError("comparison network is not isolated")
    rows = []
    for service in services:
        identifier = compose(env, "ps", "-q", service).strip()
        item = inspect(identifier)
        if not identifier or not item["State"]["Running"] or item["HostConfig"]["Privileged"]:
            raise RuntimeError("comparison service is not running unprivileged")
        if any("docker.sock" in mount["Source"] for mount in item.get("Mounts", [])):
            raise RuntimeError("comparison service received a Docker socket")
        allowed = {env["NATIVE_LANE_PROJECT"] + "_claim", env["NATIVE_LANE_PROJECT"] + "_origin"}
        if not set(item["NetworkSettings"]["Networks"]).issubset(allowed):
            raise RuntimeError("comparison service attached an unexpected network")
        path = _cgroup(item["State"]["Pid"])
        if _integer(path / "memory.swap.max") != 0:
            raise RuntimeError("comparison service has swap enabled")
        rows.append(
            {
                "service": service,
                "id": identifier,
                "path": path,
                "cpu_before_usec": _cpu_usage(path),
                "events_before": _memory_events(path),
            }
        )
    return rows


def arm(base: dict[str, str], lane: str, ordinal: int) -> dict[str, Any]:
    project = f"native-lane-{os.getpid()}-{ordinal}-{secrets.token_hex(3)}"
    env = dict(
        base, NATIVE_LANE_PROJECT=project, NATIVE_LANE_ARM=lane, NATIVE_LANE_PHASE="bootstrap"
    )
    services = SUPPORT + (
        ["ordinary", "renderer"] if lane == "native" else ["legacy-simple", "legacy-browser"]
    )
    result: dict[str, Any] = {
        "lane": lane,
        "pair": ordinal // 2,
        "workload_sha256": workload_sha256(),
    }
    stage = "base"
    try:
        compose(env, "up", "-d", "--wait", "postgres", "redis", "typesense", "fixture", timeout=90)
        stage = "migrate"
        compose(env, "run", "--rm", "--no-deps", "migrate", timeout=120)
        stage = "health"
        runner(env, "health", timeout=30)
        stage = "bootstrap"
        result["bootstrap"] = runner(env, "bootstrap")
        stage = "schema"
        compose(env, "run", "--rm", "--no-deps", "schema", timeout=120)
        stage = "exporter_owner"
        owner = compose(
            env,
            "run",
            "--rm",
            "--no-deps",
            "schema",
            "/usr/local/bin/go-typesense-exporter",
            "--owner",
        ).strip()
        if owner == "python":
            compose(env, "run", "--rm", "--no-deps", "exporter-owner")
        elif owner != "go":
            raise RuntimeError("comparison exporter owner is unknown")
        # Empty queues allow both compositions to complete startup before the
        # same future due window is published. Startup remains in lifetime CPU.
        stage = "startup"
        compose(env, "up", "-d", "--wait", *services, timeout=180)
        stage = "attestation"
        rows = measured(env, services)
        sampled: dict[str, Any] = {}
        stop = threading.Event()
        thread = threading.Thread(target=sample, args=(rows, stop, sampled), daemon=True)
        thread.start()
        began = time.monotonic()
        try:
            stage = "feed"
            result["feed"] = runner(env, "feed")
            stage = "collect"
            result["output"] = runner(env, "collect", timeout=210)
            result["completion_seconds"] = time.monotonic() - began
            stage = "retention"
            time.sleep(35)
        finally:
            stop.set()
            thread.join(timeout=3)
        if thread.is_alive() or not sampled.get("samples"):
            raise RuntimeError("comparison sampler did not complete")
        stage = "postflight"
        resource = []
        for row in rows:
            item = inspect(row["id"])
            events = _memory_events(row["path"])
            if not item["State"]["Running"] or item["RestartCount"] or item["State"]["OOMKilled"]:
                raise RuntimeError("comparison service stopped, restarted or OOMed")
            if any(events.get(key, 0) for key in ("oom", "oom_kill")):
                raise RuntimeError("comparison lifetime has OOM evidence")
            resource.append(
                {
                    "service": row["service"],
                    "cpu_seconds": (_cpu_usage(row["path"]) - row["cpu_before_usec"]) / 1e6,
                    "startup_cpu_seconds": row["cpu_before_usec"] / 1e6,
                    "lifetime_cpu_seconds": _cpu_usage(row["path"]) / 1e6,
                    "lifetime_memory_peak_bytes": _integer(row["path"] / "memory.peak"),
                    "memory_limit_bytes": _integer(row["path"] / "memory.max"),
                    "memory_events": events,
                }
            )
        result["modeled_eur_per_1000_correct_postings"] = allocation(
            sampled["groups"], sampled["duration_seconds"], result["output"]["postings"]
        )
        result["resource"] = {
            **sampled,
            "postflight_elapsed_seconds": time.monotonic() - began,
            "services": resource,
        }
    except Exception as error:
        failure: dict[str, Any] = {"stage": stage, "class": type(error).__name__}
        if isinstance(error, ComparisonFailure):
            failure["diagnostic"] = error.diagnostic
        result["failure"] = failure
        if stage == "startup":
            result["startup_diagnostics"] = startup_diagnostics(env, services)
    finally:
        try:
            compose(env, "down", "--volumes", "--remove-orphans", "--timeout", "15", timeout=90)
            for kind, command in (
                ("containers", ["docker", "ps", "-aq"]),
                ("networks", ["docker", "network", "ls", "-q"]),
                ("volumes", ["docker", "volume", "ls", "-q"]),
            ):
                remaining = run(
                    command + ["--filter", "label=com.docker.compose.project=" + project]
                )
                if remaining.strip():
                    raise RuntimeError("comparison project cleanup left " + kind)
            result["cleanup"] = True
        except Exception as error:
            result["cleanup"] = False
            result["cleanup_failure_class"] = type(error).__name__
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--native-image", required=True)
    parser.add_argument("--legacy-image", required=True)
    parser.add_argument("--renderer-image", required=True)
    parser.add_argument("--source-sha", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if not re.fullmatch("[0-9a-f]{40}", args.source_sha):
        raise RuntimeError("comparison source is invalid")
    if (
        run(["git", "rev-parse", "HEAD"]).strip() != args.source_sha
        or run(["git", "status", "--porcelain=v1", "--untracked-files=all"]).strip()
    ):
        raise RuntimeError("comparison checkout is not clean at its exact source")
    images = {
        "native": args.native_image,
        "legacy": args.legacy_image,
        "renderer": args.renderer_image,
    }
    provenance = {}
    for name, image in images.items():
        if not re.fullmatch("sha256:[0-9a-f]{64}", image):
            raise RuntimeError("comparison requires local immutable image IDs")
        item = json.loads(run(["docker", "image", "inspect", image]))[0]
        if item["Config"]["Labels"].get("org.opencontainers.image.revision") != args.source_sha:
            raise RuntimeError("comparison image source differs")
        provenance[name] = {"id": item["Id"], "architecture": item["Architecture"]}
    if len({value["architecture"] for value in provenance.values()}) != 1:
        raise RuntimeError("comparison image architectures differ")
    document: dict[str, Any] = {
        "schema": "jobseek.native-whole-lane/v1",
        "source_revision": args.source_sha,
        "runtime_contract_sha256": run(
            [
                "node",
                str(HERE.parents[3] / "scripts/derive-crawler-runtime-contract.mjs"),
                "--revision",
                args.source_sha,
            ]
        ).strip(),
        "workload_sha256": workload_sha256(),
        "images": provenance,
        "catalogue_prices_net_eur_per_hour": PRICES,
        "price_snapshot": "2026-10-30; same role weights as production observer",
        "arms": [],
        "limits": (
            "Four effective profiles,536 postings, identical input and aggregate "
            "discovery4/monitor2 budgets. Full "
            "worker/render/storage/export/drain/reaper/fixture containers included. Fixture- "
            "generated production-cardinality lookup data; no actual origin or production "
            "writes. CPU includes startup separately; peak samples are not absolute peaks. This "
            "bounded comparison does not alone prove fleet coverage, natural recovery or "
            "production freshness. "
        ),
    }
    try:
        with tempfile.TemporaryDirectory(prefix="jobseek-native-lane-") as directory:
            credential_dir = Path(directory) / "pki"
            pins = pki(credential_dir, args.native_image)
            env = {
                key: os.environ[key]
                for key in ("PATH", "HOME", "DOCKER_CONFIG")
                if key in os.environ
            }
            env.update(
                NATIVE_IMAGE=args.native_image,
                LEGACY_IMAGE=args.legacy_image,
                RENDERER_IMAGE=args.renderer_image,
                NATIVE_LANE_PKI=str(credential_dir),
                NATIVE_LANE_CA_PIN=pins["ca"],
                NATIVE_LANE_CLIENT_LEAF_PIN=pins["client-leaf"],
                NATIVE_LANE_CLIENT_SPKI_PIN=pins["client-spki"],
                POSTGRES_IMAGE=POSTGRES_IMAGE,
                REDIS_IMAGE=REDIS_IMAGE,
                PYTHON_IMAGE=PYTHON_IMAGE,
            )
            for ordinal, lane in enumerate(ORDER):
                document["arms"].append(arm(env, lane, ordinal))
                _atomic_evidence(args.output, document)
                if document["arms"][-1].get("failure") or not document["arms"][-1]["cleanup"]:
                    raise RuntimeError("comparison arm failed; retain its bounded diagnostic")
        if (
            run(["git", "rev-parse", "HEAD"]).strip() != args.source_sha
            or run(["git", "status", "--porcelain=v1", "--untracked-files=all"]).strip()
        ):
            raise RuntimeError("comparison checkout changed during the run")
        hashes = {item["output"]["canonical_sha256"] for item in document["arms"]}
        if len(hashes) != 1 or len(document["arms"]) != len(ORDER):
            raise RuntimeError("comparison canonical output differs between arms")
        document["canonical_parity"] = True
        document["complete"] = True
    except Exception as error:
        document["failure_class"] = type(error).__name__
        document["complete"] = False
    _atomic_evidence(args.output, document)
    print("NATIVE_LANE_REPORT=" + json.dumps(document, sort_keys=True, separators=(",", ":")))
    print(json.dumps({"complete": document["complete"], "arms": len(document["arms"])}))
    return 0 if document["complete"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
