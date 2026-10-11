"""Freeze ownership and currentness decisions through the original PDF gates."""

from __future__ import annotations

import asyncio
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from src.core.monitors import dom


class FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 11, 12, tzinfo=UTC)


async def main():
    single = {"pattern": r"Deadline: (\d{1,2} [A-Za-z]+ \d{4})", "date_format": "%d %B %Y"}
    window = {
        "active_window": {
            "pattern": r"(?P<month>[A-Za-z]+) (?P<opens>\d+)-(?P<closes>\d+), (?P<year>\d{4})",
            "date_format": "%B %d %Y",
        }
    }
    ownership = {"include": r"\bEmployer\b", "exclude": r"\bForeign\b"}
    definitions = [
        ("today-inclusive", {"require_unexpired_pdf": single}, "Deadline: 11 October 2026"),
        ("expired", {"require_unexpired_pdf": single}, "Deadline: 10 October 2026"),
        ("future", {"require_unexpired_pdf": single}, "Deadline: 1 November 2026"),
        ("missing-deadline", {"require_unexpired_pdf": single}, "No configured deadline"),
        (
            "ambiguous-deadline",
            {"require_unexpired_pdf": single},
            "Deadline: 11 October 2026\nDeadline: 12 October 2026",
        ),
        ("invalid-date", {"require_unexpired_pdf": single}, "Deadline: 31 February 2026"),
        (
            "german-month",
            {"require_unexpired_pdf": {"pattern": r"Deadline: (.+)", "date_format": "%d %B %Y"}},
            "Deadline: 3. März 2027",
        ),
        (
            "ordinal-of",
            {"require_unexpired_pdf": {"pattern": r"Deadline: (.+)", "date_format": "%d of %B %Y"}},
            "Deadline: 11th of October 2026",
        ),
        ("owner", {"require_pdf_text": ownership}, "Employer"),
        ("excluded-owner", {"require_pdf_text": ownership}, "Foreign"),
        ("ambiguous-owner", {"require_pdf_text": ownership}, "Employer Foreign"),
        ("unknown-owner", {"require_pdf_text": ownership}, "Unclassified publisher"),
        ("active-window", {"require_unexpired_pdf": window}, "October 10-11, 2026"),
        ("future-window", {"require_unexpired_pdf": window}, "October 12-20, 2026"),
        ("closed-window", {"require_unexpired_pdf": window}, "October 1-10, 2026"),
        ("reversed-window", {"require_unexpired_pdf": window}, "October 20-10, 2026"),
        (
            "multiple-windows",
            {"require_unexpired_pdf": window},
            "September 1-20, 2026\nOctober 10-15, 2026\nOctober 11-17, 2026",
        ),
        (
            "window-fallback",
            {"require_unexpired_pdf": {**window, "rules": [single]}},
            "Deadline: 11 October 2026",
        ),
        (
            "two-rule-ambiguity",
            {
                "require_unexpired_pdf": {
                    "rules": [single, {"pattern": r"Close: (.+)", "date_format": "%d %B %Y"}]
                }
            },
            "Deadline: 11 October 2026\nClose: 12 October 2026",
        ),
        (
            "API-owned",
            {"require_unexpired_pdf": single, "require_pdf_pattern": "Employer"},
            "Employer\nDeadline: 11 October 2026",
        ),
        (
            "API-foreign",
            {"require_unexpired_pdf": single, "require_pdf_pattern": "Employer"},
            "Foreign\nDeadline: 11 October 2026",
        ),
        (
            "expired-unclassified-owner",
            {"require_unexpired_pdf": single, "require_pdf_text": ownership},
            "Unknown\nDeadline: 10 October 2026",
        ),
    ]
    out = []
    source = "https://example.com/posting.pdf"
    for name, config, text in definitions:

        async def fetch(*args, _text=text, **kwargs):
            return _text

        row = {
            "name": name,
            "config": config,
            "text": text,
            "now": "2026-10-11T12:00:00Z",
            "keep": False,
            "deadline": "",
            "error": False,
        }
        try:
            urls = {source}
            with (
                patch.object(dom, "_fetch_bounded_pdf_text", fetch),
                patch.object(dom, "datetime", FrozenDateTime),
            ):
                if "require_unexpired_pdf" in config:
                    required = (
                        dom.re.compile(config["require_pdf_pattern"])
                        if "require_pdf_pattern" in config
                        else None
                    )
                    urls, dates = await dom._filter_unexpired_pdf_urls(
                        urls,
                        None,
                        dom._validated_unexpired_pdf_config(config["require_unexpired_pdf"]),
                        required_text_pattern=required,
                        raise_on_required_text_mismatch=True,
                        return_classified_currentness=True,
                    )
                    row["deadline"] = dates.get(source, "")
                if "require_pdf_text" in config:
                    urls = await dom._filter_pdf_text_urls(
                        urls, None, dom._validated_pdf_text_config(config["require_pdf_text"])
                    )
            row["keep"] = source in urls
        except ValueError:
            row["error"] = True
        out.append(row)
    Path(__file__).with_name("python_listing_pdf_proofs.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=2) + "\n"
    )


if __name__ == "__main__":
    asyncio.run(main())
