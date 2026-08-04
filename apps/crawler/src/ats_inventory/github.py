"""Idempotent one-per-family GitHub support issues for unknown ATS sources."""

from __future__ import annotations

import asyncio
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from typing import Any, Literal, Protocol

import httpx

from src.ats_inventory.constants import ATS_INVENTORY_LABEL as ATS_INVENTORY_LABEL
from src.ats_inventory.models import InventorySnapshot

_MARKER_RE = re.compile(r"<!-- ats-inventory-support:family=([a-z0-9][a-z0-9_]{0,63}) -->")
_API_VERSION = "2022-11-28"
_RATE_LIMIT_RESERVE = 100


class GitHubError(RuntimeError):
    pass


class GitHubRateLimitError(GitHubError):
    def __init__(
        self,
        message: str,
        *,
        retry_after: int | None = None,
        reset_at: int | None = None,
    ) -> None:
        super().__init__(message)
        self.retry_after = retry_after
        self.reset_at = reset_at

    def retry_at(self, *, now: int) -> int | None:
        candidates = [value for value in (self.reset_at,) if value is not None]
        if self.retry_after is not None:
            candidates.append(now + max(0, self.retry_after))
        return max(candidates) if candidates else None


class GitHubCreateOutcomeUnknown(GitHubError):
    """The create request may have committed before its transport failed."""


@dataclass(frozen=True, slots=True)
class ExistingIssue:
    number: int
    state: str
    title: str
    body: str
    url: str


@dataclass(frozen=True, slots=True)
class CreatedIssue:
    number: int
    url: str


@dataclass(frozen=True, slots=True)
class GitHubWorkItem:
    kind: Literal["issue", "pr"]
    number: int
    state: str
    title: str
    body: str
    url: str
    labels: tuple[str, ...] = ()
    created_at: str | None = None

    @property
    def reference(self) -> str:
        return f"{self.kind}:{self.number} [{self.state}] {self.title} ({self.url})"


@dataclass(frozen=True, slots=True)
class GitHubClaim:
    issue_number: int
    body: str
    created_at: str


class SupportIssueClient(Protocol):
    async def list_support_issues(self) -> list[ExistingIssue]: ...

    async def create_support_issue(
        self, *, title: str, body: str, labels: list[str]
    ) -> CreatedIssue: ...


