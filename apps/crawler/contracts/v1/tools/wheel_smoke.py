from __future__ import annotations

import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

CRAWLER = Path(__file__).parents[3]
EXPECTED = {
    "src/runtime_contract/v1/extension_rules.py",
    "src/runtime_contract/v1/framing.py",
    "src/runtime_contract/v1/privacy_rules.py",
    "src/runtime_contract/v1/runtime_pb2.py",
}


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
            missing = EXPECTED.difference(archive.namelist())
            if missing:
                raise AssertionError(f"crawler wheel does not contain {sorted(missing)}")
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
                "from src.runtime_contract.v1.extension_rules import EXTENSION_RULES; "
                "from src.runtime_contract.v1.framing import decode_message, encode_message; "
                "from src.runtime_contract.v1.privacy_rules import SECRET_NAMES; "
                "message = pb.ClientMessage(hello=pb.ClientHello()); "
                "wire = encode_message(message, 64); "
                "assert decode_message(wire, pb.ClientMessage, 64) == message; "
                "assert 'authentication' in SECRET_NAMES; "
                "assert 'jobseek.runtime.v1/browser/evaluation-json' in EXTENSION_RULES; "
                "assert pb.DESCRIPTOR.package == 'jobseek.crawler.runtime.v1'",
            ],
            check=True,
            cwd=output,
        )


if __name__ == "__main__":
    main()
