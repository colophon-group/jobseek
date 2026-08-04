"""Pure identity helpers for PageUp public career sites.

PageUp hosts many tenants on one shared origin.  Keep the strict URL and
first-party page-marker rules here so runtime discovery, ``ws``, and scheduled
probes agree on the same board identity.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qsl, quote, unquote, urlencode, urlparse

_HOST = "careers.pageuppeople.com"
_INSTANCE_RE = re.compile(r"[1-9]\d{0,8}")
_SOURCE_POINTER_RE = re.compile(r"[a-z][a-z0-9-]{0,15}")
_LOCALE_RE = re.compile(r"[a-z]{2,3}(?:-[a-z0-9]{2,8})*")
_JOB_ID_RE = re.compile(r"[1-9]\d{0,18}")
_SOURCE_ASSIGNMENT_RE = re.compile(r"\bPU\.Jobs\.source\s*=\s*")


def normalize_pageup_instance(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        text = str(value)
    elif isinstance(value, str):
        text = value.strip()
    else:
        return None
    return int(text) if _INSTANCE_RE.fullmatch(text) is not None else None


def normalize_pageup_source_pointer(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if _SOURCE_POINTER_RE.fullmatch(normalized) is not None else None


def normalize_pageup_locale(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip().casefold()
    return normalized if _LOCALE_RE.fullmatch(normalized) is not None else None


@dataclass(frozen=True, slots=True)
class PageUpBoard:
    instance: int
    source_pointer: str
    locale: str

    @property
    def listing_url(self) -> str:
        return f"https://{_HOST}/{self.instance}/{self.source_pointer}/{self.locale}"

    def page_url(self, page: int, *, page_size: int) -> str:
        if isinstance(page, bool) or not isinstance(page, int) or page < 1:
            raise ValueError(f"Invalid PageUp page: {page!r}")
        if isinstance(page_size, bool) or not isinstance(page_size, int) or page_size < 1:
            raise ValueError(f"Invalid PageUp page size: {page_size!r}")
        query = urlencode({"page": page, "page-items": page_size})
        return f"{self.listing_url}/listing/?{query}"

    def job_url(self, job_id: int | str, slug: str) -> str:
        normalized_id = _job_id(job_id)
        normalized_slug = _job_slug(slug)
        if normalized_id is None or normalized_slug is None:
            raise ValueError(f"Invalid PageUp job identity: {job_id!r}, {slug!r}")
        encoded_slug = quote(normalized_slug, safe="-_")
        return f"{self.listing_url}/job/{normalized_id}/{encoded_slug}"


def _job_id(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    text = str(value) if isinstance(value, int) else value
    if not isinstance(text, str) or _JOB_ID_RE.fullmatch(text) is None:
        return None
    return int(text)


def _job_slug(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        normalized = unquote(value).casefold()
    except (UnicodeDecodeError, ValueError):
        return None
    if not 1 <= len(normalized) <= 200 or not normalized[0].isalnum():
        return None
    return normalized if all(ch.isalnum() or ch in "-_" for ch in normalized) else None


def _safe_parts(url: str):
    if not isinstance(url, str) or len(url) > 4096:
        return None
    try:
        parsed = urlparse(unescape(url))
        port = parsed.port
    except (TypeError, ValueError):
        return None
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != _HOST
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.fragment
    ):
        return None
    return parsed


def _board_from_segments(segments: list[str]) -> PageUpBoard | None:
    if len(segments) < 3:
        return None
    instance = normalize_pageup_instance(segments[0])
    source_pointer = normalize_pageup_source_pointer(segments[1])
    locale = normalize_pageup_locale(segments[2])
    if instance is None or source_pointer is None or locale is None:
        return None
    return PageUpBoard(instance, source_pointer, locale)


def pageup_board_from_url(url: str) -> PageUpBoard | None:
    """Parse an unfiltered listing, pagination, or detail URL."""

    parsed = _safe_parts(url)
    if parsed is None:
        return None
    segments = [part for part in parsed.path.split("/") if part]
    board = _board_from_segments(segments)
    if board is None:
        return None

    try:
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=4)
    except ValueError:
        return None
    params: dict[str, str] = {}
    for name, value in pairs:
        if name in params:
            return None
        params[name] = value

    if len(segments) == 3:
        return board if not params else None
    if len(segments) == 4 and segments[3].casefold() == "listing":
        if params and set(params) != {"page", "page-items"}:
            return None
        if any(not value.isdigit() or int(value) < 1 for value in params.values()):
            return None
        return board
    if (
        len(segments) == 6
        and segments[3].casefold() == "job"
        and _job_id(segments[4]) is not None
        and _job_slug(segments[5]) is not None
        and not params
    ):
        return board
    return None


def pageup_board_from_metadata(metadata: Mapping[str, object]) -> PageUpBoard | None:
    """Resolve configured identity, validating an optional listing URL."""

    instance = normalize_pageup_instance(metadata.get("instance"))
    source_pointer = normalize_pageup_source_pointer(metadata.get("source_pointer"))
    locale = normalize_pageup_locale(metadata.get("locale"))
    explicit_values = (instance, source_pointer, locale)
    has_explicit = any(key in metadata for key in ("instance", "source_pointer", "locale"))
    if has_explicit and any(value is None for value in explicit_values):
        return None
    if instance is not None and source_pointer is not None and locale is not None:
        explicit = PageUpBoard(instance, source_pointer, locale)
    else:
        explicit = None

    if "listing_url" not in metadata:
        return explicit
    listing_url = metadata.get("listing_url")
    if not isinstance(listing_url, str):
        return None
    listed = pageup_board_from_url(listing_url)
    if listed is None or (explicit is not None and listed != explicit):
        return None
    return listed


def pageup_job_identity(url: str, board: PageUpBoard) -> tuple[int, str] | None:
    if pageup_board_from_url(url) != board:
        return None
    parsed = _safe_parts(url)
    if parsed is None:
        return None
    segments = [part for part in parsed.path.split("/") if part]
    if len(segments) != 6 or segments[3].casefold() != "job":
        return None
    job_id = _job_id(segments[4])
    slug = _job_slug(segments[5])
    return (job_id, slug) if job_id is not None and slug is not None else None


def pageup_pagination_identity(
    url: str,
    board: PageUpBoard,
) -> tuple[int, int] | None:
    if pageup_board_from_url(url) != board:
        return None
    parsed = _safe_parts(url)
    if parsed is None:
        return None
    segments = [part for part in parsed.path.split("/") if part]
    if len(segments) != 4 or segments[3].casefold() != "listing":
        return None
    params = dict(parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=4))
    if set(params) != {"page", "page-items"}:
        return None
    return int(params["page"]), int(params["page-items"])


def pageup_listing_boards_from_html(document: str) -> frozenset[PageUpBoard]:
    """Return every valid board asserted by ``PU.Jobs.source`` JSON."""

    decoder = json.JSONDecoder()
    boards: set[PageUpBoard] = set()
    for match in _SOURCE_ASSIGNMENT_RE.finditer(document):
        try:
            source, _end = decoder.raw_decode(document[match.end() :])
        except json.JSONDecodeError:
            continue
        if not isinstance(source, dict):
            continue
        instance = normalize_pageup_instance(source.get("instId"))
        pointer = normalize_pageup_source_pointer(source.get("sourcePointer"))
        locale = normalize_pageup_locale(source.get("language"))
        base = source.get("baseDomain")
        action = source.get("action")
        if (
            instance is None
            or pointer is None
            or locale is None
            or base != f"https://{_HOST}"
            or action != "Listing"
        ):
            continue
        boards.add(PageUpBoard(instance, pointer, locale))
    return frozenset(boards)


def pageup_listing_board_from_html(document: str) -> PageUpBoard | None:
    """Return the unique board asserted by PageUp's ``PU.Jobs.source`` JSON."""

    boards = pageup_listing_boards_from_html(document)
    return next(iter(boards)) if len(boards) == 1 else None
