from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

CRAWLER_ROOT = Path(__file__).resolve().parents[2]
PROBE_ROOT = CRAWLER_ROOT / "lightpanda-probe"
WORKFLOW = CRAWLER_ROOT.parents[1] / ".github" / "workflows" / "lightpanda-probe.yml"
EXPECTED_IMAGE = (
    "lightpanda/browser@sha256:bf2328538effa8392166d0cbdba9943a2c97fd19cd2e75b88c8c6f0cf03a1beb"
)
EXPECTED_AMD64 = "sha256:9ddc7ba5a147f713f883dace7eba3fe045c5e0537fe1d1160ed2fc4ec5359027"


def test_immutable_runtime_pins_are_consistent() -> None:
    pins = json.loads((PROBE_ROOT / "pins.json").read_text())
    go_mod = (PROBE_ROOT / "go.mod").read_text()
    integration = (PROBE_ROOT / "scripts" / "integration.sh").read_text()
    workflow = WORKFLOW.read_text()

    assert pins["go"] == "1.24.0"
    assert pins["chromedp"] == "v0.14.2"
    assert pins["lightpanda"]["version"] == "0.3.6"
    assert pins["lightpanda"]["image"] == EXPECTED_IMAGE
    assert pins["lightpanda"]["linux_amd64_manifest_digest"] == EXPECTED_AMD64
    assert "github.com/chromedp/chromedp v0.14.2" in go_mod
    assert 'go-version: "1.24.0"' in workflow
    assert EXPECTED_IMAGE in integration
    assert EXPECTED_AMD64 in integration


def test_fixture_digests_and_origins_are_frozen() -> None:
    expected = json.loads((PROBE_ROOT / "fixtures" / "digests.json").read_text())
    plans = PROBE_ROOT / "fixtures" / "plans"
    assert sorted(expected) == sorted(path.name for path in plans.glob("*.json"))

    for name, digest in expected.items():
        data = (plans / name).read_bytes()
        assert hashlib.sha256(data).hexdigest() == digest
        plan = json.loads(data)
        assert plan["contractVersion"] == "crawler.runtime/v1"
        assert plan["targetUrl"].startswith("http://fixture:8080/")
        assert not plan["targetUrl"].startswith("https://")


def test_only_the_two_candidate_capabilities_are_advertised() -> None:
    pins = json.loads((PROBE_ROOT / "pins.json").read_text())
    assert pins["supported_capabilities"] == [
        "BROWSER_CAPABILITY_RENDER",
        "BROWSER_CAPABILITY_EVALUATE",
    ]
    for path in (PROBE_ROOT / "fixtures" / "plans").glob("*.json"):
        capabilities = json.loads(path.read_text())["requiredCapabilities"]
        if path.name == "unsupported.json":
            assert capabilities == [
                "BROWSER_CAPABILITY_RENDER",
                "BROWSER_CAPABILITY_FRAMES",
            ]
        else:
            assert set(capabilities) <= {
                "BROWSER_CAPABILITY_RENDER",
                "BROWSER_CAPABILITY_EVALUATE",
            }


def test_workflow_has_no_production_or_egress_escape_hatches() -> None:
    workflow = WORKFLOW.read_text()
    integration = (PROBE_ROOT / "scripts" / "integration.sh").read_text()
    combined = workflow + integration

    assert "docker network create --internal" in integration
    assert "--network-alias fixture" in integration
    assert integration.count("--network-alias lightpanda") == 2
    assert "--network host" not in combined
    assert "--publish" not in combined
    assert "secrets." not in workflow
    assert "environment: production" not in workflow
    assert "deploy" not in workflow.lower()
    assert "apps/crawler/VERSION" not in workflow
    assert not re.search(r"uses:\s+[^\s@]+@(?![0-9a-f]{40}\b)", workflow)


def test_go_probe_has_only_fixed_synthetic_network_endpoints() -> None:
    sources = "\n".join(
        path.read_text() for path in PROBE_ROOT.rglob("*.go") if not path.name.endswith("_test.go")
    )
    assert 'FixtureOrigin = "http://fixture:8080"' in sources
    assert 'LightpandaWS  = "ws://lightpanda:9222"' in sources
    assert "https://" not in sources
    assert "Proxy: nil" in sources


def test_output_model_excludes_nondeterministic_and_sensitive_fields() -> None:
    model = (PROBE_ROOT / "internal" / "harness" / "model.go").read_text()
    forbidden_json_fields = {
        "timestamp",
        "duration_ms",
        "request_id",
        "session_id",
        "host_path",
        "credential",
    }
    for field in forbidden_json_fields:
        assert f'json:"{field}' not in model
