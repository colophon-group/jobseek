"""Experiment: compare Lightpanda vs Chromium CDP for browser-requiring boards.

Tests each unique browser-requiring domain with:
1. Plain HTTP (httpx) — baseline check
2. Lightpanda CDP via Playwright connect_over_cdp
3. Chromium CDP via Playwright connect_over_cdp

Reports success/failure rates and timing for each approach.
"""

from __future__ import annotations

import asyncio
import csv
import json
import time
from urllib.parse import urlparse

import httpx
from playwright.async_api import async_playwright

# --- CDP endpoints from .env.local ---
LIGHTPANDA_CDP = (
    "wss://euwest.cloud.lightpanda.io/ws"
    "?token=3aebdc5477d0a4676428c9784a4b9519196375e21dc9c399b90864319699a3ac"
    "&browser=lightpanda&proxy=fast_dc"
)
CHROMIUM_CDP = (
    "wss://euwest.cloud.lightpanda.io/ws"
    "?token=3aebdc5477d0a4676428c9784a4b9519196375e21dc9c399b90864319699a3ac"
    "&browser=chrome&proxy=fast_dc"
)

TIMEOUT_MS = 30_000
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/133.0.0.0 Safari/537.36"
)


def load_browser_boards() -> list[dict]:
    """Return deduplicated list of browser-requiring boards (one per domain)."""
    boards = []
    with open("data/boards.csv") as f:
        for row in csv.DictReader(f):
            mc = row.get("monitor_config", "{}") or "{}"
            sc = row.get("scraper_config", "{}") or "{}"
            mt = row.get("monitor_type", "")
            try:
                mconfig = json.loads(mc)
            except Exception:
                mconfig = {}
            try:
                sconfig = json.loads(sc)
            except Exception:
                sconfig = {}

            needs_browser = False
            if mconfig.get("render") is True:
                needs_browser = True
            if sconfig.get("render") is True:
                needs_browser = True
            if mt == "accenture":
                needs_browser = True
            if mt == "api_sniffer" and not mconfig.get("api_url"):
                needs_browser = True

            if needs_browser:
                boards.append(
                    {
                        "slug": row["board_slug"],
                        "url": row["board_url"],
                        "monitor_type": mt,
                        "scraper_type": row.get("scraper_type", ""),
                    }
                )

    # Deduplicate by domain — keep first occurrence
    seen = set()
    unique = []
    for b in boards:
        domain = urlparse(b["url"]).netloc
        if domain not in seen:
            seen.add(domain)
            unique.append(b)
    return unique


async def test_http(url: str, client: httpx.AsyncClient) -> dict:
    """Test plain HTTP GET."""
    t0 = time.monotonic()
    try:
        resp = await client.get(url, follow_redirects=True, timeout=30.0)
        elapsed = time.monotonic() - t0
        body = resp.text
        # Check if content is meaningful (>1KB and not an error page)
        meaningful = len(body) > 1024
        return {
            "ok": True,
            "status": resp.status_code,
            "bytes": len(body),
            "meaningful": meaningful,
            "elapsed_s": round(elapsed, 2),
            "error": None,
        }
    except Exception as e:
        elapsed = time.monotonic() - t0
        return {
            "ok": False,
            "status": None,
            "bytes": 0,
            "meaningful": False,
            "elapsed_s": round(elapsed, 2),
            "error": f"{type(e).__name__}: {e}",
        }


async def test_cdp(url: str, cdp_endpoint: str, pw) -> dict:
    """Test navigation via remote CDP browser."""
    t0 = time.monotonic()
    browser = None
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_endpoint, timeout=60_000)
        context = await browser.new_context(user_agent=USER_AGENT)
        context.set_default_timeout(TIMEOUT_MS)
        page = await context.new_page()
        await page.goto(url, wait_until="domcontentloaded", timeout=TIMEOUT_MS)
        content = await page.content()
        elapsed = time.monotonic() - t0
        meaningful = len(content) > 1024
        await context.close()
        return {
            "ok": True,
            "bytes": len(content),
            "meaningful": meaningful,
            "elapsed_s": round(elapsed, 2),
            "error": None,
        }
    except Exception as e:
        elapsed = time.monotonic() - t0
        return {
            "ok": False,
            "bytes": 0,
            "meaningful": False,
            "elapsed_s": round(elapsed, 2),
            "error": f"{type(e).__name__}: {e}",
        }
    finally:
        if browser:
            try:
                await browser.close()
            except Exception:
                pass


