"""Tests for the generalised scrape-failure tombstone path (#2708).

Two changes shipped together:

1. ``_RECORD_SCRAPE_FAILURE`` now also flips ``is_active = false`` when
   the failure budget is exhausted (existing 3-failure threshold) OR
   the caller passes ``permanent_gone = true``. The "gave up but still
   visible" leak across every platform is closed by the budget half;
   404 / 410 short-circuit the budget via the flag.

2. ``_is_permanent_gone`` only returns True for HTTP 404 / 410. No
   host allowlist, no platform-specific 403 handling. Avature's
   archived-JobDetail 403 (the original #2708 trigger) is caught by
   the budget half — three retries, ~90 minutes, then tombstone. The
   user pushed back on the host allowlist as overfitting; the budget
   tombstone catches every variant of the same pattern.
"""

from __future__ import annotations

import httpx
import pytest

from src.processing.scrape import _is_permanent_gone
from src.queries.scrape import _RECORD_SCRAPE_FAILURE


def _http_error(status: int, url: str) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", url)
    response = httpx.Response(status, request=request)
    return httpx.HTTPStatusError(f"{status}", request=request, response=response)


# ─── _is_permanent_gone: universal status codes only ─────────────────


@pytest.mark.parametrize("status", [404, 410])
def test_404_and_410_signal_permanent_gone(status: int) -> None:
    """RFC-defined "this resource is gone" semantics. Universal."""
    assert _is_permanent_gone(_http_error(status, "https://example.com/jobs/x")) is True


@pytest.mark.parametrize(
    "status",
    [400, 401, 403, 408, 429, 500, 502, 503, 504],
)
def test_other_status_codes_are_not_permanent_gone(status: int) -> None:
    """Including 403 — explicitly NOT a permanent-gone signal. The
    failure-budget tombstone (3 retries -> is_active=false) catches
    Avature's archived-JobDetail 403 and any other archived-posting
    403 pattern from any platform. Adding 403 here would over-aggress
    on transient WAF / rate-limit cases."""
    assert _is_permanent_gone(_http_error(status, "https://apply.deloitte.com/x")) is False


def test_non_http_exceptions_are_not_permanent_gone() -> None:
    assert _is_permanent_gone(httpx.ReadTimeout("timed out")) is False
    assert _is_permanent_gone(httpx.ConnectError("refused")) is False
    assert _is_permanent_gone(ValueError("bad")) is False
    assert _is_permanent_gone(RuntimeError("boom")) is False


# ─── SQL shape: budget tombstone + permanent-gone short-circuit ──────


def test_record_failure_sql_takes_permanent_gone_param() -> None:
    """The UPDATE is parameterised on $1 (posting_id) + $2 (permanent_gone bool)."""
    assert "$2::boolean" in _RECORD_SCRAPE_FAILURE


def test_record_failure_sql_tombstones_at_budget_exhaustion() -> None:
    """When ``scrape_failures + 1 >= 3`` (existing give-up point) OR
    permanent_gone is true, both ``is_active`` and ``next_scrape_at``
    must transition. Substring guard against a future refactor that
    drops one of the two."""
    sql = _RECORD_SCRAPE_FAILURE
    # The CASE branches share the same condition: ($2 OR scrape_failures+1>=3)
    cond = "$2::boolean OR scrape_failures + 1 >= 3"
    # Each of: next_scrape_at, is_active, updated_at gets a CASE on this cond.
    assert sql.count(cond) == 3, sql
    assert "is_active         = CASE" in sql
    assert "next_scrape_at    = CASE" in sql


def test_record_failure_sql_increments_scrape_failures_unconditionally() -> None:
    """Every call must bump ``scrape_failures`` regardless of permanent-gone,
    so the count is recoverable via a count-by query if a tombstoned
    posting later gets relisted (monitor's relisted path) and we want to
    see how many failures it accumulated before tombstone."""
    assert "scrape_failures   = scrape_failures + 1" in _RECORD_SCRAPE_FAILURE


def test_record_failure_sql_clears_lease_unconditionally() -> None:
    """Lease must clear on EVERY failure path — otherwise a tombstoned
    posting could keep its lease until the 10-minute lease window
    elapses, holding a worker slot."""
    assert "leased_until = NULL" in _RECORD_SCRAPE_FAILURE


# ─── exception-path integration: failure SQL gets permanent_gone flag ─


