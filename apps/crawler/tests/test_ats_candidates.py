from __future__ import annotations

import csv
from pathlib import Path

import httpx
import pytest

from src.ats_inventory.candidate_issues import CandidateIssueCoordinator
from src.ats_inventory.candidates import (
    Candidate,
    CandidateDeduplicator,
    GitHubCandidateIndex,
    LocalRegistryIndex,
    candidate_marker,
    normalize_board_url,
    parse_candidate_markers,
    render_candidate_issue,
)
from src.ats_inventory.github import (
    CreatedIssue,
    GitHubCreateOutcomeUnknown,
    GitHubSupportIssueClient,
    GitHubWorkItem,
)
from src.ats_inventory.ledger import CandidateLedger
from src.ats_inventory.models import CompanyImpact


def _impact(**overrides: object) -> CompanyImpact:
    values: dict[str, object] = {
        "ats": "greenhouse",
        "name": "Acme",
        "slug": "acme",
        "url": "https://job-boards.greenhouse.io/acme",
        "impact_unknown": False,
        "active_jobs": 12,
        "remote_jobs": 3,
        "location_count": 4,
        "country_codes": ("CH", "US"),
        "latest_posted_at": "2026-08-01T12:00:00Z",
    }
    values.update(overrides)
    return CompanyImpact(**values)  # type: ignore[arg-type]


def _registries(
    tmp_path: Path,
    *,
    companies: list[dict[str, str]] | None = None,
    boards: list[dict[str, str]] | None = None,
) -> LocalRegistryIndex:
    companies_path = tmp_path / "companies.csv"
    boards_path = tmp_path / "boards.csv"
    company_fields = [
        "slug",
        "name",
        "website",
        "logo_url",
        "icon_url",
        "logo_type",
        "industry",
        "employee_count_range",
        "founded_year",
        "extras",
    ]
    board_fields = [
        "company_slug",
        "board_slug",
        "board_url",
        "monitor_type",
        "monitor_config",
        "scraper_type",
        "scraper_config",
    ]
    with companies_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=company_fields)
        writer.writeheader()
        for company in companies or []:
            writer.writerow(company)
    with boards_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=board_fields)
        writer.writeheader()
        for board in boards or []:
            writer.writerow(board)
    return LocalRegistryIndex.from_csv(companies_path, boards_path)


def _github_item(
    candidate: Candidate,
    *,
    number: int = 42,
    state: str = "open",
    kind: str = "issue",
    marked: bool = True,
    title: str | None = None,
) -> GitHubWorkItem:
    body = candidate_marker(candidate.source_key, candidate.board_url) if marked else "Request"
    return GitHubWorkItem(
        kind=kind,  # type: ignore[arg-type]
        number=number,
        state=state,
        title=title or f"Add company: {candidate.name}",
        body=body,
        url=f"https://github.test/items/{number}",
    )


def _deduplicator(
    tmp_path: Path,
    local: LocalRegistryIndex,
    *,
    github: GitHubCandidateIndex | None = None,
) -> CandidateDeduplicator:
    return CandidateDeduplicator(
        local,
        github or GitHubCandidateIndex(),
        CandidateLedger(tmp_path / "ledger.sqlite"),
    )


def test_url_normalization_is_exact_but_removes_transport_and_tracking_noise() -> None:
    first = normalize_board_url(
        "http://Jobs.Example.com:80/careers/?role=eng&utm_source=x&source=board#jobs"
    )
    second = normalize_board_url(
        "https://jobs.example.com/careers?source=board&role=eng&utm_campaign=y"
    )
    assert first == second
    assert first == "https://jobs.example.com/careers?role=eng&source=board"


def test_candidate_marker_round_trips_without_exposing_raw_url() -> None:
    candidate = Candidate.from_impact(_impact())
    marker = candidate_marker(candidate.source_key, candidate.board_url)
    assert candidate.board_url not in marker
    assert parse_candidate_markers(marker) == ((candidate.source_key, candidate.board_url_hash),)


