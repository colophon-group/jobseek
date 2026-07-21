"""Regression checks for the production Grafana Alloy configuration."""

from __future__ import annotations

import re
from pathlib import Path

CONFIG = (Path(__file__).resolve().parents[1] / "alloy.river").read_text(encoding="utf-8")


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
