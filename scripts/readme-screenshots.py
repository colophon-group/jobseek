"""Capture jseek.co screenshots for the README.

Run from the repo root:

    cd apps/crawler && uv run python ../../scripts/readme-screenshots.py

Outputs PNGs to ``.github/assets/readme/``. Re-run whenever the UI changes
or you want to refresh the README hero. Requires the crawler venv (Playwright
with Chromium installed).

Each target is a viewport-sized screenshot at 1440×900 @2x device-pixel-ratio,
matching what GitHub renders the README at. Cookie banners are pre-dismissed
by seeding ``localStorage`` so they never appear in the captures.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Awaitable, Callable

from playwright.async_api import Page, async_playwright

REPO_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = REPO_ROOT / ".github" / "assets" / "readme"
BASE_URL = "https://jseek.co"

VIEWPORT = {"width": 1440, "height": 900}
DEVICE_SCALE = 2
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/130.0.0.0 Safari/537.36"
)


async def shoot_homepage(page: Page) -> None:
    await page.goto(f"{BASE_URL}/en", wait_until="networkidle", timeout=30_000)
    await page.wait_for_timeout(2_500)


async def shoot_explore_with_query(page: Page) -> None:
    """Explore page with the search bar populated and the autocomplete
    dropdown open — demonstrates the search flow more clearly than the
    default unfiltered grid."""
    await page.goto(
        f"{BASE_URL}/en/explore",
        wait_until="networkidle",
        timeout=30_000,
    )
    await page.wait_for_timeout(2_500)
    search_input = page.get_by_role("combobox").first
    await search_input.click()
    await search_input.type("engineer", delay=60)
    # Wait for the suggestions dropdown to populate.
    await page.wait_for_timeout(2_500)


async def shoot_company_stripe(page: Page) -> None:
    await page.goto(
        f"{BASE_URL}/en/company/stripe",
        wait_until="networkidle",
        timeout=30_000,
    )
    await page.wait_for_timeout(3_500)


TARGETS: dict[str, Callable[[Page], Awaitable[None]]] = {
    "hero.png": shoot_homepage,
    "explore.png": shoot_explore_with_query,
    "company.png": shoot_company_stripe,
}


async def main() -> int:
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        context = await browser.new_context(
            viewport=VIEWPORT,
            device_scale_factor=DEVICE_SCALE,
            locale="en-US",
            user_agent=USER_AGENT,
        )
        # Pre-dismiss the cookie consent banner. The component reads
        # localStorage["cookie-consent"] on mount and bails if set.
        # See apps/web/src/components/CookieBanner.tsx.
        await context.add_init_script(
            "try { localStorage.setItem('cookie-consent', '1'); } catch (_) {}"
        )

        page = await context.new_page()
        for name, capture in TARGETS.items():
            try:
                await capture(page)
                out_path = OUT_DIR / name
                await page.screenshot(path=str(out_path), full_page=False)
                size_kb = out_path.stat().st_size // 1024
                print(f"saved {name} ({size_kb} KB)")
            except Exception as exc:  # noqa: BLE001
                print(f"FAIL {name}: {exc}", file=sys.stderr)
                return 1

        await browser.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
