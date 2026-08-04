from __future__ import annotations

import csv
import hashlib
import io
import json
from collections import Counter
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.ats_inventory.compat import COMPATIBILITY
from src.ats_inventory.github import (
    CreatedIssue,
    ExistingIssue,
    GitHubCreateOutcomeUnknown,
    reconcile_support_issues,
    render_support_issue,
)
from src.ats_inventory.locking import InventoryRunBusyError, exclusive_run_lock
from src.ats_inventory.source import (
    DEFAULT_MANIFEST_URL,
    InventorySource,
    SourceValidationError,
)
from src.workspace._compat import all_monitor_types

COMPANIES_URL = "https://storage.stapply.ai/jobhive/v1/companies.csv"


def _csv_bytes(rows: list[dict[str, str]], *, header: tuple[str, ...] = ()) -> bytes:
    output = io.StringIO(newline="")
    fields = header or ("ats", "name", "slug", "url")
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    writer.writerows(rows)
    return output.getvalue().encode()


def _manifest_bytes(
    rows: list[dict[str, str]], company_csv: bytes, *, generation: int = 1
) -> bytes:
    counts = Counter(row["ats"] for row in rows)
    family_artifacts = {
        family: {
            "csv": f"https://storage.stapply.ai/jobhive/v1/{family}/companies.csv",
            "rows": count,
            "sha256": hashlib.sha256(f"{family}-companies".encode()).hexdigest(),
            "size_bytes": 100,
        }
        for family, count in counts.items()
    }
    manifest: dict[str, Any] = {
        "version": "2.0",
        "generated_at": f"2026-08-0{generation}T12:00:00+00:00",
        "stats": {"total_companies": len(rows)},
        "companies": {
            "csv": COMPANIES_URL,
            "rows": len(rows),
            "sha256": hashlib.sha256(company_csv).hexdigest(),
            "size_bytes": len(company_csv),
        },
        "by_ats_companies": family_artifacts,
        "by_ats": {family: {"rows": count * 10} for family, count in counts.items()},
    }
    return json.dumps(manifest, sort_keys=True).encode()


def _row(
    ats: str = "greenhouse",
    *,
    name: str = "Example",
    slug: str = "example",
    url: str = "https://boards.greenhouse.io/example",
) -> dict[str, str]:
    return {"ats": ats, "name": name, "slug": slug, "url": url}


class _Feed:
    def __init__(self, manifest: bytes, company_csv: bytes, *, etag: str = '"v1"') -> None:
        self.manifest = manifest
        self.company_csv = company_csv
        self.etag = etag
        self.requests: list[httpx.Request] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if str(request.url) == DEFAULT_MANIFEST_URL:
            if request.headers.get("if-none-match") == self.etag:
                return httpx.Response(304, headers={"etag": self.etag})
            return httpx.Response(200, content=self.manifest, headers={"etag": self.etag})
        if str(request.url) == COMPANIES_URL:
            return httpx.Response(200, content=self.company_csv)
        return httpx.Response(404)


async def _sync(cache: Path, feed: _Feed):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(feed.handler), follow_redirects=True
    ) as client:
        return await InventorySource(cache, client).sync()


async def test_first_sync_is_atomic_and_unchanged_etag_skips_inventory(tmp_path: Path) -> None:
    rows = [_row()]
    company_csv = _csv_bytes(rows)
    feed = _Feed(_manifest_bytes(rows, company_csv), company_csv)

    first = await _sync(tmp_path, feed)
    current = json.loads((tmp_path / "current.json").read_text())
    assert first.changed is True
    assert first.coverage.candidate_coverage_pct == 100.0
    assert current["inventory_sha256"] == hashlib.sha256(company_csv).hexdigest()

    second = await _sync(tmp_path, feed)
    assert second.changed is False
    assert second.etag_revalidated is True
    assert [str(request.url) for request in feed.requests].count(COMPANIES_URL) == 1
    assert feed.requests[-1].headers["if-none-match"] == '"v1"'


