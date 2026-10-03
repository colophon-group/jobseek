#!/usr/bin/env python3
"""Dispatch one fixed aggregate collector; no provider credentials or model."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import io
import json
import os
import re
import selectors
import subprocess
import tempfile
import time
import zipfile
from datetime import datetime
from pathlib import Path

REPOSITORY = "colophon-group/jobseek"
OWNER = "viktor-shcherb"
WORKFLOW = "company-selection-observation.yml"
WORKFLOW_PATH = ".github/workflows/" + WORKFLOW
API = "repos/" + REPOSITORY
QUARTER = 900
MAX_ZIP = 4 * 1024 * 1024
MAX_BODY = 16 * 1024 * 1024
SHA = re.compile(r"[0-9a-f]{40}")
OPERATIONS = {
    "create",
    "handoff",
    "update",
    "copy",
    "copy_shared",
    "add",
    "remove",
    "clear",
    "star",
}
OUTCOMES = {
    "success",
    "invalid_input",
    "lookup_miss",
    "lookup_unavailable",
    "identity_conflict",
    "not_found_or_forbidden",
    "limit",
    "rate_limited",
    "unauthenticated",
    "database_foreign_key",
    "database_error",
    "unexpected_failure",
    "rejected",
}
REASONS = {
    "invalid_request_timestamp",
    "missing_request_identity",
    "duplicate_request_rows",
    "unsupported_log_schema",
    "possible_provider_request_line_cap",
    "truncated_request_message",
    "unparsed_candidate_message",
    "unrecognized_telemetry_contract",
    "possible_provider_request_byte_cap",
    "matching_request_without_parseable_event",
    "malformed_request_record",
    "query_budget_exhausted",
    "query_timeout",
    "query_transport_budget_exhausted",
    "unexpected_cli_row_limit",
    "cli_query_failed",
    "saturated_minimum_window",
    "saturated_query_budget",
    "retention_boundary",
    "replay_count_regression",
}


class Rejected(Exception):
    """Messages are fixed policy reasons, never subprocess or API payloads."""


def require(condition: bool, reason: str = "invalid_remote_evidence") -> None:
    if not condition:
        raise Rejected(reason)


def instant(value: str) -> float:
    require(
        isinstance(value, str)
        and bool(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{3})?Z", value))
    )
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        raise Rejected("invalid_remote_evidence") from None


def natural(value: object) -> bool:
    return type(value) is int and 0 <= value <= 9007199254740991


class Github:
    def __init__(self, binary: str = "/usr/bin/gh", home: str = "/home/codex-runner"):
        self.binary = binary
        self.env = {
            "HOME": home,
            "PATH": "/usr/local/bin:/usr/bin:/bin",
            "GH_HOST": "github.com",
            "GH_PROMPT_DISABLED": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }
        self.deadline = time.monotonic() + 150

    def api(self, path: str, post: dict | None = None, *, binary: bool = False):
        args = [self.binary, "api", "--hostname", "github.com", path]
        data = None
        if post is not None:
            args += ["--method", "POST", "--input", "-"]
            data = json.dumps(post).encode()
        timeout = min(20, self.deadline - time.monotonic())
        require(timeout > 0, "github_timeout")
        limit = MAX_ZIP if binary else 512 * 1024
        with subprocess.Popen(
            args,
            stdin=subprocess.PIPE if data else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=self.env,
        ) as process:
            try:
                if data:
                    process.stdin.write(data)
                    process.stdin.close()
                output = bytearray()
                with selectors.DefaultSelector() as selector:
                    selector.register(process.stdout, selectors.EVENT_READ)
                    end = time.monotonic() + timeout
                    while selector.get_map():
                        left = end - time.monotonic()
                        require(left > 0, "github_timeout")
                        for key, _ in selector.select(left):
                            chunk = os.read(key.fd, 65536)
                            if not chunk:
                                selector.unregister(key.fileobj)
                                continue
                            output.extend(chunk)
                            require(len(output) <= limit, "github_output_limit")
                require(
                    process.wait(timeout=max(0.01, end - time.monotonic())) == 0,
                    "github_request_failed",
                )
            except BaseException:
                process.kill()
                process.wait()
                raise
        if binary:
            return bytes(output)
        if not output:
            return None
        try:
            return json.loads(output)
        except ValueError:
            raise Rejected("invalid_remote_evidence") from None


def identity(
    github: Github, expected_source: str | None, now: float
) -> tuple[str, str]:
    require(github.api("user").get("login") == OWNER, "wrong_github_owner")
    repo = github.api(API)
    require(
        repo.get("full_name") == REPOSITORY
        and repo.get("default_branch") == "main"
        and repo.get("archived") is False
        and repo.get("disabled") is False,
        "wrong_repository",
    )
    workflow = github.api(API + "/actions/workflows/" + WORKFLOW)
    require(
        workflow.get("path") == WORKFLOW_PATH
        and workflow.get("state") == "active"
        and natural(workflow.get("id")),
        "wrong_workflow",
    )
    source = github.api(API + "/branches/main")["commit"]["sha"]
    require(
        isinstance(source, str) and bool(SHA.fullmatch(source)), "invalid_main_identity"
    )
    require(expected_source is None or source == expected_source, "main_changed")
    start = github.api(API + "/actions/variables/COMPANY_REFERENCE_OBSERVATION_START")[
        "value"
    ]
    require(instant(start) <= now, "invalid_observation_start")
    return source, start


def runs(github: Github, now: float) -> list[dict]:
    response = github.api(API + "/actions/workflows/" + WORKFLOW + "/runs?per_page=10")
    values = response.get("workflow_runs")
    require(isinstance(values, list) and len(values) <= 10)
    for run in values:
        require(
            natural(run.get("id"))
            and run["id"] > 0
            and natural(run.get("run_attempt"))
            and run["run_attempt"] > 0
            and run.get("head_branch") == "main"
            and run.get("path") == WORKFLOW_PATH
            and isinstance(run.get("head_sha"), str)
            and bool(SHA.fullmatch(run["head_sha"]))
            and run.get("event") in {"schedule", "workflow_dispatch"}
        )
        require(
            instant(run["created_at"]) <= now + 60
            and instant(run["updated_at"]) <= now + 60
        )
        if run["event"] == "workflow_dispatch":
            require(
                run.get("actor", {}).get("login") == OWNER
                and run.get("triggering_actor", {}).get("login") == OWNER,
                "wrong_run_owner",
            )
    return values


def checkpoint(github: Github, run: dict, start: str, now: float) -> dict | None:
    artifacts = github.api(API + f"/actions/runs/{run['id']}/artifacts?per_page=20")
    values = artifacts.get("artifacts")
    require(isinstance(values, list) and len(values) <= 20)
    expected = f"company-selection-checkpoints-{run['id']}-{run['run_attempt']}"
    selected = [row for row in values if row.get("name") == expected]
    if not selected:
        return None
    require(len(selected) == 1)
    artifact = selected[0]
    require(
        natural(artifact.get("id"))
        and artifact["id"] > 0
        and artifact.get("expired") is False
        and natural(artifact.get("size_in_bytes"))
        and 0 < artifact["size_in_bytes"] <= MAX_ZIP
        and isinstance(artifact.get("digest"), str)
        and bool(re.fullmatch(r"sha256:[a-f0-9]{64}", artifact["digest"]))
    )
    archive = github.api(API + f"/actions/artifacts/{artifact['id']}/zip", binary=True)
    require(
        "sha256:" + hashlib.sha256(archive).hexdigest() == artifact["digest"],
        "artifact_digest_mismatch",
    )
    with zipfile.ZipFile(io.BytesIO(archive)) as package:
        files = package.infolist()
        require(
            len(files) == 1
            and files[0].filename == "checkpoint.json"
            and not files[0].is_dir()
            and files[0].file_size <= MAX_BODY
            and not files[0].flag_bits & 1
            and (files[0].external_attr >> 16) & 0o170000 != 0o120000,
            "invalid_checkpoint_archive",
        )
        with package.open(files[0]) as member:
            raw = member.read(MAX_BODY + 1)
        require(len(raw) <= MAX_BODY)
    envelope = json.loads(raw)
    require(set(envelope) == {"body", "sha256"})
    body = envelope["body"]
    digest = hashlib.sha256(
        json.dumps(body, ensure_ascii=False, separators=(",", ":")).encode()
    ).hexdigest()
    require(digest == envelope["sha256"], "checkpoint_digest_mismatch")
    require(
        set(body)
        == {
            "schemaVersion",
            "sourceRevision",
            "runId",
            "runAttempt",
            "observationStart",
            "generatedAt",
            "cliVersion",
            "checkpoints",
        }
        and natural(body["schemaVersion"])
        and body["schemaVersion"] == 1
        and body["sourceRevision"] == run["head_sha"]
        and body["runId"] == str(run["id"])
        and natural(body["runAttempt"])
        and body["runAttempt"] == run["run_attempt"]
        and body["observationStart"] == start
        and body["cliVersion"] == "62.1.0",
        "checkpoint_identity_mismatch",
    )
    generated = instant(body["generatedAt"])
    require(generated <= now + 60 and generated >= now - 8 * 86400)
    require(isinstance(body["checkpoints"], list) and len(body["checkpoints"]) <= 680)
    seen = set()
    for row in body["checkpoints"]:
        require(
            set(row)
            == {
                "from",
                "until",
                "queriedFrom",
                "collectedAt",
                "queryCoverage",
                "counts",
                "reasons",
            }
        )
        begin, until = instant(row["from"]), instant(row["until"])
        require(
            begin % QUARTER == 0
            and until == begin + QUARTER
            and begin not in seen
            and begin >= instant(start) - QUARTER
            and instant(row["queriedFrom"]) == max(begin, instant(start))
            and until <= generated - 300
            and until <= instant(row["collectedAt"]) <= generated
        )
        require(
            row["queryCoverage"] in {"exhausted", "partial"}
            and isinstance(row["reasons"], list)
            and len(row["reasons"]) <= len(REASONS)
            and all(
                isinstance(reason, str) and reason in REASONS
                for reason in row["reasons"]
            )
            and len(set(row["reasons"])) == len(row["reasons"])
            and (row["queryCoverage"] == "exhausted") == (len(row["reasons"]) == 0)
        )
        require(isinstance(row["counts"], list) and len(row["counts"]) <= 117)
        pairs = set()
        for count in row["counts"]:
            require(
                set(count) == {"operation", "outcome", "count"}
                and count["operation"] in OPERATIONS
                and count["outcome"] in OUTCOMES
                and natural(count["count"])
                and count["count"] > 0
                and (count["operation"], count["outcome"]) not in pairs
            )
            pairs.add((count["operation"], count["outcome"]))
        seen.add(begin)
    return body


def health(body: dict | None, start: str, now: float) -> dict:
    until = (int(now - 300) // QUARTER) * QUARTER
    first = max(instant(start), until - 7 * 86400)
    lookup = {instant(row["from"]): row for row in body["checkpoints"]} if body else {}
    missing = partial = expired = 0
    positive = False
    for begin in range(int(first) // QUARTER * QUARTER, until, QUARTER):
        row = lookup.get(begin)
        if row is None:
            missing += 1
            expired += max(begin, instant(start)) < now - 3600 + 120
        else:
            partial += row["queryCoverage"] != "exhausted"
            positive |= bool(row["counts"])
    return {
        "observationStart": start,
        "requiredClosedUntil": until,
        "missingWindows": missing,
        "partialWindows": partial,
        "expiredMissingWindows": expired,
        "positiveTelemetryObserved": positive,
        "entireDeclaredPeriodRetained": first <= instant(start),
        "providerCaptureGuarantee": "unknown",
        "rolloutAcceptance": "blocked_incomplete_history"
        if missing or partial or first > instant(start)
        else "collector_report_required",
        "periodCoverageBlocked": bool(missing or partial or first > instant(start)),
        "authenticatedCheckpoint": body is not None,
        "collectorRunId": body["runId"] if body else None,
        "checkpointAgeSeconds": max(0, int(now - instant(body["generatedAt"])))
        if body
        else None,
        "currentWindowCollected": until <= instant(start)
        or lookup.get(until - QUARTER, {}).get("queryCoverage") == "exhausted",
    }


def newest_checkpoint(
    github: Github, values: list[dict], start: str, now: float
) -> dict | None:
    for run in values[:3]:
        if run.get("status") == "completed":
            body = checkpoint(github, run, start, now)
            if body is not None:
                return body
    return None


def execute(
    github: Github,
    state: Path,
    *,
    check_only: bool = False,
    activation_ready: bool = False,
    expected_source: str | None = None,
) -> tuple[int, dict]:
    now = time.time()
    source, start = identity(github, expected_source, now)
    values = runs(github, now)
    body = newest_checkpoint(github, values, start, now)
    report = health(body, start, now)
    require(
        not (check_only or activation_ready) or body is not None,
        "no_authenticated_checkpoint",
    )
    if activation_ready:
        # This certifies only that future collection can be enabled. Historical
        # gaps stay explicit, and ordinary checks/service status stay degraded.
        source_matches = body["sourceRevision"] == source
        ready = bool(
            source_matches
            and report["currentWindowCollected"]
            and report["requiredClosedUntil"] > instant(start)
            and report["checkpointAgeSeconds"] <= 1200
        )
        return (0 if ready else 2), {
            "dispatch": "activation_readiness_only",
            "activationReady": ready,
            "activationSourceMatches": source_matches,
            "collectionHealth": "degraded"
            if report["periodCoverageBlocked"]
            else "covered",
            **report,
        }
    if report["currentWindowCollected"]:
        code = 0 if not report["missingWindows"] and not report["partialWindows"] else 2
        return code, {
            "dispatch": "not_needed",
            "collectionHealth": "covered" if code == 0 else "degraded",
            **report,
        }
    if check_only:
        return 2, {"dispatch": "check_only", "collectionHealth": "degraded", **report}
    pending = [
        run
        for run in values
        if run.get("status") != "completed" and instant(run["created_at"]) >= now - 600
    ]
    slot = int(now) // QUARTER
    saved = state / "dispatch.json"
    prior = json.loads(saved.read_text()) if saved.exists() else {}
    if not pending and prior.get("slot") != slot:
        # Record intent before POST so an ambiguous transport failure cannot
        # cause a second dispatch in the same slot. Remote evidence remains
        # authoritative; a local receipt never certifies collected coverage.
        require(
            github.api(API + "/branches/main")["commit"]["sha"] == source,
            "main_changed",
        )
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=state, prefix="dispatch-", delete=False
        ) as output:
            temporary = Path(output.name)
            json.dump({"slot": slot, "sourceRevision": source}, output)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, saved)
        github.api(
            API + "/actions/workflows/" + WORKFLOW + "/dispatches", {"ref": "main"}
        )
    deadline = min(github.deadline, time.monotonic() + 90)
    while time.monotonic() < deadline:
        time.sleep(5)
        now = time.time()
        values = runs(github, now)
        body = newest_checkpoint(github, values, start, now)
        report = health(body, start, now)
        if report["currentWindowCollected"]:
            code = (
                0
                if not report["missingWindows"] and not report["partialWindows"]
                else 2
            )
            return code, {
                "dispatch": "collection_verified",
                "collectionHealth": "covered" if code == 0 else "degraded",
                **report,
            }
    return 2, {
        "dispatch": "pending_or_unconfirmed",
        "collectionHealth": "degraded",
        **report,
    }


def main(argv: list[str] | None = None, *, github: Github | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path("/srv/jobseek-codex/state/company-selection-observation"),
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--activation-ready", action="store_true")
    parser.add_argument("--expected-source")
    args = parser.parse_args(argv)
    try:
        require(
            args.expected_source is None or bool(SHA.fullmatch(args.expected_source)),
            "invalid_expected_source",
        )
        require(
            not args.activation_ready or args.expected_source is not None,
            "activation_requires_exact_source",
        )
        os.umask(0o077)
        require(not args.state_dir.is_symlink(), "unsafe_state")
        args.state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
        with (args.state_dir / "dispatch.lock").open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Rejected("dispatcher_busy") from None
            code, report = execute(
                github or Github(),
                args.state_dir,
                check_only=args.check,
                activation_ready=args.activation_ready,
                expected_source=args.expected_source,
            )
        print(json.dumps(report, separators=(",", ":"), sort_keys=True))
        return code
    except Rejected as exc:
        print(
            json.dumps(
                {
                    "dispatch": "failed",
                    "collectionHealth": "unknown",
                    "reason": str(exc),
                }
            )
        )
    except Exception:  # noqa: BLE001 — never export unexpected exception payloads
        print(
            json.dumps(
                {
                    "dispatch": "failed",
                    "collectionHealth": "unknown",
                    "reason": "dispatcher_failed_no_raw_diagnostics",
                }
            )
        )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
