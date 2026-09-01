from __future__ import annotations

import argparse
import asyncio
import contextlib
import io
import socket
import socketserver
import struct
import subprocess
import sys
import time
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.request import urlopen

import pytest
from prometheus_client.parser import text_string_to_metric_families

from src.metrics import (
    BROWSER_RETRY_CAPTURE_CONTRACT,
    _ProcessTreeMetricsCollector,
    _QuietThreadingWSGIServer,
    _start_metrics_http_server,
    start_metrics_server,
)
from src.runtime_cost.process_tree import (
    SAMPLER_TIMING_BUCKETS,
    ProcessTreeSample,
    SamplerMetricsSnapshot,
    TimingHistogramSnapshot,
)


def _reset_connection(port: int) -> None:
    sock = socket.create_connection(("127.0.0.1", port))
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_LINGER, struct.pack("ii", 1, 0))
    sock.close()


def test_metrics_listener_defaults_to_loopback() -> None:
    server, _thread = _start_metrics_http_server(0)
    try:
        assert server.server_address[0] == "127.0.0.1"
    finally:
        server.shutdown()
        server.server_close()


def test_metrics_server_starts_process_tree_sampler() -> None:
    with (
        patch("src.metrics._start_process_tree_sampler") as start_sampler,
        patch("src.metrics._start_metrics_http_server") as start_http,
    ):
        start_metrics_server(9123)

    start_sampler.assert_called_once_with()
    start_http.assert_called_once_with(9123)


def _sample(sequence: int) -> ProcessTreeSample:
    return ProcessTreeSample(
        observation_sequence=sequence,
        observation_monotonic_seconds=float(sequence),
        observation_unixtime_seconds=1_800_000_000 + sequence,
        interval_seconds=0.5,
        root_cpu_seconds=float(sequence * 10),
        process_tree_cpu_seconds=float(sequence * 10 + 2),
        process_tree_cpu_delta_seconds=float(sequence * 10 + 2),
        root_rss_bytes=sequence * 100,
        process_tree_rss_bytes=sequence * 100 + 20,
        descendant_count=sequence,
    )


def test_process_tree_exposition_keeps_one_generation_during_concurrent_publish() -> None:
    collector = _ProcessTreeMetricsCollector()
    collector.start(0.5)
    collector.publish(_sample(1))

    exposition = collector.collect()
    families = [next(exposition)]
    collector.publish(_sample(2))
    families.extend(exposition)
    samples = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in families
        for sample in family.samples
    }

    assert samples[("crawler_runtime_process_root_cpu_seconds_total", ())] == 10
    assert samples[("crawler_runtime_process_tree_cpu_seconds_total", ())] == 12
    assert samples[("crawler_runtime_process_root_resident_memory_bytes", ())] == 100
    assert samples[("crawler_runtime_process_tree_resident_memory_bytes", ())] == 120
    for component in ("root_cpu", "tree_cpu", "root_rss", "tree_rss", "descendants"):
        labels = (("component", component),)
        assert samples[("crawler_runtime_process_tree_observation_sequence", labels)] == 1
        assert (
            samples[("crawler_runtime_process_tree_observation_unixtime_seconds", labels)]
            == 1_800_000_001
        )


def test_process_tree_exposition_includes_isolated_process_causal_evidence() -> None:
    collector = _ProcessTreeMetricsCollector()
    histogram = TimingHistogramSnapshot(
        bucket_counts=(0, 0, 0, 0, 0, 0, *([1] * (len(SAMPLER_TIMING_BUCKETS) - 6))),
        count=1,
        sum_seconds=0.25,
        limit_violations=1,
    )
    snapshot = SamplerMetricsSnapshot(
        sample=_sample(3),
        successes=3,
        failures=1,
        scheduler_late_gaps=2,
        collection_overrun_gaps=4,
        starts=2,
        wake_lateness=histogram,
        collection_duration=histogram,
        handoff_duration=histogram,
    )
    collector.attach(0.5, lambda: snapshot)

    samples = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in collector.collect()
        for sample in family.samples
    }

    assert samples[("crawler_runtime_process_tree_samples_total", (("outcome", "success"),))] == 3
    assert samples[("crawler_runtime_process_tree_samples_total", (("outcome", "failure"),))] == 1
    assert samples[("crawler_runtime_process_tree_sampling_gaps_total", ())] == 6
    assert (
        samples[
            (
                "crawler_runtime_process_tree_sampling_gap_reasons_total",
                (("reason", "scheduler_late"),),
            )
        ]
        == 2
    )
    assert (
        samples[
            (
                "crawler_runtime_process_tree_sampling_gap_reasons_total",
                (("reason", "collection_overrun"),),
            )
        ]
        == 4
    )
    assert samples[("crawler_runtime_process_tree_sampler_starts_total", ())] == 2
    assert samples[("crawler_runtime_process_tree_sampler_wake_lateness_seconds_count", ())] == 1
    assert samples[("crawler_runtime_process_tree_sampler_wake_lateness_seconds_sum", ())] == 0.25
    assert (
        samples[
            (
                "crawler_runtime_process_tree_sampler_wake_lateness_seconds_bucket",
                (("le", "0.25"),),
            )
        ]
        == 1
    )
    assert {
        labels: value
        for (name, labels), value in samples.items()
        if name == "crawler_runtime_process_tree_sampler_timing_limit_violations_total"
    } == {
        (("phase", "wake_lateness"),): 1,
        (("phase", "collection"),): 1,
        (("phase", "handoff"),): 1,
    }


