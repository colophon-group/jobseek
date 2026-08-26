"""Johdi Suite offer-detail API scraper."""

from __future__ import annotations

import re
from urllib.parse import urlparse

import httpx

from src.core.monitors.johdi import (
    detail_url,
    fetch_json,
    normalized_offer_id,
    validated_config,
)
from src.core.scrapers import JobContent, register

_OFFER_FRAGMENT_RE = re.compile(r"^/offer/(?P<id>\d+)/[A-Za-z0-9_-]+/?$")


def _offer_id(url: str) -> str | None:
    match = _OFFER_FRAGMENT_RE.fullmatch(urlparse(url).fragment)
    return normalized_offer_id(match.group("id")) if match else None


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
    title = raw.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError("Johdi Suite detail omitted its title")
    description_parts = [
        value.strip()
        for key in ("introduction", "description")
        if isinstance((value := raw.get(key)), str) and value.strip()
    ]
    if not description_parts:
        raise ValueError("Johdi Suite detail omitted its description")
    apply_url = raw.get("apply_link") or raw.get("postulation_url")
    metadata = {
        key: value
        for key, value in {
            "id": normalized_offer_id(raw.get("id")),
            "reference": raw.get("ref"),
            "subtitle": raw.get("subtitle"),
            "sector": raw.get("sector"),
            "activity_from": raw.get("activity_from"),
            "activity_to": raw.get("activity_to"),
            "expiration_date": raw.get("expiration_date"),
            "apply_url": apply_url if isinstance(apply_url, str) else None,
        }.items()
        if value not in (None, "")
    }
    contract_type = raw.get("contract_type")
    publication_date = raw.get("publication_date")
    return JobContent(
        title=title.strip(),
        description="\n".join(description_parts),
        locations=_location(raw),
        employment_type=contract_type.strip() if isinstance(contract_type, str) else None,
        date_posted=publication_date.strip() if isinstance(publication_date, str) else None,
        language=locale.split("-", 1)[0].lower(),
        metadata=metadata or None,
    )


async def scrape(url: str, config: dict, http: httpx.AsyncClient, **kwargs) -> JobContent:
    """Fetch and parse one Johdi offer detail record."""
    _ = kwargs
    offer_id = _offer_id(url)
    validated = validated_config(config)
    if offer_id is None or validated is None:
        raise ValueError("Johdi Suite scraper requires a valid stable offer URL and config")

    api_url = detail_url(
        validated["company_key"],
        validated["flow"],
        offer_id,
        validated["locale"],
    )
    payload = await fetch_json(http, api_url)
    if not isinstance(payload, dict):
        raise ValueError("Johdi Suite detail response is not an object")
    if normalized_offer_id(payload.get("id")) != offer_id:
        raise ValueError("Johdi Suite detail identity does not match the stable offer URL")
    return _parse_detail(payload, validated["locale"])


register("johdi", scrape)
