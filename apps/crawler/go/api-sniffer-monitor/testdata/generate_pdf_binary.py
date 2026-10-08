"""Small binary extraction fixtures with actual legacy Python field output."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
from dataclasses import asdict
from pathlib import Path

import pypdf
from PIL import Image, ImageDraw, ImageFont

from src.core.scrapers.pdf import parse_bytes

root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "pdf_reference_tests", root / "tests/test_pdf_scraper.py"
)
assert spec is not None and spec.loader is not None
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)


def blank(pages, size=612):
    writer = pypdf.PdfWriter()
    for _ in range(pages):
        writer.add_blank_page(width=size, height=size)
    stream = io.BytesIO()
    writer.write(stream)
    return stream.getvalue()


async def main():
    image = Image.new("RGB", (1500, 300), "white")
    ImageDraw.Draw(image).text(
        (70, 80), "Software Engineer", fill="black", font=ImageFont.load_default(size=80)
    )
    stream = io.BytesIO()
    image.save(stream, format="PDF", resolution=72)
    samples = [
        (
            "text",
            helpers._make_pdf("Software Engineer in Zurich"),
            {"title_source": "text", "location_pattern": "(Zurich)"},
        ),
        ("url", helpers._make_pdf("Build reliable software"), {}),
        ("blank", blank(1), {}),
        (
            "required-failure",
            helpers._make_pdf("No matching title"),
            {"title_source": "text", "title_pattern": "Title: (.+)", "require_title_pattern": True},
        ),
        ("ocr", stream.getvalue(), {"ocr": True, "title_source": "text"}),
        ("ocr-page-limit", blank(21), {"ocr": True}),
        ("ocr-pixel-limit", blank(1, 6000), {"ocr": True}),
    ]
    cases = []
    for name, binary, config in samples:
        source = "https://example.com/Engineer.pdf"
        try:
            content = await parse_bytes(binary, source, config)
            output, error = asdict(content), False
        except ValueError:
            output, error = None, True
        cases.append(
            dict(
                name=name,
                body=base64.b64encode(binary).decode(),
                config=config,
                source=source,
                output=output,
                error=error,
            )
        )
    Path(__file__).with_name("python_pdf_binary.json").write_text(
        json.dumps(cases, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
