"""Validated alternate read URLs for public pages.

Some public career pages block the crawler network while an equivalent,
read-only representation remains reachable through a rendering gateway.  A
fetch URL transform keeps the official URL as the posting identity while
changing only the URL used for the HTTP read.
"""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_MAX_PATTERN_LENGTH = 2_048
_MAX_REPLACEMENT_LENGTH = 4_096
_MAX_URL_LENGTH = 8_192


def transformed_fetch_url(url: str, value: object, *, owner: str) -> str:
    """Return a validated alternate URL produced by one regex rewrite.

    The transform is deliberately fail-closed: a configured pattern must
    match exactly once and must produce an absolute public HTTP(S) URL.  This
    prevents a stale gateway rule from silently falling back to a blocked
    origin or rewriting an unexpected URL.
    """

    if value is None:
        return url
    if not isinstance(value, dict) or set(value) != {"find", "replace"}:
        raise ValueError(f"{owner} fetch_url_transform must contain only find and replace")

    find = value.get("find")
    replace = value.get("replace")
    if (
        not isinstance(find, str)
        or not find
        or len(find) > _MAX_PATTERN_LENGTH
        or "\x00" in find
    ):
        raise ValueError(f"{owner} fetch_url_transform.find must be non-empty bounded text")
    if (
        not isinstance(replace, str)
        or not replace
        or len(replace) > _MAX_REPLACEMENT_LENGTH
        or "\x00" in replace
    ):
        raise ValueError(f"{owner} fetch_url_transform.replace must be non-empty bounded text")

    try:
        transformed, count = re.compile(find).subn(replace, url)
    except re.error as exc:
        raise ValueError(f"{owner} fetch_url_transform is invalid: {exc}") from exc
    if count != 1:
        raise ValueError(f"{owner} fetch_url_transform must rewrite the source URL exactly once")
    if len(transformed) > _MAX_URL_LENGTH or "\x00" in transformed:
        raise ValueError(f"{owner} fetch_url_transform produced an invalid URL")

    parsed = urlsplit(transformed)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError(f"{owner} fetch_url_transform must produce a public HTTP(S) URL")
    return transformed
