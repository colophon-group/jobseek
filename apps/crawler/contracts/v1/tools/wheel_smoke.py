from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

CRAWLER = Path(__file__).parents[3]
EXPECTED = "src/runtime_contract/v1/runtime_pb2.py"


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="jobseek-runtime-wheel-") as temporary:
        output = Path(temporary)
        subprocess.run(
            ["uv", "build", "--wheel", "--out-dir", str(output), str(CRAWLER)],
            check=True,
        )
        wheels = list(output.glob("*.whl"))
        if len(wheels) != 1:
            raise AssertionError(f"expected one crawler wheel, got {wheels}")
        wheel = wheels[0]
        with zipfile.ZipFile(wheel) as archive:
            if EXPECTED not in archive.namelist():
                raise AssertionError(f"crawler wheel does not contain {EXPECTED}")
        environment = output / "venv"
        subprocess.run(["uv", "venv", "--python", sys.executable, str(environment)], check=True)
        python = environment / "bin/python"
        subprocess.run(
            [
                "uv",
                "pip",
                "install",
                "--python",
                str(python),
                "--no-deps",
                "protobuf>=6.33.0,<7",
                str(wheel),
            ],
            check=True,
        )
        subprocess.run(
            [
                str(python),
                "-I",
                "-c",
                "from src.runtime_contract.v1 import runtime_pb2 as pb; "
                "assert pb.DESCRIPTOR.package == 'jobseek.crawler.runtime.v1'",
            ],
            check=True,
            cwd=output,
        )


if __name__ == "__main__":
    main()
