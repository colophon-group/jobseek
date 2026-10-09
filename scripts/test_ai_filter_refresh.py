from __future__ import annotations

import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest

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
    def test_dispatches_to_only_the_fixed_endpoint_and_omits_private_response_fields(self):
        opener = FakeOpener([{"contractVersion": "narrowed-refresh-v1", "status": "completed", "claimed": 1, "started": 1, "deferred": 0, "failed": 0, "private": "do not log"}])
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
        opener = FakeOpener([{"contractVersion": "narrowed-refresh-v1", "status": "completed", "claimed": True}])
        with tempfile.TemporaryDirectory() as folder:
            (Path(folder) / "refresh-bearer").write_text("fixture" * 8)
            with self.assertRaisesRegex(ValueError, "response_invalid"):
                runner.run(opener, folder)

    def test_timer_and_service_keep_credential_and_docker_boundaries(self):
        service = (ROOT.parent / "deploy/systemd/jobseek-ai-filter-refresh.service").read_text()
        timer = (ROOT.parent / "deploy/systemd/jobseek-ai-filter-refresh.timer").read_text()
        self.assertIn("DynamicUser=true", service)
        self.assertIn("LoadCredential=refresh-bearer:", service)
        self.assertIn("InaccessiblePaths=-/var/run/docker.sock", service)
        self.assertIn("OnCalendar=*-*-* *:*:00", timer)
        self.assertNotIn("EnvironmentFile", service)


if __name__ == "__main__":
    unittest.main()
