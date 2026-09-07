"""Provider-identity contracts for the MediaMarktSaturn boards."""

from __future__ import annotations

import csv
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx
import pytest

from src.core.monitor import (
    MonitorResult,
    _apply_url_allowlist,
    _apply_url_transform,
    monitor_one_stream,
)
from src.core.monitors import _REGISTRY, DiscoveredJob, MonitorType

_BOARDS_PATH = Path(__file__).parents[1] / "data" / "boards.csv"
_GLOBAL_BOARD = "mediamarktsaturn-careers-global"
_DTB_INLINE_BOARDS = {"mediamarktsaturn-dtb-headquarters"}
_DTB_JOIN_BOARD = "mediamarktsaturn-dtb-technicians"
_SOURCE_ALLOWLIST = (
    r"^https://careers\.mediamarktsaturn\.com/"
    r"[A-Za-z][A-Za-z0-9]*/job/[^/?#]+/\d+/$"
)
_STABLE_TRANSFORM = {
    "find": (
        r"^https://careers\.mediamarktsaturn\.com/"
        r"[A-Za-z][A-Za-z0-9]*/job/[^/?#]+/(\d+)/$"
    ),
    "replace": r"https://careers.mediamarktsaturn.com/job/_/\1/",
    "collision_policy": "prefer_source_pattern",
    "collision_preferred_source_patterns": [
        r"^https://careers\.mediamarktsaturn\.com/MediaMarktSaturn/",
        r"^https://careers\.mediamarktsaturn\.com/MediaMarktCH/",
        r"^https://careers\.mediamarktsaturn\.com/",
    ],
    "collision_canonical_identity_regex": (
        r"^https://careers\.mediamarktsaturn\.com/job/_/(\d+)/$"
    ),
    "collision_identity_metadata_key": "id",
    "collision_stream_buffer_limit": 5_000,
}


def _board_rows() -> dict[str, dict[str, str]]:
    with _BOARDS_PATH.open(newline="") as handle:
        return {
            row["board_slug"]: row
            for row in csv.DictReader(handle)
            if row["company_slug"] == "mediamarktsaturn"
        }


def _config(row: dict[str, str]) -> dict:
    return json.loads(row["monitor_config"])


def _canonicalize(source_urls: set[str]) -> MonitorResult:
    jobs = {
        url: DiscoveredJob(
            url=url,
            title=("German" if "/MediaMarktSaturn/" in url else "Localized"),
            metadata={"id": urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]},
        )
        for url in source_urls
    }
    result = MonitorResult(urls=source_urls, jobs_by_url=jobs)
    result = _apply_url_allowlist(result, {"url_allowlist": _SOURCE_ALLOWLIST})
    return _apply_url_transform(result, {"url_transform": _STABLE_TRANSFORM})


def test_global_successfactors_board_has_fail_closed_numeric_identity_contract():
    config = _config(_board_rows()[_GLOBAL_BOARD])

    assert config["url_allowlist"] == _SOURCE_ALLOWLIST
    assert config["url_transform"] == _STABLE_TRANSFORM


def test_global_successfactors_locale_and_title_variants_converge():
    result = _canonicalize(
        {
            "https://careers.mediamarktsaturn.com/MediaMarktCH/job/"
            "Z%C3%BCrich-Verkaufsberater-DE/123456/",
            "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/Paris-Conseiller-FR/123456/",
            "https://careers.mediamarktsaturn.com/MediaWorld/job/Milano-Addetto-IT/987654/",
        }
    )

    assert result.security_filtered_count == 0
    assert result.urls == {
        "https://careers.mediamarktsaturn.com/job/_/123456/",
        "https://careers.mediamarktsaturn.com/job/_/987654/",
    }
    assert result.jobs_by_url is not None
    assert set(result.jobs_by_url) == result.urls
    assert all(job.url == url for url, job in result.jobs_by_url.items())
    assert (
        result.jobs_by_url["https://careers.mediamarktsaturn.com/job/_/123456/"].title == "German"
    )


def test_global_successfactors_contract_rejects_unstable_or_foreign_shapes():
    invalid_urls = {
        "https://careers.mediamarktsaturn.com/MediaMarktCH/job/title/not-numeric/",
        "https://careers.mediamarktsaturn.com/MediaMarktCH/job/123456/",
        "https://careers.mediamarktsaturn.com/job/_/123456/",
        "https://evil.example/MediaMarktCH/job/title/123456/",
    }

    result = _canonicalize(invalid_urls)

    assert result.urls == set()
    assert result.jobs_by_url == {}
    assert result.security_filtered_count == len(invalid_urls)


