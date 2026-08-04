from __future__ import annotations

import hashlib
import io
from pathlib import Path
from typing import Any

import httpx
import polars as pl
import pytest

from src.ats_inventory.impact import ImpactCache, ImpactValidationError
from src.ats_inventory.models import Coverage, InventoryRow, InventorySnapshot
from src.ats_inventory.tenant_keys import tenant_key


def _parquet(rows: list[dict[str, Any]], *, drop: str | None = None) -> bytes:
    columns: dict[str, list[object]] = {
        "url": [],
        "company": [],
        "ats_id": [],
        "location": [],
        "country_iso": [],
        "is_remote": [],
        "posted_at": [],
    }
    if any("tenant_key" in row for row in rows):
        columns["tenant_key"] = []
    for row in rows:
        for column in columns:
            columns[column].append(row.get(column))
    if drop is not None:
        columns.pop(drop)
    output = io.BytesIO()
    pl.DataFrame(columns).write_parquet(output)
    return output.getvalue()


def _job(
    url: str,
    company: str,
    *,
    ats_id: str = "job-1",
    location: str = "Zurich",
    country: str = "CH",
    remote: bool | str = False,
    posted_at: str = "2026-08-01T12:00:00+00:00",
    direct: str | None = None,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "url": url,
        "company": company,
        "ats_id": ats_id,
        "location": location,
        "country_iso": country,
        "is_remote": remote,
        "posted_at": posted_at,
    }
    if direct is not None:
        value["tenant_key"] = direct
    return value


def _snapshot(
    rows: list[InventoryRow],
    artifacts: dict[str, bytes | None],
    *,
    generation: int = 1,
) -> InventorySnapshot:
    by_ats: dict[str, dict[str, Any]] = {}
    for family, body in artifacts.items():
        if body is None:
            continue
        by_ats[family] = {
            "parquet": f"https://storage.stapply.ai/jobhive/v1/{family}/jobs.parquet",
            "parquet_sha256": hashlib.sha256(body).hexdigest(),
            "parquet_size_bytes": len(body),
            "rows": pl.read_parquet(io.BytesIO(body)).height,
        }
    manifest_hash = hashlib.sha256(f"manifest-{generation}".encode()).hexdigest()
    inventory_hash = hashlib.sha256(
        "\n".join(f"{row.ats}|{row.url}" for row in rows).encode()
    ).hexdigest()
    family_counts: dict[str, int] = {}
    for row in rows:
        family_counts[row.ats] = family_counts.get(row.ats, 0) + 1
    total = len(rows)
    coverage = Coverage(
        total_rows=total,
        supported_rows=total,
        unsupported_rows=0,
        excluded_rows=0,
        candidate_rows=total,
        supported_candidate_rows=total,
        classified_coverage_pct=100.0,
        candidate_coverage_pct=100.0,
        unsupported_families=(),
        excluded_families=(),
        candidate_generation_allowed=True,
    )
    return InventorySnapshot(
        manifest_sha256=manifest_hash,
        manifest_etag=None,
        inventory_sha256=inventory_hash,
        generated_at=f"2026-08-0{generation}T12:00:00+00:00",
        validated_at=f"2026-08-0{generation}T12:01:00+00:00",
        manifest={"version": "2.0", "by_ats": by_ats},
        rows=tuple(rows),
        family_counts=family_counts,
        coverage=coverage,
        changed=True,
        etag_revalidated=False,
        new_families=(),
        removed_families=(),
        changed_urls=0,
    )


class _Artifacts:
    def __init__(self, bodies: dict[str, bytes | int]) -> None:
        self.bodies = bodies
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        result = self.bodies.get(url, 404)
        if isinstance(result, int):
            return httpx.Response(result)
        return httpx.Response(200, content=result)