async def run_experiment():
    boards = load_browser_boards()
    print(f"Testing {len(boards)} unique browser-requiring domains\n")
    print("=" * 100)

    results = []

    async with (
        httpx.AsyncClient(
            headers={"User-Agent": USER_AGENT},
        ) as http_client,
        async_playwright() as pw,
    ):
        for i, board in enumerate(boards):
            url = board["url"]
            slug = board["slug"]
            print(f"\n[{i + 1}/{len(boards)}] {slug}")
            print(f"  URL: {url}")
            print(f"  Monitor: {board['monitor_type']}, Scraper: {board['scraper_type']}")

            # 1. Plain HTTP
            http_result = await test_http(url, http_client)
            print(
                f"  HTTP:       ok={http_result['ok']}, {http_result['bytes']}B, "
                f"{http_result['elapsed_s']}s"
                f"{' ERROR: ' + str(http_result['error']) if http_result['error'] else ''}"
            )

            # 2. Lightpanda CDP
            lp_result = await test_cdp(url, LIGHTPANDA_CDP, pw)
            print(
                f"  Lightpanda: ok={lp_result['ok']}, {lp_result['bytes']}B, "
                f"{lp_result['elapsed_s']}s"
                f"{' ERROR: ' + str(lp_result['error']) if lp_result['error'] else ''}"
            )

            # 3. Chromium CDP
            cr_result = await test_cdp(url, CHROMIUM_CDP, pw)
            print(
                f"  Chromium:   ok={cr_result['ok']}, {cr_result['bytes']}B, "
                f"{cr_result['elapsed_s']}s"
                f"{' ERROR: ' + str(cr_result['error']) if cr_result['error'] else ''}"
            )

            results.append(
                {
                    "slug": slug,
                    "url": url,
                    "monitor_type": board["monitor_type"],
                    "scraper_type": board["scraper_type"],
                    "http": http_result,
                    "lightpanda": lp_result,
                    "chromium": cr_result,
                }
            )

            # Small delay between domains to be polite
            await asyncio.sleep(1)

    # --- Summary ---
    print("\n" + "=" * 100)
    print("SUMMARY")
    print("=" * 100)

    total = len(results)

    # HTTP stats
    http_ok = sum(1 for r in results if r["http"]["ok"] and r["http"]["meaningful"])
    http_fail = total - http_ok
    print(f"\nPlain HTTP meaningful response: {http_ok}/{total} ({100 * http_ok / total:.1f}%)")
    print(f"Plain HTTP insufficient:        {http_fail}/{total} ({100 * http_fail / total:.1f}%)")

    # Lightpanda stats
    lp_ok = sum(1 for r in results if r["lightpanda"]["ok"] and r["lightpanda"]["meaningful"])
    lp_err = sum(1 for r in results if not r["lightpanda"]["ok"])
    lp_empty = total - lp_ok - lp_err
    print(f"\nLightpanda success:  {lp_ok}/{total} ({100 * lp_ok / total:.1f}%)")
    print(f"Lightpanda error:    {lp_err}/{total} ({100 * lp_err / total:.1f}%)")
    print(f"Lightpanda empty:    {lp_empty}/{total} ({100 * lp_empty / total:.1f}%)")

    # Chromium stats
    cr_ok = sum(1 for r in results if r["chromium"]["ok"] and r["chromium"]["meaningful"])
    cr_err = sum(1 for r in results if not r["chromium"]["ok"])
    cr_empty = total - cr_ok - cr_err
    print(f"\nChromium success:    {cr_ok}/{total} ({100 * cr_ok / total:.1f}%)")
    print(f"Chromium error:      {cr_err}/{total} ({100 * cr_err / total:.1f}%)")
    print(f"Chromium empty:      {cr_empty}/{total} ({100 * cr_empty / total:.1f}%)")

    # Timing stats
    lp_times = [r["lightpanda"]["elapsed_s"] for r in results if r["lightpanda"]["ok"]]
    cr_times = [r["chromium"]["elapsed_s"] for r in results if r["chromium"]["ok"]]

    if lp_times:
        print(
            f"\nLightpanda avg time: {sum(lp_times) / len(lp_times):.2f}s "
            f"(min={min(lp_times):.2f}s, max={max(lp_times):.2f}s)"
        )
        lp_hours_per_req = (sum(lp_times) / len(lp_times)) / 3600
        print(f"Lightpanda browser-hours/request: {lp_hours_per_req:.6f}")
    if cr_times:
        print(
            f"\nChromium avg time:   {sum(cr_times) / len(cr_times):.2f}s "
            f"(min={min(cr_times):.2f}s, max={max(cr_times):.2f}s)"
        )
        cr_hours_per_req = (sum(cr_times) / len(cr_times)) / 3600
        print(f"Chromium browser-hours/request:   {cr_hours_per_req:.6f}")

    # Cost analysis (Lightpanda pricing: $0.08/hour on Builder plan)
    lp_price_per_hour = 0.08
    if lp_times:
        lp_cost_per_req = lp_hours_per_req * lp_price_per_hour
        print(f"\nLightpanda cost/request: ${lp_cost_per_req:.6f}")
    if cr_times:
        cr_cost_per_req = cr_hours_per_req * lp_price_per_hour
        print(f"Chromium cost/request:   ${cr_cost_per_req:.6f}")

    # Error breakdown
    print("\n\nERROR DETAILS — Lightpanda:")
    for r in results:
        if r["lightpanda"]["error"]:
            print(f"  {r['slug']}: {r['lightpanda']['error'][:120]}")

    print("\nERROR DETAILS — Chromium:")
    for r in results:
        if r["chromium"]["error"]:
            print(f"  {r['slug']}: {r['chromium']['error'][:120]}")

    # Dump full results to JSON
    with open("scripts/browser_experiment_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\nFull results saved to scripts/browser_experiment_results.json")


if __name__ == "__main__":
    asyncio.run(run_experiment())
