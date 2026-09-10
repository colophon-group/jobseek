#!/usr/bin/env python3
"""Failure-retaining executor for the frozen Stage B2 density protocol.

Only frozen identifiers, immutable image digests, and aggregate cgroup
counters cross the evidence boundary. Docker transcripts, container names,
host PIDs, cgroup paths, URLs, and exception text are never serialized.
"""

from __future__ import annotations

import argparse
import copy
import re
import secrets
from pathlib import Path
from typing import Any, Callable, Protocol

try:
    from . import controller, evidence_protocol
except ImportError:  # Direct script execution.
    import controller  # type: ignore[no-redef]
    import evidence_protocol  # type: ignore[no-redef]


IMAGE_KEYS = ("go", "python", "fixture")
PROVENANCE_LABEL = "org.jobseek.density.source-commit"
EXECUTOR_STATES = frozenset({"initialized", "running", "complete", "aborted"})
ABORT_FAILURE_IDS = evidence_protocol.PROTOCOL_FAILURE_IDS | {"containment"}


class GuardFailure(RuntimeError):
    """A bounded executor guard failure; no external text is retained."""

    def __init__(self, failure_id: str) -> None:
        self.failure_id = (
            failure_id
            if failure_id in evidence_protocol.FAILURE_IDS
            else "infrastructure"
        )
        super().__init__(self.failure_id)


class IdentityGuard(Protocol):
    def resolve(self) -> dict[str, str]: ...

    def verify(self, identities: dict[str, str]) -> None: ...


class HealthGuard(Protocol):
    def capture(self) -> None: ...

    def verify(self) -> None: ...


class DockerImageGuard:
    """Resolve image tags once and reject any later tag mutation."""

    def __init__(
        self,
        docker: controller.Docker,
        references: dict[str, str],
        source_commit: str,
    ) -> None:
        if (
            set(references) != set(IMAGE_KEYS)
            or re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
        ):
            raise GuardFailure("image")
        self.docker = docker
        self.references = dict(references)
        self.source_commit = source_commit

    def _identity(self, reference: str) -> str:
        try:
            data = self.docker.json(["image", "inspect", reference])
            if not isinstance(data, list) or len(data) != 1:
                raise KeyError
            item = data[0]
            identity = item["Id"]
            controller._digest(identity, prefixed=True)
            labels = item["Config"]["Labels"]
            if (
                not isinstance(labels, dict)
                or labels.get(PROVENANCE_LABEL) != self.source_commit
            ):
                raise KeyError
            return identity
        except (controller.SmokeFailure, KeyError, TypeError, IndexError):
            raise GuardFailure("image") from None

    def resolve(self) -> dict[str, str]:
        return {key: self._identity(self.references[key]) for key in IMAGE_KEYS}

    def verify(self, identities: dict[str, str]) -> None:
        if set(identities) != set(IMAGE_KEYS):
            raise GuardFailure("containment")
        for key in IMAGE_KEYS:
            if self._identity(self.references[key]) != identities[key]:
                raise GuardFailure("containment")


class ProtectedContainerGuard:
    """Require optional protected containers to stay healthy and unchanged."""

    def __init__(self, docker: controller.Docker, names: tuple[str, ...]) -> None:
        if len(set(names)) != len(names) or any(
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", name) is None
            for name in names
        ):
            raise GuardFailure("containment")
        self.docker = docker
        self.names = names
        self._baseline: tuple[tuple[str, str, str, int], ...] | None = None

    def _snapshot(self) -> tuple[tuple[str, str, str, int], ...]:
        values: list[tuple[str, str, str, int]] = []
        for name in self.names:
            try:
                data = self.docker.json(["inspect", name])
                if not isinstance(data, list) or len(data) != 1:
                    raise KeyError
                item = data[0]
                state = item["State"]
                health = state.get("Health")
                if (
                    state.get("Status") != "running"
                    or state.get("Running") is not True
                    or state.get("Restarting") is not False
                    or state.get("Dead") is not False
                    or (health is not None and health.get("Status") != "healthy")
                ):
                    raise KeyError
                identity = item["Id"]
                image = item["Image"]
                started = state["StartedAt"]
                restarts = item["RestartCount"]
                if (
                    re.fullmatch(r"[0-9a-f]{64}", identity) is None
                    or re.fullmatch(r"sha256:[0-9a-f]{64}", image) is None
                    or not isinstance(started, str)
                    or not started
                    or isinstance(restarts, bool)
                    or not isinstance(restarts, int)
                    or restarts < 0
                ):
                    raise KeyError
                values.append((identity, image, started, restarts))
            except (
                controller.SmokeFailure,
                KeyError,
                TypeError,
                AttributeError,
            ):
                raise GuardFailure("containment") from None
        return tuple(values)

    def capture(self) -> None:
        self._baseline = self._snapshot()

    def verify(self) -> None:
        if self._baseline is None or self._snapshot() != self._baseline:
            raise GuardFailure("containment")


