"""Fail-closed Required-CI driver for candidate runtime-v1 conformance."""

from __future__ import annotations

import json
import os
import runpy
import selectors
import shutil
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest

_CONTRACT_ROOT = (Path(__file__).parents[1] / "contracts" / "v1").resolve()
_PYTHON_ROOT = _CONTRACT_ROOT / "conformance" / "python"
_MAX_COMMAND_OUTPUT = 2 * 1024 * 1024
_COMMAND_TIMEOUT_SECONDS = 60.0


def _discover_python_tests() -> tuple[dict[str, Any], set[str]]:
    sources = sorted(_PYTHON_ROOT.rglob("*.py"))
    hidden = [
        path for path in sources if path.name != "__init__.py" and not path.name.startswith("test_")
    ]
    if hidden:
        names = ", ".join(path.name for path in hidden)
        raise AssertionError(f"unexposed Python conformance modules: {names}")

    modules = [path for path in sources if path.name.startswith("test_")]
    if not modules:
        raise AssertionError("runtime v1 has no Python conformance modules")

    exported: dict[str, Any] = {}
    original_names: set[str] = set()
    for path in modules:
        if path.is_symlink() or not path.resolve().is_relative_to(_PYTHON_ROOT.resolve()):
            raise AssertionError(f"Python conformance module escapes its root: {path}")
        module_id = "__".join(path.relative_to(_PYTHON_ROOT).with_suffix("").parts)
        namespace = runpy.run_path(
            str(path),
            run_name=f"runtime_v1_conformance_{module_id}",
        )
        tests = {
            name: value
            for name, value in namespace.items()
            if name.startswith("test_") and callable(value)
        }
        if not tests:
            raise AssertionError(f"Python conformance module has no tests: {path.name}")
        for name, value in sorted(tests.items()):
            exported_name = f"test_runtime_v1_{module_id.removeprefix('test_')}__{name[5:]}"
            if exported_name in exported:
                raise AssertionError(f"duplicate exported conformance test: {exported_name}")
            exported[exported_name] = value
            original_names.add(name)

    if not exported:
        raise AssertionError("runtime v1 Python conformance discovery was empty")
    return exported, original_names


_EXPORTED_PYTHON_TESTS, _ORIGINAL_PYTHON_TEST_NAMES = _discover_python_tests()

assert {
    "test_current_descriptor_matches_frozen_introduction_and_git_base",
    "test_committed_self_regenerated_baseline_cannot_authenticate_itself",
    "test_prior_main_addition_cannot_later_be_removed",
} <= _ORIGINAL_PYTHON_TEST_NAMES

globals().update(_EXPORTED_PYTHON_TESTS)


def _terminate(process: subprocess.Popen[bytes]) -> None:
    process.kill()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.terminate()
        process.wait(timeout=5)


def _bounded_command(
    command: list[str],
    *,
    cwd: Path,
    env: dict[str, str],
    timeout: float = _COMMAND_TIMEOUT_SECONDS,
) -> bytes:
    process = subprocess.Popen(  # noqa: S603 - fixed executable and arguments
        command,
        cwd=cwd,
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    assert process.stdout is not None
    selector = selectors.DefaultSelector()
    selector.register(process.stdout, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate(process)
                raise AssertionError(f"command timed out after {timeout:g}s: {command!r}")
            for key, _ in selector.select(timeout=min(0.25, remaining)):
                chunk = os.read(key.fd, min(65536, _MAX_COMMAND_OUTPUT + 1 - len(output)))
                if not chunk:
                    selector.unregister(key.fileobj)
                    continue
                output.extend(chunk)
                if len(output) > _MAX_COMMAND_OUTPUT:
                    _terminate(process)
                    raise AssertionError(
                        f"command exceeded {_MAX_COMMAND_OUTPUT} output bytes: {command!r}"
                    )
        returncode = process.wait(timeout=5)
    finally:
        selector.close()
        process.stdout.close()
    if returncode != 0:
        rendered = output.decode("utf-8", errors="replace")
        raise AssertionError(f"command failed with exit {returncode}: {command!r}\n{rendered}")
    return bytes(output)


def _go_packages() -> list[Path]:
    symlinks = sorted(path for path in _CONTRACT_ROOT.rglob("*") if path.is_symlink())
    if symlinks:
        names = ", ".join(str(path.relative_to(_CONTRACT_ROOT)) for path in symlinks)
        raise AssertionError(f"runtime v1 conformance tree contains symlinks: {names}")
    sources = sorted(_CONTRACT_ROOT.rglob("*_test.go"))
    if not sources:
        raise AssertionError("runtime v1 has no Go conformance test sentinel")
    packages: set[Path] = set()
    for source in sources:
        if source.is_symlink():
            raise AssertionError(f"Go conformance test cannot be a symlink: {source}")
        resolved = source.resolve()
        if not resolved.is_relative_to(_CONTRACT_ROOT):
            raise AssertionError(f"Go conformance test escapes contract root: {source}")
        packages.add(resolved.parent)
    return sorted(packages)


def _go_environment(tmp_path: Path) -> dict[str, str]:
    return {
        "CGO_ENABLED": "1",
        "GOCACHE": str(tmp_path / "go-cache"),
        "GOENV": "off",
        "GOMODCACHE": str(tmp_path / "go-mod-cache"),
        "GOPATH": str(tmp_path / "go-path"),
        "GO111MODULE": "off",
        "GOTOOLCHAIN": "local",
        "LANG": "C.UTF-8",
        "LC_ALL": "C.UTF-8",
        "PATH": os.environ.get("PATH", "/usr/local/go/bin:/usr/bin:/bin"),
        "TERM": "dumb",
        "TMPDIR": tempfile.gettempdir(),
    }


@pytest.mark.timeout(180)
def test_runtime_v1_all_go_conformance_packages(tmp_path: Path) -> None:
    go = shutil.which("go")
    if go is None:
        raise AssertionError("Required CI is missing the Go toolchain")
    executable = Path(go).resolve()
    if not executable.is_file():
        raise AssertionError(f"resolved Go executable is not a file: {executable}")

    env = _go_environment(tmp_path)
    version = _bounded_command([str(executable), "version"], cwd=_CONTRACT_ROOT, env=env)
    assert version.startswith(b"go version go"), version.decode(errors="replace")

    for package in _go_packages():
        output = _bounded_command(
            [str(executable), "test", "-race", "-count=1", "-json", "."],
            cwd=package,
            env=env,
        )
        passed: set[str] = set()
        for line in output.splitlines():
            try:
                event = json.loads(line)
            except json.JSONDecodeError as error:
                raise AssertionError(
                    f"go test emitted non-JSON output in {package}: {line!r}"
                ) from error
            if event.get("Action") == "pass" and isinstance(event.get("Test"), str):
                passed.add(event["Test"])
        if not passed:
            raise AssertionError(f"Go conformance package executed zero named tests: {package}")
        _bounded_command([str(executable), "vet", "."], cwd=package, env=env)
