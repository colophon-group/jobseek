"""Pure async ``feedback`` function.

Lifted from ``src.workspace.commands.crawl.feedback_cmd``.  Records a
quality verdict + per-field ratings against the *active* named config in
:class:`~src.workspace.lib.claim_kv.ClaimKV`.

The lib intentionally does NOT touch any sibling named configs in the
KV — a feedback call against ``cfg-2`` cannot corrupt the slot for
``cfg-1`` (verified by the test suite).

Quality vocabulary
------------------

Each per-field rating is one of :data:`QUALITY_VALUES`.  The verdict is
one of :data:`VERDICT_VALUES`.  Any other value raises
:class:`~src.workspace.lib.exceptions.WsConfigInvalid`.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field
from typing import Any

from src.workspace.lib.claim_kv import ClaimKV
from src.workspace.lib.exceptions import WsConfigInvalid, WsFeedbackIncomplete

# ── Vocabulary (kept in sync with the CLI handler) ──────────────────

QUALITY_VALUES: tuple[str, ...] = ("clean", "noisy", "unusable", "absent")
VERDICT_VALUES: tuple[str, ...] = ("good", "acceptable", "poor", "unusable")

FEEDBACK_FIELDS: tuple[str, ...] = (
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "base_salary",
    "skills",
    "qualifications",
    "responsibilities",
    "valid_through",
)

REQUIRED_FIELDS: tuple[str, ...] = ("title", "description")
IMPORTANT_FIELDS: tuple[str, ...] = (
    "locations",
    "employment_type",
    "job_location_type",
)

# Quality ranking — used to pick the worst rating across a tier.
_QUALITY_RANK: dict[str, int] = {
    "clean": 0,
    "noisy": 1,
    "unusable": 2,
    "absent": 3,
}


# ── Result dataclass ────────────────────────────────────────────────


@dataclass
class FeedbackResult:
    """Structured result returned by :func:`feedback`.

    Mirrors the on-disk shape under ``cfg["feedback"]`` in the legacy
    YAML so the CLI adapter writes byte-identical state.
    """

    name: str
    verdict: str
    fields: dict[str, dict[str, str]] = field(default_factory=dict)
    required: dict[str, str] = field(default_factory=dict)
    important: dict[str, str] = field(default_factory=dict)
    optional: dict[str, str] = field(default_factory=dict)
    verdict_notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "verdict": self.verdict,
            "fields": {k: dict(v) for k, v in self.fields.items()},
            "required": dict(self.required),
            "important": dict(self.important),
            "optional": dict(self.optional),
            "verdict_notes": self.verdict_notes,
        }


# ── Public lib function ─────────────────────────────────────────────


async def feedback(
    claim_kv: ClaimKV,
    verdict: str,
    per_field: dict[str, dict[str, str]] | None = None,
    *,
    verdict_notes: str = "",
    monitor_run: dict[str, Any] | None = None,
    scraper_run: dict[str, Any] | None = None,
) -> FeedbackResult:
    """Record feedback against the active named config in ``claim_kv``.

    The active name is read via :meth:`ClaimKV.get_active`.  The slot
    under that name is updated with a ``feedback`` key holding the
    structured rating; sibling named configs in ``claim_kv`` are NOT
    touched.

    Args:
        claim_kv: Per-claim KV store with an active config set.
        verdict: One of :data:`VERDICT_VALUES`.
        per_field: Optional mapping ``field_name -> {"quality": str,
            "notes": str?}``.  ``quality`` must be in
            :data:`QUALITY_VALUES`.  Coverage strings (``"3/10"``) are
            computed from ``monitor_run`` / ``scraper_run`` when those
            are provided, otherwise omitted (matches CLI when no
            programmatic sample exists).
        verdict_notes: Free-text comment on the verdict (the CLI flag is
            ``--verdict-notes``; required by the click handler but
            optional here so HTTP callers can omit).
        monitor_run: Optional run summary used to compute coverage
            fractions for monitor-side fields. Shape mirrors the legacy
            YAML: ``{"jobs": int, "quality": {field: count, ...}}``.
        scraper_run: Same shape, for scraper-side fields.

    Returns:
        :class:`FeedbackResult` with per-field, tier, and verdict data.

    Raises:
        WsConfigInvalid: ``verdict`` not in :data:`VERDICT_VALUES`, no
            active named config, or a per-field quality not in
            :data:`QUALITY_VALUES`.
        WsFeedbackIncomplete: a field has positive coverage but no
            explicit rating, or a required field is missing.
    """
    if verdict not in VERDICT_VALUES:
        raise WsConfigInvalid(
            f"feedback: verdict must be one of {VERDICT_VALUES!r}, got {verdict!r}"
        )

    name = await claim_kv.get_active()
    if not name:
        raise WsConfigInvalid("feedback: no active named config in claim_kv")

    slot = await claim_kv.get(name)
    if not isinstance(slot, dict):
        raise WsConfigInvalid(f"feedback: active config {name!r} is not a stored dict slot")
    slot = copy.deepcopy(slot)

    # Coverage data (count per field) is the union of monitor + scraper
    # quality dicts. Totals come from ``jobs`` / ``count``.
    monitor_run = monitor_run or {}
    scraper_run = scraper_run or {}
    monitor_total = int(monitor_run.get("jobs", 0) or 0)
    scraper_total = int(scraper_run.get("count", 0) or 0)
    monitor_quality = monitor_run.get("quality") or {}
    scraper_quality = scraper_run.get("quality") or {}
    coverage_data: dict[str, int] = {**monitor_quality, **scraper_quality}
    has_field_data = bool(coverage_data)

    explicit: dict[str, dict[str, str]] = {}
    for k, v in (per_field or {}).items():
        if not isinstance(v, dict):
            raise WsConfigInvalid(
                f"feedback: per_field[{k!r}] must be a dict, got {type(v).__name__}"
            )
        q = v.get("quality")
        if q is None:
            continue  # caller may pass {"notes": "..."} placeholder; treated as no rating
        if q not in QUALITY_VALUES:
            raise WsConfigInvalid(
                f"feedback: per_field[{k!r}].quality must be one of {QUALITY_VALUES!r}, got {q!r}"
            )
        explicit[k] = {kk: str(vv) for kk, vv in v.items()}

    fields_fb: dict[str, dict[str, str]] = {}
    for fname in FEEDBACK_FIELDS:
        count = int(coverage_data.get(fname, 0) or 0)
        # Total comes from whichever side provided the coverage data.
        if fname in scraper_quality:
            total = scraper_total or monitor_total
        else:
            total = monitor_total or scraper_total
        coverage = (f"{count}/{total}" if total else "0/0") if has_field_data else ""

        rating = explicit.get(fname)
        quality_value = rating.get("quality") if rating else None
        if quality_value is None and has_field_data and count == 0:
            quality_value = "absent"

        if quality_value is None:
            continue

        entry: dict[str, str] = {"quality": quality_value}
        if coverage:
            entry["coverage"] = coverage
        if rating and rating.get("notes"):
            entry["notes"] = rating["notes"]
        fields_fb[fname] = entry

    # Validate completeness — fields with coverage > 0 (or required) must have
    # an explicit rating.
    missing: list[str] = []
    for fname in FEEDBACK_FIELDS:
        if fname in fields_fb:
            continue
        count = int(coverage_data.get(fname, 0) or 0)
        if count > 0 or fname in REQUIRED_FIELDS:
            missing.append(fname)
    if missing:
        raise WsFeedbackIncomplete(
            "feedback: explicit per-field quality required for: " + ", ".join(missing)
        )

    def _tier_summary(tier: tuple[str, ...]) -> dict[str, str]:
        tier_coverage = 0
        tier_total = 0
        worst = "clean"
        for f in tier:
            fb = fields_fb.get(f)
            if not fb:
                continue
            cov = fb.get("coverage")
            if cov:
                c, t = cov.split("/")
                tier_coverage += int(c)
                tier_total += int(t)
            if _QUALITY_RANK.get(fb["quality"], 0) > _QUALITY_RANK.get(worst, 0):
                worst = fb["quality"]
        return {"coverage": f"{tier_coverage}/{tier_total}", "quality": worst}

    optional_tier = tuple(
        f for f in FEEDBACK_FIELDS if f not in REQUIRED_FIELDS and f not in IMPORTANT_FIELDS
    )

    feedback_data = {
        "fields": fields_fb,
        "required": _tier_summary(REQUIRED_FIELDS),
        "important": _tier_summary(IMPORTANT_FIELDS),
        "optional": _tier_summary(optional_tier),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
    }

    slot["feedback"] = feedback_data
    await claim_kv.set(name, slot)
    # Active pointer is unchanged — feedback is recorded against whichever
    # slot was already active.

    return FeedbackResult(
        name=name,
        verdict=verdict,
        fields=fields_fb,
        required=feedback_data["required"],
        important=feedback_data["important"],
        optional=feedback_data["optional"],
        verdict_notes=verdict_notes,
    )


__all__ = [
    "FEEDBACK_FIELDS",
    "FeedbackResult",
    "IMPORTANT_FIELDS",
    "QUALITY_VALUES",
    "REQUIRED_FIELDS",
    "VERDICT_VALUES",
    "feedback",
]
