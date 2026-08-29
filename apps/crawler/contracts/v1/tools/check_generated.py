#!/usr/bin/env python3
"""Regenerate crawler runtime v1 bindings and fail on any byte drift."""

from __future__ import annotations

import argparse
import difflib
import tempfile
from pathlib import Path

from generate_bindings import ROOT, generate

MANAGED = (
    Path("gen/go/runtime.pb.go"),
    Path("gen/manifest.json"),
    Path("python/jobseek_runtime_v1/__init__.py"),
    Path("python/jobseek_runtime_v1/runtime_pb2.py"),
)


def _difference(relative: Path, expected: bytes, actual: bytes) -> str:
    try:
        expected_text = expected.decode("utf-8").splitlines(keepends=True)
        actual_text = actual.decode("utf-8").splitlines(keepends=True)
    except UnicodeDecodeError:
        return f"generated binary drift: {relative}"
    return "".join(
        difflib.unified_diff(
            expected_text,
            actual_text,
            fromfile=f"committed/{relative}",
            tofile=f"regenerated/{relative}",
        )
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    failures: list[str] = []
    with tempfile.TemporaryDirectory(prefix="runtime-v1-check-") as temp_dir:
        generated = Path(temp_dir)
        generate(generated)
        for relative in MANAGED:
            committed = ROOT / relative
            if not committed.is_file():
                failures.append(f"missing generated output: {relative}")
                continue
            expected = committed.read_bytes()
            actual = (generated / relative).read_bytes()
            if expected != actual:
                failures.append(_difference(relative, expected, actual))

    if failures:
        raise SystemExit("\n".join(failures))
    print(f"checked {len(MANAGED)} deterministic runtime-v1 binding files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
