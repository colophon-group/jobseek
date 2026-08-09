"""Regression tests for production worker memory/concurrency sizing."""

from __future__ import annotations

from pathlib import Path

import yaml


def test_simple_worker_concurrency_is_bounded_for_one_gibibyte_limit():
    compose_path = Path(__file__).parents[1] / "docker-compose.yml"
    compose = yaml.safe_load(compose_path.read_text())

    for worker_name in ("worker-1", "worker-2", "worker-3"):
        worker = compose["services"][worker_name]
        assert worker["mem_limit"] == "1g"
        assert worker["cpus"] == 1.0
        assert worker["environment"]["DISCOVERY_CONCURRENCY"] == "20"
        assert worker["environment"]["MONITOR_CONCURRENCY"] == "5"
