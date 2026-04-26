"""POST to the web-side typeahead-cache invalidation endpoint.

Called after a successful ``crawler sync`` so the web app's *-suggest:*
caches don't serve stale taxonomy autocompletes for up to the cache TTL
(currently 1h) after a CSV change. The endpoint
(``apps/web/app/api/internal/invalidate-typeahead/route.ts``) owns the
list of prefixes to sweep — the crawler only triggers the sweep, doesn't
specify the keys, so this side stays decoupled from the web cache key
shape.
"""

from __future__ import annotations

import os

import httpx
import structlog

log = structlog.get_logger()


async def notify_invalidate_typeahead(http: httpx.AsyncClient) -> bool:
    """POST to the typeahead-invalidation endpoint. Returns True on 2xx.

    Uses two env vars:

    - ``WEB_INVALIDATE_URL``: full URL to the endpoint
      (e.g. ``https://jseek.co/api/internal/invalidate-typeahead``).
    - ``INTERNAL_REVALIDATE_TOKEN``: bearer token shared with the web app.

    Logs and returns False (does NOT raise) on any failure. The TTL on the
    suggest cache (1h) is the backstop — a missed invalidation just means
    a longer staleness window, not a correctness issue.
    """
    url = os.environ.get("WEB_INVALIDATE_URL")
    token = os.environ.get("INTERNAL_REVALIDATE_TOKEN")
    if not url or not token:
        log.info(
            "invalidate.typeahead.skipped",
            reason="WEB_INVALIDATE_URL or INTERNAL_REVALIDATE_TOKEN unset",
        )
        return False

    try:
        resp = await http.post(
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=10.0,
        )
    except httpx.HTTPError as e:
        log.warning("invalidate.typeahead.request_failed", error=str(e))
        return False

    if resp.status_code >= 400:
        log.warning(
            "invalidate.typeahead.bad_status",
            status=resp.status_code,
            body=resp.text[:200],
        )
        return False

    try:
        data = resp.json()
        log.info(
            "invalidate.typeahead.done",
            total=data.get("total"),
            deleted=data.get("deleted"),
        )
    except ValueError:
        log.info("invalidate.typeahead.done")
    return True
