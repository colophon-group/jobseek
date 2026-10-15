from __future__ import annotations

import importlib.util
import io
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = Path(__file__).resolve().parent


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / name)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


runner = load("ai-filter-refresh.py")


class Response(io.BytesIO):
    status = 200


class FakeOpener:
    def __init__(self, responses):
        self.responses = iter(responses)
        self.requests = []

    def open(self, request, timeout):
        self.requests.append(request)
        return Response(json.dumps(next(self.responses)).encode())


class RefreshTimerTests(unittest.TestCase):
    def test_shared_driver_continues_active_work_backs_off_idle_and_stops(self):
        class Stop:
            waits = []
            def is_set(self):
                return len(self.waits) == 3
            def wait(self, seconds):
                self.waits.append(seconds)

        stop = Stop()
        outcomes = iter([
            {"status": "completed", "started": 1, "claimed": 1},
            {"status": "completed", "started": 0, "claimed": 0},
            {"status": "completed", "started": 0, "claimed": 0},
        ])
        output = io.StringIO()
        with redirect_stdout(output):
            runner.serve(lambda: next(outcomes), stop)
        self.assertEqual(stop.waits, [2, 30, 30])
        self.assertEqual(len(output.getvalue().splitlines()), 2)

    def test_shared_driver_backs_off_failures_without_logging_exception_data(self):
        class Stop:
            waits = []
            def is_set(self):
                return len(self.waits) == 3
            def wait(self, seconds):
                self.waits.append(seconds)

        def fail():
            raise ValueError("sensitive upstream text")

        stop = Stop()
        output = io.StringIO()
        with redirect_stdout(output):
            runner.serve(fail, stop)
        self.assertEqual(stop.waits, [60, 120, 240])
        self.assertNotIn("sensitive", output.getvalue())

    def test_dispatches_to_only_the_fixed_endpoint_and_omits_private_response_fields(self):
        opener = FakeOpener([{"contractVersion": "narrowed-worker-v2", "status": "completed", "claimed": 1, "started": 1, "deferred": 0, "failed": 0, "private": "do not log"}])
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "refresh-bearer").write_text("fixture" * 8)
            result = runner.run(opener, folder)
        self.assertEqual(opener.requests[0].full_url, runner.ENDPOINT)
        self.assertNotIn("private", result)
        self.assertEqual(result["started"], 1)

    def test_rejects_an_old_web_contract(self):
        opener = FakeOpener([{"status": "completed"}])
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "refresh-bearer").write_text("fixture" * 8)
            with self.assertRaisesRegex(ValueError, "contract_unavailable"):
                runner.run(opener, folder)

    def test_does_not_follow_a_redirect_with_the_bearer(self):
        self.assertIsNone(runner.NoRedirect().redirect_request(None, None, 302, "", {}, "https://other.example"))

    def test_rejects_malformed_counters(self):
        opener = FakeOpener([{"contractVersion": "narrowed-worker-v2", "status": "completed", "claimed": True}])
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "refresh-bearer").write_text("fixture" * 8)
            with self.assertRaisesRegex(ValueError, "response_invalid"):
                runner.run(opener, folder)

    def test_pause_operation_is_bounded_and_activation_refuses_source_maintenance(self):
        pause = (ROOT.parent / "deploy/ai-filter-refresh/pause-remote.sh").read_text()
        self.assertIn("StrictHostKeyChecking=yes", pause)
        host = (ROOT.parent / "deploy/ai-filter-refresh/pause-host.sh").read_text()
        self.assertIn("systemctl disable --now jobseek-ai-filter-refresh.timer", host)
        self.assertIn("systemctl stop jobseek-ai-filter-refresh.service", host)
        self.assertIn("systemctl reset-failed jobseek-ai-filter-refresh.service", host)
        self.assertNotIn("AI_FILTER_REFRESH_SECRET", pause)
        self.assertNotIn("docker", pause)
        activate = (ROOT.parent / "deploy/ai-filter-refresh/deploy-remote.sh").read_text()
        self.assertIn('controls["narrowedWorkerPaused"] is False', activate)
        workflow = (ROOT.parent / ".github/workflows/deploy-ai-filter-refresh.yml").read_text()
        self.assertIn("default: pause", workflow)
        self.assertIn("if: inputs.action == 'activate'", workflow)
        self.assertIn("if: inputs.action == 'pause'", workflow)

    def test_host_pause_handles_active_failed_and_unloaded_service_without_starting_units(self):
        fake_systemctl = """#!/usr/bin/env python3
import json, os, sys
from pathlib import Path
path = Path(os.environ['PAUSE_FIXTURE_STATE'])
state = json.loads(path.read_text())
a = sys.argv[1:]
state['calls'].append(a)
code = 0
if a[0] == 'show' and a[1] == 'jobseek-ai-filter-refresh.timer':
    print('loaded')
elif a == ['disable', '--now', 'jobseek-ai-filter-refresh.timer']:
    state['enabled'] = False
    state['timer'] = 'inactive'
elif a == ['disable', 'jobseek-ai-filter-refresh.service']:
    state['service_enabled'] = False
elif a == ['stop', 'jobseek-ai-filter-refresh.service']:
    if state['service'] != 'failed': state['service'] = 'unloaded'
elif a == ['is-failed', '--quiet', 'jobseek-ai-filter-refresh.service']:
    code = 0 if state['service'] == 'failed' else 1
elif a == ['reset-failed', 'jobseek-ai-filter-refresh.service']:
    if state['service'] == 'unloaded': code = 1
    else: state['service'] = 'unloaded'
elif a == ['is-enabled', 'jobseek-ai-filter-refresh.timer']:
    print('enabled' if state['enabled'] else 'disabled')
    code = 0 if state['enabled'] else 1
elif a[0] == 'is-active' and a[1] in ['jobseek-ai-filter-refresh.timer', 'jobseek-ai-filter-refresh.service']:
    value = state['timer'] if a[1].endswith('.timer') else state['service']
    print('inactive' if value == 'unloaded' else value)
    code = 0 if value == 'active' else 3
else:
    code = 99
path.write_text(json.dumps(state))
sys.exit(code)
"""
        for initial in ('active', 'failed', 'unloaded'):
            with self.subTest(initial=initial), tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                binary = folder / 'systemctl'
                binary.write_text(fake_systemctl)
                binary.chmod(0o700)
                state_file = folder / 'state.json'
                state_file.write_text(json.dumps({'service': initial, 'timer': 'active', 'enabled': True, 'calls': []}))
                environment = {**os.environ, 'PATH': str(folder) + os.pathsep + os.environ['PATH'], 'PAUSE_FIXTURE_STATE': str(state_file)}
                for attempt in range(2):
                    result = subprocess.run(['bash', str(ROOT.parent / 'deploy/ai-filter-refresh/pause-host.sh')], env=environment, capture_output=True, text=True)
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertIn('ai_filter_refresh_paused', result.stdout)
                state = json.loads(state_file.read_text())
                self.assertFalse(state['enabled'])
                self.assertEqual(state['timer'], 'inactive')
                self.assertEqual(state['service'], 'unloaded')
                resets = [call for call in state['calls'] if call[0] == 'reset-failed']
                self.assertEqual(len(resets), 1 if initial == 'failed' else 0)
                self.assertFalse(any(call[0] in ('start', 'restart', 'enable') for call in state['calls']))

    def test_timer_and_service_keep_credential_and_docker_boundaries(self):
        service = (ROOT.parent / "deploy/systemd/jobseek-ai-filter-refresh.service").read_text()
        timer = (ROOT.parent / "deploy/systemd/jobseek-ai-filter-refresh.timer").read_text()
        self.assertIn("DynamicUser=true", service)
        self.assertIn("LoadCredential=refresh-bearer:", service)
        self.assertIn("InaccessiblePaths=-/var/run/docker.sock", service)
        self.assertIn("Type=simple", service)
        self.assertIn("runner.py --worker", service)
        self.assertIn("TimeoutStopSec=65s", service)
        self.assertIn("CPUQuota=10%", service)
        self.assertIn("MemoryMax=64M", service)
        self.assertIn("OnCalendar=*-*-* *:*:00", timer)
        self.assertNotIn("EnvironmentFile", service)


if __name__ == "__main__":
    unittest.main()
