from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[3] / "deploy/lightpanda-renderer/proxy-settings.py"
spec = importlib.util.spec_from_file_location("lightpanda_proxy_settings", SCRIPT)
assert spec is not None and spec.loader is not None
settings = importlib.util.module_from_spec(spec)
spec.loader.exec_module(settings)


def test_registry_preserves_pool_order_rotation_and_explicit_disabled_provider(tmp_path: Path):
    endpoints = ["http://fixture:one@proxy.example:8000", "http://fixture:two@proxy.example:8001"]
    environment = {"PROXY_PROVIDER": "webshare", "WEBSHARE_PROXY_URLS": json.dumps(endpoints)}
    target = tmp_path / "registry.json"
    settings.write_registry(target, environment)
    assert target.stat().st_mode & 0o777 == 0o600
    assert json.loads(target.read_bytes()) == endpoints
    assert (
        settings.registry_bytes({"PROXY_PROVIDER": "none", **{"WEBSHARE_PROXY_URLS": "bad"}})
        == b"[]\n"
    )
    with pytest.raises(FileExistsError):
        settings.write_registry(target, environment)
    assert (
        json.loads(
            settings.registry_bytes(
                {"PROXY_PROVIDER": "webshare", "WEBSHARE_PROXY_URL": endpoints[0]}
            )
        )
        == endpoints[:1]
    )


@pytest.mark.parametrize(
    "environment",
    [
        {"PROXY_PROVIDER": "other"},
        {"PROXY_PROVIDER": "webshare"},
        {"PROXY_PROVIDER": "webshare", "WEBSHARE_PROXY_URLS": "null"},
        {"PROXY_PROVIDER": "webshare", "WEBSHARE_PROXY_URLS": "[true]"},
        {"PROXY_PROVIDER": "webshare", "WEBSHARE_PROXY_URLS": "[1]"},
        {"PROXY_PROVIDER": "webshare", "WEBSHARE_PROXY_URLS": '[""]'},
        {
            "PROXY_PROVIDER": "webshare",
            "WEBSHARE_PROXY_URLS": json.dumps(["http://proxy.example"] * 65),
        },
    ],
)
def test_invalid_registry_never_creates_partial_secret_file(
    tmp_path: Path, environment: dict[str, str]
):
    target = tmp_path / "registry.json"
    with pytest.raises((ValueError, TypeError)):
        settings.write_registry(target, environment)
    assert not target.exists()
