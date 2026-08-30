from __future__ import annotations

import copy
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator, FormatChecker

from src.migration_gates.__main__ import main
from src.migration_gates.model import GateModelError, evaluate_promotion, load_candidate_policy

CRAWLER_ROOT = Path(__file__).resolve().parents[1]
RESOURCE_ROOT = CRAWLER_ROOT / "src" / "migration_gates" / "resources"
SCHEMA_ROOT = RESOURCE_ROOT / "schemas"
UNSAFE_IDENTIFIERS = (
    "https://secret.example/token?credential=hunter2",
    "token=secret",
    ("oversized-secret-" + "x" * 4096)[:4096],
)
UNSAFE_MARKERS = ("secret.example", "hunter2", "token=secret", "oversized-secret")
MAX_PUBLIC_ERROR_LENGTH = 256


def _json(path: Path) -> dict:
    value = json.loads(path.read_text())
    assert isinstance(value, dict)
    return value


def _policy() -> dict:
    return load_candidate_policy()


def _observation(required_class: dict) -> dict:
    rare = required_class["sample_policy"] == "rare"
    return {
        "class_id": required_class["class_id"],
        "work_class": required_class["work_class"],
        "capability_class": required_class["capability_class"],
        "browser_class": required_class["browser_class"],
        "browser_backend": required_class["browser_backend"],
        "service_lane": required_class["service_lane"],
        "resource_authority": required_class["resource_authority"],
        "provider_family": "mixed",
        "sample_size": 10 if rare else 1000,
        "population_size": 10 if rare else None,
        "completed_schedule_cycles": 7,
        "replay_complete": True,
        "mismatches": {
            "url_set": 0,
            "field_hash": 0,
            "result_flag": 0,
            "projected_db_effect": 0,
        },
        "freshness": {
            "schedule_compliance_ratio": 0.99,
            "error_budget_burn": 1.0,
            "due_to_claim_p95_seconds": 300,
            "due_to_claim_p99_seconds": 900,
            "due_to_complete_p95_seconds": 1800,
            "due_to_complete_p99_seconds": 3600,
        },
        "capacity": {
            "eligible_demand_present": True,
            "routed_assignment_present": True,
            "zero_demand_proven": False,
            "zero_assignment_proven": False,
            "avoidable_idle_seconds_with_eligible_backlog": 0,
            "utilization_p95_ratio": 0.8,
            "headroom_p05_ratio": 0.2,
        },
        "request_amplification_ratio": 1.05,
        "antibot_regression_ratio": 1.05,
        "resource_saturation_events": 0,
    }


def _evidence() -> dict:
    policy = _policy()
    return {
        "schema_version": "jobseek.crawler-migration-promotion-evidence/v1",
        "evidence_id": "candidate-week-1",
        "policy_id": policy["policy_id"],
        "routing_revision": "route-2026-08-30",
        "candidate": {
            "implementation": "go",
            "release": "go-candidate-1",
            "region": "hetzner-eu-central",
            "cohort": "candidate",
        },
        "window": {
            "start_at": "2026-08-01T00:00:00Z",
            "end_at": "2026-08-08T00:00:00Z",
            "duration_seconds": 604800,
        },
        "freeze_signals": {
            "stale_authoritative_writes": 0,
            "bulk_gone_or_delist_events": 0,
            "tdm_violations": 0,
            "queue_loss_or_duplication_events": 0,
            "origin_policy_violations": 0,
            "cross_backend_runtime_fallbacks": 0,
        },
        "observations": [_observation(item) for item in policy["required_classes"]],
    }


def _reason_codes(result: dict) -> set[str]:
    return {reason["code"] for reason in result["reasons"]}


def _set_unassigned(
    observation: dict,
    *,
    eligible_demand_present: bool,
    zero_demand_proven: bool,
    zero_assignment_proven: bool,
) -> None:
    observation["sample_size"] = 0
    observation["population_size"] = 0
    observation["completed_schedule_cycles"] = 0
    observation["freshness"] = {name: None for name in observation["freshness"]}
    observation["request_amplification_ratio"] = None
    observation["antibot_regression_ratio"] = None
    observation["capacity"] = {
        "eligible_demand_present": eligible_demand_present,
        "routed_assignment_present": False,
        "zero_demand_proven": zero_demand_proven,
        "zero_assignment_proven": zero_assignment_proven,
        "avoidable_idle_seconds_with_eligible_backlog": 0,
        "utilization_p95_ratio": 0,
        "headroom_p05_ratio": 1,
    }


