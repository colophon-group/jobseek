"""HTTP fetch with bounded retries and explicit failure semantics.

Used by paginating monitors (#2722) to distinguish *transient* errors
(retry, then propagate) from *legitimate* end-of-pagination signals.

The 2026-04-26 NHS spike (#2722) showed why this matters: the dom
monitor's ``_paginate_urls`` treated any falsy fetch result as
"end of pagination" and silently truncated the URL set, then
``_MARK_GONE_BY_TIMESTAMP`` tombstoned the missing URLs. With
:func:`fetch_with_retry`, transient 5xx / 429 / network errors are
retried and then raise :exc:`PaginationFetchError` — propagating up
to ``_process_one_board_streaming``'s generic ``except Exception``
which records the run as a failure rather than a partial success.
"""

from __future__ import annotations

import asyncio
import random
from typing import TYPE_CHECKING

import structlog

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()


class PaginationFetchError(Exception):
    """A page fetch exhausted its retry budget on transient errors.

    Pagination monitors must propagate this rather than treating it as
    end-of-pagination — silently truncating the URL set is the bug
    from the 2026-04-26 NHS spike (#2722). The crawler's success path
    only fires ``_MARK_GONE_BY_TIMESTAMP`` when the monitor returns
    cleanly; raising here routes the run through ``_RECORD_FAILURE``
    instead (consecutive_failures++ with exponential backoff).
    """

    def __init__(
        self,
        url: str,
        attempts: int,
        *,
        last_status: int | None = None,
        last_error: str | None = None,
    ) -> None:
        self.url = url
        self.attempts = attempts
        self.last_status = last_status
        self.last_error = last_error
        detail = f"status={last_status}" if last_status is not None else f"error={last_error}"
        super().__init__(f"pagination fetch failed for {url} after {attempts} attempts ({detail})")


# Statuses we retry on (transient by convention).
_RETRYABLE_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})

# Statuses that mean "no content here, but the request was understood".
# Pagination treats these as legitimate end-of-pagination signals so the
# monitor returns its accumulated set as a successful run.
_END_OF_PAGINATION_STATUSES = frozenset({404, 410})


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = 3,
    base_delay: float = 0.5,
    max_chars: int = 500_000,
    timeout: float | None = None,
    headers: dict | None = None,
) -> str | None:
    """Fetch ``url`` and return its text body.

    Returns:
        - ``str`` (truncated to ``max_chars``) on HTTP 200.
        - ``None`` on HTTP 404 / 410 (legitimate end-of-pagination), or
          any other non-retryable 4xx (caller should treat as "no more
          content here" — same semantic as the prior tolerant
          ``fetch_page_text``).

    Raises:
        :exc:`PaginationFetchError` when *retries* attempts have all
        hit a retryable failure (transient 5xx, 429, timeout, network
        error). The caller is expected to propagate so
        ``_process_one_board_streaming`` records the run as a failure
        rather than a partial success.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` between
    retries — exponential with full jitter. Defaults to ~0.5–1s,
    1–2s, 2–4s for 3 attempts.
    """
    last_exc: BaseException | None = None
    last_status: int | None = None

    for attempt in range(retries):
        try:
            resp = await client.get(
                url,
                follow_redirects=True,
                timeout=timeout,
                headers=headers,
            )
            last_status = resp.status_code
            if resp.status_code == 200:
                return resp.text[:max_chars]
            if resp.status_code in _END_OF_PAGINATION_STATUSES:
                return None
            if resp.status_code in _RETRYABLE_STATUSES:
                last_exc = None  # status-only, no exception
            else:
                # Other 4xx (auth, forbidden, bad-request, etc.) — not
                # transient, but also not "end of pagination" in the
                # canonical sense. Mirror the prior lenient
                # ``fetch_page_text`` behaviour and return None so the
                # caller stops paginating without flagging the run as a
                # failure. Logged so anomalies are observable.
                log.warning(
                    "http_retry.non_retryable_status",
                    url=url,
                    status=resp.status_code,
                )
                return None
        except Exception as exc:  # httpx.TimeoutException, NetworkError, etc.
            last_exc = exc
            last_status = None

        if attempt < retries - 1:
            # Exponential backoff with full jitter.
            delay = base_delay * (2**attempt) * (0.5 + random.random())
            log.info(
                "http_retry.backoff",
                url=url,
                attempt=attempt + 1,
                delay_s=round(delay, 2),
                last_status=last_status,
                last_error=type(last_exc).__name__ if last_exc else None,
            )
            await asyncio.sleep(delay)

    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
    )
