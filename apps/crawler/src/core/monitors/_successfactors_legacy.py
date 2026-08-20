"""Native transport for legacy SAP SuccessFactors career boards.

Legacy boards load listings through Direct Web Remoting (DWR).  Responses are
JavaScript assignment text, but this module never evaluates them: a restricted
parser accepts only declarations, property/index assignments, primitive
literals, and references.  The authoritative count and pagination envelope
are validated before partial results can be considered complete.
"""

from __future__ import annotations

import json
import re
import secrets
from collections.abc import AsyncIterator, Iterable, Mapping
from dataclasses import dataclass
from datetime import date
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse

import httpx
import structlog

from src.core.monitor import MonitorResult
from src.core.monitors import BoardGoneError, DiscoveredJob
from src.shared.http_retry import (
    PaginationFetchError,
    ResponseBodyTooLargeError,
    fetch_text_page_with_retry,
)
from src.shared.successfactors import (
    SuccessFactorsLegacyBoard,
    successfactors_legacy_board_from_metadata,
    successfactors_legacy_board_from_url,
)

log = structlog.get_logger()

PAGE_SIZE = 100
MAX_JOBS = 50_000
MAX_PAGES = MAX_JOBS // PAGE_SIZE
MAX_BOOTSTRAP_CHARS = 5_000_000
MAX_DWR_CHARS = 5_000_000
_TRANSIENT_STATUSES = frozenset({401, 403, 429})
_GONE_STATUSES = frozenset({404, 410})
_AJAX_TOKEN_RE = re.compile(r'\bvar\s+ajaxSecKey="([A-Za-z0-9%+/_=-]{8,512})";')
_EVENT_ID_RE = re.compile(r"[A-Za-z0-9._:-]{8,256}")
_LOCALE_RE = re.compile(r"[a-z]{2}(?:_[A-Z]{2})?")
_DECL_RE = re.compile(r"\bvar\s+(s\d+)=(\{\}|\[\]);")
_ASSIGN_RE = re.compile(
    r"\b(?P<owner>s\d+)(?:"
    r"\.(?P<property>[A-Za-z_$][A-Za-z0-9_$]*)"
    r"|\[(?P<index>\d{1,6})\]"
    r"|\['(?P<key>(?:\\.|[^'\\]){1,256})'\]"
    r")=(?P<value>"
    r"s\d+|\"(?:\\.|[^\"\\])*\"|null|true|false|-?(?:0|[1-9]\d*)(?:\.\d+)?"
    r");"
)
_CALLBACK_RE = re.compile(
    r"dwr\.engine\._remoteHandleCallback\('(?P<batch>\d+)',\s*'0',\s*"
    r"(?P<root>\{(?:payload:s\d+|filters:s\d+,results:s\d+)\})\s*\);"
)
_PRELUDE_RE = re.compile(
    r"\A\s*(?:throw\s+'allowScriptTagRemoting is false\.';\s*//#DWR-INSERT\s*)?"
    r"//#DWR-REPLY\s*"
)
_WHITESPACE_RE = re.compile(r"\s*")
_INITIAL_ROOT_RE = re.compile(r"\{payload:(s\d+)\}")
_SEARCH_ROOT_RE = re.compile(r"\{filters:(s\d+),results:(s\d+)\}")
_LOCATION_LABEL_RE = re.compile(
    r"(?:location|duty\s+station|work\s*(?:place|site)|city|country|region|"
    r"standort|arbeitsort|lieu|emplacement|ubicaci[oó]n|localit[aà]|sede|"
    r"地点|工作地|勤務地|근무지)",
    re.IGNORECASE,
)
_DETAIL_ALLOWED_KEYS = frozenset(
    {
        "career_job_req_id",
        "career_ns",
        "company",
        "lang",
        "navBarLevel",
        "rcm_site_locale",
        "site",
    }
)


class SuccessFactorsLegacyProtocolError(ValueError):
    """Legacy SuccessFactors returned an unsafe or internally inconsistent payload."""


class SuccessFactorsLegacyRedirect(SuccessFactorsLegacyProtocolError):
    """The configured legacy tenant redirected to a migrated career site."""

    def __init__(self, location: str):
        super().__init__(f"legacy SuccessFactors board redirected to {location!r}")
        self.location = location


