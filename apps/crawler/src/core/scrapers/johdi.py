"""Johdi Suite offer-detail API scraper."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx
import structlog

from src.core.monitors.johdi import detail_url, validated_config
from src.core.scrapers import JobContent, register

log = structlog.get_logger()

_OFFER_FRAGMENT_RE = re.compile(r"^/offer/(?P<id>\d+)/[A-Za-z0-9_-]+/?$")


def _offer_id(url: str) -> str | None:
    match = _OFFER_FRAGMENT_RE.fullmatch(urlparse(url).fragment)
    return match.group("id") if match else None


def _location(raw: dict) -> list[str] | None:
    parts: list[str] = []
    for key in ("work_place", "city", "canton"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip() and value.strip() not in parts:
            parts.append(value.strip())
    country = raw.get("country_code")
    if isinstance(country, str) and country.strip():
        normalized = country.strip().upper()
        if normalized not in parts:
            parts.append(normalized)
    return [", ".join(parts)] if parts else None


def _parse_detail(raw: dict, locale: str) -> JobContent:
    description_parts = [
        value.strip()
        for key in ("introduction", "description")
        if isinstance((value := raw.get(key)), str) and value.strip()
    ]
    metadata = {
        key: value
        for key, value in {
            "id": raw.get("id"),
            "reference": raw.get("ref"),
            "subtitle": raw.get("subtitle"),
            "sector": raw.get("sector"),
            "activity_from": raw.get("activity_from"),
            "activity_to": raw.get("activity_to"),
            "expiration_date": raw.get("expiration_date"),
            "apply_url": raw.get("apply_link") or raw.get("postulation_url"),
        }.items()
        if value not in (None, "")
    }
    return JobContent(
        title=raw.get("title") or None,
        description="\n".join(description_parts) or None,
        locations=_location(raw),
        employment_type=raw.get("contract_type") or None,
        date_posted=raw.get("publication_date") or None,
        language=locale.split("-", 1)[0].lower(),
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch and parse one Johdi offer detail record."""
    _ = kwargs
    offer_id = _offer_id(url)
    validated = validated_config(config)
    if offer_id is None or validated is None:
        log.warning("johdi_scraper.invalid_config_or_url", url=url)
        return JobContent()

    api_url = detail_url(
        validated["company_key"],
        validated["flow"],
        offer_id,
        validated["locale"],
    )
    response = await http.get(
        api_url,
        headers={"Accept": "application/json"},
        follow_redirects=True,
        timeout=30,
    )
    if response.status_code != 200:
        log.warning("johdi_scraper.detail_failed", url=url, status=response.status_code)
        return JobContent()
    payload = response.json()
    if not isinstance(payload, dict):
        log.warning("johdi_scraper.invalid_detail", url=url)
        return JobContent()
    return _parse_detail(payload, validated["locale"])


register("johdi", scrape)