def _not_run_record(
    pair: dict[str, Any], arm_index: int, implementation: str
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pair_id": pair["id"],
        "arm_index": arm_index,
        "implementation": implementation,
        "concurrency": pair["concurrency"],
        "outcome": "failure",
        "failure_id": evidence_protocol.NOT_RUN_FAILURE_ID,
        "elapsed_ns": None,
        "resources": None,
    }


def _blank_records(schedule: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        _not_run_record(pair, arm_index, implementation)
        for pair in schedule["pairs"]
        for arm_index, implementation in enumerate(pair["order"])
    ]


def _safe_resources(
    raw: dict[str, Any] | None,
    envelope: dict[str, Any],
    *,
    final_sample: bool,
) -> dict[str, Any] | None:
    if not isinstance(raw, dict):
        return None
    keys = evidence_protocol._RESOURCE_KEYS - {"final_sample", "observation"}
    if not keys <= set(raw):
        return None
    candidate = {
        "final_sample": final_sample,
        "observation": "final" if final_sample else "sampled_not_final",
        **{key: copy.deepcopy(raw[key]) for key in keys},
    }
    try:
        evidence_protocol._validate_resources(candidate, envelope)
    except evidence_protocol.ProtocolError:
        return None
    return candidate


def _failure_record(
    slot: dict[str, Any],
    failure_id: str,
    resources: dict[str, Any] | None,
) -> dict[str, Any]:
    record = dict(slot)
    record["failure_id"] = (
        failure_id
        if failure_id in evidence_protocol.FAILURE_IDS
        else "infrastructure"
    )
    record["resources"] = resources
    return record