@dataclass(slots=True)
class _LegacySession:
    board: SuccessFactorsLegacyBoard
    ajax_token: str
    event_id: str
    script_session_id: str


def _board_identity(board: dict) -> SuccessFactorsLegacyBoard:
    metadata = board.get("metadata") or {}
    configured = (
        successfactors_legacy_board_from_metadata(metadata)
        if isinstance(metadata, Mapping)
        else None
    )
    direct = successfactors_legacy_board_from_url(board["board_url"])
    identity_keys = {"company", "host", "listing_url"}
    has_configured_identity = isinstance(metadata, Mapping) and bool(
        identity_keys & metadata.keys()
    )
    if has_configured_identity and configured is None:
        raise ValueError("Invalid or internally inconsistent SuccessFactors legacy config")
    if configured is not None and direct is not None and configured != direct:
        raise ValueError("Configured SuccessFactors identity does not match the board URL")
    resolved = configured or direct
    if resolved is None:
        raise ValueError(
            f"Cannot derive a legacy SuccessFactors board from {board['board_url']!r}; "
            "configure metadata.host and metadata.company"
        )
    return resolved


async def _bootstrap_session(
    board: SuccessFactorsLegacyBoard,
    client: httpx.AsyncClient,
    *,
    terminal: bool,
) -> _LegacySession:
    headers: dict[str, str] = {}
    try:
        document = await fetch_text_page_with_retry(
            client,
            board.listing_url,
            follow_redirects=False,
            retryable_statuses=_TRANSIENT_STATUSES,
            end_of_pagination_statuses=(),
            require_nonempty=True,
            max_chars=MAX_BOOTSTRAP_CHARS + 1,
            max_bytes=MAX_BOOTSTRAP_CHARS,
            response_headers=headers,
            log_event="rss.successfactors_legacy_bootstrap_backoff",
        )
    except ResponseBodyTooLargeError as exc:
        raise SuccessFactorsLegacyProtocolError("legacy bootstrap exceeded the HTML cap") from exc
    except PaginationFetchError as exc:
        if exc.last_status is not None and 300 <= exc.last_status < 400 and exc.last_location:
            raise SuccessFactorsLegacyRedirect(
                urljoin(board.listing_url, exc.last_location)
            ) from exc
        if terminal and exc.last_status in _GONE_STATUSES:
            raise BoardGoneError(
                "SuccessFactors legacy board no longer exists",
                url=board.listing_url,
                status_code=exc.last_status,
            ) from exc
        raise
    if document is None:  # Strict terminal status handling makes this unreachable.
        raise SuccessFactorsLegacyProtocolError("legacy bootstrap returned no document")
    if len(document) > MAX_BOOTSTRAP_CHARS:
        raise SuccessFactorsLegacyProtocolError("legacy bootstrap exceeded the HTML cap")
    if "careerJobSearchController" not in document or "getInitialJobSearchData" not in document:
        raise SuccessFactorsLegacyProtocolError("legacy bootstrap omitted the listing markers")
    company_markers = (
        f'companyId: "{board.company}"',
        f"companyId={board.company}",
    )
    if not any(marker in document for marker in company_markers):
        raise SuccessFactorsLegacyProtocolError("legacy bootstrap did not assert the company")
    token_match = _AJAX_TOKEN_RE.search(document)
    event_id = headers.get("x-event-id", "")
    if token_match is None or _EVENT_ID_RE.fullmatch(event_id) is None:
        raise SuccessFactorsLegacyProtocolError("legacy bootstrap omitted session security data")
    return _LegacySession(
        board=board,
        ajax_token=token_match.group(1),
        event_id=event_id,
        script_session_id=secrets.token_hex(12),
    )


def _listing_page(board: SuccessFactorsLegacyBoard) -> str:
    parsed = urlparse(board.listing_url)
    return f"{parsed.path}?{parsed.query}"


def _dwr_headers(session: _LegacySession) -> dict[str, str]:
    origin = f"https://{session.board.host}"
    return {
        "content-type": "text/plain",
        "origin": origin,
        "referer": session.board.listing_url,
        "viewid": "/ui/rcmcareer/pages/careersite/career.jsp.xhtml",
        "x-ajax-token": session.ajax_token,
        "x-csrf-token": session.ajax_token,
        "x-event-id": session.event_id,
        "x-sap-page-info": f"companyId={session.board.company}",
        "x-subaction": "0",
    }