@pytest.mark.parametrize(
    "family, first_url, second_url",
    [
        (
            "successfactors",
            "https://career5.successfactors.eu/career?company=alpha",
            "https://career5.successfactors.eu/career?company=beta",
        ),
        (
            "taleo",
            "https://example.taleo.net/a/ats/careers/v2/searchResults?cws=1&org=ACME",
            "https://example.taleo.net/a/ats/careers/v2/searchResults?cws=37&org=ACME",
        ),
        (
            "moka",
            "https://app.mokahr.com/campus-recruitment/alpha/100",
            "https://app.mokahr.com/campus-recruitment/beta/200",
        ),
        (
            "keka",
            "https://schools.keka.com/careers",
            "https://schools.keka.com/careers/europe",
        ),
    ],
)
def test_provider_board_scopes_remain_distinct(
    family: str, first_url: str, second_url: str
) -> None:
    first = Candidate.from_impact(_impact(ats=family, url=first_url))
    second = Candidate.from_impact(_impact(ats=family, url=second_url))
    assert first.source_key != second.source_key
    assert first.tenant != second.tenant


def test_exact_native_tenant_is_hard_skip_across_greenhouse_host_aliases(
    tmp_path: Path,
) -> None:
    local = _registries(
        tmp_path,
        companies=[{"slug": "acme", "name": "Acme", "website": "https://acme.com"}],
        boards=[
            {
                "company_slug": "acme",
                "board_slug": "acme-greenhouse",
                "board_url": "https://boards.greenhouse.io/acme",
                "monitor_type": "greenhouse",
                "monitor_config": '{"token":"acme"}',
            }
        ],
    )
    plan = _deduplicator(tmp_path, local).plan(Candidate.from_impact(_impact()))
    assert not plan.eligible
    assert {item.code for item in plan.hard_skips} == {"existing_ats_tenant"}


def test_subsidiary_and_regional_board_are_soft_matches_not_skips(tmp_path: Path) -> None:
    local = _registries(
        tmp_path,
        companies=[{"slug": "acme", "name": "Acme", "website": "https://acme.com"}],
        boards=[
            {
                "company_slug": "acme",
                "board_slug": "acme-us",
                "board_url": "https://acme.wd5.myworkdayjobs.com/AcmeUS",
                "monitor_type": "workday",
                "monitor_config": "{}",
            }
        ],
    )
    candidate = Candidate.from_impact(
        _impact(
            ats="workday",
            name="Acme Switzerland",
            slug="acme-switzerland",
            url="https://acme.wd5.myworkdayjobs.com/AcmeEurope",
        )
    )
    plan = _deduplicator(tmp_path, local).plan(candidate)
    assert plan.eligible
    assert "possible_parent_or_region" in {item.code for item in plan.soft_warnings}


def test_same_company_on_a_second_ats_is_not_suppressed(tmp_path: Path) -> None:
    local = _registries(
        tmp_path,
        companies=[{"slug": "acme", "name": "Acme", "website": "https://acme.com"}],
        boards=[
            {
                "company_slug": "acme",
                "board_slug": "acme-greenhouse",
                "board_url": "https://job-boards.greenhouse.io/acme",
                "monitor_type": "greenhouse",
                "monitor_config": '{"token":"acme"}',
            }
        ],
    )
    candidate = Candidate.from_impact(_impact(ats="lever", url="https://jobs.lever.co/acme-europe"))
    plan = _deduplicator(tmp_path, local).plan(candidate)
    assert plan.eligible
    assert "similar_company_identity" in {item.code for item in plan.soft_warnings}


def test_ambiguous_homepage_domain_is_advisory(tmp_path: Path) -> None:
    local = _registries(
        tmp_path,
        companies=[{"slug": "old-brand", "name": "Old Brand", "website": "https://www.acme.com"}],
    )
    candidate = Candidate.from_impact(
        _impact(
            ats="bytedance",
            name="New Brand",
            slug="new-brand",
            url="https://jobs.acme.com/careers",
        )
    )
    plan = _deduplicator(tmp_path, local).plan(candidate)
    assert plan.eligible
    assert {item.code for item in plan.soft_warnings} == {"shared_homepage_domain"}