def test_checked_in_policy_and_schemas_are_valid() -> None:
    schemas = {path.name: _json(path) for path in sorted(SCHEMA_ROOT.glob("*.schema.json"))}
    for schema in schemas.values():
        Draft202012Validator.check_schema(schema)

    validator = Draft202012Validator(
        schemas["promotion-policy-v1.schema.json"], format_checker=FormatChecker()
    )
    assert not list(validator.iter_errors(_policy()))


def test_policy_is_full_backend_neutral_runtime_v1_cross_product() -> None:
    policy = _policy()
    browser_rows = [
        item for item in policy["required_classes"] if item["browser_backend"] != "none"
    ]
    assert {
        (item["work_class"], item["browser_backend"], item["capability_class"])
        for item in browser_rows
    } == {
        (work_class, backend, capability)
        for work_class in ("monitor", "detail")
        for backend in ("lightpanda", "chromium")
        for capability in (
            "navigation-evaluation",
            "interaction-capture",
            "identity-transport",
        )
    }
    assert len(browser_rows) == 12
    assert all(item["browser_class"] == "service" for item in browser_rows)
    assert all(item["service_lane"] == item["browser_backend"] for item in browser_rows)
    assert all(
        item["resource_authority"] == f"{item['browser_backend']}-service-cgroup-v2"
        for item in browser_rows
    )

    http_rows = [item for item in policy["required_classes"] if item["browser_backend"] == "none"]
    assert {item["work_class"] for item in http_rows} == {"monitor", "detail"}
    assert all(
        item["capability_class"] == "shared-http"
        and item["browser_class"] == "none"
        and item["service_lane"] == "none"
        and item["resource_authority"] == "worker-cgroup-v2"
        for item in http_rows
    )


def test_policy_vocabulary_maps_exactly_to_landed_runtime_v1_enums() -> None:
    policy = _policy()
    runtime_proto = (CRAWLER_ROOT / "contracts" / "v1" / "runtime.proto").read_text()
    browser_capabilities = set(policy["label_allowlists"]["capability_class"]) - {"shared-http"}
    assert browser_capabilities == {
        "navigation-evaluation",
        "interaction-capture",
        "identity-transport",
    }
    for capability in browser_capabilities:
        enum_name = f"BROWSER_CAPABILITY_CLASS_{capability.replace('-', '_').upper()}"
        assert f"{enum_name} =" in runtime_proto
    for backend in ("lightpanda", "chromium"):
        assert f"BROWSER_BACKEND_{backend.upper()} =" in runtime_proto
        assert f"BROWSER_SERVICE_LANE_{backend.upper()} =" in runtime_proto
    assert "render-evaluate" not in json.dumps(policy)
    assert "chromium-required" not in json.dumps(policy)


def test_metric_contract_has_bounded_gate_labels_and_separate_service_cgroups() -> None:
    metric_contract = _policy()["metric_contract"]
    assert metric_contract["gate_labels"] == [
        "implementation",
        "region",
        "cohort",
        "work_class",
        "capability_class",
        "browser_class",
        "browser_backend",
        "service_lane",
        "provider_family",
    ]
    assert metric_contract["service_resource_authority"] == "isolated-service-cgroup-v2"
    assert metric_contract["service_resource_labels"] == ["browser_backend", "service_lane"]
    assert set(metric_contract["service_resources"]) == {
        "browser_seconds_total",
        "concurrency_limit",
        "cpu_seconds_total",
        "crashes_total",
        "recycles_total",
        "resident_memory_bytes",
        "resource_limit_outcomes_total",
        "sessions",
    }


@pytest.mark.parametrize(
    ("class_id", "field", "value"),
    [
        ("monitor_http", "browser_class", "service"),
        ("monitor_http", "capability_class", "navigation-evaluation"),
        ("monitor_http", "service_lane", "lightpanda"),
        (
            "monitor_lightpanda_navigation_evaluation",
            "service_lane",
            "chromium",
        ),
        (
            "monitor_lightpanda_navigation_evaluation",
            "resource_authority",
            "chromium-service-cgroup-v2",
        ),
        (
            "monitor_chromium_identity_transport",
            "capability_class",
            "shared-http",
        ),
    ],
)
def test_policy_rejects_forbidden_dimension_combinations(
    class_id: str, field: str, value: str
) -> None:
    policy = _policy()
    row = next(item for item in policy["required_classes"] if item["class_id"] == class_id)
    row[field] = value

    with pytest.raises(GateModelError):
        evaluate_promotion(policy, _evidence())


