from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
DEPLOY = ROOT / "deploy" / "lightpanda-renderer"
HOST = DEPLOY / "acceptance-host.py"
REMOTE = DEPLOY / "acceptance-remote.sh"
CRAWLER = DEPLOY / "acceptance-crawler.sh"
CLIENT = DEPLOY / "acceptance-client.py"
VERIFY = DEPLOY / "verify.py"
INSTALL = DEPLOY / "install-host.sh"
WORKFLOW = ROOT / ".github" / "workflows" / "accept-lightpanda-renderer-host.yml"
COMMIT = "a" * 40
IMAGE = "ghcr.io/colophon-group/jobseek-lightpanda-renderer@sha256:" + "b" * 64
BOOT = "11111111-1111-1111-1111-111111111111"
REBOOT = "22222222-2222-2222-2222-222222222222"


def protected() -> dict[str, object]:
    containers = {}
    for index, name in enumerate(("deploy-cloudflared-1", "deploy-murmur-1")):
        containers[name] = {
            "id": str(index + 1) * 64,
            "image_id": "sha256:" + str(index + 3) * 64,
            "running": False,
            "status": "exited",
            "started_at": "2026-09-09T00:00:00Z",
            "finished_at": "2026-09-09T00:01:00Z",
            "exit_code": 0,
            "oom_killed": False,
            "restart_count": 0,
            "restart_policy": {"Name": "no", "MaximumRetryCount": 0},
            "config_sha256": str(index + 5) * 64,
            "host_config_sha256": str(index + 6) * 64,
            "networks": [],
            "mounts": [],
        }
    return {"schema_version": 1, "containers": containers}


def renderer(mode: str) -> dict[str, object]:
    return {
        "container_id": "9" * 64,
        "image_id": "sha256:" + "8" * 64,
        "image_ref": IMAGE,
        "source_commit": COMMIT,
        "release_id": f"sha-{COMMIT}-r1a1",
        "running": mode == "running",
        "status": "running" if mode == "running" else "exited",
        "started_at": "2026-09-11T00:00:00Z",
        "finished_at": "2026-09-11T00:10:00Z" if mode == "cold" else "0001-01-01T00:00:00Z",
        "exit_code": 0,
        "oom_killed": False,
        "restart_count": 0,
        "restart_policy": {"Name": "on-failure", "MaximumRetryCount": 3},
        "config_sha256": "4" * 64,
        "host_config_sha256": "5" * 64,
        "mounts_sha256": "6" * 64,
    }


def snapshot(mode: str, boot: str) -> dict[str, object]:
    return {
        "mode": mode,
        "boot_id": boot,
        "host_components": {
            "policy_sha256": "1" * 64,
            "inventory_sha256": "2" * 64,
            "unit_sha256": "3" * 64,
        },
        "protected_containers": protected(),
        "container_names": [
            "deploy-cloudflared-1",
            "deploy-murmur-1",
            "jobseek-lightpanda-renderer",
        ],
        "running_container_names": ["jobseek-lightpanda-renderer"] if mode == "running" else [],
        "network_endpoint_counts": {
            "jobseek-lightpanda-renderer": 1 if mode == "running" else 0,
            "jobseek-lightpanda-egress": 1 if mode == "running" else 0,
        },
        "phase_a_receipt_sha256": "7" * 64,
        "renderer": renderer(mode),
    }


def run_report(tmp_path: Path, mutation: str = "") -> subprocess.CompletedProcess[str]:
    stages = [
        snapshot("running", BOOT),
        snapshot("running", BOOT),
        snapshot("cold", BOOT),
        snapshot("cold", REBOOT),
    ]
    crawler = {"status": "accepted", "credential_bundle_sha256": "8" * 64}
    probes = {"status": "accepted", "probes": [{"packet_delta": 1}]}
    traffic_before = {
        "schema_version": 1,
        "ingress_new": {"packets": 1, "bytes": 100},
        "egress_dns": {"packets": 2, "bytes": 200},
        "egress_https": {"packets": 3, "bytes": 300},
        "drop_rules": [{"rule": ["-A", "JSLP4-EGRESS", "-j", "DROP"], "packets": 0, "bytes": 0}],
    }
    traffic_after = {
        "schema_version": 1,
        "ingress_new": {"packets": 2, "bytes": 200},
        "egress_dns": {"packets": 3, "bytes": 300},
        "egress_https": {"packets": 4, "bytes": 400},
        "drop_rules": [{"rule": ["-A", "JSLP4-EGRESS", "-j", "DROP"], "packets": 0, "bytes": 0}],
    }
    if mutation == "same_boot":
        stages[-1]["boot_id"] = BOOT
    elif mutation == "protected":
        stages[-1]["protected_containers"]["containers"]["deploy-murmur-1"]["finished_at"] = (
            "changed"
        )
    elif mutation == "renderer":
        stages[-1]["renderer"]["container_id"] = "0" * 64
    elif mutation == "crawler":
        crawler["status"] = "failed"
    elif mutation == "traffic":
        traffic_after["egress_dns"] = traffic_before["egress_dns"]
    elif mutation == "drop":
        traffic_after["drop_rules"][0]["packets"] = 1
    elif mutation == "reported_image":
        for stage in stages:
            stage["renderer"]["image_ref"] = IMAGE.replace("b", "c")
    paths = []
    for index, payload in enumerate(stages):
        path = tmp_path / f"stage-{index}.json"
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths.append(path)
    crawler_path, probes_path = tmp_path / "crawler.json", tmp_path / "probes.json"
    traffic_before_path = tmp_path / "traffic-before.json"
    traffic_after_path = tmp_path / "traffic-after.json"
    crawler_path.write_text(json.dumps(crawler), encoding="utf-8")
    probes_path.write_text(json.dumps(probes), encoding="utf-8")
    traffic_before_path.write_text(json.dumps(traffic_before), encoding="utf-8")
    traffic_after_path.write_text(json.dumps(traffic_after), encoding="utf-8")
    return subprocess.run(
        [
            sys.executable,
            str(HOST),
            "report",
            "--initial",
            str(paths[0]),
            "--after-probe",
            str(paths[1]),
            "--after-docker",
            str(paths[2]),
            "--after-reboot",
            str(paths[3]),
            "--crawler",
            str(crawler_path),
            "--probes",
            str(probes_path),
            "--traffic-before",
            str(traffic_before_path),
            "--traffic-after",
            str(traffic_after_path),
            "--acceptance-source-commit",
            COMMIT,
            "--renderer-source-commit",
            COMMIT,
            "--renderer-image-ref",
            IMAGE,
        ],
        check=False,
        capture_output=True,
        text=True,
    )