@pytest.mark.asyncio
async def test_do_one_scrape_passes_permanent_gone_true_on_404(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end: 404 from the inner scraper -> exception handler ->
    SQL receives permanent_gone=True so the immediate-tombstone branch
    of _RECORD_SCRAPE_FAILURE fires."""
    from src.processing import scrape as scrape_mod

    posting_id = "00000000-0000-0000-0000-000000000404"
    url = "https://anyhost.example/jobs/abc"

    async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise _http_error(404, url)

    monkeypatch.setattr(scrape_mod._batch, "scrape_one", _boom, raising=False)

    executed: list[tuple[str, tuple]] = []

    class _StubConn:
        async def execute(self, sql: str, *args):  # type: ignore[no-untyped-def]
            executed.append((sql, args))

    class _StubPool:
        def acquire(self):  # type: ignore[no-untyped-def]
            class _Ctx:
                async def __aenter__(self_inner) -> _StubConn:
                    return _StubConn()

                async def __aexit__(self_inner, *args):  # type: ignore[no-untyped-def]
                    pass

            return _Ctx()

    item = scrape_mod.ScrapeItem(
        job_posting_id=posting_id,
        url=url,
        board_id="board-1",
        description_r2_hash=None,
    )

    success, _ = await scrape_mod._process_one_scrape(
        item=item,
        pool=_StubPool(),  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        scraper_type="json-ld",
        scraper_config={},
        scrape_step=0,
        scrape_interval=24,
    )

    assert success is False
    assert len(executed) == 1
    sql, args = executed[0]
    assert sql == _RECORD_SCRAPE_FAILURE
    assert args == (posting_id, True), f"expected permanent_gone=True, got {args!r}"


@pytest.mark.asyncio
async def test_do_one_scrape_passes_permanent_gone_false_on_403(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 (Avature archived JobDetail, transient WAF, missing cookie,
    rate-limit) flows through the standard failure path with
    permanent_gone=False. The budget tombstone (after 3 such failures)
    catches the archived case without a host allowlist."""
    from src.processing import scrape as scrape_mod

    posting_id = "00000000-0000-0000-0000-000000000403"
    url = "https://apply.deloitte.com/en_US/careers/JobDetail/Tax-Manager/305820"

    async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise _http_error(403, url)

    monkeypatch.setattr(scrape_mod._batch, "scrape_one", _boom, raising=False)

    executed: list[tuple[str, tuple]] = []

    class _StubConn:
        async def execute(self, sql: str, *args):  # type: ignore[no-untyped-def]
            executed.append((sql, args))

    class _StubPool:
        def acquire(self):  # type: ignore[no-untyped-def]
            class _Ctx:
                async def __aenter__(self_inner) -> _StubConn:
                    return _StubConn()

                async def __aexit__(self_inner, *args):  # type: ignore[no-untyped-def]
                    pass

            return _Ctx()

    item = scrape_mod.ScrapeItem(
        job_posting_id=posting_id,
        url=url,
        board_id="board-1",
        description_r2_hash=None,
    )

    await scrape_mod._process_one_scrape(
        item=item,
        pool=_StubPool(),  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        scraper_type="json-ld",
        scraper_config={},
        scrape_step=0,
        scrape_interval=24,
    )

    assert len(executed) == 1
    sql, args = executed[0]
    assert sql == _RECORD_SCRAPE_FAILURE
    assert args == (posting_id, False), f"expected permanent_gone=False, got {args!r}"


@pytest.mark.asyncio
async def test_do_one_scrape_passes_permanent_gone_false_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network timeouts are transient by definition."""
    from src.processing import scrape as scrape_mod

    async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise httpx.ReadTimeout("timed out")

    monkeypatch.setattr(scrape_mod._batch, "scrape_one", _boom, raising=False)

    executed: list[tuple[str, tuple]] = []

    class _StubConn:
        async def execute(self, sql: str, *args):  # type: ignore[no-untyped-def]
            executed.append((sql, args))

    class _StubPool:
        def acquire(self):  # type: ignore[no-untyped-def]
            class _Ctx:
                async def __aenter__(self_inner) -> _StubConn:
                    return _StubConn()

                async def __aexit__(self_inner, *args):  # type: ignore[no-untyped-def]
                    pass

            return _Ctx()

    item = scrape_mod.ScrapeItem(
        job_posting_id="00000000-0000-0000-0000-000000000999",
        url="https://example.com/jobs/x",
        board_id="board-1",
        description_r2_hash=None,
    )

    await scrape_mod._process_one_scrape(
        item=item,
        pool=_StubPool(),  # type: ignore[arg-type]
        http=None,  # type: ignore[arg-type]
        scraper_type="json-ld",
        scraper_config={},
        scrape_step=0,
        scrape_interval=24,
    )

    sql, args = executed[0]
    assert args[1] is False
