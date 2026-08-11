from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
BACKUP_PATH = ROOT / "scripts" / "jobseek-data-backup.py"
PROTECTOR_PATH = ROOT / "deploy/backups/web-postgresql/protect-client-image.sh"
SPEC = importlib.util.spec_from_file_location("jobseek_data_backup_image_gc", BACKUP_PATH)
assert SPEC and SPEC.loader
backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(backup)


def _fake_docker(tmp_path: Path) -> tuple[Path, Path]:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    state_path = tmp_path / "docker-state.json"
    docker = fake_bin / "docker"
    docker.write_text(
        """#!/usr/bin/env python3
import json
import os
import signal
import sys
from pathlib import Path

path = Path(os.environ["FAKE_DOCKER_STATE"])
state = json.loads(path.read_text()) if path.exists() else {
    "images": [], "containers": {}, "events": []
}
args = sys.argv[1:]

def save():
    path.write_text(json.dumps(state, sort_keys=True))

def fail(code=1):
    save()
    raise SystemExit(code)

if args[:2] == ["pull", "--quiet"]:
    image = args[2]
    if image not in state["images"]:
        state["images"].append(image)
    state["events"].append(["pull", image])
    save()
elif args[:2] == ["image", "inspect"]:
    image = args[-1]
    if image not in state["images"]:
        fail()
    print(json.dumps([{"Id": "sha256:local", "RepoDigests": [image]}]))
    save()
elif args[:2] == ["container", "inspect"]:
    name = args[-1]
    container = state["containers"].get(name)
    if container is None:
        fail()
    if "--format" in args:
        print(
            f'{container["image"]}|{str(container["running"]).lower()}|{container["label"]}'
            '|none|true|["ALL"]|["no-new-privileges:true"]|'
            '{"/var/lib/postgresql/data":"rw,noexec,nosuid,nodev,size=65536"}'
            '|[]|["/bin/true"]'
        )
    else:
        print(json.dumps([{
            "Config": {
                "Image": container["image"],
                "Labels": {"jobseek.backup.helper-image": container["label"]},
                "Entrypoint": ["/bin/true"],
            },
            "State": {"Running": container["running"]},
            "HostConfig": {
                "NetworkMode": "none",
                "ReadonlyRootfs": True,
                "CapDrop": ["ALL"],
                "SecurityOpt": ["no-new-privileges:true"],
                "Tmpfs": {
                    "/var/lib/postgresql/data": "rw,noexec,nosuid,nodev,size=65536"
                },
            },
            "Mounts": [],
        }]))
    save()
elif args and args[0] == "create":
    name = args[args.index("--name") + 1]
    label = args[args.index("--label") + 1].split("=", 1)[1]
    image = next(value for value in args if "@sha256:" in value)
    tmpfs = args[args.index("--tmpfs") + 1]
    if tmpfs != "/var/lib/postgresql/data:rw,noexec,nosuid,nodev,size=65536":
        fail()
    if image not in state["images"] or name in state["containers"]:
        fail()
    state["containers"][name] = {"image": image, "label": label, "running": False}
    print("fake-container-id")
    save()
elif args and args[0] == "rm":
    name = args[-1]
    if state["containers"].pop(name, None) is None:
        fail()
    save()
    if os.environ.get("FAKE_DOCKER_TERM_AFTER_RM") == "1":
        os.kill(os.getppid(), signal.SIGTERM)
        raise SystemExit(143)
elif args and args[0] == "rename":
    old, new = args[1:3]
    if old not in state["containers"] or new in state["containers"]:
        fail()
    if os.environ.get("FAKE_DOCKER_RENAME_FAILURE") == "1":
        fail()
    state["containers"][new] = state["containers"].pop(old)
    save()
elif args[:2] == ["image", "prune"]:
    referenced = {container["image"] for container in state["containers"].values()}
    state["images"] = [image for image in state["images"] if image in referenced]
    state["events"].append(["emergency-prune"])
    save()
elif args and args[0] == "run":
    image = next((value for value in args if "@sha256:" in value), "")
    state["events"].append(["scheduled-run", image])
    save()
    if image not in state["images"]:
        raise SystemExit(125)
else:
    fail(2)
""",
        encoding="utf-8",
    )
    docker.chmod(0o755)
    timeout = fake_bin / "timeout"
    timeout.write_text(
        """#!/bin/sh
while [ "$#" -gt 0 ]; do
  case "$1" in
    --foreground) shift ;;
    --signal=*|--kill-after=*) shift ;;
    *s) shift; break ;;
    *) exit 2 ;;
  esac
done
exec "$@"
""",
        encoding="utf-8",
    )
    timeout.chmod(0o755)
    return fake_bin, state_path


