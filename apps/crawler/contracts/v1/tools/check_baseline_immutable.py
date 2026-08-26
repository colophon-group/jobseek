from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]
BASELINE = ROOT / "baseline/runtime-v1.descriptor.b64"
REPOSITORY_PATH = "apps/crawler/contracts/v1/baseline/runtime-v1.descriptor.b64"


def main() -> None:
    base_sha = os.environ.get("BASELINE_BASE_SHA", "").strip()
    if not base_sha:
        return
    completed = subprocess.run(
        ["git", "show", f"{base_sha}:{REPOSITORY_PATH}"],
        check=False,
        capture_output=True,
        cwd=ROOT,
    )
    if completed.returncode != 0:
        # The baseline does not exist on the introduction PR's base.
        return
    if completed.stdout != BASELINE.read_bytes():
        raise AssertionError("runtime v1 introduction descriptor is immutable")


if __name__ == "__main__":
    main()
