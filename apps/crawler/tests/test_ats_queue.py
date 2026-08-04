from __future__ import annotations

import csv
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from src.ats_inventory.candidate_issues import CandidateIssueCoordinator
from src.ats_inventory.candidates import LocalRegistryIndex
from src.ats_inventory.github import (
    CreatedIssue,
    GitHubClaim,
    GitHubError,
    GitHubRateLimitError,
    GitHubSupportIssueClient,
    GitHubWorkItem,
)
from src.ats_inventory.ledger import CandidateLedger
from src.ats_inventory.models import CompanyImpact
from src.ats_inventory.queue import IMPORT_LABEL, QueuePolicy, QueueRefiller, classify_queue

NOW = datetime(2026, 8, 4, 12, 0, tzinfo=UTC)


def _item(
    number: int,
    *,
    kind: str = "issue",
    state: str = "open",
    body: str = "",
    imported: bool = False,
) -> GitHubWorkItem:
    return GitHubWorkItem(
        kind=kind,  # type: ignore[arg-type]
        number=number,
        state=state,
        title=f"Item {number}",
        body=body,
        url=f"https://github.test/{kind}/{number}",
        labels=("company-request", IMPORT_LABEL) if imported else ("company-request",),
    )


def _claim(number: int, created_at: datetime | str) -> GitHubClaim:
    timestamp = (
        created_at.isoformat().replace("+00:00", "Z")
        if isinstance(created_at, datetime)
        else created_at
    )
    return GitHubClaim(
        issue_number=number,
        body="<!-- ws-claim -->\nWorking on it",
        created_at=timestamp,
    )


def _impact(number: int, active_jobs: int) -> CompanyImpact:
    return CompanyImpact(
        ats="greenhouse",
        name=f"Company {number}",
        slug=f"company-{number}",
        url=f"https://job-boards.greenhouse.io/company{number}",
        impact_unknown=False,
        active_jobs=active_jobs,
        remote_jobs=0,
        location_count=1,
        country_codes=("US",),
        latest_posted_at=None,
    )


async def _open_count(items: list[GitHubWorkItem]) -> int:
    return sum(item.kind == "issue" and item.state == "open" for item in items)


def _local_registry(tmp_path: Path) -> LocalRegistryIndex:
    companies_path = tmp_path / "companies.csv"
    boards_path = tmp_path / "boards.csv"
    with companies_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["slug", "name", "website"])
        writer.writeheader()
    with boards_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "company_slug",
                "board_slug",
                "board_url",
                "monitor_type",
                "monitor_config",
                "scraper_type",
                "scraper_config",
            ],
        )
        writer.writeheader()
    return LocalRegistryIndex.from_csv(companies_path, boards_path)


class _Client:
    def __init__(self, items: list[GitHubWorkItem] | None = None) -> None:
        self.items = list(items or [])
        self.created_labels: list[list[str]] = []
        self.rate_remaining = 4_900
        self.rate_reset = 1_800_000_000

    async def list_candidate_work_items(self) -> list[GitHubWorkItem]:
        return list(self.items)

    async def create_candidate_issue(
        self, *, title: str, body: str, labels: list[str]
    ) -> CreatedIssue:
        number = 10_000 + len(self.created_labels)
        self.created_labels.append(labels)
        self.items.append(
            GitHubWorkItem(
                kind="issue",
                number=number,
                state="open",
                title=title,
                body=body,
                url=f"https://github.test/issues/{number}",
                labels=tuple(labels),
            )
        )
        return CreatedIssue(number=number, url=f"https://github.test/issues/{number}")


async def _refiller(
    tmp_path: Path,
    *,
    items: list[GitHubWorkItem] | None = None,
    claims: list[GitHubClaim] | None = None,
    policy: QueuePolicy | None = None,
    sleeps: list[float] | None = None,
) -> tuple[QueueRefiller, CandidateLedger, _Client]:
    client = _Client(items)
    ledger = CandidateLedger(tmp_path / "ledger.sqlite")
    coordinator = await CandidateIssueCoordinator.bootstrap(
        client=client,
        local=_local_registry(tmp_path),
        ledger=ledger,
        items=client.items,
    )

    async def sleep(delay: float) -> None:
        if sleeps is not None:
            sleeps.append(delay)

    return (
        QueueRefiller(
            coordinator=coordinator,
            ledger=ledger,
            items=client.items,
            claims=claims or [],
            policy=policy or QueuePolicy(),
            now=lambda: NOW,
            sleep=sleep,
            jitter=lambda low, high: (low + high) / 2,
            refresh_open_count=lambda: _open_count(client.items),
        ),
        ledger,
        client,
    )


