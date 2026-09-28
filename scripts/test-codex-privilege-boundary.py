#!/usr/bin/env python3
"""Disposable Linux permission test; CI runs this as root, never on a live runner."""

from __future__ import annotations

import os
import pwd
import shutil
import subprocess
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    if os.geteuid() != 0:
        raise SystemExit("run as root in disposable Linux CI")
    nobody = pwd.getpwnam("nobody")
    # /tmp is deliberately disallowed as a privileged source ancestor.
    with tempfile.TemporaryDirectory(prefix="codex-permission-test-", dir="/opt") as tmp:
        directory = Path(tmp)
        directory.chmod(0o755)
        source = directory / "bundle"
        (source / "scripts").mkdir(parents=True)
        deploy = source / "scripts/deploy-codex-runner-host.sh"
        shutil.copyfile(ROOT / "scripts/deploy-codex-runner-host.sh", deploy)
        helper = source / "scripts/jobseek_maintenance_provenance.py"
        helper.write_text("# trusted helper\n")

        def verify(expected: int) -> None:
            result = subprocess.run(
                ["bash", "-c", 'source "$1"; verify_trusted_bundle', "bash", str(deploy)],
                capture_output=True,
                text=True,
            )
            assert result.returncode == expected, result.stderr

        verify(0)
        # A protected executable does not help if its directory is writable.
        source.chmod(0o777)
        verify(1)
        source.chmod(0o755)
        helper.unlink()
        helper.symlink_to("/etc/passwd")
        verify(1)
        helper.unlink()
        helper.write_text("# trusted helper\n")
        os.chown(helper, nobody.pw_uid, nobody.pw_gid)
        verify(1)
        os.chown(helper, 0, 0)
        verify(0)
        for statement in (
            'open(__import__("sys").argv[1], "w").write("replaced")',
            '__import__("os").unlink(__import__("sys").argv[1])',
            '__import__("os").rename(__import__("sys").argv[1], __import__("sys").argv[1]+".old")',
        ):
            denied = subprocess.run(
                ["runuser", "-u", "nobody", "--", "python3", "-c", statement, str(helper)],
                capture_output=True,
            )
            assert denied.returncode != 0, "unprivileged replacement unexpectedly succeeded"
    print("privileged source ancestors, helper ownership and replacement defenses passed")


if __name__ == "__main__":
    main()