async def _sync(cache: Path, feed: _Artifacts, snapshot: InventorySnapshot):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(feed.handler), follow_redirects=True
    ) as client:
        return await ImpactCache(
            cache,
            client,
            max_cache_bytes=8 * 1024 * 1024,
            max_artifact_bytes=4 * 1024 * 1024,
            min_free_bytes=0,
        ).sync(snapshot)


async def test_unchanged_manifest_uses_compact_cache_without_artifact_downloads(
    tmp_path: Path,
) -> None:
    rows = [
        InventoryRow(
            ats="greenhouse",
            name="Acme",
            slug="acme",
            url="https://job-boards.greenhouse.io/acme",
        ),
        InventoryRow(
            ats="greenhouse",
            name="Zero Jobs",
            slug="zero",
            url="https://job-boards.greenhouse.io/zero",
        ),
        InventoryRow(
            ats="adp",
            name="Unknown Impact",
            slug="tenant/portal",
            url=(
                "https://workforcenow.adp.com/mascsr/default/mdf/recruitment/"
                "recruitment.html?cid=tenant&ccId=portal"
            ),
        ),
    ]
    greenhouse = _parquet(
        [
            _job(
                "https://job-boards.greenhouse.io/acme/jobs/1",
                "Acme",
                remote=True,
            ),
            _job(
                "https://job-boards.greenhouse.io/acme/jobs/2",
                "Acme",
                location="Berlin",
                country="DE",
            ),
        ]
    )
    url = "https://storage.stapply.ai/jobhive/v1/greenhouse/jobs.parquet"
    feed = _Artifacts({url: greenhouse})
    source = _snapshot(rows, {"greenhouse": greenhouse, "adp": None})

    first = await _sync(tmp_path, feed, source)
    orphan = tmp_path / "families" / "greenhouse" / f"{'f' * 64}.json"
    orphan.write_text("orphan")
    second = await _sync(tmp_path, feed, source)

    assert first.downloads == 1
    assert second.downloads == 0
    assert second.changed is False
    assert feed.requests == [url]
    assert not orphan.exists()
    assert [company.name for company in second.ranked()] == [
        "Acme",
        "Unknown Impact",
        "Zero Jobs",
    ]
    acme = second.ranked()[0]
    assert acme.active_jobs == 2
    assert acme.remote_jobs == 1
    assert acme.location_count == 2
    assert acme.country_codes == ("CH", "DE")


async def test_changed_inventory_reuses_same_family_summary(tmp_path: Path) -> None:
    body = _parquet(
        [
            _job(
                "https://jobs.ashbyhq.com/acme/1",
                "Acme",
                direct="acme",
                remote="true",
            )
        ]
    )
    url = "https://storage.stapply.ai/jobhive/v1/ashby/jobs.parquet"
    feed = _Artifacts({url: body})
    initial = _snapshot(
        [InventoryRow("ashby", "Acme", "acme", "https://jobs.ashbyhq.com/acme")],
        {"ashby": body},
    )
    await _sync(tmp_path, feed, initial)
    changed = _snapshot(
        [
            InventoryRow("ashby", "Acme", "acme", "https://jobs.ashbyhq.com/acme"),
            InventoryRow("ashby", "New Co", "new-co", "https://jobs.ashbyhq.com/new-co"),
        ],
        {"ashby": body},
        generation=2,
    )

    impact = await _sync(tmp_path, feed, changed)

    assert impact.downloads == 0
    assert len(impact.companies) == 2
    assert impact.ranked()[0].remote_jobs == 1
    assert feed.requests == [url]


async def test_shared_job_route_keeps_company_fallback_buckets_separate(
    tmp_path: Path,
) -> None:
    rows = [
        InventoryRow("workable", "Acme Incorporated", "acme", "https://apply.workable.com/acme"),
        InventoryRow("workable", "Beta Limited", "beta", "https://apply.workable.com/beta"),
    ]
    body = _parquet(
        [
            _job("https://apply.workable.com/j/ACME1/", "acme"),
            _job("https://apply.workable.com/j/BETA1/", "beta"),
            _job("https://apply.workable.com/j/BETA2/", "beta"),
        ]
    )
    url = "https://storage.stapply.ai/jobhive/v1/workable/jobs.parquet"
    impact = await _sync(
        tmp_path,
        _Artifacts({url: body}),
        _snapshot(rows, {"workable": body}),
    )

    assert [(company.slug, company.active_jobs) for company in impact.ranked()] == [
        ("beta", 2),
        ("acme", 1),
    ]