def test_queue_counts_humans_and_imports_but_excludes_fresh_claims_and_linked_prs() -> None:
    items = [
        _item(1),
        _item(2, imported=True),
        _item(3, imported=True),
        _item(4),
        _item(5),
        _item(90, kind="pr", body="Closes #4\nFixes #5"),
    ]
    claims = [
        _claim(2, NOW - timedelta(minutes=10)),
        _claim(3, NOW - timedelta(hours=5)),
        _claim(5, NOW - timedelta(minutes=20)),
    ]
    snapshot = classify_queue(items, claims, now=NOW, claim_ttl_seconds=4 * 60 * 60)
    assert snapshot.total_open == 5
    assert snapshot.available == 2
    assert snapshot.fresh_claimed == 2
    assert snapshot.active_linked_pr == 2
    assert snapshot.import_open == 2
    assert snapshot.human_open == 3
    assert snapshot.fresh_claim_issue_numbers == (2, 5)
    assert snapshot.linked_pr_issue_numbers == (4, 5)


def test_invalid_claim_timestamp_fails_closed_and_is_reported() -> None:
    snapshot = classify_queue(
        [_item(1)],
        [_claim(1, "not-a-timestamp")],
        now=NOW,
        claim_ttl_seconds=60,
    )
    assert snapshot.available == 0
    assert snapshot.unparseable_claims == 1


@pytest.mark.asyncio
async def test_refill_selects_highest_impact_and_obeys_rollout_with_jitter(
    tmp_path: Path,
) -> None:
    sleeps: list[float] = []
    refiller, ledger, client = await _refiller(
        tmp_path,
        policy=QueuePolicy(rollout_cap=5),
        sleeps=sleeps,
    )
    companies = [
        _impact(number, jobs)
        for number, jobs in [(1, 1), (2, 50), (3, 5), (4, 20), (5, 2), (6, 10)]
    ]
    report = await refiller.run(companies, mode="refill")

    assert report.status == "refilled"
    assert report.requested_creates == 5
    assert report.created == 5
    assert [action.plan.candidate.name for action in report.actions] == [
        "Company 2",
        "Company 4",
        "Company 6",
        "Company 3",
        "Company 5",
    ]
    assert all(labels == ["company-request", IMPORT_LABEL] for labels in client.created_labels)
    assert sleeps == [2.0, 2.0, 2.0, 2.0]
    assert ledger.count_created_since(int(NOW.replace(hour=0).timestamp())) == 5
    assert report.available_after == 5
    assert report.total_open_after == 5


@pytest.mark.asyncio
async def test_per_tick_cap_bounds_dry_run_selection(tmp_path: Path) -> None:
    refiller, _, client = await _refiller(
        tmp_path,
        policy=QueuePolicy(per_tick_cap=3, rollout_cap=5),
    )
    report = await refiller.run(
        [_impact(1, 100), _impact(1, 100), _impact(2, 90), _impact(3, 80)],
        mode="dry-run",
    )
    assert report.status == "dry_run"
    assert report.requested_creates == 3
    assert report.selected_candidates == 3
    assert report.inspected_candidates == 4
    assert report.hard_skip_counts == {"selected_source_key": 1}
    assert report.created == 0
    assert not client.created_labels


@pytest.mark.asyncio
async def test_dry_run_is_write_free_and_hard_cap_wins(tmp_path: Path) -> None:
    items = [_item(number) for number in range(1, 601)]
    refiller, _, client = await _refiller(
        tmp_path,
        items=items,
        claims=[_claim(number, NOW) for number in range(1, 201)],
        policy=QueuePolicy(rollout_cap=25),
    )
    report = await refiller.run([_impact(1, 100)], mode="dry-run")
    assert report.queue_before.available == 400
    assert report.status == "hard_cap"
    assert report.requested_creates == 0
    assert not client.created_labels


@pytest.mark.asyncio
async def test_live_hard_cap_recheck_reserves_a_slot_for_external_writer(
    tmp_path: Path,
) -> None:
    items = [_item(number) for number in range(1, 599)]
    refiller, _, client = await _refiller(
        tmp_path,
        items=items,
        claims=[_claim(number, NOW) for number in range(1, 201)],
        policy=QueuePolicy(rollout_cap=5),
    )

    async def external_writer_already_opened_one() -> int:
        return 599

    refiller.refresh_open_count = external_writer_already_opened_one
    report = await refiller.run([_impact(1, 100)], mode="refill")
    assert report.status == "hard_cap_live"
    assert report.requested_creates == 2
    assert report.created == 0
    assert report.total_open_after == 599
    assert not client.created_labels


@pytest.mark.asyncio
async def test_live_hard_cap_remembers_own_create_if_github_list_is_stale(
    tmp_path: Path,
) -> None:
    items = [_item(number) for number in range(1, 599)]
    refiller, _, client = await _refiller(
        tmp_path,
        items=items,
        claims=[_claim(number, NOW) for number in range(1, 201)],
        policy=QueuePolicy(rollout_cap=5),
    )

    async def stale_open_count() -> int:
        return 598

    refiller.refresh_open_count = stale_open_count
    report = await refiller.run([_impact(1, 100), _impact(2, 90)], mode="refill")
    assert report.status == "hard_cap_live"
    assert report.requested_creates == 2
    assert report.created == 1
    assert report.total_open_after == 599
    assert len(client.created_labels) == 1