def _dwr_envelope(session: _LegacySession, method: str, body: list[str], batch: int) -> str:
    return "\n".join(
        [
            "callCount=1",
            f"page={_listing_page(session.board)}",
            "httpSessionId=",
            f"scriptSessionId={session.script_session_id}",
            "c0-scriptName=careerJobSearchControllerProxy",
            f"c0-methodName={method}",
            "c0-id=0",
            *body,
            f"batchId={batch}",
            "",
        ]
    )


def _initial_body(session: _LegacySession) -> str:
    return _dwr_envelope(
        session,
        "getInitialJobSearchData",
        [
            "c0-e1=string:",
            "c0-e2=string:",
            "c0-e3=boolean:false",
            "c0-e4=string:Etc%2FUTC",
            (
                "c0-param0=Object_Object:{filterOnly:reference:c0-e1,"
                "jobAlertId:reference:c0-e2,returnToList:reference:c0-e3,"
                "browserTimeZone:reference:c0-e4}"
            ),
        ],
        0,
    )


def _search_body(session: _LegacySession, page: int) -> str:
    return _dwr_envelope(
        session,
        "search",
        [
            f"c0-e1=number:{page}",
            f"c0-e2=number:{PAGE_SIZE}",
            "c0-e3=Object_Object:{currentPage:reference:c0-e1,pageSize:reference:c0-e2}",
            "c0-e4=string:JOB_POSTING_DATE",
            "c0-e5=string:DESC",
            (
                "c0-param0=Object_Object:{pagination:reference:c0-e3,"
                "sortByColumn:reference:c0-e4,sortOrder:reference:c0-e5}"
            ),
        ],
        page,
    )


async def _post_dwr(
    session: _LegacySession,
    client: httpx.AsyncClient,
    *,
    method: str,
    body: str,
) -> str:
    origin = f"https://{session.board.host}"
    url = f"{origin}/xi/ajax/remoting/call/plaincall/careerJobSearchControllerProxy.{method}.dwr"
    try:
        response = await fetch_text_page_with_retry(
            client,
            url,
            method="POST",
            content=body,
            headers=_dwr_headers(session),
            follow_redirects=False,
            retryable_statuses=_TRANSIENT_STATUSES,
            end_of_pagination_statuses=(),
            require_nonempty=True,
            max_chars=MAX_DWR_CHARS + 1,
            max_bytes=MAX_DWR_CHARS,
            log_event="rss.successfactors_legacy_dwr_backoff",
        )
    except ResponseBodyTooLargeError as exc:
        raise SuccessFactorsLegacyProtocolError(
            "legacy DWR response exceeded the safety cap"
        ) from exc
    if response is None:
        raise SuccessFactorsLegacyProtocolError("legacy DWR returned no response")
    if len(response) > MAX_DWR_CHARS:
        raise SuccessFactorsLegacyProtocolError("legacy DWR response exceeded the safety cap")
    return response


def _decode_js_string(raw: str) -> str:
    # DWR occasionally escapes apostrophes inside a double-quoted string;
    # JSON correctly handles every other accepted escape in this grammar.
    try:
        decoded = json.loads(raw.replace("\\'", "'"))
    except json.JSONDecodeError as exc:
        raise SuccessFactorsLegacyProtocolError("unsupported DWR string escape") from exc
    if not isinstance(decoded, str):
        raise SuccessFactorsLegacyProtocolError("DWR string did not decode to text")
    return decoded


def _parse_value(raw: str, objects: dict[str, object]) -> object:
    if raw.startswith("s"):
        if raw not in objects:
            raise SuccessFactorsLegacyProtocolError("DWR referenced an undeclared object")
        return objects[raw]
    if raw.startswith('"'):
        return _decode_js_string(raw)
    if raw == "null":
        return None
    if raw == "true":
        return True
    if raw == "false":
        return False
    try:
        return float(raw) if "." in raw else int(raw)
    except ValueError as exc:
        raise SuccessFactorsLegacyProtocolError("unsupported DWR assignment value") from exc