@pytest.mark.parametrize("reverse", [False, True])
def test_global_successfactors_de_en_alias_selection_is_order_independent(reverse):
    de_url = (
        "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/"
        "Berlin-Verkaufsberater-DE/123456/"
    )
    en_url = "https://careers.mediamarktsaturn.com/MediaMarktCH/job/Zurich-Sales-Advisor-EN/123456/"
    ordered = [de_url, en_url]
    if reverse:
        ordered.reverse()
    jobs = {
        url: DiscoveredJob(
            url=url,
            title="German title" if url == de_url else "English title",
            metadata={"id": "123456"},
        )
        for url in ordered
    }

    result = _apply_url_transform(
        MonitorResult(urls=set(ordered), jobs_by_url=jobs),
        {"url_transform": _STABLE_TRANSFORM},
    )

    assert result.jobs_by_url is not None
    selected = result.jobs_by_url["https://careers.mediamarktsaturn.com/job/_/123456/"]
    assert selected.title == "German title"


def test_global_successfactors_alias_selection_is_hash_seed_independent():
    script = textwrap.dedent(
        f"""
        from src.core.monitor import MonitorResult, _apply_url_transform
        from src.core.monitors import DiscoveredJob
        de = "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/Stelle-DE/123456/"
        en = "https://careers.mediamarktsaturn.com/MediaMarktCH/job/Role-EN/123456/"
        urls = set([de, en])
        jobs = {{
            url: DiscoveredJob(
                url=url,
                title="de" if url == de else "en",
                metadata={{"id": "123456"}},
            )
            for url in urls
        }}
        result = _apply_url_transform(
            MonitorResult(urls=urls, jobs_by_url=jobs),
            {{"url_transform": {json.dumps(_STABLE_TRANSFORM)}}},
        )
        print(result.jobs_by_url["https://careers.mediamarktsaturn.com/job/_/123456/"].title)
        """
    )
    outputs = []
    for seed in ("1", "2", "3", "4", "5", "6", "7", "8"):
        env = {**os.environ, "PYTHONHASHSEED": seed}
        outputs.append(
            subprocess.check_output(
                [sys.executable, "-c", script],
                cwd=Path(__file__).parents[1],
                env=env,
                text=True,
            ).strip()
        )

    assert outputs == ["de"] * len(outputs)


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse_batches", [False, True])
async def test_global_successfactors_aliases_resolve_across_stream_batches(
    reverse_batches,
):
    de_url = (
        "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/"
        "Berlin-Verkaufsberater-DE/123456/"
    )
    en_url = "https://careers.mediamarktsaturn.com/MediaMarktCH/job/Zurich-Sales-Advisor-EN/123456/"
    batches = [
        [DiscoveredJob(url=de_url, title="German title", metadata={"id": "123456"})],
        [DiscoveredJob(url=en_url, title="English title", metadata={"id": "123456"})],
    ]
    if reverse_batches:
        batches.reverse()

    async def stub_discover(_board, _client, pw=None):
        return []

    async def stub_stream(_board, _client, pw=None):
        for batch in batches:
            yield batch

    monitor_type = MonitorType(
        name="__mediamarktsaturn_collision_test__",
        cost=1,
        discover=stub_discover,
        stream=stub_stream,
        rich=True,
    )
    _REGISTRY.append(monitor_type)
    try:
        async with httpx.AsyncClient() as client:
            results = [
                result
                async for result in monitor_one_stream(
                    "https://careers.mediamarktsaturn.com/search/",
                    monitor_type.name,
                    {
                        "url_allowlist": _SOURCE_ALLOWLIST,
                        "url_transform": _STABLE_TRANSFORM,
                    },
                    client,
                )
            ]
    finally:
        _REGISTRY.remove(monitor_type)

    assert len(results) == 1
    assert results[0].jobs_by_url is not None
    selected = results[0].jobs_by_url["https://careers.mediamarktsaturn.com/job/_/123456/"]
    assert selected.title == "German title"


