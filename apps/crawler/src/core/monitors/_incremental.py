"""Generic pagination helpers for incremental monitors.

Pure async helpers — no HTTP client inside, no monitor-specific state. The
caller wraps its own ``fetch_page`` closure (containing auth, retries,
rate-limiting) and passes it along with a ``get_timestamp`` extractor.

Two main patterns:

- ``paginate_until_old``: paginate newest-first, stop when all items on a
  page are older than a watermark. Used for steady-state incremental crawls
  where most of the upstream dataset is already in the DB and only the first
  few pages contain new items.
- ``paginate_all``: full linear pagination. Used for first-run crawls and
  periodic full re-syncs (to catch updates missed by watermark-based early
  termination).

Used by: eightfold/_pcsx (first callers). Designed so amazon, accenture,
oracle_hcm and any other paginating monitor can adopt the same pattern.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

import structlog

log = structlog.get_logger()


class HardPageCap(Exception):
    """Raised when hard_page_cap is reached without hitting the watermark.

    Acts as a safety valve against pathological upstream data (e.g. all
    items have missing timestamps, so the early-termination condition is
    never satisfied).
    """


async def paginate_until_old[T](
    fetch_page: Callable[[int], Awaitable[list[T]]],
    get_timestamp: Callable[[T], int | None],
    *,
    max_watermark: int,
    page_size: int,
    safety_pages: int = 3,
    hard_page_cap: int = 500,
) -> list[T]:
    """Paginate newest-first, stop after all items on a page are ``<= max_watermark``.

    Semantics:

    - Pages are fetched at offsets ``0, page_size, 2*page_size, ...``.
    - For each item, ``get_timestamp(item)`` returns its comparison key
      (e.g. unix ``postedTs``). Items returning ``None`` are treated as
      "newer than any watermark" and are always kept — never silently
      dropped. This makes missing timestamps a data-quality warning, not
      a correctness bug.
    - A page is "all old" iff every item's timestamp is non-None AND
      ``<= max_watermark``.
    - After the first all-old page, keep fetching ``safety_pages`` more
      pages before returning. This tolerates boundary jitter where two
      items on opposite sides of a page boundary share the same timestamp,
      or where a new item is appended mid-pagination and bumps later items
      to an offset we already visited.
    - An empty page ends pagination (end of upstream data).
    - Reaching ``hard_page_cap`` without termination raises ``HardPageCap``.

    Returns the accumulated list of raw items — the caller is responsible
    for mapping to domain objects and deduplicating by a stable key.
    """
    out: list[T] = []
    safety_remaining: int | None = None  # None = haven't seen boundary yet
    for page_index in range(hard_page_cap):
        offset = page_index * page_size
        items = await fetch_page(offset)
        if not items:
            return out
        out.extend(items)

        # Check termination condition: all items on this page are old.
        all_old = all(
            (ts is not None and ts <= max_watermark)
            for ts in (get_timestamp(item) for item in items)
        )
        if all_old:
            if safety_remaining is None:
                safety_remaining = safety_pages
            if safety_remaining <= 0:
                return out
            safety_remaining -= 1
        # If we saw the boundary earlier but this page has new items again,
        # reset the safety counter — the boundary is jittery, keep going.
        elif safety_remaining is not None:
            safety_remaining = None

    # Exhausted page budget without termination.
    log.warning(
        "incremental.hard_page_cap_reached",
        pages=hard_page_cap,
        page_size=page_size,
        items=len(out),
        watermark=max_watermark,
    )
    raise HardPageCap(
        f"paginate_until_old: reached {hard_page_cap} pages "
        f"({hard_page_cap * page_size} items) without crossing watermark"
    )


async def paginate_all[T](
    fetch_page: Callable[[int], Awaitable[list[T]]],
    *,
    page_size: int,
    max_items: int | None = None,
    hard_page_cap: int = 5000,
) -> list[T]:
    """Full linear pagination, newest-first or arbitrary order.

    Fetches pages sequentially until an empty page is returned or
    ``max_items`` / ``hard_page_cap`` is reached. Used for first-run
    crawls and periodic full re-syncs.

    No concurrency within this helper — callers that want concurrency
    should implement it themselves with their own ``asyncio.Semaphore``
    around the ``fetch_page`` closure, because the optimal concurrency
    depends on the upstream's rate-limit profile.
    """
    out: list[T] = []
    for page_index in range(hard_page_cap):
        offset = page_index * page_size
        items = await fetch_page(offset)
        if not items:
            return out
        out.extend(items)
        if max_items is not None and len(out) >= max_items:
            return out[:max_items]

    log.warning(
        "incremental.paginate_all_hard_cap",
        pages=hard_page_cap,
        page_size=page_size,
        items=len(out),
    )
    return out
