"""Experiment v2: Lightpanda vs Chromium CDP — proper benchmarking.

Fixes from v1:
- Separates connection time from navigation time
- Tests multiple wait strategies (commit, domcontentloaded, networkidle)
- Measures connect, goto, and content() independently
- Reports head-to-head speedup ratios
"""

from __future__ import annotations

import asyncio
import csv
import json
import statistics
import time
from urllib.parse import urlparse

from playwright.async_api import async_playwright

# --- CDP endpoints ---
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

WAIT_STRATEGIES = ["commit", "domcontentloaded", "networkidle"]


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

    seen = set()
    unique = []
    for b in boards:
        domain = urlparse(b["url"]).netloc
        if domain not in seen:
            seen.add(domain)
            unique.append(b)
    return unique


async def test_cdp_granular(url: str, cdp_endpoint: str, pw, wait_until: str) -> dict:
    """Connect → navigate → content, measuring each phase separately."""
    timings = {}

    # Phase 1: CDP connection
    t0 = time.monotonic()
    try:
        browser = await pw.chromium.connect_over_cdp(cdp_endpoint, timeout=60_000)
    except Exception as e:
        return {
            "ok": False,
            "bytes": 0,
            "meaningful": False,
            "timings": {"connect_ms": round((time.monotonic() - t0) * 1000, 1)},
            "error": f"CONNECT: {type(e).__name__}: {str(e)[:200]}",
        }
    timings["connect_ms"] = round((time.monotonic() - t0) * 1000, 1)

    try:
        # Phase 2: Context + page setup
        t1 = time.monotonic()
        context = await browser.new_context(user_agent=USER_AGENT)
        context.set_default_timeout(TIMEOUT_MS)
        page = await context.new_page()
        timings["setup_ms"] = round((time.monotonic() - t1) * 1000, 1)

        # Phase 3: Navigation (the core rendering work)
        t2 = time.monotonic()
        await page.goto(url, wait_until=wait_until, timeout=TIMEOUT_MS)
        timings["navigation_ms"] = round((time.monotonic() - t2) * 1000, 1)

        # Phase 4: Extract content
        t3 = time.monotonic()
        content = await page.content()
        timings["content_ms"] = round((time.monotonic() - t3) * 1000, 1)

        timings["render_ms"] = timings["navigation_ms"] + timings["content_ms"]
        timings["total_ms"] = round((time.monotonic() - t0) * 1000, 1)

        await context.close()
        return {
            "ok": True,
            "bytes": len(content),
            "meaningful": len(content) > 1024,
            "timings": timings,
            "error": None,
        }
    except Exception as e:
        timings["total_ms"] = round((time.monotonic() - t0) * 1000, 1)
        return {
            "ok": False,
            "bytes": 0,
            "meaningful": False,
            "timings": timings,
            "error": f"{type(e).__name__}: {str(e)[:200]}",
        }
    finally:
        try:
            await browser.close()
        except Exception:
            pass


