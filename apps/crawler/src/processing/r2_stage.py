"""R2 staging — compute hashes, build extras, stage pending uploads."""

from __future__ import annotations

import json

from src.core.description_store import content_hash
from src.processing.cpu import _coerce_datetime

# Fields that are volatile across cycles and should be excluded from the
# R2 content hash to avoid spurious re-uploads.  They are still stored in
# extras (visible in history.json) but changes to them alone don't trigger
# a write.  Checked at top-level extras AND inside nested metadata dict.
_HASH_VOLATILE_FIELDS = frozenset(
    {
        "valid_through",
        "expiration_date",
    }
)


def _stable_date(val: object | None) -> str | None:
    """Coerce a date to a stable ISO 8601 date-only string (YYYY-MM-DD).

    Strips time components and timezone offsets so the hash doesn't churn
    when the source alternates between date-only and datetime formats.
    """
    dt = _coerce_datetime(val)
    if dt is None:
        return None
    return dt.date().isoformat()


def _deep_sort(obj: object) -> object:
    """Recursively sort dicts by key and lists of strings for stable JSON."""
    if isinstance(obj, dict):
        return {k: _deep_sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_deep_sort(item) for item in obj]
    return obj


def _build_r2_extras(
    *,
    title: str | None,
    locations: list[str] | None,
    extras: dict | None,
    metadata: dict | None,
    date_posted: object | None,
    base_salary: dict | None,
    employment_type: str | None,
    job_location_type: str | None,
) -> dict:
    """Build the merged extras dict for R2 upload."""
    merged: dict = {}
    if extras and isinstance(extras, dict):
        merged.update(extras)
    # Explicit fields overwrite anything from extras
    if title is not None:
        merged["title"] = title
    if locations:
        merged["locations"] = locations
    if metadata and isinstance(metadata, dict):
        merged["metadata"] = metadata
    if date_posted is not None:
        stable = _stable_date(date_posted)
        if stable is not None:
            merged["date_posted"] = stable
    if base_salary is not None:
        merged["base_salary"] = base_salary
    if employment_type is not None:
        merged["raw_employment_type"] = employment_type
    if job_location_type is not None:
        merged["raw_job_location_type"] = job_location_type
    return merged


def _compute_r2_hash(description: str | None, merged_extras: dict) -> int:
    """Compute a combined hash of all R2-bound content.

    Uses deep-sorted JSON serialization so nested dicts (metadata,
    base_salary, extras) produce a stable hash regardless of key order.
    Excludes volatile fields (valid_through, expiration_date) that change
    frequently but don't represent meaningful content updates.
    """
    parts = description or ""
    if merged_extras:
        hashable = {}
        for k, v in merged_extras.items():
            if k in _HASH_VOLATILE_FIELDS:
                continue
            if k == "metadata" and isinstance(v, dict):
                v = {mk: mv for mk, mv in v.items() if mk not in _HASH_VOLATILE_FIELDS}
            hashable[k] = v
        parts += "\0" + json.dumps(_deep_sort(hashable), sort_keys=True, ensure_ascii=False)
    return content_hash(parts)


def _serialize_localizations(
    localizations: dict | None,
    primary_locale: str,
) -> dict[str, str] | None:
    """Flatten localizations to ``{locale: html_string}`` for JSON storage."""
    if not localizations or not isinstance(localizations, dict):
        return None
    result: dict[str, str] = {}
    for loc_locale, loc_data in localizations.items():
        if loc_locale == primary_locale:
            continue
        if isinstance(loc_data, dict):
            desc = loc_data.get("description")
        elif isinstance(loc_data, str):
            desc = loc_data
        else:
            continue
        if desc:
            result[loc_locale] = desc
    return result or None


def _stage_r2_pending(
    *,
    title: str | None,
    description: str | None,
    language: str | None,
    locations: list[str] | None,
    localizations: dict | None,
    extras: dict | None,
    metadata: dict | None,
    date_posted: object | None,
    base_salary: dict | None,
    employment_type: str | None,
    job_location_type: str | None,
    current_hash: int | None = None,
    source: str = "monitor",
    tech_ids: list[int] | None = None,
) -> tuple[str, str, int] | None:
    """Compute R2 pending data without any network I/O.

    Returns ``(description_html, locale, new_hash)``
    or ``None`` if nothing changed (hash match) or no description.

    Callers write the result into the ``descriptions`` table.
    """
    if not description:
        return None

    locale = language or "en"
    merged = _build_r2_extras(
        title=title,
        locations=locations,
        extras=extras,
        metadata=metadata,
        date_posted=date_posted,
        base_salary=base_salary,
        employment_type=employment_type,
        job_location_type=job_location_type,
    )
    new_hash = _compute_r2_hash(description, merged)

    if current_hash is not None and current_hash == new_hash:
        return None

    return (description, locale, new_hash)