def test_public_evaluator_rejects_baseline_before_scoring() -> None:
    evidence = _evidence()
    evidence["candidate"]["cohort"] = "baseline"
    evidence["freeze_signals"]["tdm_violations"] = 1

    with pytest.raises(GateModelError, match=r"candidate\.cohort.*validator=const"):
        evaluate_promotion(_policy(), evidence)


@pytest.mark.parametrize(
    ("document", "field", "value", "safe_location"),
    [
        ("evidence", "raw_host", "crawler.internal", "<root>"),
        ("observation", "board_url", "https://secret.example/jobs", "observations.[]"),
        ("policy", "activation", {"enabled": True}, "<root>"),
    ],
)
def test_public_evaluator_rejects_unknown_fields_recursively(
    document: str, field: str, value: object, safe_location: str
) -> None:
    policy = _policy()
    evidence = _evidence()
    target = {
        "evidence": evidence,
        "observation": evidence["observations"][0],
        "policy": policy,
    }[document]
    target[field] = value

    with pytest.raises(GateModelError) as error:
        evaluate_promotion(policy, evidence)
    message = str(error.value)
    assert f"at {safe_location}" in message
    assert "validator=additionalProperties" in message
    assert field not in message
    assert str(value) not in message
    assert len(message) <= MAX_PUBLIC_ERROR_LENGTH


@pytest.mark.parametrize("unsafe_id", UNSAFE_IDENTIFIERS)
def test_public_evaluator_rejects_unsafe_or_oversized_evidence_id(unsafe_id: str) -> None:
    evidence = _evidence()
    evidence["evidence_id"] = unsafe_id

    with pytest.raises(GateModelError, match="evidence_id") as error:
        evaluate_promotion(_policy(), evidence)
    message = str(error.value)
    assert unsafe_id not in message
    assert all(marker not in message for marker in UNSAFE_MARKERS)
    assert len(message) <= MAX_PUBLIC_ERROR_LENGTH
    assert "validator=" in message


@pytest.mark.parametrize("unsafe_id", ["token=secret", "p" * 65])
def test_public_evaluator_rejects_unsafe_or_oversized_policy_id(unsafe_id: str) -> None:
    policy = _policy()
    evidence = _evidence()
    policy["policy_id"] = unsafe_id
    evidence["policy_id"] = unsafe_id

    with pytest.raises(GateModelError, match="policy_id"):
        evaluate_promotion(policy, evidence)


@pytest.mark.parametrize("unsafe_revision", UNSAFE_IDENTIFIERS)
def test_public_evaluator_rejects_unsafe_routing_revision(unsafe_revision: str) -> None:
    evidence = _evidence()
    evidence["routing_revision"] = unsafe_revision

    with pytest.raises(GateModelError, match="routing_revision") as error:
        evaluate_promotion(_policy(), evidence)
    message = str(error.value)
    assert unsafe_revision not in message
    assert all(marker not in message for marker in UNSAFE_MARKERS)
    assert len(message) <= MAX_PUBLIC_ERROR_LENGTH


def test_complete_boundary_evidence_promotes_advisory_candidate() -> None:
    result = evaluate_promotion(_policy(), _evidence())

    assert result == {
        "schema_version": "jobseek.crawler-migration-promotion-decision/v1",
        "policy_id": "crawler-migration-candidate-runtime-v1-2026-08-30",
        "policy_status": "candidate",
        "evidence_id": "candidate-week-1",
        "routing_revision": "route-2026-08-30",
        "candidate": {
            "cohort": "candidate",
            "implementation": "go",
            "region": "hetzner-eu-central",
            "release": "go-candidate-1",
        },
        "decision": "promote",
        "reasons": [],
    }


def test_missing_duplicate_and_unknown_classes_fail_closed() -> None:
    missing = _evidence()
    missing["observations"].pop()
    with pytest.raises(
        GateModelError,
        match="missing required classes: detail_chromium_identity_transport",
    ):
        evaluate_promotion(_policy(), missing)

    duplicate = _evidence()
    duplicate["observations"].append(copy.deepcopy(duplicate["observations"][0]))
    with pytest.raises(GateModelError, match="monitor_http is duplicated"):
        evaluate_promotion(_policy(), duplicate)

    unknown = _evidence()
    unknown["observations"][0]["class_id"] = "board_123"
    with pytest.raises(GateModelError, match="missing required classes: monitor_http"):
        evaluate_promotion(_policy(), unknown)


