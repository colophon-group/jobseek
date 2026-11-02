#!/usr/bin/env python3
"""Verify offline package identity and native CLI delegation without side effects."""

from __future__ import annotations

import argparse
import asyncio
import sysconfig
from importlib.metadata import version as distribution_version
from pathlib import Path
from unittest.mock import patch

from src import cli, metrics


class _StopAfterExec(Exception):
    """Stop before replacing the control CLI with its native executable."""


ROLES = (
    ("run", "go-ordinary-worker", ["go-ordinary-worker"]),
    ("run-browser", "go-ordinary-worker", ["go-ordinary-worker"]),
    ("export", "go-typesense-exporter", ["go-typesense-exporter", "--run"]),
    ("drain", "go-r2-drain", ["go-r2-drain"]),
)


async def _forbid_database() -> None:
    raise AssertionError("native alias initialized a Python database pool")


def _forbid_python_runtime(*_args, **_kwargs) -> None:
    raise AssertionError("native alias initialized the retired Python runtime")


def _verify_role(command: str, binary: str, argv: list[str]) -> None:
    calls = []

    def exec_and_stop(actual_binary: str, actual_argv: list[str]) -> None:
        calls.append((actual_binary, actual_argv))
        raise _StopAfterExec

    args = argparse.Namespace(command=command, batch_size=2000, interval=1)
    with (
        patch.object(cli, "parse_args", return_value=args),
        patch.object(cli.os, "execvp", side_effect=exec_and_stop),
        patch.object(cli, "setup_logging", side_effect=_forbid_python_runtime),
        patch.object(cli, "start_metrics_server", side_effect=_forbid_python_runtime),
        patch.object(cli, "create_local_pool", new=_forbid_database),
        patch.object(cli, "create_http_client", side_effect=_forbid_python_runtime),
    ):
        try:
            asyncio.run(cli.run())
        except _StopAfterExec:
            pass
        else:
            raise AssertionError(f"{command} did not exec its native implementation")
    assert calls == [(binary, argv)]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-version", required=True)
    provenance = parser.add_mutually_exclusive_group(required=True)
    provenance.add_argument("--require-installed", action="store_true")
    provenance.add_argument("--require-source-checkout", action="store_true")
    parser.add_argument("--expected-source-root", type=Path)
    args = parser.parse_args()

    expected_version = args.expected_version.strip()
    assert expected_version and expected_version != "unknown"
    assert distribution_version("jobseek-crawler") == expected_version
    assert metrics._read_version() == expected_version

    if args.require_installed:
        site_packages = Path(sysconfig.get_paths()["purelib"]).resolve()
        module_path = Path(metrics.__file__).resolve()
        assert module_path.is_relative_to(site_packages), (module_path, site_packages)
        assert not metrics.is_source_checkout()
    else:
        assert args.expected_source_root is not None
        module_root = Path(metrics.__file__).resolve().parent.parent
        assert module_root == args.expected_source_root.resolve(), (
            module_root,
            args.expected_source_root,
        )
        assert metrics.is_source_checkout()

    for command, binary, argv in ROLES:
        _verify_role(command, binary, argv)

    print(f"verified offline package={expected_version} and {len(ROLES)} native aliases")


if __name__ == "__main__":
    main()
