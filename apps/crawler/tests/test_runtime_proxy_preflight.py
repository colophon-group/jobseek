from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

CRAWLER_ROOT = Path(__file__).resolve().parent.parent


def _run_preflight(**overrides: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env.update(
        {
            "PROXY_PROVIDER": "none",
            "WEBSHARE_PROXY_URLS": "[]",
            "WEBSHARE_PROXY_URL": "",
            **overrides,
        }
    )
    return subprocess.run(
        [sys.executable, "-m", "src.runtime_proxy_preflight"],
        cwd=CRAWLER_ROOT,
        env=env,
        capture_output=True,
        check=False,
        text=True,
    )


def test_preflight_accepts_pool_and_emits_only_sanitized_cardinality() -> None:
    result = _run_preflight(
        PROXY_PROVIDER="webshare",
        WEBSHARE_PROXY_URLS=(
            '["http://first-user:first-password@p.webshare.io:10001",'
            '"http://second-user:second-password@p.webshare.io:10002"]'
        ),
    )

    assert result.returncode == 0
    assert result.stdout == (
        "Runtime proxy configuration valid: mode=backbone_pool, pool_entries=2\n"
    )
    assert result.stderr == ""
    for sensitive in ("first-user", "first-password", "p.webshare.io"):
        assert sensitive not in result.stdout
        assert sensitive not in result.stderr


def test_preflight_rejects_non_json_without_echoing_input() -> None:
    invalid = "not-json-and-must-not-be-echoed"
    result = _run_preflight(
        PROXY_PROVIDER="webshare",
        WEBSHARE_PROXY_URLS=invalid,
    )

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Runtime proxy configuration invalid (SettingsError)\n"
    assert invalid not in result.stderr


def test_preflight_rejects_selected_provider_without_endpoint() -> None:
    result = _run_preflight(PROXY_PROVIDER="webshare")

    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == "Runtime proxy configuration invalid (ValueError)\n"