def test_similar_closed_issue_title_without_marker_is_only_a_warning(tmp_path: Path) -> None:
    candidate = Candidate.from_impact(_impact())
    item = _github_item(candidate, state="closed", marked=False)
    plan = _deduplicator(
        tmp_path,
        _registries(tmp_path),
        github=GitHubCandidateIndex([item]),
    ).plan(candidate)
    assert plan.eligible
    assert {warning.code for warning in plan.soft_warnings} == {"similar_github_title"}


def test_closed_issue_or_active_pr_exact_marker_is_a_hard_skip(tmp_path: Path) -> None:
    candidate = Candidate.from_impact(_impact())
    items = [
        _github_item(candidate, number=10, state="closed"),
        _github_item(candidate, number=11, kind="pr"),
    ]
    plan = _deduplicator(
        tmp_path,
        _registries(tmp_path),
        github=GitHubCandidateIndex(items),
    ).plan(candidate)
    assert not plan.eligible
    assert {item.code for item in plan.hard_skips} == {
        "github_source_marker",
        "github_board_marker",
    }


def test_renamed_candidate_is_still_found_by_durable_source_key(tmp_path: Path) -> None:
    original = Candidate.from_impact(_impact(name="Old Name"))
    ledger = CandidateLedger(tmp_path / "ledger.sqlite")
    ledger.record_created(
        source_key=original.source_key,
        normalized_url=original.board_url,
        family=original.family,
        tenant=original.tenant,
        item=_github_item(original),
    )
    renamed = Candidate.from_impact(_impact(name="Entirely New Name", slug="new-name"))
    plan = CandidateDeduplicator(_registries(tmp_path), GitHubCandidateIndex(), ledger).plan(
        renamed
    )
    assert not plan.eligible
    assert {item.code for item in plan.hard_skips} == {
        "ledger_source_key",
        "ledger_board_url",
    }


def test_ledger_reconciles_remote_drift_but_remains_fail_closed(tmp_path: Path) -> None:
    candidate = Candidate.from_impact(_impact())
    ledger = CandidateLedger(tmp_path / "ledger.sqlite")
    recovered = ledger.reconcile_remote([_github_item(candidate, state="closed")])
    assert recovered.recovered_sources == 1
    assert ledger.find_source(candidate.source_key).state == "remote_issue_closed"  # type: ignore[union-attr]
    assert ledger.find_url(candidate.board_url)

    missing = ledger.reconcile_remote([])
    assert missing.missing_remote_sources == (candidate.source_key,)
    assert ledger.find_source(candidate.source_key).state == "remote_missing"  # type: ignore[union-attr]


class _UnknownOutcomeClient:
    def __init__(self, candidate: Candidate) -> None:
        self.candidate = candidate
        self.items: list[GitHubWorkItem] = []
        self.list_calls = 0
        self.create_calls = 0

    async def list_candidate_work_items(self) -> list[GitHubWorkItem]:
        self.list_calls += 1
        return list(self.items)

    async def create_candidate_issue(
        self, *, title: str, body: str, labels: list[str]
    ) -> CreatedIssue:
        self.create_calls += 1
        assert labels == ["company-request"]
        self.items.append(
            GitHubWorkItem(
                kind="issue",
                number=44,
                state="open",
                title=title,
                body=body,
                url="https://github.test/issues/44",
            )
        )
        raise GitHubCreateOutcomeUnknown("timeout after commit")


