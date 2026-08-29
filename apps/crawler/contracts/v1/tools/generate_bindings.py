#!/usr/bin/env python3
"""Generate the pinned crawler runtime v1 Python and Go bindings."""

from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

GRPCIO_TOOLS_VERSION = "1.76.0"
LIBPROTOC_VERSION = "31.1"
PROTOBUF_PYTHON_VERSION = "6.33.6"
PROTOC_GEN_GO_VERSION = "1.36.10"
PROTOBUF_GO_VERSION = "1.36.10"
PYTHON_RUNTIME = "3.13"
GO_LANGUAGE_VERSION = "1.24.0"

ROOT = Path(__file__).resolve().parents[1]
PROTO = ROOT / "runtime.proto"
PYTHON_PACKAGE = ROOT / "python" / "jobseek_runtime_v1"
GO_PACKAGE = ROOT / "gen" / "go"
MANIFEST = ROOT / "gen" / "manifest.json"

PACKAGE_INIT = '''"""Generated crawler runtime v1 Python contract."""

from __future__ import annotations

from . import runtime_pb2

CONTRACT_VERSION = "crawler.runtime/v1"

__all__ = ["CONTRACT_VERSION", "runtime_pb2"]
'''


class GenerationError(RuntimeError):
    """Raised when the pinned generator environment is unavailable or drifts."""


def _run(command: list[str], *, cwd: Path = ROOT) -> str:
    try:
        result = subprocess.run(
            command,
            cwd=cwd,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError) as exc:
        detail = getattr(exc, "stderr", None) or str(exc)
        raise GenerationError(f"generator command failed: {' '.join(command)}: {detail}") from exc
    return result.stdout.strip()


def _require_version(distribution: str, expected: str) -> None:
    try:
        actual = importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError as exc:
        raise GenerationError(f"{distribution} {expected} is required") from exc
    if actual != expected:
        raise GenerationError(f"{distribution} {expected} is required; found {actual}")


def _require_generators() -> None:
    _require_version("grpcio-tools", GRPCIO_TOOLS_VERSION)
    _require_version("protobuf", PROTOBUF_PYTHON_VERSION)
    libprotoc = _run([sys.executable, "-m", "grpc_tools.protoc", "--version"])
    if libprotoc != f"libprotoc {LIBPROTOC_VERSION}":
        raise GenerationError(
            f"grpcio-tools must expose libprotoc {LIBPROTOC_VERSION}; found {libprotoc}"
        )
    plugin = shutil.which("protoc-gen-go")
    if plugin is None:
        raise GenerationError(f"protoc-gen-go v{PROTOC_GEN_GO_VERSION} is required")
    plugin_version = _run([plugin, "--version"])
    if plugin_version != f"protoc-gen-go v{PROTOC_GEN_GO_VERSION}":
        raise GenerationError(
            f"protoc-gen-go v{PROTOC_GEN_GO_VERSION} is required; found {plugin_version}"
        )


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def generate(destination: Path) -> None:
    """Generate every managed output below ``destination``."""

    _require_generators()
    python_package = destination / "python" / "jobseek_runtime_v1"
    go_package = destination / "gen" / "go"
    python_package.mkdir(parents=True, exist_ok=True)
    go_package.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="runtime-v1-protoc-") as raw_dir:
        raw = Path(raw_dir)
        raw_python = raw / "python"
        raw_go = raw / "go"
        raw_python.mkdir()
        raw_go.mkdir()
        _run(
            [
                sys.executable,
                "-m",
                "grpc_tools.protoc",
                f"--proto_path={ROOT}",
                f"--python_out={raw_python}",
                f"--go_out={raw_go}",
                "--go_opt=paths=source_relative",
                str(PROTO),
            ]
        )
        generated_python = raw_python / "runtime_pb2.py"
        generated_go = raw_go / "runtime.pb.go"
        if not generated_python.is_file() or not generated_go.is_file():
            raise GenerationError("pinned protoc invocation omitted an expected binding")
        python_output = python_package / "runtime_pb2.py"
        python_output.write_text(
            "# ruff: noqa\n# fmt: off\n" + generated_python.read_text(encoding="utf-8"),
            encoding="utf-8",
        )
        shutil.copyfile(generated_go, go_package / "runtime.pb.go")

    (python_package / "__init__.py").write_text(PACKAGE_INIT, encoding="utf-8")
    outputs = {
        "gen/go/runtime.pb.go": _sha256(go_package / "runtime.pb.go"),
        "python/jobseek_runtime_v1/__init__.py": _sha256(python_package / "__init__.py"),
        "python/jobseek_runtime_v1/runtime_pb2.py": _sha256(python_package / "runtime_pb2.py"),
    }
    manifest = {
        "format": "jobseek.crawler.runtime-bindings/v1",
        "go_language_version": GO_LANGUAGE_VERSION,
        "generators": {
            "grpcio_tools": GRPCIO_TOOLS_VERSION,
            "libprotoc": LIBPROTOC_VERSION,
            "protoc_gen_go": PROTOC_GEN_GO_VERSION,
        },
        "outputs": outputs,
        "runtimes": {
            "go_protobuf": PROTOBUF_GO_VERSION,
            "python": PYTHON_RUNTIME,
            "python_protobuf": PROTOBUF_PYTHON_VERSION,
        },
        "source": "runtime.proto",
        "source_sha256": _sha256(PROTO),
    }
    manifest_path = destination / "gen" / "manifest.json"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def write_generated() -> None:
    with tempfile.TemporaryDirectory(prefix="runtime-v1-bindings-") as temp_dir:
        generated = Path(temp_dir)
        generate(generated)
        for relative in (
            Path("gen/go/runtime.pb.go"),
            Path("gen/manifest.json"),
            Path("python/jobseek_runtime_v1/__init__.py"),
            Path("python/jobseek_runtime_v1/runtime_pb2.py"),
        ):
            destination = ROOT / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(generated / relative, destination)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.parse_args()
    write_generated()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
