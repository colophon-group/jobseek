"""Freeze actual legacy PDF field projection without selecting a binary engine."""

from __future__ import annotations

import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.core.scrapers.pdf import parse_bytes


async def main():
    cases = [
        ("url", "Senior_Engineer.pdf", "A job\n\nBuild tools", {}),
        ("empty", "Engineer.pdf", "", {}),
        ("hash", "0123456789abcdef0123456789_Engineer.pdf", "A job", {}),
        (
            "heading",
            "Job.pdf",
            "lowercase\n• bullet\n\nSenior Engineer\nBuild tools",
            {"title_source": "text"},
        ),
        ("long-heading", "Engineer.pdf", "A" * 121 + "\nNext heading", {"title_source": "text"}),
        (
            "missing-required",
            "Engineer.pdf",
            "No match",
            {"title_source": "text", "title_pattern": "Title: (.+)", "require_title_pattern": True},
        ),
        (
            "empty-required",
            "Engineer.pdf",
            "",
            {"title_source": "text", "title_pattern": "Title: (.+)", "require_title_pattern": True},
        ),
        (
            "title-text",
            "Engineer.pdf",
            "Title: Senior\nEngineer\n\nLocation: Zurich",
            {
                "title_source": "text",
                "title_pattern": "(?s)Title: (.*?)\\n\\n",
                "location_pattern": "Location: (.+)",
            },
        ),
        (
            "named",
            "Engineer.pdf",
            "Job: M\nechanical Engineer at A Coruna",
            {
                "fields_pattern": "(?s)Job: (?P<title>.*?) at (?P<location>.+)",
                "repair_split_initial": True,
            },
        ),
        (
            "title-only-repair",
            "Engineer.pdf",
            "Title: M\nechanical Engineer\nLocation: A Coruna",
            {
                "title_source": "text",
                "title_pattern": "(?s)Title: (.*?)\\nLocation:",
                "location_pattern": "Location: (.+)",
                "repair_split_initial": True,
            },
        ),
        (
            "hyphen",
            "Engineer.pdf",
            "Title: large-\nscale Researcher",
            {"title_source": "text", "title_pattern": "(?s)Title: (.+)"},
        ),
        (
            "no-capture",
            "Engineer.pdf",
            "Clinical Pharmacologist",
            {"title_pattern": "Clinical Pharmacologist"},
        ),
        (
            "url-pattern",
            "12_2026_Clinical_Pharmacologist_DE.pdf",
            "A role",
            {"title_pattern": "^\\d{2}_\\d{4}_(.+?)_DE$"},
        ),
        (
            "url-location",
            "Engineer-Zurich.pdf",
            "",
            {"location_url_pattern": "-(.+)\\.pdf$", "defaults": {"locations": ["Fallback"]}},
        ),
        (
            "defaults",
            "Engineer.pdf",
            "A role",
            {
                "defaults": {
                    "locations": ["Fribourg, Switzerland"],
                    "employment_type": "part_time",
                    "base_salary": {"currency": "CHF", "min": 100, "unit": "hour"},
                    "metadata": {"team": "Science"},
                }
            },
        ),
        ("description", "Engineer.pdf", "First & <Go>\nsecond\n\nThird paragraph", {}),
        (
            "optional-named",
            "Engineer.pdf",
            "Position: Researcher",
            {"fields_pattern": "Position: (?P<title>.+?)(?: at (?P<location>.+))?$"},
        ),
    ]
    output = []
    for name, filename, text, config in cases:
        source = "https://example.com/" + filename
        page = SimpleNamespace(extract_text=lambda text=text: text)
        with patch("pypdf.PdfReader", return_value=SimpleNamespace(pages=[page])):
            try:
                content = await parse_bytes(b"fixture", source, config)
                result, error = asdict(content), False
            except ValueError:
                result, error = None, True
        output.append(
            dict(name=name, text=text, source=source, config=config, output=result, error=error)
        )
    Path(__file__).with_name("python_pdf_text.json").write_text(
        json.dumps(output, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