def _parse_dwr(response: str, *, batch: int, initial: bool) -> dict:
    prelude = _PRELUDE_RE.match(response)
    if prelude is None:
        raise SuccessFactorsLegacyProtocolError("response omitted the DWR reply marker")

    objects: dict[str, object] = {}
    callback: re.Match[str] | None = None
    cursor = prelude.end()
    while cursor < len(response):
        whitespace = _WHITESPACE_RE.match(response, cursor)
        if whitespace is not None:
            cursor = whitespace.end()
        if cursor == len(response):
            break

        declaration = _DECL_RE.match(response, cursor)
        if declaration is not None:
            name, shape = declaration.groups()
            if name in objects:
                raise SuccessFactorsLegacyProtocolError("DWR redeclared an object")
            objects[name] = {} if shape == "{}" else []
            cursor = declaration.end()
            continue

        assignment = _ASSIGN_RE.match(response, cursor)
        if assignment is not None:
            owner = objects.get(assignment.group("owner"))
            if owner is None:
                raise SuccessFactorsLegacyProtocolError("DWR assigned to an undeclared object")
            value = _parse_value(assignment.group("value"), objects)
            property_name = assignment.group("property") or assignment.group("key")
            if property_name is not None:
                if not isinstance(owner, dict):
                    raise SuccessFactorsLegacyProtocolError("DWR used an object key on an array")
                if property_name in owner:
                    raise SuccessFactorsLegacyProtocolError("DWR assigned an object key twice")
                owner[property_name] = value
            else:
                if not isinstance(owner, list):
                    raise SuccessFactorsLegacyProtocolError("DWR used an array index on an object")
                index = int(assignment.group("index"))
                if index > MAX_JOBS * 4:
                    raise SuccessFactorsLegacyProtocolError(
                        "DWR array index exceeded the safety cap"
                    )
                if index < len(owner) and owner[index] is not None:
                    raise SuccessFactorsLegacyProtocolError("DWR assigned an array index twice")
                if index >= len(owner):
                    owner.extend([None] * (index + 1 - len(owner)))
                owner[index] = value
            cursor = assignment.end()
            continue

        parsed_callback = _CALLBACK_RE.match(response, cursor)
        if parsed_callback is not None and callback is None:
            callback = parsed_callback
            cursor = parsed_callback.end()
            if response[cursor:].strip():
                raise SuccessFactorsLegacyProtocolError(
                    "DWR contained statements after its callback"
                )
            break

        raise SuccessFactorsLegacyProtocolError(
            "DWR contained a statement outside the supported grammar"
        )

    if not objects:
        raise SuccessFactorsLegacyProtocolError("DWR response declared no data graph")
    if callback is None or callback.group("batch") != str(batch):
        raise SuccessFactorsLegacyProtocolError("DWR response omitted its unique callback")
    root_text = callback.group("root")
    root_match = (_INITIAL_ROOT_RE if initial else _SEARCH_ROOT_RE).fullmatch(root_text)
    if root_match is None:
        raise SuccessFactorsLegacyProtocolError("DWR callback had an unexpected result shape")
    if initial:
        root = objects.get(root_match.group(1))
        if not isinstance(root, dict):
            raise SuccessFactorsLegacyProtocolError("DWR initial payload was not an object")
        return root
    filters = objects.get(root_match.group(1))
    results = objects.get(root_match.group(2))
    if not isinstance(filters, dict) or not isinstance(results, dict):
        raise SuccessFactorsLegacyProtocolError("DWR search payload was not an object")
    return {"filters": filters, "results": results}


def _as_count(value: object, field: str) -> int:
    if isinstance(value, bool):
        raise SuccessFactorsLegacyProtocolError(f"{field} was not an integer")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not 0 <= value <= MAX_JOBS:
        raise SuccessFactorsLegacyProtocolError(f"{field} exceeded the supported range")
    return value


def _as_job_id(value: object) -> int:
    if isinstance(value, bool):
        raise SuccessFactorsLegacyProtocolError("posting id was not an integer")
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    if not isinstance(value, int) or not 1 <= value <= 999_999_999_999_999_999:
        raise SuccessFactorsLegacyProtocolError("posting id exceeded the supported range")
    return value


def _results(root: dict, *, initial: bool) -> tuple[dict, dict]:
    payload = root if initial else root
    filters = payload.get("filters")
    results = payload.get("results")
    if not isinstance(filters, dict) or not isinstance(results, dict):
        raise SuccessFactorsLegacyProtocolError("DWR payload omitted filters or results")
    return filters, results