async def test_304_with_corrupt_local_object_refetches_and_repairs_cache(tmp_path: Path) -> None:
    rows = [_row()]
    company_csv = _csv_bytes(rows)
    feed = _Feed(_manifest_bytes(rows, company_csv), company_csv)
    first = await _sync(tmp_path, feed)
    inventory_path = tmp_path / "objects" / first.inventory_sha256
    inventory_path.write_bytes(b"corrupt")

    recovered = await _sync(tmp_path, feed)
    assert recovered.rows[0].slug == "example"
    assert recovered.changed is False
    assert recovered.new_families == ()
    assert recovered.removed_families == ()
    assert recovered.changed_urls == 0
    assert hashlib.sha256(inventory_path.read_bytes()).hexdigest() == first.inventory_sha256
    assert [str(request.url) for request in feed.requests].count(DEFAULT_MANIFEST_URL) == 3
    assert [str(request.url) for request in feed.requests].count(COMPANIES_URL) == 2


async def test_unchanged_304_run_prunes_orphans_and_crash_temps(tmp_path: Path) -> None:
    rows = [_row()]
    company_csv = _csv_bytes(rows)
    feed = _Feed(_manifest_bytes(rows, company_csv), company_csv)
    await _sync(tmp_path, feed)
    orphan_object = tmp_path / "objects" / ("f" * 64)
    root_temp = tmp_path / ".current.json.crash"
    snapshot_temp = tmp_path / "snapshots" / ".snapshot.json.crash"
    for path in (orphan_object, root_temp, snapshot_temp):
        path.write_text("orphan")

    unchanged = await _sync(tmp_path, feed)
    assert unchanged.etag_revalidated is True
    assert not orphan_object.exists()
    assert not root_temp.exists()
    assert not snapshot_temp.exists()


async def test_older_manifest_cannot_replace_newer_snapshot(tmp_path: Path) -> None:
    rows = [_row()]
    company_csv = _csv_bytes(rows)
    await _sync(
        tmp_path,
        _Feed(_manifest_bytes(rows, company_csv, generation=2), company_csv, etag='"v2"'),
    )
    pointer_before = (tmp_path / "current.json").read_bytes()

    older_rows = [_row(url="https://boards.greenhouse.io/older")]
    older_csv = _csv_bytes(older_rows)
    with pytest.raises(SourceValidationError, match="older manifest"):
        await _sync(
            tmp_path,
            _Feed(_manifest_bytes(older_rows, older_csv, generation=1), older_csv, etag='"v1"'),
        )
    assert (tmp_path / "current.json").read_bytes() == pointer_before


def test_exclusive_run_lock_rejects_overlapping_process(tmp_path: Path) -> None:
    lock_path = tmp_path / "runner.lock"
    with (
        exclusive_run_lock(lock_path),
        pytest.raises(InventoryRunBusyError),
        exclusive_run_lock(lock_path),
    ):
        pytest.fail("overlapping lock unexpectedly acquired")


async def test_changed_snapshot_reports_new_family_and_changed_url(tmp_path: Path) -> None:
    initial_rows = [_row()]
    initial_csv = _csv_bytes(initial_rows)
    await _sync(tmp_path, _Feed(_manifest_bytes(initial_rows, initial_csv), initial_csv))

    changed_rows = [
        _row(url="https://boards.greenhouse.io/example-new"),
        _row(
            "brand_new_ats",
            name="New Tenant",
            slug="new-tenant",
            url="https://jobs.example.net/new-tenant",
        ),
    ]
    changed_csv = _csv_bytes(changed_rows)
    snapshot = await _sync(
        tmp_path,
        _Feed(_manifest_bytes(changed_rows, changed_csv, generation=2), changed_csv, etag='"v2"'),
    )

    assert snapshot.new_families == ("brand_new_ats",)
    assert snapshot.changed_urls == 1
    assert snapshot.coverage.unsupported_families == ("brand_new_ats",)
    assert snapshot.coverage.unsupported_rows == 1
    assert snapshot.coverage.candidate_generation_allowed is False


