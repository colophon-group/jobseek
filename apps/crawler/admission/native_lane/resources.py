"""Shared-role resource accounting for the bounded comparison, not billing."""

from __future__ import annotations

import threading
import time
from collections.abc import Mapping
from typing import Any

from admission.lightpanda_b0.controller import _cpu_usage, _integer

# Same public catalogue snapshot as the four-node October 30 production read.
# Prices are net compute EUR/hour. The benchmark runs on one architecture;
# these role weights do not model architecture performance or billed savings.
PRICES = {
    "crawler": {"cpus": 8, "memory_gib": 16, "eur_per_hour": 0.0256},
    "renderer": {"cpus": 2, "memory_gib": 4, "eur_per_hour": 0.0096},
    "postgresql": {"cpus": 4, "memory_gib": 8, "eur_per_hour": 0.0136},
    "typesense": {"cpus": 4, "memory_gib": 8, "eur_per_hour": 0.0136},
}


def role(service: str) -> str:
    if service == "postgres":
        return "postgresql"
    if service in ("renderer", "typesense", "fixture"):
        return service
    if service in (
        "ordinary",
        "legacy-simple",
        "legacy-browser",
        "redis",
        "reaper",
        "exporter",
        "drain",
    ):
        return "crawler"
    raise ValueError("comparison service has no declared resource role")


def allocation(groups: Mapping[str, Mapping[str, float]], seconds: float, correct: int) -> float:
    if seconds <= 0 or correct <= 0:
        raise ValueError("resource allocation needs completed work and positive time")
    hourly = 0.0
    for name, price in PRICES.items():
        group = groups.get(name, {})
        share = min(
            1,
            max(
                group.get("mean_cpu_cores", 0) / price["cpus"],
                group.get("mean_working_memory_bytes", 0) / (price["memory_gib"] * 1024**3),
            ),
        )
        hourly += share * price["eur_per_hour"]
    return hourly * seconds / 3600 / correct * 1000


def sample(rows: list[dict[str, Any]], stop: threading.Event, output: dict[str, Any]) -> None:
    def memory() -> dict[str, int]:
        result: dict[str, int] = {}
        for row in rows:
            path = row["path"]
            # Match production working-set accounting rather than counting
            # reclaimable inactive file pages as retained process memory.
            inactive = 0
            for line in (path / "memory.stat").read_text().splitlines():
                key, value = line.split()
                if key == "inactive_file":
                    inactive = int(value)
            name = role(row["service"])
            result[name] = result.get(name, 0) + max(
                0, _integer(path / "memory.current") - inactive
            )
        return result

    started = previous_time = time.monotonic()
    previous = memory()
    area = {name: 0.0 for name in previous}
    peak = sum(previous.values())
    count = 1
    while True:
        done = stop.wait(0.1)
        now = time.monotonic()
        current = memory()
        for name in area:
            area[name] += (now - previous_time) * (previous[name] + current[name]) / 2
        peak = max(peak, sum(current.values()))
        count += 1
        previous, previous_time = current, now
        if done:
            break
    seconds = previous_time - started
    groups: dict[str, dict[str, float]] = {
        name: {"mean_working_memory_bytes": value / seconds, "mean_cpu_cores": 0}
        for name, value in area.items()
    }
    total_cpu = 0.0
    for row in rows:
        cpu = (_cpu_usage(row["path"]) - row["cpu_before_usec"]) / 1e6
        if cpu < 0:
            raise ValueError("comparison CPU counter decreased")
        total_cpu += cpu
        groups[role(row["service"])]["mean_cpu_cores"] += cpu / seconds
    output.update(
        samples=count,
        duration_seconds=seconds,
        groups=groups,
        sampled_peak_working_memory_bytes=peak,
        retained_working_memory_bytes=sum(previous.values()),
        cpu_seconds=total_cpu,
    )
