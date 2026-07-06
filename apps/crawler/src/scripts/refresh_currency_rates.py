"""Refresh ``currency_rate`` from the ECB daily reference-rate feed.

ECB publishes rates as "1 EUR = N <currency>". The crawler stores the
inverse multiplier in ``currency_rate.to_eur`` because salary extraction
converts source amounts with ``amount * to_eur``.
"""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation

import asyncpg
import httpx
import structlog
from asyncpg.pool import PoolConnectionProxy

log = structlog.get_logger()

ECB_DAILY_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
_ONE = Decimal("1")
_TO_EUR_QUANT = Decimal("0.000000000001")
_CURRENCY_RE = re.compile(r"^[A-Z]{3}$")

_UPSERT_CURRENCY_RATE = """
INSERT INTO currency_rate (currency, to_eur, updated_at)
VALUES ($1, $2, $3)
ON CONFLICT (currency) DO UPDATE
SET to_eur = EXCLUDED.to_eur,
    updated_at = EXCLUDED.updated_at
"""


@dataclass(frozen=True)
class CurrencyRate:
    currency: str
    to_eur: Decimal


@dataclass(frozen=True)
class CurrencyRateSnapshot:
    rate_date: date
    rates: tuple[CurrencyRate, ...]


@dataclass(frozen=True)
class RefreshCurrencyRatesResult:
    rate_date: date
    count: int
    updated_at: datetime
    dry_run: bool


def _parse_rate(value: str, currency: str) -> Decimal:
    try:
        rate = Decimal(value)
    except InvalidOperation as exc:
        raise ValueError(f"invalid ECB rate for {currency}: {value!r}") from exc
    if rate <= 0:
        raise ValueError(f"invalid ECB rate for {currency}: {value!r}")
    return rate


def parse_ecb_daily_rates(xml: str | bytes) -> CurrencyRateSnapshot:
    """Parse the ECB daily XML payload into EUR conversion multipliers."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as exc:
        raise ValueError("invalid ECB XML payload") from exc

    dated_cubes = [node for node in root.iter() if "time" in node.attrib]
    if len(dated_cubes) != 1:
        raise ValueError(f"expected exactly one dated ECB Cube, found {len(dated_cubes)}")

    dated_cube = dated_cubes[0]
    try:
        rate_date = date.fromisoformat(dated_cube.attrib["time"])
    except ValueError as exc:
        raise ValueError(f"invalid ECB rate date: {dated_cube.attrib['time']!r}") from exc

    rates: dict[str, Decimal] = {"EUR": _ONE}
    for node in dated_cube:
        currency = node.attrib.get("currency", "")
        rate_text = node.attrib.get("rate")
        if not currency and rate_text is None:
            continue
        if not _CURRENCY_RE.fullmatch(currency):
            raise ValueError(f"invalid ECB currency code: {currency!r}")
        if rate_text is None:
            raise ValueError(f"missing ECB rate for {currency}")
        rate = _parse_rate(rate_text, currency)
        rates[currency] = (_ONE / rate).quantize(_TO_EUR_QUANT)

    if len(rates) == 1:
        raise ValueError("ECB payload did not contain any non-EUR rates")

    return CurrencyRateSnapshot(
        rate_date=rate_date,
        rates=tuple(CurrencyRate(currency, to_eur) for currency, to_eur in sorted(rates.items())),
    )


async def fetch_ecb_daily_rates(http: httpx.AsyncClient) -> CurrencyRateSnapshot:
    response = await http.get(ECB_DAILY_URL, headers={"Accept": "application/xml,text/xml;q=0.9"})
    response.raise_for_status()
    return parse_ecb_daily_rates(response.text)


async def upsert_currency_rates(
    conn: asyncpg.Connection | PoolConnectionProxy,
    snapshot: CurrencyRateSnapshot,
    *,
    updated_at: datetime | None = None,
) -> int:
    updated_at = updated_at or datetime.now(UTC)
    records = [(rate.currency, rate.to_eur, updated_at) for rate in snapshot.rates]
    async with conn.transaction():
        await conn.executemany(_UPSERT_CURRENCY_RATE, records)
    return len(records)


async def refresh_currency_rates(
    pool: asyncpg.Pool | None,
    http: httpx.AsyncClient,
    *,
    dry_run: bool = False,
) -> RefreshCurrencyRatesResult:
    snapshot = await fetch_ecb_daily_rates(http)
    updated_at = datetime.now(UTC)
    if dry_run:
        log.info(
            "currency_rates.refresh.dry_run",
            rate_date=snapshot.rate_date.isoformat(),
            count=len(snapshot.rates),
        )
        return RefreshCurrencyRatesResult(
            rate_date=snapshot.rate_date,
            count=len(snapshot.rates),
            updated_at=updated_at,
            dry_run=True,
        )

    if pool is None:
        raise ValueError("pool is required when dry_run is false")

    async with pool.acquire() as conn:
        count = await upsert_currency_rates(conn, snapshot, updated_at=updated_at)

    log.info(
        "currency_rates.refresh.complete",
        rate_date=snapshot.rate_date.isoformat(),
        updated_at=updated_at.isoformat(),
        count=count,
    )
    return RefreshCurrencyRatesResult(
        rate_date=snapshot.rate_date,
        count=count,
        updated_at=updated_at,
        dry_run=False,
    )
