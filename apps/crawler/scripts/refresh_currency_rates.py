"""Refresh currency exchange rates from ECB and recompute salary_eur.

Data source: ECB daily reference rates (free, no API key, no rate limits).
https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml

Run weekly via CI cron or manually:
  uv run python scripts/refresh_currency_rates.py
"""

from __future__ import annotations

import asyncio
import sys
import xml.etree.ElementTree as ET

import asyncpg
import httpx

from src.config import settings

ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ECB_NS = {
    "gesmes": "http://www.gesmes.org/xml/2002-08-01",
    "eurofxref": "http://www.ecb.int/vocabulary/2002-08-01/eurofxref",
}


def _parse_ecb_rates(xml_text: str) -> dict[str, float]:
    """Parse ECB XML and return {currency: to_eur} mapping.

    ECB publishes rates as units-per-EUR (e.g. USD=1.1478 means 1 EUR = 1.1478 USD).
    We need the inverse: to_eur = 1 / rate (e.g. 1 USD = 0.8712 EUR).
    """
    root = ET.fromstring(xml_text)
    rates: dict[str, float] = {"EUR": 1.0}

    for cube in root.iter():
        if cube.tag.endswith("}Cube") and "currency" in cube.attrib:
            currency = cube.attrib["currency"]
            rate = float(cube.attrib["rate"])
            rates[currency] = 1.0 / rate

    return rates


async def main() -> int:
    # Fetch ECB rates
    async with httpx.AsyncClient(timeout=30) as http:
        resp = await http.get(ECB_URL)
        resp.raise_for_status()

    rates = _parse_ecb_rates(resp.text)
    print(f"Fetched {len(rates)} rates from ECB")

    if len(rates) < 10:
        print("ERROR: Too few rates parsed, aborting")
        return 1

    pool = await asyncpg.create_pool(
        settings.database_url, min_size=1, max_size=2, statement_cache_size=0
    )
    assert pool is not None

    try:
        async with pool.acquire() as conn:
            # Load old rates for comparison
            old_rows = await conn.fetch("SELECT currency, to_eur FROM currency_rate")
            old_rates = {r["currency"]: float(r["to_eur"]) for r in old_rows}

            # Upsert new rates
            upserted = 0
            for currency, to_eur in rates.items():
                old = old_rates.get(currency)
                result = await conn.execute(
                    """
                    INSERT INTO currency_rate (currency, to_eur, updated_at)
                    VALUES ($1, $2, now())
                    ON CONFLICT (currency) DO UPDATE
                    SET to_eur = $2, updated_at = now()
                """,
                    currency,
                    to_eur,
                )
                if result:
                    upserted += 1
                if old is not None and abs(old - to_eur) > 0.0001:
                    pct = (to_eur - old) / old * 100
                    print(f"  {currency}: {old:.6f} -> {to_eur:.6f} ({pct:+.2f}%)")

            print(f"\nUpserted {upserted} rates")

            # Recompute salary_eur for all rows with salary data
            result = await conn.execute("""
                UPDATE job_posting
                SET salary_eur = round(salary_min * cr.to_eur)::integer
                FROM currency_rate cr
                WHERE job_posting.salary_currency = cr.currency
                  AND job_posting.salary_min IS NOT NULL
            """)
            count = int(result.split()[-1]) if result else 0
            print(f"Recomputed salary_eur for {count} postings")

    finally:
        await pool.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
