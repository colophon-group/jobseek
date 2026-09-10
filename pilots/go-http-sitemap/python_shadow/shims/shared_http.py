"""Minimal client selector used by the credential-free benchmark image."""

from __future__ import annotations

from contextlib import asynccontextmanager


@asynccontextmanager
async def client_for(http, config):
    # Benchmark manifests cannot request proxying or disabled TLS validation.
    if config.get("proxy") or config.get("skip_ssl"):
        raise ValueError("benchmark client overrides are forbidden")
    yield http
