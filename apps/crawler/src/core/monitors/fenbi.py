"""Fenbi careers monitor for the public website's bundled job inventory.

Fenbi's Angular page server-renders only the first five rows. The complete,
authoritative ``fulltime`` and ``parttime`` arrays live in the versioned main
bundle that the page itself loads. This monitor discovers that bundle on every
cycle and parses the bounded object literal instead of depending on a stale
asset hash or treating the visible pager as HTTP pagination.
"""

from __future__ import annotations

import html
import json
import re
from datetime import datetime
from typing import TYPE_CHECKING
from urllib.parse import urljoin, urlparse

from selectolax.lexbor import LexborHTMLParser

from src.core.monitors import DiscoveredJob, register

if TYPE_CHECKING:
    import httpx

_PAGE_HOST = "www.fenbi.com"
_BUNDLE_HOST = "nodestatic.fbstatic.cn"
_BUNDLE_PATH_RE = re.compile(r"^/weblts_spa_online/page/main-[A-Z0-9]+\.js$")
_INVENTORY_MARKER = "this.joinUsArr="
_DATE_EXPRESSION_RE = re.compile(
    r"new Date\((?P<year>\d{4}),(?P<month>\d{1,2}),(?P<day>\d{1,2})\)\.getTime\(\)"
)
_IDENTIFIER_RE = re.compile(r"[A-Za-z_$][A-Za-z0-9_$]*")
_PUBLIC_DATE_RE = re.compile(r"^(?P<year>\d{4})年(?P<month>\d{1,2})月(?P<day>\d{1,2})日$")
_PREFERRED_LOCATION_SUFFIX_RE = re.compile(r"[（(]优先[）)]$")
_MAX_PAGE_BYTES = 2_000_000
_MAX_BUNDLE_BYTES = 5_000_000
_MAX_INVENTORY_BYTES = 500_000
_MAX_JOBS = 500
_KINDS = frozenset({"fulltime", "parttime"})


def _bounded_text(response, *, label: str, max_bytes: int) -> str:
    raw = response.content
    if len(raw) > max_bytes:
        raise ValueError(f"Fenbi {label} exceeded the {max_bytes}-byte safety cap")
    return response.text


def _require_response_url(response, *, host: str, path_pattern: re.Pattern[str] | None) -> None:
    parsed = urlparse(str(response.url))
    if (
        parsed.scheme != "https"
        or parsed.hostname != host
        or parsed.username is not None
        or parsed.password is not None
        or (path_pattern is not None and path_pattern.fullmatch(parsed.path) is None)
    ):
        raise ValueError(f"Fenbi response redirected outside the trusted {host} source")


def _bundle_url(page_html: str, board_url: str) -> str:
    matches: list[str] = []
    for node in LexborHTMLParser(page_html).css("script[src]"):
        raw = node.attributes.get("src")
        if not raw:
            continue
        candidate = urlparse(urljoin(board_url, raw))
        if (
            candidate.scheme == "https"
            and candidate.hostname == _BUNDLE_HOST
            and candidate.username is None
            and candidate.password is None
            and _BUNDLE_PATH_RE.fullmatch(candidate.path)
            and not candidate.query
            and not candidate.fragment
        ):
            matches.append(candidate.geturl())
    if len(matches) != 1:
        raise ValueError(f"Fenbi page must expose exactly one main bundle (found {len(matches)})")
    return matches[0]


def _extract_inventory_literal(bundle: str) -> str:
    if bundle.count(_INVENTORY_MARKER) != 1:
        raise ValueError("Fenbi bundle must contain exactly one careers inventory marker")
    start = bundle.index(_INVENTORY_MARKER) + len(_INVENTORY_MARKER)
    if start >= len(bundle) or bundle[start] != "{":
        raise ValueError("Fenbi careers inventory marker is not followed by an object")

    depth = 0
    quoted = False
    escaped = False
    for index in range(start, len(bundle)):
        character = bundle[index]
        if quoted:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            continue
        if character == '"':
            quoted = True
        elif character in {"'", "`"}:
            raise ValueError("Fenbi careers inventory uses an unsupported string delimiter")
        elif character == "{":
            depth += 1
        elif character == "}":
            depth -= 1
            if depth == 0:
                literal = bundle[start : index + 1]
                if len(literal) > _MAX_INVENTORY_BYTES:
                    raise ValueError("Fenbi careers inventory exceeded the safety cap")
                return literal
    raise ValueError("Fenbi careers inventory object is unterminated")


