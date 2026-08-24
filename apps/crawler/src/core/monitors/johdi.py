"""Johdi Suite public careers monitor.

Johdi's embeddable widget exposes a public list endpoint. Custom careers
pages configure the widget with an opaque company key, publication flow, and
locale on ``#ats-offers``. The monitor returns canonical widget-route URLs;
the Johdi scraper fetches full details on the normal scrape schedule.
"""

from __future__ import annotations

from html.parser import HTMLParser
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors import BoardGoneError, fetch_page_text, register
from src.shared.truncation import truncated_url_result

log = structlog.get_logger()

API_ORIGIN = "https://ats.johdisuite.ch"
MAX_JOBS = 50_000


class _WidgetParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.config: dict[str, str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.config is not None:
            return
        values = {key.casefold(): value for key, value in attrs if value is not None}
        if values.get("id") != "ats-offers":
            return
        company_key = values.get("data-company-hash-key")
        flow = values.get("data-flow")
        locale = values.get("data-locale")
        if company_key and flow and locale:
            self.config = {
                "company_key": company_key,
                "flow": flow,
                "locale": locale,
            }


def validated_config(config: dict) -> dict[str, str] | None:
    company_key = config.get("company_key")
    flow = config.get("flow")
    locale = config.get("locale")
    if not all(isinstance(value, str) for value in (company_key, flow, locale)):
        return None
    if not (16 <= len(company_key) <= 4_096):
        return None
    if not company_key.replace("-", "").replace("_", "").replace("=", "").isalnum():
        return None
    if not flow.replace("-", "").replace("_", "").isalnum():
        return None
    if not (2 <= len(locale) <= 10 and locale.replace("-", "").isalnum()):
        return None
    return {"company_key": company_key, "flow": flow, "locale": locale}


def _widget_config(page: str) -> dict[str, str] | None:
    parser = _WidgetParser()
    parser.feed(page)
    return validated_config(parser.config or {})


def list_url(company_key: str, flow: str, locale: str) -> str:
    return f"{API_ORIGIN}/api/company/{company_key}/publicationFlows/{flow}/offers/{locale}"


def detail_url(company_key: str, flow: str, offer_id: int | str, locale: str) -> str:
    return (
        f"{API_ORIGIN}/api/company/{company_key}/publicationFlows/{flow}/offer/{offer_id}/{locale}"
    )


def _job_url(board_url: str, raw: dict) -> str | None:
    offer_id = raw.get("id")
    slug = raw.get("slug")
    if not isinstance(offer_id, (int, str)) or not isinstance(slug, str) or not slug.strip():
        return None
    parsed = urlparse(board_url)
    if parsed.scheme not in {"http", "https"} or parsed.hostname is None:
        return None
    base = parsed._replace(fragment="").geturl().rstrip("/")
    return f"{base}#/offer/{offer_id}/{slug.strip()}"


async def _fetch_offers(config: dict[str, str], client: httpx.AsyncClient) -> list[dict]:
    url = list_url(config["company_key"], config["flow"], config["locale"])
    response = await client.get(
        url,
        headers={"Accept": "application/json"},
        follow_redirects=True,
        timeout=30,
    )
    if response.status_code == 404:
        raise BoardGoneError("Johdi Suite board returned 404", url=url, status_code=404)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
        raise ValueError("Johdi Suite offers response is not a list of objects")
    return payload


async def _resolve_config(board: dict, client: httpx.AsyncClient) -> dict[str, str]:
    configured = validated_config(board.get("metadata") or {})
    if configured is not None:
        return configured

    page = await fetch_page_text(board["board_url"], client, max_chars=2_000_000)
    config = _widget_config(page or "")
    if config is None:
        raise ValueError(f"Johdi Suite widget configuration not found at {board['board_url']!r}")
    return config


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