@pytest.mark.asyncio
async def test_global_successfactors_conflicting_same_source_content_fails_stream():
    source_url = (
        "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/"
        "Berlin-Verkaufsberater-DE/123456/"
    )

    async def stub_discover(_board, _client, pw=None):
        return []

    async def stub_stream(_board, _client, pw=None):
        yield [
            DiscoveredJob(
                url=source_url,
                title="First title",
                metadata={"id": "123456"},
            )
        ]
        yield [
            DiscoveredJob(
                url=source_url,
                title="Conflicting title",
                metadata={"id": "123456"},
            )
        ]

    monitor_type = MonitorType(
        name="__mediamarktsaturn_conflict_test__",
        cost=1,
        discover=stub_discover,
        stream=stub_stream,
        rich=True,
    )
    _REGISTRY.append(monitor_type)
    try:
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="conflicting content"):
                async for _result in monitor_one_stream(
                    "https://careers.mediamarktsaturn.com/search/",
                    monitor_type.name,
                    {
                        "url_allowlist": _SOURCE_ALLOWLIST,
                        "url_transform": _STABLE_TRANSFORM,
                    },
                    client,
                ):
                    pass
    finally:
        _REGISTRY.remove(monitor_type)


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse", [False, True])
async def test_global_successfactors_conflicting_same_source_content_fails_same_batch(
    reverse,
):
    source_url = (
        "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/"
        "Berlin-Verkaufsberater-DE/123456/"
    )
    batch = [
        DiscoveredJob(
            url=source_url,
            title="First title",
            metadata={"id": "123456"},
        ),
        DiscoveredJob(
            url=source_url,
            title="Conflicting title",
            metadata={"id": "123456"},
        ),
    ]
    if reverse:
        batch.reverse()

    async def stub_discover(_board, _client, pw=None):
        return []

    async def stub_stream(_board, _client, pw=None):
        yield batch

    monitor_type = MonitorType(
        name="__mediamarktsaturn_same_batch_conflict_test__",
        cost=1,
        discover=stub_discover,
        stream=stub_stream,
        rich=True,
    )
    _REGISTRY.append(monitor_type)
    try:
        async with httpx.AsyncClient() as client:
            with pytest.raises(ValueError, match="collision batch emitted conflicting content"):
                async for _result in monitor_one_stream(
                    "https://careers.mediamarktsaturn.com/search/",
                    monitor_type.name,
                    {
                        "url_allowlist": _SOURCE_ALLOWLIST,
                        "url_transform": _STABLE_TRANSFORM,
                    },
                    client,
                ):
                    pass
    finally:
        _REGISTRY.remove(monitor_type)


@pytest.mark.asyncio
async def test_global_successfactors_identical_same_source_content_coalesces_same_batch():
    source_url = (
        "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/"
        "Berlin-Verkaufsberater-DE/123456/"
    )
    batch = [
        DiscoveredJob(
            url=source_url,
            title="Identical title",
            metadata={"id": "123456"},
        ),
        DiscoveredJob(
            url=source_url,
            title="Identical title",
            metadata={"id": "123456"},
        ),
    ]

    async def stub_discover(_board, _client, pw=None):
        return []

    async def stub_stream(_board, _client, pw=None):
        yield batch

    monitor_type = MonitorType(
        name="__mediamarktsaturn_same_batch_identical_test__",
        cost=1,
        discover=stub_discover,
        stream=stub_stream,
        rich=True,
    )
    _REGISTRY.append(monitor_type)
    try:
        async with httpx.AsyncClient() as client:
            results = [
                result
                async for result in monitor_one_stream(
                    "https://careers.mediamarktsaturn.com/search/",
                    monitor_type.name,
                    {
                        "url_allowlist": _SOURCE_ALLOWLIST,
                        "url_transform": _STABLE_TRANSFORM,
                    },
                    client,
                )
            ]
    finally:
        _REGISTRY.remove(monitor_type)

    assert len(results) == 1
    assert results[0].jobs_by_url is not None
    assert list(results[0].jobs_by_url) == ["https://careers.mediamarktsaturn.com/job/_/123456/"]
    assert (
        results[0].jobs_by_url["https://careers.mediamarktsaturn.com/job/_/123456/"].title
        == "Identical title"
    )


def test_global_successfactors_conflicting_provider_identity_fails_closed():
    source_url = (
        "https://careers.mediamarktsaturn.com/MediaMarktSaturn/job/"
        "Berlin-Verkaufsberater-DE/123456/"
    )
    result = MonitorResult(
        urls={source_url},
        jobs_by_url={
            source_url: DiscoveredJob(
                url=source_url,
                title="Mismatched identity",
                metadata={"id": "999999"},
            )
        },
    )

    with pytest.raises(ValueError, match="identity does not match canonical"):
        _apply_url_transform(result, {"url_transform": _STABLE_TRANSFORM})


def test_dtb_inline_boards_use_salesforce_record_ids_for_identity():
    rows = _board_rows()

    for board_slug in _DTB_INLINE_BOARDS:
        config = _config(rows[board_slug])
        assert config["detail_identity_selector"] == 'label[for^="a7u"]'
        assert config["detail_identity_attribute"] == "for"
        assert config["detail_identity_regex"] == r"^(a7u[A-Za-z0-9]{15})-.+$"


def test_dtb_technicians_uses_verified_join_provider():
    row = _board_rows()[_DTB_JOIN_BOARD]

    assert row["monitor_type"] == "join"
    assert _config(row) == {"slug": "deutsche-technikberatung"}
    assert row["scraper_type"] == "json-ld"


def test_turkey_board_keeps_numeric_provider_id_in_discovered_url():
    config = _config(_board_rows()["mediamarktsaturn-careers-turkey"])

    assert "Home/detail" in config["link_selector"]
    assert "skinNo=39815" in config["url_filter"]
    sample = "https://hr-link.net/Home/detail?id=123456&skinNo=39815"
    query = parse_qs(urlparse(sample).query)
    assert query["id"] == ["123456"]
