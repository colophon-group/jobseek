"""Small immutable models shared by inventory ingestion and queue stages."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class InventoryRow:
    ats: str
    name: str
    slug: str
    url: str


@dataclass(frozen=True, slots=True)
class Coverage:
    total_rows: int
    supported_rows: int
    unsupported_rows: int
    excluded_rows: int
    candidate_rows: int
    supported_candidate_rows: int
    classified_coverage_pct: float
    candidate_coverage_pct: float
    unsupported_families: tuple[str, ...]
    excluded_families: tuple[str, ...]
    candidate_generation_allowed: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class InventorySnapshot:
    manifest_sha256: str
    manifest_etag: str | None
    inventory_sha256: str
    generated_at: str
    validated_at: str
    manifest: dict[str, Any]
    rows: tuple[InventoryRow, ...]
    family_counts: dict[str, int]
    coverage: Coverage
    changed: bool
    etag_revalidated: bool
    new_families: tuple[str, ...]
    removed_families: tuple[str, ...]
    changed_urls: int

    def to_report(self) -> dict[str, Any]:
        return {
            "source": "kalil0321/ats-scrapers published inventory",
            "data_only": True,
            "manifest_sha256": self.manifest_sha256,
            "manifest_etag": self.manifest_etag,
            "inventory_sha256": self.inventory_sha256,
            "generated_at": self.generated_at,
            "validated_at": self.validated_at,
            "changed": self.changed,
            "etag_revalidated": self.etag_revalidated,
            "rows": len(self.rows),
            "families": len(self.family_counts),
            "family_counts": dict(sorted(self.family_counts.items())),
            "new_families": list(self.new_families),
            "removed_families": list(self.removed_families),
            "changed_urls": self.changed_urls,
            "coverage": self.coverage.to_dict(),
        }


@dataclass(frozen=True, slots=True)
class CompanyImpact:
    """Compact impact signals for one published company-inventory row."""

    ats: str
    name: str
    slug: str
    url: str
    impact_unknown: bool
    active_jobs: int
    remote_jobs: int
    location_count: int
    country_codes: tuple[str, ...]
    latest_posted_at: str | None

    @property
    def source_key(self) -> str:
        """Deterministic local tie-breaker; #6187 owns durable source identity."""

        return f"{self.ats}:{self.url.casefold()}"

    def to_dict(self) -> dict[str, Any]:
        return {
            "ats": self.ats,
            "name": self.name,
            "slug": self.slug,
            "url": self.url,
            "impact_unknown": self.impact_unknown,
            "active_jobs": self.active_jobs,
            "remote_jobs": self.remote_jobs,
            "location_count": self.location_count,
            "country_codes": list(self.country_codes),
            "latest_posted_at": self.latest_posted_at,
        }


@dataclass(frozen=True, slots=True)
class ImpactSnapshot:
    """Atomically published, compact company-impact snapshot."""

    snapshot_sha256: str
    manifest_sha256: str
    inventory_sha256: str
    generated_at: str
    derived_at: str
    companies: tuple[CompanyImpact, ...]
    family_artifacts: dict[str, str | None]
    family_reports: dict[str, dict[str, Any]]
    changed: bool
    downloads: int

    def ranked(self) -> tuple[CompanyImpact, ...]:
        """Rank active companies first, then unknowns, then confirmed zeroes."""
        return tuple(sorted(self.companies, key=company_impact_rank_key))

    def to_report(self) -> dict[str, Any]:
        known = sum(not company.impact_unknown for company in self.companies)
        active = sum(company.active_jobs > 0 for company in self.companies)
        return {
            "snapshot_sha256": self.snapshot_sha256,
            "manifest_sha256": self.manifest_sha256,
            "inventory_sha256": self.inventory_sha256,
            "generated_at": self.generated_at,
            "derived_at": self.derived_at,
            "changed": self.changed,
            "downloads": self.downloads,
            "companies": len(self.companies),
            "impact_known": known,
            "impact_unknown": len(self.companies) - known,
            "active_companies": active,
            "family_artifacts": dict(sorted(self.family_artifacts.items())),
            "family_reports": dict(sorted(self.family_reports.items())),
        }


def company_impact_rank_key(company: CompanyImpact) -> tuple[object, ...]:
    """Stable impact ordering shared by cache reports and queue selection."""

    if company.active_jobs > 0:
        impact_group = 0
    elif company.impact_unknown:
        impact_group = 1
    else:
        impact_group = 2
    return (
        impact_group,
        -company.active_jobs,
        -company.location_count,
        -len(company.country_codes),
        company.country_codes,
        company.name.casefold(),
        company.source_key,
    )
