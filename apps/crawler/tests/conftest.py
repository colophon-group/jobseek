from __future__ import annotations

import os

import pytest

# Set DATABASE_URL before any src module import to prevent config failures
os.environ.setdefault("DATABASE_URL", "postgresql://test:test@localhost:5432/test")


@pytest.fixture(autouse=True)
def _isolate_cdp_module_state():
    """Reset ``src.shared.cdp`` module-level state between every test.

    The CDP transport caches sessions in a module-level ``_sessions`` dict
    and reads ``_SHUTDOWN_CLOSE_TIMEOUT_SEC`` as a module global. Tests
    that mutate either (``_set_session_for_test``, the hung-close test)
    are written defensively with their own ``finally:`` cleanup, but a
    missed cleanup would silently leak state into the next test — which
    is exactly the kind of bug that's hard to reproduce locally and
    shows up only in CI on a specific test order.

    The fixture is autouse + cheap so it runs around every test
    regardless of whether the test touches CDP. The import is lazy so
    importing conftest doesn't pull in playwright/httpx for unrelated
    tests.
    """
    yield
    try:
        from src.shared import cdp as _cdp
    except ImportError:  # pragma: no cover — playwright not installed in some envs
        return
    _cdp._sessions.clear()
    _cdp._SHUTDOWN_CLOSE_TIMEOUT_SEC = 5.0