def _validate_total(filters: dict, results: dict) -> int:
    total = _as_count(results.get("postingCount"), "postingCount")
    filter_total = _as_count(filters.get("postingCount"), "filter postingCount")
    if filter_total != total:
        raise SuccessFactorsLegacyProtocolError("SuccessFactors result counts disagreed")
    options = results.get("options")
    pagination = options.get("pagination") if isinstance(options, dict) else None
    if not isinstance(pagination, dict):
        raise SuccessFactorsLegacyProtocolError("SuccessFactors omitted pagination metadata")
    if _as_count(pagination.get("totalCount"), "pagination totalCount") != total:
        raise SuccessFactorsLegacyProtocolError("SuccessFactors pagination total drifted")
    return total


def _detail_prefix(results: dict) -> str:
    prefix = results.get("detailURLPrefix")
    if not isinstance(prefix, str) or not 1 <= len(prefix) <= 2048:
        raise SuccessFactorsLegacyProtocolError("SuccessFactors omitted its detail URL prefix")
    return prefix


def _filter_labels(filters: dict) -> dict[str, str]:
    configs = filters.get("configs")
    raw_filters = configs.get("filters") if isinstance(configs, dict) else None
    labels: dict[str, str] = {}
    if not isinstance(raw_filters, list):
        return labels
    for raw in raw_filters:
        if not isinstance(raw, dict):
            continue
        name = raw.get("fieldName")
        label = raw.get("label")
        if not isinstance(name, str) or not isinstance(label, str):
            continue
        field_id = name.removeprefix("customFilter_")
        if field_id and 1 <= len(label.strip()) <= 200:
            labels[field_id] = label.strip()
    return labels


def _field_values(value: object, *, depth: int = 0) -> Iterable[tuple[str, str]]:
    if depth > 5:
        return
    if isinstance(value, list):
        for item in value:
            yield from _field_values(item, depth=depth + 1)
        return
    if not isinstance(value, dict):
        return
    field_id = value.get("fieldId")
    long_value = value.get("longVal")
    short_value = value.get("shortVal")
    text = long_value if isinstance(long_value, str) and long_value.strip() else short_value
    if isinstance(field_id, str) and isinstance(text, str) and text.strip():
        yield field_id, " ".join(text.split())


def _canonical_detail_url(
    board: SuccessFactorsLegacyBoard,
    prefix: str,
    job_id: int,
) -> tuple[str, str | None]:
    candidate = urljoin(f"https://{board.host}", f"{prefix}{job_id}")
    try:
        parsed = urlparse(candidate)
        port = parsed.port
        pairs = parse_qsl(parsed.query, keep_blank_values=True, max_num_fields=12)
    except (TypeError, ValueError) as exc:
        raise SuccessFactorsLegacyProtocolError(
            "SuccessFactors returned an unsafe detail URL"
        ) from exc
    if (
        parsed.scheme.casefold() != "https"
        or (parsed.hostname or "").casefold().rstrip(".") != board.host
        or parsed.username is not None
        or parsed.password is not None
        or port not in {None, 443}
        or parsed.path.rstrip("/").casefold() != "/career"
        or parsed.fragment
    ):
        raise SuccessFactorsLegacyProtocolError("SuccessFactors detail URL escaped the board")
    params: dict[str, str] = {}
    for name, value in pairs:
        if name in params or name not in _DETAIL_ALLOWED_KEYS:
            raise SuccessFactorsLegacyProtocolError("SuccessFactors detail URL had unsafe scope")
        params[name] = value
    if (
        params.get("company") != board.company
        or params.get("career_ns") != "job_listing"
        or params.get("navBarLevel") != "JOB_SEARCH"
        or params.get("career_job_req_id") != str(job_id)
    ):
        raise SuccessFactorsLegacyProtocolError("SuccessFactors detail URL identity drifted")
    locale = params.get("rcm_site_locale")
    canonical_params = {
        "career_ns": "job_listing",
        "company": board.company,
        "navBarLevel": "JOB_SEARCH",
    }
    if locale:
        canonical_params["rcm_site_locale"] = locale
    for optional_key in ("site", "lang"):
        if params.get(optional_key):
            canonical_params[optional_key] = params[optional_key]
    canonical_params["career_job_req_id"] = str(job_id)
    return f"https://{board.host}/career?{urlencode(canonical_params)}", locale