def test_short_window_and_standard_sample_shortfall_hold() -> None:
    evidence = _evidence()
    evidence["window"] = {
        "start_at": "2026-08-01T00:00:00Z",
        "end_at": "2026-08-02T00:00:00Z",
        "duration_seconds": 86400,
    }
    evidence["observations"][0]["sample_size"] = 999

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "hold"
    assert _reason_codes(result) == {"hold:window-duration", "hold:class-sample-coverage"}
    sample_reason = next(
        reason for reason in result["reasons"] if reason["code"] == "hold:class-sample-coverage"
    )
    assert sample_reason["affected_classes"] == ["monitor_http"]


@pytest.mark.parametrize("population_size,sample_size", [(None, 10), (11, 10), (1, 0)])
def test_rare_class_requires_complete_declared_population(
    population_size: int | None, sample_size: int
) -> None:
    evidence = _evidence()
    rare = evidence["observations"][1]
    rare["population_size"] = population_size
    rare["sample_size"] = sample_size

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "hold"
    assert "hold:rare-population-coverage" in _reason_codes(result)


def test_replay_correctness_cycles_and_freshness_are_independent_holds() -> None:
    evidence = _evidence()
    observation = evidence["observations"][2]
    observation["completed_schedule_cycles"] = 6
    observation["replay_complete"] = False
    observation["mismatches"]["field_hash"] = 1
    observation["freshness"]["schedule_compliance_ratio"] = 0.989
    observation["freshness"]["due_to_claim_p95_seconds"] = 301

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "hold"
    assert _reason_codes(result) == {
        "hold:class-cycle-coverage",
        "hold:correctness-mismatch",
        "hold:freshness-latency",
        "hold:freshness-schedule-compliance",
        "hold:replay-incomplete",
    }


@pytest.mark.parametrize(
    ("signal", "reason"),
    [
        ("stale_authoritative_writes", "freeze:stale-authoritative-write"),
        ("bulk_gone_or_delist_events", "freeze:bulk-gone-or-delist"),
        ("tdm_violations", "freeze:tdm-violation"),
        ("queue_loss_or_duplication_events", "freeze:queue-loss-or-duplication"),
        ("origin_policy_violations", "freeze:origin-policy-violation"),
        ("cross_backend_runtime_fallbacks", "freeze:cross-backend-runtime-fallback"),
    ],
)
def test_zero_tolerance_signal_freezes_even_with_hold_conditions(signal: str, reason: str) -> None:
    evidence = _evidence()
    evidence["freeze_signals"][signal] = 1
    evidence["window"] = {
        "start_at": "2026-08-01T00:00:00Z",
        "end_at": "2026-08-02T00:00:00Z",
        "duration_seconds": 86400,
    }

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "freeze"
    assert reason in _reason_codes(result)
    assert "hold:window-duration" in _reason_codes(result)


@pytest.mark.parametrize(
    ("field", "reason"),
    [
        ("request_amplification_ratio", "freeze:request-amplification"),
        ("antibot_regression_ratio", "freeze:antibot-regression"),
    ],
)
def test_ratio_above_boundary_freezes(field: str, reason: str) -> None:
    evidence = _evidence()
    evidence["observations"][0][field] = 1.0500001

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "freeze"
    assert reason in _reason_codes(result)


def test_freshness_error_budget_above_boundary_freezes() -> None:
    evidence = _evidence()
    evidence["observations"][3]["freshness"]["error_budget_burn"] = 1.000001

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "freeze"
    assert _reason_codes(result) == {"freeze:freshness-error-budget"}


def test_browser_backends_are_separate_and_cannot_mask_each_other() -> None:
    evidence = _evidence()
    lightpanda = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "monitor_lightpanda_navigation_evaluation"
    )
    chromium = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "monitor_chromium_navigation_evaluation"
    )
    lightpanda["request_amplification_ratio"] = 1.06
    chromium["request_amplification_ratio"] = 1.0

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "freeze"
    reason = next(
        item for item in result["reasons"] if item["code"] == "freeze:request-amplification"
    )
    assert reason["affected_classes"] == ["monitor_lightpanda_navigation_evaluation"]


