"""TDM-Reservation respect — W3C Text-and-Data-Mining opt-out (#2842).

Every shared fetch helper in :mod:`src.shared.http_retry` and the
per-monitor retry helpers (workday, lever, smartrecruiters, hireology,
api_sniffer, accenture, umantis, dom-browser-page) inspect each upstream
response for two TDM-Reservation signals:

- HTTP response header ``tdm-reservation: 1`` (case-insensitive name).
- HTML ``<meta name="tdm-reservation" content="1">`` in the response
  body — fallback for static hosts/CDNs that can't set headers.

A ``=1`` value (per W3C TDM Reservation Protocol §3.1, integer values
``0``/``1`` are conformant) raises :exc:`TDMReservedError`. The exception
is **not** retried — it's a publisher policy declaration, not a transient
failure — and propagates up to ``_process_one_board_streaming`` where it
is caught, logged, and counter-incremented separately from the
``_RECORD_FAILURE`` path. The board run is treated as a clean skip
(no tombstoning, no consecutive_failures bump).

Spec: https://www.w3.org/TR/tdmrep/. Header values are integer ``0``
(allowed) or ``1`` (reserved/disallowed). Values outside the spec
(non-integer strings, multi-value comma-separated lists, ``true``/``false``
strings) are treated leniently as **absent** rather than reserved —
defensive parsing per the spec's "implementation-defined" clause for
non-conformant values.

Blast radius (issue #2842 comment): 0 of 4709 active boards / 0 of 881
distinct origins emit any TDM-Reservation signal as of 2026-05-09. The
hook is enforce-direct (no shadow-mode flag) — there is nothing to
shadow. The check exists so that future emissions on currently-loadbearing
hosts (e.g. ``boards.greenhouse.io``, ``jobs.ashbyhq.com``,
``jobs.lever.co`` — any one of which would skip thousands of boards at
once) are honored from day one of emission, without waiting for a code
deploy.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx


__all__ = [
    "TDMReservedError",
    "check_response",
    "check_browser_response",
]


# ``<meta name="tdm-reservation" content="1">`` — attribute order is not
# fixed by HTML spec, so the regex matches both
# ``name="tdm-reservation" ... content="1"`` and the reverse. Quotes may
# be double, single, or absent; the pattern accepts all three. Whitespace
# inside the attribute value is conservatively rejected — the spec only
# admits the bare integer ``1``.
#
# Anchored with ``<meta`` and ``>`` to avoid spuriously matching inside a
# script/style payload that happens to contain the substring. The body
# excerpt we receive in :func:`check_response` is bounded by
# ``fetch_with_retry``'s ``max_chars`` truncation — typically 64 KB or
# less — so the regex cost is negligible.
_META_TDM_RESERVATION_RE = re.compile(
    r"""<meta\s+
        (?:
            name\s*=\s*["']?tdm-reservation["']?\s+
            content\s*=\s*["']?1["']?
        |
            content\s*=\s*["']?1["']?\s+
            name\s*=\s*["']?tdm-reservation["']?
        )
        \s*/?\s*>""",
    re.IGNORECASE | re.VERBOSE,
)


def _parse_reservation_value(raw: object) -> int | None:
    """Parse a ``tdm-reservation`` value. Returns ``0``/``1`` or ``None``.

    Defensive (issue #2842): values that aren't strict integers are
    treated as absent. The spec (W3C TDMRep §3.1) admits ``0`` and ``1``
    as conformant; anything else is implementation-defined. We choose the
    permissive interpretation (no enforcement on garbage) over the
    conservative one (enforce on garbage) because false positives here
    cost real boards while the spec gives us no obligation either way.

    The ``raw`` parameter is typed ``object`` rather than ``str | None``
    because callers may pass a value extracted from a partial-mock
    response in tests, where ``headers.get(...)`` can return a
    ``MagicMock``. We treat any non-string as absent rather than
    crash-loud — the production callers (httpx + Playwright) always
    yield strings on real header lookups.
    """
    if raw is None or not isinstance(raw, str):
        return None
    s = raw.strip()
    if not s:
        return None
    # Reject multi-value headers (``"0, 1"``, ``"1; foo"``) outright —
    # the spec does not define multi-value semantics, and a comma in the
    # value is a malformed publisher signal we shouldn't second-guess.
    if "," in s or ";" in s:
        return None
    try:
        v = int(s)
    except ValueError:
        return None
    if v == 0 or v == 1:
        return v
    return None


def _extract_meta_reservation(body_excerpt: str | None) -> bool:
    """Return ``True`` iff *body_excerpt* contains ``<meta tdm-reservation=1>``.

    Lightweight regex scan rather than a full HTML parse — the cost
    matters because every fetched page on every paginating monitor goes
    through this. Anchored at ``<meta`` to avoid script-body false
    positives. ``content="0"`` is *not* matched here (an explicit opt-in
    declaration shouldn't accidentally trigger enforcement).
    """
    if not body_excerpt:
        return False
    return _META_TDM_RESERVATION_RE.search(body_excerpt) is not None


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
    """Inspect an httpx response for TDM-Reservation signals.

    Raises:
        :class:`TDMReservedError` if the upstream declares
        ``tdm-reservation: 1`` via header or (when *body_excerpt* is
        provided and the header is absent) via HTML meta tag.

    Returns:
        ``None`` (no-op) when no reservation is declared, including the
        case where the header explicitly says ``0`` (which takes
        precedence over any conflicting body meta — the header is the
        canonical signal per spec §3.2).

    Header lookup is case-insensitive (httpx handles this natively).
    The ``tdm-policy`` companion header is captured into the raised
    exception's ``policy_url`` attribute when present, but absence does
    not affect the enforcement decision.
    """
    header_raw = resp.headers.get("tdm-reservation")
    parsed = _parse_reservation_value(header_raw)
    policy_url = resp.headers.get("tdm-policy") or None
    url = str(resp.request.url) if resp.request is not None else "<unknown>"

    if parsed == 1:
        raise TDMReservedError(url, source="header", policy_url=policy_url)
    if parsed == 0:
        # Explicit opt-in. Header is canonical — don't fall through to
        # the body-meta scan. Per the issue spec: "Header takes precedence
        # over meta (= 0 wins even if meta = 1, per spec since header is
        # canonical)".
        return
    # Header is absent (or non-integer / multi-value gibberish that we
    # parsed as absent). Fall through to the body-meta scan if we have
    # a body excerpt to scan.
    if _extract_meta_reservation(body_excerpt):
        raise TDMReservedError(url, source="meta", policy_url=policy_url)


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
    if parsed == 1:
        raise TDMReservedError(url, source="header", policy_url=policy_url)
    if parsed == 0:
        return
    if _extract_meta_reservation(html):
        raise TDMReservedError(url, source="meta", policy_url=policy_url)
