"""Regression tests for production worker memory/concurrency sizing."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_retained_legacy_worker_concurrency_is_bounded_for_one_gibibyte_limit():
    compose_path = Path(__file__).parent / "fixtures/docker-compose.legacy-b0.yml"
    compose = yaml.safe_load(compose_path.read_text())

    for worker_name in ("worker-1", "worker-2", "worker-3"):
        worker = compose["services"][worker_name]
        assert worker["mem_limit"] == "1g"
        assert worker["cpus"] == 1.0
        assert worker["environment"]["DISCOVERY_CONCURRENCY"] == "20"
        assert worker["environment"]["MONITOR_CONCURRENCY"] == "5"


def test_native_owner_preserves_aggregate_http_worker_capacity():
    compose_path = Path(__file__).parents[1] / "docker-compose.yml"
    services = yaml.safe_load(compose_path.read_text())["services"]
    retained_legacy = yaml.safe_load(
        (Path(__file__).parent / "fixtures/docker-compose.legacy-b0.yml").read_text()
    )["services"]
    native = services["ordinary-go"]
    for setting in ("DISCOVERY_CONCURRENCY", "MONITOR_CONCURRENCY"):
        previous_capacity = sum(
            int(retained_legacy[name]["environment"][setting])
            for name in ("worker-1", "worker-2", "worker-3")
        )
        assert int(native["environment"][setting]) == previous_capacity
    assert native["cpus"] == 2.0
    assert native["mem_limit"] == "1g"
    assert native["environment"]["GOMEMLIMIT"] == "512MiB"
