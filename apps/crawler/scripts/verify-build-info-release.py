#!/usr/bin/env python3
"""Verify installed crawler build-info wiring without external side effects."""

from __future__ import annotations

import argparse
import asyncio
import sysconfig
from importlib.metadata import version as distribution_version
from pathlib import Path
from unittest.mock import patch

from src import cli, metrics


class _StopAfterMetrics(Exception):
    """Stop a role before it can initialize a database or HTTP client."""


ROLES = (
    ("worker-1", "run", 9095),
    ("worker-2", "run", 9096),
    ("worker-3", "run", 9097),
    ("browser-1", "run-browser", 9098),
    ("exporter", "export", 9093),
    ("drain", "drain", 9094),
)


async def _forbid_database() -> None:
    raise AssertionError("role reached database initialization before the release probe stopped it")


async def _close_no_pools() -> None:
    """Replace global pool cleanup after the intentionally interrupted role."""


def _forbid_http() -> None:
    raise AssertionError("role reached HTTP initialization before the release probe stopped it")


def _assert_build_info(expected_version: str) -> None:
    samples = list(metrics.build_info.collect())[0].samples
    exact = [
        sample
        for sample in samples
        if sample.labels == {"version": expected_version} and sample.value == 1.0
    ]
    assert len(exact) == 1, (
        f"expected one crawler_build_info sample for {expected_version!r}, got {samples!r}"
    )
    assert all(sample.labels.get("version") != "unknown" for sample in samples), samples


def _verify_role(role: str, command: str, port: int, expected_version: str) -> None:
    metrics.build_info.clear()
    starts = 0

    def start_and_stop(actual_port: int) -> None:
        nonlocal starts
        starts += 1
        assert actual_port == port, (role, actual_port, port)
        metrics.start_metrics_server(actual_port)
        _assert_build_info(expected_version)
        raise _StopAfterMetrics

    args = argparse.Namespace(command=command)
    with (
        patch.object(cli, "parse_args", return_value=args),
        patch.object(cli, "setup_logging"),
        patch.object(cli, "start_metrics_server", new=start_and_stop),
        patch.object(cli, "create_local_pool", new=_forbid_database),
        patch.object(cli, "close_all_pools", new=_close_no_pools),
        patch.object(cli, "create_http_client", new=_forbid_http),
        patch.object(cli.settings, "metrics_port", port),
        patch.object(metrics, "_start_process_tree_sampler"),
        patch.object(metrics, "_start_metrics_http_server"),
    ):
        try:
            asyncio.run(cli.run())
        except _StopAfterMetrics:
            pass
        else:
            raise AssertionError(f"{role} did not initialize crawler build info")

    assert starts == 1, (role, starts)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--expected-version", required=True)
    parser.add_argument("--require-installed", action="store_true")
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

    for role, command, port in ROLES:
        _verify_role(role, command, port, expected_version)

    print(f"verified crawler_build_info={expected_version} for {len(ROLES)} installed roles")


if __name__ == "__main__":
    main()