@pytest.mark.parametrize("failure", ["malformed", "checksum"])
async def test_malformed_or_checksum_bad_update_preserves_last_known_good(
    tmp_path: Path, failure: str
) -> None:
    rows = [_row()]
    initial_csv = _csv_bytes(rows)
    await _sync(tmp_path, _Feed(_manifest_bytes(rows, initial_csv), initial_csv))
    pointer_before = (tmp_path / "current.json").read_bytes()

    next_rows = [_row(url="https://boards.greenhouse.io/example-v2")]
    expected_csv = _csv_bytes(next_rows)
    if failure == "malformed":
        next_csv = expected_csv.replace(b"ats,name,slug,url\n", b"ats,name,slug,wrong_url\n", 1)
        manifest_csv = next_csv
    else:
        next_csv = expected_csv + b"corrupt"
        manifest_csv = expected_csv
    with pytest.raises(SourceValidationError):
        await _sync(
            tmp_path,
            _Feed(
                _manifest_bytes(next_rows, manifest_csv, generation=2),
                next_csv,
                etag='"v2"',
            ),
        )

    assert (tmp_path / "current.json").read_bytes() == pointer_before
    assert len(list((tmp_path / "objects").iterdir())) == 2


async def test_unexpected_family_shrink_preserves_last_known_good(tmp_path: Path) -> None:
    stable = [
        _row(
            name=f"Stable {index}",
            slug=f"stable-{index}",
            url=f"https://boards.greenhouse.io/stable-{index}",
        )
        for index in range(100)
    ]
    shrinking = [
        _row(
            "lever",
            name=f"Shrinking {index}",
            slug=f"shrinking-{index}",
            url=f"https://jobs.lever.co/shrinking-{index}",
        )
        for index in range(20)
    ]
    initial_rows = stable + shrinking
    initial_csv = _csv_bytes(initial_rows)
    await _sync(tmp_path, _Feed(_manifest_bytes(initial_rows, initial_csv), initial_csv))
    pointer_before = (tmp_path / "current.json").read_bytes()

    next_rows = stable + shrinking[:10]
    next_csv = _csv_bytes(next_rows)
    with pytest.raises(SourceValidationError, match="family 'lever' shrank"):
        await _sync(
            tmp_path,
            _Feed(_manifest_bytes(next_rows, next_csv, generation=2), next_csv, etag='"v2"'),
        )
    assert (tmp_path / "current.json").read_bytes() == pointer_before


async def test_small_removed_family_is_reported_instead_of_permanently_blocking(
    tmp_path: Path,
) -> None:
    stable = [
        _row(
            name=f"Stable {index}",
            slug=f"stable-{index}",
            url=f"https://boards.greenhouse.io/stable-{index}",
        )
        for index in range(20)
    ]
    initial_rows = stable + [
        _row(
            "bytedance",
            name="ByteDance",
            slug="bytedance",
            url="https://joinbytedance.com",
        )
    ]
    initial_csv = _csv_bytes(initial_rows)
    await _sync(tmp_path, _Feed(_manifest_bytes(initial_rows, initial_csv), initial_csv))

    next_csv = _csv_bytes(stable)
    snapshot = await _sync(
        tmp_path,
        _Feed(_manifest_bytes(stable, next_csv, generation=2), next_csv, etag='"v2"'),
    )
    assert snapshot.removed_families == ("bytedance",)


