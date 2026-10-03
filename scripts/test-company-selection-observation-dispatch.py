#!/usr/bin/env python3
"""No network/auth: actual subprocess, artifact and activation regressions."""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import os
import shlex
import subprocess
import sys
import tempfile
import time
import unittest
import zipfile
from datetime import timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "dispatcher", ROOT / "scripts/codex-company-selection-observation-dispatch.py"
)
D = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(D)
SOURCE = "a" * 40
START = "2026-10-03T17:22:34.000Z"
NOW = D.instant("2026-10-03T18:08:00.000Z")


def iso(value):
    return (
        D.datetime.fromtimestamp(value, timezone.utc)
        .isoformat(timespec="milliseconds")
        .replace("+00:00", "Z")
    )


def sample_body():
    rows = []
    for begin in range(
        int(D.instant(START)) // 900 * 900, int(NOW - 300) // 900 * 900, 900
    ):
        rows.append(
            {
                "from": iso(begin),
                "until": iso(begin + 900),
                "queriedFrom": iso(max(begin, D.instant(START))),
                "collectedAt": "2026-10-03T18:07:00.000Z",
                "queryCoverage": "exhausted",
                "counts": [{"operation": "create", "outcome": "success", "count": 1}],
                "reasons": [],
            }
        )
    return {
        "schemaVersion": 1,
        "sourceRevision": SOURCE,
        "runId": "1",
        "runAttempt": 1,
        "observationStart": START,
        "generatedAt": "2026-10-03T18:07:00.000Z",
        "cliVersion": "62.1.0",
        "checkpoints": rows,
    }


class FakeGithub:
    def __init__(self):
        self.deadline = time.monotonic() - 1  # No sleeps in missing-run cases.
        self.calls = []
        self.user = D.OWNER
        self.source = SOURCE
        self.repo = {
            "full_name": D.REPOSITORY,
            "default_branch": "main",
            "archived": False,
            "disabled": False,
        }
        self.workflow = {"path": D.WORKFLOW_PATH, "state": "active", "id": 5}
        self.run = {
            "id": 1,
            "run_attempt": 1,
            "head_sha": SOURCE,
            "head_branch": "main",
            "path": D.WORKFLOW_PATH,
            "event": "workflow_dispatch",
            "actor": {"login": D.OWNER},
            "triggering_actor": {"login": D.OWNER},
            "status": "completed",
            "created_at": "2026-10-03T18:05:00Z",
            "updated_at": "2026-10-03T18:07:00Z",
        }
        self.body = sample_body()
        self.observation_start = START
        self.bad_archive = None
        self.bad_digest = False
        self.extra_member = False
        self.post_error = False
        self.artifact = {
            "name": "company-selection-checkpoints-1-1",
            "id": 9,
            "expired": False,
        }

    def zip(self):
        envelope = {
            "body": self.body,
            "sha256": hashlib.sha256(
                json.dumps(
                    self.body, ensure_ascii=False, separators=(",", ":")
                ).encode()
            ).hexdigest(),
        }
        archive = io.BytesIO()
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as z:
            z.writestr("checkpoint.json", json.dumps(envelope))
            if self.extra_member:
                z.writestr("../outside", "rejected")
        return archive.getvalue()

    def api(self, path, post=None, **_kwargs):
        self.calls.append((path, post))
        if post is not None:
            if self.post_error:
                raise D.Rejected("github_request_failed")
            return None
        if path == "user":
            return {"login": self.user}
        if path == D.API:
            return self.repo
        if path.endswith("/actions/workflows/" + D.WORKFLOW):
            return self.workflow
        if path.endswith("/branches/main"):
            return {"commit": {"sha": self.source}}
        if path.endswith("/actions/variables/COMPANY_REFERENCE_OBSERVATION_START"):
            return {"value": self.observation_start}
        if path.endswith("/runs?per_page=10"):
            return {"workflow_runs": [self.run]}
        if path.endswith("/artifacts?per_page=20"):
            archive = self.zip()
            return {
                "artifacts": [
                    {
                        **self.artifact,
                        "size_in_bytes": len(archive),
                        "digest": "sha256:"
                        + (
                            "0" * 64
                            if self.bad_digest
                            else hashlib.sha256(archive).hexdigest()
                        ),
                    }
                ]
            }
        if path.endswith("/zip"):
            return self.bad_archive or self.zip()
        raise AssertionError("unexpected API boundary")


