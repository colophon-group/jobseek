"""Exercise ingress staging, listener recovery, and installed feed audit checks."""

from __future__ import annotations

import hashlib
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
import unittest
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
TRANSPORT = ROOT / "deploy/networking/run-remote.sh"
WORKFLOW = ROOT / ".github/workflows/deploy-hetzner-ingress.yml"


class IngressStageOrderTests(unittest.TestCase):
    def test_data_services_finish_before_crawler_stage(self):
        job = WORKFLOW.read_text().split("  host-stage-or-audit:\n", 1)[1]
        job = job.split("\n  provider-audit-conformance:", 1)[0]
        self.assertNotIn("strategy:", job)
        self.assertNotIn("matrix.", job)
        self.assertLess(
            job.index("Stage PostgreSQL host policy"), job.index("Stage Typesense host policy")
        )
        self.assertLess(
            job.index("Stage Typesense host policy"), job.index("Stage crawler host policy")
        )
        self.assertEqual(job.count("failure() && inputs.action == 'apply'"), 3)
        self.assertEqual(job.count("!cancelled() && inputs.action == 'audit'"), 3)

    def exercise_recovery(self, available_after, clock):
        source = TRANSPORT.read_text().split("<<'NATIVE_LISTENER_RECOVERY'", 1)[1]
        source = source.split("\nNATIVE_LISTENER_RECOVERY", 1)[0]
        probes = []

        def connect(address, *, timeout):
            self.assertEqual(address[0], "127.0.0.1")
            self.assertEqual(timeout, 0.25)
            probes.append(address[1])
            if clock[0] < available_after:
                raise ConnectionRefusedError
            return nullcontext()

        def sleep(seconds):
            self.assertGreater(seconds, 0)
            self.assertLessEqual(seconds, 1)
            clock[0] += seconds

        with patch.dict(
            sys.modules,
            {
                "socket": SimpleNamespace(create_connection=connect),
                "time": SimpleNamespace(monotonic=lambda: clock[0], sleep=sleep),
            },
        ):
            exec(compile(source, "native-listener-recovery", "exec"), {})
        return probes

    def test_immediate_and_delayed_recovery_probe_all_required_ports(self):
        for delay in (0.0, 3.0, 89.0):
            with self.subTest(delay=delay):
                clock = [0.0]
                self.assertEqual(set(self.exercise_recovery(delay, clock)), {9093, 9094, 9104})
                self.assertEqual(clock[0], delay)

    def test_recovery_has_a_90_second_deadline(self):
        clock = [0.0]
        with self.assertRaisesRegex(SystemExit, "did not recover within 90 seconds"):
            self.exercise_recovery(float("inf"), clock)
        self.assertEqual(clock[0], 90)

    def test_recovery_does_not_replace_strict_conformance_or_error_trap(self):
        stage = TRANSPORT.read_text().split("<<'REMOTE_STAGE'", 1)[1].split("\nREMOTE_STAGE", 1)[0]
        recovered = stage.index("NATIVE_LISTENER_RECOVERY\nfi")
        conformance = stage.rindex("--require-enforced")
        self.assertLess(recovered, conformance)
        self.assertLess(conformance, stage.index("trap - ERR EXIT", recovered))


class FeedRoleAuditTests(unittest.TestCase):
    def test_only_the_exact_safe_helper_reaches_the_database_audit(self):
        source = TRANSPORT.read_text(encoding="utf-8")
        remote = source.split("<<'REMOTE_FEED_AUDIT'\n", 1)[1].split(
            "\nREMOTE_FEED_AUDIT", 1
        )[0]
        remote = remote.replace("== 0:0:700", f"== {os.getuid()}:{os.getgid()}:700")
        if sys.platform == "darwin":
            remote = remote.replace("stat -c '%u:%g:%a' --", "stat -f '%u:%g:%Lp'")
        if shutil.which("sha256sum") is None:
            remote = remote.replace("sha256sum", "shasum -a 256")
        exact = (
            b"import sys\nassert sys.argv[1:] == ['audit']\nprint('audit executed')\n"
        )
        expected = hashlib.sha256(exact).hexdigest()
        cases = {
            "missing": "installed helper is missing",
            "symlink": "installed helper is not a regular file",
            "unsafe": "installed helper owner, group or permissions are unsafe",
            "different": "installed helper differs from the reviewed main revision",
            "exact": None,
        }
        for case, failure in cases.items():
            with self.subTest(case=case), tempfile.TemporaryDirectory() as temp:
                helper = Path(temp) / "installed-helper"
                test_script = remote.replace(
                    "/usr/local/sbin/jobseek-justup-feed-role", shlex.quote(str(helper))
                )
                if case == "symlink":
                    helper.symlink_to(Path(temp) / "absent-target")
                elif case != "missing":
                    helper.write_bytes(
                        exact if case != "different" else b"print('changed helper')\n"
                    )
                    helper.chmod(0o755 if case == "unsafe" else 0o700)
                result = subprocess.run(
                    ["bash", "-s", "--", expected],
                    input=test_script,
                    text=True,
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
                if failure is None:
                    self.assertEqual(
                        (result.returncode, result.stdout, result.stderr),
                        (0, "audit executed\n", ""),
                    )
                else:
                    self.assertEqual((result.returncode, result.stdout), (1, ""))
                    self.assertEqual(
                        result.stderr, f"Justup feed role audit: {failure}\n"
                    )


if __name__ == "__main__":
    unittest.main()
