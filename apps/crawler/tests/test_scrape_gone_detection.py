"""Tests for the scrape-side delisting path (#2708 + critic-driven
revision).

Three failure classes:

* ``permanent_gone`` (HTTP 404 / 410) — tombstone IMMEDIATELY via
  ``_RECORD_SCRAPE_FAILURE`` with ``permanent_gone=True``.
* ``budget_eligible`` (4xx other than 401, 403, 429) — counts toward
  the existing 3-failure tombstone budget, also via
  ``_RECORD_SCRAPE_FAILURE`` with ``permanent_gone=False``.
* ``transient`` (5xx, timeouts, connect errors, 401 / 403 / 429,
  empty extraction) — backs off via ``_RECORD_SCRAPE_TRANSIENT``,
  NEVER tombstones. The monitor authority remains the only delisting
  decision-maker for these.

The transient class exists because the first iteration of #2708 made
EVERY 3rd consecutive failure tombstone the posting. Critic A2 + C5
flagged the false-positive risk: a 2-hour upstream 5xx incident or a
regex break in an extraction config would mass-tombstone live
postings. The classification protects against both.

403 is in the transient class on purpose: the Avature
archived-JobDetail 403 (original #2708 trigger) is one signal, but
transient WAF challenges (Cloudflare/Datadome/Akamai cold-connect
patterns) also return 403. Tombstoning on any 403 — even after 3
retries — risks too many false positives on still-live postings, so
we leave the Avature class to the monitor authority and accept that
those URLs may stay orphaned briefly.
"""

from __future__ import annotations

import httpx
import pytest

from src.processing.scrape import (
    _is_budget_eligible_failure,
    _is_permanent_gone,
)
from src.queries.scrape import _RECORD_SCRAPE_FAILURE, _RECORD_SCRAPE_TRANSIENT


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


# ─── _is_budget_eligible_failure: 4xx classification ─────────────────


@pytest.mark.parametrize("status", [400, 405, 406, 408, 422])
def test_4xx_excl_401_403_429_are_budget_eligible(status: int) -> None:
    """Most 4xx codes mean the request was rejected for a content reason
    that's likely to repeat — count toward the tombstone budget."""
    assert _is_budget_eligible_failure(_http_error(status, "https://x/y")) is True


@pytest.mark.parametrize("status", [401, 403, 429])
def test_401_403_429_are_NOT_budget_eligible(status: int) -> None:
    """401 = missing session cookie; 403 = could be transient WAF
    (Cloudflare/Datadome/Akamai) or archived posting; 429 = rate limit.
    All three are too ambiguous to count toward the tombstone budget —
    a brief auth/cookie/rate hiccup would otherwise mass-tombstone
    live postings."""
    assert _is_budget_eligible_failure(_http_error(status, "https://x/y")) is False


@pytest.mark.parametrize("status", [404, 410])
def test_permanent_gone_codes_are_NOT_budget_eligible(status: int) -> None:
    """Disjoint classification: 404/410 take the immediate-tombstone
    path via ``permanent_gone=True``, NOT the budget path. The two
    helpers must not double-count the same exception."""
    assert _is_budget_eligible_failure(_http_error(status, "https://x/y")) is False
    assert _is_permanent_gone(_http_error(status, "https://x/y")) is True


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_are_NOT_budget_eligible(status: int) -> None:
    """Server-side failures are by definition transient; never
    contribute to a tombstone."""
    assert _is_budget_eligible_failure(_http_error(status, "https://x/y")) is False


def test_non_http_exceptions_are_NOT_budget_eligible() -> None:
    assert _is_budget_eligible_failure(httpx.ReadTimeout("t/o")) is False
    assert _is_budget_eligible_failure(httpx.ConnectError("refused")) is False
    assert _is_budget_eligible_failure(ValueError("bad")) is False


# ─── _RECORD_SCRAPE_TRANSIENT: SQL shape ─────────────────────────────


def test_transient_sql_does_not_touch_is_active() -> None:
    """The transient path MUST NOT mention is_active anywhere in the
    SET clause — that's the whole point of having a separate path.
    Tombstoning on transient failures was the bug the critics caught."""
    assert "is_active" not in _RECORD_SCRAPE_TRANSIENT


def test_transient_sql_does_bump_failures_and_apply_backoff() -> None:
    """Same backoff semantics as the budget path so we don't hammer
    a host that's mid-incident, but no tombstone trigger."""
    sql = _RECORD_SCRAPE_TRANSIENT
    assert "scrape_failures + 1" in sql
    assert "next_scrape_at" in sql
    assert "leased_until = NULL" in sql


# ─── exception-path: 403 takes transient route, NOT budget ───────────


@pytest.mark.asyncio
async def test_do_one_scrape_routes_403_to_transient_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A 403 from the inner scraper (Avature archived posting,
    Cloudflare WAF, missing session cookie, etc.) MUST take the
    transient SQL path — never the budget-eligible path. Critic A2 +
    C5 explicitly flagged that the prior "always counts toward
    budget" behaviour mass-tombstones live postings during transient
    WAF events."""
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
    assert sql == _RECORD_SCRAPE_TRANSIENT, f"403 must take the transient path, got {sql!r}"
    # Transient SQL takes only posting_id (one positional arg).
    assert args == (posting_id,)


@pytest.mark.asyncio
async def test_do_one_scrape_routes_5xx_to_transient_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An upstream 503 cluster (e.g. Cloudflare incident) must NOT
    feed the tombstone budget."""
    from src.processing import scrape as scrape_mod

    async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise _http_error(503, "https://example.com/jobs/x")

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
        job_posting_id="00000000-0000-0000-0000-000000000503",
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

    sql, _ = executed[0]
    assert sql == _RECORD_SCRAPE_TRANSIENT


@pytest.mark.asyncio
async def test_do_one_scrape_routes_400_to_budget_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """400 / 405 / 422 etc. (true client-side rejections) DO count
    toward the budget — three of them in a row is a strong signal
    something about the URL itself is wrong."""
    from src.processing import scrape as scrape_mod

    async def _boom(*args, **kwargs):  # type: ignore[no-untyped-def]
        raise _http_error(400, "https://example.com/jobs/x")

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
        job_posting_id="00000000-0000-0000-0000-000000000400",
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
    assert sql == _RECORD_SCRAPE_FAILURE
    assert args[1] is False  # budget path, not permanent_gone


@pytest.mark.asyncio
async def test_do_one_scrape_routes_timeout_to_transient_sql(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Network timeouts MUST take the transient path. A 90-minute
    network blip would otherwise burn the 3-failure budget on every
    scheduled scrape and tombstone live postings cohort-wide."""
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
    assert sql == _RECORD_SCRAPE_TRANSIENT
    assert args == ("00000000-0000-0000-0000-000000000999",)