def test_report_preserves_component_and_full_protected_identity(tmp_path: Path) -> None:
    completed = run_report(tmp_path)
    assert completed.returncode == 0, completed.stderr
    report = json.loads(completed.stdout)
    assert [stage["mode"] for stage in report["stages"]] == ["running", "running", "cold", "cold"]
    murmur = report["protected_containers"]["containers"]["deploy-murmur-1"]
    assert {"started_at", "finished_at", "restart_count", "exit_code", "networks", "mounts"} <= set(
        murmur
    )
    assert report["renderer_source_commit"] == COMMIT
    assert report["renderer_image_ref"] == IMAGE
    assert report["public_render_accept_rule_counters"]["deltas"]["egress_dns"]["packets"] == 1
    assert report["public_render_accept_rule_counters"]["deltas"]["drop_rules"][0]["packets"] == 0


@pytest.mark.parametrize(
    "mutation",
    ["same_boot", "protected", "renderer", "crawler", "traffic", "drop", "reported_image"],
)
def test_report_rejects_cross_phase_drift(tmp_path: Path, mutation: str) -> None:
    assert run_report(tmp_path, mutation).returncode == 1


def test_acceptance_is_manual_exact_main_and_one_artifact() -> None:
    workflow = WORKFLOW.read_text(encoding="utf-8")
    assert (
        "workflow_dispatch:" in workflow
        and "schedule:" not in workflow
        and "\n  push:\n" not in workflow
    )
    assert "environment: Production" in workflow and "group: deploy-murmur-shim" in workflow
    assert "git ls-remote --exit-code origin refs/heads/main" in workflow
    assert "renderer_source_commit:" in workflow and "renderer_image_ref:" in workflow
    assert '"${{ inputs.phase }}"' not in workflow
    assert workflow.count("actions/upload-artifact") == 1
    assert "packages: write" not in workflow and "acceptance-receipt" not in workflow


def test_phase_a_is_a_pre_start_component_gate() -> None:
    install = INSTALL.read_text(encoding="utf-8")
    gate = install.index("phase-a-receipt \\")
    assert gate < install.index('docker stop --time 30 "$existing_id"')
    assert gate < install.index('up --detach --no-deps "$SERVICE"')
    verify = VERIFY.read_text(encoding="utf-8")
    assert 'receipt.get("host_components") != expected_components' in verify
    assert "/var/lib/jobseek-lightpanda-acceptance/phase-a.json" in install
    assert "installed_unit_sha256 != unit_sha256" in verify
    assert '"verify-attested", policy_sha256, inventory_sha256' in verify
    assert 'run_text(["systemctl", "is-active", unit])' in verify
    assert 'receipt.get("acceptance_source_commit") ==' not in verify
    assert 'receipt.get("protected_containers") != snapshot_protected()' in verify


