#!/usr/bin/env python3
"""Run the rollout tool on a Typesense host with a key sent over SSH stdin."""

from __future__ import annotations

import argparse
import shlex
import subprocess
import sys
from pathlib import Path


def load_key(path: Path) -> str:
    for line in path.read_text().splitlines():
        if line.startswith("TYPESENSE_ADMIN_KEY="):
            value = line.partition("=")[2].strip().strip("\"'")
            if value:
                return value
    raise ValueError("TYPESENSE_ADMIN_KEY is missing")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", required=True)
    parser.add_argument("--identity", type=Path, required=True)
    parser.add_argument("--key-env-file", type=Path, required=True)
    parser.add_argument("rollout_args", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    if not args.rollout_args:
        parser.error("supply a rollout subcommand")
    remote = shlex.join(
        [
            "python3",
            "/root/typesense-candidate-order-rollout.py",
            *args.rollout_args,
            "--url",
            "http://127.0.0.1:8108",
            "--key-stdin",
        ]
    )
    command = ["ssh", "-o", "BatchMode=yes", "-i", str(args.identity), args.host, remote]
    result = subprocess.run(
        command, input=load_key(args.key_env_file) + "\n", text=True, check=False
    )
    return result.returncode


if __name__ == "__main__":
    sys.exit(main())
