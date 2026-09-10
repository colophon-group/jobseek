"""Browser-neutral structured job content shared by extraction runtimes."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(slots=True)
class JobContent:
    """Structured job data extracted from a single page.

    Text fields use HTML to preserve document structure. ``description`` is
    an HTML fragment, matching the format emitted by API monitors.
    """

    title: str | None = None
    description: str | None = None
    locations: list[str] | None = None
    employment_type: str | None = None
    job_location_type: str | None = None
    date_posted: str | None = None
    base_salary: dict | None = None
    language: str | None = None
    extras: dict | None = None
    metadata: dict | None = None

    def __post_init__(self) -> None:
        if isinstance(self.base_salary, str):
            from src.core.salary_extract import parse_salary_text

            self.base_salary = parse_salary_text(self.base_salary)


_TAG_RE = re.compile(r"<[^>]+>")
_SENTINEL_EMPTY = frozenset({"unavailable", "not available", "n/a", "none", "null", "-"})


def _plain(html: str) -> str:
    return _TAG_RE.sub(" ", html).strip()


def _is_meaningful(item: object) -> bool:
    text = _plain(str(item)).strip()
    return bool(text) and text.lower() not in _SENTINEL_EMPTY


def enrich_description(obj: JobContent) -> None:
    """Append meaningful structured extras to the description in place."""

    if not obj.extras:
        return

    desc_plain = _plain(obj.description).lower() if obj.description else ""
    sections: list[str] = []
    for key, heading in [
        ("responsibilities", "Responsibilities"),
        ("qualifications", "Qualifications"),
        ("skills", "Skills"),
    ]:
        items = obj.extras.get(key)
        if not items:
            continue

        if isinstance(items, str):
            if not _is_meaningful(items):
                continue
            snippet = _plain(items).lower()
            if desc_plain and snippet[:80] in desc_plain:
                continue
            sections.append(f"<h3>{heading}</h3>\n{items}")
        elif isinstance(items, list):
            non_empty = [item for item in items if _is_meaningful(item)]
            if not non_empty:
                continue
            already_present = False
            for item in non_empty:
                snippet = _plain(str(item)).lower()
                if len(snippet) >= 10:
                    if desc_plain and snippet[:80] in desc_plain:
                        already_present = True
                    break
            if already_present:
                continue
            list_items = "".join(f"<li>{item}</li>" for item in non_empty)
            sections.append(f"<h3>{heading}</h3>\n<ul>{list_items}</ul>")

    if not sections:
        return
    extra_html = "\n".join(sections)
    obj.description = f"{obj.description}\n{extra_html}" if obj.description else extra_html


__all__ = ["JobContent", "enrich_description"]
