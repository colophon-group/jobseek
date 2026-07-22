"""Regression checks for the production Grafana Alloy configuration."""

from __future__ import annotations

import re
from pathlib import Path

CONFIG = (Path(__file__).resolve().parents[1] / "alloy.river").read_text(encoding="utf-8")
COMPOSE = (Path(__file__).resolve().parents[1] / "docker-compose.yml").read_text(encoding="utf-8")
ROOT = Path(__file__).resolve().parents[3]
HOST_CONFIG = (ROOT / "deploy" / "observability" / "alloy-host.alloy").read_text(encoding="utf-8")
HOST_INSTALLER = (ROOT / "deploy" / "observability" / "install-host.sh").read_text(encoding="utf-8")
HOST_SERVICE = (ROOT / "deploy" / "systemd" / "jobseek-alloy.service").read_text(encoding="utf-8")


def _component_body(kind: str) -> str:
    match = re.search(
        rf'^{re.escape(kind)} "containers" \{{(?P<body>.*?)^\}}',
        CONFIG,
        flags=re.MULTILINE | re.DOTALL,
    )
    assert match is not None, f"missing Alloy component: {kind} containers"
    return match.group("body")


def test_docker_discovery_and_tailer_refresh_intervals_are_aligned():
    """Deleted --rm containers must leave discovery within one tailer retry."""
    assert 'refresh_interval = "5s"' in _component_body("discovery.docker")
    assert 'refresh_interval = "5s"' in _component_body("loki.source.docker")


def test_alloy_uses_supported_environment_lookup():
    assert re.search(r"(?<!sys\.)\benv\(", CONFIG) is None
    assert CONFIG.count("sys.env(") == 6


def test_crawler_alloy_is_pinned_and_no_longer_privileged():
    assert "grafana/alloy:v1.18.0@sha256:" in COMPOSE
    alloy_section = COMPOSE.split("  alloy:\n", 1)[1].split("\nvolumes:", 1)[0]
    assert "privileged:" not in alloy_section
    assert "pid: host" not in alloy_section
    assert "- /:/host:ro" not in alloy_section
    assert "read_only: true" in alloy_section
    assert "no-new-privileges:true" in alloy_section
    assert "cap_drop:\n      - ALL" in alloy_section
    assert "--server.http.listen-addr=127.0.0.1:12346" in alloy_section


def test_host_metrics_have_stable_roles_and_no_public_listener():
    assert 'replacement  = "integrations/unix"' in HOST_CONFIG
    assert 'replacement  = sys.env("JOBSEEK_HOST_INSTANCE")' in HOST_CONFIG
    assert 'replacement  = sys.env("JOBSEEK_HOST_ROLE")' in HOST_CONFIG
    assert '"__address__" = "127.0.0.1:12347"' in HOST_CONFIG
    assert "--server.http.listen-addr=127.0.0.1:12347" in HOST_SERVICE
    assert "/var/run/docker.sock" not in HOST_CONFIG
    assert 'directory = "/var/lib/jobseek-observability/textfile"' in HOST_CONFIG


def test_host_alloy_unprivileged_paths_and_readiness_are_enforced():
    assert (
        'install -d -o root -g jobseek-alloy -m 0750 "$CONFIG_ROOT" "$STATE_ROOT"' in HOST_INSTALLER
    )
    assert 'install -d -o root -g jobseek-alloy -m 0750 "${STATE_ROOT}/textfile"' in HOST_INSTALLER
    assert "install -o root -g jobseek-alloy -m 0640" in HOST_INSTALLER
    assert "alloy_service_pid_is_expected" in HOST_INSTALLER
    assert 'readlink -f "/proc/${pid}/exe"' in HOST_INSTALLER
    assert "systemctl is-active --quiet jobseek-alloy.service" in HOST_INSTALLER
    assert 'ALLOY_READY_URL="http://${ALLOY_LISTEN_ADDR}/-/ready"' in HOST_INSTALLER
    assert 'rm -f "$BINARY"' in HOST_INSTALLER
    assert 'rm -f "$SAMPLER"' in HOST_INSTALLER
    assert '"${STATE_ROOT}/deployed-sha"' in HOST_INSTALLER