async def test_company_name_can_match_unique_composite_slug_root(tmp_path: Path) -> None:
    rows = [
        InventoryRow("moka", "Tesla China", "tesla/123", "https://app.mokahr.com"),
        InventoryRow("moka", "Example China", "example/456", "https://hire-r1.mokahr.com"),
    ]
    body = _parquet(
        [
            _job("https://app.mokahr.com/job/1", "tesla"),
            _job("https://app.mokahr.com/job/2", "tesla"),
        ]
    )
    url = "https://storage.stapply.ai/jobhive/v1/moka/jobs.parquet"
    impact = await _sync(
        tmp_path,
        _Artifacts({url: body}),
        _snapshot(rows, {"moka": body}),
    )

    assert impact.ranked()[0].slug == "tesla/123"
    assert impact.ranked()[0].active_jobs == 2


@pytest.mark.parametrize("failure", ["partial", "checksum", "schema"])
async def test_bad_changed_artifact_preserves_last_known_good(tmp_path: Path, failure: str) -> None:
    rows = [InventoryRow("lever", "Acme", "acme", "https://jobs.lever.co/acme")]
    stable = _parquet([_job("https://jobs.lever.co/acme/1", "Acme")])
    url = "https://storage.stapply.ai/jobhive/v1/lever/jobs.parquet"
    feed = _Artifacts({url: stable})
    await _sync(tmp_path, feed, _snapshot(rows, {"lever": stable}))
    pointer_before = (tmp_path / "current.json").read_bytes()

    if failure == "schema":
        declared = _parquet([_job("https://jobs.lever.co/acme/2", "Acme")], drop="company")
        delivered = declared
    else:
        declared = _parquet([_job("https://jobs.lever.co/acme/2", "Acme")])
        delivered = declared[:-5] if failure == "partial" else declared + b"corrupt"
    feed.bodies[url] = delivered

    with pytest.raises(ImpactValidationError):
        await _sync(tmp_path, feed, _snapshot(rows, {"lever": declared}, generation=2))

    assert (tmp_path / "current.json").read_bytes() == pointer_before


async def test_partial_refresh_resumes_completed_family_without_redownload(
    tmp_path: Path,
) -> None:
    rows = [
        InventoryRow("greenhouse", "Green", "green", "https://job-boards.greenhouse.io/green"),
        InventoryRow("lever", "Lever", "lever", "https://jobs.lever.co/lever"),
    ]
    green_v1 = _parquet([_job("https://job-boards.greenhouse.io/green/jobs/1", "Green")])
    lever_v1 = _parquet([_job("https://jobs.lever.co/lever/1", "Lever")])
    green_url = "https://storage.stapply.ai/jobhive/v1/greenhouse/jobs.parquet"
    lever_url = "https://storage.stapply.ai/jobhive/v1/lever/jobs.parquet"
    feed = _Artifacts({green_url: green_v1, lever_url: lever_v1})
    await _sync(
        tmp_path,
        feed,
        _snapshot(rows, {"greenhouse": green_v1, "lever": lever_v1}),
    )
    pointer_before = (tmp_path / "current.json").read_bytes()
    green_v2 = _parquet(
        [
            _job("https://job-boards.greenhouse.io/green/jobs/2", "Green"),
            _job("https://job-boards.greenhouse.io/green/jobs/3", "Green"),
        ]
    )
    lever_v2 = _parquet([_job("https://jobs.lever.co/lever/2", "Lever")])
    feed.bodies = {green_url: green_v2, lever_url: 503}
    changed = _snapshot(
        rows,
        {"greenhouse": green_v2, "lever": lever_v2},
        generation=2,
    )

    with pytest.raises(httpx.HTTPStatusError):
        await _sync(tmp_path, feed, changed)
    assert (tmp_path / "current.json").read_bytes() == pointer_before

    feed.bodies[lever_url] = lever_v2
    requests_before = feed.requests.count(green_url)
    recovered = await _sync(tmp_path, feed, changed)

    assert recovered.downloads == 1
    assert feed.requests.count(green_url) == requests_before
    assert recovered.ranked()[0].active_jobs == 2


