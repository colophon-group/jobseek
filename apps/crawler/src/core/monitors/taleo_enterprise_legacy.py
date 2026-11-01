"""Bounded server-rendered Oracle Taleo Enterprise listing support.

This is the public older ``joblist.ftl`` surface, separate from Taleo Business
Edition and from the newer faceted JSON API supported by ``api_sniffer``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, quote, unquote, urlparse

import httpx

from src.core.monitors.dom import _raise_if_bot_challenge
from src.core.scrapers.taleo import _parse_fill_list
from src.shared.http_retry import fetch_text_page_with_retry

_LIST_PATH = re.compile(r"^/careersection/[a-z0-9_-]{1,64}/(?:joblist|jobsearch)\.ftl$")
_LIST_MARKER = re.compile(
    r"api\.fillList\(\s*['\"]requisitionListInterface['\"]\s*,\s*['\"]listRequisition['\"]\s*,\s*"
)
_TOTAL = re.compile(r"\b([0-9]{1,5}) jobs? found\b")
_ID = re.compile(r"^[a-z0-9_-]{1,64}$", re.I)
_ROW_WIDTH = 43
_MAX_ROWS = 100


@dataclass(frozen=True)
class LegacyBoard:
    listing: str
    detail_prefix: str


def board_from_url(raw: str) -> LegacyBoard | None:
    try:
        parsed = urlparse(raw)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    query = parse_qs(parsed.query, keep_blank_values=True)
    portal = query.get("portal")
    if not (
        parsed.scheme == "https"
        and host.endswith(".taleo.net")
        and not host.endswith(".tbe.taleo.net")
        and parsed.username is None
        and parsed.password is None
        and port is None
        and not parsed.fragment
        and _LIST_PATH.fullmatch(parsed.path)
        and query.get("lang") == ["en"]
        and set(query) <= {"lang", "portal"}
        and (portal is None or len(portal) == 1 and 0 < len(portal[0]) <= 64)
        and (not parsed.path.endswith("joblist.ftl") or portal is None)
    ):
        return None
    path = parsed.path.replace("jobsearch.ftl", "joblist.ftl")
    return LegacyBoard(
        listing=f"https://{host}{path}?lang=en",
        detail_prefix=f"https://{host}{path.removesuffix('joblist.ftl')}jobdetail.ftl?job=",
    )


def parse_listing(html: str, board: LegacyBoard) -> list[dict[str, object]]:
    if len(html) > 2_000_000:
        raise ValueError("Taleo Enterprise listing exceeded the HTML safety cap")
    total_match = _TOTAL.search(html)
    if total_match is None:
        raise ValueError("Taleo Enterprise listing omitted its total")
    total = int(total_match.group(1))
    if total > 10_000:
        raise ValueError("Taleo Enterprise listing exceeded the job safety cap")
    values = _parse_fill_list(html, _LIST_MARKER, _ROW_WIDTH * _MAX_ROWS)
    if values is None or len(values) % _ROW_WIDTH or len(values) // _ROW_WIDTH != total:
        raise ValueError("Taleo Enterprise listing is incomplete or changed layout")
    jobs: list[dict[str, object]] = []
    seen: set[str] = set()
    for index in range(0, len(values), _ROW_WIDTH):
        row = values[index : index + _ROW_WIDTH]
        job_id, requisition = row[3].strip(), row[12].strip()
        title = unescape(unquote(row[4])).strip()
        if not (_ID.fullmatch(job_id) and _ID.fullmatch(requisition) and title):
            raise ValueError("Taleo Enterprise listing has invalid job identity")
        if requisition in seen:
            raise ValueError("Taleo Enterprise listing repeats a requisition")
        seen.add(requisition)
        job: dict[str, object] = {
            "url": board.detail_prefix + quote(requisition) + "&lang=en",
            "title": title,
            "metadata": {"ats_job_id": job_id, "requisition_number": requisition},
        }
        location = unescape(row[13]).strip()
        if location:
            job["locations"] = [location]
        posted = unescape(row[20]).strip()
        if re.search(r"\b20\d{2}\b", posted):
            job["date_posted"] = posted
        jobs.append(job)
    return jobs


async def discover(board: LegacyBoard, client: httpx.AsyncClient) -> set[str]:
    html = await fetch_text_page_with_retry(
        client,
        board.listing,
        require_nonempty=True,
        max_chars=2_000_001,
        follow_redirects=False,
        end_of_pagination_statuses=(),
        retryable_statuses=frozenset({202, 401, 403}),
        log_event="taleo_enterprise_legacy.listing_backoff",
    )
    if html is None:
        raise ValueError("Taleo Enterprise listing returned no page")
    _raise_if_bot_challenge(board.listing, html)
    return {str(job["url"]) for job in parse_listing(html, board)}
