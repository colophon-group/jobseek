"""Reviewed public career-page links stay separate from crawler endpoints."""

from __future__ import annotations

import polars as pl
import pytest

from src.sync import _canonical_public_career_url, sync_source_coverage_links


def test_public_career_url_preserves_reviewed_non_root_path() -> None:
    assert (
        _canonical_public_career_url("https://Careers.Example.com:443/team/jobs")
        == "https://careers.example.com/team/jobs"
    )


@pytest.mark.parametrize(
    "url",
    [
        "http://example.com/jobs",
        "https://user:pass@example.com/jobs",
        "https://127.0.0.1/jobs",
        "https://service.internal/jobs",
        "https://example.test/jobs",
        "https://careers.example/jobs",
        "https://example.com:8080/jobs",
        "https://example.com/jobs?token=x",
        "https://example.com/jobs#fragment",
        "https://example.com/jobs%2Fprivate",
        "https://example.com/jobs here",
        "https://example.com/a/../jobs",
        "https://example.com/jobs\\private",
        "https://example.com/jobs\x00secret",
    ],
)
def test_public_career_url_rejects_unsafe_sources(url: str) -> None:
    with pytest.raises(ValueError):
        _canonical_public_career_url(url)


@pytest.mark.asyncio
async def test_reviewed_link_registry_rejects_duplicate_or_unapproved_rows() -> None:
    duplicate = pl.DataFrame(
        {
            "company_slug": ["acme", "acme"],
            "board_slug": ["acme-careers", "acme-careers"],
            "public_url": ["https://example.com/jobs", "https://example.com/openings"],
            "provenance": ["employer-careers", "ats-careers"],
        }
    )
    with pytest.raises(ValueError):
        await sync_source_coverage_links(None, duplicate, dry_run=True)  # type: ignore[arg-type]
    unapproved = duplicate.head(1).with_columns(pl.lit("unreviewed").alias("provenance"))
    with pytest.raises(ValueError):
        await sync_source_coverage_links(None, unapproved, dry_run=True)  # type: ignore[arg-type]