def _json_from_javascript_literal(literal: str) -> dict:
    """Convert Fenbi's bounded minified object literal to strict JSON."""
    output: list[str] = []
    index = 0
    quoted = False
    escaped = False
    while index < len(literal):
        character = literal[index]
        if quoted:
            output.append(character)
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == '"':
                quoted = False
            index += 1
            continue
        if character == '"':
            quoted = True
            output.append(character)
            index += 1
            continue
        if character in {"'", "`"}:
            raise ValueError("Fenbi careers inventory uses an unsupported string delimiter")

        date_match = _DATE_EXPRESSION_RE.match(literal, index)
        if date_match is not None:
            output.append("0")
            index = date_match.end()
            continue

        output.append(character)
        index += 1
        if character not in "{,":
            continue
        whitespace_start = index
        while index < len(literal) and literal[index].isspace():
            index += 1
        output.append(literal[whitespace_start:index])
        key_match = _IDENTIFIER_RE.match(literal, index)
        if key_match is None:
            continue
        key_end = key_match.end()
        colon_index = key_end
        while colon_index < len(literal) and literal[colon_index].isspace():
            colon_index += 1
        if colon_index >= len(literal) or literal[colon_index] != ":":
            continue
        output.append(json.dumps(key_match.group(0)))
        output.append(literal[key_end : colon_index + 1])
        index = colon_index + 1

    try:
        payload = json.loads("".join(output))
    except json.JSONDecodeError as exc:
        raise ValueError("Fenbi careers inventory is not a supported object literal") from exc
    if not isinstance(payload, dict):
        raise ValueError("Fenbi careers inventory must be an object")
    return payload


def _required_text(raw: dict, key: str, *, job_id: int) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Fenbi job {job_id} has invalid {key}")
    return value.strip()


def _text_list(
    raw: dict,
    key: str,
    *,
    job_id: int,
    allow_empty: bool = False,
) -> list[str]:
    value = raw.get(key)
    if not isinstance(value, list) or (not value and not allow_empty):
        raise ValueError(f"Fenbi job {job_id} has invalid {key}")
    if any(not isinstance(item, str) or not item.strip() for item in value):
        raise ValueError(f"Fenbi job {job_id} has invalid {key}")
    return [item.strip() for item in value]


def _description(responsibilities: list[str], qualifications: list[str]) -> str:
    def section(title: str, items: list[str]) -> str:
        rows = "".join(f"<li>{html.escape(item)}</li>" for item in items)
        return f"<h2>{title}</h2><ol>{rows}</ol>"

    result = section("岗位职责", responsibilities)
    if qualifications:
        result += section("任职要求", qualifications)
    return result


def _date_posted(value: object, *, job_id: int) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Fenbi job {job_id} has invalid publicDateShow")
    match = _PUBLIC_DATE_RE.fullmatch(value.strip())
    if match is None:
        raise ValueError(f"Fenbi job {job_id} has invalid publicDateShow")
    try:
        parsed = datetime(
            int(match.group("year")),
            int(match.group("month")),
            int(match.group("day")),
        )
    except ValueError as exc:
        raise ValueError(f"Fenbi job {job_id} has invalid publicDateShow") from exc
    return parsed.date().isoformat()


def _locations_and_type(value: str, *, job_id: int) -> tuple[list[str], str | None]:
    if value == "网络办公":
        return ["China"], "remote"
    locations: list[str] = []
    has_home_option = False
    for raw_part in value.split("、"):
        part = _PREFERRED_LOCATION_SUFFIX_RE.sub("", raw_part.strip()).strip()
        if part == "居家":
            has_home_option = True
        elif part == "全国":
            locations.append("China")
        elif part:
            locations.append(part)
    if not locations:
        raise ValueError(f"Fenbi job {job_id} has invalid location")
    return locations, "hybrid" if has_home_option else None