@pytest.mark.asyncio
async def test_unknown_create_outcome_is_reconciled_and_retry_is_idempotent(
    tmp_path: Path,
) -> None:
    candidate = Candidate.from_impact(_impact())
    client = _UnknownOutcomeClient(candidate)
    coordinator = await CandidateIssueCoordinator.bootstrap(
        client=client,
        local=_registries(tmp_path),
        ledger=CandidateLedger(tmp_path / "ledger.sqlite"),
    )
    first = await coordinator.create(candidate, dry_run=False)
    second = await coordinator.create(candidate, dry_run=False)
    assert first.action == "created_reconciled"
    assert first.issue_number == 44
    assert second.action == "hard_skip"
    assert client.create_calls == 1
    assert client.list_calls == 2


@pytest.mark.asyncio
async def test_dry_run_explains_hard_skips_and_soft_warnings(tmp_path: Path) -> None:
    candidate = Candidate.from_impact(_impact())
    existing = _github_item(candidate, marked=False)

    class Client:
        async def list_candidate_work_items(self) -> list[GitHubWorkItem]:
            return [existing]

        async def create_candidate_issue(
            self, *, title: str, body: str, labels: list[str]
        ) -> CreatedIssue:
            raise AssertionError("dry run must not write")

    coordinator = await CandidateIssueCoordinator.bootstrap(
        client=Client(),
        local=_registries(
            tmp_path,
            companies=[{"slug": "acme", "name": "Acme", "website": "https://acme.com"}],
            boards=[
                {
                    "company_slug": "acme",
                    "board_slug": "acme",
                    "board_url": candidate.board_url,
                    "monitor_type": "greenhouse",
                    "monitor_config": '{"token":"acme"}',
                }
            ],
        ),
        ledger=CandidateLedger(tmp_path / "ledger.sqlite"),
    )
    action = await coordinator.create(candidate, dry_run=True)
    report = action.to_dict()
    assert action.action == "hard_skip"
    assert {item["code"] for item in report["plan"]["hard_skips"]} == {
        "existing_board_url",
        "existing_ats_tenant",
    }
    assert {item["code"] for item in report["plan"]["soft_warnings"]} == {
        "similar_company_identity",
        "similar_github_title",
    }


def test_rendered_issue_preserves_all_soft_evidence_for_ws(tmp_path: Path) -> None:
    candidate = Candidate.from_impact(_impact())
    plan = _deduplicator(
        tmp_path,
        _registries(
            tmp_path,
            companies=[{"slug": "acme", "name": "Acme", "website": "https://acme.com"}],
        ),
    ).plan(candidate)
    title, body = render_candidate_issue(plan)
    assert title == "Add company: Acme"
    assert candidate_marker(candidate.source_key, candidate.board_url) in body
    assert "similar_company_identity" in body
    assert "do not import, execute, or add a runtime dependency" in body
    assert "simplified `ws` path" in body


@pytest.mark.asyncio
async def test_github_state_is_bulk_indexed_without_per_candidate_searches() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.url.path.endswith("/issues"):
            payload = [
                {
                    "number": 1,
                    "state": "closed",
                    "title": "Add company: Acme",
                    "body": "legacy request",
                    "html_url": "https://github.test/issues/1",
                },
                {
                    "number": 2,
                    "state": "open",
                    "title": "PR surfaced by issues endpoint",
                    "body": "",
                    "html_url": "https://github.test/pull/2",
                    "pull_request": {},
                },
            ]
        else:
            payload = [
                {
                    "number": 3,
                    "state": "open",
                    "title": "Add Globex",
                    "body": "active PR",
                    "html_url": "https://github.test/pull/3",
                }
            ]
        return httpx.Response(
            200,
            json=payload,
            headers={"x-ratelimit-remaining": "4998"},
            request=request,
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        github = GitHubSupportIssueClient(
            http,
            repo="example/repo",
            token="test-token",
            pace_seconds=0,
        )
        items = await github.list_candidate_work_items()

    assert [(item.kind, item.number) for item in items] == [("issue", 1), ("pr", 3)]
    assert len(requests) == 2
    assert requests[0].url.params["labels"] == "company-request"
    assert requests[0].url.params["state"] == "all"
    assert requests[1].url.params["state"] == "open"
