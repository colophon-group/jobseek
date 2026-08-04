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