def test_client_stage_is_readable_by_numeric_container_identity() -> None:
    remote = REMOTE.read_text(encoding="utf-8")
    assert "chmod 0444 '$crawler_stage/acceptance-client.py'" in remote
    if shutil.which("setpriv") is None or (os.geteuid() != 0 and shutil.which("sudo") is None):
        pytest.skip("numeric-user permission execution requires setpriv privilege")
    if os.geteuid() != 0 and subprocess.run(["sudo", "-n", "true"], check=False).returncode:
        pytest.skip("passwordless privilege is unavailable")
    directory = Path(tempfile.mkdtemp(prefix="jobseek-acceptance-client-", dir="/tmp"))
    try:
        directory.chmod(0o755)
        staged = directory / "acceptance-client.py"
        shutil.copyfile(CLIENT, staged)
        staged.chmod(0o444)
        prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
        completed = subprocess.run(
            [
                *prefix,
                "setpriv",
                "--reuid=10001",
                "--regid=10001",
                "--clear-groups",
                "test",
                "-r",
                staged,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
    finally:
        shutil.rmtree(directory)


def test_host_stage_discards_archive_owner_and_normalizes_verifier_metadata(
    tmp_path: Path,
) -> None:
    remote = REMOTE.read_text(encoding="utf-8")
    extract = (
        "tar --extract --gzip --file - --directory '$host_stage' "
        "--no-same-owner --no-same-permissions"
    )
    assert extract in remote
    assert ("chown root:root '$host_stage/acceptance-host.py' '$host_stage/verify.py'") in remote
    assert "--no-same-permissions && chown root:root" in remote
    assert "'$host_stage/verify.py' && chmod 0600" in remote

    prefix = [] if os.geteuid() == 0 else ["sudo", "-n"]
    if prefix and (
        shutil.which("sudo") is None
        or subprocess.run([*prefix, "true"], check=False, capture_output=True, text=True).returncode
    ):
        pytest.skip("root extraction is required to verify discarded archive ownership")

    sources = []
    for name in ("acceptance-host.py", "verify.py"):
        source = tmp_path / name
        source.write_text("VALUE = 1\n", encoding="utf-8")
        sources.append(source)
    archive = tmp_path / "host.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        for source in sources:
            info = bundle.gettarinfo(source, arcname=source.name)
            info.uid = 12345
            info.gid = 12345
            info.mode = 0o777
            with source.open("rb") as content:
                bundle.addfile(info, content)

    stage = tmp_path / "stage"
    stage.mkdir()
    subprocess.run(
        [
            *prefix,
            "tar",
            "--extract",
            "--gzip",
            "--file",
            str(archive),
            "--directory",
            str(stage),
            "--no-same-owner",
            "--no-same-permissions",
        ],
        check=True,
    )
    staged = stage / "verify.py"
    subprocess.run([*prefix, "chown", "root:root", staged], check=True)
    subprocess.run([*prefix, "chmod", "0600", staged], check=True)
    metadata = staged.stat(follow_symlinks=False)
    assert metadata.st_uid == 0
    assert stat.S_IMODE(metadata.st_mode) == 0o600

    damaged = tmp_path / "damaged.tar.gz"
    damaged.write_bytes(archive.read_bytes()[:-8])
    damaged_stage = tmp_path / "damaged-stage"
    damaged_stage.mkdir()
    damaged_result = subprocess.run(
        [
            *prefix,
            "bash",
            "-c",
            'tar --extract --gzip --file "$1" --directory "$2" '
            '--no-same-owner --no-same-permissions && chown root:root "$3" "$4" '
            '&& chmod 0600 "$3" "$4"',
            "acceptance-stage-test",
            str(damaged),
            str(damaged_stage),
            str(damaged_stage / "acceptance-host.py"),
            str(damaged_stage / "verify.py"),
        ],
        check=False,
        capture_output=True,
        text=True,
    )
    assert damaged_result.returncode != 0
    for name in ("acceptance-host.py", "verify.py"):
        materialized = damaged_stage / name
        assert materialized.is_file()
        assert stat.S_IMODE(materialized.stat().st_mode) != 0o600


def test_negative_and_mtls_probes_use_real_installed_components() -> None:
    host = HOST.read_text(encoding="utf-8")
    remote = REMOTE.read_text(encoding="utf-8")
    crawler = CRAWLER.read_text(encoding="utf-8")
    client = CLIENT.read_text(encoding="utf-8")
    assert "iptables-save" in host and "packet_delta" in host and "nsenter" in host
    assert all(name in host for name in ("ingress_new", "egress_dns", "egress_https"))
    assert "iptables -I" not in host and "acceptance-sink" not in remote
    assert "LightpandaB0Client" in client and "https://example.com/" in client
    assert "credential_digest" in client and "--network host" in crawler
    assert "/home/deploy/.env" in crawler and "LIGHTPANDA_B0_CREDENTIAL_DIR" in crawler
    assert "10001:10001:400:1" in crawler and "$(id -u):$(id -g):711" in crawler
    assert 'item.get("Mounts") not in (None, [])' in crawler
    before = remote.index('traffic_counters >"$traffic_before"')
    render = remote.index("acceptance-crawler.sh' '$crawler_stage' '$owner'", before)
    after = remote.index('traffic_counters >"$traffic_after"', render)
    assert before < render < after
    cleanup = remote[remote.index("cleanup() {") : remote.index("trap cleanup")]
    assert cleanup.index("network-policy quarantine") < cleanup.index("restart docker.service")
    assert "drop_rules" in host and "public render traversed an installed DROP rule" in host
    assert "os.fchmod(descriptor, 0o640)" in host
    assert "--memory 256m" in crawler and "image_source_revision" not in crawler
    assert not (DEPLOY / "acceptance-claimant.py").exists()
    assert not (DEPLOY / "acceptance-sink.py").exists()
