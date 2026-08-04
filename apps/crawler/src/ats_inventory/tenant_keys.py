"""Small URL/identifier extractors for published ATS job artifacts.

The upstream job schema currently has no universal tenant column.  Keep these
extractors deliberately small: they identify the board, never scrape it.  An
unknown or ambiguous key remains unmatched and therefore ``impact_unknown``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from urllib.parse import parse_qs, urlsplit

_SPACE_RE = re.compile(r"\s+")
_UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
    re.IGNORECASE,
)


def normalize_identity(value: object, *, max_length: int = 500) -> str | None:
    """Normalize a published identifier or company name for exact matching."""

    if value is None:
        return None
    text = _SPACE_RE.sub(" ", str(value).strip()).casefold()
    if not text or len(text) > max_length:
        return None
    return text


def tenant_key(family: str, url: object, ats_id: object = None) -> str | None:
    """Return a family-scoped tenant key shared by board and job URLs."""

    parsed = _safe_url(url)
    if parsed is None:
        return _paylocity_ats_id(family, ats_id)
    extractor = _EXTRACTORS.get(family, _origin)
    return extractor(parsed, ats_id)


def _safe_url(value: object):  # type: ignore[no-untyped-def]
    if not isinstance(value, str) or len(value) > 4096:
        return None
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    return parsed


def _segments(parsed) -> tuple[str, ...]:  # type: ignore[no-untyped-def]
    return tuple(segment.casefold() for segment in parsed.path.split("/") if segment)


def _host(parsed) -> str:  # type: ignore[no-untyped-def]
    hostname = parsed.hostname
    assert hostname is not None
    return hostname.casefold().rstrip(".")


def _origin(parsed, _ats_id: object) -> str:  # type: ignore[no-untyped-def]
    return f"host:{_host(parsed)}"


def _first_path(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    if not parts:
        return None
    return f"hostpath:{_host(parsed)}/{parts[0]}"


def _path_after(label: str) -> Callable[[object, object], str | None]:
    def extract(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
        parts = _segments(parsed)
        try:
            index = parts.index(label.casefold())
            value = parts[index + 1]
        except (ValueError, IndexError):
            return None
        return f"hostpath:{_host(parsed)}/{label.casefold()}/{value}"

    return extract


def _adp(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    query = {key.casefold(): values for key, values in parse_qs(parsed.query).items()}
    cid = normalize_identity((query.get("cid") or [None])[0])
    ccid = normalize_identity((query.get("ccid") or [None])[0])
    if cid is None:
        return None
    return f"adp:{cid}/{ccid or ''}"


def _cornerstone(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    query = {key.casefold(): values for key, values in parse_qs(parsed.query).items()}
    client = normalize_identity((query.get("c") or [None])[0])
    try:
        site = parts[parts.index("careersite") + 1]
    except (ValueError, IndexError):
        site = ""
    if client is None and not site:
        return _origin(parsed, _ats_id)
    return f"cornerstone:{_host(parsed)}/{client or ''}/{site}"


def _dayforce(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    if not parts:
        return None
    portal = parts[1] if len(parts) > 1 and parts[1] not in {"jobs", "job"} else ""
    return f"dayforce:{parts[0]}/{portal}"


def _moka(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    try:
        index = parts.index("social-recruitment")
        tenant = parts[index + 1]
        numeric_id = parts[index + 2]
    except (ValueError, IndexError):
        return _origin(parsed, _ats_id)
    return f"moka:{tenant}/{numeric_id}"


def _oracle(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    query = {key.casefold(): values for key, values in parse_qs(parsed.query).items()}
    site = normalize_identity((query.get("site_number") or [None])[0]) or ""
    try:
        path_site = parts[parts.index("sites") + 1]
    except (ValueError, IndexError):
        path_site = ""
    site = site or path_site
    return f"oracle:{_host(parsed)}/{site}"


def _pageup(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    if not parts:
        return None
    return f"pageup:{parts[0]}"


def _paycom(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    try:
        portal = parts[parts.index("portal") + 1]
    except (ValueError, IndexError):
        return None
    return f"paycom:{portal}"


def _paylocity_ats_id(family: str, ats_id: object) -> str | None:
    if family != "paylocity":
        return None
    identifier = normalize_identity(ats_id)
    if identifier is None:
        return None
    company_id = identifier.split(":", 1)[0]
    if not _UUID_RE.fullmatch(company_id):
        return None
    return f"paylocity:{company_id}"


def _paylocity(parsed, ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    from_id = _paylocity_ats_id("paylocity", ats_id)
    if from_id is not None:
        return from_id
    for part in reversed(_segments(parsed)):
        if _UUID_RE.fullmatch(part):
            return f"paylocity:{part}"
    return None


def _softgarden(parsed, _ats_id: object) -> str:  # type: ignore[no-untyped-def]
    host = _host(parsed)
    for suffix in (".career.softgarden.de", ".softgarden.io"):
        if host.endswith(suffix):
            tenant = host[: -len(suffix)]
            if tenant and "." not in tenant:
                return f"softgarden:{tenant}"
    return f"host:{host}"


def _taleo(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    query = {key.casefold(): values for key, values in parse_qs(parsed.query).items()}
    organisation = normalize_identity((query.get("org") or [None])[0])
    parts = _segments(parsed)
    deployment = parts[0] if parts else ""
    if organisation is None:
        return None
    # cws differs between the search page and individual requisitions, so it
    # is intentionally not part of the tenant identity.
    return f"taleo:{_host(parsed)}/{deployment}/{organisation}"


def _ukg(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    try:
        board_index = parts.index("jobboard")
        board_id = parts[board_index + 1]
    except (ValueError, IndexError):
        return None
    client = parts[0] if parts else ""
    return f"ukg:{client}/{board_id}"


def _workday(parsed, _ats_id: object) -> str | None:  # type: ignore[no-untyped-def]
    parts = _segments(parsed)
    if not parts:
        return None
    return f"workday:{_host(parsed)}/{parts[0]}"


_EXTRACTORS: dict[str, Callable[[object, object], str | None]] = {
    "adp": _adp,
    "ashby": _first_path,
    "cornerstone": _cornerstone,
    "dayforce": _dayforce,
    "gem": _first_path,
    "greenhouse": _first_path,
    "herp": _path_after("v1"),
    "hrmos": _path_after("pages"),
    "jobvite": _first_path,
    "join_com": _path_after("companies"),
    "lever": _first_path,
    "moka": _moka,
    "oracle": _oracle,
    "pageup": _pageup,
    "paycom": _paycom,
    "paylocity": _paylocity,
    "rippling": _first_path,
    "smartrecruiters": _first_path,
    "softgarden": _softgarden,
    "taleo": _taleo,
    "ukg": _ukg,
    "workable": _first_path,
    "workday": _workday,
}