def test_compatibility_registry_is_exhaustive_and_uses_real_monitors() -> None:
    expected_families = {
        "adp",
        "ashby",
        "avature",
        "bamboohr",
        "beisen",
        "beisen_legacy",
        "breezy",
        "bytedance",
        "cornerstone",
        "darwinbox",
        "dayforce",
        "eightfold",
        "gem",
        "greenhouse",
        "gupy",
        "herp",
        "hrmos",
        "icims",
        "infojobs_es",
        "jazzhr",
        "jobbankca",
        "jobs_cz",
        "jobvite",
        "join_com",
        "keka",
        "lever",
        "mercor",
        "moka",
        "oracle",
        "pageup",
        "paycom",
        "paylocity",
        "personio",
        "phenom",
        "pinpoint",
        "recruitee",
        "recruiterbox",
        "rippling",
        "seek",
        "smartrecruiters",
        "softgarden",
        "successfactors",
        "taleo",
        "teamtailor",
        "ukg",
        "workable",
        "workday",
    }
    assert set(COMPATIBILITY) == expected_families
    assert {
        compat.monitor_type for compat in COMPATIBILITY.values() if compat.monitor_type
    } <= all_monitor_types()
    assert COMPATIBILITY["join_com"].monitor_type == "join"
    assert COMPATIBILITY["oracle"].monitor_type == "oracle_hcm"
    assert COMPATIBILITY["beisen_legacy"].monitor_type == "beisen"
    assert COMPATIBILITY["successfactors"].monitor_config == {"preset": "successfactors"}


class _FakeSupportClient:
    def __init__(self, existing: list[ExistingIssue] | None = None) -> None:
        self.existing = list(existing or [])
        self.created = 0
        self.fail_after_create = False

    async def list_support_issues(self) -> list[ExistingIssue]:
        return list(self.existing)

    async def create_support_issue(
        self, *, title: str, body: str, labels: list[str]
    ) -> CreatedIssue:
        self.created += 1
        issue = ExistingIssue(
            number=7000 + self.created,
            state="open",
            title=title,
            body=body,
            url=f"https://github.com/example/repo/issues/{7000 + self.created}",
        )
        self.existing.append(issue)
        if self.fail_after_create:
            raise GitHubCreateOutcomeUnknown("response lost")
        return CreatedIssue(number=issue.number, url=issue.url)


async def _unknown_snapshot(tmp_path: Path):
    rows = [
        _row(
            "future_ats",
            name=f"Tenant {index}",
            slug=f"tenant-{index}",
            url=f"https://careers.example{index}.com/jobs",
        )
        for index in range(3)
    ]
    company_csv = _csv_bytes(rows)
    return await _sync(tmp_path, _Feed(_manifest_bytes(rows, company_csv), company_csv))


async def test_unknown_family_plans_one_issue_not_one_per_company(tmp_path: Path) -> None:
    snapshot = await _unknown_snapshot(tmp_path)
    client = _FakeSupportClient()

    actions = await reconcile_support_issues(snapshot, client, create=False)
    assert [(action.family, action.action, action.tenant_rows) for action in actions] == [
        ("future_ats", "would_create", 3)
    ]
    title, body = render_support_issue(snapshot, "future_ats")
    assert title.endswith("future_ats")
    assert "<!-- ats-inventory-support:family=future_ats -->" in body
    assert "Company/tenant rows: 3" in body


@pytest.mark.parametrize(
    "state, expected", [("open", "open_existing"), ("closed", "closed_existing")]
)
async def test_existing_support_issue_is_never_duplicated(
    tmp_path: Path, state: str, expected: str
) -> None:
    snapshot = await _unknown_snapshot(tmp_path)
    existing = ExistingIssue(
        number=42,
        state=state,
        title="Existing",
        body="<!-- ats-inventory-support:family=future_ats -->",
        url="https://github.com/example/repo/issues/42",
    )
    client = _FakeSupportClient([existing])

    actions = await reconcile_support_issues(snapshot, client, create=True)
    assert actions[0].action == expected
    assert actions[0].issue_number == 42
    assert client.created == 0


async def test_unknown_create_outcome_reconciles_marker_before_retry(tmp_path: Path) -> None:
    snapshot = await _unknown_snapshot(tmp_path)
    client = _FakeSupportClient()
    client.fail_after_create = True

    actions = await reconcile_support_issues(snapshot, client, create=True)
    assert actions[0].action == "created_reconciled"
    assert actions[0].issue_number == 7001
    assert client.created == 1
