"""Resource-level TDM header/meta checks (not complete TDMRep coverage).

HTML metadata supersedes headers in TDMRep section 6.7. When only headers
are available, a reservation is rejected immediately; callers may therefore
conservatively skip before reading a later HTML opt-in. Origin-file policies,
other reservation methods and downstream use of stored copies need separate
controls. See issue #10090.

https://w3c.github.io/cg-reports/tdmrep/CG-FINAL-tdmrep-20240510/
"""

from __future__ import annotations

from html.parser import HTMLParser
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


__all__ = [
    "TDMReservedError",
    "check_response",
    "check_browser_response",
]


# Match within the same bounded head excerpt used by fetch helpers. A fast
# keyword check avoids parsing the normal no-signal response.
_META_MAX_CHARS = 65_536


class _ReservationParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.reservation: int | None = None
        self.policy_url: str | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "meta":
            return
        values = dict(attrs)
        name = (values.get("name") or "").lower()
        if name == "tdm-reservation":
            parsed = _parse_reservation_value(values.get("content"))
            if parsed is not None:
                self.reservation = parsed
        elif name == "tdm-policy":
            self.policy_url = values.get("content") or None


def _parse_reservation_value(raw: object) -> int | None:
    """Return the protocol's literal 0/1 values, treating others as unset."""
    if raw is None or not isinstance(raw, str):
        return None
    value = raw.strip()
    return int(value) if value in {"0", "1"} else None


def _extract_meta(body_excerpt: str | None) -> tuple[int | None, str | None]:
    if not body_excerpt:
        return None, None
    excerpt = body_excerpt[:_META_MAX_CHARS]
    if "tdm-" not in excerpt.lower():
        return None, None
    parser = _ReservationParser()
    parser.feed(excerpt)
    return parser.reservation, parser.policy_url


class TDMReservedError(Exception):
    """The upstream resource declared TDM-Reservation: 1.

    Sentinel raised by :func:`check_response` /
    :func:`check_browser_response` when an upstream response signals
    text-and-data-mining opt-out. Distinct from
    :exc:`PaginationFetchError` so the caller's monitor wrapper can
    pattern-match the publisher-policy class separately from the
    transient-failure class — and route it to a graceful skip
    (counter increment, no tombstoning) rather than the failure ramp.

    Attributes:
        url: The URL that emitted the TDM signal.
        source: ``"header"`` (HTTP response header) or ``"meta"`` (HTML
            ``<meta>`` tag in the body excerpt).
        policy_url: The companion ``tdm-policy`` header value if present,
            else ``None``. Captured for logging/observability — the spec
            optionally pairs the reservation flag with a policy URL
            describing licensing terms; we surface it so future operator
            workflows can attempt to satisfy the policy out-of-band.
    """

    def __init__(
        self,
        url: str,
        *,
        source: str,
        policy_url: str | None = None,
    ) -> None:
        self.url = url
        self.source = source
        self.policy_url = policy_url
        detail = f"source={source}"
        if policy_url:
            detail += f" policy={policy_url}"
        super().__init__(f"tdm-reservation=1 declared by {url} ({detail})")


def check_response(
    resp: httpx.Response,
    *,
    body_excerpt: str | None = None,
) -> None:
    """Check available resource signals; parsed HTML metadata wins over headers."""
    parsed = _parse_reservation_value(resp.headers.get("tdm-reservation"))
    policy_url = resp.headers.get("tdm-policy") or None
    meta, meta_policy = _extract_meta(body_excerpt)
    if meta is not None:
        parsed = meta
    if parsed == 1:
        url = str(resp.request.url)
        raise TDMReservedError(
            url,
            source="meta" if meta is not None else "header",
            policy_url=meta_policy or policy_url,
        )


def check_browser_response(
    headers: dict[str, str] | None,
    html: str | None,
    *,
    url: str,
) -> None:
    """Inspect a Playwright-style response for TDM-Reservation signals.

    Symmetric with :func:`check_response` but accepts pre-extracted
    headers + body text (the ``page.evaluate(fetch(...))`` shape used
    by ``dom._fetch_via_page``) since we don't have a real
    :class:`httpx.Response` on the browser path.

    *headers* is treated case-insensitively even though it's a plain
    dict — JS ``Headers`` objects normalize names to lowercase, so the
    common case is already lowercase, but we scan all keys to be
    defensive against callers that pass un-normalized header dicts.
    """
    header_raw: str | None = None
    policy_url: str | None = None
    if headers:
        for k, v in headers.items():
            k_lower = k.lower()
            if k_lower == "tdm-reservation":
                header_raw = v
            elif k_lower == "tdm-policy":
                policy_url = v or None

    parsed = _parse_reservation_value(header_raw)
    meta, meta_policy = _extract_meta(html)
    if meta is not None:
        parsed = meta
    if parsed == 1:
        raise TDMReservedError(
            url,
            source="meta" if meta is not None else "header",
            policy_url=meta_policy or policy_url,
        )