def _parse_jobs(payload: dict, *, kind: str, board_url: str) -> list[DiscoveredJob]:
    rows = payload.get(kind)
    if not isinstance(rows, list) or not rows or len(rows) > _MAX_JOBS:
        raise ValueError(f"Fenbi {kind} inventory must contain 1-{_MAX_JOBS} jobs")

    jobs: list[DiscoveredJob] = []
    seen_ids: set[int] = set()
    for raw in rows:
        if not isinstance(raw, dict):
            raise ValueError(f"Fenbi {kind} inventory contains a non-object job")
        job_id = raw.get("id")
        if not isinstance(job_id, int) or isinstance(job_id, bool) or job_id <= 0:
            raise ValueError(f"Fenbi {kind} inventory contains an invalid job id")
        if job_id in seen_ids:
            raise ValueError(f"Fenbi {kind} inventory contains duplicate job id {job_id}")
        seen_ids.add(job_id)

        title = _required_text(raw, "title", job_id=job_id)
        location = _required_text(raw, "location", job_id=job_id)
        responsibilities = _text_list(raw, "description", job_id=job_id)
        qualifications = _text_list(raw, "requirements", job_id=job_id, allow_empty=True)
        locations, job_location_type = _locations_and_type(location, job_id=job_id)

        jobs.append(
            DiscoveredJob(
                url=urljoin(board_url, f"/page/joinusdetail/{kind}/{job_id}"),
                title=title,
                description=_description(responsibilities, qualifications),
                locations=locations,
                employment_type="full_time" if kind == "fulltime" else "part_time",
                job_location_type=job_location_type,
                date_posted=_date_posted(raw.get("publicDateShow"), job_id=job_id),
                extras={
                    "responsibilities": responsibilities,
                    "qualifications": qualifications,
                },
                metadata={
                    "provider_id": job_id,
                    "department": raw.get("department") or None,
                },
                source_identity=f"fenbi:careers:{kind}-{job_id}",
            )
        )
    return jobs


async def discover(
    board: dict,
    client: httpx.AsyncClient,
    pw=None,
) -> list[DiscoveredJob]:
    del pw
    board_url = board["board_url"]
    metadata = board.get("metadata") or {}
    kind = metadata.get("kind")
    if kind not in _KINDS:
        raise ValueError("Fenbi monitor kind must be fulltime or parttime")
    expected_path = "/page/joinus" if kind == "fulltime" else "/page/joinus/parttime"
    parsed_board = urlparse(board_url)
    if (
        parsed_board.scheme != "https"
        or parsed_board.hostname != _PAGE_HOST
        or parsed_board.username is not None
        or parsed_board.password is not None
        or parsed_board.path != expected_path
        or parsed_board.query
        or parsed_board.fragment
    ):
        raise ValueError("Fenbi monitor requires its canonical official board URL")

    page_response = await client.get(board_url, follow_redirects=True)
    page_response.raise_for_status()
    _require_response_url(page_response, host=_PAGE_HOST, path_pattern=None)
    page_html = _bounded_text(page_response, label="board page", max_bytes=_MAX_PAGE_BYTES)
    bundle_url = _bundle_url(page_html, board_url)
    bundle_response = await client.get(bundle_url, follow_redirects=True)
    bundle_response.raise_for_status()
    _require_response_url(bundle_response, host=_BUNDLE_HOST, path_pattern=_BUNDLE_PATH_RE)
    bundle = _bounded_text(bundle_response, label="main bundle", max_bytes=_MAX_BUNDLE_BYTES)
    payload = _json_from_javascript_literal(_extract_inventory_literal(bundle))
    return _parse_jobs(payload, kind=kind, board_url=board_url)


register("fenbi", discover, cost=10, rich=True)
