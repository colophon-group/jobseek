"""Freeze actual original DOM HTTP/binary behavior; no production effects."""

from __future__ import annotations

import asyncio
import base64
import importlib.util
import io
import json
import os
import zipfile
from dataclasses import asdict
from pathlib import Path

import httpx

from src.core.scrapers.dom import scrape

os.environ["DOM_GO_PARSE_ENABLED"] = "0"


def fixed_docx(**kwargs):
    source = helpers._make_document_docx(**kwargs)
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(source)) as original, zipfile.ZipFile(output, "w") as target:
        for name in original.namelist():
            info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
            target.writestr(info, original.read(name))
    return output.getvalue()


root = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location(
    "reference_dom_tests", root / "tests/test_dom_scraper.py"
)
helpers = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helpers)
legacy = Path(__file__).with_name("reference-simple.doc").read_bytes()


async def main():
    samples = [
        ("pdf", helpers._make_document_pdf("Consultant Role"), {"pdf": {"title_source": "text"}}),
        (
            "docx",
            fixed_docx(location="Zurich"),
            {"docx": {"title_source": "text", "location_pattern": r"Location: (.+)"}},
        ),
        (
            "docx-defaults",
            fixed_docx(),
            {
                "docx": {
                    "title_pattern": r"^(Strategic Communications Officer)$",
                    "title_source": "text",
                    "defaults": {"locations": ["Lausanne"], "employment_type": "full_time"},
                }
            },
        ),
        ("doc-url", legacy, {"doc": {}}),
        ("doc-text", legacy, {"doc": {"title_source": "text"}}),
        (
            "doc-regex-defaults",
            legacy,
            {"doc": {"title_pattern": r"(?im)^(This.+)$", "defaults": {"locations": ["Zurich"]}}},
        ),
        (
            "html",
            b"<html><h1>Software Engineer</h1><p>Build reliable systems.</p></html>",
            {"pdf": {}},
        ),
        ("invalid-pdf", b"%PDF- invalid", {"pdf": {}}),
        ("invalid-docx", b"PK\x03\x04invalid", {"docx": {}}),
        ("invalid-doc", bytes.fromhex("d0cf11e0a1b11ae1") + b"invalid", {"doc": {}}),
    ]
    rows = []
    for name, body, fallback in samples:
        config = {
            "steps": [
                {"tag": "h1", "field": "title"},
                {"tag": "p", "field": "description", "html": True},
            ],
            "document_fallback": fallback,
        }
        for mode in ("success", "header-reserved", "http-error"):

            async def handle(request, mode=mode, body=body):
                return httpx.Response(
                    403 if mode == "http-error" else 200,
                    content=body,
                    headers={"TDM-Reservation": "1"} if mode == "header-reserved" else {},
                )

            async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as client:
                try:
                    output = asdict(
                        await scrape("https://example.com/Engineer.doc", config, client)
                    )
                    error = False
                except Exception:
                    output = None
                    error = True
            rows.append(
                dict(
                    name=name + "-" + mode,
                    body=base64.b64encode(body).decode(),
                    config=config,
                    source="https://example.com/Engineer.doc",
                    mode=mode,
                    output=output,
                    error=error,
                )
            )
    Path(__file__).with_name("python_dom_document_fallback.json").write_text(
        json.dumps(rows, ensure_ascii=False, indent=2) + "\n"
    )


asyncio.run(main())