def test_process_tree_strict_timing_children_are_preseeded_at_zero() -> None:
    samples = [
        sample
        for family in _ProcessTreeMetricsCollector().collect()
        for sample in family.samples
        if sample.name == "crawler_runtime_process_tree_sampler_timing_limit_violations_total"
    ]

    assert {tuple(sample.labels.items()): sample.value for sample in samples} == {
        (("phase", "wake_lateness"),): 0,
        (("phase", "collection"),): 0,
        (("phase", "handoff"),): 0,
    }


def test_process_tree_source_failure_is_persistent_and_metrics_endpoint_stays_available() -> None:
    collector = _ProcessTreeMetricsCollector()
    calls = 0
    empty = TimingHistogramSnapshot.empty()
    healthy = SamplerMetricsSnapshot(
        sample=_sample(1),
        successes=1,
        failures=0,
        scheduler_late_gaps=0,
        collection_overrun_gaps=0,
        starts=1,
        wake_lateness=empty,
        collection_duration=empty,
        handoff_duration=empty,
    )

    def source() -> SamplerMetricsSnapshot:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("synthetic IPC failure")
        return healthy

    collector.attach(0.5, source)
    first = list(collector.collect())
    second = list(collector.collect())
    failures = [
        sample.value
        for family in second
        for sample in family.samples
        if sample.name == "crawler_runtime_process_tree_samples_total"
        and sample.labels == {"outcome": "failure"}
    ]

    assert first
    assert failures == [1]


def test_fresh_process_seeds_every_browser_retry_child_at_zero() -> None:
    script = """
from prometheus_client import generate_latest
import src.metrics
print(generate_latest().decode())
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr
    seeded = {
        (sample.name, tuple(sorted(sample.labels.items()))): sample.value
        for family in text_string_to_metric_families(completed.stdout)
        for sample in family.samples
    }
    for family in BROWSER_RETRY_CAPTURE_CONTRACT:
        metric = str(family["metric"])
        labels = family["labels"]
        if len(labels) == 1:
            label_name, values = labels[0]
            for value in values:
                assert seeded[(metric, ((label_name, value),))] == 0
        else:
            first_name, first_values = labels[0]
            second_name, second_values = labels[1]
            for first_value in first_values:
                for second_value in second_values:
                    expected_labels = tuple(
                        sorted(((first_name, first_value), (second_name, second_value)))
                    )
                    assert seeded[(metric, expected_labels)] == 0


@pytest.mark.parametrize("command", ["run", "run-browser", "export", "drain"])
def test_invalid_installed_version_stops_roles_before_external_clients(command: str) -> None:
    from src import cli

    create_pool = AsyncMock()
    create_http = MagicMock()
    close_pools = AsyncMock()
    installed_distribution = MagicMock()
    installed_distribution.version = "unknown"
    installed_distribution.read_text.return_value = None
    with (
        patch.object(cli, "parse_args", return_value=argparse.Namespace(command=command)),
        patch.object(cli, "setup_logging"),
        patch.object(cli, "log"),
        patch.object(cli, "create_local_pool", new=create_pool),
        patch.object(cli, "create_http_client", new=create_http),
        patch.object(cli, "close_all_pools", new=close_pools),
        patch("src.metrics.is_source_checkout", return_value=False),
        patch("src.metrics.get_distribution", return_value=installed_distribution),
        pytest.raises(RuntimeError, match="distribution version is invalid"),
    ):
        asyncio.run(cli.run())

    create_pool.assert_not_awaited()
    create_http.assert_not_called()
    close_pools.assert_awaited_once_with()


def test_metrics_module_import_does_not_require_runtime_cost_package() -> None:
    script = """
import builtins

real_import = builtins.__import__
def guarded_import(name, *args, **kwargs):
    if name.startswith("src.runtime_cost"):
        raise ModuleNotFoundError(name)
    return real_import(name, *args, **kwargs)

builtins.__import__ = guarded_import
import src.metrics
"""
    completed = subprocess.run(
        [sys.executable, "-c", script],
        check=False,
        capture_output=True,
        text=True,
    )

    assert completed.returncode == 0, completed.stderr


def test_metrics_client_reset_does_not_emit_traceback():
    """A peer reset before the request line is expected scrape noise (#5354)."""
    stderr = io.StringIO()
    with contextlib.redirect_stderr(stderr):
        server, _thread = _start_metrics_http_server(0, addr="127.0.0.1")
        port = server.server_address[1]
        try:
            for _ in range(5):
                _reset_connection(port)

            # A completed request proves the earlier threaded handlers had a
            # scheduling opportunity and that the listener remains healthy.
            with urlopen(f"http://127.0.0.1:{port}/metrics", timeout=2) as response:
                assert response.status == 200
                assert b"crawler_build_info" in response.read()
            time.sleep(0.05)
        finally:
            server.shutdown()
            server.server_close()

    assert stderr.getvalue() == ""


@pytest.mark.parametrize("error", [ConnectionResetError(), BrokenPipeError()])
def test_expected_disconnects_skip_default_handler(error: OSError):
    server = object.__new__(_QuietThreadingWSGIServer)
    with patch.object(socketserver.BaseServer, "handle_error") as default_handler:
        try:
            raise error
        except OSError:
            server.handle_error(object(), ("127.0.0.1", 1))
    default_handler.assert_not_called()


def test_unexpected_handler_error_keeps_default_traceback_path():
    server = object.__new__(_QuietThreadingWSGIServer)
    request = object()
    address = ("127.0.0.1", 1)
    with patch.object(socketserver.BaseServer, "handle_error") as default_handler:
        try:
            raise RuntimeError("unexpected")
        except RuntimeError:
            server.handle_error(request, address)
    default_handler.assert_called_once_with(request, address)
