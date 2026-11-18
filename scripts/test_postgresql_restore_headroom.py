"""Exercise restore pressure cleanup without Docker, databases or large files."""

from __future__ import annotations

import importlib.util
import os
import shlex
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
DRILL = ROOT / "deploy/backups/postgresql/restore-drill.sh"


class RestoreHeadroomTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.base = Path(self.temp.name)
        self.bin = self.base / "bin"
        self.bin.mkdir()
        self.trace = self.base / "docker.trace"
        self.available = self.base / "available"
        self.available.write_text(str(20 * 1024**3))
        self.env = {
            **os.environ,
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "TEST_AVAILABLE": str(self.available),
            "TEST_TRACE": str(self.trace),
        }
        for name, body in {
            "python3": '#!/bin/sh\ncat "$TEST_AVAILABLE"\n',
            "docker": """#!/bin/sh
printf '%s\\n' "$*" >> "$TEST_TRACE"
case "$1 $2" in
  'container inspect') exit 1 ;;
  'run --rm') exit "${TEST_LOADER_EXIT:-0}" ;;
  'exec '*)
    case "$*" in
      *current_setting*) echo '16.13|f|off' ;;
      *pg_is_in_recovery*) echo f ;;
    esac ;;
esac
exit 0
""",
            "mountpoint": "#!/bin/sh\nexit 0\n",
            "findmnt": "#!/bin/sh\necho cifs\n",
            "install": '#!/bin/sh\nfor value do case "$value" in /*) mkdir -p -- "$value";; esac; done\n',
            "timeout": '#!/bin/sh\nshift 3\nexec "$@"\n',
        }.items():
            path = self.bin / name
            path.write_text(body)
            path.chmod(0o700)
        source = DRILL.read_text()
        self.functions = source.split("scratch_headroom_ok()", 1)[1]
        self.functions = "scratch_headroom_ok()" + self.functions.split("\ntrap cleanup EXIT", 1)[0]
        self.data = self.base / "run"
        self.spool = self.base / "run-spool"
        self.data.mkdir()
        self.spool.mkdir()
        self.failure = self.base / "run-headroom-failed"
        variables = {
            "BASE_DIR": self.base,
            "DATA_DIR": self.data,
            "SPOOL_DIR": self.spool,
            "HEADROOM_FAILURE": self.failure,
            "RESTORE_CONTAINER": "jobseek-postgresql-restore-load-test",
            "CONTAINER": "jobseek-postgresql-restore-test",
            "HEADROOM_WATCH_PID": "",
            "HEADROOM_STOP_BYTES": 10 * 1024**3,
            "KEEP_FAILED": "true",
        }
        self.setup = "\n".join(f"{k}={shlex.quote(str(v))}" for k, v in variables.items())

    def run_shell(self, body):
        return subprocess.run(
            ["bash", "-c", "set -euo pipefail\n" + self.setup + "\n" + self.functions + "\n" + body],
            env=self.env,
            capture_output=True,
            text=True,
            timeout=8,
            check=False,
        )

    def test_headroom_threshold_and_unavailable_measurement(self):
        self.assertEqual(self.run_shell("require_headroom").returncode, 0)
        for value in (str(9 * 1024**3), "not-a-number"):
            with self.subTest(value=value):
                self.available.write_text(value)
                self.assertNotEqual(self.run_shell("require_headroom").returncode, 0)

    def test_memory_limit_applies_to_both_phases_and_loader_failure_stops(self):
        config = self.base / "config"
        config.mkdir()
        (config / "pgbackrest.conf").write_text("synthetic\n")
        env = {
            **self.env,
            "JOBSEEK_POSTGRES_BACKUP_CONFIG": str(config),
            "JOBSEEK_POSTGRES_RESTORE_DRILL_DIR": str(self.base),
            "JOBSEEK_POSTGRES_RESTORE_KEEP_FAILED": "false",
        }
        for loader_exit in (0, 137):
            with self.subTest(loader_exit=loader_exit):
                self.trace.unlink(missing_ok=True)
                result = subprocess.run(
                    ["bash", str(DRILL), "20261118-010000F"],
                    env={**env, "TEST_LOADER_EXIT": str(loader_exit)},
                    capture_output=True,
                    text=True,
                    timeout=8,
                    check=False,
                )
                self.assertEqual(result.returncode, loader_exit, result.stderr)
                launches = [line.split() for line in self.trace.read_text().splitlines() if line.startswith("run ")]
                expected_phases = ["--rm"] if loader_exit else ["--rm", "--detach"]
                self.assertEqual([args[1] for args in launches], expected_phases)
                for args in launches:
                    self.assertEqual(args[args.index("--memory") + 1], "4g")
                    self.assertEqual(args[args.index("--network") + 1], "none")
                self.assertEqual(list(self.base.glob("20*")), [])

    def test_pressure_watch_removes_only_owned_containers_including_phase_race(self):
        result = self.run_shell(
            'watch_headroom &\nHEADROOM_WATCH_PID=$!\n'
            'sleep 0.1\ntest ! -e "$HEADROOM_FAILURE"\n'
            'printf "0\\n" > "$TEST_AVAILABLE"\n'
            # A second removal catches a container created after the first check.
            'for _ in {1..100}; do\n'
            '  if [[ -e "$TEST_TRACE" ]] && [[ $(wc -l < "$TEST_TRACE") -ge 2 ]]; then break; fi\n'
            '  sleep 0.03\ndone\n'
            'kill "$HEADROOM_WATCH_PID"\nwait "$HEADROOM_WATCH_PID" || true\n'
            'test -e "$HEADROOM_FAILURE"\n'
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        commands = self.trace.read_text().splitlines()
        self.assertGreaterEqual(len(commands), 2)
        self.assertEqual(set(commands), {
            "rm -f jobseek-postgresql-restore-load-test jobseek-postgresql-restore-test"
        })

    def test_pressure_failure_cleans_scratch_even_when_debug_retention_requested(self):
        self.failure.touch()
        result = self.run_shell("true\ncleanup")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.data.exists())
        self.assertFalse(self.spool.exists())
        self.assertFalse(self.failure.exists())
        self.assertNotIn("drill passed", result.stdout)

    def test_marker_write_failure_still_stops_and_fails_closed_after_space_recovers(self):
        self.available.write_text("not-a-number")
        result = self.run_shell(
            'HEADROOM_FAILURE="$BASE_DIR/absent-parent/marker"\n'
            'watch_headroom &\nHEADROOM_WATCH_PID=$!\n'
            'for _ in {1..100}; do\n'
            '  [[ ! -s "$TEST_TRACE" ]] || break\n  sleep 0.03\ndone\n'
            'test -s "$TEST_TRACE"\n'
            'test ! -e "$HEADROOM_FAILURE"\n'
            # A recovered measurement cannot erase the watcher failure.
            f'printf "{20 * 1024**3}\\n" > "$TEST_AVAILABLE"\n'
            'true\ncleanup\n'
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(
            "rm -f jobseek-postgresql-restore-load-test jobseek-postgresql-restore-test",
            self.trace.read_text().splitlines(),
        )
        self.assertFalse(self.data.exists())
        self.assertFalse(self.spool.exists())

    def test_other_failure_keeps_requested_debug_data_with_healthy_space(self):
        result = self.run_shell("set +e\nfalse\ncleanup")
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(self.data.exists())
        self.assertFalse(self.spool.exists())

    def test_role_drill_rejects_inherited_paths_and_shell_startup_environment(self):
        path = ROOT / "deploy/networking/justup-feed-readonly-role.py"
        spec = importlib.util.spec_from_file_location("restore_role_contract", path)
        module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {spec.name: module}):
            spec.loader.exec_module(module)
            process = Mock()
            process.wait.return_value = 0
            with patch.dict(os.environ, {
                "JOBSEEK_POSTGRES_RESTORE_DRILL_DIR": "/unexpected-scratch",
                "JOBSEEK_POSTGRES_BACKUP_IMAGE": "unexpected-image",
                "JOBSEEK_POSTGRES_BACKUP_CONFIG": "/unexpected-config",
                "BASH_ENV": "/unexpected-startup",
            }), patch.object(module.subprocess, "Popen", return_value=process) as popen:
                module.run_restore_drill(Path("/reviewed/drill"), "exact-label", 9)
            kwargs = popen.call_args.kwargs
            self.assertEqual(kwargs["env"], {
                "PATH": "/usr/sbin:/usr/bin:/sbin:/bin", "LANG": "C", "TZ": "UTC",
                "HOME": "/nonexistent", "JOBSEEK_POSTGRES_RESTORE_KEEP_FAILED": "false",
            })
            self.assertEqual(kwargs["pass_fds"], (9,))


if __name__ == "__main__":
    unittest.main()
