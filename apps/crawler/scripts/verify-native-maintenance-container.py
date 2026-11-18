#!/usr/bin/env python3
"""Trusted CI only: render Compose and create (never start) maintenance containers."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


def main() -> None:
    if os.environ.get("JOBSEEK_TRUSTED_MAINTENANCE_CONTAINER_TEST") != "1":
        raise SystemExit("requires the trusted CI Docker runner")
    docker = shutil.which("docker")
    image = os.environ.get("JOBSEEK_MAINTENANCE_TEST_IMAGE")
    if not docker or not image:
        raise SystemExit("trusted fixture Docker and installed image are required")
    crawler = Path(__file__).resolve().parents[1]
    source = (crawler / "deploy.sh").read_text()

    def function(name: str) -> str:
        start = source.index(name + "() {")
        return source[start : source.index("\n}\n", start) + 3]

    def command(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=45, env=env)

    with tempfile.TemporaryDirectory(prefix="jobseek-native-maintenance-") as temporary:
        root = Path(temporary)
        previous = root / "previous"
        previous.mkdir()
        (previous / "data").mkdir()
        expected_image = "ghcr.io/colophon-group/jobseek-crawler@sha256:" + "a" * 64
        values = {
            "CRAWLER_IMAGE_REF": expected_image,
            "LOCAL_DATABASE_URL": "postgresql://fixture/authority",
            "TYPESENSE_HOST": "fixture.invalid",
            "TYPESENSE_PORT": "8108",
            "TYPESENSE_PROTOCOL": "https",
            "TYPESENSE_OPERATIONS_KEY": "fixture",
        }
        (previous / "environment.env").write_text(
            "".join(key + "=" + value + "\n" for key, value in values.items())
        )
        shutil.copyfile(crawler / "docker-compose.yml", previous / "docker-compose.yml")
        rendered = json.loads(
            command(
                docker,
                "compose",
                "--env-file",
                str(previous / "environment.env"),
                "-f",
                str(previous / "docker-compose.yml"),
                "config",
                "--format",
                "json",
                env={
                    "PATH": os.environ["PATH"],
                    "HOME": temporary,
                    "COMPOSE_PROFILES": "maintenance",
                },
            ).stdout
        )
        carrier = rendered["services"]["worker-1"]
        assert carrier["network_mode"] == "host" and carrier["image"] == expected_image
        assert not carrier.get("links")

        # The wrapper only substitutes the fixture image and converts `run` to
        # `create`: the real Docker daemon validates all networking, env, mount
        # and entrypoint options emitted by the actual deployment function.
        # No container starts, PostgreSQL/Typesense connection or origin call.
        wrapper = root / "docker"
        wrapper.write_text(
            "#!/usr/bin/env python3\nimport json, os, pathlib, subprocess, sys\n"
            "args=sys.argv[1:]; assert args[:2]==['run','--rm']\n"
            f"assert args.count({expected_image!r}) == 1\n"
            f"args=[{image!r} if a=={expected_image!r} else a for a in args[2:]]\n"
            "# A private bind fixture avoids allocating a production-named CI volume.\n"
            f"args=[{str(previous / 'data')!r}+':/run/jobseek-lightpanda-producer:ro' "
            "if a=='jobseek-lightpanda-b0-producer-socket:"
            "/run/jobseek-lightpanda-producer:ro' else a for a in args]\n"
            f"result=subprocess.run([{docker!r},'create',*args],check=True,capture_output=True,text=True,timeout=30)\n"
            f"pathlib.Path({str(root / 'container')!r}).write_text(result.stdout.strip())\n"
        )
        wrapper.chmod(0o700)
        verifier = root / "verifier"
        verifier.write_text('#!/bin/sh\ntest "$1" = release-bridge\n')
        verifier.chmod(0o700)
        environment = {
            **os.environ,
            "PATH": temporary + ":" + os.environ["PATH"],
            "NATIVE_DEPLOY_TOOLS": str(verifier),
            "PREVIOUS": str(previous),
            "OWNER": "colophon-group",
            "COMPOSE_FILE": "incoming-must-not-leak",
            "LOCAL_DATABASE_URL": "incoming-must-not-leak",
            "WEB_DATABASE_URL": "incoming-must-not-leak",
        }
        for operation in ("inspect", "sync"):
            identifier = None
            try:
                command(
                    "bash",
                    "-c",
                    "\n".join(
                        (
                            "set -euo pipefail",
                            "MAINTENANCE_PROVENANCE_LABELS=("
                            "--label com.docker.compose.project=native-maintenance-fixture "
                            "--label com.docker.compose.container-number=1 "
                            "--label com.docker.compose.oneoff=True)",
                            function("read_exact_release_value"),
                            function("previous_native_maintenance"),
                            'previous_native_maintenance "$PREVIOUS" ' + operation,
                        )
                    ),
                    env=environment,
                )
                identifier = (root / "container").read_text()
                (created,) = json.loads(command(docker, "inspect", identifier).stdout)
                assert created["State"]["Status"] == "created"
                assert created["HostConfig"]["NetworkMode"] == "host"
                assert not created["HostConfig"].get("Links")
                assert created["HostConfig"]["Init"] is True
                assert created["HostConfig"]["NanoCpus"] == 1_000_000_000
                assert created["HostConfig"]["Memory"] == 1_073_741_824
                assert created["Config"]["Entrypoint"] == ["/usr/local/bin/go-typesense-exporter"]
                container_env = dict(item.split("=", 1) for item in created["Config"]["Env"])
                assert container_env["LOCAL_DATABASE_URL"] == values["LOCAL_DATABASE_URL"]
                assert container_env["REDIS_URL"] == "redis://localhost:6379/0"
                assert "WEB_DATABASE_URL" not in container_env
                assert "COMPOSE_FILE" not in container_env
                assert str(previous / "data") + ":/app/data:ro" in created["HostConfig"]["Binds"]
            finally:
                if identifier:
                    command(docker, "rm", "-f", identifier)
    print(
        "Native maintenance Compose rendering and real Docker create/inspect passed; "
        "no container started"
    )


if __name__ == "__main__":
    main()
