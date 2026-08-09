"""Conservative, explainable candidate identity and deduplication.

Only exact durable identities are hard stops.  Human-facing similarities are
retained as warnings for the configuration agent, never promoted to skips.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import json
import re
import unicodedata
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

from src.ats_inventory.compat import COMPATIBILITY, Compatibility
from src.ats_inventory.models import CompanyImpact
from src.ats_inventory.tenant_keys import tenant_key

if TYPE_CHECKING:
    from src.ats_inventory.github import GitHubWorkItem
    from src.ats_inventory.ledger import CandidateLedger

_FAMILY_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}$")
_MARKER_RE = re.compile(
    r"<!-- ats-inventory-candidate:v1 source=([a-z2-7]+) board=([0-9a-f]{64}) -->"
)
_NON_ALNUM_RE = re.compile(r"[^a-z0-9]+")
_ISSUE_PREFIX_RE = re.compile(
    r"^(?:\[ats inventory\]\s*)?(?:add|import|configure|monitor)\s+(?:company\s*:\s*)?",
    re.IGNORECASE,
)
_WORKDAY_STANDARD_HOST_RE = re.compile(
    r"^(?P<company>[a-z0-9_-]+)\.(?P<instance>wd\d+)\.myworkdayjobs\.com$"
)
_WORKDAY_CUSTOM_HOST_RE = re.compile(r"^(?P<instance>wd\d+)\.myworkdaysite\.com$")
_LOCALE_SEGMENT_RE = re.compile(r"^[a-z]{2}-[a-z]{2}$")
_TRACKING_QUERY_KEYS = {"fbclid", "gclid", "mc_cid", "mc_eid"}
_COMMON_SECOND_LEVEL_SUFFIXES = {
    "ac.uk",
    "co.jp",
    "co.nz",
    "co.uk",
    "com.au",
    "com.br",
    "com.mx",
    "com.sg",
    "org.uk",
}


@dataclass(frozen=True, slots=True)
class Candidate:
    family: str
    native_ats: str
    tenant: str
    source_key: str
    name: str
    slug: str
    board_url: str
    impact_unknown: bool
    active_jobs: int
    remote_jobs: int
    location_count: int
    country_codes: tuple[str, ...]
    latest_posted_at: str | None

    @classmethod
    def from_impact(cls, company: CompanyImpact) -> Candidate:
        if not _FAMILY_RE.fullmatch(company.ats):
            raise ValueError(f"invalid ATS family {company.ats!r}")
        compatibility = COMPATIBILITY.get(company.ats)
        if compatibility is None or not compatibility.candidate_eligible:
            raise ValueError(f"ATS family {company.ats!r} is not candidate eligible")
        board_url = normalize_board_url(company.url)
        tenant = candidate_tenant_key(company.ats, board_url)
        if tenant is None or len(tenant) > 240:
            tenant = f"url-sha256:{hash_text(board_url)}"
        source_component = quote(tenant, safe="")
        if len(source_component) > 400:
            source_component = f"tenant-sha256-{hash_text(tenant)}"
        source_key = f"ats-scrapers:{company.ats}:{source_component}"
        return cls(
            family=company.ats,
            native_ats=_native_ats_identity(company.ats, compatibility),
            tenant=tenant,
            source_key=source_key,
            name=company.name.strip(),
            slug=company.slug.strip(),
            board_url=board_url,
            impact_unknown=company.impact_unknown,
            active_jobs=company.active_jobs,
            remote_jobs=company.remote_jobs,
            location_count=company.location_count,
            country_codes=company.country_codes,
            latest_posted_at=company.latest_posted_at,
        )

    @property
    def source_hash(self) -> str:
        return hash_text(self.source_key)

    @property
    def board_url_hash(self) -> str:
        return hash_text(self.board_url)


@dataclass(frozen=True, slots=True)
class DedupEvidence:
    code: str
    summary: str
    references: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class CandidatePlan:
    candidate: Candidate
    hard_skips: tuple[DedupEvidence, ...]
    soft_warnings: tuple[DedupEvidence, ...]

    @property
    def eligible(self) -> bool:
        return not self.hard_skips

    def to_dict(self) -> dict[str, Any]:
        return {
            "decision": "eligible" if self.eligible else "hard_skip",
            "candidate": asdict(self.candidate),
            "hard_skips": [item.to_dict() for item in self.hard_skips],
            "soft_warnings": [item.to_dict() for item in self.soft_warnings],
        }


@dataclass(frozen=True, slots=True)
class LocalCompany:
    slug: str
    name: str
    website: str

    @property
    def reference(self) -> str:
        suffix = f" ({self.website})" if self.website else ""
        return f"company:{self.slug} {self.name}{suffix}"


@dataclass(frozen=True, slots=True)
class LocalBoard:
    company_slug: str
    board_slug: str
    board_url: str
    monitor_type: str

    @property
    def reference(self) -> str:
        return f"board:{self.board_slug} ({self.monitor_type}, {self.board_url})"


class LocalRegistryIndex:
    """Bulk in-memory index of the two checked-in registries."""

    def __init__(self) -> None:
        self.board_urls: dict[str, list[LocalBoard]] = defaultdict(list)
        self.ats_tenants: dict[tuple[str, str], list[LocalBoard]] = defaultdict(list)
        self.company_names: dict[str, list[LocalCompany]] = defaultdict(list)
        self.company_slugs: dict[str, list[LocalCompany]] = defaultdict(list)
        self.company_domains: dict[str, list[LocalCompany]] = defaultdict(list)
        self.company_tokens: dict[str, list[LocalCompany]] = defaultdict(list)

    @classmethod
    def from_csv(cls, companies_path: Path, boards_path: Path) -> LocalRegistryIndex:
        index = cls()
        with companies_path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                company = LocalCompany(
                    slug=(raw.get("slug") or "").strip(),
                    name=(raw.get("name") or "").strip(),
                    website=(raw.get("website") or "").strip(),
                )
                if not company.slug or not company.name:
                    continue
                index.company_names[normalize_label(company.name)].append(company)
                index.company_slugs[normalize_label(company.slug)].append(company)
                domain = website_domain(company.website)
                if domain:
                    index.company_domains[domain].append(company)
                for token in _label_tokens(company.name, company.slug):
                    index.company_tokens[token].append(company)

        with boards_path.open(newline="", encoding="utf-8") as handle:
            for raw in csv.DictReader(handle):
                raw_url = (raw.get("board_url") or "").strip()
                monitor_type = (raw.get("monitor_type") or "").strip()
                if not raw_url or not monitor_type:
                    continue
                try:
                    board_url = normalize_board_url(raw_url)
                except ValueError:
                    continue
                board = LocalBoard(
                    company_slug=(raw.get("company_slug") or "").strip(),
                    board_slug=(raw.get("board_slug") or "").strip(),
                    board_url=board_url,
                    monitor_type=monitor_type,
                )
                index.board_urls[hash_text(board_url)].append(board)
                config = _json_object(raw.get("monitor_config"))
                for family, compatibility in COMPATIBILITY.items():
                    if not _board_proves_compatibility(monitor_type, config, compatibility):
                        continue
                    tenant = candidate_tenant_key(family, board_url, config=config)
                    if tenant is not None:
                        native_ats = _native_ats_identity(family, compatibility)
                        index.ats_tenants[(native_ats, tenant)].append(board)
                        if family == "workday" and config.get("all_sites", True) is not False:
                            wildcard = _workday_tenant_wildcard(tenant)
                            if wildcard is not None:
                                index.ats_tenants[(native_ats, wildcard)].append(board)
        return index

    def hard_evidence(self, candidate: Candidate) -> list[DedupEvidence]:
        evidence: list[DedupEvidence] = []
        exact_urls = self.board_urls.get(candidate.board_url_hash, ())
        if exact_urls:
            evidence.append(
                DedupEvidence(
                    "existing_board_url",
                    "The normalized board URL is already configured.",
                    _references(exact_urls),
                )
            )
        tenant_keys = [(candidate.native_ats, candidate.tenant)]
        if candidate.family == "workday":
            wildcard = _workday_tenant_wildcard(candidate.tenant)
            if wildcard is not None:
                tenant_keys.append((candidate.native_ats, wildcard))
        exact_tenants = tuple(
            board for key in tenant_keys for board in self.ats_tenants.get(key, ())
        )
        if exact_tenants:
            evidence.append(
                DedupEvidence(
                    "existing_ats_tenant",
                    "The native ATS and exact tenant identity are already configured.",
                    _references(exact_tenants),
                )
            )
        return evidence

    def soft_evidence(self, candidate: Candidate) -> list[DedupEvidence]:
        result: list[DedupEvidence] = []
        name = normalize_label(candidate.name)
        slug = normalize_label(candidate.slug)
        exact_name = self.company_names.get(name, ()) if name else ()
        exact_slug = self.company_slugs.get(slug, ()) if slug else ()
        if exact_name or exact_slug:
            result.append(
                DedupEvidence(
                    "similar_company_identity",
                    "A company name or slug matches; verify whether this is another valid board.",
                    _references((*exact_name, *exact_slug)),
                )
            )

        related = self._related_companies(candidate)
        exact_refs = {item.reference for item in (*exact_name, *exact_slug)}
        related = [item for item in related if item.reference not in exact_refs]
        if related:
            result.append(
                DedupEvidence(
                    "possible_parent_or_region",
                    "A related parent, subsidiary, or regional company name exists.",
                    _references(related),
                )
            )

        domain = website_domain(candidate.board_url)
        domain_matches = self.company_domains.get(domain, ()) if domain else ()
        if domain_matches:
            result.append(
                DedupEvidence(
                    "shared_homepage_domain",
                    "The board shares a registrable domain with existing company metadata.",
                    _references(domain_matches),
                )
            )
        return result

    def _related_companies(self, candidate: Candidate) -> list[LocalCompany]:
        candidate_tokens = set(_label_tokens(candidate.name, candidate.slug))
        possible: dict[str, LocalCompany] = {}
        for token in candidate_tokens:
            matches = self.company_tokens.get(token, ())
            if len(matches) > 200:
                continue
            for company in matches:
                possible[company.slug] = company
        related: list[LocalCompany] = []
        candidate_name = normalize_label(candidate.name)
        for company in possible.values():
            company_name = normalize_label(company.name)
            if min(len(candidate_name), len(company_name)) < 4:
                continue
            company_tokens = set(_label_tokens(company.name, company.slug))
            if (
                candidate_name in company_name
                or company_name in candidate_name
                or candidate_tokens < company_tokens
                or company_tokens < candidate_tokens
            ):
                related.append(company)
        return sorted(related, key=lambda item: (item.name.casefold(), item.slug))


class GitHubCandidateIndex:
    """One bulk snapshot of import issues and active PR markers."""

    def __init__(self, items: Iterable[GitHubWorkItem] = ()) -> None:
        self.items: list[GitHubWorkItem] = []
        self.source_hashes: dict[str, list[GitHubWorkItem]] = defaultdict(list)
        self.board_hashes: dict[str, list[GitHubWorkItem]] = defaultdict(list)
        self.title_labels: dict[str, list[GitHubWorkItem]] = defaultdict(list)
        self.title_tokens: dict[str, list[GitHubWorkItem]] = defaultdict(list)
        for item in items:
            self.add(item)

    def add(self, item: GitHubWorkItem) -> None:
        self.items.append(item)
        for source_key, board_hash in parse_candidate_markers(item.body):
            self.source_hashes[hash_text(source_key)].append(item)
            self.board_hashes[board_hash].append(item)
        label = normalize_issue_title(item.title)
        if label:
            self.title_labels[label].append(item)
            for token in _label_tokens(label):
                self.title_tokens[token].append(item)

    def hard_evidence(self, candidate: Candidate) -> list[DedupEvidence]:
        evidence: list[DedupEvidence] = []
        source_items = self.source_hashes.get(candidate.source_hash, ())
        if source_items:
            evidence.append(
                DedupEvidence(
                    "github_source_marker",
                    "An import issue or active PR has the exact source marker.",
                    _references(source_items),
                )
            )
        url_items = self.board_hashes.get(candidate.board_url_hash, ())
        if url_items:
            evidence.append(
                DedupEvidence(
                    "github_board_marker",
                    "An import issue or active PR has the exact normalized board URL marker.",
                    _references(url_items),
                )
            )
        return evidence

    def soft_evidence(self, candidate: Candidate) -> list[DedupEvidence]:
        label = normalize_label(candidate.name)
        matches: dict[tuple[str, int], GitHubWorkItem] = {}
        for item in self.title_labels.get(label, ()):
            matches[(item.kind, item.number)] = item
        candidate_tokens = set(_label_tokens(candidate.name, candidate.slug))
        for token in candidate_tokens:
            token_items = self.title_tokens.get(token, ())
            if len(token_items) > 200:
                continue
            for item in token_items:
                title = normalize_issue_title(item.title)
                title_tokens = set(_label_tokens(title))
                if (
                    label in title
                    or title in label
                    or candidate_tokens < title_tokens
                    or title_tokens < candidate_tokens
                ):
                    matches[(item.kind, item.number)] = item
        if not matches:
            return []
        return [
            DedupEvidence(
                "similar_github_title",
                "A company-request issue or active PR has a similar title; inspect it manually.",
                _references(matches.values()),
            )
        ]


class CandidateDeduplicator:
    def __init__(
        self,
        local: LocalRegistryIndex,
        github: GitHubCandidateIndex,
        ledger: CandidateLedger,
    ) -> None:
        self.local = local
        self.github = github
        self.ledger = ledger

    def plan(self, candidate: Candidate) -> CandidatePlan:
        hard = [*self.local.hard_evidence(candidate), *self.github.hard_evidence(candidate)]
        ledger_source = self.ledger.find_source(candidate.source_key)
        if ledger_source is not None:
            hard.append(
                DedupEvidence(
                    "ledger_source_key",
                    "The durable ledger already records this exact source "
                    f"({ledger_source.state}).",
                    ledger_source.references,
                )
            )
        ledger_urls = self.ledger.find_url(candidate.board_url)
        if ledger_urls:
            hard.append(
                DedupEvidence(
                    "ledger_board_url",
                    "The durable ledger already records this exact normalized board URL.",
                    tuple(
                        sorted(
                            {reference for record in ledger_urls for reference in record.references}
                        )
                    ),
                )
            )
        soft = [*self.local.soft_evidence(candidate), *self.github.soft_evidence(candidate)]
        return CandidatePlan(candidate, tuple(hard), tuple(soft))


def normalize_board_url(value: str) -> str:
    """Conservatively normalize an exact board URL without guessing tenants."""

    if not isinstance(value, str) or not value.strip() or len(value) > 4096:
        raise ValueError("board URL is empty or too long")
    try:
        parsed = urlsplit(value.strip())
        port = parsed.port
    except ValueError as exc:
        raise ValueError("board URL is invalid") from exc
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        raise ValueError("board URL must be HTTP(S) with a hostname")
    if parsed.username or parsed.password:
        raise ValueError("board URL must not contain credentials")
    try:
        host = parsed.hostname.rstrip(".").encode("idna").decode("ascii").casefold()
    except UnicodeError as exc:
        raise ValueError("board URL hostname is invalid") from exc
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    netloc = host
    if port is not None and port not in {80, 443}:
        netloc = f"{netloc}:{port}"
    path = parsed.path or "/"
    if path != "/":
        path = path.rstrip("/") or "/"
    query_items = []
    for key, item in parse_qsl(parsed.query, keep_blank_values=True):
        lowered = key.casefold()
        if lowered.startswith("utm_") or lowered in _TRACKING_QUERY_KEYS:
            continue
        query_items.append((key, item))
    query = urlencode(sorted(query_items), doseq=True)
    return urlunsplit(("https", netloc, path, query, ""))


def candidate_tenant_key(
    family: str, board_url: str, *, config: dict[str, Any] | None = None
) -> str | None:
    """Return the candidate/board identity, including provider board scope.

    Impact matching intentionally keeps host details where job artifacts need
    them. Candidate dedup instead normalizes equivalent provider hosts and
    retains known multi-board scopes so a second valid board is not lost.
    """

    parsed = urlsplit(board_url)
    segments = tuple(segment.casefold() for segment in parsed.path.split("/") if segment)
    provider_token = _configured_provider_token(family, config or {})
    if provider_token is None:
        provider_token = _provider_token_from_url(family, parsed, segments)
    if provider_token is not None:
        return f"{family}:{provider_token}"
    if family == "workday":
        configured = config or {}
        company = str(configured.get("company") or "").strip().casefold()
        instance = str(configured.get("wd_instance") or "").strip().casefold()
        site = str(configured.get("site") or "").strip().casefold()
        if company and instance and site:
            return f"workday:{company}:{instance}:{site}"
        host = (parsed.hostname or "").casefold()
        standard = _WORKDAY_STANDARD_HOST_RE.fullmatch(host)
        if standard:
            scoped = (
                segments[1:] if segments and _LOCALE_SEGMENT_RE.fullmatch(segments[0]) else segments
            )
            if scoped:
                return (
                    f"workday:{standard.group('company')}:{standard.group('instance')}:{scoped[0]}"
                )
        custom = _WORKDAY_CUSTOM_HOST_RE.fullmatch(host)
        if custom and len(segments) >= 3 and segments[0] == "recruiting":
            return f"workday:{segments[1]}:{custom.group('instance')}:{segments[2]}"
    if family == "jobvite" and len(segments) > 0:
        token = segments[1] if segments[0] == "careers" and len(segments) > 1 else segments[0]
        return f"jobvite:{token}"
    if family == "moka":
        for label in ("social-recruitment", "campus-recruitment"):
            try:
                index = segments.index(label)
                return f"moka:{label}:{segments[index + 1]}:{segments[index + 2]}"
            except (ValueError, IndexError):
                continue
    if family == "successfactors":
        query = {key.casefold(): value for key, value in parse_qsl(parsed.query)}
        company = query.get("company", "").strip().casefold()
        if company and parsed.hostname:
            return f"successfactors:{parsed.hostname.casefold()}:{company}"
    if family == "taleo":
        base = tenant_key(family, board_url)
        query = {key.casefold(): value for key, value in parse_qsl(parsed.query)}
        cws = query.get("cws", "").strip().casefold()
        if base and cws:
            return f"{base}:cws:{cws}"
    if family == "keka" and parsed.hostname:
        scope = ""
        if len(segments) > 1 and segments[0] == "careers":
            scope = f":{segments[1]}"
        return f"keka:{parsed.hostname.casefold()}{scope}"
    return tenant_key(family, board_url)


def _workday_tenant_wildcard(tenant: str) -> str | None:
    if not tenant.startswith("workday:") or tenant.count(":") != 3:
        return None
    return f"{tenant.rsplit(':', 1)[0]}:*"


def _configured_provider_token(family: str, config: dict[str, Any]) -> str | None:
    keys = {
        "ashby": ("token",),
        "gem": ("token", "slug"),
        "greenhouse": ("token", "board_token"),
        "lever": ("token",),
        "rippling": ("slug",),
        "smartrecruiters": ("token",),
        "workable": ("token",),
    }.get(family, ())
    for key in keys:
        value = config.get(key)
        if isinstance(value, str) and 0 < len(value.strip()) <= 240:
            return value.strip().casefold()
    return None


def _provider_token_from_url(family: str, parsed: Any, segments: tuple[str, ...]) -> str | None:
    query = {key.casefold(): value for key, value in parse_qsl(parsed.query)}
    if family == "greenhouse":
        configured = query.get("for") or query.get("url_token")
        if configured:
            return configured.strip().casefold()
        value = _path_value_after(segments, "boards")
        if value:
            return value
        if segments and segments[0] not in {"embed", "v1"}:
            return segments[0]
    elif family == "ashby":
        value = _path_value_after(segments, "job-board")
        if value:
            return value
        if segments and segments[0] != "posting-api":
            return segments[0]
    elif family == "lever":
        value = _path_value_after(segments, "postings")
        if value:
            return value
        if segments:
            return segments[0]
    elif family == "smartrecruiters":
        value = _path_value_after(segments, "companies")
        if value:
            return value
        if segments:
            return segments[0]
    elif family == "gem":
        value = _path_value_after(segments, "v0")
        if value:
            return value
        if segments:
            return segments[0]
    elif family == "rippling":
        value = _path_value_after(segments, "board")
        if value:
            return value
        if segments:
            return segments[0]
    elif family == "workable":
        value = _path_value_after(segments, "accounts")
        if value:
            return value
        if segments:
            return segments[0]
    return None


def _path_value_after(segments: tuple[str, ...], label: str) -> str | None:
    try:
        return segments[segments.index(label) + 1]
    except (ValueError, IndexError):
        return None


def render_candidate_issue(plan: CandidatePlan, *, parent_issue: int = 6184) -> tuple[str, str]:
    candidate = plan.candidate
    marker = candidate_marker(candidate.source_key, candidate.board_url)
    title_name = _safe_title_text(candidate.name, max_length=180)
    body_name = _safe_code(_bounded_untrusted_text(candidate.name, max_length=300))
    title = f"Add company: {title_name}"
    impact = "unknown" if candidate.impact_unknown else str(candidate.active_jobs)
    lines = [
        marker,
        "### Company",
        "",
        f"`{body_name}` — `{_safe_code(candidate.board_url)}`",
        "",
        "### Preconfigured board source",
        "",
        f"- Source key: `{_safe_code(candidate.source_key)}`",
        f"- Upstream inventory family: `{candidate.family}`",
        f"- Jobseek-native ATS identity: `{candidate.native_ats}`",
        f"- Exact tenant: `{_safe_code(candidate.tenant)}`",
        f"- Normalized board URL: `{_safe_code(candidate.board_url)}`",
        f"- Published active jobs: {impact}",
        f"- Remote jobs: {candidate.remote_jobs}",
        f"- Distinct locations: {candidate.location_count}",
        f"- Countries: {', '.join(candidate.country_codes) or 'unknown'}",
        "",
        "The board is inventory evidence only. Use Jobseek-owned monitor and scraper code;",
        "do not import, execute, or add a runtime dependency on upstream scraper code.",
        "The simplified `ws` path should seed the compatible native monitor, then verify the",
        "company metadata, any additional official boards, live jobs, and normal quality gates.",
        "",
        "### Conservative deduplication context",
        "",
    ]
    if plan.soft_warnings:
        for warning in plan.soft_warnings:
            lines.append(f"- **{warning.code}**: {warning.summary}")
            lines.extend(f"  - `{_safe_code(reference)}`" for reference in warning.references)
    else:
        lines.append("- No soft matches found in the bulk local/GitHub indexes.")
    lines.extend(
        [
            "",
            "Soft matches are advisory. Preserve subsidiaries, regions, acquisitions, and",
            "valid additional ATS/board configurations unless verification proves a duplicate.",
            "",
            f"Parent: #{parent_issue}",
            "",
        ]
    )
    return title, "\n".join(lines)


def candidate_marker(source_key: str, board_url: str) -> str:
    source = base64.b32encode(source_key.encode("utf-8")).decode("ascii").rstrip("=").lower()
    return (
        f"<!-- ats-inventory-candidate:v1 source={source} "
        f"board={hash_text(normalize_board_url(board_url))} -->"
    )


def parse_candidate_markers(body: str) -> tuple[tuple[str, str], ...]:
    found: list[tuple[str, str]] = []
    for match in _MARKER_RE.finditer(body or ""):
        encoded = match.group(1).upper()
        encoded += "=" * (-len(encoded) % 8)
        try:
            source_key = base64.b32decode(encoded, casefold=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError):
            continue
        if not source_key.startswith("ats-scrapers:") or len(source_key) > 600:
            continue
        found.append((source_key, match.group(2)))
    return tuple(found)


def hash_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def normalize_label(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return _NON_ALNUM_RE.sub(" ", normalized.casefold()).strip()


def normalize_issue_title(value: str) -> str:
    return normalize_label(_ISSUE_PREFIX_RE.sub("", value.strip()))


def website_domain(value: str) -> str | None:
    try:
        parsed = urlsplit(value)
    except ValueError:
        return None
    if parsed.scheme.casefold() not in {"http", "https"} or not parsed.hostname:
        return None
    host = parsed.hostname.casefold().rstrip(".")
    if host.startswith("www."):
        host = host[4:]
    parts = host.split(".")
    if len(parts) <= 2:
        return host
    suffix = ".".join(parts[-2:])
    if suffix in _COMMON_SECOND_LEVEL_SUFFIXES and len(parts) >= 3:
        return ".".join(parts[-3:])
    return suffix


def _native_ats_identity(family: str, compatibility: Compatibility) -> str:
    if compatibility.kind == "native":
        assert compatibility.monitor_type is not None
        return f"native:{compatibility.monitor_type}"
    return f"family:{family}"


def _board_proves_compatibility(
    monitor_type: str, config: dict[str, Any], compatibility: Compatibility
) -> bool:
    if not compatibility.candidate_eligible or compatibility.monitor_type != monitor_type:
        return False
    if compatibility.kind == "native":
        return True
    if not compatibility.seedable and compatibility.monitor_config is None:
        return False
    expected = compatibility.monitor_config or {}
    return all(config.get(key) == value for key, value in expected.items())


def _json_object(value: object) -> dict[str, Any]:
    if not isinstance(value, str) or not value.strip():
        return {}
    try:
        decoded = json.loads(value)
    except json.JSONDecodeError:
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _label_tokens(*values: str) -> tuple[str, ...]:
    tokens: set[str] = set()
    for value in values:
        tokens.update(token for token in normalize_label(value).split() if len(token) >= 3)
    return tuple(sorted(tokens))


def _references(items: Iterable[Any], *, limit: int = 8) -> tuple[str, ...]:
    references = sorted({str(item.reference) for item in items})
    if len(references) > limit:
        hidden = len(references) - limit
        return (*references[:limit], f"... and {hidden} more")
    return tuple(references)


def _safe_code(value: str) -> str:
    return value.replace("`", "\u02cb").replace("@", "@\u200b")


def _bounded_untrusted_text(value: str, *, max_length: int) -> str:
    text = " ".join(value.split())
    text = "".join(character for character in text if character.isprintable()).strip()
    text = text.replace("@", "@\u200b")
    if not text:
        return "Unnamed company"
    if len(text) <= max_length:
        return text
    return f"{text[: max_length - 1].rstrip()}…"


def _safe_title_text(value: str, *, max_length: int) -> str:
    text = _bounded_untrusted_text(value, max_length=max_length)
    return text.translate(
        str.maketrans(
            {
                "`": "ˋ",
                "[": "［",
                "]": "］",
                "*": "∗",
                "_": "＿",
                "#": "＃",
                "<": "‹",
                ">": "›",
            }
        )
    )
