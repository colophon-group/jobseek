"""URL validation helpers for public Pay-Net job boards."""

from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlparse

_BOARD_HOSTS = frozenset({"pay-netonline.com", "www.pay-netonline.com"})
_BOARD_PATH = "/paynet/applicant/postings.aspx"
_COMPANY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{1,31}$")


def paynet_company_from_url(url: str) -> str | None:
    """Return the exact public company ID from an unfiltered board URL."""

    try:
        parsed = urlparse(url)
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme != "https"
        or (parsed.hostname or "").casefold() not in _BOARD_HOSTS
        or parsed.path.casefold() != _BOARD_PATH
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None

    params = parse_qsl(parsed.query, keep_blank_values=True)
    if len(params) != 1 or params[0][0].casefold() != "co":
        return None
    company_id = params[0][1].strip()
    return company_id if _COMPANY_RE.fullmatch(company_id) else None