@dataclass(frozen=True, slots=True)
class SupportIssueAction:
    family: str
    action: str
    tenant_rows: int
    job_rows: int | None
    issue_number: int | None = None
    issue_url: str | None = None
    duplicate_issue_numbers: tuple[int, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class GitHubSupportIssueClient:
    """Minimal REST client; credentials are injected and never persisted."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        repo: str,
        token: str,
        rate_limit_reserve: int = _RATE_LIMIT_RESERVE,
        pace_seconds: float = 0.25,
    ) -> None:
        owner, separator, name = repo.partition("/")
        if not separator or not owner or not name or "/" in name:
            raise ValueError("repo must be in owner/name form")
        if not token:
            raise ValueError("GitHub token is required")
        self.client = client
        self.repo = f"{owner}/{name}"
        self.headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        self.rate_limit_reserve = rate_limit_reserve
        self.pace_seconds = max(0.0, pace_seconds)
        self.rate_remaining: int | None = None
        self.rate_reset: int | None = None

    async def list_support_issues(self) -> list[ExistingIssue]:
        issues: list[ExistingIssue] = []
        for page in range(1, 101):
            if self.rate_remaining is not None and self.rate_remaining <= self.rate_limit_reserve:
                raise GitHubRateLimitError(
                    f"GitHub primary rate limit is below reserve ({self.rate_remaining})",
                    reset_at=self.rate_reset,
                )
            response = await self.client.get(
                f"https://api.github.com/repos/{self.repo}/issues",
                headers=self.headers,
                params={
                    "state": "all",
                    "per_page": 100,
                    "page": page,
                },
            )
            self._check_response(response)
            await asyncio.sleep(self.pace_seconds)
            payload = response.json()
            if not isinstance(payload, list):
                raise GitHubError("GitHub issues response is not a list")
            for raw in payload:
                if not isinstance(raw, dict) or "pull_request" in raw:
                    continue
                body = raw.get("body")
                if not isinstance(body, str) or _MARKER_RE.search(body) is None:
                    continue
                try:
                    issues.append(
                        ExistingIssue(
                            number=int(raw["number"]),
                            state=str(raw["state"]),
                            title=str(raw["title"]),
                            body=body,
                            url=str(raw["html_url"]),
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise GitHubError("GitHub issue has an invalid shape") from exc
            if len(payload) < 100:
                return issues
        raise GitHubError("GitHub issue pagination exceeded 100 pages")

    async def list_candidate_work_items(self) -> list[GitHubWorkItem]:
        """Bulk-index all company requests plus currently active PRs."""

        issue_payloads = await self._list_pages(
            "issues", params={"state": "all", "labels": "company-request"}
        )
        pr_payloads = await self._list_pages("pulls", params={"state": "open"})
        items: list[GitHubWorkItem] = []
        for kind, payloads in (("issue", issue_payloads), ("pr", pr_payloads)):
            for raw in payloads:
                if kind == "issue" and "pull_request" in raw:
                    continue
                body = raw.get("body")
                try:
                    items.append(
                        GitHubWorkItem(
                            kind=kind,  # type: ignore[arg-type]
                            number=int(raw["number"]),
                            state=str(raw["state"]),
                            title=str(raw["title"]),
                            body=body if isinstance(body, str) else "",
                            url=str(raw["html_url"]),
                            labels=_label_names(raw.get("labels")) if kind == "issue" else (),
                            created_at=(
                                raw["created_at"]
                                if isinstance(raw.get("created_at"), str)
                                else None
                            ),
                        )
                    )
                except (KeyError, TypeError, ValueError) as exc:
                    raise GitHubError(f"GitHub {kind} has an invalid shape") from exc
        return items

    async def list_recent_claims(self, *, since: datetime) -> list[GitHubClaim]:
        """Bulk-load recent repository comments containing the ws claim marker."""

        if since.tzinfo is None:
            raise ValueError("since must be timezone-aware")
        payloads = await self._list_pages(
            "issues/comments",
            params={
                "since": since.astimezone(UTC).isoformat().replace("+00:00", "Z"),
                "sort": "created",
                "direction": "asc",
            },
        )
        claims: list[GitHubClaim] = []
        for raw in payloads:
            body = raw.get("body")
            issue_url = raw.get("issue_url")
            created_at = raw.get("created_at")
            if not isinstance(body, str) or not body.startswith("<!-- ws-claim -->"):
                continue
            if not isinstance(issue_url, str) or not isinstance(created_at, str):
                raise GitHubError("GitHub issue comment has an invalid claim shape")
            try:
                issue_number = int(issue_url.rstrip("/").rsplit("/", 1)[1])
            except (IndexError, ValueError) as exc:
                raise GitHubError("GitHub issue comment has an invalid issue URL") from exc
            if issue_number <= 0:
                raise GitHubError("GitHub issue comment has an invalid issue number")
            claims.append(
                GitHubClaim(
                    issue_number=issue_number,
                    body=body,
                    created_at=created_at,
                )
            )
        return claims

    async def count_open_company_requests(self) -> int:
        """Return a live bulk count for the pre-create hard-cap gate."""

        payloads = await self._list_pages(
            "issues", params={"state": "open", "labels": "company-request"}
        )
        return sum("pull_request" not in raw for raw in payloads)

    async def _list_pages(self, endpoint: str, *, params: dict[str, str]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for page in range(1, 101):
            if self.rate_remaining is not None and self.rate_remaining <= self.rate_limit_reserve:
                raise GitHubRateLimitError(
                    f"GitHub primary rate limit is below reserve ({self.rate_remaining})",
                    reset_at=self.rate_reset,
                )
            response = await self.client.get(
                f"https://api.github.com/repos/{self.repo}/{endpoint}",
                headers=self.headers,
                params={**params, "per_page": "100", "page": str(page)},
            )
            self._check_response(response)
            await asyncio.sleep(self.pace_seconds)
            payload = response.json()
            if not isinstance(payload, list) or not all(isinstance(item, dict) for item in payload):
                raise GitHubError(f"GitHub {endpoint} response is not an object list")
            result.extend(payload)
            if len(payload) < 100:
                return result
        raise GitHubError(f"GitHub {endpoint} pagination exceeded 100 pages")

    async def create_support_issue(
        self, *, title: str, body: str, labels: list[str]
    ) -> CreatedIssue:
        if self.rate_remaining is not None and self.rate_remaining <= self.rate_limit_reserve:
            raise GitHubRateLimitError(
                f"GitHub primary rate limit is below reserve ({self.rate_remaining})",
                reset_at=self.rate_reset,
            )
        try:
            response = await self.client.post(
                f"https://api.github.com/repos/{self.repo}/issues",
                headers=self.headers,
                json={"title": title, "body": body, "labels": labels},
            )
        except httpx.TransportError as exc:
            raise GitHubCreateOutcomeUnknown("GitHub create outcome is unknown") from exc
        if response.status_code >= 500 or response.status_code in {408, 425}:
            self._update_rate(response)
            raise GitHubCreateOutcomeUnknown(
                f"GitHub create outcome is unknown after HTTP {response.status_code}"
            )
        self._check_response(response)
        try:
            payload = response.json()
        except ValueError as exc:
            raise GitHubCreateOutcomeUnknown(
                "GitHub create committed but returned invalid JSON"
            ) from exc
        if not isinstance(payload, dict):
            raise GitHubCreateOutcomeUnknown(
                "GitHub create committed but returned a non-object response"
            )
        number = payload.get("number")
        url = payload.get("html_url")
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
            or number <= 0
            or not isinstance(url, str)
            or not url.strip()
        ):
            raise GitHubCreateOutcomeUnknown(
                "GitHub create committed but returned an invalid issue shape"
            )
        created = CreatedIssue(number=number, url=url)
        await asyncio.sleep(max(1.0, self.pace_seconds))
        return created

    async def create_candidate_issue(
        self, *, title: str, body: str, labels: list[str]
    ) -> CreatedIssue:
        return await self.create_support_issue(title=title, body=body, labels=labels)

    def _check_response(self, response: httpx.Response) -> None:
        self._update_rate(response)
        if response.status_code == 429 or (
            response.status_code == 403 and _is_rate_limited_403(response)
        ):
            retry_after = _optional_int(response.headers.get("retry-after"))
            raise GitHubRateLimitError(
                f"GitHub rate limited request with HTTP {response.status_code}",
                retry_after=retry_after,
                reset_at=self.rate_reset,
            )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise GitHubError(f"GitHub request failed with HTTP {response.status_code}") from exc

    def _update_rate(self, response: httpx.Response) -> None:
        remaining = _optional_int(response.headers.get("x-ratelimit-remaining"))
        reset = _optional_int(response.headers.get("x-ratelimit-reset"))
        if remaining is not None:
            self.rate_remaining = remaining
        if reset is not None:
            self.rate_reset = reset


async def reconcile_support_issues(
    snapshot: InventorySnapshot,
    client: SupportIssueClient,
    *,
    create: bool,
) -> list[SupportIssueAction]:
    """Plan or create one issue for each unsupported inventory family.

    Both open and closed issues are reconciled by a stable body marker. A
    closed issue is surfaced, never replaced. If a POST's outcome is unknown,
    the marker is refetched before the error escapes.
    """

    if not snapshot.coverage.unsupported_families:
        return []
    existing = await client.list_support_issues()
    by_family = _issues_by_family(existing)
    actions: list[SupportIssueAction] = []
    for family in snapshot.coverage.unsupported_families:
        matching = sorted(by_family.get(family, ()), key=lambda issue: issue.number)
        tenant_rows = snapshot.family_counts[family]
        job_rows = _job_rows(snapshot.manifest, family)
        if matching:
            primary = matching[0]
            action = "open_existing" if primary.state == "open" else "closed_existing"
            if len(matching) > 1:
                action = "duplicate_existing"
            actions.append(
                SupportIssueAction(
                    family=family,
                    action=action,
                    tenant_rows=tenant_rows,
                    job_rows=job_rows,
                    issue_number=primary.number,
                    issue_url=primary.url,
                    duplicate_issue_numbers=tuple(issue.number for issue in matching[1:]),
                )
            )
            continue

        if not create:
            actions.append(
                SupportIssueAction(
                    family=family,
                    action="would_create",
                    tenant_rows=tenant_rows,
                    job_rows=job_rows,
                )
            )
            continue

        title, body = render_support_issue(snapshot, family)
        try:
            created = await client.create_support_issue(
                title=title,
                body=body,
                labels=["enhancement", "type:feature", "area:crawler"],
            )
        except GitHubCreateOutcomeUnknown:
            # A timeout may happen after GitHub committed the issue. Reconcile
            # before allowing a later invocation to consider another create.
            recovered = _issues_by_family(await client.list_support_issues()).get(family, [])
            if not recovered:
                raise
            issue = min(recovered, key=lambda candidate: candidate.number)
            actions.append(
                SupportIssueAction(
                    family=family,
                    action="created_reconciled",
                    tenant_rows=tenant_rows,
                    job_rows=job_rows,
                    issue_number=issue.number,
                    issue_url=issue.url,
                )
            )
        else:
            actions.append(
                SupportIssueAction(
                    family=family,
                    action="created",
                    tenant_rows=tenant_rows,
                    job_rows=job_rows,
                    issue_number=created.number,
                    issue_url=created.url,
                )
            )
    return actions


def render_support_issue(snapshot: InventorySnapshot, family: str) -> tuple[str, str]:
    if family not in snapshot.coverage.unsupported_families:
        raise ValueError(f"family {family!r} is not unsupported")
    rows = [row for row in snapshot.rows if row.ats == family]
    tenant_rows = len(rows)
    job_rows = _job_rows(snapshot.manifest, family)
    representatives = sorted(rows, key=lambda row: (row.name.casefold(), row.url))[:5]
    title = f"[ats inventory] Add native Jobseek support for {family}"
    body_lines = [
        f"<!-- ats-inventory-support:family={family} -->",
        "## Outcome",
        "",
        f"Add Jobseek-owned support for the newly observed `{family}` inventory family.",
        "The upstream project is an inventory/data source only: do not import or execute its",
        "scraper implementation and do not add it as a runtime dependency.",
        "",
        "## Current evidence",
        "",
        f"- Upstream family: `{family}`",
        f"- Company/tenant rows: {tenant_rows}",
        f"- Published active job rows: {job_rows if job_rows is not None else 'unknown'}",
        f"- Manifest: `{snapshot.manifest_sha256}`",
        "- Suggested reuse: start with the shared RSS, sitemap, DOM, nextdata, or API",
        "  monitor primitives and shared HTTP/retry/pagination helpers; add a dedicated thin",
        "  monitor only where the provider contract requires it.",
        "",
        "Representative inventory rows:",
        "",
    ]
    body_lines.extend(
        f"- {_markdown_code(row.name)} — {_markdown_code(row.url)}" for row in representatives
    )
    body_lines.extend(
        [
            "",
            "## Acceptance",
            "",
            "- Implement native Jobseek crawling with deterministic failure-mode tests.",
            "- Reuse base monitor/scraper machinery wherever practical.",
            "- Extend `ws` detection, selection, help, validation, scheduling, and tests.",
            "- Deliver this family in its own isolated-worktree PR from latest `origin/main`.",
            "- Obtain a fresh-context subagent review for codebase fit, simplification,",
            "  resilience, and performance; address findings and require green CI.",
            "- Keep companies in this family quarantined until the compatibility registry",
            "  points to the verified native monitor or preset.",
            "",
            "Parent: #6184",
            "",
        ]
    )
    return title, "\n".join(body_lines)


def _issues_by_family(issues: list[ExistingIssue]) -> dict[str, list[ExistingIssue]]:
    result: dict[str, list[ExistingIssue]] = {}
    for issue in issues:
        for match in _MARKER_RE.finditer(issue.body):
            result.setdefault(match.group(1), []).append(issue)
    return result


def _job_rows(manifest: dict[str, Any], family: str) -> int | None:
    by_ats = manifest.get("by_ats")
    artifact = by_ats.get(family) if isinstance(by_ats, dict) else None
    value = artifact.get("rows") if isinstance(artifact, dict) else None
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _markdown_code(value: str) -> str:
    safe = value.replace("`", "\u02cb").replace("@", "@\u200b")
    return f"`{safe}`"


def _label_names(value: object) -> tuple[str, ...]:
    if not isinstance(value, list):
        return ()
    names: set[str] = set()
    for raw in value:
        if isinstance(raw, dict) and isinstance(raw.get("name"), str):
            names.add(raw["name"])
    return tuple(sorted(names))


def _is_rate_limited_403(response: httpx.Response) -> bool:
    if response.headers.get("retry-after") is not None:
        return True
    if _optional_int(response.headers.get("x-ratelimit-remaining")) == 0:
        return True
    try:
        payload = response.json()
    except ValueError:
        return False
    message = payload.get("message") if isinstance(payload, dict) else None
    if not isinstance(message, str):
        return False
    lowered = message.casefold()
    return any(
        phrase in lowered
        for phrase in (
            "secondary rate limit",
            "api rate limit exceeded",
            "abuse detection mechanism",
        )
    )


def _optional_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None
