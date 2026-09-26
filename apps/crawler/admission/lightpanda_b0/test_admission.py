"""Focused checks for the real-producer fixture admission path."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
import yaml
from redis.asyncio import Redis

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import controller  # noqa: E402
import fixture_server  # noqa: E402

from src.lightpanda import admission  # noqa: E402
from src.lightpanda.producer_client import ProducerResult  # noqa: E402


def test_frozen_fixture_matches_fixed_go_cohorts() -> None:
    assert (
        hashlib.sha256(controller.WORKLOAD.read_bytes()).hexdigest() == controller.WORKLOAD_SHA256
    )
    fixture = fixture_server.load_workload(controller.WORKLOAD)
    assert len(fixture["waves"]) == 4
    assert all(len(wave["tasks"]) == 4 for wave in fixture["waves"])
    c1 = admission.load_tasks(controller.WORKLOAD, 1)
    c4 = admission.load_tasks(controller.WORKLOAD, 4)
    assert len(c1) == 4 and len(c4) == 16
    assert all(task["source_url"].startswith("https://") for task in c4)
    assert {task["board_slug"] for task in c1} == {"browser-use-careers"}
    assert {task["board_slug"] for task in c4} == set(admission.COHORT)
    assert len({task["source_url"] for task in c4}) == 16
    assert b'"@type":"JobPosting"' in fixture_server.response_body(fixture["waves"][0]["tasks"][0])
    assert len(controller.SCHEDULE) == 16
    assert {pair for pair, _, _ in controller.SCHEDULE} == {
        *(f"c1-p{index}" for index in range(1, 4)),
        *(f"c4-p{index}" for index in range(1, 6)),
    }


def test_candidate_feed_uses_go_prepare_and_activate(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = admission.load_tasks(controller.WORKLOAD, 1)
    calls: list[dict[str, Any]] = []
    legacy_calls = 0

    async def enqueue(schedules: list[Any]) -> list[bool]:
        nonlocal legacy_calls
        legacy_calls += 1
        return [True] * len(schedules)

    async def manifest(cohort: str) -> ProducerResult:
        assert cohort == "c1"
        return ProducerResult("manifest", cohort="c1", board_slugs=("browser-use-careers",))

    async def request(**kwargs: Any) -> ProducerResult:
        calls.append(kwargs)
        if kwargs["operation"] == "prepare":
            return ProducerResult("prepared", preparation_digest="a" * 64)
        assert kwargs["operation"] == "activate"
        assert kwargs["expected_digest"] == "a" * 64
        return ProducerResult("activated", activated=True)

    monkeypatch.setattr(admission, "enqueue_scrapes", enqueue)
    monkeypatch.setattr(admission, "request_manifest", manifest)
    monkeypatch.setattr(admission, "request_task", request)
    result = asyncio.run(admission._feed(cast(Redis, SimpleNamespace()), tasks, True, 1000.0))
    assert result == [task["id"] for task in tasks]
    assert legacy_calls == 0
    assert [call["operation"] for call in calls] == ["prepare", "activate"] * 4
    assert all(call["operator_transfer"] is True and call["browser"] is True for call in calls)


def test_candidate_terminal_evidence_reads_redis_set(monkeypatch: pytest.MonkeyPatch) -> None:
    ids = [task["id"] for task in admission.load_tasks(controller.WORKLOAD, 1)]

    class RedisFixture:
        async def keys(self, _pattern: str) -> list[str]:
            return []

        async def hgetall(self, key: str) -> dict[str, str]:
            if key.endswith(":route"):
                return {"claim_sequence": "4"}
            return {task_id: json.dumps({"state": "terminal", "failures": 0}) for task_id in ids}

        async def smembers(self, _key: str) -> set[str]:
            return set(ids)

    async def fast_sleep(_seconds: float) -> None:
        return None

    monkeypatch.setenv("LIGHTPANDA_B0_QUEUE_NAMESPACE", "admission-b0")
    monkeypatch.setattr(admission.asyncio, "sleep", fast_sleep)
    assert asyncio.run(admission._redis_evidence(RedisFixture(), True, ids))["exact"] is True  # type: ignore[arg-type]


def test_legacy_seed_is_separate_from_go_transfer(monkeypatch: pytest.MonkeyPatch) -> None:
    tasks = admission.load_tasks(controller.WORKLOAD, 1)
    seen: list[int] = []

    async def enqueue(schedules: list[Any]) -> list[bool]:
        seen.append(len(schedules))
        return [True] * len(schedules)

    async def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("legacy seed must not contact Go producer")

    monkeypatch.setattr(admission, "enqueue_scrapes", enqueue)
    monkeypatch.setattr(admission, "request_manifest", forbidden)
    monkeypatch.setattr(admission, "request_task", forbidden)
    assert asyncio.run(admission._feed(cast(Redis, SimpleNamespace()), tasks, False, 1000.0)) == [
        task["id"] for task in tasks
    ]
    assert seen == [4]


def test_fixture_limits_selected_origins() -> None:
    workload = fixture_server.load_workload(controller.WORKLOAD)
    c1 = fixture_server.FixtureState(workload, 1)
    c4 = fixture_server.FixtureState(workload, 4)
    assert len(c1.routes) == 4 and c1.expected_count == 4
    assert len(c4.routes) == 16 and c4.expected_count == 16
    assert all(origin == "origin-0" for origin, _ in c1.routes)


def test_renderer_private_key_has_required_mode(tmp_path: Path) -> None:
    pki = tmp_path / "pki"
    controller.generate_pki(pki)
    assert (pki / "server-key.pem").stat().st_mode & 0o777 == 0o400


def test_compose_counts_real_producer_inside_equal_lane() -> None:
    compose = (HERE / "compose.yml").read_text()
    services = yaml.safe_load(compose)["services"]
    assert services["executor"]["healthcheck"]["test"] == [
        "CMD",
        "/usr/local/bin/lightpanda-b0-supervisor",
        "executor-health",
    ]
    assert services["executor"]["healthcheck"]["timeout"] == "3s"
    assert services["executor"]["healthcheck"]["interval"] == "5s"
    assert services["executor"]["cpus"] == 1.0
    assert services["control"]["cpus"] == sum(
        services[name]["cpus"] for name in ("producer", "supervisor", "executor", "renderer")
    )
    for service in services.values():
        for mount in service.get("tmpfs", []):
            assert mount.startswith("/")
            assert "uid=" not in mount or ":" in mount
    assert "LIGHTPANDA_B0_PRODUCER_MODE: enabled" in compose
    assert compose.count('LIGHTPANDA_B0_ROUTING_EPOCH: "2"') == 3
    migration = (
        HERE.parent.parent
        / "src/migrations/versions/0032_add_lightpanda_b0_routing_epoch_sequence.py"
    ).read_text()
    assert "START WITH 2" in migration
    assert 'LIGHTPANDA_B0_PRODUCER_CLIENT_UID: "0"' in compose
    assert services["runner"]["cap_add"] == ["DAC_OVERRIDE"]
    assert "producer-socket:/run/jobseek-lightpanda-producer:ro" in compose
    assert "producer-socket:/run/jobseek-lightpanda-producer" in compose
    assert "mem_limit: 32m" in compose
    assert "mem_limit: 96m" in compose
    assert "mem_limit: 384m" in compose
    assert "mem_limit: 1g" in compose
    assert "mem_limit: 1536m" in compose
    assert controller.evaluate({"failure": "unmeasured", "mode": "synthetic"})["admitted"] is False


def test_controller_rejects_unmeasured_or_changed_workload(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="workload identity changed"):
        changed = tmp_path / "workload.json"
        value = json.loads(controller.WORKLOAD.read_text())
        value["waves"][0]["tasks"][0]["title"] = "Changed"
        changed.write_text(json.dumps(value))
        fixture_server.load_workload(changed)
    assert not controller.evaluate({"arms": [], "mode": "synthetic"})["admitted"]


def _numeric_evidence() -> dict[str, Any]:
    source, renderer_source = "a" * 40, "b" * 40
    images = {
        role: f"sha256:{digit * 64}"
        for role, digit in (("candidate", "1"), ("browser", "2"), ("renderer", "3"))
    }
    provenance = {
        role: {
            "reference": reference,
            "image_id": reference,
            "repo_digests": [],
            "method": "image_label",
            "source_sha": renderer_source if role == "renderer" else source,
            "observed_source_label": renderer_source if role == "renderer" else source,
        }
        for role, reference in images.items()
    }
    arms: list[dict[str, Any]] = []
    for pair, concurrency, lane in controller.SCHEDULE:
        project = f"b0-{pair}-{lane}"
        caps = (
            {
                "producer": 32 * 1024**2,
                "supervisor": 96 * 1024**2,
                "executor": 384 * 1024**2,
                "renderer": 1024**3,
            }
            if lane == "candidate"
            else {"control": 1536 * 1024**2}
        )
        services = [
            {
                "service": service,
                "memory_max": cap,
                "memory_peak_bytes": cap // 4,
                "memory_peak_before": cap // 8,
                "networks": [f"{project}_claim", f"{project}_origin"]
                if service in {"renderer", "control"}
                else [f"{project}_claim"],
                "running": True,
                "state_status": "running",
                "healthcheck_present": service != "renderer",
                "health_status": None if service == "renderer" else "healthy",
                "restart_count": 0,
                "oom_killed": False,
                "memory_events_delta": {"oom": 0, "oom_kill": 0},
            }
            for service, cap in caps.items()
        ]
        peak = 400_000_000 if lane == "candidate" else 800_000_000
        arms.append(
            {
                "pair": pair,
                "concurrency": concurrency,
                "lane": lane,
                "project": project,
                "arm": {
                    "feed": 4 * concurrency,
                    "persisted": 4 * concurrency,
                    "terminal": 4 * concurrency,
                    "writes": 4 * concurrency,
                    "redis": {"exact": True},
                    "canonical_sha256": "c" * 64,
                    "per_task_sha256": {"same": "d" * 64},
                    "elapsed_ns": 100 if lane == "candidate" else 120,
                    "p99_ms": 100 if lane == "candidate" else 120,
                },
                "metrics": {"exact": True},
                "network": {
                    "internal": True,
                    "subnet": "11.252.0.0/24",
                    "fixture_aliases": [f"origin-{index}.lane.bench.test" for index in range(4)],
                },
                "resource": {
                    "samples": 60,
                    "retention_seconds": 5,
                    "peak_bytes": peak,
                    "sampled_peak_bytes": peak,
                    "retained_bytes": peak // 2,
                    "cpu_seconds": 1.0 if lane == "candidate" else 2.0,
                    "peak_policy": "synchronized_sum_memory_current",
                    "service_peak_policy": "safety_only_not_density",
                    "limit_bytes": 1536 * 1024**2,
                    "services": services,
                },
                "fixture": {
                    "requests": 4 * concurrency,
                    "max_global_in_flight": concurrency,
                    "max_per_origin_in_flight": 1,
                    "unexpected": 0,
                },
                "cleanup": {
                    "project": project,
                    "exact": True,
                    "errors": [],
                    "containers": [],
                    "networks": [],
                    "volumes": [],
                },
            }
        )
    return {
        "mode": "synthetic",
        "source_sha": source,
        "renderer_source_sha": renderer_source,
        "images": images,
        "image_provenance": provenance,
        "checkout": {
            "head": source,
            "postflight_head": source,
            "clean": True,
            "postflight_clean": True,
        },
        "arms": arms,
    }


def test_numeric_gate_requires_parity_and_repeatable_efficiency() -> None:
    evidence = _numeric_evidence()
    verdict = controller.evaluate(evidence)
    assert verdict["admitted"] is True
    assert len(verdict["pair_metrics"]) == 8
    evidence["arms"][0]["arm"]["canonical_sha256"] = "e" * 64
    assert "c1-p1:canonical_parity" in controller.evaluate(evidence)["reasons"]
    evidence = _numeric_evidence()
    for arm in evidence["arms"]:
        if arm["lane"] == "candidate" and arm["pair"] in {"c4-p1", "c4-p2", "c4-p3"}:
            arm["resource"]["peak_bytes"] = 1_400_000_000
            arm["resource"]["sampled_peak_bytes"] = 1_400_000_000
    assert "density" in controller.evaluate(evidence)["reasons"]
