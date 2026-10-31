"""Prepare fixture-only certificate trust for the isolated legacy comparator."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path


def prepare_nss_database(home: Path, certificate: Path) -> None:
    database = home / ".pki/nssdb"
    database.mkdir(parents=True, mode=0o700)
    for arguments in (
        ["-N", "--empty-password"],
        ["-A", "-n", "native-lane-fixture", "-t", "C,,", "-i", str(certificate)],
    ):
        subprocess.run(
            ["certutil", "-d", "sql:" + str(database), *arguments],
            check=True,
            capture_output=True,
            timeout=10,
            umask=0o077,
        )


def main() -> None:
    os.umask(0o077)
    home = Path("/tmp/native-lane-home")
    os.environ["HOME"] = str(home)
    prepare_nss_database(home, Path("/run/native-lane/ca.pem"))
    os.execv("/app/.venv/bin/crawler", ["crawler", "run-browser"])


if __name__ == "__main__":
    main()