def test_aggregate_backend_and_runtime_fallback_masking_are_forbidden() -> None:
    aggregate = _evidence()
    aggregate["observations"][1]["browser_backend"] = "mixed"
    with pytest.raises(GateModelError, match="browser_backend"):
        evaluate_promotion(_policy(), aggregate)

    fallback = _evidence()
    fallback["freeze_signals"]["cross_backend_runtime_fallbacks"] = 1
    result = evaluate_promotion(_policy(), fallback)
    assert result["decision"] == "freeze"
    assert "freeze:cross-backend-runtime-fallback" in _reason_codes(result)


def test_backend_resource_saturation_freezes_only_the_affected_service_class() -> None:
    evidence = _evidence()
    chromium = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "detail_chromium_identity_transport"
    )
    chromium["resource_saturation_events"] = 1

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "freeze"
    reason = next(
        item for item in result["reasons"] if item["code"] == "freeze:backend-resource-saturation"
    )
    assert reason["affected_classes"] == ["detail_chromium_identity_transport"]


def test_proven_zero_assignment_does_not_force_chromium_use_or_retirement() -> None:
    evidence = _evidence()
    chromium = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "detail_chromium_identity_transport"
    )
    _set_unassigned(
        chromium,
        eligible_demand_present=False,
        zero_demand_proven=True,
        zero_assignment_proven=True,
    )

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "promote"
    assert result["reasons"] == []


def test_zero_assignment_must_be_proven_when_routed_demand_is_absent() -> None:
    evidence = _evidence()
    chromium = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "detail_chromium_identity_transport"
    )
    _set_unassigned(
        chromium,
        eligible_demand_present=False,
        zero_demand_proven=True,
        zero_assignment_proven=False,
    )

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "hold"
    assert "hold:zero-assignment-unproven" in _reason_codes(result)


def test_positive_demand_may_be_unrouted_without_implying_backend_or_retirement() -> None:
    evidence = _evidence()
    lightpanda = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "monitor_lightpanda_identity_transport"
    )
    _set_unassigned(
        lightpanda,
        eligible_demand_present=True,
        zero_demand_proven=False,
        zero_assignment_proven=True,
    )

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "promote"
    assert result["reasons"] == []
    assert "retire" not in json.dumps(result)


def test_zero_demand_and_zero_assignment_are_independent_fail_closed_proofs() -> None:
    evidence = _evidence()
    chromium = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "monitor_chromium_interaction_capture"
    )
    _set_unassigned(
        chromium,
        eligible_demand_present=False,
        zero_demand_proven=False,
        zero_assignment_proven=True,
    )

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "hold"
    assert _reason_codes(result) == {"hold:zero-demand-unproven"}


@pytest.mark.parametrize(
    ("eligible", "assigned", "zero_demand", "zero_assignment", "message"),
    [
        (False, True, False, False, "assignment without eligible demand"),
        (True, True, True, False, "demand and zero-demand proof"),
        (True, True, False, True, "assignment and zero-assignment proof"),
    ],
)
def test_contradictory_demand_and_assignment_proofs_are_rejected(
    eligible: bool,
    assigned: bool,
    zero_demand: bool,
    zero_assignment: bool,
    message: str,
) -> None:
    evidence = _evidence()
    capacity = evidence["observations"][1]["capacity"]
    capacity.update(
        {
            "eligible_demand_present": eligible,
            "routed_assignment_present": assigned,
            "zero_demand_proven": zero_demand,
            "zero_assignment_proven": zero_assignment,
        }
    )

    with pytest.raises(GateModelError, match=message):
        evaluate_promotion(_policy(), evidence)


def test_avoidable_idle_with_backlog_and_insufficient_headroom_hold() -> None:
    evidence = _evidence()
    lightpanda = next(
        item
        for item in evidence["observations"]
        if item["class_id"] == "monitor_lightpanda_navigation_evaluation"
    )
    lightpanda["capacity"]["avoidable_idle_seconds_with_eligible_backlog"] = 0.001
    lightpanda["capacity"]["utilization_p95_ratio"] = 0.851
    lightpanda["capacity"]["headroom_p05_ratio"] = 0.149

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "hold"
    assert {
        "hold:avoidable-idle-with-eligible-backlog",
        "hold:backend-capacity-headroom",
    }.issubset(_reason_codes(result))


