from __future__ import annotations

from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from src.scripts.refresh_currency_rates import (
    CurrencyRate,
    CurrencyRateSnapshot,
    parse_ecb_daily_rates,
    upsert_currency_rates,
)

ECB_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<gesmes:Envelope
    xmlns:gesmes="http://www.gesmes.org/xml/2002-08-01"
    xmlns="http://www.ecb.int/vocabulary/2002-08-01/eurofxref">
  <gesmes:subject>Reference rates</gesmes:subject>
  <Cube>
    <Cube time="2026-07-06">
      <Cube currency="USD" rate="1.25"/>
      <Cube currency="PLN" rate="4.00"/>
      <Cube currency="JPY" rate="200"/>
    </Cube>
  </Cube>
</gesmes:Envelope>
"""


def test_parse_ecb_daily_rates_inverts_ecb_rates_and_adds_eur() -> None:
    snapshot = parse_ecb_daily_rates(ECB_SAMPLE)

    assert snapshot.rate_date == date(2026, 7, 6)
    assert snapshot.rates == (
        CurrencyRate("EUR", Decimal("1")),
        CurrencyRate("JPY", Decimal("0.005000000000")),
        CurrencyRate("PLN", Decimal("0.250000000000")),
        CurrencyRate("USD", Decimal("0.800000000000")),
    )


def test_parse_ecb_daily_rates_rejects_missing_dated_cube() -> None:
    with pytest.raises(ValueError, match="dated ECB Cube"):
        parse_ecb_daily_rates("<Envelope><Cube /></Envelope>")


def test_parse_ecb_daily_rates_rejects_invalid_rate() -> None:
    xml = ECB_SAMPLE.replace('rate="1.25"', 'rate="0"')
    with pytest.raises(ValueError, match="invalid ECB rate"):
        parse_ecb_daily_rates(xml)


async def test_upsert_currency_rates_uses_currency_conflict_key() -> None:
    conn = AsyncMock()
    tx = AsyncMock()
    conn.transaction = MagicMock(return_value=tx)
    tx.__aenter__ = AsyncMock(return_value=None)
    tx.__aexit__ = AsyncMock(return_value=False)
    updated_at = datetime(2026, 7, 6, 12, 0, tzinfo=UTC)
    snapshot = CurrencyRateSnapshot(
        rate_date=date(2026, 7, 6),
        rates=(
            CurrencyRate("EUR", Decimal("1")),
            CurrencyRate("USD", Decimal("0.800000000000")),
        ),
    )

    count = await upsert_currency_rates(conn, snapshot, updated_at=updated_at)

    assert count == 2
    sql, records = conn.executemany.call_args.args
    assert "ON CONFLICT (currency) DO UPDATE" in sql
    assert records == [
        ("EUR", Decimal("1"), updated_at),
        ("USD", Decimal("0.800000000000"), updated_at),
    ]


@pytest.mark.parametrize("dry_run", [False, True])
async def test_cli_delegates_currency_refresh_before_python_pool_open(monkeypatch, dry_run) -> None:
    import argparse

    from src import cli

    class ExecComplete(Exception):
        pass

    execute = MagicMock(side_effect=ExecComplete)
    pool = AsyncMock(side_effect=AssertionError("Python pool must not open"))
    monkeypatch.setattr(
        cli,
        "parse_args",
        lambda: argparse.Namespace(command="refresh-currency-rates", dry_run=dry_run),
    )
    monkeypatch.setattr(cli.os, "execvp", execute)
    monkeypatch.setattr(cli, "create_local_pool", pool)
    with pytest.raises(ExecComplete):
        await cli.run()
    args = ["go-typesense-exporter", "--refresh-currency-rates"]
    if dry_run:
        args.append("--dry-run")
    execute.assert_called_once_with(args[0], args)
    pool.assert_not_awaited()