def gapped_producer():
    fake = FakeGithub()
    fake.observation_start = "2026-10-03T15:22:34.000Z"
    fake.body["observationStart"] = fake.observation_start
    rows = []
    begins = range(
        int(D.instant(fake.observation_start)) // 900 * 900,
        int(NOW - 300) // 900 * 900,
        900,
    )
    for index, begin in enumerate(begins):
        if index < 5:
            continue
        rows.append(
            {
                "from": iso(begin),
                "until": iso(begin + 900),
                "queriedFrom": iso(max(begin, D.instant(fake.observation_start))),
                "collectedAt": fake.body["generatedAt"],
                "queryCoverage": "exhausted",
                "counts": [],
                "reasons": [],
            }
        )
    fake.body["checkpoints"] = rows
    return fake


class DispatchTests(unittest.TestCase):
    def invoke(self, fake, *args, directory=None):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(directory or temp) / "state"
            capture = io.StringIO()
            with (
                patch.object(D.time, "time", return_value=NOW),
                contextlib.redirect_stdout(capture),
            ):
                result = D.main(["--state-dir", str(target), *args], github=fake)
            return result, json.loads(capture.getvalue())

    def test_authenticated_complete_artifact_suppresses_duplicate_dispatch(self):
        fake = FakeGithub()
        code, report = self.invoke(fake)
        self.assertEqual(code, 0)
        self.assertEqual(report["collectionHealth"], "covered")
        self.assertEqual(report["missingWindows"], 0)
        self.assertFalse(any(post for _, post in fake.calls))

    def test_quiet_complete_window_does_not_manufacture_events_or_dispatch(self):
        fake = FakeGithub()
        for row in fake.body["checkpoints"]:
            row["counts"] = []
        code, report = self.invoke(fake)
        self.assertEqual(code, 0)
        self.assertFalse(report["positiveTelemetryObserved"])
        self.assertEqual(report["providerCaptureGuarantee"], "unknown")
        self.assertFalse(any(post for _, post in fake.calls))

    def test_missing_window_dispatches_only_fixed_workflow_main_once_per_slot(self):
        fake = FakeGithub()
        fake.body["checkpoints"].pop()
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.invoke(fake, directory=directory)[0], 2)
            self.assertEqual(self.invoke(fake, directory=directory)[0], 2)
        posts = [(path, post) for path, post in fake.calls if post]
        self.assertEqual(
            posts,
            [
                (
                    D.API + "/actions/workflows/" + D.WORKFLOW + "/dispatches",
                    {"ref": "main"},
                )
            ],
        )

    def test_unknown_post_result_records_intent_and_never_retries_same_slot(self):
        fake = FakeGithub()
        fake.body["checkpoints"].pop()
        fake.post_error = True
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(self.invoke(fake, directory=directory)[0], 2)
            self.assertEqual(self.invoke(fake, directory=directory)[0], 2)
        self.assertEqual(sum(post is not None for _, post in fake.calls), 1)

    def test_recent_inflight_is_pending_not_healthy_and_prevents_duplicate(self):
        fake = FakeGithub()
        fake.run["status"] = "in_progress"
        code, report = self.invoke(fake)
        self.assertEqual(code, 2)
        self.assertEqual(report["collectionHealth"], "degraded")
        self.assertFalse(any(post for _, post in fake.calls))

    def test_dispatch_is_healthy_only_after_real_artifact_coverage(self):
        fake = FakeGithub()
        fake.body["checkpoints"].pop()
        fake.deadline = time.monotonic() + 1
        api = fake.api

        def completed(path, post=None, **kwargs):
            result = api(path, post, **kwargs)
            if post:
                fake.body = sample_body()
            return result

        fake.api = completed
        with patch.object(D.time, "sleep"):
            code, report = self.invoke(fake)
        self.assertEqual(code, 0)
        self.assertEqual(report["dispatch"], "collection_verified")
        self.assertEqual(report["missingWindows"], 0)
        self.assertEqual(sum(post is not None for _, post in fake.calls), 1)

    def test_main_change_before_post_does_not_dispatch(self):
        fake = FakeGithub()
        fake.body["checkpoints"].pop()
        api = fake.api
        reads = 0

        def changing(path, post=None, **kwargs):
            nonlocal reads
            if path.endswith("/branches/main"):
                reads += 1
                if reads == 2:
                    fake.source = "b" * 40
            return api(path, post, **kwargs)

        fake.api = changing
        code, report = self.invoke(fake)
        self.assertEqual(code, 2)
        self.assertEqual(report["reason"], "main_changed")
        self.assertFalse(any(post for _, post in fake.calls))

    def test_stale_inflight_cannot_hide_retention_risk(self):
        fake = FakeGithub()
        fake.run.update(status="in_progress", created_at="2026-10-03T17:00:00Z")
        code, report = self.invoke(fake)
        self.assertEqual(code, 2)
        self.assertEqual(report["collectionHealth"], "degraded")
        self.assertTrue(any(post for _, post in fake.calls))

    def test_partial_or_expired_history_stays_degraded_even_current_window_complete(
        self,
    ):
        for partial in (False, True):
            fake = FakeGithub()
            if partial:
                fake.body["checkpoints"][0].update(
                    queryCoverage="partial", reasons=["replay_count_regression"]
                )
            else:
                fake.body["checkpoints"].pop(0)
            code, report = self.invoke(fake)
            self.assertEqual(code, 2)
            self.assertEqual(report["collectionHealth"], "degraded")
            self.assertFalse(any(post for _, post in fake.calls))

    def test_wrong_identity_refuses_before_any_dispatch(self):
        for mutate in (
            lambda f: setattr(f, "user", "other"),
            lambda f: f.repo.update(default_branch="other"),
            lambda f: f.repo.update(full_name="other/repo"),
            lambda f: f.workflow.update(path=".github/workflows/other.yml"),
            lambda f: f.workflow.update(state="disabled_manually"),
            lambda f: f.run.update(event="push"),
            lambda f: f.run.update(head_branch="other"),
            lambda f: f.run["actor"].update(login="other"),
            lambda f: f.run["triggering_actor"].update(login="other"),
        ):
            fake = FakeGithub()
            mutate(fake)
            self.assertEqual(self.invoke(fake)[0], 2)
            self.assertFalse(any(post for _, post in fake.calls))

    def test_wrong_activation_revision_and_readonly_gap_check_never_dispatch(self):
        fake = FakeGithub()
        self.assertEqual(
            self.invoke(fake, "--check", "--expected-source", "b" * 40)[0], 2
        )
        fake.body["checkpoints"].pop()
        self.assertEqual(
            self.invoke(fake, "--check", "--expected-source", SOURCE)[0], 2
        )
        self.assertFalse(any(post for _, post in fake.calls))

    def test_gapped_history_allows_only_activation_readiness_with_blocked_period(self):
        fake = gapped_producer()
        strict, strict_report = self.invoke(
            fake, "--check", "--expected-source", SOURCE
        )
        code, report = self.invoke(
            fake, "--activation-ready", "--expected-source", SOURCE
        )
        self.assertEqual(strict, 2)
        self.assertEqual(code, 0)
        self.assertTrue(report["activationReady"])
        self.assertEqual(report["collectionHealth"], "degraded")
        self.assertTrue(report["periodCoverageBlocked"])
        self.assertEqual(report["rolloutAcceptance"], "blocked_incomplete_history")
        self.assertEqual(report["missingWindows"], strict_report["missingWindows"])
        self.assertEqual(
            report["expiredMissingWindows"], strict_report["expiredMissingWindows"]
        )
        self.assertFalse(any(post for _, post in fake.calls))
        self.assertEqual(report["observationStart"], "2026-10-03T15:22:34.000Z")
        self.assertEqual(report["missingWindows"], 5)
        self.assertEqual(report["expiredMissingWindows"], 5)

    def test_activation_readiness_rejects_current_gap_missing_artifact_stale_or_wrong_source(
        self,
    ):
        for mutation in (
            lambda f: f.body["checkpoints"].pop(),
            lambda f: f.body["checkpoints"][-1].update(
                queryCoverage="partial", reasons=["retention_boundary"]
            ),
            lambda f: f.artifact.update(name="wrong-artifact"),
            lambda f: f.body.update(generatedAt="2026-10-03T17:00:00.000Z"),
            lambda f: setattr(f, "source", "b" * 40),
        ):
            fake = FakeGithub()
            mutation(fake)
            code, report = self.invoke(
                fake, "--activation-ready", "--expected-source", SOURCE
            )
            self.assertEqual(code, 2)
            self.assertFalse(report.get("activationReady", False))
            self.assertFalse(any(post for _, post in fake.calls))
        self.assertEqual(self.invoke(FakeGithub(), "--activation-ready")[0], 2)

    def test_activation_binds_real_checkpoint_source_but_historical_replay_remains_valid(
        self,
    ):
        fake = gapped_producer()
        fake.run["head_sha"] = "b" * 40
        fake.body["sourceRevision"] = "b" * 40
        # The run, ZIP digest and payload digest are internally valid; only
        # activation must require their source to equal exact current main.
        code, report = self.invoke(
            fake, "--activation-ready", "--expected-source", SOURCE
        )
        self.assertEqual(code, 2)
        self.assertFalse(report["activationReady"])
        self.assertFalse(report["activationSourceMatches"])
        self.assertEqual(report["missingWindows"], 5)
        self.assertEqual(report["expiredMissingWindows"], 5)
        self.assertTrue(report["periodCoverageBlocked"])
        self.assertEqual(report["observationStart"], fake.observation_start)
        self.assertFalse(any(post for _, post in fake.calls))
        strict, historical = self.invoke(fake, "--check", "--expected-source", SOURCE)
        self.assertEqual(strict, 2)
        self.assertTrue(historical["authenticatedCheckpoint"])
        self.assertEqual(historical["collectionHealth"], "degraded")
        self.assertEqual(historical["expiredMissingWindows"], 5)

    def test_new_windows_are_dispatched_despite_blocked_past_history(self):
        fake = FakeGithub()
        fake.body["checkpoints"].pop(0)
        fake.body["checkpoints"].pop()
        code, report = self.invoke(fake)
        self.assertEqual(code, 2)
        self.assertTrue(report["periodCoverageBlocked"])
        self.assertEqual(sum(post is not None for _, post in fake.calls), 1)

    def test_artifact_digest_boundary_source_start_or_counts_corruption_fails(self):
        mutations = [
            lambda f: setattr(f, "bad_digest", True),
            lambda f: setattr(f, "extra_member", True),
            lambda f: f.body.update(sourceRevision="b" * 40),
            lambda f: f.body.update(observationStart="2026-10-03T17:00:00Z"),
            lambda f: f.body["checkpoints"][0]["counts"][0].update(
                operation="private_identifier"
            ),
            lambda f: f.body["checkpoints"][0].update(
                queryCoverage="exhausted", reasons=["retention_boundary"]
            ),
        ]
        for mutate in mutations:
            fake = FakeGithub()
            mutate(fake)
            code, report = self.invoke(fake)
            self.assertEqual(code, 2)
            self.assertEqual(report["collectionHealth"], "unknown")
            self.assertNotIn("private_identifier", json.dumps(report))
            self.assertFalse(any(post for _, post in fake.calls))

    def test_real_subprocess_strips_provider_env_and_raw_stderr(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "gh"
            binary.write_text(
                f'#!{sys.executable}\nimport os,sys,json\nassert "VERCEL_TOKEN" not in os.environ\nassert "DATABASE_URL" not in os.environ\nassert "GH_TOKEN" not in os.environ\nprint("PRIVATE_TOKEN_SENTINEL", file=sys.stderr)\nprint(json.dumps({{"login":"other"}}))\n'
            )
            binary.chmod(0o700)
            with patch.dict(
                os.environ,
                {
                    "VERCEL_TOKEN": "PRIVATE_TOKEN_SENTINEL",
                    "DATABASE_URL": "PRIVATE_TOKEN_SENTINEL",
                    "GH_TOKEN": "PRIVATE_TOKEN_SENTINEL",
                },
            ):
                code, report = self.invoke(D.Github(str(binary), directory))
            self.assertEqual(code, 2)
            self.assertNotIn("PRIVATE_TOKEN_SENTINEL", json.dumps(report))
            self.assertEqual(report["reason"], "wrong_github_owner")

    def test_real_subprocess_timeout_and_output_budget(self):
        for body in (
            "import time;time.sleep(2)",
            "import sys;sys.stdout.write('X'*600000)",
        ):
            with tempfile.TemporaryDirectory() as directory:
                binary = Path(directory) / "gh"
                binary.write_text(f"#!{sys.executable}\n{body}\n")
                binary.chmod(0o700)
                github = D.Github(str(binary), directory)
                github.deadline = time.monotonic() + 0.15
                before = time.monotonic()
                self.assertEqual(self.invoke(github)[0], 2)
                self.assertLess(time.monotonic() - before, 1)

    def test_actual_post_subprocess_receives_only_fixed_ref_json(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / "gh"
            binary.write_text(
                f'#!{sys.executable}\nimport json,sys\nassert sys.argv[1:]==["api","--hostname","github.com","{D.API}/actions/workflows/{D.WORKFLOW}/dispatches","--method","POST","--input","-"]\nassert json.load(sys.stdin)=={{"ref":"main"}}\n'
            )
            binary.chmod(0o700)
            self.assertIsNone(
                D.Github(str(binary), directory).api(
                    D.API + "/actions/workflows/" + D.WORKFLOW + "/dispatches",
                    {"ref": "main"},
                )
            )


class ActivationTests(unittest.TestCase):
    def shell(self, body, env=None):
        return subprocess.run(
            [
                "bash",
                "-c",
                "set -euo pipefail\nsource scripts/deploy-codex-runner-host.sh\n"
                + body,
            ],
            cwd=ROOT,
            env={**os.environ, **(env or {})},
            capture_output=True,
            text=True,
            check=False,
        )

    def test_updates_keep_observation_optin_even_legacy_start_all(self):
        for start in (0, 1):
            result = self.shell(f"""TIMERS=(alpha.timer "$OBSERVATION_TIMER")
START_TIMERS={start}
systemctl() {{ echo "$*"; }}
restore_timer_enablement""")
            self.assertEqual(result.returncode, 0, result.stderr)
            disabled = [
                row.split()[1:]
                for row in result.stdout.splitlines()
                if row.startswith("disable ")
            ]
            enabled = [
                row.split()[1:]
                for row in result.stdout.splitlines()
                if row.startswith("enable ")
            ]
            self.assertTrue(
                any(
                    "jobseek-codex-company-selection-observation.timer" in row
                    for row in disabled
                )
            )
            self.assertFalse(
                any(
                    "jobseek-codex-company-selection-observation.timer" in row
                    for row in enabled
                )
            )

    def activation_fixture(self, directory):
        folder = Path(directory)
        (folder / "release.txt").write_text("revision=" + SOURCE + "\n")
        for name in (
            "codex-company-selection-observation-dispatch.py",
            "jobseek-codex-company-selection-observation.service",
            "jobseek-codex-company-selection-observation.timer",
        ):
            source = (
                ROOT / ("scripts" if name.endswith(".py") else "deploy/systemd") / name
            )
            (folder / name).write_bytes(source.read_bytes())
        script = f"""PRIVILEGED_DIR={shlex.quote(directory)}
FIXTURE_UNITS={shlex.quote(directory)}
EXPECTED_SHA={SOURCE}
JOBSEEK_CODEX_OBSERVATION_CONFIRMATION=ACTIVATE-COMPANY-SELECTION-OBSERVATION
stat() {{ echo 0:644; }}
id() {{ echo codex-runner; }}
as_runner() {{ echo "runner-check $*"; return "$CHECK_RESULT"; }}
systemctl() {{ echo "systemctl $*"; }}
activate_company_selection_observation_timer"""
        # Substitute only /etc fixture paths; the real file predicates and cmp
        # compare installed bytes against the exact trusted bundle. stat models
        # matching root-owner/0644 metadata while release.txt binds the SHA.
        return script.replace(
            "activate_company_selection_observation_timer",
            'eval "$(declare -f activate_company_selection_observation_timer | sed "s#/etc/systemd/system/#$FIXTURE_UNITS/#g")"\nactivate_company_selection_observation_timer',
        )

    def test_targeted_activation_checks_revision_and_coverage_before_only_one_timer(
        self,
    ):
        with tempfile.TemporaryDirectory() as directory:
            script = self.activation_fixture(directory)
            for code in (0, 1):
                result = self.shell(script, {"CHECK_RESULT": str(code)})
                if code:
                    self.assertNotEqual(result.returncode, 0)
                    self.assertNotIn("systemctl", result.stdout)
                else:
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn(
                        "--activation-ready --expected-source " + SOURCE, result.stdout
                    )
                    self.assertEqual(
                        [
                            row
                            for row in result.stdout.splitlines()
                            if row.startswith("systemctl ")
                        ],
                        [
                            "systemctl start --no-block jobseek-codex-company-selection-observation.service",
                            "systemctl enable --now jobseek-codex-company-selection-observation.timer",
                            "systemctl is-active --quiet jobseek-codex-company-selection-observation.timer",
                        ],
                    )

    def test_gapped_readiness_evidence_is_emitted_before_only_future_trigger_activation(
        self,
    ):
        fake = gapped_producer()
        code, report = DispatchTests().invoke(
            fake, "--activation-ready", "--expected-source", SOURCE
        )
        self.assertEqual(code, 0)
        self.assertEqual(report["expiredMissingWindows"], 5)
        with tempfile.TemporaryDirectory() as directory:
            script = self.activation_fixture(directory)
            evidence = shlex.quote(json.dumps(report))
            script = script.replace(
                'as_runner() { echo "runner-check $*"; return "$CHECK_RESULT"; }',
                'as_runner() { echo "runner-check $*"; echo '
                + evidence
                + '; return "$CHECK_RESULT"; }',
            )
            result = self.shell(script, {"CHECK_RESULT": str(code)})
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "--activation-ready --expected-source " + SOURCE, result.stdout
            )
            lines = result.stdout.splitlines()
            emitted = json.loads(next(line for line in lines if line.startswith("{")))
            self.assertEqual(emitted["expiredMissingWindows"], 5)
            self.assertTrue(emitted["periodCoverageBlocked"])
            self.assertEqual(emitted["collectionHealth"], "degraded")
            self.assertLess(
                lines.index(next(line for line in lines if line.startswith("{"))),
                lines.index(
                    "systemctl start --no-block jobseek-codex-company-selection-observation.service"
                ),
            )

    def test_matching_release_and_modes_cannot_activate_stale_installed_bytes(self):
        for name in (
            "codex-company-selection-observation-dispatch.py",
            "jobseek-codex-company-selection-observation.service",
            "jobseek-codex-company-selection-observation.timer",
        ):
            with (
                self.subTest(stale_file=name),
                tempfile.TemporaryDirectory() as directory,
            ):
                script = self.activation_fixture(directory)
                (Path(directory) / name).write_text("stale partial deployment bytes\n")
                result = self.shell(script, {"CHECK_RESULT": "0"})
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("installed observation bytes differ", result.stderr)
                self.assertNotIn("runner-check", result.stdout)
                self.assertNotIn("systemctl", result.stdout)

    def test_activation_refuses_wrong_authority_without_starting_any_unit(self):
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "release.txt").write_text("revision=" + SOURCE + "\n")
            base = f"""PRIVILEGED_DIR={shlex.quote(directory)}
EXPECTED_SHA={SOURCE}
JOBSEEK_CODEX_OBSERVATION_CONFIRMATION=ACTIVATE-COMPANY-SELECTION-OBSERVATION
systemctl() {{ echo UNEXPECTED_UNIT_MUTATION; }}
"""
            for override in (
                "EXPECTED_SHA=invalid",
                "EXPECTED_SHA=" + "b" * 40,
                "BRANCH=feature",
                "START_TIMERS=1",
                "JOBSEEK_CODEX_OBSERVATION_CONFIRMATION=wrong",
            ):
                result = self.shell(
                    base + override + "\nactivate_company_selection_observation_timer"
                )
                self.assertNotEqual(result.returncode, 0)
                self.assertNotIn("UNEXPECTED_UNIT_MUTATION", result.stdout)

    def test_updates_preserve_previously_enabled_and_active_observation_only(self):
        for start in (0, 1):
            result = self.shell(f"""TIMERS=(alpha.timer "$OBSERVATION_TIMER")
START_TIMERS={start}
ENABLED_TIMERS_BEFORE_DEPLOY=("$OBSERVATION_TIMER")
systemctl() {{ echo "$*"; }}
restore_timer_enablement""")
            self.assertEqual(result.returncode, 0, result.stderr)
            enabled = [
                row.split()[1:]
                for row in result.stdout.splitlines()
                if row.startswith("enable ")
            ]
            self.assertTrue(
                any(
                    "jobseek-codex-company-selection-observation.timer" in row
                    for row in enabled
                )
            )
            for previously_active in (False, True):
                previous = '("$OBSERVATION_TIMER")' if previously_active else "()"
                result = self.shell(f"""TIMERS=(alpha.timer "$OBSERVATION_TIMER")
START_TIMERS={start}
ACTIVE_TIMERS_BEFORE_DEPLOY={previous}
TIMER_RESTORE_ARMED=1
systemctl() {{ echo "$*"; }}
restore_timers_on_exit""")
                self.assertEqual(result.returncode, 0, result.stderr)
                started = [
                    row.split()[1:]
                    for row in result.stdout.splitlines()
                    if row.startswith("start ")
                ]
                self.assertEqual(
                    any(
                        "jobseek-codex-company-selection-observation.timer" in row
                        for row in started
                    ),
                    previously_active,
                )

    def test_service_privilege_and_workflow_activation_boundaries(self):
        unit = (
            ROOT / "deploy/systemd/jobseek-codex-company-selection-observation.service"
        ).read_text()
        for field in (
            "User=codex-runner",
            "NoNewPrivileges=true",
            "ProtectHome=read-only",
            "ProtectSystem=strict",
            "MemoryMax=128M",
            "TimeoutStartSec=3min",
            "python3 -I /usr/local/lib/jobseek-codex/codex-company-selection-observation-dispatch.py",
            "-/var/run/docker.sock",
            "-/etc/jobseek-codex",
            "-/home/codex-runner/.codex",
        ):
            self.assertIn(field, unit)
        self.assertNotIn("EnvironmentFile", unit)
        self.assertNotIn("ExecStart=+", unit)
        self.assertNotIn("codex-runner.lock", unit)
        workflow = (ROOT / ".github/workflows/deploy-codex-runner.yml").read_text()
        activation = workflow.split("  activate-observation:")[1]
        for field in (
            'test "$ACTOR" = viktor-shcherb',
            'test "$TRIGGERING_ACTOR" = viktor-shcherb',
            'test "$REF" = refs/heads/main',
            'test "$EXPECTED_SHA" = "$GITHUB_SHA"',
            "ACTIVATE-COMPANY-SELECTION-OBSERVATION",
            "--activate-company-selection-observation",
            "JOBSEEK_CODEX_START_TIMERS=0",
        ):
            self.assertIn(field, activation)
        self.assertNotIn("VERCEL_TOKEN", activation)
        self.assertNotIn("systemctl start jobseek-codex-governor", activation)
        self.assertIn(
            "scripts/test-company-selection-observation-dispatch.py",
            (ROOT / ".github/workflows/ci.yml").read_text(),
        )


if __name__ == "__main__":
    unittest.main()
