"""Infoniqa jobexchange form-pagination monitor.

Infoniqa's initial ``showJobOfferList`` document is only a pre-hydration
shell.  The browser starts a session-scoped search with a CSRF-protected POST,
then drains the result through ``hasNextJobOffers`` / ``showNextJobOffers``
POSTs.  Job rows are ``li`` elements whose detail URL lives in an ``onclick``
handler rather than an anchor.

This monitor replays that provider protocol without JavaScript.  It binds the
session to the configured employer using two independent shell markers,
requires two independent copies of the advertised result count, validates
every detail URL against the board origin, and only accepts zero after the
provider explicitly reports that no result page remains.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import parse_qsl, urlencode, urljoin, urlparse
from uuid import UUID

import structlog

from src.core.monitors import register
from src.core.monitors.raw import save_text_response
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.tdm import TDMReservedError

if TYPE_CHECKING:
    import httpx

log = structlog.get_logger()

MAX_JOBS = 50_000
MAX_PAGES = 2_000
MAX_HTML_BYTES = 5_000_000
_LIST_PATH = "/hcm/jobexchange/showJobOfferList.do"
_DETAIL_PATH = "/hcm/jobexchange/showJobOfferDetail.do"
_JOB_ID_RE = re.compile(r"[0-9a-f]{32}")
_LOCATION_RE = re.compile(r"(?:^|[;{])\s*window\.location\.href\s*=\s*'([^']+)'\s*(?:;|\}|$)")
_BUTTON_COUNT_RE = re.compile(r"\$\('#showJobsButton'\)\.val\('[^'\r\n]*\((\d+)\)'\)")
_INFO_COUNT_RE = re.compile(r"(?<![\w])\d+(?![\w])")


@dataclass(frozen=True, slots=True)
class _Board:
    board_url: str
    origin: str
    host: str
    list_url: str
    search_url: str


@dataclass(frozen=True, slots=True)
class _Shell:
    csrf: str
    employer_name: str


@dataclass(frozen=True, slots=True)
class _SearchResult:
    total: int
    urls: tuple[str, ...]


def _attrs(values: list[tuple[str, str | None]]) -> dict[str, str]:
    return {name.lower(): value or "" for name, value in values}


def _classes(value: str) -> set[str]:
    return set(value.split())


def _clean_text(chunks: list[str]) -> str:
    return " ".join("".join(chunks).split())


def _board_from_url(url: str) -> _Board | None:
    try:
        parsed = urlparse(url)
        port = parsed.port
    except ValueError:
        return None
    host = (parsed.hostname or "").lower()
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or port not in (None, 443)
        or not host.endswith(".infoniqa.io")
        or parsed.path != _LIST_PATH
        or parsed.fragment
    ):
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 2 or dict(query) != {"init": "true", "j": "jobexchange"}:
        return None
    origin = f"https://{host}"
    list_url = f"{origin}{_LIST_PATH}"
    return _Board(
        board_url=url,
        origin=origin,
        host=host,
        list_url=list_url,
        search_url=f"{list_url}?search=true",
    )


class _ShellParser(HTMLParser):
    def __init__(self, board: _Board) -> None:
        super().__init__()
        self.board = board
        self.body_classes: list[set[str]] = []
        self.captions: list[str] = []
        self.logo_alts: list[str] = []
        self.quicksearch_urls: list[str] = []
        self.forms: list[tuple[str, str]] = []
        self.form_jobexchange: list[str] = []
        self.form_csrf: list[str] = []
        self.scripts: list[str] = []
        self._caption_chunks: list[str] | None = None
        self._in_search_form = False
        self._in_script = False

    def handle_starttag(self, tag: str, values: list[tuple[str, str | None]]) -> None:
        attrs = _attrs(values)
        classes = _classes(attrs.get("class", ""))
        tag = tag.lower()
        if tag == "body":
            self.body_classes.append(classes)
        elif tag == "h1" and "caption" in classes:
            if self._caption_chunks is not None:
                raise ValueError("Infoniqa shell nested employer headings")
            self._caption_chunks = []
        elif tag == "img" and attrs.get("alt") and "/styles/jobexchange/" in attrs.get("src", ""):
            self.logo_alts.append(attrs.get("alt", ""))
        elif tag == "a" and "menuQuicksearch" in classes:
            self.quicksearch_urls.append(urljoin(self.board.list_url, attrs.get("href", "")))
        elif tag == "form" and attrs.get("id") == "jobOfferSearch":
            if self._in_search_form:
                raise ValueError("Infoniqa shell nested search forms")
            self._in_search_form = True
            self.forms.append((attrs.get("method", "").lower(), attrs.get("action", "")))
        elif tag == "input" and self._in_search_form:
            if attrs.get("name") == "j":
                self.form_jobexchange.append(attrs.get("value", ""))
            elif attrs.get("name") == "_csrf":
                self.form_csrf.append(attrs.get("value", ""))
        elif tag == "script":
            self._in_script = True

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h1" and self._caption_chunks is not None:
            self.captions.append(_clean_text(self._caption_chunks))
            self._caption_chunks = None
        elif tag == "form" and self._in_search_form:
            self._in_search_form = False
        elif tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._caption_chunks is not None:
            self._caption_chunks.append(data)
        if self._in_script:
            self.scripts.append(data)


def _parse_shell(document: str, board: _Board, expected_employer: str | None) -> _Shell:
    parser = _ShellParser(board)
    parser.feed(document)
    parser.close()

    if parser.body_classes != [{"jobOfferList"}]:
        raise ValueError("Infoniqa shell is missing its unique listing body identity")
    if len(parser.captions) != 1 or not parser.captions[0].startswith("Stellenangebote der "):
        raise ValueError("Infoniqa shell is missing its unique employer heading")
    employer_name = parser.captions[0].removeprefix("Stellenangebote der ")
    if not employer_name or len(employer_name) > 200:
        raise ValueError("Infoniqa shell exposed an invalid employer name")
    if expected_employer is not None and employer_name != expected_employer:
        raise ValueError("Infoniqa shell employer does not match the configured employer")
    if parser.logo_alts != [f"{employer_name} Logo"]:
        raise ValueError("Infoniqa shell logo does not match the employer heading")

    expected_quicksearch = f"{board.list_url}?j=jobexchange"
    if parser.quicksearch_urls != [expected_quicksearch]:
        raise ValueError("Infoniqa shell quick-search link crossed board identity")
    if parser.forms != [("post", "")]:
        raise ValueError("Infoniqa shell search form changed unexpectedly")
    if parser.form_jobexchange != ["jobexchange"] or len(parser.form_csrf) != 1:
        raise ValueError("Infoniqa shell search form is missing its session identity")
    csrf = parser.form_csrf[0]
    try:
        canonical_csrf = str(UUID(csrf))
    except (ValueError, AttributeError):
        canonical_csrf = ""
    if canonical_csrf != csrf.lower():
        raise ValueError("Infoniqa shell exposed an invalid CSRF token")

    scripts = "\n".join(parser.scripts)
    required_protocol = (
        "url: '/hcm/jobexchange/showJobOfferList.do?search=true'",
        "showNextJobOffers: 'true'",
        "hasNextJobOffers: 'true'",
    )
    if any(marker not in scripts for marker in required_protocol):
        raise ValueError("Infoniqa shell no longer exposes the expected pagination protocol")
    if scripts.count(csrf) < 3:
        raise ValueError("Infoniqa shell pagination protocol is not bound to the form session")
    return _Shell(csrf=csrf, employer_name=employer_name)


def _canonical_job_url(raw_url: str, board: _Board) -> str | None:
    parsed = urlparse(raw_url)
    if parsed.scheme or parsed.netloc or parsed.path != "showJobOfferDetail.do" or parsed.fragment:
        return None
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if len(query) != 3 or len({key for key, _value in query}) != 3:
        return None
    values = dict(query)
    if set(values) != {"jobOfferId", "j", "organizationUnitId"}:
        return None
    job_id = values["jobOfferId"]
    if (
        _JOB_ID_RE.fullmatch(job_id) is None
        or values["j"] != "jobexchange"
        or values["organizationUnitId"] != ""
    ):
        return None
    return f"{board.origin}{_DETAIL_PATH}?jobOfferId={job_id}&j=jobexchange&organizationUnitId="


class _ResultParser(HTMLParser):
    def __init__(self, board: _Board) -> None:
        super().__init__()
        self.board = board
        self.urls: list[str] = []
        self.titles: list[str] = []
        self.info_texts: list[str] = []
        self.scripts: list[str] = []
        self.job_list_markers = 0
        self.next_markers = 0
        self.invalid_job = False
        self._job_li_depth = 0
        self._job_url: str | None = None
        self._title_chunks: list[str] | None = None
        self._info_chunks: list[str] | None = None
        self._in_script = False

    def handle_starttag(self, tag: str, values: list[tuple[str, str | None]]) -> None:
        attrs = _attrs(values)
        classes = _classes(attrs.get("class", ""))
        tag = tag.lower()
        if tag == "ul" and attrs.get("id") == "jobOffers":
            self.job_list_markers += 1
        elif tag == "span" and attrs.get("id") == "showNextJobOffers":
            self.next_markers += 1
        elif tag == "p" and "searchResultInfo" in classes:
            if self._info_chunks is not None:
                self.invalid_job = True
            self._info_chunks = []
        elif tag == "script":
            self._in_script = True

        if tag == "li" and "jobOffer" in classes:
            if self._job_li_depth:
                self.invalid_job = True
                return
            onclick_urls = _LOCATION_RE.findall(attrs.get("onclick", ""))
            onkeydown_urls = _LOCATION_RE.findall(attrs.get("onkeydown", ""))
            if len(onclick_urls) != 1 or len(onkeydown_urls) != 1:
                self.invalid_job = True
                self._job_url = None
            else:
                onclick = _canonical_job_url(onclick_urls[0], self.board)
                onkeydown = _canonical_job_url(onkeydown_urls[0], self.board)
                if onclick is None or onclick != onkeydown:
                    self.invalid_job = True
                    self._job_url = None
                else:
                    self._job_url = onclick
            self._job_li_depth = 1
            self._title_chunks = None
        elif tag == "li" and self._job_li_depth:
            self._job_li_depth += 1
        elif tag == "h2" and self._job_li_depth and "jobOfferDescription" in classes:
            if self._title_chunks is not None:
                self.invalid_job = True
            self._title_chunks = []

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        if tag == "h2" and self._title_chunks is not None:
            title = _clean_text(self._title_chunks)
            if not title or len(title) > 300:
                self.invalid_job = True
            else:
                self.titles.append(title)
            self._title_chunks = None
        elif tag == "li" and self._job_li_depth:
            self._job_li_depth -= 1
            if self._job_li_depth == 0:
                if self._job_url is None:
                    self.invalid_job = True
                else:
                    self.urls.append(self._job_url)
                self._job_url = None
        elif tag == "p" and self._info_chunks is not None:
            self.info_texts.append(_clean_text(self._info_chunks))
            self._info_chunks = None
        elif tag == "script":
            self._in_script = False

    def handle_data(self, data: str) -> None:
        if self._title_chunks is not None:
            self._title_chunks.append(data)
        if self._info_chunks is not None:
            self._info_chunks.append(data)
        if self._in_script:
            self.scripts.append(data)

    def finish(self) -> None:
        if self._job_li_depth or self._job_url is not None or self._title_chunks is not None:
            self.invalid_job = True
        if self.invalid_job or len(self.titles) != len(self.urls):
            raise ValueError("Infoniqa result contained a malformed job row")


def _parse_search(document: str, board: _Board) -> _SearchResult:
    parser = _ResultParser(board)
    parser.feed(document)
    parser.close()
    parser.finish()
    if parser.job_list_markers != 1 or parser.next_markers != 1:
        raise ValueError("Infoniqa search fragment is missing pagination containers")
    if len(parser.info_texts) != 1:
        raise ValueError("Infoniqa search fragment is missing its authoritative result count")
    info_counts = _INFO_COUNT_RE.findall(parser.info_texts[0])
    button_counts = _BUTTON_COUNT_RE.findall("\n".join(parser.scripts))
    if len(info_counts) != 1 or len(button_counts) != 1 or info_counts != button_counts:
        raise ValueError("Infoniqa search fragment exposed inconsistent result counts")
    total = int(info_counts[0])
    if total > MAX_JOBS:
        raise ValueError(f"Infoniqa board exceeds the {MAX_JOBS:,}-job safety cap")
    if len(set(parser.urls)) != len(parser.urls) or len(parser.urls) > total:
        raise ValueError("Infoniqa initial result fragment is inconsistent with its total")
    return _SearchResult(total=total, urls=tuple(parser.urls))


def _parse_page(document: str, board: _Board) -> tuple[str, ...]:
    parser = _ResultParser(board)
    parser.feed(document)
    parser.close()
    parser.finish()
    if parser.job_list_markers or parser.next_markers or parser.info_texts:
        raise ValueError("Infoniqa pagination page unexpectedly repeated the search envelope")
    if not parser.urls or len(set(parser.urls)) != len(parser.urls):
        raise ValueError("Infoniqa pagination returned an empty or duplicate page")
    return tuple(parser.urls)


def _validate_content_type(headers: dict[str, str], expected: str, stage: str) -> None:
    content_type = headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if content_type != expected:
        raise ValueError(f"Infoniqa {stage} returned unexpected content type {content_type!r}")


async def _fetch_shell(client: httpx.AsyncClient, board: _Board) -> str:
    headers: dict[str, str] = {}
    document = await fetch_text_page_with_retry(
        client,
        board.board_url,
        headers={"Accept": "text/html,application/xhtml+xml"},
        follow_redirects=False,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        max_bytes=MAX_HTML_BYTES,
        response_headers=headers,
        log_event="infoniqa.shell_backoff",
    )
    if document is None:  # Strict status handling makes this unreachable.
        raise RuntimeError("Infoniqa shell fetch returned no document")
    _validate_content_type(headers, "text/html", "shell")
    return document


async def _post(
    client: httpx.AsyncClient,
    board: _Board,
    *,
    url: str,
    data: list[tuple[str, str]],
    expected_content_type: str,
    stage: str,
) -> str:
    headers: dict[str, str] = {}
    document = await fetch_text_page_with_retry(
        client,
        url,
        method="POST",
        content=urlencode(data).encode(),
        headers={
            "Accept": (
                "application/json, text/javascript, */*; q=0.01"
                if expected_content_type == "application/json"
                else "text/html, */*; q=0.01"
            ),
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": board.origin,
            "Referer": board.board_url,
            "X-Requested-With": "XMLHttpRequest",
        },
        follow_redirects=False,
        end_of_pagination_statuses=(),
        require_nonempty=True,
        max_bytes=MAX_HTML_BYTES,
        response_headers=headers,
        log_event=f"infoniqa.{stage}_backoff",
    )
    if document is None:  # Strict status handling makes this unreachable.
        raise RuntimeError(f"Infoniqa {stage} returned no document")
    _validate_content_type(headers, expected_content_type, stage)
    return document


async def _has_next(client: httpx.AsyncClient, board: _Board, shell: _Shell) -> bool:
    document = await _post(
        client,
        board,
        url=board.list_url,
        data=[("hasNextJobOffers", "true"), ("_csrf", shell.csrf)],
        expected_content_type="application/json",
        stage="has_next",
    )
    value = document.strip()
    if value not in {"true", "false"}:
        raise ValueError("Infoniqa has-next response was not a JSON boolean")
    return value == "true"


async def _crawl(
    board: _Board,
    client: httpx.AsyncClient,
    expected_employer: str | None,
) -> tuple[set[str], str]:
    shell = _parse_shell(await _fetch_shell(client, board), board, expected_employer)
    search_document = await _post(
        client,
        board,
        url=board.search_url,
        data=[("j", "jobexchange"), ("_csrf", shell.csrf)],
        expected_content_type="text/html",
        stage="search",
    )
    search = _parse_search(search_document, board)
    urls = set(search.urls)
    page = 0
    while await _has_next(client, board, shell):
        page += 1
        if page > MAX_PAGES:
            raise ValueError("Infoniqa pagination exceeded the page safety cap")
        page_document = await _post(
            client,
            board,
            url=board.list_url,
            data=[
                ("showNextJobOffers", "true"),
                ("j", "jobexchange"),
                ("_csrf", shell.csrf),
            ],
            expected_content_type="text/html",
            stage="page",
        )
        page_urls = set(_parse_page(page_document, board))
        if overlap := urls & page_urls:
            raise ValueError(f"Infoniqa pagination repeated {len(overlap)} jobs")
        urls.update(page_urls)
        if len(urls) > search.total:
            raise ValueError("Infoniqa pagination exceeded its authoritative result count")

    if len(urls) != search.total:
        raise ValueError(
            f"Infoniqa pagination returned {len(urls)} unique jobs, expected {search.total}"
        )
    log.info(
        "infoniqa.discovered",
        host=board.host,
        employer=shell.employer_name,
        jobs=len(urls),
        pages=page,
    )
    return urls, shell.employer_name


async def discover(board: dict, client: httpx.AsyncClient, pw=None) -> set[str]:
    """Return the complete session-scoped inventory for one Infoniqa employer."""
    _ = pw
    resolved = _board_from_url(board["board_url"])
    if resolved is None:
        raise ValueError("Infoniqa monitor requires a canonical HTTPS jobexchange listing URL")
    metadata = board.get("metadata") or {}
    employer_name = metadata.get("employer_name")
    if (
        not isinstance(employer_name, str)
        or employer_name != employer_name.strip()
        or not employer_name
        or len(employer_name) > 200
    ):
        raise ValueError("Infoniqa monitor requires a non-empty employer_name")
    urls, _name = await _crawl(resolved, client, employer_name)
    return urls


async def can_handle(url: str, client: httpx.AsyncClient | None = None, pw=None) -> dict | None:
    """Recognize and live-validate a canonical Infoniqa employer board."""
    _ = pw
    board = _board_from_url(url)
    if board is None or client is None:
        return None
    try:
        urls, employer_name = await _crawl(board, client, None)
    except TDMReservedError:
        raise
    except Exception:
        log.debug("infoniqa.probe_failed", url=url, exc_info=True)
        return None
    return {"employer_name": employer_name, "jobs": len(urls)}


async def save_raw(
    artifact_dir: Path,
    board_url: str,
    metadata: dict,
    client: httpx.AsyncClient,
) -> None:
    _ = metadata
    await save_text_response(
        artifact_dir,
        client,
        board_url,
        filename="infoniqa-shell.html",
        follow_redirects=False,
    )


register("infoniqa", discover, cost=10, can_handle=can_handle, save_raw=save_raw)
