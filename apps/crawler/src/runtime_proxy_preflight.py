"""Validate deployment proxy secrets without disclosing their values."""

from __future__ import annotations

import sys

import httpx


def main() -> int:
    try:
        # Import inside the guarded boundary because pydantic-settings parses
        # complex environment values while importing ``src.config``.
        from src.config import settings

        pool_entries = len(settings.webshare_proxy_urls)
        if settings.proxy_provider == "webshare":
            if pool_entries:
                mode = "backbone_pool"
                candidates = settings.webshare_proxy_urls
            elif settings.webshare_proxy_url:
                mode = "legacy_direct"
                candidates = [settings.webshare_proxy_url]
            else:
                raise ValueError("selected proxy provider has no endpoint")
            for candidate in candidates:
                httpx.Proxy(candidate)
        else:
            mode = "disabled"
    except Exception as exc:
        # Exception details can include input values. Emit only the bounded
        # class name so invalid secrets remain masked in Actions logs.
        print(
            f"Runtime proxy configuration invalid ({type(exc).__name__})",
            file=sys.stderr,
        )
        return 1

    print(f"Runtime proxy configuration valid: mode={mode}, pool_entries={pool_entries}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