def test_install_pull_emergency_gc_then_scheduled_backup_dependency(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_bin, state_path = _fake_docker(tmp_path)
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state_path),
    }
    image = backup.WEB_POSTGRES_IMAGE

    installed = subprocess.run(
        ["bash", str(PROTECTOR_PATH), image],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert installed.returncode == 0, installed.stderr

    emergency_gc = subprocess.run(
        ["docker", "image", "prune", "--all", "--force"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert emergency_gc.returncode == 0, emergency_gc.stderr

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["images"] == [image]
    assert state["containers"][backup.WEB_POSTGRES_IMAGE_LEASE] == {
        "image": image,
        "label": "web-postgresql",
        "running": False,
    }

    monkeypatch.setenv("PATH", environment["PATH"])
    monkeypatch.setenv("FAKE_DOCKER_STATE", str(state_path))
    backup._require_web_postgres_helper_image()
    scheduled = subprocess.run(
        backup._web_postgres_client_command("pg_isready", database_env=False),
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert scheduled.returncode == 0, scheduled.stderr
    assert json.loads(state_path.read_text(encoding="utf-8"))["events"] == [
        ["pull", image],
        ["emergency-prune"],
        ["scheduled-run", image],
    ]


def test_image_protector_rejects_a_mutable_tag_before_pull(tmp_path: Path) -> None:
    fake_bin, state_path = _fake_docker(tmp_path)
    result = subprocess.run(
        ["bash", str(PROTECTOR_PATH), "postgres:17-alpine"],
        check=False,
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ['PATH']}",
            "FAKE_DOCKER_STATE": str(state_path),
        },
    )

    assert result.returncode == 2
    assert "exact sha256 digest" in result.stderr
    assert not state_path.exists()


def test_failed_canonical_rename_leaves_the_digest_gc_protected(tmp_path: Path) -> None:
    fake_bin, state_path = _fake_docker(tmp_path)
    old_image = "postgres:16-alpine@sha256:" + "0" * 64
    state_path.write_text(
        json.dumps(
            {
                "images": [old_image],
                "containers": {
                    backup.WEB_POSTGRES_IMAGE_LEASE: {
                        "image": old_image,
                        "label": "web-postgresql",
                        "running": False,
                    }
                },
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state_path),
        "FAKE_DOCKER_RENAME_FAILURE": "1",
    }

    result = subprocess.run(
        ["bash", str(PROTECTOR_PATH), backup.WEB_POSTGRES_IMAGE],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode == 1
    assert "protected candidate remains" in result.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert backup.WEB_POSTGRES_IMAGE_LEASE not in state["containers"]
    assert len(state["containers"]) == 1
    candidate_name, candidate = next(iter(state["containers"].items()))
    assert candidate_name.startswith(f"{backup.WEB_POSTGRES_IMAGE_LEASE}.candidate.")
    assert candidate["image"] == backup.WEB_POSTGRES_IMAGE

    emergency_gc = subprocess.run(
        ["docker", "image", "prune", "--all", "--force"],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )
    assert emergency_gc.returncode == 0, emergency_gc.stderr
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["images"] == [backup.WEB_POSTGRES_IMAGE]


def test_signal_after_old_lease_removal_preserves_the_candidate(tmp_path: Path) -> None:
    fake_bin, state_path = _fake_docker(tmp_path)
    old_image = "postgres:16-alpine@sha256:" + "0" * 64
    state_path.write_text(
        json.dumps(
            {
                "images": [old_image],
                "containers": {
                    backup.WEB_POSTGRES_IMAGE_LEASE: {
                        "image": old_image,
                        "label": "web-postgresql",
                        "running": False,
                    }
                },
                "events": [],
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "PATH": f"{fake_bin}:{os.environ['PATH']}",
        "FAKE_DOCKER_STATE": str(state_path),
        "FAKE_DOCKER_TERM_AFTER_RM": "1",
    }

    result = subprocess.run(
        ["bash", str(PROTECTOR_PATH), backup.WEB_POSTGRES_IMAGE],
        check=False,
        capture_output=True,
        text=True,
        env=environment,
    )

    assert result.returncode != 0
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert backup.WEB_POSTGRES_IMAGE_LEASE not in state["containers"]
    assert len(state["containers"]) == 1
    candidate_name, candidate = next(iter(state["containers"].items()))
    assert candidate_name.startswith(f"{backup.WEB_POSTGRES_IMAGE_LEASE}.candidate.")
    assert candidate["image"] == backup.WEB_POSTGRES_IMAGE


@pytest.mark.timeout(240)
def test_real_docker_prune_retains_the_leased_digest() -> None:
    if os.environ.get("JOBSEEK_RUN_DOCKER_GC_INTEGRATION") != "1":
        pytest.skip("real Docker GC regression is opt-in")
    destructive_guard = (
        os.environ.get("JOBSEEK_ALLOW_DESTRUCTIVE_DOCKER_GC_TEST")
        == "github-hosted-ephemeral-runner"
        and os.environ.get("GITHUB_ACTIONS") == "true"
        and os.environ.get("RUNNER_ENVIRONMENT") == "github-hosted"
    )
    if not destructive_guard:
        pytest.fail("real Docker GC regression requires an acknowledged GitHub-hosted runner")
    if shutil.which("docker") is None:
        pytest.fail("Docker is required for the opted-in GC regression")

    lease = backup.WEB_POSTGRES_IMAGE_LEASE
    image = backup.WEB_POSTGRES_IMAGE
    existing = subprocess.run(
        ["docker", "container", "inspect", lease],
        check=False,
        capture_output=True,
        text=True,
    )
    if existing.returncode == 0:
        pytest.fail(f"refusing to replace pre-existing Docker container {lease}")

    try:
        installed = subprocess.run(
            ["bash", str(PROTECTOR_PATH), image],
            check=False,
            capture_output=True,
            text=True,
        )
        assert installed.returncode == 0, installed.stderr

        pruned = subprocess.run(
            ["docker", "image", "prune", "--all", "--force"],
            check=False,
            capture_output=True,
            text=True,
        )
        assert pruned.returncode == 0, pruned.stderr
        backup._require_web_postgres_helper_image()

        scheduled_dependency = subprocess.run(
            backup._web_postgres_client_command(
                "postgres", "--version", network="none", database_env=False
            ),
            check=False,
            capture_output=True,
            text=True,
        )
        assert scheduled_dependency.returncode == 0, scheduled_dependency.stderr
        assert scheduled_dependency.stdout.startswith("postgres (PostgreSQL) 17.")
    finally:
        subprocess.run(
            ["docker", "rm", "--force", lease],
            check=False,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["docker", "image", "rm", image],
            check=False,
            capture_output=True,
            text=True,
        )


def test_reviewed_web_backup_surfaces_share_one_immutable_helper_digest() -> None:
    image = backup.WEB_POSTGRES_IMAGE
    assert image.startswith("postgres:17-alpine@sha256:")
    assert len(image.rsplit("@sha256:", 1)[1]) == 64

    expected_references = {
        ROOT / "deploy/backups/install-host.sh": f'WEB_POSTGRES_IMAGE="{image}"',
        ROOT / "deploy/backups/web-postgresql/operations.py": image,
        ROOT / "deploy/backups/web-postgresql/restore-drill.sh": image,
        ROOT / "deploy/systemd/jobseek-web-postgresql-backup.service": (
            f"Environment=WEB_POSTGRES_IMAGE={image}"
        ),
        ROOT / "scripts/jobseek-host-observability.py": image,
    }
    for path, reference in expected_references.items():
        assert reference in path.read_text(encoding="utf-8"), path

    installer = (ROOT / "deploy/backups/install-host.sh").read_text(encoding="utf-8")
    assert '"$WEB_POSTGRES_IMAGE"' in installer
    assert "docker pull" not in installer


def test_real_gc_regression_is_restricted_to_an_acknowledged_ephemeral_runner() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")

    assert 'JOBSEEK_RUN_DOCKER_GC_INTEGRATION: "1"' in workflow
    assert "JOBSEEK_ALLOW_DESTRUCTIVE_DOCKER_GC_TEST: github-hosted-ephemeral-runner" in workflow
    assert 'os.environ.get("GITHUB_ACTIONS") == "true"' in Path(__file__).read_text(
        encoding="utf-8"
    )
