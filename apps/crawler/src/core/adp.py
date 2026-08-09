"""Shared ADP field normalization used by its monitor and detail scraper."""

from __future__ import annotations

import re

from src.core.enum_normalize import normalize_employment_type


def normalize_adp_employment_type(raw: object) -> str | None:
    """Normalize customizable ADP worker-category labels by their stable phrase."""
    if not isinstance(raw, str):
        return None
    label = re.sub(r"[-_/]+", " ", raw).casefold()
    label = " ".join(label.split())
    has_full_time = "full time" in label
    has_part_time = "part time" in label
    if has_full_time and has_part_time:
        return normalize_employment_type("full_or_part")
    if has_part_time:
        return normalize_employment_type("part_time")
    if has_full_time:
        return normalize_employment_type("full_time")
    if "per diem" in label or "seasonal" in label:
        return normalize_employment_type("part_time")
    if "intern" in label or "apprentice" in label or "trainee" in label:
        return normalize_employment_type("internship")
    if "temporary" in label or label == "temp":
        return normalize_employment_type("temporary")
    if "contract" in label or "consultant" in label:
        return normalize_employment_type("contract")
    return normalize_employment_type(raw)