def test_low_utilization_is_not_a_failure_without_avoidable_idle() -> None:
    evidence = _evidence()
    for observation in evidence["observations"]:
        observation["capacity"]["utilization_p95_ratio"] = 0.01
        observation["capacity"]["headroom_p05_ratio"] = 0.99

    result = evaluate_promotion(_policy(), evidence)

    assert result["decision"] == "promote"


def test_nonfinite_number_and_window_mismatch_are_rejected() -> None:
    nonfinite = _evidence()
    nonfinite["observations"][0]["request_amplification_ratio"] = float("nan")
    with pytest.raises(GateModelError, match="must be finite"):
        evaluate_promotion(_policy(), nonfinite)

    mismatch = _evidence()
    mismatch["window"]["duration_seconds"] -= 1
    with pytest.raises(GateModelError, match="does not match its boundaries"):
        evaluate_promotion(_policy(), mismatch)


def test_unknown_label_and_unsorted_histogram_are_rejected() -> None:
    unknown_label = _evidence()
    unknown_label["observations"][0]["provider_family"] = "board-host.example"
    with pytest.raises(GateModelError, match="provider_family"):
        evaluate_promotion(_policy(), unknown_label)

    unsorted_policy = _policy()
    unsorted_policy["histogram_buckets_seconds"]["due_to_claim"] = [1, 5, 4]
    with pytest.raises(GateModelError, match="strictly increasing"):
        evaluate_promotion(unsorted_policy, _evidence())


def test_cli_validates_json_and_uses_decision_exit_codes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    policy_path = tmp_path / "policy.json"
    evidence_path = tmp_path / "evidence.json"
    policy_path.write_text(json.dumps(_policy()))
    evidence_path.write_text(json.dumps(_evidence()))
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "migration-gates",
            "evaluate",
            "--policy",
            str(policy_path),
            "--evidence",
            str(evidence_path),
        ],
    )

    assert main() == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "promote"

    evidence_path.write_text(evidence_path.read_text().replace("1.05", "NaN", 1))
    assert main() == 2
    assert "cannot load" in capsys.readouterr().err


def test_clean_wheel_cli_uses_packaged_policy_and_schemas(tmp_path: Path) -> None:
    dist_dir = tmp_path / "dist"
    subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(dist_dir)],
        cwd=CRAWLER_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    wheel_path = next(dist_dir.glob("*.whl"))
    site_packages = tmp_path / "site-packages"
    with zipfile.ZipFile(wheel_path) as wheel:
        wheel.extractall(site_packages)

    evidence_path = tmp_path / "evidence.json"
    evidence_path.write_text(json.dumps(_evidence()))
    env = os.environ.copy()
    env["PYTHONPATH"] = str(site_packages)
    env["PYTHONNOUSERSITE"] = "1"

    imported = subprocess.run(
        [sys.executable, "-c", "import src.migration_gates as m; print(m.__file__)"],
        cwd=tmp_path,
        env=env,
        check=True,
        capture_output=True,
        text=True,
    )
    assert str(site_packages) in imported.stdout

    evaluated = subprocess.run(
        [
            sys.executable,
            "-m",
            "src.migration_gates",
            "evaluate",
            "--evidence",
            str(evidence_path),
        ],
        cwd=tmp_path,
        env=env,
        check=False,
        capture_output=True,
        text=True,
    )
    assert evaluated.returncode == 0, evaluated.stderr
    assert json.loads(evaluated.stdout)["decision"] == "promote"

    for index, unsafe_id in enumerate(UNSAFE_IDENTIFIERS):
        evidence = _evidence()
        evidence["evidence_id"] = unsafe_id
        unsafe_path = tmp_path / f"unsafe-evidence-{index}.json"
        unsafe_path.write_text(json.dumps(evidence))
        rejected = subprocess.run(
            [
                sys.executable,
                "-m",
                "src.migration_gates",
                "evaluate",
                "--evidence",
                str(unsafe_path),
            ],
            cwd=tmp_path,
            env=env,
            check=False,
            capture_output=True,
            text=True,
        )
        assert rejected.returncode == 2
        assert rejected.stdout == ""
        assert unsafe_id not in rejected.stderr
        assert all(marker not in rejected.stderr for marker in UNSAFE_MARKERS)
        assert len(rejected.stderr) <= MAX_PUBLIC_ERROR_LENGTH
        assert "validator=" in rejected.stderr
