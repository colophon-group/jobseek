"""Tests for the post-deploy host textfile ingestion gate."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "verify-grafana-host-metrics.py"
SPEC = importlib.util.spec_from_file_location("verify_grafana_host_metrics", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
verify = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(verify)


def _row(value: float, **labels: str) -> dict:
    return {"metric": labels, "value": [1_800_000_000, str(value)]}


def _healthy_results(now: float) -> dict:
    roles = sorted(verify.EXPECTED_ROLES)
    return {
        "fresh_sampler": [_row(now - 30, host_role=role) for role in roles],
        "crawler_up": [
            _row(1, instance=instance) for instance in sorted(verify.EXPECTED_CRAWLER_INSTANCES)
        ],
        "probe_series": [_row(4, host_role=role) for role in roles],
        "failed_probes": [],
        "container_series": [_row(1, host_role=role) for role in roles],
        "stopped_containers": [],
        "backup_series": [
            _row(1, host_role="postgresql", service="postgresql"),
            _row(1, host_role="typesense", service="typesense"),
        ],
        "failed_backups": [],
        "backup_helper_image_series": [],
        "backup_helper_image_gc_series": [],
        "postgresql_ready": [_row(1)],
        "postgresql_shared_memory": [_row(1)],
        "postgresql_emergency_reserve": [_row(1)],
        "postgresql_checkpoint_metrics": [_row(1)],
        "postgresql_query_latency": [_row(1)],
        "typesense_ready": [_row(1)],
        "codex_review_series": [_row(4)],
        "alloy_series": [
            _row(1, host_role="crawler", collector="host"),
            _row(1, host_role="crawler", collector="compose"),
            _row(1, host_role="postgresql", collector="host"),
            _row(1, host_role="typesense", collector="host"),
        ],
        "alloy_unready": [],
        "alloy_rejections": [],
        "alloy_stale": [],
        "alloy_backlog": [],
        "active_series": [_row(6000)],
        "crawler_series": [_row(1000)],
        "crawler_capability_series": [_row(500)],
        "crawler_created_series": [_row(0)],
        "redis_series": [_row(100)],
        "unix_series": [_row(1200)],
    }


def test_validate_results_requires_fresh_complete_healthy_fleet() -> None:
    now = 1_800_000_000.0

    verify.validate_results(_healthy_results(now), now=now, max_age_seconds=300)


def test_validate_results_rejects_missing_role_and_stale_sampler() -> None:
    now = 1_800_000_000.0
    missing = _healthy_results(now)
    missing["fresh_sampler"].pop()
    with pytest.raises(verify.VerificationError, match="all expected host roles"):
        verify.validate_results(missing, now=now, max_age_seconds=300)

    stale = _healthy_results(now)
    stale["fresh_sampler"][0]["value"][1] = str(now - 301)
    with pytest.raises(verify.VerificationError, match="stale or invalid"):
        verify.validate_results(stale, now=now, max_age_seconds=300)


def test_validate_results_requires_post_deployment_sampler() -> None:
    now = 1_800_000_000.0
    cached = _healthy_results(now)

    with pytest.raises(verify.VerificationError, match="predates the completed deployment"):
        verify.validate_results(
            cached,
            now=now,
            max_age_seconds=300,
            minimum_collected_at=now - 1,
        )


def test_validate_results_rejects_silent_probe_or_backup_failure() -> None:
    now = 1_800_000_000.0
    probe_failure = _healthy_results(now)
    probe_failure["failed_probes"] = [_row(0, host_role="postgresql", probe="backup")]
    with pytest.raises(verify.VerificationError, match="failed_probes is nonzero"):
        verify.validate_results(probe_failure, now=now, max_age_seconds=300)

    missing_backup = _healthy_results(now)
    missing_backup["backup_series"].pop()
    with pytest.raises(verify.VerificationError, match="missing=typesense/typesense"):
        verify.validate_results(missing_backup, now=now, max_age_seconds=300)

    missing_shm = _healthy_results(now)
    missing_shm["postgresql_shared_memory"] = []
    with pytest.raises(verify.VerificationError, match="shared-memory metric is missing"):
        verify.validate_results(missing_shm, now=now, max_age_seconds=300)

    missing_reserve = _healthy_results(now)
    missing_reserve["postgresql_emergency_reserve"] = []
    with pytest.raises(verify.VerificationError, match="emergency-reserve metric is missing"):
        verify.validate_results(missing_reserve, now=now, max_age_seconds=300)

    missing_checkpoint = _healthy_results(now)
    missing_checkpoint["postgresql_checkpoint_metrics"] = []
    with pytest.raises(verify.VerificationError, match="checkpoint-duration metric is missing"):
        verify.validate_results(missing_checkpoint, now=now, max_age_seconds=300)

    missing_query_latency = _healthy_results(now)
    missing_query_latency["postgresql_query_latency"] = []
    with pytest.raises(
        verify.VerificationError, match="statistics-query latency metric is missing"
    ):
        verify.validate_results(missing_query_latency, now=now, max_age_seconds=300)

    missing_codex_deadman = _healthy_results(now)
    missing_codex_deadman["codex_review_series"] = [_row(3)]
    with pytest.raises(verify.VerificationError, match="daily-review deadman"):
        verify.validate_results(missing_codex_deadman, now=now, max_age_seconds=300)


def test_validate_results_requires_all_crawler_scrape_targets_healthy() -> None:
    now = 1_800_000_000.0
    missing = _healthy_results(now)
    missing["crawler_up"].pop()
    with pytest.raises(verify.VerificationError, match="crawler scrape target.*worker-3"):
        verify.validate_results(missing, now=now, max_age_seconds=300)

    unhealthy = _healthy_results(now)
    unhealthy["crawler_up"][0]["value"][1] = "0"
    with pytest.raises(verify.VerificationError) as captured:
        verify.validate_results(unhealthy, now=now, max_age_seconds=300)

    failure = captured.value.failures[0]
    assert failure.query_name == "crawler_up"
    assert failure.host_role == "crawler"
    assert failure.observed_value == 0


def test_validate_results_rejects_unexpected_or_duplicate_crawler_target() -> None:
    now = 1_800_000_000.0
    unexpected = _healthy_results(now)
    unexpected["crawler_up"].append(_row(1, instance="unexpected"))
    with pytest.raises(verify.VerificationError, match="invalid target labels"):
        verify.validate_results(unexpected, now=now, max_age_seconds=300)

    duplicate = _healthy_results(now)
    duplicate["crawler_up"].append(_row(1, instance="worker-1"))
    with pytest.raises(verify.VerificationError, match="invalid target labels"):
        verify.validate_results(duplicate, now=now, max_age_seconds=300)


def test_validate_results_accepts_optional_web_postgresql_backup() -> None:
    now = 1_800_000_000.0
    results = _healthy_results(now)
    results["backup_series"].append(_row(1, host_role="typesense", service="web-postgresql"))
    results["backup_helper_image_series"].append(
        _row(1, host_role="typesense", service="web-postgresql")
    )
    results["backup_helper_image_gc_series"].append(
        _row(1, host_role="typesense", service="web-postgresql")
    )

    verify.validate_results(results, now=now, max_age_seconds=300)

    results["backup_helper_image_gc_series"] = []
    with pytest.raises(verify.VerificationError, match="activated web backup"):
        verify.validate_results(results, now=now, max_age_seconds=300)


@pytest.mark.parametrize(
    ("result_name", "labels", "expected"),
    [
        (
            "failed_probes",
            {"host_role": "postgresql", "probe": "backup"},
            "failed_probes is nonzero: postgresql/backup",
        ),
        (
            "stopped_containers",
            {"host_role": "crawler", "container": "deploy-exporter-1"},
            "stopped_containers is nonzero: crawler/deploy-exporter-1",
        ),
        (
            "failed_backups",
            {"host_role": "typesense", "service": "typesense"},
            "failed_backups is nonzero: typesense/typesense",
        ),
    ],
)
def test_validate_results_identifies_unhealthy_series(
    result_name: str, labels: dict[str, str], expected: str
) -> None:
    now = 1_800_000_000.0
    results = _healthy_results(now)
    results[result_name] = [_row(0, **labels)]

    with pytest.raises(verify.VerificationError, match=expected):
        verify.validate_results(results, now=now, max_age_seconds=300)


def test_validate_results_rejects_alloy_delivery_failure_or_series_growth() -> None:
    now = 1_800_000_000.0
    rejected = _healthy_results(now)
    rejected["alloy_rejections"] = [_row(2)]
    with pytest.raises(verify.VerificationError, match="alloy_rejections is nonzero"):
        verify.validate_results(rejected, now=now, max_age_seconds=300)

    missing_collector = _healthy_results(now)
    missing_collector["alloy_series"].pop()
    with pytest.raises(verify.VerificationError, match="three host and one compose"):
        verify.validate_results(missing_collector, now=now, max_age_seconds=300)

    excessive = _healthy_results(now)
    excessive["active_series"] = [_row(12_001)]
    with pytest.raises(verify.VerificationError, match="12000-series budget"):
        verify.validate_results(excessive, now=now, max_age_seconds=300)

    excessive_capability = _healthy_results(now)
    excessive_capability["crawler_capability_series"] = [_row(2_001)]
    with pytest.raises(verify.VerificationError, match="2000-series budget"):
        verify.validate_results(excessive_capability, now=now, max_age_seconds=300)

    created = _healthy_results(now)
    created["crawler_created_series"] = [_row(1)]
    with pytest.raises(verify.VerificationError, match="_created series are present"):
        verify.validate_results(created, now=now, max_age_seconds=300)


def test_failures_include_query_value_timestamp_age_and_role() -> None:
    now = 1_800_000_000.0
    results = _healthy_results(now)
    results["fresh_sampler"][0]["value"][1] = str(now - 301)
    results["alloy_stale"] = [_row(181, host_role="postgresql", collector="host")]
    results["crawler_series"] = [_row(5_001)]

    with pytest.raises(verify.VerificationError) as captured:
        verify.validate_results(results, now=now, max_age_seconds=300)

    failures = captured.value.failures
    assert len(failures) == 3
    by_query = {failure.query_name: failure for failure in failures}
    stale_sampler = by_query["fresh_sampler"]
    assert stale_sampler.host_role == "crawler"
    assert stale_sampler.observed_value == now - 301
    assert stale_sampler.sample_timestamp == now - 301
    assert stale_sampler.age_seconds == 301
    assert by_query["alloy_stale"].host_role == "postgresql"
    assert by_query["alloy_stale"].observed_value == 181
    assert by_query["crawler_series"].host_role == "crawler"
    assert by_query["crawler_series"].observed_value == 5_001
    assert by_query["crawler_series"].sample_timestamp == now
    assert by_query["crawler_series"].age_seconds == 0


def test_invalid_observation_is_attributed_and_not_written_to_evidence() -> None:
    now = 1_800_000_000.0
    results = _healthy_results(now)
    results["failed_probes"] = [_row(0, host_role="postgresql", probe="unsafe label with spaces")]

    with pytest.raises(verify.VerificationError) as captured:
        verify.validate_results(results, now=now, max_age_seconds=300)

    assert captured.value.failures[0].query_name == "failed_probes"
    assert captured.value.failures[0].labels == {}


def test_non_evidence_prometheus_metadata_is_ignored() -> None:
    now = 1_800_000_000.0
    results = _healthy_results(now)
    for row in results["fresh_sampler"]:
        row["metric"]["job"] = "integrations/unix"
        row["metric"]["instance"] = "https://metrics.example.com/prometheus"

    verify.validate_results(results, now=now, max_age_seconds=300)

    results["alloy_stale"] = [
        _row(
            181,
            host_role="postgresql",
            collector="host",
            job="integrations/unix",
        )
    ]
    with pytest.raises(verify.VerificationError) as captured:
        verify.validate_results(results, now=now, max_age_seconds=300)

    assert captured.value.failures[0].labels == {
        "host_role": "postgresql",
        "collector": "host",
    }


def test_validate_results_rejects_duplicate_alloy_collector_series() -> None:
    now = 1_800_000_000.0
    results = _healthy_results(now)
    results["alloy_series"].append(_row(1, host_role="crawler", collector="host"))

    with pytest.raises(verify.VerificationError, match="duplicate series labels"):
        verify.validate_results(results, now=now, max_age_seconds=300)


def test_verify_allows_delayed_convergence_within_deployment_window() -> None:
    now = [1_800_000_000.0]
    stale = _healthy_results(now[0])
    stale["fresh_sampler"][0]["value"][1] = str(now[0] - 301)
    responses = [stale, _healthy_results(now[0] + 10)]

    def query_all(_base_url: str, _username: str, _password: str, _timeout_seconds: float) -> dict:
        result = responses.pop(0)
        if not responses:
            for row in result["fresh_sampler"]:
                row["value"][1] = str(now[0])
        return result

    evidence = verify.verify(
        "https://prom.example.com/api/prom/push",
        "tenant",
        "secret",
        deployment_completed_at=now[0] - 1,
        convergence_seconds=20,
        max_age_seconds=300,
        query_all=query_all,
        wall_time=lambda: now[0],
        monotonic=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert evidence["status"] == "passed"
    assert evidence["attempts"] == 2


def test_verify_fails_closed_after_persistent_staleness() -> None:
    now = [1_800_000_000.0]
    stale = _healthy_results(now[0])
    stale["fresh_sampler"][0]["value"][1] = str(now[0] - 301)

    with pytest.raises(verify.VerificationError) as captured:
        verify.verify(
            "https://prom.example.com/api/prom/push",
            "tenant",
            "secret",
            deployment_completed_at=now[0],
            convergence_seconds=20,
            max_age_seconds=300,
            query_all=lambda *_args: stale,
            wall_time=lambda: now[0],
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    assert captured.value.evidence is not None
    assert captured.value.evidence["status"] == "failed"
    assert captured.value.evidence["attempts"] == 2
    assert captured.value.evidence["failures"][0]["query_name"] == "fresh_sampler"


def test_verify_preserves_health_evidence_across_terminal_transport_error() -> None:
    now = [1_800_000_000.0]
    stale = _healthy_results(now[0])
    for row in stale["fresh_sampler"]:
        row["value"][1] = str(now[0])
    stale["crawler_series"] = [_row(5_001)]
    attempts = [stale, verify.httpx.ConnectError("connection reset")]

    def query_all(*_args) -> dict:
        result = attempts.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    with pytest.raises(verify.VerificationError) as captured:
        verify.verify(
            "https://prom.example.com/api/prom/push",
            "tenant",
            "secret",
            deployment_completed_at=now[0],
            convergence_seconds=20,
            max_age_seconds=300,
            query_all=query_all,
            wall_time=lambda: now[0],
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    evidence = captured.value.evidence
    assert evidence is not None
    assert evidence["failures"][0]["query_name"] == "crawler_series"
    assert evidence["failures"][0]["observed_value"] == 5_001
    assert evidence["latest_query_error"]["attempt"] == 2
    assert "ConnectError" in evidence["latest_query_error"]["failures"][0]["invariant"]


def test_verify_uses_transport_error_when_no_query_batch_completed() -> None:
    now = [1_800_000_000.0]

    with pytest.raises(verify.VerificationError) as captured:
        verify.verify(
            "https://prom.example.com/api/prom/push",
            "tenant",
            "secret",
            deployment_completed_at=now[0],
            convergence_seconds=10,
            max_age_seconds=300,
            query_all=lambda *_args: (_ for _ in ()).throw(
                verify.httpx.ConnectError("connection reset")
            ),
            wall_time=lambda: now[0],
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    evidence = captured.value.evidence
    assert evidence is not None
    assert "ConnectError" in evidence["failures"][0]["invariant"]
    assert evidence["latest_query_error"]["attempt"] == 1


def test_verify_can_converge_after_health_and_transport_failures() -> None:
    now = [1_800_000_000.0]
    stale = _healthy_results(now[0])
    stale["crawler_series"] = [_row(5_001)]
    responses = [stale, verify.httpx.ConnectError("connection reset"), "healthy"]

    def query_all(*_args) -> dict:
        result = responses.pop(0)
        if isinstance(result, Exception):
            raise result
        healthy = _healthy_results(now[0]) if result == "healthy" else result
        for row in healthy["fresh_sampler"]:
            row["value"][1] = str(now[0])
        return healthy

    evidence = verify.verify(
        "https://prom.example.com/api/prom/push",
        "tenant",
        "secret",
        deployment_completed_at=now[0],
        convergence_seconds=30,
        max_age_seconds=300,
        query_all=query_all,
        wall_time=lambda: now[0],
        monotonic=lambda: now[0],
        sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
    )

    assert evidence["status"] == "passed"
    assert evidence["attempts"] == 3
    assert evidence["failures"] == []


def test_verify_rejects_cached_pre_deployment_sampler() -> None:
    now = [1_800_000_000.0]
    cached = _healthy_results(now[0])

    with pytest.raises(verify.VerificationError) as captured:
        verify.verify(
            "https://prom.example.com/api/prom/push",
            "tenant",
            "secret",
            deployment_completed_at=now[0] - 1,
            convergence_seconds=10,
            max_age_seconds=300,
            query_all=lambda *_args: cached,
            wall_time=lambda: now[0],
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    assert captured.value.evidence is not None
    assert captured.value.evidence["attempts"] == 1
    assert all(
        failure["invariant"] == "sampler timestamp predates the completed deployment"
        for failure in captured.value.evidence["failures"]
    )


def test_verify_rejects_query_batch_that_overruns_convergence_deadline() -> None:
    now = [1_800_000_000.0]

    def query_all(_base_url: str, _username: str, _password: str, _timeout_seconds: float) -> dict:
        now[0] += 400
        results = _healthy_results(now[0])
        for row in results["fresh_sampler"]:
            row["value"][1] = str(now[0])
        return results

    with pytest.raises(verify.VerificationError) as captured:
        verify.verify(
            "https://prom.example.com/api/prom/push",
            "tenant",
            "secret",
            deployment_completed_at=now[0],
            convergence_seconds=300,
            max_age_seconds=300,
            query_all=query_all,
            wall_time=lambda: now[0],
            monotonic=lambda: now[0],
            sleep=lambda seconds: now.__setitem__(0, now[0] + seconds),
        )

    assert captured.value.evidence is not None
    assert captured.value.evidence["status"] == "failed"
    assert captured.value.evidence["attempts"] == 1
    assert "after the convergence deadline" in captured.value.failures[0].invariant


def test_workflow_reports_install_and_telemetry_outcomes_separately() -> None:
    workflow = (
        SCRIPT.parents[1] / ".github" / "workflows" / "deploy-hetzner-observability.yml"
    ).read_text(encoding="utf-8")

    assert "Record the completed deployment boundary" in workflow
    assert "--deployment-completed-at" in workflow
    assert "--convergence-seconds 300" in workflow
    assert "timeout-minutes: 10" in workflow
    assert "post_deploy_telemetry_health_failed" in workflow
    assert "deploy_install_failed" in workflow
    assert "Publish telemetry convergence evidence" in workflow


def test_verifier_changes_trigger_observability_deployment() -> None:
    workflow = SCRIPT.parents[1] / ".github" / "workflows" / "deploy-hetzner-observability.yml"

    assert "- 'scripts/verify-grafana-host-metrics.py'" in workflow.read_text(encoding="utf-8")
