from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from src.labeller.mining_guard import MiningGuardError, _assert_current


@pytest.mark.parametrize("count", [0, 1])
async def test_corpus_requires_all_current_postings_to_be_unreserved(monkeypatch, count):
    pool = AsyncMock()
    pool.fetchval.return_value = count
    close = AsyncMock()
    monkeypatch.setattr("src.db.create_local_pool", AsyncMock(return_value=pool))
    monkeypatch.setattr("src.db.close_all_pools", close)
    ids = [str(uuid4()), str(uuid4())]
    with pytest.raises(MiningGuardError, match="Reserved or missing"):
        await _assert_current(ids)
    close.assert_awaited_once()


async def test_corpus_db_outage_does_not_authorize_mining(monkeypatch):
    monkeypatch.setattr("src.db.create_local_pool", AsyncMock(side_effect=OSError("unavailable")))
    monkeypatch.setattr("src.db.close_all_pools", AsyncMock())
    with pytest.raises(MiningGuardError, match="eligibility unavailable"):
        await _assert_current([str(uuid4())])


async def test_prepared_enrichment_rechecks_before_provider_submission():
    from src.core.enrich.batch import submit_batch
    from src.core.enrich.providers import BatchRequest

    pool, provider = AsyncMock(), AsyncMock()
    pool.fetchval.return_value = True
    request = BatchRequest(custom_id=str(uuid4()), system_prompt="system", user_content="retained")
    with pytest.raises(RuntimeError, match="TDM reservation"):
        await submit_batch(pool, provider, [request], [request.custom_id])
    provider.submit_batch.assert_not_awaited()
