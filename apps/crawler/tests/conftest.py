from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

# Set DATABASE_URL before any src module import to prevent config failures
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


@pytest.fixture(autouse=True)
def python_adapter_tests(monkeypatch):
    """Legacy mock HTTP/dispatcher tests explicitly own the Python adapter.

    Native routing tests clear these flags to exercise the production defaults;
    provided Lightpanda/native runtimes remain authoritative regardless.
    """
    monkeypatch.setenv("JSONLD_GO_DETAIL_PERCENT", "0")
    monkeypatch.setenv("SMARTRECRUITERS_GO_DETAIL_PERCENT", "0")
    monkeypatch.setenv("DOM_GO_PARSE_ENABLED", "0")
    monkeypatch.setenv("DOM_GO_HTTP_ENABLED", "0")


@pytest.fixture(scope="session")
def jsonld_binary(tmp_path_factory):
    binary = tmp_path_factory.mktemp("jsonld") / "jsonld-detail-live"
    subprocess.run(
        ["go", "build", "-o", str(binary), "./cmd/live"],
        cwd=Path(__file__).resolve().parents[1] / "go/jsonld-detail",
        check=True,
    )
    return str(binary)
