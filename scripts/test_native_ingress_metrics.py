from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "native_metrics_conformance", ROOT / "scripts/jobseek-ingress-conformance.py"
)
assert SPEC and SPEC.loader
conformance = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = conformance
SPEC.loader.exec_module(conformance)


def listener(port: int, address: str = "127.0.0.1") -> str:
    return f"LISTEN 0 128 {address}:{port} 0.0.0.0:*\n"


def audit(listeners: str) -> dict:
    def run(command):
        return conformance.CommandResult(0, listeners if command == ["ss", "-H", "-lnt"] else "")

    with (
        patch.object(conformance, "_run", side_effect=run),
        patch.object(conformance, "_ufw_state", return_value={"compliant": True}),
        patch.object(conformance, "_sshd_state", return_value={"compliant": True}),
    ):
        return conformance.audit("crawler", "192.0.2.1")


class NativeMetricsIngressTests(unittest.TestCase):
    def test_required_set_matches_migrated_actor_contract(self):
        self.assertEqual(conformance.CRAWLER_METRICS_PORTS, {9093, 9094, 9104})
        compose = (ROOT / "apps/crawler/docker-compose.yml").read_text()
        for service, port in [
            ("ordinary-go", 9104),
            ("exporter", 9093),
            ("drain", 9094),
        ]:
            body = compose.split(f"\n  {service}:\n", 1)[1].split("\n  # --", 1)[0]
            self.assertIn(f'METRICS_PORT: "{port}"', body)
        source = (ROOT / "apps/crawler/go/ordinary-worker/environment.go").read_text()
        self.assertIn('c.metricsAddress = "127.0.0.1:9104"', source)
        self.assertNotIn(9101, conformance.CRAWLER_METRICS_PORTS)
        self.assertTrue(
            conformance.KNOWN_PORTS
            >= conformance.CRAWLER_METRICS_PORTS | conformance.CRAWLER_OPTIONAL_METRICS_PORTS
        )

    def test_native_listeners_pass_without_retired_or_dark_listeners(self):
        state = audit("".join(listener(p) for p in [9093, 9094, 9104]))
        self.assertTrue(state["required_listener_scope"])
        self.assertTrue(state["compliant"])
        diagnostics = state["runtime_metrics_listener_diagnostics"]
        self.assertEqual(
            diagnostics["required"],
            dict.fromkeys(["9093", "9094", "9104"], "loopback_only"),
        )
        self.assertTrue(all(value == "missing" for value in diagnostics["optional"].values()))
        self.assertNotIn("192.0.2.1", json.dumps(state))

    def test_missing_native_worker_fails_even_if_retired_ports_exist(self):
        state = audit("".join(listener(p) for p in [9093, 9094, 9095, 9096, 9097, 9098, 9101]))
        self.assertFalse(state["compliant"])
        self.assertEqual(
            state["runtime_metrics_listener_diagnostics"]["required"]["9104"], "missing"
        )

    def test_every_required_listener_must_exist_and_stay_loopback(self):
        for port in conformance.CRAWLER_METRICS_PORTS:
            for address in [None, "0.0.0.0", "10.0.0.8", "192.0.2.8"]:
                with self.subTest(port=port, address=address):
                    rows = "".join(
                        listener(p) for p in conformance.CRAWLER_METRICS_PORTS if p != port
                    )
                    if address is not None:
                        rows += listener(port, address)
                    state = audit(rows)
                    self.assertFalse(state["required_listener_scope"])
                    self.assertFalse(state["compliant"])

    def test_optional_listener_absence_is_allowed_but_exposure_is_not(self):
        base = "".join(listener(p) for p in conformance.CRAWLER_METRICS_PORTS)
        for port in conformance.CRAWLER_OPTIONAL_METRICS_PORTS:
            with self.subTest(port=port):
                self.assertTrue(audit(base + listener(port))["compliant"])
                for address in ["0.0.0.0", "10.0.0.8", "192.0.2.8"]:
                    self.assertFalse(audit(base + listener(port, address))["compliant"])

    def test_legacy_diagnostic_field_keeps_its_fixed_categories(self):
        state = audit(listener(9093) + listener(9094) + listener(9104))
        self.assertEqual(
            set(state["metrics_listener_diagnostics"]),
            {str(p) for p in [9093, 9094, 9095, 9096, 9097, 9098]},
        )
        self.assertEqual(state["metrics_listener_diagnostics"]["9098"], "missing")


if __name__ == "__main__":
    unittest.main()