class EvidenceRunner:
    """Execute and checkpoint every frozen schedule slot in exact order."""

    def __init__(
        self,
        *,
        source_commit: str,
        schedule: dict[str, Any],
        schedule_sha256: str,
        workload: dict[str, Any],
        workload_sha256: str,
        timeout: float,
        output: Path,
        identity_guard: IdentityGuard,
        health_guard: HealthGuard,
        docker: controller.Docker | None = None,
        arm_runner: Callable[
            [dict[str, Any], int, str, dict[str, str]], dict[str, Any]
        ]
        | None = None,
        checkpoint_writer: Callable[[dict[str, Any]], None] | None = None,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        evidence_protocol.validate_schedule(schedule)
        if (
            re.fullmatch(r"[0-9a-f]{40}", source_commit) is None
            or schedule_sha256
            != evidence_protocol.EXPECTED_SCHEDULE_SHA256
            or workload_sha256 != schedule["workload_sha256"]
            or timeout <= 0
        ):
            raise GuardFailure("infrastructure")
        self.source_commit = source_commit
        self.schedule = schedule
        self.schedule_sha256 = schedule_sha256
        self.workload = workload
        self.workload_sha256 = workload_sha256
        self.timeout = timeout
        self.output = output
        self.identity_guard = identity_guard
        self.health_guard = health_guard
        self.docker = docker or controller.Docker()
        self.arm_runner = arm_runner or self._run_arm
        self.checkpoint_writer = checkpoint_writer or (
            lambda report: controller.write_report(self.output, report)
        )
        self.token_factory = token_factory or (lambda: secrets.token_hex(8))

    def _run_arm(
        self,
        pair: dict[str, Any],
        _arm_index: int,
        implementation: str,
        identities: dict[str, str],
    ) -> dict[str, Any]:
        smoke = controller.SmokeController(
            self.docker,
            self.source_commit,
            pair["concurrency"],
            self.timeout,
            self.workload,
            self.workload_sha256,
            run_id=self.token_factory(),
        )
        return smoke.run_arm(
            implementation,
            identities[implementation],
            identities["fixture"],
            measured_pids=self.schedule["resource_envelope"]["pids_limit"],
            expected_image_identity=identities[implementation],
            expected_fixture_identity=identities["fixture"],
            retain_failure_resources=True,
        )

    def _report(
        self,
        state: str,
        identities: dict[str, str | None],
        records: list[dict[str, Any]],
        executor_failure_id: str | None,
    ) -> dict[str, Any]:
        if (
            state not in EXECUTOR_STATES
            or set(identities) != set(IMAGE_KEYS)
            or any(
                value is not None
                and re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                for value in identities.values()
            )
            or (
                executor_failure_id is not None
                and executor_failure_id not in evidence_protocol.FAILURE_IDS
            )
        ):
            raise AssertionError("executor report is not closed")
        report = {
            "schema_version": 1,
            "protocol_id": self.schedule["protocol_id"],
            "schedule_sha256": self.schedule_sha256,
            "source_commit": self.source_commit,
            "state": state,
            "executor_failure_id": executor_failure_id,
            "image_identities": identities,
            "completed_arms": sum(
                record["failure_id"] != evidence_protocol.NOT_RUN_FAILURE_ID
                for record in records
            ),
            "records": records,
            "summary": evidence_protocol.summarize_evidence(
                self.schedule, records
            ),
        }
        return report

    def _checkpoint(
        self,
        state: str,
        identities: dict[str, str | None],
        records: list[dict[str, Any]],
        executor_failure_id: str | None,
    ) -> dict[str, Any]:
        report = self._report(
            state, identities, records, executor_failure_id
        )
        self.checkpoint_writer(report)
        return report

    def run(self) -> dict[str, Any]:
        records = _blank_records(self.schedule)
        identities: dict[str, str | None] = {key: None for key in IMAGE_KEYS}
        try:
            resolved = self.identity_guard.resolve()
            if (
                set(resolved) != set(IMAGE_KEYS)
                or any(
                    re.fullmatch(r"sha256:[0-9a-f]{64}", value) is None
                    for value in resolved.values()
                )
            ):
                raise GuardFailure("image")
            identities = dict(resolved)
            self.identity_guard.verify(resolved)
            self.health_guard.capture()
            self.health_guard.verify()
        except GuardFailure as error:
            return self._checkpoint(
                "aborted", identities, records, error.failure_id
            )
        except Exception:
            return self._checkpoint(
                "aborted", identities, records, "infrastructure"
            )

        self._checkpoint("initialized", identities, records, None)
        position = 0
        aborted: str | None = None
        envelope = self.schedule["resource_envelope"]
        for pair in self.schedule["pairs"]:
            for arm_index, implementation in enumerate(pair["order"]):
                slot = records[position]
                try:
                    self.identity_guard.verify(resolved)
                    self.health_guard.verify()
                    result = self.arm_runner(
                        pair, arm_index, implementation, resolved
                    )
                    resources = _safe_resources(
                        result.get("resources"), envelope, final_sample=True
                    )
                    measured = result.get("measured")
                    elapsed = (
                        measured.get("elapsed_ns")
                        if isinstance(measured, dict)
                        else None
                    )
                    if (
                        resources is None
                        or isinstance(elapsed, bool)
                        or not isinstance(elapsed, int)
                        or elapsed <= 0
                    ):
                        raise controller.SmokeFailure("transcript")
                    record = dict(slot)
                    record.update(
                        outcome="pass",
                        failure_id=None,
                        elapsed_ns=elapsed,
                        resources=resources,
                    )
                except controller.SmokeFailure as error:
                    resources = _safe_resources(
                        error.resources,
                        envelope,
                        final_sample=error.resources_final,
                    )
                    # SmokeController owns failure attribution because it knows
                    # whether the failing lifecycle belongs to the measured
                    # container, fixture, or controller. Reclassifying from the
                    # measured cgroup counters here could turn a fixture failure
                    # into apparent measured memory pressure.
                    record = _failure_record(
                        slot, error.failure_id, resources
                    )
                except GuardFailure as error:
                    record = _failure_record(slot, error.failure_id, None)
                except Exception:
                    record = _failure_record(slot, "infrastructure", None)

                records[position] = record
                try:
                    self.identity_guard.verify(resolved)
                    self.health_guard.verify()
                except (GuardFailure, Exception):
                    resources = record.get("resources")
                    records[position] = _failure_record(
                        slot, "containment", resources
                    )
                    aborted = "containment"

                if record["failure_id"] in ABORT_FAILURE_IDS:
                    aborted = record["failure_id"]
                position += 1
                if aborted is not None:
                    return self._checkpoint(
                        "aborted", identities, records, aborted
                    )
                state = (
                    "complete"
                    if position == self.schedule["expected_arm_records"]
                    else "running"
                )
                report = self._checkpoint(state, identities, records, None)
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--go-image", required=True)
    parser.add_argument("--python-image", required=True)
    parser.add_argument("--fixture-image", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--schedule", type=Path, default=Path(__file__).with_name("schedule.v1.json"))
    parser.add_argument("--timeout-seconds", type=float, default=180)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--docker-binary", default="docker")
    parser.add_argument("--protected-container", action="append", default=[])
    args = parser.parse_args(argv)

    try:
        schedule, schedule_sha = evidence_protocol.load_schedule(args.schedule)
        workload, workload_sha = controller.load_manifest(
            Path(__file__).resolve().parent
        )
        docker = controller.Docker(args.docker_binary)
        images = DockerImageGuard(
            docker,
            {
                "go": args.go_image,
                "python": args.python_image,
                "fixture": args.fixture_image,
            },
            args.source_commit,
        )
        health = ProtectedContainerGuard(
            docker, tuple(args.protected_container)
        )
        runner = EvidenceRunner(
            source_commit=args.source_commit,
            schedule=schedule,
            schedule_sha256=schedule_sha,
            workload=workload,
            workload_sha256=workload_sha,
            timeout=args.timeout_seconds,
            output=args.output,
            identity_guard=images,
            health_guard=health,
            docker=docker,
        )
        report = runner.run()
    except evidence_protocol.ProtocolError as error:
        controller.write_report(
            args.output,
            {
                "schema_version": 1,
                "state": "aborted",
                "failure_id": error.reason_id,
            },
        )
        return 1
    except (OSError, GuardFailure, controller.SmokeFailure) as error:
        failure_id = getattr(error, "failure_id", "infrastructure")
        controller.write_report(
            args.output,
            {
                "schema_version": 1,
                "state": "aborted",
                "failure_id": failure_id,
            },
        )
        return 1
    return 0 if report["state"] == "complete" else 1


if __name__ == "__main__":
    raise SystemExit(main())