def _parse_posting_date(value: object, locale: str | None) -> str | None:
    if not isinstance(value, str):
        return None
    match = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", value.strip())
    if match is None:
        return None
    first, second, year = (int(part) for part in match.groups())
    month, day = (first, second) if locale == "en_US" else (second, first)
    try:
        return date(year, month, day).isoformat()
    except ValueError:
        return None


def _parse_jobs(
    postings: object,
    *,
    board: SuccessFactorsLegacyBoard,
    prefix: str,
    labels: dict[str, str],
) -> list[DiscoveredJob]:
    if not isinstance(postings, list) or any(item is None for item in postings):
        raise SuccessFactorsLegacyProtocolError("SuccessFactors postings were not a dense array")
    jobs: list[DiscoveredJob] = []
    for raw in postings:
        if not isinstance(raw, dict):
            raise SuccessFactorsLegacyProtocolError("SuccessFactors posting was not an object")
        job_id = _as_job_id(raw.get("id"))
        title = raw.get("title")
        if not isinstance(title, str) or not 1 <= len(" ".join(title.split())) <= 500:
            raise SuccessFactorsLegacyProtocolError("SuccessFactors posting omitted a title")
        title = " ".join(title.split())
        url, prefix_locale = _canonical_detail_url(board, prefix, job_id)
        raw_locale = raw.get("defaultLocale")
        locale = (
            raw_locale if isinstance(raw_locale, str) and _LOCALE_RE.fullmatch(raw_locale) else None
        )
        locale = locale or prefix_locale

        values: dict[str, list[str]] = {}
        for field_id, field_value in _field_values(raw.get("otherValues")):
            bucket = values.setdefault(field_id, [])
            if field_value not in bucket:
                bucket.append(field_value)
        locations: list[str] = []
        for field_id, field_values in values.items():
            label = labels.get(field_id, "")
            if _LOCATION_LABEL_RE.search(label):
                locations.extend(value for value in field_values if value not in locations)

        posting_date = raw.get("postingDate")
        metadata: dict[str, object] = {"id": str(job_id)}
        if locale:
            metadata["default_locale"] = locale
        if isinstance(posting_date, str) and posting_date.strip():
            metadata["posting_date_raw"] = posting_date.strip()
        if values:
            metadata["fields"] = {
                labels.get(field_id, field_id): field_values[0]
                if len(field_values) == 1
                else field_values
                for field_id, field_values in values.items()
            }

        jobs.append(
            DiscoveredJob(
                url=url,
                title=title,
                locations=locations or None,
                date_posted=_parse_posting_date(posting_date, locale),
                language=locale[:2].casefold() if locale else None,
                metadata=metadata,
            )
        )
    return jobs


async def _initial_payload(
    session: _LegacySession,
    client: httpx.AsyncClient,
) -> tuple[int, str, dict[str, str]]:
    response = await _post_dwr(
        session,
        client,
        method="getInitialJobSearchData",
        body=_initial_body(session),
    )
    root = _parse_dwr(response, batch=0, initial=True)
    filters, results = _results(root, initial=True)
    total = _validate_total(filters, results)
    prefix = _detail_prefix(results)
    postings = results.get("postings")
    if not isinstance(postings, list) or len(postings) != min(total, 10):
        raise SuccessFactorsLegacyProtocolError("SuccessFactors initial page was incomplete")
    if total > MAX_JOBS:
        raise SuccessFactorsLegacyProtocolError("SuccessFactors board exceeded the job cap")
    return total, prefix, _filter_labels(filters)


async def probe_legacy(
    board: SuccessFactorsLegacyBoard,
    client: httpx.AsyncClient,
) -> dict:
    """Validate one legacy board and return canonical RSS monitor config."""

    # Shared SuccessFactors origins occasionally invalidate the DWR session
    # between the bootstrap GET and the initial POST.  The resulting body is
    # an HTML/session response rather than a DWR reply.  Retry the complete
    # handshake once so detection does not fall through to an expensive DOM
    # monitor because of a single stale security token.  Persistent protocol
    # drift still fails closed on the second attempt.
    for attempt in range(2):
        session = await _bootstrap_session(board, client, terminal=False)
        try:
            total, _prefix, _labels = await _initial_payload(session, client)
            break
        except SuccessFactorsLegacyRedirect:
            raise
        except SuccessFactorsLegacyProtocolError:
            if attempt:
                raise
            log.info(
                "rss.successfactors_legacy_probe_retry",
                host=board.host,
                company=board.company,
            )
    return {
        "preset": "successfactors",
        "variant": "legacy",
        "host": board.host,
        "company": board.company,
        "listing_url": board.listing_url,
        "jobs": total,
    }


