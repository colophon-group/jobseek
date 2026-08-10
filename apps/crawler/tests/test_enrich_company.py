from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from src.core.enrich.company import _extract_from_wikidata, _get_ticker_symbol
from src.workspace.commands.config import _merge_enrichment_extras


def _string_snak(value: str) -> dict:
    return {"datavalue": {"type": "string", "value": value}}


def test_ticker_comes_from_p249_qualifier_not_exchange_entity() -> None:
    claims = {
        "P414": [
            {
                "rank": "normal",
                "mainsnak": {
                    "datavalue": {
                        "type": "wikibase-entityid",
                        "value": {"id": "Q82059"},
                    }
                },
                "qualifiers": {"P249": [_string_snak("NTNX")]},
            }
        ]
    }

    assert _get_ticker_symbol(claims) == "NTNX"


def test_ticker_prefers_preferred_rank_deterministically() -> None:
    claims = {
        "P414": [
            {"rank": "normal", "qualifiers": {"P249": [_string_snak("ZZZ")]}},
            {"rank": "preferred", "qualifiers": {"P249": [_string_snak("BBB")]}},
            {"rank": "preferred", "qualifiers": {"P249": [_string_snak("AAA")]}},
        ]
    }

    assert _get_ticker_symbol(claims) == "AAA"


def test_exchange_without_ticker_qualifier_is_not_a_ticker() -> None:
    claims = {
        "P414": [
            {
                "rank": "normal",
                "mainsnak": {
                    "datavalue": {
                        "type": "wikibase-entityid",
                        "value": {"id": "Q13677"},
                    }
                },
            }
        ]
    }

    assert _get_ticker_symbol(claims) is None


@pytest.mark.asyncio
async def test_wikidata_extraction_serializes_symbol() -> None:
    payload = {
        "entities": {
            "Q1": {
                "claims": {
                    "P414": [
                        {
                            "rank": "normal",
                            "qualifiers": {"P249": [_string_snak("ACME")]},
                        }
                    ]
                }
            }
        }
    }
    with (
        patch("src.core.enrich.company._wikidata_api", new=AsyncMock(return_value=payload)),
        patch("src.core.enrich.company._resolve_labels", new=AsyncMock(return_value={})),
    ):
        result = await _extract_from_wikidata(AsyncMock(), "Q1")

    assert result["ticker_symbol"] == "ACME"
    assert result["wikidata_id"] == "Q1"


def test_enrichment_extras_only_fill_missing_values() -> None:
    existing = {
        "tickerSymbol": "RIGHT",
        "wikidataId": "",
        "operatorMetadata": {"reviewed": True},
    }
    inferred = {
        "tickerSymbol": "WRONG",
        "wikidataId": "Q123",
        "legalName": "Acme, Inc.",
    }

    assert _merge_enrichment_extras(existing, inferred) == {
        "tickerSymbol": "RIGHT",
        "wikidataId": "Q123",
        "operatorMetadata": {"reviewed": True},
        "legalName": "Acme, Inc.",
    }