@pytest.mark.asyncio
async def test_coverage_quarantine_suppresses_all_candidate_admission(tmp_path: Path) -> None:
    refiller, _, client = await _refiller(tmp_path, policy=QueuePolicy(rollout_cap=25))
    report = await refiller.run(
        [_impact(1, 100)],
        mode="refill",
        admission_block="coverage_quarantined",
    )
    assert report.status == "coverage_quarantined"
    assert report.requested_creates == 0
    assert report.inspected_candidates == 0
    assert not client.created_labels


@pytest.mark.asyncio
async def test_daily_cap_stops_later_refills(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("src.ats_inventory.ledger.time.time", lambda: NOW.timestamp())
    policy = QueuePolicy(daily_cap=1, rollout_cap=1)
    first, ledger, client = await _refiller(tmp_path, policy=policy)
    first_report = await first.run([_impact(1, 10)], mode="refill")
    assert first_report.created == 1

    coordinator = await CandidateIssueCoordinator.bootstrap(
        client=client,
        local=_local_registry(tmp_path),
        ledger=ledger,
        items=client.items,
    )
    second = QueueRefiller(
        coordinator=coordinator,
        ledger=ledger,
        items=client.items,
        claims=[],
        policy=policy,
        now=lambda: NOW,
    )
    second_report = await second.run([_impact(2, 20)], mode="refill")
    assert second_report.status == "daily_cap"
    assert second_report.created == 0
    assert len(client.created_labels) == 1


@pytest.mark.asyncio
async def test_refill_stops_cleanly_on_rate_limit(tmp_path: Path) -> None:
    class LimitedClient(_Client):
        async def create_candidate_issue(
            self, *, title: str, body: str, labels: list[str]
        ) -> CreatedIssue:
            raise GitHubRateLimitError(
                "secondary limit",
                retry_after=17,
                reset_at=1_800_000_000,
            )

    client = LimitedClient()
    ledger = CandidateLedger(tmp_path / "ledger.sqlite")
    coordinator = await CandidateIssueCoordinator.bootstrap(
        client=client,
        local=_local_registry(tmp_path),
        ledger=ledger,
        items=[],
    )
    report = await QueueRefiller(
        coordinator=coordinator,
        ledger=ledger,
        items=[],
        claims=[],
        policy=QueuePolicy(),
        now=lambda: NOW,
        refresh_open_count=lambda: _open_count(client.items),
    ).run([_impact(1, 10)], mode="refill")

    assert report.status == "rate_limited"
    assert report.created == 0
    assert report.retry_after == 17
    assert report.retry_at == 1_800_000_000


@pytest.mark.parametrize("status", [403, 429])
@pytest.mark.asyncio
async def test_github_rate_limit_preserves_retry_and_reset(status: int) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            status,
            headers={
                "retry-after": "17",
                "x-ratelimit-remaining": "0",
                "x-ratelimit-reset": "1800000000",
            },
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        github = GitHubSupportIssueClient(
            http,
            repo="example/repo",
            token="token",
            pace_seconds=0,
        )
        with pytest.raises(GitHubRateLimitError) as caught:
            await github.create_candidate_issue(
                title="Add company: Acme",
                body="body",
                labels=["company-request", IMPORT_LABEL],
            )
    assert caught.value.retry_after == 17
    assert caught.value.reset_at == 1_800_000_000


@pytest.mark.asyncio
async def test_permission_403_is_not_misclassified_as_rate_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={"message": "Resource not accessible by integration"},
            headers={"x-ratelimit-remaining": "4999"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        github = GitHubSupportIssueClient(
            http,
            repo="example/repo",
            token="token",
            pace_seconds=0,
        )
        with pytest.raises(GitHubError) as caught:
            await github.create_candidate_issue(
                title="Add company: Acme",
                body="body",
                labels=["company-request", IMPORT_LABEL],
            )
    assert not isinstance(caught.value, GitHubRateLimitError)


@pytest.mark.asyncio
async def test_recent_claims_are_loaded_in_bulk() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json=[
                {
                    "body": "<!-- ws-claim -->\nrun: issue-1",
                    "issue_url": "https://api.github.com/repos/example/repo/issues/12",
                    "created_at": "2026-08-04T11:00:00Z",
                },
                {
                    "body": "ordinary comment",
                    "issue_url": "https://api.github.com/repos/example/repo/issues/13",
                    "created_at": "2026-08-04T11:01:00Z",
                },
            ],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        github = GitHubSupportIssueClient(
            http,
            repo="example/repo",
            token="token",
            pace_seconds=0,
        )
        claims = await github.list_recent_claims(since=NOW - timedelta(hours=4))
    assert [claim.issue_number for claim in claims] == [12]
    assert len(requests) == 1
    assert requests[0].url.path.endswith("/issues/comments")
    assert requests[0].url.params["since"] == "2026-08-04T08:00:00Z"


@pytest.mark.asyncio
async def test_live_open_count_excludes_pull_requests_from_issues_endpoint() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[
                {"number": 1},
                {"number": 2},
                {"number": 3, "pull_request": {}},
            ],
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        github = GitHubSupportIssueClient(
            http,
            repo="example/repo",
            token="token",
            pace_seconds=0,
        )
        count = await github.count_open_company_requests()
    assert count == 2
