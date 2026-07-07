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

__all__ = [
    "END_OF_PAGINATION_STATUSES",
    "PaginationFetchError",
    "_RETRYABLE_STATUSES",
    "fetch_with_retry",
    "is_retryable_status",
]


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


# Explicitly-retryable non-5xx statuses. Anything in the 500–599 range is
# also retried — see ``is_retryable_status`` — covering Cloudflare's 520-526
# / 530 origin-error codes that real jobs sites behind CDNs commonly emit.
_EXTRA_RETRYABLE_STATUSES = frozenset({408, 425, 429})

# Auth/forbidden statuses that some callers want treated as transient. By
# default these route through the "non-retryable 4xx → return None" path,
# matching the prior tolerant ``fetch_page_text`` behaviour: a 403 from a
# pagination sub-page (Indeed company pages, etc.) is a permanent block on
# *that* URL, so dropping the page and continuing is the right move.
#
# Sitemap-shard fetchers behave differently — see #2994. mchire's
# (Phenom-managed AWS ELB) 403s a fraction of child shards on every cycle
# from a single egress IP, even though the sitemap *index* itself returns
# 200. Treating those 403s as "shard is gone" silently drops thousands of
# URLs and tombstones real postings via ``_MARK_GONE_BY_TIMESTAMP`` — the
# same bug class as #2722 / #2974, just on the 4xx leg of the WAF instead
# of the 5xx leg. ``_fetch_child_xml`` opts in via ``transient_403=True``.
_TRANSIENT_403_STATUSES = frozenset({401, 403})

# Statuses that mean "no content here, but the request was understood".
# Pagination treats these as legitimate end-of-pagination signals so the
# monitor returns its accumulated set as a successful run. Public so
# alternate-transport pagination helpers (``dom.py``'s
# ``_fetch_via_page`` for ``pagination.browser=true``) can match the
# httpx classification without re-encoding the constant.
END_OF_PAGINATION_STATUSES = frozenset({404, 410})


def is_retryable_status(status: int) -> bool:
    """Whether *status* should be retried by a pagination fetcher.

    Retried: any 5xx (Cloudflare 520-526/530 included) plus 408 (request
    timeout), 425 (too early), 429 (rate-limited). Returning ``True``
    here will, on retry exhaustion, surface as ``PaginationFetchError``.

    Public so that alternate transports (Playwright ``page.evaluate``
    fetches in ``dom.py``) classify identically to the httpx path —
    keeping operator-facing semantics symmetric across pagination paths.
    """
    if 500 <= status < 600:
        return True
    return status in _EXTRA_RETRYABLE_STATUSES


# Backward-compatible alias for tests / introspection. Reflects the
# union of explicit + range-based retryable statuses for documentation
# purposes; the real check uses ``is_retryable_status``.
_RETRYABLE_STATUSES = _EXTRA_RETRYABLE_STATUSES | frozenset(range(500, 600))


