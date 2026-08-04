#!/usr/bin/env python3
"""Mint one short-lived GitHub App installation token into a protected file."""

from __future__ import annotations

import argparse
import base64
import json
import os
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

API_VERSION = "2022-11-28"
MAX_CREDENTIAL_BYTES = 64 * 1024
ID_RE = re.compile(r"^[1-9][0-9]{0,19}$")


class TokenError(RuntimeError):
    """Credential validation or GitHub token minting failed."""


def _read_credential(path: Path, *, max_bytes: int = MAX_CREDENTIAL_BYTES) -> str:
    try:
        stat = path.lstat()
        if path.is_symlink() or not path.is_file() or not 0 < stat.st_size <= max_bytes:
            raise TokenError(f"credential {path.name} is not a bounded regular file")
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TokenError(f"credential {path.name} is unreadable") from exc
    if not value.strip():
        raise TokenError(f"credential {path.name} is empty")
    return value


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def build_app_jwt(app_id: str, private_key: Path, *, now: int | None = None) -> str:
    """Build an RS256 GitHub App JWT without copying the key into argv/env."""
    if ID_RE.fullmatch(app_id) is None:
        raise TokenError("GitHub App ID is invalid")
    issued = int(time.time()) if now is None else now
    header = _base64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode("ascii"))
    payload = _base64url(
        json.dumps(
            {"iat": issued - 60, "exp": issued + 540, "iss": app_id},
            separators=(",", ":"),
        ).encode("ascii")
    )
    signing_input = f"{header}.{payload}".encode("ascii")
    try:
        signed = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", str(private_key)],
            input=signing_input,
            capture_output=True,
            check=False,
            timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise TokenError("OpenSSL JWT signing failed") from exc
    if signed.returncode or not signed.stdout:
        raise TokenError("OpenSSL rejected the GitHub App private key")
    return f"{header}.{payload}.{_base64url(signed.stdout)}"


def mint_installation_token(
    app_id: str,
    installation_id: str,
    private_key: Path,
    *,
    now: int | None = None,
) -> str:
    if ID_RE.fullmatch(installation_id) is None:
        raise TokenError("GitHub App installation ID is invalid")
    jwt = build_app_jwt(app_id, private_key, now=now)
    request = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=b"{}",
        method="POST",
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {jwt}",
            "X-GitHub-Api-Version": API_VERSION,
            "User-Agent": "jobseek-ats-inventory-deployer/1",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310
            payload: Any = json.load(response)
    except urllib.error.HTTPError as exc:
        raise TokenError(f"GitHub App token request returned HTTP {exc.code}") from exc
    except (OSError, ValueError) as exc:
        raise TokenError("GitHub App token request failed") from exc
    token = payload.get("token") if isinstance(payload, dict) else None
    if not isinstance(token, str) or not token or len(token) > 4096 or token.strip() != token:
        raise TokenError("GitHub App token response has an invalid shape")
    return token


def _atomic_write_token(path: Path, token: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(token + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credentials-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = args.credentials_dir
    app_id = _read_credential(root / "github-app-id", max_bytes=64).strip()
    installation_id = _read_credential(
        root / "github-app-installation-id", max_bytes=64
    ).strip()
    private_key = root / "github-app-private-key"
    key_text = _read_credential(private_key)
    if "PRIVATE KEY-----" not in key_text:
        raise TokenError("GitHub App private key is not PEM encoded")
    token = mint_installation_token(app_id, installation_id, private_key)
    _atomic_write_token(args.output, token)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except TokenError as exc:
        print(f"ERROR: {exc}", file=__import__("sys").stderr)
        raise SystemExit(1) from None