async def test_disk_pressure_preserves_last_known_good(tmp_path: Path) -> None:
    rows = [InventoryRow("workable", "Acme", "acme", "https://apply.workable.com/acme")]
    first_body = _parquet([_job("https://apply.workable.com/acme/j/1", "Acme")])
    url = "https://storage.stapply.ai/jobhive/v1/workable/jobs.parquet"
    feed = _Artifacts({url: first_body})
    await _sync(tmp_path, feed, _snapshot(rows, {"workable": first_body}))
    pointer_before = (tmp_path / "current.json").read_bytes()
    changed_body = _parquet([_job("https://apply.workable.com/acme/j/2", "Acme")])
    feed.bodies[url] = changed_body
    changed = _snapshot(rows, {"workable": changed_body}, generation=2)

    async with httpx.AsyncClient(transport=httpx.MockTransport(feed.handler)) as client:
        cache = ImpactCache(
            tmp_path,
            client,
            max_cache_bytes=8 * 1024 * 1024,
            max_artifact_bytes=4 * 1024 * 1024,
            min_free_bytes=10**18,
        )
        with pytest.raises(ImpactValidationError, match="free-space reserve"):
            await cache.sync(changed)

    assert (tmp_path / "current.json").read_bytes() == pointer_before


@pytest.mark.parametrize(
    ("family", "board_url", "job_url", "ats_id"),
    [
        (
            "adp",
            "https://workforcenow.adp.com/x?cid=Tenant&ccId=Portal",
            "https://workforcenow.adp.com/job?ccId=Portal&cid=Tenant&id=1",
            None,
        ),
        (
            "greenhouse",
            "https://job-boards.greenhouse.io/acme",
            "https://job-boards.greenhouse.io/acme/jobs/1",
            None,
        ),
        (
            "paylocity",
            "https://recruiting.paylocity.com/Recruiting/Jobs/All/"
            "8e0feae7-e42f-437e-97b1-53b917185eed",
            "https://recruiting.paylocity.com/Recruiting/Jobs/Details/4375627",
            "8e0feae7-e42f-437e-97b1-53b917185eed:4375627",
        ),
        (
            "oracle",
            "https://eluq.fa.us2.oraclecloud.com/hcmUI/CandidateExperience/en/sites/CX_2001",
            "https://eluq.fa.us2.oraclecloud.com/?mode=jobs&site_number=CX_2001#21210",
            None,
        ),
        (
            "softgarden",
            "https://baufi24.career.softgarden.de/",
            "https://baufi24.softgarden.io/job/123",
            None,
        ),
        (
            "taleo",
            "https://phg.tbe.taleo.net/phg03/ats/careers/v2/searchResults?org=ACME&cws=1",
            "https://phg.tbe.taleo.net/phg03/ats/careers/v2/viewRequisition?org=ACME&cws=37&rid=1",
            None,
        ),
        (
            "workday",
            "https://acme.wd1.myworkdayjobs.com/external",
            "https://acme.wd1.myworkdayjobs.com/external/job/Zurich/Engineer_R1",
            None,
        ),
    ],
)
def test_tenant_extractors_share_board_and_job_identity(
    family: str, board_url: str, job_url: str, ats_id: str | None
) -> None:
    assert tenant_key(family, board_url) == tenant_key(family, job_url, ats_id)
