"""Johdi Suite public careers monitor.

Johdi's embeddable widget exposes a public list endpoint. Custom careers
pages configure the widget with an opaque company key, publication flow, and
locale on ``#ats-offers``. The monitor returns canonical widget-route URLs;
the Johdi scraper fetches full details on the normal scrape schedule.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, fetch_page_text, register
from src.shared.http_retry import fetch_text_page_with_retry
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

API_ORIGIN = "https://ats.johdisuite.ch"
MAX_JOBS = 50_000
MAX_JSON_RESPONSE_BYTES = 8_000_000
_COMPANY_KEY_RE = re.compile(r"[A-Za-z0-9_=-]{16,4096}")
_FLOW_RE = re.compile(r"[A-Za-z0-9_-]{1,64}")
_LOCALE_RE = re.compile(r"[A-Za-z]{2}(?:-[A-Za-z]{2})?")
_OFFER_ID_RE = re.compile(r"[1-9]\d{0,18}")


class _WidgetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.matches = 0
        self.configs: list[dict[str, str]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key.casefold(): value for key, value in attrs if value is not None}
        if values.get("id") != "ats-offers":
            return
        self.matches += 1
        company_key = values.get("data-company-hash-key")
        flow = values.get("data-flow")
        locale = values.get("data-locale")
        if company_key and flow and locale:
            self.configs.append(
                {
                    "company_key": company_key,
                    "flow": flow,
                    "locale": locale,
                }
            )


def validated_config(config: Mapping[str, object]) -> dict[str, str] | None:
    company_key = config.get("company_key")
    flow = config.get("flow")
    locale = config.get("locale")
    if not isinstance(company_key, str):
        return None
    if not isinstance(flow, str):
        return None
    if not isinstance(locale, str):
        return None
    if _COMPANY_KEY_RE.fullmatch(company_key) is None:
        return None
    if _FLOW_RE.fullmatch(flow) is None:
        return None
    if _LOCALE_RE.fullmatch(locale) is None:
        return None
    return {"company_key": company_key, "flow": flow, "locale": locale}


def _widget_config(page: str) -> dict[str, str] | None:
    parser = _WidgetParser()
    parser.feed(page)
    if parser.matches != 1 or len(parser.configs) != 1:
        return None
    return validated_config(parser.configs[0])


def list_url(company_key: str, flow: str, locale: str) -> str:
    return f"{API_ORIGIN}/api/company/{company_key}/publicationFlows/{flow}/offers/{locale}"


def detail_url(company_key: str, flow: str, offer_id: int | str, locale: str) -> str:
    return (
        f"{API_ORIGIN}/api/company/{company_key}/publicationFlows/{flow}/offer/{offer_id}/{locale}"
    )


def normalized_offer_id(value: object) -> str | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return str(value) if value > 0 else None
    if not isinstance(value, str) or _OFFER_ID_RE.fullmatch(value) is None:
        return None
    return str(int(value))


def _job_url(board_url: str, raw: dict) -> str | None:
    offer_id = normalized_offer_id(raw.get("id"))
    if offer_id is None:
        return None
    parsed = urlparse(board_url)
    if (
        parsed.scheme != "https"
        or parsed.hostname is None
        or parsed.username is not None
        or parsed.password is not None
    ):
        return None
    base = parsed._replace(fragment="").geturl().rstrip("/")
    # Johdi's route requires a slug but fetches details by ID only. A constant
    # route slug keeps the official deep link functional without letting a
    # translated or edited title churn the crawler identity.
    return f"{base}#/offer/{offer_id}/job"


async def fetch_json(client: httpx.AsyncClient, url: str) -> object:
    response_headers: dict[str, str] = {}
    text = await fetch_text_page_with_retry(
        client,
        url,
        headers={"Accept": "application/json"},
        follow_redirects=False,
        end_of_pagination_statuses=set(),
        require_nonempty=True,
        max_bytes=MAX_JSON_RESPONSE_BYTES,
        response_headers=response_headers,
    )
    if text is None:
        raise ValueError("Johdi Suite API returned no response")
    content_type = response_headers.get("content-type", "").partition(";")[0].strip().casefold()
    if content_type != "application/json" and not content_type.endswith("+json"):
        raise ValueError("Johdi Suite API response was not JSON content")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("Johdi Suite API response was not valid JSON") from exc


async def _fetch_offers(config: dict[str, str], client: httpx.AsyncClient) -> list[dict]:
    url = list_url(config["company_key"], config["flow"], config["locale"])
    try:
        payload = await fetch_json(client, url)
    except Exception as exc:
        status = getattr(exc, "last_status", None)
        if status == 404:
            raise BoardGoneError(
                "Johdi Suite board returned 404", url=url, status_code=404
            ) from exc
        raise
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("Johdi Suite offers response is not a list of objects")
    return payload


async def _resolve_config(board: dict, client: httpx.AsyncClient) -> dict[str, str]:
    metadata = board.get("metadata") or {}
    configured = validated_config(metadata)
    if metadata and configured is None:
        raise ValueError("Johdi Suite configured widget identity is invalid")
    page = await fetch_page_text(board["board_url"], client, max_chars=2_000_000)
    discovered = _widget_config(page or "")
    if discovered is None:
        raise ValueError(f"Johdi Suite widget configuration not found at {board['board_url']!r}")
    if configured is not None and configured != discovered:
        raise ValueError("Johdi Suite configured widget identity does not match the official page")
    return discovered


async def discover(board: dict, client: httpx.AsyncClient, pw=None):
    """Fetch the complete active-offer inventory as canonical widget URLs."""
    _ = pw
    config = await _resolve_config(board, client)
    offers = await _fetch_offers(config, client)
    urls = {url for raw in offers if (url := _job_url(board["board_url"], raw))}
    if offers and len(urls) != len(offers):
        raise ValueError("Johdi Suite offers response contains invalid or duplicate offers")

    log.info("johdi.discovered", board_url=board["board_url"], jobs=len(urls))
    if len(urls) > MAX_JOBS:
        log.warning("johdi.truncated", total=len(urls), cap=MAX_JOBS)
        return truncated_url_result(urls)
    return urls


async def can_handle(
    url: str,
    client: httpx.AsyncClient | None = None,
    pw=None,
) -> dict | None:
    """Detect an embedded Johdi widget and verify its public offers feed."""
    _ = pw
    if client is None:
        return None
    page = await fetch_page_text(url, client, max_chars=2_000_000)
    config = _widget_config(page or "")
    if config is None:
        return None
    try:
        offers = await _fetch_offers(config, client)
    except Exception:
        log.debug("johdi.probe_failed", url=url, exc_info=True)
        return None
    return {**config, "jobs": len(offers)}


register("johdi", discover, cost=10, can_handle=can_handle)
