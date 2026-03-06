"""Single-job scraper dispatcher.

Pure function — takes URL, scraper config, and HTTP client, returns job content.
No database awareness, no side effects beyond HTTP requests.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from src.core.scrapers import JobContent, enrich_description, get_scraper
from src.shared.throttle import throttle_domain

if TYPE_CHECKING:
    import httpx


async def _save_raw_page(
    artifact_dir: Path,
    url: str,
    job_id: str,
    http: httpx.AsyncClient,
) -> None:
    """Fetch and save raw page HTML to *artifact_dir*."""
    try:
        resp = await http.get(url, follow_redirects=True)
        if resp.status_code == 200:
            (artifact_dir / f"{job_id}.html").write_text(resp.text)
    except Exception:
        pass  # Best-effort


async def scrape_one(
    url: str,
    scraper_type: str,
    scraper_config: dict | None,
    http: httpx.AsyncClient,
    artifact_dir: Path | None = None,
    job_id: str | None = None,
    pw=None,
) -> JobContent:
    """Extract structured job data from one URL.

    This is the single-job layer — a pure function with no DB awareness.

    When *artifact_dir* is provided (workspace runs), the raw page HTML
    is saved there for debugging.

    When *pw* is provided (an ``AsyncPlaywright`` instance), it is forwarded
    to the scraper to reuse a shared browser process.
    """
    scraper = get_scraper(scraper_type)

    # Per-domain politeness throttle
    await throttle_domain(url)

    if artifact_dir is not None and job_id is not None:
        artifact_dir.mkdir(parents=True, exist_ok=True)
        await _save_raw_page(artifact_dir, url, job_id, http)

    content = await scraper(url, scraper_config or {}, http, pw=pw, artifact_dir=artifact_dir)

    enrich_description(content)
    return content