async def run_experiment():
    boards = load_browser_boards()
    print(f"Testing {len(boards)} unique browser-requiring domains")
    print(f"Wait strategies: {WAIT_STRATEGIES}")
    print(f"Each test: fresh CDP connect → navigate → extract → close")
    print("=" * 120)

    results = []

    async with async_playwright() as pw:
        for i, board in enumerate(boards):
            url = board["url"]
            slug = board["slug"]
            print(f"\n[{i + 1}/{len(boards)}] {slug}")
            print(f"  URL: {url}")

            board_result = {
                "slug": slug,
                "url": url,
                "monitor_type": board["monitor_type"],
                "scraper_type": board["scraper_type"],
                "lightpanda": {},
                "chromium": {},
            }

            for wait in WAIT_STRATEGIES:
                # Lightpanda
                lp = await test_cdp_granular(url, LIGHTPANDA_CDP, pw, wait)
                board_result["lightpanda"][wait] = lp
                t = lp["timings"]
                status = "OK" if lp["ok"] else "ERR"
                print(
                    f"  LP [{wait:>18}]: {status}  "
                    f"conn={t.get('connect_ms', '—'):>6}  "
                    f"nav={t.get('navigation_ms', '—'):>6}  "
                    f"content={t.get('content_ms', '—'):>5}  "
                    f"render={t.get('render_ms', '—'):>6}  "
                    f"{lp['bytes']:>7}B"
                    f"{'  ' + lp['error'][:60] if lp['error'] else ''}"
                )

                # Chromium
                cr = await test_cdp_granular(url, CHROMIUM_CDP, pw, wait)
                board_result["chromium"][wait] = cr
                t = cr["timings"]
                status = "OK" if cr["ok"] else "ERR"
                print(
                    f"  CR [{wait:>18}]: {status}  "
                    f"conn={t.get('connect_ms', '—'):>6}  "
                    f"nav={t.get('navigation_ms', '—'):>6}  "
                    f"content={t.get('content_ms', '—'):>5}  "
                    f"render={t.get('render_ms', '—'):>6}  "
                    f"{cr['bytes']:>7}B"
                    f"{'  ' + cr['error'][:60] if cr['error'] else ''}"
                )

            results.append(board_result)
            await asyncio.sleep(0.5)

    # --- Summary ---
    print("\n" + "=" * 120)
    print("SUMMARY")
    print("=" * 120)

    total = len(results)

    for wait in WAIT_STRATEGIES:
        print(f"\n{'=' * 60}")
        print(f"  Wait strategy: {wait}")
        print(f"{'=' * 60}")

        lp_ok = [r for r in results if r["lightpanda"][wait]["ok"]]
        cr_ok = [r for r in results if r["chromium"][wait]["ok"]]
        lp_err = total - len(lp_ok)
        cr_err = total - len(cr_ok)

        print(
            f"  Success — LP: {len(lp_ok)}/{total} ({100 * len(lp_ok) / total:.1f}%)  "
            f"CR: {len(cr_ok)}/{total} ({100 * len(cr_ok) / total:.1f}%)"
        )
        print(
            f"  Errors  — LP: {lp_err} ({100 * lp_err / total:.1f}%)  "
            f"CR: {cr_err} ({100 * cr_err / total:.1f}%)"
        )

        def stats_line(label, values):
            if not values:
                return
            print(
                f"  {label}: p50={statistics.median(values):.0f}ms  "
                f"avg={statistics.mean(values):.0f}ms  "
                f"p90={sorted(values)[min(int(len(values) * 0.9), len(values) - 1)]:.0f}ms  "
                f"max={max(values):.0f}ms"
            )

        # Connection times
        lp_conn = [
            r["lightpanda"][wait]["timings"]["connect_ms"]
            for r in results
            if "connect_ms" in r["lightpanda"][wait]["timings"]
        ]
        cr_conn = [
            r["chromium"][wait]["timings"]["connect_ms"]
            for r in results
            if "connect_ms" in r["chromium"][wait]["timings"]
        ]
        print()
        stats_line("  LP connect  ", lp_conn)
        stats_line("  CR connect  ", cr_conn)

        # Navigation times (the pure rendering comparison)
        lp_nav = [
            r["lightpanda"][wait]["timings"]["navigation_ms"]
            for r in results
            if r["lightpanda"][wait]["ok"] and "navigation_ms" in r["lightpanda"][wait]["timings"]
        ]
        cr_nav = [
            r["chromium"][wait]["timings"]["navigation_ms"]
            for r in results
            if r["chromium"][wait]["ok"] and "navigation_ms" in r["chromium"][wait]["timings"]
        ]
        print()
        stats_line("  LP navigate ", lp_nav)
        stats_line("  CR navigate ", cr_nav)

        # Content extraction times
        lp_content = [
            r["lightpanda"][wait]["timings"]["content_ms"]
            for r in results
            if r["lightpanda"][wait]["ok"] and "content_ms" in r["lightpanda"][wait]["timings"]
        ]
        cr_content = [
            r["chromium"][wait]["timings"]["content_ms"]
            for r in results
            if r["chromium"][wait]["ok"] and "content_ms" in r["chromium"][wait]["timings"]
        ]
        print()
        stats_line("  LP content()", lp_content)
        stats_line("  CR content()", cr_content)

        # Render = nav + content (the actual browser work, excluding connect overhead)
        lp_render = [
            r["lightpanda"][wait]["timings"]["render_ms"]
            for r in results
            if r["lightpanda"][wait]["ok"] and "render_ms" in r["lightpanda"][wait]["timings"]
        ]
        cr_render = [
            r["chromium"][wait]["timings"]["render_ms"]
            for r in results
            if r["chromium"][wait]["ok"] and "render_ms" in r["chromium"][wait]["timings"]
        ]
        print()
        stats_line("  LP render   ", lp_render)
        stats_line("  CR render   ", cr_render)

        # Head-to-head on navigation (pure rendering speed)
        both_ok = [
            r
            for r in results
            if r["lightpanda"][wait]["ok"]
            and r["chromium"][wait]["ok"]
            and "navigation_ms" in r["lightpanda"][wait]["timings"]
            and "navigation_ms" in r["chromium"][wait]["timings"]
        ]
        if both_ok:
            speedups_nav = []
            speedups_render = []
            for r in both_ok:
                lp_ms = r["lightpanda"][wait]["timings"]["navigation_ms"]
                cr_ms = r["chromium"][wait]["timings"]["navigation_ms"]
                if lp_ms > 0:
                    speedups_nav.append(cr_ms / lp_ms)
                lp_r = r["lightpanda"][wait]["timings"].get("render_ms", lp_ms)
                cr_r = r["chromium"][wait]["timings"].get("render_ms", cr_ms)
                if lp_r > 0:
                    speedups_render.append(cr_r / lp_r)

            print(f"\n  HEAD-TO-HEAD ({len(both_ok)} domains where both succeeded):")
            print(f"  Navigation speedup (>1 = LP faster):")
            print(f"    LP faster: {sum(1 for s in speedups_nav if s > 1.1)}/{len(speedups_nav)}")
            print(
                f"    Similar:   {sum(1 for s in speedups_nav if 0.9 <= s <= 1.1)}/{len(speedups_nav)}"
            )
            print(f"    CR faster: {sum(1 for s in speedups_nav if s < 0.9)}/{len(speedups_nav)}")
            print(f"    Median:    {statistics.median(speedups_nav):.2f}x")
            print(f"    Mean:      {statistics.mean(speedups_nav):.2f}x")
            if speedups_render:
                print(f"  Render speedup (nav+content, >1 = LP faster):")
                print(f"    Median:    {statistics.median(speedups_render):.2f}x")
                print(f"    Mean:      {statistics.mean(speedups_render):.2f}x")

    # Cost projection using domcontentloaded (most common wait strategy)
    wait = "domcontentloaded"
    print(f"\n\n{'=' * 120}")
    print(f"COST PROJECTION (wait={wait})")
    print(f"{'=' * 120}")

    lp_conn_vals = [
        r["lightpanda"][wait]["timings"]["connect_ms"]
        for r in results
        if "connect_ms" in r["lightpanda"][wait]["timings"]
    ]
    cr_conn_vals = [
        r["chromium"][wait]["timings"]["connect_ms"]
        for r in results
        if "connect_ms" in r["chromium"][wait]["timings"]
    ]
    lp_render_vals = [
        r["lightpanda"][wait]["timings"]["render_ms"]
        for r in results
        if r["lightpanda"][wait]["ok"] and "render_ms" in r["lightpanda"][wait]["timings"]
    ]
    cr_render_vals = [
        r["chromium"][wait]["timings"]["render_ms"]
        for r in results
        if r["chromium"][wait]["ok"] and "render_ms" in r["chromium"][wait]["timings"]
    ]

    lp_err_rate = sum(1 for r in results if not r["lightpanda"][wait]["ok"]) / total
    cr_err_rate = sum(1 for r in results if not r["chromium"][wait]["ok"]) / total

    if lp_render_vals and cr_render_vals:
        # Use render time (excludes connection overhead — that's Lightpanda's value prop)
        lp_avg_render_s = statistics.mean(lp_render_vals) / 1000
        cr_avg_render_s = statistics.mean(cr_render_vals) / 1000
        lp_p50_render_s = statistics.median(lp_render_vals) / 1000
        cr_p50_render_s = statistics.median(cr_render_vals) / 1000

        # Total session time (connect + render — what you actually pay for)
        lp_avg_session_s = (statistics.mean(lp_conn_vals) + statistics.mean(lp_render_vals)) / 1000
        cr_avg_session_s = (statistics.mean(cr_conn_vals) + statistics.mean(cr_render_vals)) / 1000

        print(
            f"\n  Avg render time (nav+content)  — LP: {lp_avg_render_s:.2f}s, CR: {cr_avg_render_s:.2f}s"
        )
        print(
            f"  P50 render time                — LP: {lp_p50_render_s:.2f}s, CR: {cr_p50_render_s:.2f}s"
        )
        print(
            f"  Avg full session (conn+render)  — LP: {lp_avg_session_s:.2f}s, CR: {cr_avg_session_s:.2f}s"
        )
        print(f"  Error rate                      — LP: {lp_err_rate:.1%}, CR: {cr_err_rate:.1%}")

        price_per_hour = 0.08

        for label, pages_day in [
            ("Current 3.6%", 36_000),
            ("10% scraper", 100_000),
            ("All browser", 1_000_000),
        ]:
            print(f"\n  --- {label} ({pages_day:,} browser pages/day) ---")
            print(f"  {'':12} {'hrs/day':>10} {'hrs/mo':>10} {'$/mo':>10} {'concurrent':>12}")
            for name, avg_s, err_rate in [
                ("Lightpanda", lp_avg_session_s, lp_err_rate),
                ("Chromium", cr_avg_session_s, cr_err_rate),
            ]:
                attempts = pages_day / (1 - err_rate) if err_rate < 1 else pages_day
                hours_day = attempts * avg_s / 3600
                hours_month = hours_day * 30
                cost_month = 19 + max(0, hours_month - 300) * price_per_hour
                concurrent = attempts / 24 / 3600 * avg_s
                print(
                    f"    {name:12} {hours_day:>10.1f} {hours_month:>10.0f} {cost_month:>10.0f} {concurrent:>12.1f}"
                )

    # Per-domain detail
    wait = "domcontentloaded"
    print(f"\n\n{'=' * 120}")
    print(f"PER-DOMAIN DETAIL (wait={wait})")
    print(f"{'=' * 120}")
    print(
        f"  {'Slug':<40} {'LP conn':>8} {'LP nav':>8} {'CR conn':>8} {'CR nav':>8} "
        f"{'Nav ratio':>10} {'LP ok':>5} {'CR ok':>5}"
    )
    print("  " + "-" * 115)
    for r in sorted(results, key=lambda x: x["slug"]):
        lp_d = r["lightpanda"][wait]
        cr_d = r["chromium"][wait]
        lp_conn = (
            f"{lp_d['timings'].get('connect_ms', 0):.0f}"
            if "connect_ms" in lp_d["timings"]
            else "—"
        )
        cr_conn = (
            f"{cr_d['timings'].get('connect_ms', 0):.0f}"
            if "connect_ms" in cr_d["timings"]
            else "—"
        )
        lp_nav = lp_d["timings"].get("navigation_ms") if lp_d["ok"] else None
        cr_nav = cr_d["timings"].get("navigation_ms") if cr_d["ok"] else None
        ratio = ""
        if lp_nav and cr_nav and lp_nav > 0:
            r_val = cr_nav / lp_nav
            ratio = f"{r_val:.1f}x"
        lp_nav_s = f"{lp_nav:.0f}" if lp_nav is not None else "ERR"
        cr_nav_s = f"{cr_nav:.0f}" if cr_nav is not None else "ERR"
        print(
            f"  {r['slug']:<40} {lp_conn:>8} {lp_nav_s:>8} {cr_conn:>8} {cr_nav_s:>8} "
            f"{ratio:>10} {'Y' if lp_d['ok'] else 'N':>5} {'Y' if cr_d['ok'] else 'N':>5}"
        )

    # Error summary
    for browser_name in ("lightpanda", "chromium"):
        errs = []
        for r in results:
            for w in WAIT_STRATEGIES:
                err = r[browser_name][w].get("error")
                if err:
                    errs.append(f"  [{w}] {r['slug']}: {err[:100]}")
        if errs:
            print(f"\n\nERRORS — {browser_name} ({len(errs)} total):")
            for e in errs:
                print(e)

    # Save
    with open("scripts/browser_experiment_v2_results.json", "w") as f:
        json.dump(results, f, indent=2)
    print("\n\nFull results saved to scripts/browser_experiment_v2_results.json")


if __name__ == "__main__":
    asyncio.run(run_experiment())
