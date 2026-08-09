"""Bulk-indexed, crash-safe GitHub candidate issue coordination."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from src.ats_inventory.candidates import (
    Candidate,
    CandidateDeduplicator,
    CandidatePlan,
    GitHubCandidateIndex,
    LocalRegistryIndex,
    render_candidate_issue,
)
from src.ats_inventory.github import (
    ATS_INVENTORY_LABEL,
    CreatedIssue,
    GitHubCreateOutcomeUnknown,
    GitHubWorkItem,
)
from src.ats_inventory.ledger import CandidateLedger, LedgerReconciliation


class CandidateIssueClient(Protocol):
    async def list_candidate_work_items(self) -> list[GitHubWorkItem]: ...

    async def create_candidate_issue(
        self, *, title: str, body: str, labels: list[str]
    ) -> CreatedIssue: ...


@dataclass(frozen=True, slots=True)
class CandidateIssueAction:
    source_key: str
    action: str
    plan: CandidatePlan
    issue_number: int | None = None
    issue_url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_key": self.source_key,
            "action": self.action,
            "plan": self.plan.to_dict(),
            "issue_number": self.issue_number,
            "issue_url": self.issue_url,
        }


class CandidateIssueCoordinator:
    """Uses one GitHub snapshot for all normal plans and creates in a run."""

    def __init__(
        self,
        *,
        client: CandidateIssueClient,
        local: LocalRegistryIndex,
        ledger: CandidateLedger,
        github: GitHubCandidateIndex,
        reconciliation: LedgerReconciliation,
    ) -> None:
        self.client = client
        self.local = local
        self.ledger = ledger
        self.github = github
        self.reconciliation = reconciliation

    @classmethod
    async def bootstrap(
        cls,
        *,
        client: CandidateIssueClient,
        local: LocalRegistryIndex,
        ledger: CandidateLedger,
        items: list[GitHubWorkItem] | None = None,
    ) -> CandidateIssueCoordinator:
        if items is None:
            items = await client.list_candidate_work_items()
        reconciliation = ledger.reconcile_remote(items)
        return cls(
            client=client,
            local=local,
            ledger=ledger,
            github=GitHubCandidateIndex(items),
            reconciliation=reconciliation,
        )

    def plan(self, candidate: Candidate) -> CandidatePlan:
        return CandidateDeduplicator(self.local, self.github, self.ledger).plan(candidate)

    async def create(self, candidate: Candidate, *, dry_run: bool) -> CandidateIssueAction:
        plan = self.plan(candidate)
        if not plan.eligible:
            return CandidateIssueAction(candidate.source_key, "hard_skip", plan)
        if dry_run:
            return CandidateIssueAction(candidate.source_key, "would_create", plan)

        title, body = render_candidate_issue(plan)
        try:
            created = await self.client.create_candidate_issue(
                title=title,
                body=body,
                labels=["company-request", ATS_INVENTORY_LABEL],
            )
        except GitHubCreateOutcomeUnknown:
            recovered = await self.client.list_candidate_work_items()
            self.reconciliation = self.ledger.reconcile_remote(recovered)
            self.github = GitHubCandidateIndex(recovered)
            matches = tuple(
                item
                for item in self.github.source_hashes.get(candidate.source_hash, ())
                if item.kind == "issue"
            )
            if not matches:
                raise
            item = min(matches, key=lambda value: value.number)
            self.ledger.record_created(
                source_key=candidate.source_key,
                normalized_url=candidate.board_url,
                family=candidate.family,
                tenant=candidate.tenant,
                item=item,
            )
            return CandidateIssueAction(
                candidate.source_key,
                "created_reconciled",
                plan,
                item.number if item.kind == "issue" else None,
                item.url,
            )

        item = GitHubWorkItem(
            kind="issue",
            number=created.number,
            state="open",
            title=title,
            body=body,
            url=created.url,
        )
        # SQLite commits before the next candidate can be considered. A crash
        # just before this point is repaired from the GitHub marker at startup.
        self.ledger.record_created(
            source_key=candidate.source_key,
            normalized_url=candidate.board_url,
            family=candidate.family,
            tenant=candidate.tenant,
            item=item,
        )
        self.github.add(item)
        return CandidateIssueAction(
            candidate.source_key,
            "created",
            plan,
            created.number,
            created.url,
        )
