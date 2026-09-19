"""Regression coverage for the University of New Mexico board configuration."""

from __future__ import annotations

import csv
import io
import json
import zipfile
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from src.core.monitors.dom import dom_discover
from src.core.scrapers.dom import scrape

DATA_DIR = Path(__file__).resolve().parents[1] / "data"
BOARD_SLUG = "university-of-new-mexico-graduate-assistantships"
FETCH_PATCH = "src.shared.http_retry.fetch_with_retry"


def _board_configs() -> tuple[dict, dict]:
    with (DATA_DIR / "boards.csv").open(newline="", encoding="utf-8") as handle:
        row = next(row for row in csv.DictReader(handle) if row["board_slug"] == BOARD_SLUG)
    return json.loads(row["monitor_config"]), json.loads(row["scraper_config"])


def _docx_bytes() -> bytes:
    document = b"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Assistantship Type</w:t></w:r></w:p>
    <w:p><w:r><w:t>Graduate Teaching Associate</w:t></w:r></w:p>
    <w:p><w:r><w:t>Department</w:t></w:r></w:p>
    <w:p><w:r><w:t>Center for Teaching and Learning</w:t></w:r></w:p>
    <w:p><w:r><w:t>Position Summary</w:t></w:r></w:p>
    <w:p><w:r><w:t>Support graduate teaching and student learning.</w:t></w:r></w:p>
  </w:body>
</w:document>"""
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w") as archive:
        archive.writestr("word/document.xml", document)
    return output.getvalue()


async def test_monitor_scopes_links_to_current_openings() -> None:
    monitor_config, _ = _board_configs()
    html = """
    <main id="primary">
      <a href="/assets/unrelated-policy.pdf">Policy</a>
      <h2>Current Openings</h2>
      <h3><a href="docs/teaching-assistant.docx">Teaching Assistant</a></h3>
      <h3><a href="docs/project-assistant.pdf">Project Assistant</a></h3>
      <h2>Assistantship Eligibility</h2>
      <a href="assistantship_posting_template.docx">Posting template</a>
    </main>
    """
    board = {
        "board_url": (
            "https://oap.unm.edu/graduate-student-assistantships/"
            "assistantship-opportunities2/assistantship-opportunities.html"
        ),
        "metadata": monitor_config,
    }

    with patch(FETCH_PATCH, AsyncMock(return_value=html)):
        jobs = await dom_discover(board, AsyncMock())

    assert {job.url for job in jobs} == {
        "https://oap.unm.edu/graduate-student-assistantships/"
        "assistantship-opportunities2/docs/teaching-assistant.docx",
        "https://oap.unm.edu/graduate-student-assistantships/"
        "assistantship-opportunities2/docs/project-assistant.pdf",
    }
    assert {job.title for job in jobs} == {"Teaching Assistant", "Project Assistant"}


async def test_docx_fallback_extracts_required_fields() -> None:
    _, scraper_config = _board_configs()

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=_docx_bytes(), request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        content = await scrape(
            "https://oap.unm.edu/jobs/teaching-assistant.docx",
            scraper_config,
            client,
        )

    assert content.title == "Graduate Teaching Associate"
    assert content.locations == ["Albuquerque, NM, US"]
    assert content.employment_type == "temporary"
    assert "Support graduate teaching and student learning." in (content.description or "")


def test_document_probe_fix_covers_both_board_formats() -> None:
    _, scraper_config = _board_configs()
    assert set(scraper_config["document_fallback"]) == {"pdf", "docx"}
