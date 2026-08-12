from __future__ import annotations

from datetime import UTC, datetime
from typing import cast

import asyncpg
import pytest

from src.labeller.sampling import (
    CANDIDATE_LIMIT_CEILING,
    CANDIDATE_LIMIT_FLOOR,
    CANDIDATES_PER_REQUESTED_SAMPLE,
    SAMPLE_CANDIDATES_SQL,
    Sample,
    _rarity_weights,
    sample_postings,
)


class _Pool:
    def __init__(self, rows: list[dict[str, object]]):
        self.rows = rows
        self.calls: list[tuple[str, tuple[datetime, datetime, int]]] = []

    async def fetch(
        self,
        query: str,
        start: datetime,
        end: datetime,
        limit: int,
    ) -> list[dict[str, object]]:
        self.calls.append((query, (start, end, limit)))
        return self.rows


def _row(
    posting_id: str,
    company_id: str,
    *,
    occupation_id: int | None = None,
    primary_locale: str | None = None,
) -> dict[str, object]:
    return {
        "posting_id": posting_id,
        "company_id": company_id,
        "source_url": f"https://example/{posting_id}",
        "occupation_id": occupation_id,
        "primary_locale": primary_locale,
    }


async def test_sample_is_seeded_company_diverse_and_uses_a_stable_bounded_order() -> None:
    rows = [
        _row("a-3", "a", occupation_id=9, primary_locale="it-CH"),
        _row("a-2", "a", occupation_id=1, primary_locale="en-US"),
        _row("a-1", "a", occupation_id=1, primary_locale="en_US"),
        _row("b-1", "b", occupation_id=2, primary_locale="de-CH"),
        _row("c-1", "c", occupation_id=3, primary_locale="fr"),
    ]
    end = datetime(2026, 7, 23, tzinfo=UTC)
    first_pool = _Pool(rows)
    second_pool = _Pool(rows)

    first = await sample_postings(
        cast(asyncpg.Pool, first_pool),
        end_time_utc=end,
        count=4,
        seed=5929,
    )
    second = await sample_postings(
        cast(asyncpg.Pool, second_pool),
        end_time_utc=end,
        count=4,
        seed=5929,
    )

    assert first == second
    assert len(first) == 4
    assert {sample.company_id for sample in first[:3]} == {"a", "b", "c"}
    assert {sample.company_id for sample in first} == {"a", "b", "c"}
    assert {"de", "fr"} <= {sample.primary_locale for sample in first}
    assert all(
        sample.primary_locale
        and "-" not in sample.primary_locale
        and "_" not in sample.primary_locale
        for sample in first
    )
    assert "ORDER BY p.first_seen_at DESC, p.id" in SAMPLE_CANDIDATES_SQL
    assert "LIMIT $3" in SAMPLE_CANDIDATES_SQL
    assert first_pool.calls[0][1][2] == CANDIDATE_LIMIT_FLOOR
    assert first_pool.calls == second_pool.calls


@pytest.mark.parametrize(
    ("count", "expected_limit"),
    [
        (1, CANDIDATE_LIMIT_FLOOR),
        (100, 100 * CANDIDATES_PER_REQUESTED_SAMPLE),
        (1_000, CANDIDATE_LIMIT_CEILING),
    ],
)
async def test_candidate_loading_is_bounded_and_scales_with_batch(
    count: int,
    expected_limit: int,
) -> None:
    pool = _Pool([])
    await sample_postings(
        cast(asyncpg.Pool, pool),
        end_time_utc=datetime(2026, 7, 23, tzinfo=UTC),
        count=count,
        seed=1,
    )
    assert pool.calls[0][1][2] == expected_limit


def test_missing_strata_are_neutral_instead_of_receiving_a_rarity_bonus() -> None:
    candidates = [
        *[
            Sample(f"common-{index}", "a", f"https://example/common-{index}", 1, "en")
            for index in range(9)
        ],
        Sample("rare", "a", "https://example/rare", 2, "de"),
        Sample("missing", "a", "https://example/missing"),
    ]

    weights = _rarity_weights(candidates)

    assert weights["rare"] > weights["common-0"]
    assert weights["common-0"] == 1.0
    assert weights["missing"] == 1.0


@pytest.mark.parametrize("rare_dimension", ["occupation", "locale"])
async def test_underrepresented_stratum_gets_more_selection_opportunity(
    rare_dimension: str,
) -> None:
    """The weighted tail favors rare observable data, not missing values."""

    rows: list[dict[str, object]] = []
    for index in range(10):
        rows.append(
            _row(
                f"a-common-{index}",
                "a",
                occupation_id=1 if rare_dimension == "occupation" else None,
                primary_locale="en" if rare_dimension == "locale" else None,
            )
        )
    rows.append(
        _row(
            "a-rare",
            "a",
            occupation_id=2 if rare_dimension == "occupation" else None,
            primary_locale="de" if rare_dimension == "locale" else None,
        )
    )
    rows.append(
        _row(
            "b-only",
            "b",
            occupation_id=1 if rare_dimension == "occupation" else None,
            primary_locale="en" if rare_dimension == "locale" else None,
        )
    )

    end = datetime(2026, 7, 23, tzinfo=UTC)
    rare_hits = 0
    representative_common_hits = 0
    for seed in range(3_000):
        selected = await sample_postings(
            cast(asyncpg.Pool, _Pool(rows)),
            end_time_utc=end,
            count=3,
            seed=seed,
        )
        selected_ids = {sample.posting_id for sample in selected}
        rare_hits += "a-rare" in selected_ids
        representative_common_hits += "a-common-0" in selected_ids
        # The first two slots remain one-per-company before any weighted tail.
        assert {sample.company_id for sample in selected[:2]} == {"a", "b"}

    assert rare_hits > representative_common_hits * 1.5


async def test_non_positive_count_does_not_query_the_database() -> None:
    pool = _Pool([])
    samples = await sample_postings(
        cast(asyncpg.Pool, pool),
        end_time_utc=datetime(2026, 7, 23, tzinfo=UTC),
        count=0,
        seed=1,
    )
    assert samples == []
    assert pool.calls == []


async def test_rejects_non_positive_lookback() -> None:
    with pytest.raises(ValueError, match="window_hours must be positive"):
        await sample_postings(
            cast(asyncpg.Pool, _Pool([])),
            end_time_utc=datetime(2026, 7, 23, tzinfo=UTC),
            window_hours=0,
            count=1,
        )