async def discover_legacy_stream(
    board: dict,
    client: httpx.AsyncClient,
) -> AsyncIterator[MonitorResult]:
    identity = _board_identity(board)
    session = await _bootstrap_session(identity, client, terminal=True)
    total, expected_prefix, labels = await _initial_payload(session, client)
    if total == 0:
        yield MonitorResult(hybrid=True)
        return

    pages = (total + PAGE_SIZE - 1) // PAGE_SIZE
    if pages > MAX_PAGES:
        raise SuccessFactorsLegacyProtocolError("SuccessFactors pagination exceeded the page cap")
    seen: set[int] = set()
    for page in range(1, pages + 1):
        response = await _post_dwr(
            session,
            client,
            method="search",
            body=_search_body(session, page),
        )
        root = _parse_dwr(response, batch=page, initial=False)
        filters, results = _results(root, initial=False)
        if _validate_total(filters, results) != total:
            raise SuccessFactorsLegacyProtocolError("SuccessFactors total changed between pages")
        prefix = _detail_prefix(results)
        if prefix != expected_prefix:
            raise SuccessFactorsLegacyProtocolError(
                "SuccessFactors detail prefix changed between pages"
            )
        options = results["options"]
        pagination = options["pagination"]
        expected_count = min(PAGE_SIZE, total - ((page - 1) * PAGE_SIZE))
        expected_start = ((page - 1) * PAGE_SIZE) + 1
        # SAP reports the requested page boundary rather than clamping the
        # final page's endRow to totalCount (for 71 jobs at size 100: 1..100).
        expected_end = page * PAGE_SIZE
        if (
            _as_count(pagination.get("currentPage"), "currentPage") != page
            or _as_count(pagination.get("pageSize"), "pageSize") != PAGE_SIZE
            or _as_count(pagination.get("startRow"), "startRow") != expected_start
            or _as_count(pagination.get("endRow"), "endRow") != expected_end
        ):
            raise SuccessFactorsLegacyProtocolError("SuccessFactors pagination envelope drifted")
        jobs = _parse_jobs(
            results.get("postings"),
            board=identity,
            prefix=prefix,
            labels=labels,
        )
        if len(jobs) != expected_count:
            raise SuccessFactorsLegacyProtocolError("SuccessFactors page was incomplete")
        page_ids = {int((job.metadata or {})["id"]) for job in jobs}
        if len(page_ids) != len(jobs) or seen & page_ids:
            raise SuccessFactorsLegacyProtocolError(
                "SuccessFactors repeated a posting across pages"
            )
        seen.update(page_ids)
        by_url = {job.url: job for job in jobs}
        if len(by_url) != len(jobs):
            raise SuccessFactorsLegacyProtocolError("SuccessFactors produced duplicate detail URLs")
        log.info(
            "rss.successfactors_legacy_page",
            host=identity.host,
            company=identity.company,
            page=page,
            pages=pages,
            jobs=len(jobs),
            total=total,
        )
        yield MonitorResult(urls=set(by_url), jobs_by_url=by_url, hybrid=True)

    if len(seen) != total:
        raise SuccessFactorsLegacyProtocolError("SuccessFactors crawl did not match its total")


async def discover_legacy(
    board: dict,
    client: httpx.AsyncClient,
) -> MonitorResult:
    jobs: dict[str, DiscoveredJob] = {}
    async for batch in discover_legacy_stream(board, client):
        if batch.jobs_by_url:
            overlap = jobs.keys() & batch.jobs_by_url.keys()
            if overlap:
                raise SuccessFactorsLegacyProtocolError(
                    "SuccessFactors materialization found duplicate URLs"
                )
            jobs.update(batch.jobs_by_url)
    return MonitorResult(urls=set(jobs), jobs_by_url=jobs or None, hybrid=True)
