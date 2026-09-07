"""R2 staging — build byte-authoritative pending description uploads."""

from __future__ import annotations

from src.core.description_store import content_hash
from src.processing.cpu import _coerce_datetime

# Historical composite-hash helpers remain re-exported by ``src.batch`` for
# compatibility. They no longer participate in description staging: R2 stores
# HTML only, and the byte hash below is the sole description content version.
_HASH_VOLATILE_FIELDS = frozenset(
    {
        "valid_through",
        "expiration_date",
    }
)


def _stable_date(val: object | None) -> str | None:
    """Coerce a date to a stable ISO 8601 date-only compatibility value."""
    dt = _coerce_datetime(val)
    if dt is None:
        return None
    return dt.date().isoformat()


def _deep_sort(obj: object) -> object:
    """Recursively sort dicts by key and lists whose elements are all strings.

    Lists of strings inside the hash input represent set-like collections
    (``locations``, ``metadata["tags"]``, ``metadata["categories"]``, …) whose
    upstream order is often non-deterministic: Accenture, Google, Workday and
    others routinely return the same location set in different orders for
    different requests. Sorting them here makes the emitted JSON — and the
    content hash derived from it — stable across scrapes.

    Nested-list entries and heterogeneous lists keep their original order so
    ordered content (``extras["qualifications"]`` bullet points, structured
    dicts) is not reshuffled.
    """
    if isinstance(obj, dict):
        return {k: _deep_sort(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        if obj and all(isinstance(item, str) for item in obj):
            return sorted(obj)
        return [_deep_sort(item) for item in obj]
    return obj


def _deep_sort_legacy(obj: object) -> object:
    """Preserve the pre-#2223 deep-sort behavior for compatibility callers."""
    if isinstance(obj, dict):
        return {k: _deep_sort_legacy(v) for k, v in sorted(obj.items())}
    if isinstance(obj, list):
        return [_deep_sort_legacy(item) for item in obj]
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
    """Build the retired composite-hash extras shape for compatibility."""
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


def _hashable_payload(merged_extras: dict) -> dict:
    """Strip volatile fields from top-level and nested ``metadata`` dict."""
    hashable: dict = {}
    for k, v in merged_extras.items():
        if k in _HASH_VOLATILE_FIELDS:
            continue
        if k == "metadata" and isinstance(v, dict):
            v = {mk: mv for mk, mv in v.items() if mk not in _HASH_VOLATILE_FIELDS}
        hashable[k] = v
    return hashable


def _compute_r2_hash(description: str | None, merged_extras: dict) -> int:
    """Return the hash of the exact bytes uploaded by ``put_description``.

    ``merged_extras`` remains in the signature for compatibility with callers
    from before #8455. R2 description objects contain HTML only, so metadata
    must not affect their content version.
    """
    return content_hash(description or "")


def _compute_r2_hash_legacy(description: str | None, merged_extras: dict) -> int:
    """Compatibility alias for the retired composite-hash call shape.

    Existing composite hashes are preserved by the exact-HTML UPSERT when the
    body is unchanged; computing another composite candidate would only risk a
    migration rewrite wave.
    """
    return _compute_r2_hash(description, merged_extras)


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
) -> tuple[str, str, int, int] | None:
    """Build a byte-authoritative R2 description candidate.

    Returns ``(description_html, locale, byte_hash, compatibility_hash)`` or
    ``None`` when there is no description. Both hash slots contain
    ``content_hash(description_html)``; the duplicate fourth value preserves
    the existing call contract while callers migrate away from the former
    composite-hash compatibility parameter.

    ``current_hash`` is intentionally ignored. It comes from the scalar
    ``job_posting.description_r2_hash`` and cannot say whether the candidate
    locale already exists or whether that locale's exact HTML is current. The
    atomic description-row UPSERT is the final deduplication guard.
    """
    if not description:
        return None

    locale = language or "en"
    byte_hash = content_hash(description)
    return (description, locale, byte_hash, byte_hash)