async def fetch_with_retry(
    client: httpx.AsyncClient,
    url: str,
    *,
    retries: int = 3,
    base_delay: float = 0.5,
    max_chars: int = 500_000,
    timeout: float | None = None,
    headers: dict | None = None,
    transient_403: bool = False,
) -> str | None:
    """Fetch ``url`` and return its text body.

    Returns:
        - ``str`` (truncated to ``max_chars``) on HTTP 200 with a
          **non-empty** body.
        - ``None`` on HTTP 404 / 410 (legitimate end-of-pagination), or
          any other non-retryable 4xx (caller should treat as "no more
          content here" — same semantic as the prior tolerant
          ``fetch_page_text``).

    Raises:
        :exc:`PaginationFetchError` when *retries* attempts have all
        hit a retryable failure (transient 5xx, 429, timeout, network
        error, **or 200-with-empty-body**). The caller is expected to
        propagate so ``_process_one_board_streaming`` records the run
        as a failure rather than a partial success.

    Empty-200 handling (#2739). A 200 with an empty body is treated
    as transient — retry, then raise. Real career pages always have
    at least a skeleton HTML body; an empty 200 is an anti-bot
    challenge, partial CDN response, or origin glitch. Returning
    ``""`` (which is falsy) caused ``_paginate_urls`` and other
    callers to treat it as legitimate end-of-pagination and tombstone
    the un-fetched tail via ``_MARK_GONE_BY_TIMESTAMP`` — the same
    silent-truncation shape as the bug fixed in #2722 / #2737, just
    on a different input (empty body rather than 5xx).

    Transient-403 opt-in (#2994). When ``transient_403=True``, 401 and
    403 statuses are treated as retryable and surface as
    ``PaginationFetchError`` after retry exhaustion instead of
    returning ``None``. This is for callers fetching child shards from
    an index that succeeded — a 403 there is empirically a WAF /
    anti-bot block on the egress IP (mchire's awselb/2.0 returned 403
    on 18-44%% of phenom-monitor child shards from the production
    Webshare egress while the index returned 200), not the shard being
    permanently removed. Default ``False`` preserves the dom-monitor
    pagination contract where a 403 on Indeed company pages means
    "this URL is permanently blocked, drop it" — silently turning that
    into a hard failure would 5-strike-disable boards on first
    encounter.

    Backoff: ``base_delay × 2^attempt × (0.5 + random())`` between
    retries — exponential with full jitter. Defaults to ~0.5–1s,
    1–2s, 2–4s for 3 attempts.
    """
    # Imported lazily inside the loop to keep the hot-path import graph
    # narrow — :mod:`src.shared.tdm` re-exports the sentinel exception
    # type so tests can ``except TDMReservedError`` without importing
    # http_retry first. The check itself runs only on 200 responses.
    # Retry observability (#3210). Imported lazily so test environments
    # that stub out ``src.metrics`` (or import this module without the
    # full crawler runtime) don't pay the cost on the happy path. The
    # counter is bumped at each retry-causing site, plus once on
    # ``recovered`` / ``exhausted``; ``host`` is bounded cardinality.
    from src.metrics import (
        http_retry_attempts_total,
        http_retry_empty_200_total,
        http_retry_host,
        http_retry_transient_403_total,
    )
    from src.shared.tdm import check_response as _tdm_check

    host = http_retry_host(url)

    last_exc: BaseException | None = None
    last_status: int | None = None
    retried = False  # observability: did we burn at least one retry?

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
                text = resp.text
                if text:
                    # TDM-Reservation respect (#2842). Inspect the response
                    # for the W3C TDM opt-out signal before returning the
                    # body. ``TDMReservedError`` is *not* retried — it's a
                    # publisher policy declaration, not a transient
                    # failure — and propagates up the call stack to the
                    # monitor wrapper in ``processing/board.py``.
                    _tdm_check(resp, body_excerpt=text)
                    if retried:
                        http_retry_attempts_total.labels(host=host, outcome="recovered").inc()
                    return text[:max_chars]
                # Empty-200 (#2739): treat as transient, fall through
                # to backoff. ``last_exc`` stays None so retry-budget
                # exhaustion raises with last_status=200 and a
                # null last_error, which an operator pattern-matches
                # in logs as the empty-body signal.
                last_exc = None
                http_retry_empty_200_total.labels(host=host).inc()
                http_retry_attempts_total.labels(host=host, outcome="retry").inc()
                retried = True
                log.info(
                    "http_retry.empty_200",
                    url=url,
                    attempt=attempt + 1,
                )
            elif resp.status_code in END_OF_PAGINATION_STATUSES:
                return None
            elif is_retryable_status(resp.status_code):
                last_exc = None  # status-only, no exception
                http_retry_attempts_total.labels(host=host, outcome="retry").inc()
                retried = True
            elif transient_403 and resp.status_code in _TRANSIENT_403_STATUSES:
                # WAF/anti-bot block on a sitemap-shard fetch (#2994). The
                # caller (``_fetch_child_xml``) opted in: retry through
                # the budget, then raise so the cycle records as a
                # failure rather than silently dropping this shard's
                # URLs. ``last_exc`` stays None so the raised
                # PaginationFetchError carries ``last_status=403`` for
                # operator log pattern-matching.
                last_exc = None
                http_retry_transient_403_total.labels(host=host).inc()
                http_retry_attempts_total.labels(host=host, outcome="retry").inc()
                retried = True
                log.info(
                    "http_retry.transient_403",
                    url=url,
                    status=resp.status_code,
                    attempt=attempt + 1,
                )
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
            # TDM-Reservation (#2842) is a publisher policy decision, not
            # a transient failure — never retry, propagate to the monitor
            # wrapper for graceful skip handling.
            from src.shared.tdm import TDMReservedError as _TDMReservedError

            if isinstance(exc, _TDMReservedError):
                raise
            last_exc = exc
            last_status = None
            http_retry_attempts_total.labels(host=host, outcome="retry").inc()
            retried = True

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

    http_retry_attempts_total.labels(host=host, outcome="exhausted").inc()
    raise PaginationFetchError(
        url,
        attempts=retries,
        last_status=last_status,
        last_error=type(last_exc).__name__ if last_exc else None,
    )
