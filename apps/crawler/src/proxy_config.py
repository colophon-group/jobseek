"""Safely materialize the Webshare backbone pool into an operator env file.

The updater is deliberately local/operator-only. It reads the fixed Webshare
control-plane endpoint, creates a mode-0600 backup before every mutation, and
atomically updates only the Webshare runtime keys. It never prints credentials
or deploys the API key to crawler containers.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import shutil
import tempfile
from datetime import UTC, datetime
from ipaddress import ip_address
from pathlib import Path
from typing import Any
from urllib.parse import quote

import httpx
from dotenv import dotenv_values

from src.proxy_audit import ProxyAuditError

_API_URL = "https://proxy.webshare.io/api/v2/proxy/list/"
_ENV_ASSIGNMENT = re.compile(r"^\s*(?:export\s+)?([A-Z][A-Z0-9_]*)=")
_MANAGED_KEYS = (
    "PROXY_PROVIDER",
    "WEBSHARE_PROXY_URLS",
    "WEBSHARE_EXPECTED_CLIENT_IPS",
)
_RETIRED_KEYS = frozenset({"DECODO_PROXY_URL"})


def _response_rows(response: httpx.Response) -> list[dict[str, Any]]:
    if response.status_code >= 400:
        raise ProxyAuditError(f"Webshare backbone proxy list returned HTTP {response.status_code}")
    try:
        payload = response.json()
    except ValueError as exc:
        raise ProxyAuditError("Webshare backbone proxy list returned invalid JSON") from exc
    if not isinstance(payload, dict):
        raise ProxyAuditError("Webshare backbone proxy list returned an unexpected shape")
    rows = payload.get("results")
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ProxyAuditError("Webshare backbone proxy list omitted its results list")
    count = payload.get("count")
    if not isinstance(count, int) or count != len(rows):
        raise ProxyAuditError("Webshare backbone proxy list was unexpectedly paginated")
    return rows


async def fetch_webshare_backbone_urls(
    api_key: str,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
) -> tuple[str, ...]:
    """Fetch and validate all current per-proxy backbone credentials."""

    if not api_key:
        raise ProxyAuditError("WEBSHARE_API_KEY is not configured")
    headers = {
        "Authorization": f"Token {api_key}",
        "Accept": "application/json",
        "User-Agent": "jobseek-proxy-config/1",
    }
    try:
        async with httpx.AsyncClient(
            headers=headers,
            timeout=httpx.Timeout(20.0),
            follow_redirects=False,
            transport=transport,
        ) as client:
            response = await client.get(
                _API_URL,
                params={"mode": "backbone", "page": 1, "page_size": 100},
            )
    except httpx.HTTPError as exc:
        raise ProxyAuditError(
            f"Webshare backbone proxy list request failed ({type(exc).__name__})"
        ) from exc

    urls: list[str] = []
    for row in _response_rows(response):
        username = row.get("username")
        password = row.get("password")
        port = row.get("port")
        valid = row.get("valid")
        if (
            not isinstance(username, str)
            or not username
            or not isinstance(password, str)
            or not password
            or not isinstance(port, int)
            or not 1 <= port <= 65535
            or valid is not True
        ):
            raise ProxyAuditError("Webshare returned an invalid backbone proxy entry")
        urls.append(
            f"http://{quote(username, safe='')}:{quote(password, safe='')}@p.webshare.io:{port}"
        )
    if not urls:
        raise ProxyAuditError("Webshare returned an empty backbone proxy pool")
    if len(urls) > 64 or len(set(urls)) != len(urls):
        raise ProxyAuditError("Webshare returned an invalid backbone proxy pool")
    return tuple(urls)


def _backup_path(path: Path, now: datetime) -> Path:
    stamp = now.astimezone(UTC).strftime("%Y%m%dT%H%M%S.%fZ")
    return path.with_name(f"{path.name}.backup-{stamp}")


def update_proxy_env_file(
    path: Path,
    *,
    webshare_urls: tuple[str, ...],
    expected_client_ips: tuple[str, ...] = (),
    now: datetime | None = None,
) -> dict[str, object]:
    """Back up and atomically update the pool without returning secrets."""

    expanded_path = path.expanduser()
    if expanded_path.is_symlink():
        raise ProxyAuditError("proxy env path must be an existing regular file")
    path = expanded_path.resolve(strict=True)
    if not path.is_file():
        raise ProxyAuditError("proxy env path must be an existing regular file")
    if (
        not webshare_urls
        or len(webshare_urls) > 64
        or len(set(webshare_urls)) != len(webshare_urls)
    ):
        raise ProxyAuditError("refusing to write an invalid Webshare proxy pool")
    original = path.read_text(encoding="utf-8")
    backup = _backup_path(path, now or datetime.now(UTC))
    if backup.exists():
        raise ProxyAuditError("refusing to overwrite an existing proxy env backup")
    shutil.copyfile(path, backup)
    os.chmod(backup, 0o600)

    values = {
        "PROXY_PROVIDER": "webshare",
        "WEBSHARE_PROXY_URLS": json.dumps(list(webshare_urls), separators=(",", ":")),
        "WEBSHARE_EXPECTED_CLIENT_IPS": json.dumps(
            list(expected_client_ips), separators=(",", ":")
        ),
    }
    found: set[str] = set()
    retired_removed: set[str] = set()
    updated_lines: list[str] = []
    for line in original.splitlines(keepends=True):
        match = _ENV_ASSIGNMENT.match(line)
        key = match.group(1) if match else None
        if key in _RETIRED_KEYS:
            retired_removed.add(key)
            continue
        if key in values:
            if key not in found:
                newline = "\n" if line.endswith("\n") else ""
                updated_lines.append(f"{key}={values[key]}{newline}")
                found.add(key)
            continue
        updated_lines.append(line)
    if updated_lines and not updated_lines[-1].endswith("\n"):
        updated_lines[-1] += "\n"
    for key in _MANAGED_KEYS:
        if key not in found:
            updated_lines.append(f"{key}={values[key]}\n")

    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.writelines(updated_lines)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
        os.chmod(path, 0o600)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise

    return {
        "status": "updated",
        "env_file": str(path),
        "backup_file": str(backup),
        "pool_size": len(webshare_urls),
        "expected_source_count": len(expected_client_ips),
        "retired_keys_removed": sorted(retired_removed),
    }


async def configure_webshare_env(
    *,
    api_key: str,
    env_file: Path,
    transport: httpx.AsyncBaseTransport | None = None,
) -> dict[str, object]:
    urls = await fetch_webshare_backbone_urls(api_key, transport=transport)
    operator_values = dotenv_values(env_file)
    expected_ips: list[str] = []
    for key in ("CRAWLER_BROWSER_IPv4", "CRAWLER_BROWSER_IPv6"):
        value = operator_values.get(key)
        if not value:
            continue
        try:
            expected_ips.append(str(ip_address(value.strip())))
        except ValueError:
            continue
    return update_proxy_env_file(
        env_file,
        webshare_urls=urls,
        expected_client_ips=tuple(sorted(set(expected_ips))),
    )
