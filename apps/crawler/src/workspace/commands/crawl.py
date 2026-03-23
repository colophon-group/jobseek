"""Crawl commands: probe, select monitor/scraper, run monitor/scraper."""

from __future__ import annotations

import asyncio
import json
import math
import random
import re
import time

import click

from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace._compat import api_monitor_types, auto_scraper_type
from src.workspace.state import (
    load_board,
    load_workspace,
    resolve_board_alias,
    resolve_slug,
    resolve_two_args,
    save_board,
    workspace_exists,
)

_SUSPICIOUS_ROUND_THRESHOLDS = {1000, 5000, 10000, 50000, 100000}


def _warn_suspicious_count(count: int, category: str) -> None:
    """Warn when a job count looks like a server-side cap or is unusually high."""
    if count <= 0:
        return
    if count in _SUSPICIOUS_ROUND_THRESHOLDS:
        out.warn(
            category,
            f"Exactly {count} jobs — likely a server-side cap (real count may be higher). "
            "Verify against the website.",
        )
    elif count >= 5000:
        out.warn(
            category,
            f"{count} jobs is unusually high — verify this isn't inflated by "
            "duplicates, test data, or missing filters. "
            "If legitimate, set max_items/max_pages with ~50% headroom for growth.",
        )


def _resolve_board(slug: str, board_alias: str | None = None):
    """Resolve board from --board flag or active_board.

    Reads workspace.yaml (for active_board fallback) but never writes it.
    Runs lightweight preflight checks and prints warnings.
    """
    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")
    ws = load_workspace(slug)

    # Preflight checks (branch mismatch etc.)
    from src.workspace.preflight import run_preflight

    for issue in run_preflight(ws):
        if issue.severity == "critical":
            out.die(f"Preflight: {issue.message}")
        else:
            out.warn("preflight", issue.message)

    alias = board_alias or ws.active_board
    if not alias:
        out.die("No active board. Provide --board or run: ws add board <alias> --url <url>")
    resolved_alias = resolve_board_alias(slug, alias)
    if resolved_alias != alias:
        out.warn("board", f"Resolved {alias!r} to alias {resolved_alias!r}")
    board = load_board(slug, resolved_alias)
    return ws, board


def _auto_config_name(board, type_: str) -> str:
    """Generate a unique config name for the given type."""
    if type_ not in board.configs:
        return type_
    n = 2
    while f"{type_}-{n}" in board.configs:
        n += 1
    return f"{type_}-{n}"


# ── Cost scoring ───────────────────────────────────────────────────────


def _estimate_monitor_cost(name: str, n_jobs: int, metadata: dict | None = None) -> float:
    """Estimate seconds per single monitor invocation (one polling cycle)."""
    if name in api_monitor_types():
        return 1.0
    if name == "api_sniffer":
        page_size = (metadata or {}).get("items", 50)
        pages = max(1, math.ceil(n_jobs / page_size))
        if (metadata or {}).get("browser"):
            return 5.0 + 0.5 * pages
        return 0.3 * pages
    if name == "sitemap":
        return 1.5
    if name in ("dom", "nextdata"):
        return 1.0
    return 2.0


#: Default per-job scraper cost assumptions by scraper type.
_SCRAPER_COST_PER_JOB: dict[str, float] = {
    "json-ld": 0.3,
    "nextdata": 0.3,
    "embedded": 0.3,
    "dom": 0.5,
    "dom_render": 4.0,
    "api_sniffer": 3.0,
}

_DEFAULT_SCRAPER_COST = 0.3  # json-ld / httpx-based


def _estimate_cycle_cost(
    monitor_cost: float, n_jobs: int, rich: bool, scraper_per_job: float = _DEFAULT_SCRAPER_COST
) -> float:
    """Estimate steady-state cost per polling cycle (monitor + amortized scraper).

    At ~3% monthly turnover, ~N/24000 new jobs appear per hour-long cycle.
    Rich monitors (API-native, api_sniffer with fields) skip the scraper entirely.

    This represents the ongoing cost, NOT the initial load.
    """
    if rich:
        return monitor_cost
    new_per_cycle = n_jobs / 24000
    return monitor_cost + scraper_per_job * new_per_cycle


def _estimate_initial_load(n_jobs: int, scraper_per_job: float = _DEFAULT_SCRAPER_COST) -> float:
    """Estimate one-time cost to scrape all existing jobs on first run.

    Only applies to URL-only monitors; rich monitors return 0.
    """
    return n_jobs * scraper_per_job


_MONITOR_PROBE_HINTS: dict[str, str] = {
    "sitemap": "Tip: may include non-job URLs \u2014 verify count, consider url_filter",
    "api_sniffer": "Tip: auto-maps fields from API response — verify field quality",
    "dom": "Tip: static detection only \u2014 try render: true if count seems low",
}


# ── Probe result processing (shared by single-board and all-boards) ───


def _score_probe_results(
    results: list[tuple[str, dict | None, str]], current_jobs: int
) -> list[tuple[str, dict | None, str, float | None, float, bool]]:
    """Compute cost scores and classify probe results.

    Returns list of (name, metadata, comment, monitor_cost, initial_load, rich).
    """
    n_jobs = current_jobs or 200
    scored: list[tuple[str, dict | None, str, float | None, float, bool]] = []
    for name, metadata, comment in results:
        if metadata is not None:
            rich = name in api_monitor_types() or (
                name == "api_sniffer" and bool((metadata or {}).get("fields"))
            )
            mon_cost = _estimate_monitor_cost(name, n_jobs, metadata)
            init_load = 0.0 if rich else _estimate_initial_load(n_jobs)
            scored.append((name, metadata, comment, mon_cost, init_load, rich))
        else:
            scored.append((name, metadata, comment, None, 0.0, False))
    return scored


def _store_detections(board, scored):
    """Store probe detections with cost estimates on board object."""
    board.detections["_meta"] = {"url": board.url}
    for name, metadata, _comment, mon_cost, init_load, rich in scored:
        if metadata is not None:
            detection = dict(metadata)
            if mon_cost is not None:
                detection["monitor_per_cycle"] = round(mon_cost, 2)
                detection["initial_load"] = round(init_load, 2)
                detection["rich"] = rich
            board.detections[name] = detection


def _process_probe_results(slug, board, results, current_jobs):
    """Score probe results, store detections on board, save, and return scored list."""
    from src.workspace.artifacts import probe_run_dir, save_probe

    probe_dir = probe_run_dir(slug, board.alias)
    save_probe(
        probe_dir,
        [
            {
                "name": name,
                "detected": metadata is not None,
                "metadata": metadata,
                "comment": comment,
            }
            for name, metadata, comment in results
        ],
    )

    scored = _score_probe_results(results, current_jobs)
    _store_detections(board, scored)

    summary_parts = []
    for name, metadata, *_ in scored:
        symbol = "\u2713" if metadata is not None else "\u2717"
        summary_parts.append(f"{name} {symbol}")
    summary = ", ".join(summary_parts)
    action_log.append_to_list(board.log, "probe monitor", True, summary)
    save_board(slug, board)
    return scored


def _print_probe_results(scored, current_jobs):
    """Print formatted probe results to terminal."""
    n_jobs = current_jobs or 200

    # Determine priority threshold
    detected_url_only_costs = [
        s for _, m, _, s, _il, r in scored if m is not None and not r and s is not None
    ]
    threshold = min(detected_url_only_costs) if detected_url_only_costs else 1.5

    high = [e for e in scored if e[1] is not None and e[3] is not None and e[3] <= threshold]
    low = [e for e in scored if e[1] is not None and (e[3] is None or e[3] > threshold)]
    undetected = [e for e in scored if e[1] is None]

    use_priority_split = current_jobs > 0 and (high or low)

    def _print_entry(name, metadata, comment, mon_cost, init_load, rich):
        symbol = "\u2713" if metadata is not None else "\u2717"
        cost_str = f"~{mon_cost:.1f}s/cycle" if mon_cost is not None else ""
        if rich:
            kind_str = "rich"
        elif metadata is not None:
            kind_str = f"URL-only (+scraper ~{init_load:.0f}s initial)" if init_load else "URL-only"
        else:
            kind_str = ""
        parts = [f"{name:<14}{symbol}  {comment}"]
        if cost_str:
            parts.append(f"  {cost_str}  {kind_str}")
        out.plain("probe", "  ".join(parts))
        if metadata is not None and name in _MONITOR_PROBE_HINTS:
            out.plain("probe", f"  {_MONITOR_PROBE_HINTS[name]}")
        if metadata is not None and name == "api_sniffer" and metadata.get("alternatives"):
            for alt in metadata["alternatives"]:
                total_str = f", total: {alt['total']}" if alt.get("total") else ""
                out.plain(
                    "probe",
                    f"  Also: {alt['items']} items, score: {alt['score']}{total_str} "
                    f"at {alt['url'][:100]}",
                )

    if use_priority_split:
        if high:
            out.plain("probe", f"-- High priority (<={threshold:.1f}s/cycle at N={n_jobs}) --")
            for entry in high:
                _print_entry(*entry)
            print()
        if low:
            out.plain("probe", f"-- Low priority (>{threshold:.1f}s/cycle at N={n_jobs}) --")
            for entry in low:
                _print_entry(*entry)
            print()
        if undetected:
            for entry in undetected:
                _print_entry(*entry)
    else:
        for entry in scored:
            _print_entry(*entry)

    # Suggest best detected monitor
    detected_scored = [(n, m, c, s, il, r) for n, m, c, s, il, r in scored if m is not None]
    if detected_scored:
        detected_scored.sort(key=lambda x: (not x[5], x[3] if x[3] is not None else float("inf")))
        best_name, best_meta, _, _, _, _ = detected_scored[0]
        best_jobs = best_meta.get("jobs", best_meta.get("urls", best_meta.get("count")))
        if best_jobs is not None and best_jobs == 0:
            out.warn(
                "probe",
                f"{best_name} detected but returned 0 jobs — verify board URL, "
                "then iterate its config before switching type. "
                "Try: ws task troubleshoot 'zero jobs'",
            )
            out.plain("probe", f"Config reference: ws help monitor {best_name}")
    else:
        out.warn(
            "probe",
            "No monitors detected. Check the board URL or try "
            "ws select monitor dom --config '{\"render\": true}'",
        )


@click.command(name="monitor")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option(
    "--current-jobs",
    "-n",
    type=int,
    required=True,
    help="Number of jobs visible on the website (check the careers page)",
)
@click.option("--all-boards", is_flag=True, default=False, help="Probe all boards sequentially")
def probe_monitors(slug: str | None, board_alias: str | None, current_jobs: int, all_boards: bool):
    """Probe all monitor types for the active board's URL."""
    slug = resolve_slug(slug)

    if all_boards:
        _probe_all_boards(slug, current_jobs)
        return

    ws, board = _resolve_board(slug, board_alias)

    async def _run():
        from playwright.async_api import async_playwright

        from src.core.monitors import probe_all_monitors
        from src.shared.http import create_http_client

        http = create_http_client()
        try:
            async with async_playwright() as pw:
                results = await probe_all_monitors(board.url, http, pw=pw)
            return results
        finally:
            await http.aclose()

    results = asyncio.run(_run())

    # Save probe artifact
    from src.workspace.artifacts import probe_run_dir, save_probe

    probe_dir = probe_run_dir(slug, board.alias)
    save_probe(
        probe_dir,
        [
            {
                "name": name,
                "detected": metadata is not None,
                "metadata": metadata,
                "comment": comment,
            }
            for name, metadata, comment in results
        ],
    )
    out.plain("artifacts", f"Saved: {probe_dir}")

    scored = _score_probe_results(results, current_jobs)

    _print_probe_results(scored, current_jobs)

    _store_detections(board, scored)

    # Log
    summary_parts = []
    for name, metadata, *_ in scored:
        symbol = "\u2713" if metadata is not None else "\u2717"
        summary_parts.append(f"{name} {symbol}")
    summary = ", ".join(summary_parts)
    action_log.append_to_list(board.log, "probe monitor", True, summary)
    save_board(slug, board)


def _probe_all_boards(slug: str, current_jobs: int) -> None:
    """Probe all boards sequentially, sharing one browser instance."""
    from src.workspace.state import list_boards as _list_boards

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    boards = _list_boards(slug)
    if not boards:
        out.die("No boards found. Add boards first: ws add boards")

    SAME_HOST_DELAY = 3.0

    async def _run():
        from playwright.async_api import async_playwright

        from src.core.monitors import probe_all_monitors
        from src.shared.http import create_http_client

        http = create_http_client()
        try:
            async with async_playwright() as pw:
                all_results = {}
                for i, board in enumerate(boards):
                    out.info(
                        "probe",
                        f"[{i + 1}/{len(boards)}] Probing {board.alias} ({board.url})",
                    )
                    try:
                        all_results[board.alias] = await probe_all_monitors(board.url, http, pw=pw)
                    except Exception as e:
                        out.warn("probe", f"  Failed: {e}")
                        all_results[board.alias] = []
                    if i < len(boards) - 1:
                        await asyncio.sleep(SAME_HOST_DELAY)
                return all_results
        finally:
            await http.aclose()

    all_results = asyncio.run(_run())

    # Process each board's results
    summary_rows: list[tuple[str, str, int, float | None, bool]] = []
    for board in boards:
        results = all_results.get(board.alias, [])
        scored = _process_probe_results(slug, board, results, current_jobs)

        # Find best detected monitor for summary
        detected = [(n, m, c, s, il, r) for n, m, c, s, il, r in scored if m is not None]
        if detected:
            detected.sort(key=lambda x: (not x[5], x[3] if x[3] is not None else float("inf")))
            best_name, best_meta, _, best_cost, _, best_rich = detected[0]
            best_items = best_meta.get("jobs", best_meta.get("urls", best_meta.get("count", 0)))
            summary_rows.append((board.alias, best_name, best_items or 0, best_cost, best_rich))
        else:
            summary_rows.append((board.alias, "(none)", 0, None, False))

    # Print summary table
    print()
    out.info("probe", f"Batch probe complete — {len(boards)} boards")
    print()
    out.plain(
        "probe",
        f"{'Board':<24} {'Monitor':<16} {'Items':>6} {'Score':>6} {'Rich':<4}",
    )
    for alias, monitor, items, cost, rich in summary_rows:
        cost_str = f"{cost:.1f}s" if cost is not None else "—"
        rich_str = "yes" if rich else "no"
        out.plain(
            "probe",
            f"{alias:<24} {monitor:<16} {items:>6} {cost_str:>6} {rich_str:<4}",
        )


@click.command(name="scraper")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option("--url", "urls", multiple=True, help="Override sample URLs")
def probe_scraper(slug: str | None, board_alias: str | None, urls: tuple[str, ...]):
    """Probe all scraper types against sample URLs."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)

    # Guard: scraper already auto-configured by monitor
    auto = board.scraper_type
    if auto and auto in ("skip",):
        out.warn(
            "scraper",
            f"Scraper auto-configured ({auto}) \u2014 no manual selection needed",
        )
        return

    # Determine sample URLs
    target_urls: list[str] = list(urls)
    if not target_urls:
        if not board.monitor_run:
            out.die("No monitor results. Run: ws run monitor (or provide --url)")
        target_urls = board.monitor_run.get("sample_urls", [])
        if not target_urls:
            out.die("No sample URLs available. Run the monitor first, or provide --url.")

    # Use all available sample URLs (up to 10, as stored by monitor run)
    target_urls = target_urls[:10]

    out.info("probe", f"Probing {len(target_urls)} sample URLs...")

    async def _run():
        from playwright.async_api import async_playwright

        from src.core.scrapers import probe_scrapers
        from src.shared.http import create_http_client

        http = create_http_client()
        try:
            async with async_playwright() as pw:
                return await probe_scrapers(target_urls, http, pw=pw)
        finally:
            await http.aclose()

    results, spa_suspect = asyncio.run(_run())

    # Save artifacts
    from src.workspace.artifacts import save_probe, scraper_probe_run_dir

    probe_dir = scraper_probe_run_dir(slug, board.alias)
    save_probe(
        probe_dir,
        [
            {
                "name": name,
                "detected": metadata is not None,
                "metadata": metadata,
                "comment": comment,
            }
            for name, metadata, comment in results
        ],
    )
    out.plain("artifacts", f"Saved: {probe_dir}")

    # SPA warning — probe fetches statically, results may be unreliable
    if spa_suspect:
        print()
        out.warn(
            "probe",
            "Pages have very little static text \u2014 site may be JS-rendered (SPA).",
        )
        out.plain(
            "probe",
            "  Probe results may be unreliable. Consider render: true.",
        )
        out.plain(
            "probe",
            "  Check page source for embedded structured data (script tags, inline JSON).",
        )
        out.plain(
            "probe",
            "  If found: ws select scraper embedded --config '{...}' (ws help scraper embedded)",
        )

    # Print results
    print()
    best_name = None
    best_meta = None

    for name, metadata, comment in results:
        if metadata is not None:
            symbol = "\u2713"
            suffix = ""
            # json-ld needs no config, others are heuristic
            if name != "json-ld":
                suffix = "  (heuristic config)"
            out.plain("probe", f"  {name:<14}{symbol}  {comment}{suffix}")

            # Print suggested config
            config = metadata.get("config", {})
            if config:
                out.plain("probe", f"    config: {json.dumps(config)}")

            # Track best
            if best_name is None:
                best_name = name
                best_meta = metadata
        else:
            symbol = "\u2717"
            out.plain("probe", f"  {name:<14}{symbol}  {comment}")

    print()

    # Quality hints
    if best_name:
        # Gate: suppress "Next:" when required fields are 0/N
        titles_ok = (best_meta or {}).get("titles", 0) > 0
        descs_ok = (best_meta or {}).get("descriptions", 0) > 0

        if not titles_ok or not descs_ok:
            missing = []
            if not titles_ok:
                missing.append("titles")
            if not descs_ok:
                missing.append("descriptions")
            out.warn(
                "probe",
                f"Best scraper has 0/N {' and '.join(missing)} "
                "\u2014 heuristic config is wrong, do not use as-is",
            )
            out.plain("probe", "  Inspect page source for embedded JSON → ws help scraper embedded")
            out.plain("probe", "  Or try manual dom config → ws help steps")
        else:
            # Heuristic warning for non-json-ld
            if best_name != "json-ld":
                out.warn("probe", "Heuristic config \u2014 verify fields, check for unmapped data")
    else:
        out.warn("probe", "No scrapers auto-detected. Try manual dom config:")
        out.plain("probe", "  ws help steps")

    # Log action
    probe_summary_parts = [
        f"{name} {'✓' if meta is not None else '✗'}" for name, meta, _ in results
    ]
    action_log.append_to_list(
        board.log,
        "probe scraper",
        True,
        ", ".join(probe_summary_parts),
    )
    save_board(slug, board)


@click.command(name="deep")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option(
    "--current-jobs",
    "-n",
    type=int,
    required=True,
    help="Number of jobs visible on the website (check the careers page)",
)
def probe_deep(slug: str | None, board_alias: str | None, current_jobs: int):
    """Playwright-based api_sniffer detection with cost scoring."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)
    n_jobs = current_jobs or 200

    async def _run():
        from urllib.parse import urljoin

        from playwright.async_api import async_playwright

        from src.core.monitors import get_can_handle
        from src.shared.http import create_http_client

        http = create_http_client()
        try:
            async with async_playwright() as pw:
                can_handle = get_can_handle("api_sniffer")
                diag: dict = {}
                metadata = await can_handle(board.url, http, pw=pw, diagnostics=diag)
                # Test plain httpx access to detected api_url
                httpx_ok = False
                if metadata and metadata.get("api_url"):
                    from src.core.monitors.api_sniffer import http_fetch

                    data = await http_fetch(
                        http,
                        metadata.get("method", "GET"),
                        metadata["api_url"],
                    )
                    httpx_ok = data is not None

                # Probe CMS candidate endpoints
                cms_results: list[dict] = []
                cms_info = diag.get("cms")
                if cms_info and not metadata:
                    from src.core.monitors.api_sniffer import http_fetch as _http_fetch

                    for candidate_path in cms_info.get("candidates", []):
                        candidate_url = urljoin(board.url, candidate_path)
                        resp = await _http_fetch(http, "GET", candidate_url)
                        if resp is not None:
                            # Count items if it's a list
                            item_count = len(resp) if isinstance(resp, list) else None
                            cms_results.append(
                                {
                                    "url": candidate_url,
                                    "items": item_count,
                                    "type": type(resp).__name__,
                                }
                            )

                return metadata, httpx_ok, diag, cms_results
        finally:
            await http.aclose()

    metadata, httpx_ok, diagnostics, cms_results = asyncio.run(_run())

    # Save artifact
    from src.workspace.artifacts import deep_probe_run_dir, save_probe

    probe_dir = deep_probe_run_dir(slug, board.alias)
    save_probe(
        probe_dir,
        [
            {
                "name": "api_sniffer",
                "detected": metadata is not None,
                "metadata": metadata,
                "diagnostics": diagnostics,
                "cms_results": cms_results,
            }
        ],
    )
    out.plain("artifacts", f"Saved: {probe_dir}")

    if metadata:
        rich = bool(metadata.get("fields"))
        mon_pw = _estimate_monitor_cost(
            "api_sniffer",
            n_jobs,
            {**metadata, "browser": True},
        )
        mon_httpx = (
            _estimate_monitor_cost(
                "api_sniffer",
                n_jobs,
                {**metadata, "browser": False},
            )
            if httpx_ok
            else None
        )
        init_load = 0.0 if rich else _estimate_initial_load(n_jobs)

        rich_str = "rich" if rich else f"URL-only (+scraper ~{init_load:.0f}s initial)"
        items = metadata.get("items", "?")
        out.info(
            "deep",
            f"api_sniffer   OK  {items} items  ~{mon_pw:.1f}s/cycle (PW)  {rich_str}",
        )
        if isinstance(items, int):
            _warn_suspicious_count(items, "deep")
        out.plain("deep", f"  api_url: {metadata.get('api_url', '?')}")
        if metadata.get("fields"):
            fields = (
                ", ".join(metadata["fields"].keys())
                if isinstance(metadata["fields"], dict)
                else str(metadata["fields"])
            )
            out.plain("deep", f"  fields: {fields}")
        if httpx_ok and mon_httpx is not None:
            out.plain(
                "deep",
                f"  httpx test: OK accessible (~{mon_httpx:.1f}s/cycle without Playwright)",
            )
        elif httpx_ok is False:
            out.plain("deep", "  httpx test: failed (Playwright required)")

        # Store in detections with clear cost breakdown
        detection = dict(metadata)
        detection["monitor_per_cycle_pw"] = round(mon_pw, 2)
        if mon_httpx is not None:
            detection["monitor_per_cycle_httpx"] = round(mon_httpx, 2)
        detection["initial_load"] = round(init_load, 2)
        detection["httpx_accessible"] = httpx_ok
        detection["rich"] = rich
        board.detections["api_sniffer"] = detection
        items = metadata.get("items", "?")
        action_log.append_to_list(
            board.log,
            "probe deep",
            True,
            f"api_sniffer detected, {items} items",
        )
        save_board(slug, board)
    else:
        out.warn(
            "deep",
            "api_sniffer not detected — no XHR/fetch API found. "
            "Try: ws task troubleshoot 'zero jobs'",
        )

        # Show captured exchanges for debugging
        exchanges = diagnostics.get("exchanges", [])
        if exchanges:
            out.plain("deep", "")
            out.plain("deep", f"Captured {len(exchanges)} XHR/fetch exchange(s):")
            out.table(
                ["Method", "Status", "Phase", "Arrays", "Items", "URL"],
                [
                    [
                        ex["method"],
                        str(ex["status"]),
                        ex["phase"],
                        str(ex["arrays"]),
                        str(ex["best_items"]),
                        ex["url"][:80],
                    ]
                    for ex in exchanges
                ],
            )
        else:
            out.plain("deep", "No XHR/fetch exchanges captured during page load.")

        # Show script URL discoveries
        script_urls = diagnostics.get("script_urls", [])
        if script_urls:
            out.plain("deep", "")
            out.plain("deep", f"Found {len(script_urls)} API URL(s) in page scripts:")
            for su in script_urls:
                out.plain("deep", f"  {su['url']}")
                out.plain("deep", f"    context: {su['context']}")
            out.plain("deep", "")

        # Show CMS detection and probe results
        cms_info = diagnostics.get("cms")
        if cms_info:
            out.plain("deep", "")
            out.plain("deep", f"CMS detected: {cms_info['cms']}")
            if cms_results:
                out.plain("deep", f"  {len(cms_results)} endpoint(s) responded:")
                out.table(
                    ["URL", "Type", "Items"],
                    [
                        [
                            cr["url"],
                            cr["type"],
                            str(cr["items"]) if cr["items"] is not None else "?",
                        ]
                        for cr in cms_results
                    ],
                )
            else:
                out.plain("deep", "  No candidate endpoints responded.")

        action_log.append_to_list(board.log, "probe deep", False, "api_sniffer not detected")
        save_board(slug, board)


@click.command(name="api")
@click.argument("url")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
def probe_api(url: str, slug: str | None, board_alias: str | None):
    """Fetch and analyze an API endpoint for api_sniffer configuration."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)

    async def _run():
        from src.shared.http import create_http_client

        http = create_http_client()
        try:
            resp = await http.get(url, timeout=30)
            return resp
        finally:
            await http.aclose()

    resp = asyncio.run(_run())

    # Save artifact
    from src.workspace.artifacts import api_probe_run_dir

    probe_dir = api_probe_run_dir(slug, board.alias)
    content_type = resp.headers.get("content-type", "")

    if "json" in content_type:
        data = resp.json()
        (probe_dir / "response.json").write_text(json.dumps(data, indent=2, default=str))
        ct = content_type.split(";")[0]
        out.info(
            "probe",
            f"Fetched {resp.status_code} ({ct}, {len(resp.content):,} bytes)",
        )

        # Analyze JSON structure
        from src.shared.api_sniff import find_arrays, find_total_count, find_url_field

        arrays = find_arrays(data)

        if arrays:
            from src.core.monitors.api_sniffer import (
                find_html_strings,
                pick_best_array,
            )

            best_path, best_items = pick_best_array(arrays, url)
            url_field = find_url_field(best_items)
            total = find_total_count(data, best_path) or len(best_items)
            html_hits = find_html_strings(best_items[0]) if best_items else []

            print()
            out.plain("probe", f"Best array: {best_path} ({len(best_items)} items)")
            if best_items:
                sample = best_items[0]
                out.plain("probe", f"Item keys: {', '.join(list(sample.keys())[:15])}")
            if url_field:
                out.plain("probe", f"URL field: {url_field}")
            out.plain("probe", f"Total count: {total}")
            if html_hits:
                out.plain("probe", f"HTML fields: {', '.join(p for p, _ in html_hits[:5])}")

            # Suggest config
            print()
            suggested: dict = {
                "api_url": url,
                "json_path": best_path,
            }
            if url_field:
                suggested["url_field"] = url_field

            out.plain("probe", f"Suggested config: {json.dumps(suggested)}")
        else:
            out.warn("probe", "No arrays found in JSON response")
    else:
        (probe_dir / "response.html").write_bytes(resp.content)
        ct = content_type.split(";")[0]
        out.info(
            "probe",
            f"Fetched {resp.status_code} ({ct}, {len(resp.content):,} bytes)",
        )
        out.plain(
            "probe",
            "HTML response — scan for embedded API endpoints manually",
        )
        out.plain("probe", f"Saved to: {probe_dir / 'response.html'}")

    out.plain("artifacts", f"Saved: {probe_dir}")
    action_log.append_to_list(board.log, "probe api", True, f"Analyzed {url}")
    save_board(slug, board)


_MONITOR_CONFIG_HINTS = {
    "join": "Optional: slug (auto-derived from join.com URL), description_path(s)",
    "ashby": "Requires: token (auto-filled from probe)",
    "bite": "Requires: key (auto-filled from probe)",
    "breezy": "Optional: portal_url or slug (auto-filled from probe)",
    "dvinci": "Requires: slug (auto-filled from probe)",
    "greenhouse": "Requires: token (auto-filled from probe)",
    "hireology": "Requires: slug (auto-filled from probe)",
    "lever": "Requires: token (auto-filled from probe)",
    "pinpoint": "Requires: slug (auto-filled from probe)",
    "recruitee": "Requires: slug or api_base (auto-filled from probe)",
    "rippling": "Requires: slug (auto-filled from probe)",
    "smartrecruiters": "Requires: token (auto-filled from probe)",
    "softgarden": "Requires: slug. Optional: job_url_pattern",
    "traffit": "Requires: slug (auto-filled from probe)",
    "workable": "Requires: token (auto-filled from probe)",
    "workday": "Requires: company, wd_instance, site (auto-filled from probe)",
    "personio": "Requires: slug. Optional: language, backfill_languages",
    "rss": "Optional: preset, feed_url (auto-filled from probe)",
    "umantis": "Requires: customer_id. Optional: region, listing_path",
    "sitemap": "Optional: sitemap_url, url_filter (regex to include/exclude URLs)",
    "nextdata": "Requires: path, url_template. Optional: fields, render, actions, url_filter",
    "dom": "Optional: render, actions, wait, timeout, url_filter",
    "api_sniffer": "Auto-filled from probe: api_url, method, json_path, fields, pagination",
}

_SCRAPER_CONFIG_HINTS = {
    "json-ld": "Optional: render, actions, wait, timeout",
    "dom": "Requires: steps[]. Optional: render, actions, wait, timeout",
    "nextdata": "Requires: fields. Optional: path, render, actions",
    "embedded": "Requires: fields + one of: script_id/pattern/variable. Optional: path, render",
    "api_sniffer": "Optional: fields (auto-maps if omitted). Requires Playwright.",
}


@click.command(name="monitor")
@click.argument("slug_or_type")
@click.argument("type_", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option("--as", "config_name", default=None, help="Name for this configuration")
@click.option("--config", "config_json", help="Monitor config JSON")
def select_monitor(
    slug_or_type: str,
    type_: str | None,
    board_alias: str | None,
    config_name: str | None,
    config_json: str | None,
):
    """Set monitor type for the active board."""
    slug, type_ = resolve_two_args(slug_or_type, type_)
    ws, board = _resolve_board(slug, board_alias)
    prev_monitor_type = board.monitor_type

    # Stale probe detection: warn if board URL changed since probe ran
    probe_meta = board.detections.get("_meta", {})
    if probe_meta and probe_meta.get("url") and probe_meta["url"] != board.url:
        out.warn(
            "monitor",
            f"Probe was run against {probe_meta['url']!r} "
            f"but board URL is now {board.url!r} — re-probe recommended",
        )

    # Validate type against registry
    from src.core.monitors import get_discoverer

    try:
        get_discoverer(type_)
    except ValueError as e:
        out.die(str(e))

    # Nudge config-first exploration when switching monitor types.
    if prev_monitor_type and prev_monitor_type != type_:
        out.warn(
            "monitor",
            f"Switching monitor type: {prev_monitor_type} -> {type_}. "
            "Prefer iterating config on the current type first unless this is a hard mismatch.",
        )
        out.plain("monitor", f"Config reference: ws help monitor {prev_monitor_type}")
        out.plain(
            "monitor",
            "Try: ws select monitor "
            f"{prev_monitor_type} --as {prev_monitor_type}-alt --config '{{...}}'",
        )

    config = {}
    if config_json:
        config = json.loads(config_json)
    elif type_ in board.detections:
        # Auto-fill from probe detections
        config = dict(board.detections[type_])
        out.info("monitor", f"Auto-filled config from probe: {json.dumps(config)}")
    elif board.monitor_config and "_probe" in board.monitor_config:
        # Backward compat: old-style probe data in monitor_config
        probe_data = board.monitor_config["_probe"]
        if type_ in probe_data:
            config = {k: v for k, v in probe_data[type_].items()}
            out.info("monitor", f"Auto-filled config from probe: {json.dumps(config)}")

    # Validate pagination config schema if present
    if "pagination" in config:
        _VALID_PAG_KEYS = {
            "param_name",
            "style",
            "start",
            "start_value",
            "increment",
            "location",
            "max_pages",
            "page_size",
            "browser",
        }
        pag_cfg = config["pagination"]
        if isinstance(pag_cfg, dict):
            unknown = set(pag_cfg.keys()) - _VALID_PAG_KEYS
            if unknown:
                # Check for common aliases and suggest corrections
                _PAG_ALIASES = {"param": "param_name", "type": "style", "page_size": "increment"}
                suggestions = []
                for k in unknown:
                    if k in _PAG_ALIASES:
                        suggestions.append(f"'{k}' -> '{_PAG_ALIASES[k]}'")
                msg = f"Unknown pagination key(s): {', '.join(sorted(unknown))}"
                if suggestions:
                    msg += f". Did you mean: {', '.join(suggestions)}?"
                out.die(msg)
            if "param_name" not in pag_cfg:
                out.die("Pagination config requires 'param_name'. See: ws help monitor api_sniffer")

    # Clean up probe/internal data from config
    _internal_keys = {
        "_probe",
        "monitor_per_cycle",
        "initial_load",
        "cost_est",
        "cost_est_pw",
        "cost_est_httpx",
        "rich",
        "httpx_accessible",
        "jobs",
        "urls",
        "count",
        "coverage",
        "domain",
    }
    clean_config = {k: v for k, v in config.items() if k not in _internal_keys}

    # Generate or use provided config name
    name = config_name or _auto_config_name(board, type_)

    # Create named config entry with cost estimate
    mon_est = _estimate_monitor_cost(type_, 200, clean_config)
    auto = auto_scraper_type(type_, clean_config)
    init_load = 0.0 if auto else _estimate_initial_load(200)
    cfg_entry: dict = {
        "monitor_type": type_,
        "monitor_config": clean_config,
        "status": "selected",
        "cost": {
            "monitor_per_cycle": round(mon_est, 2),
            "initial_load": round(init_load, 2),
        },
    }
    # Auto-configure scraper when the monitor determines it
    if auto:
        cfg_entry["scraper_type"] = auto[0]
    board.configs[name] = cfg_entry
    board.active_config = name

    action_log.append_to_list(
        board.log,
        "select monitor",
        True,
        f"Selected monitor: {type_} (as {name!r})",
    )
    save_board(slug, board)

    out.info("monitor", f"Selected monitor: {type_} (as {name!r})")
    if clean_config:
        out.plain("monitor", f"Config: {json.dumps(clean_config)}")
    elif type_ in _MONITOR_CONFIG_HINTS:
        out.plain("monitor", f"Config: {_MONITOR_CONFIG_HINTS[type_]}")
    if auto:
        out.info("monitor", f"Scraper auto-configured: {auto[0]}")


# Fields checked in quality reports for DiscoveredJob (monitor rich data)
_MONITOR_QUALITY_FIELDS = [
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "base_salary",
    "skills",
    "responsibilities",
    "qualifications",
]

# Fields checked in quality reports for JobContent (scraper extraction)
_SCRAPER_QUALITY_FIELDS = [
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "valid_through",
    "base_salary",
    "skills",
    "responsibilities",
    "qualifications",
]

# Core fields always shown in terminal output
_CORE_FIELDS = ("title", "description", "locations")

# Fields that live inside the ``extras`` dict rather than as direct attributes
_EXTRAS_FIELDS = {"skills", "responsibilities", "qualifications", "valid_through"}


def _get_field(obj: object, field: str) -> object:
    """Get a quality/display field value from a job object.

    For fields in ``_EXTRAS_FIELDS``, looks inside the ``extras`` dict.
    """
    if field in _EXTRAS_FIELDS:
        extras = getattr(obj, "extras", None)
        if isinstance(extras, dict):
            return extras.get(field)
        return None
    return getattr(obj, field, None)


@click.command(name="monitor")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option("--config", "config_name", default=None, help="Named config to run (default: active)")
def run_monitor(slug: str | None, board_alias: str | None, config_name: str | None):
    """Test-crawl the active board with its selected monitor."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)

    # Resolve which config to use
    if config_name:
        if config_name not in board.configs:
            out.die(
                f"Config {config_name!r} not found. "
                f"Available: {', '.join(board.configs) or '(none)'}"
            )
        _target_cfg = board.configs[config_name]
        mon_type = _target_cfg.get("monitor_type")
        mon_config = _target_cfg.get("monitor_config") or {}
        if not mon_type:
            out.die(f"Config {config_name!r} has no monitor_type")
    else:
        mon_type = board.monitor_type
        mon_config = board.monitor_config
        _target_cfg = None  # will use board._ensure_cfg() for active

    if not mon_type:
        out.die("No monitor selected. Run: ws select monitor <type>")

    # Create artifact directory before the run so monitor_one can write raw data
    from src.workspace.artifacts import (
        capture_structlog,
        monitor_run_dir,
        save_events,
        save_http_log,
        save_jobs,
        save_quality,
    )

    run_dir = monitor_run_dir(slug, board.alias)
    log_events = capture_structlog()

    async def _run():
        from playwright.async_api import async_playwright

        from src.core.monitor import monitor_one
        from src.shared.http import create_logging_http_client

        http, http_log = create_logging_http_client()
        try:
            async with async_playwright() as pw:
                start = time.monotonic()
                result = await monitor_one(
                    board.url,
                    mon_type,
                    mon_config or None,
                    http,
                    artifact_dir=run_dir,
                    pw=pw,
                )
                elapsed = time.monotonic() - start
            return result, elapsed, http_log
        finally:
            await http.aclose()

    try:
        result, elapsed, http_log = asyncio.run(_run())
    except Exception as exc:
        detail = str(exc).strip() or exc.__class__.__name__
        out.error("monitor", f"Run failed: {detail}")
        out.plain("monitor", f"Monitor: {mon_type} | Board: {board.url}")
        out.plain("monitor", "Try:")
        out.plain("monitor", "  ws probe monitor -n <current-job-count>")
        out.plain("monitor", f"  ws help monitor {mon_type}")

        if "Cannot derive" in detail and ("token" in detail.lower() or "slug" in detail.lower()):
            key = "token" if "token" in detail.lower() else "slug"
            out.plain(
                "monitor",
                f'  ws select monitor {mon_type} --config \'{{"{key}": "..."}}\'',
            )

        out.plain("monitor", "  ws task troubleshoot 'monitor failed'")
        raise SystemExit(1) from exc

    # Save HTTP log and structlog events
    save_http_log(run_dir, http_log)
    save_events(run_dir, log_events)

    job_count = len(result.urls)
    has_rich = result.jobs_by_url is not None

    # Resolve the config dict for write-back
    cfg = _target_cfg if _target_cfg is not None else board._ensure_cfg()

    # Regression detection: previous run had jobs, now 0
    prev_jobs = (cfg.get("run") or {}).get("jobs", 0)

    # Store description samples for quality gate validation (rich monitors)
    desc_samples: list[dict] = []
    if result.jobs_by_url:
        for job in list(result.jobs_by_url.values())[:5]:
            desc = getattr(job, "description", None)
            if desc:
                plain = re.sub(r"<[^>]+>", "", desc).strip()
                desc_samples.append({"length": len(plain), "snippet": plain[:200]})

    # Store results
    run_data: dict = {
        "jobs": job_count,
        "time": round(elapsed, 1),
        "has_rich_data": has_rich,
        "sample_urls": random.sample(sorted(result.urls), min(10, len(result.urls))),
        "ran_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    if desc_samples:
        run_data["description_samples"] = desc_samples
    cfg["run"] = run_data

    # Mark config as tested and record measured cost
    cfg["status"] = "tested"
    cost = cfg.get("cost") or {}
    cost["monitor_per_cycle"] = round(elapsed, 2)
    cfg["cost"] = cost

    # Save processed job data (raw data already saved by monitor_one)
    jobs_data = []
    if result.jobs_by_url:
        from dataclasses import asdict

        jobs_data = [asdict(j) for j in result.jobs_by_url.values()]
    else:
        jobs_data = [{"url": u} for u in list(result.urls)[:100]]

    save_jobs(run_dir, jobs_data)

    # Build and save quality report for rich data
    quality: dict | None = None
    if result.jobs_by_url:
        jobs = list(result.jobs_by_url.values())
        total = len(jobs)
        quality = {"total": total, "fields": {}}
        for field in _MONITOR_QUALITY_FIELDS:
            count = sum(1 for j in jobs if _get_field(j, field))
            pct = round(count / total * 100) if total else 0
            quality["fields"][field] = {"count": count, "pct": pct}
        save_quality(run_dir, quality)
        run_data["quality"] = {f: v["count"] for f, v in quality["fields"].items()}

    out.plain("artifacts", f"Saved: {run_dir}")

    save_board(slug, board)

    # Print results
    if result.filtered_count:
        out.plain("monitor", f"URL filter: {result.filtered_count} URLs removed")

    if job_count == 0:
        out.warn(
            "monitor",
            f"0 jobs in {elapsed:.1f}s \u2014 check board URL, "
            "then iterate monitor config before switching type. "
            "Try: ws task troubleshoot 'zero jobs'",
        )
        if mon_type:
            out.plain("monitor", f"Config reference: ws help monitor {mon_type}")
            out.plain(
                "monitor",
                f"Try: ws select monitor {mon_type} --as {mon_type}-alt --config '{{...}}'",
            )
        if prev_jobs > 0:
            out.warn(
                "monitor",
                f"Regression: previous run found {prev_jobs} jobs, now 0 — board may have changed",
            )
    else:
        out.info("monitor", f"{job_count} jobs in {elapsed:.1f}s")

    # Surface pagination warnings from structlog events
    for ev in log_events:
        if ev.get("event") == "api_sniff.page_size_mismatch":
            out.warn(
                "monitor",
                f"API returns {ev['actual_page_size']} items/page but pagination increment "
                f"is {ev['configured_increment']} — skipping jobs! "
                f"Fix: set increment to {ev['actual_page_size']}",
            )
        elif ev.get("event") == "api_sniff.pagination_gap":
            out.warn(
                "monitor",
                f"API reports {ev['total_count']} total but only {ev['discovered']} discovered "
                "— likely pagination misconfiguration",
            )

    # Suspicious count warnings
    _warn_suspicious_count(job_count, "monitor")

    # Count verification prompt — completeness matters most
    if job_count > 0:
        out.plain(
            "monitor",
            "Verify: compare this count against the website's displayed job total",
        )

    # url_filter tip for sitemap monitors without a filter
    if mon_type == "sitemap" and job_count > 0 and not (mon_config or {}).get("url_filter"):
        sample_urls = sorted(result.urls)[:20]
        if sample_urls:
            from os.path import commonprefix
            from urllib.parse import urlparse

            paths = [urlparse(u).path for u in sample_urls]
            prefix = commonprefix(paths)
            # Trim to last full segment
            if prefix and "/" in prefix:
                prefix = prefix[: prefix.rindex("/") + 1]
            if prefix and prefix != "/" and len(prefix) >= 3:
                matching = sum(1 for u in sample_urls if urlparse(u).path.startswith(prefix))
                if matching / len(sample_urls) >= 0.8:
                    out.plain(
                        "monitor",
                        f"Tip: {matching}/{len(sample_urls)} sample URLs share prefix "
                        f'"{prefix}" \u2014 consider url_filter for reliability',
                    )

    if has_rich:
        out.plain("monitor", "Rich data: yes (titles, descriptions)")
        # Quality summary for rich data
        if quality:
            total = quality["total"]
            fields = quality["fields"]
            # Core fields on one line
            core_parts = [f"{fields[f]['count']}/{total} {f}" for f in _CORE_FIELDS]
            out.plain("monitor", f"Quality: {', '.join(core_parts)}")
            # Optional fields that have any data
            optional_parts = [
                f"{fields[f]['count']}/{total} {f}"
                for f in _MONITOR_QUALITY_FIELDS
                if f not in _CORE_FIELDS and fields[f]["count"] > 0
            ]
            if optional_parts:
                out.plain("monitor", f"Optional: {', '.join(optional_parts)}")
        # Auto-configure scraper based on description coverage
        desc_count = quality["fields"].get("description", {}).get("count", 0) if quality else 0
        desc_pct = (desc_count / quality["total"] * 100) if quality and quality["total"] else 0
        if cfg.get("scraper_type") == "skip" and desc_pct < 80 and quality and quality["total"] > 0:
            # Rich monitor auto-skipped scraper at select time, but
            # descriptions are insufficient — override to allow scraper
            # selection so detail pages can fill in descriptions.
            cfg.pop("scraper_type", None)
            save_board(slug, board)
            if desc_pct > 0:
                out.warn(
                    "monitor",
                    f"Only {desc_count}/{quality['total']} jobs have descriptions"
                    " — scraper auto-skip overridden",
                )
            else:
                out.warn(
                    "monitor",
                    "Monitor returned structured data but no descriptions"
                    " — scraper auto-skip overridden",
                )
        elif cfg.get("scraper_type"):
            # Already auto-configured at select time
            out.plain(
                "monitor",
                f"Scraper: auto-configured ({cfg['scraper_type']})",
            )
        elif desc_pct >= 80:
            # Dynamically rich — auto-configure skip
            cfg["scraper_type"] = "skip"
            out.plain("monitor", "Scraper: auto-configured (skip)")
            save_board(slug, board)
        elif has_rich and desc_pct > 0:
            out.warn(
                "monitor",
                f"Only {desc_count}/{quality['total']} jobs have descriptions — scraper needed",
            )
        elif has_rich:
            out.warn(
                "monitor",
                "Monitor returned structured data but no descriptions — scraper needed",
            )
    else:
        if cfg.get("scraper_type"):
            out.plain(
                "monitor",
                f"Scraper: auto-configured ({cfg['scraper_type']})",
            )
        else:
            out.plain("monitor", "Scraper: manual selection needed")

    if result.urls:
        sample = next(iter(result.urls))
        out.plain("monitor", f"Sample: {sample}")

    # Content samples for rich monitors — show actual values for quality verification
    if result.jobs_by_url:
        sample_jobs = list(result.jobs_by_url.values())[:5]
        _SAMPLE_FIELDS = [
            "title",
            "locations",
            "description",
            "employment_type",
            "job_location_type",
            "date_posted",
            "base_salary",
            "qualifications",
            "responsibilities",
            "skills",
            "metadata",
        ]
        print()
        out.plain("monitor", "Extracted content:")
        for field_name in _SAMPLE_FIELDS:
            values = []
            for job in sample_jobs:
                val = _get_field(job, field_name)
                if val is None:
                    values.append("\u2014")
                elif isinstance(val, str):
                    if len(val) > 120:
                        values.append(val[:60] + " \u2026 " + val[-40:])
                    else:
                        values.append(val)
                elif isinstance(val, list):
                    values.append(", ".join(str(v)[:60] for v in val[:5]))
                elif isinstance(val, dict):
                    values.append(json.dumps(val)[:120])
                else:
                    values.append(str(val)[:120])

            if all(v == "\u2014" for v in values):
                continue
            out.plain("monitor", f"  {field_name}:")
            for i, v in enumerate(values):
                out.plain("monitor", f"    [{i}] {v}")

    action_log.append_to_list(
        board.log,
        "run monitor",
        True,
        f"{job_count} jobs in {elapsed:.1f}s ({'rich data' if has_rich else 'URLs only'})",
    )
    save_board(slug, board)


@click.command(name="scraper")
@click.argument("slug_or_type")
@click.argument("type_", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option("--config", "config_json", help="Scraper config JSON")
def select_scraper(
    slug_or_type: str,
    type_: str | None,
    board_alias: str | None,
    config_json: str | None,
):
    """Set scraper type for the active board."""
    slug, type_ = resolve_two_args(slug_or_type, type_)
    ws, board = _resolve_board(slug, board_alias)
    prev_scraper_type = board.scraper_type

    # Validate type against registry
    from src.core.scrapers import get_scraper

    try:
        get_scraper(type_)
    except ValueError as e:
        out.die(str(e))

    # Nudge config-first exploration when switching scraper types.
    if prev_scraper_type and prev_scraper_type != type_:
        out.warn(
            "scraper",
            f"Switching scraper type: {prev_scraper_type} -> {type_}. "
            "Prefer iterating config on the current type first unless this is a hard mismatch.",
        )
        out.plain("scraper", f"Config reference: ws help scraper {prev_scraper_type}")
        out.plain(
            "scraper",
            f"Try: ws select scraper {prev_scraper_type} --config '{{...}}'",
        )

    config = json.loads(config_json) if config_json else {}

    board.scraper_type = type_
    board.scraper_config = config

    save_board(slug, board)

    action_log.append_to_list(board.log, "select scraper", True, f"Selected scraper: {type_}")
    save_board(slug, board)

    out.info("scraper", f"Selected scraper: {type_}")
    if config:
        out.plain("scraper", f"Config: {json.dumps(config)}")
    elif type_ in _SCRAPER_CONFIG_HINTS:
        out.plain("scraper", f"Config: {_SCRAPER_CONFIG_HINTS[type_]}")


@click.command(name="scraper")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option("--url", "urls", multiple=True, help="Specific URLs to scrape (repeatable)")
@click.option("--config", "config_name", default=None, help="Named config to run (default: active)")
def run_scraper(
    slug: str | None, board_alias: str | None, urls: tuple[str, ...], config_name: str | None
):
    """Test-scrape sample job pages from the active board."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)

    # Resolve which config to use
    if config_name:
        if config_name not in board.configs:
            out.die(
                f"Config {config_name!r} not found. "
                f"Available: {', '.join(board.configs) or '(none)'}"
            )
        _target_cfg = board.configs[config_name]
        scr_type = _target_cfg.get("scraper_type")
        scr_config = _target_cfg.get("scraper_config") or {}
        if not scr_type:
            out.die(f"Config {config_name!r} has no scraper_type")
        sample_urls_source = (_target_cfg.get("run") or {}).get("sample_urls", [])
    else:
        scr_type = board.scraper_type
        scr_config = board.scraper_config
        sample_urls_source = board.monitor_run.get("sample_urls", [])
        _target_cfg = None

    if not scr_type:
        out.die("No scraper selected. Run: ws select scraper <type>")

    # Determine which URLs to scrape
    target_urls: list[str] = list(urls)
    if not target_urls:
        target_urls = sample_urls_source
        if not target_urls:
            out.die("No URLs available. Run the monitor first, or provide --url.")

    # Create artifact directory before the run so scrape_one can write raw HTML
    from src.workspace.artifacts import (
        capture_structlog,
        save_events,
        save_http_log,
        save_quality,
        save_results,
        scraper_run_dir,
    )

    run_dir = scraper_run_dir(slug, board.alias)
    log_events = capture_structlog()

    async def _run():
        from httpx import HTTPStatusError
        from playwright.async_api import async_playwright

        from src.core.scrape import scrape_one
        from src.shared.http import create_logging_http_client

        http, http_log = create_logging_http_client()
        results = []
        skipped: list[tuple[str, str]] = []
        try:
            async with async_playwright() as pw:
                for i, url in enumerate(target_urls):
                    job_id = f"sample-{i}"
                    start = time.monotonic()
                    try:
                        content = await scrape_one(
                            url,
                            scr_type,
                            scr_config or None,
                            http,
                            artifact_dir=run_dir,
                            job_id=job_id,
                            pw=pw,
                        )
                    except HTTPStatusError as exc:
                        skipped.append((url, str(exc.response.status_code)))
                        continue
                    elapsed = time.monotonic() - start
                    results.append((url, content, elapsed))
            return results, http_log, skipped
        finally:
            await http.aclose()

    results, http_log, skipped = asyncio.run(_run())

    # Save HTTP log and structlog events
    save_http_log(run_dir, http_log)
    save_events(run_dir, log_events)

    # Aggregate stats
    titles_found = sum(1 for _, c, _ in results if c.title)
    locations_found = sum(1 for _, c, _ in results if c.locations)
    descs_found = sum(1 for _, c, _ in results if c.description)
    times = [e for _, _, e in results]
    avg_time = sum(times) / len(times) if times else 0

    # Store description samples for quality gate validation
    desc_samples = []
    for _, content, _ in results:
        if content.description and len(desc_samples) < 5:
            plain = re.sub(r"<[^>]+>", "", content.description).strip()
            desc_samples.append({"length": len(plain), "snippet": plain[:200]})

    scraper_run_data = {
        "count": len(results),
        "avg_time": round(avg_time, 1),
        "titles": titles_found,
        "descriptions": descs_found,
        "locations": locations_found,
        "description_samples": desc_samples,
        "ran_at": __import__("datetime")
        .datetime.now(__import__("datetime").timezone.utc)
        .strftime("%Y-%m-%dT%H:%M:%SZ"),
    }

    # Resolve the config dict for write-back
    cfg = _target_cfg if _target_cfg is not None else board._ensure_cfg()

    cfg["scraper_run"] = scraper_run_data
    cfg["status"] = "tested"
    cost = cfg.get("cost") or {}
    cost["scraper_per_job"] = round(avg_time, 2)
    # Update initial load estimate using measured per-job time
    mon_run = cfg.get("run") or board.monitor_run or {}
    n_jobs = mon_run.get("jobs", 200)
    cost["initial_load"] = round(_estimate_initial_load(n_jobs, avg_time), 2)
    cfg["cost"] = cost

    # Save extracted job content (raw HTML already saved by scrape_one)
    from dataclasses import asdict

    result_dicts = []
    for i, (url, content, _) in enumerate(results):
        d = asdict(content)
        d["id"] = f"sample-{i}"
        d["url"] = url
        result_dicts.append(d)

    save_results(run_dir, result_dicts)

    # Build and save per-URL quality report
    total = len(results)
    quality_per_url = []
    quality_totals: dict[str, int] = {f: 0 for f in _SCRAPER_QUALITY_FIELDS}
    for url, content, _ in results:
        url_fields = {}
        for field in _SCRAPER_QUALITY_FIELDS:
            present = bool(_get_field(content, field))
            url_fields[field] = present
            if present:
                quality_totals[field] += 1
        quality_per_url.append({"url": url, "fields": url_fields})

    quality = {
        "total": total,
        "fields": {
            f: {
                "count": quality_totals[f],
                "pct": round(quality_totals[f] / total * 100) if total else 0,
            }
            for f in _SCRAPER_QUALITY_FIELDS
        },
        "per_url": quality_per_url,
    }
    save_quality(run_dir, quality)
    board.scraper_run["quality"] = {f: v for f, v in quality_totals.items()}
    board.scraper_run["count"] = total

    out.plain("artifacts", f"Saved: {run_dir}")

    save_board(slug, board)

    # Print results table
    print()
    out.table(
        ["URL", "Title", "Location", "Desc", "Time"],
        [
            [
                url.split("/")[-1][:30] or url[:30],
                (c.title or "")[:30],
                (", ".join(c.locations) if c.locations else "")[:20],
                "yes" if c.description else "",
                f"{e:.1f}s",
            ]
            for url, c, e in results
        ],
    )
    if skipped:
        print()
        out.warn("scraper", f"{len(skipped)} URL(s) skipped due to errors:")
        for skip_url, skip_err in skipped:
            short_err = skip_err.split("\n")[0][:80]
            out.plain("scraper", f"  {skip_url} — {short_err}")
    print()

    # Report skipped URLs
    # Core stats
    out.info(
        "scraper",
        f"{total} pages, {titles_found}/{total} titles, "
        f"{descs_found}/{total} descriptions, "
        f"{locations_found}/{total} locations, avg {avg_time:.1f}s",
    )
    # Optional fields that have any data
    optional_parts = [
        f"{quality_totals[f]}/{total} {f}"
        for f in _SCRAPER_QUALITY_FIELDS
        if f not in _CORE_FIELDS and quality_totals[f] > 0
    ]
    if optional_parts:
        out.plain("scraper", f"Optional: {', '.join(optional_parts)}")

    # Show missing important fields to prompt optimization
    _IMPORTANT_OPTIONAL = ("job_location_type", "employment_type", "date_posted")
    missing_important = [f for f in _IMPORTANT_OPTIONAL if quality_totals.get(f, 0) == 0]
    if missing_important:
        missing = ", ".join(missing_important)
        out.plain("scraper", f"Missing: {missing} \u2014 check raw data for mappable fields")

    # Content samples grouped by field — show actual values for quality verification
    _SAMPLE_FIELDS = [
        "title",
        "locations",
        "description",
        "employment_type",
        "job_location_type",
        "date_posted",
        "valid_through",
        "qualifications",
        "responsibilities",
        "skills",
        "metadata",
    ]

    if results:
        print()
        out.plain("scraper", "Extracted content:")
        for field_name in _SAMPLE_FIELDS:
            values = []
            for _, content, _ in results:
                val = _get_field(content, field_name)
                if val is None:
                    values.append("\u2014")
                elif isinstance(val, str):
                    if len(val) > 120:
                        values.append(val[:60] + " \u2026 " + val[-40:])
                    else:
                        values.append(val)
                elif isinstance(val, list):
                    values.append(", ".join(str(v)[:60] for v in val[:5]))
                elif isinstance(val, dict):
                    values.append(json.dumps(val)[:120])
                else:
                    values.append(str(val)[:120])

            # Only show fields that have at least one non-empty value
            if all(v == "\u2014" for v in values):
                continue
            out.plain("scraper", f"  {field_name}:")
            for i, v in enumerate(values):
                out.plain("scraper", f"    [{i}] {v}")

    action_log.append_to_list(
        board.log,
        "run scraper",
        True,
        f"{total} pages scraped, {titles_found} titles, avg {avg_time:.1f}s",
    )
    save_board(slug, board)

    # Quality-aware next step
    alt_scraper = "dom" if board.scraper_type != "dom" else "json-ld"
    if titles_found == 0:
        out.warn(
            "scraper",
            "No titles extracted — iterate current scraper config first; "
            "switch type only if needed. "
            "Try: ws task troubleshoot 'empty fields'",
        )
        out.plain("scraper", f"Config reference: ws help scraper {board.scraper_type}")
        out.plain("scraper", f"Try: ws select scraper {board.scraper_type} --config '{{...}}'")
        out.plain("scraper", f"If still failing: ws select scraper {alt_scraper}")
    elif descs_found == 0:
        out.warn(
            "scraper",
            "No descriptions extracted — iterate current scraper config first; "
            "switch type only if needed. "
            "Try: ws task troubleshoot 'empty fields'",
        )
        out.plain("scraper", f"Config reference: ws help scraper {board.scraper_type}")
        out.plain("scraper", f"Try: ws select scraper {board.scraper_type} --config '{{...}}'")
        out.plain("scraper", f"If still failing: ws select scraper {alt_scraper}")
    elif titles_found < total or descs_found < total:
        parts = []
        if titles_found < total:
            parts.append(f"{titles_found}/{total} titles")
        if descs_found < total:
            parts.append(f"{descs_found}/{total} descriptions")
        out.warn("scraper", f"{', '.join(parts)} — check scraper config or try a different type")
        out.plain("scraper", f"Config reference: ws help scraper {board.scraper_type}")


# ── Config management commands ─────────────────────────────────────────


@click.command(name="config")
@click.argument("name")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
def select_config(name: str, slug: str | None, board_alias: str | None):
    """Re-activate a previously tested configuration."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)

    if name not in board.configs:
        out.die(f"Config {name!r} not found. Available: {', '.join(board.configs) or 'none'}")

    cfg = board.configs[name]
    if cfg.get("status") == "rejected":
        reason = cfg.get("rejection_reason", "")
        out.warn("config", f"Config {name!r} was previously rejected: {reason}")

    board.active_config = name
    action_log.append_to_list(board.log, "select config", True, f"Re-activated config: {name!r}")
    save_board(slug, board)

    out.info("config", f"Active config: {name!r} ({cfg.get('monitor_type', '?')})")
    status = cfg.get("status", "unknown")
    out.plain("config", f"Status: {status}")


@click.command(name="reject-config")
@click.argument("name")
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option("--reason", "-r", required=True, help="Reason for rejecting this config")
def reject_config(name: str, slug: str | None, board_alias: str | None, reason: str):
    """Reject a configuration (board-local, not a GitHub action)."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)

    if name not in board.configs:
        out.die(f"Config {name!r} not found. Available: {', '.join(board.configs) or 'none'}")

    board.configs[name]["status"] = "rejected"
    board.configs[name]["rejection_reason"] = reason

    # If rejecting the active config, clear active_config
    if board.active_config == name:
        board.active_config = None

    action_log.append_to_list(board.log, "reject config", True, f"Rejected {name!r}: {reason}")
    save_board(slug, board)

    out.info("config", f"Rejected config {name!r}: {reason}")
    remaining = [n for n, c in board.configs.items() if c.get("status") != "rejected"]
    if remaining:
        out.plain("config", f"Available configs: {', '.join(remaining)}")


# ── Feedback command ───────────────────────────────────────────────────

_QUALITY_VALUES = ("clean", "noisy", "unusable", "absent")

_FEEDBACK_FIELDS = [
    "title",
    "description",
    "locations",
    "employment_type",
    "job_location_type",
    "date_posted",
    "base_salary",
    "skills",
    "qualifications",
    "responsibilities",
    "valid_through",
]

_REQUIRED_FIELDS = ("title", "description")
_IMPORTANT_FIELDS = ("locations", "employment_type", "job_location_type")


@click.command(name="feedback")
@click.argument("name", required=False)
@click.argument("slug", required=False)
@click.option("--board", "-b", "board_alias", default=None, help="Target board alias")
@click.option("--title", "title_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--description", "desc_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--locations", "loc_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--locations-notes", "loc_notes", default="")
@click.option("--employment-type", "et_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--employment-type-notes", "et_notes", default="")
@click.option("--job-location-type", "jlt_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--job-location-type-notes", "jlt_notes", default="")
@click.option("--date-posted", "dp_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--base-salary", "bs_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--skills", "sk_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--qualifications", "qual_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--responsibilities", "resp_q", type=click.Choice(_QUALITY_VALUES))
@click.option("--valid-through", "vt_q", type=click.Choice(_QUALITY_VALUES))
@click.option(
    "--verdict",
    required=True,
    type=click.Choice(("good", "acceptable", "poor", "unusable")),
    help="Overall verdict",
)
@click.option("--verdict-notes", required=True, help="Brief comment on this config's outcome")
def feedback_cmd(
    name: str | None,
    slug: str | None,
    board_alias: str | None,
    title_q: str | None,
    desc_q: str | None,
    loc_q: str | None,
    loc_notes: str,
    et_q: str | None,
    et_notes: str,
    jlt_q: str | None,
    jlt_notes: str,
    dp_q: str | None,
    bs_q: str | None,
    sk_q: str | None,
    qual_q: str | None,
    resp_q: str | None,
    vt_q: str | None,
    verdict: str,
    verdict_notes: str,
):
    """Record extraction quality feedback for a configuration."""
    slug = resolve_slug(slug)
    ws, board = _resolve_board(slug, board_alias)

    # Default name to active config
    if not name:
        name = board.active_config
    if not name or name not in board.configs:
        out.die(f"Config {name!r} not found. Available: {', '.join(board.configs) or 'none'}")

    cfg = board.configs[name]

    # Gather run quality data for auto-population
    run = cfg.get("run") or {}
    scraper_run = cfg.get("scraper_run") or {}
    monitor_total = run.get("jobs", 0)
    scraper_total = scraper_run.get("count", 0)
    run_quality = run.get("quality") or {}
    scraper_quality = scraper_run.get("quality") or {}
    coverage_data = {**run_quality, **scraper_quality}

    # Map option values to field names
    explicit_quality = {
        "title": title_q,
        "description": desc_q,
        "locations": loc_q,
        "employment_type": et_q,
        "job_location_type": jlt_q,
        "date_posted": dp_q,
        "base_salary": bs_q,
        "skills": sk_q,
        "qualifications": qual_q,
        "responsibilities": resp_q,
        "valid_through": vt_q,
    }
    notes_map = {
        "locations": loc_notes,
        "employment_type": et_notes,
        "job_location_type": jlt_notes,
    }

    # Build per-field feedback
    fields_fb: dict[str, dict] = {}
    for field_name in _FEEDBACK_FIELDS:
        count = coverage_data.get(field_name, 0)
        # Use the total from whichever source provided this field's data
        if field_name in scraper_quality:
            total = scraper_total or monitor_total
        else:
            total = monitor_total or scraper_total
        coverage = f"{count}/{total}" if total else "0/0"

        # Determine quality: explicit > auto-populate
        q = explicit_quality.get(field_name)
        if q is None:
            q = "absent" if count == 0 else None

        if q is not None:
            entry: dict[str, str] = {"coverage": coverage, "quality": q}
            notes = notes_map.get(field_name, "")
            if notes:
                entry["notes"] = notes
            fields_fb[field_name] = entry

    # Require explicit quality for all fields that have coverage (or are required)
    missing_explicit = []
    for field_name in _FEEDBACK_FIELDS:
        if field_name in fields_fb:
            continue
        count = coverage_data.get(field_name, 0)
        if field_name in scraper_quality:
            total = scraper_total or monitor_total
        else:
            total = monitor_total or scraper_total
        if count > 0 or field_name in _REQUIRED_FIELDS:
            flag = f"--{field_name.replace('_', '-')}"
            if count > 0:
                missing_explicit.append(f"{flag} ({count}/{total} jobs)")
            else:
                missing_explicit.append(flag)
    if missing_explicit:
        out.die(
            "Explicit quality required for: "
            f"{', '.join(missing_explicit)} "
            "(clean/noisy/unusable/absent)",
        )

    # Compute tier summaries
    def _tier_summary(tier_fields: tuple[str, ...]) -> dict[str, str]:
        tier_coverage = 0
        tier_total = 0
        worst_q = "clean"
        q_rank = {"clean": 0, "noisy": 1, "unusable": 2, "absent": 3}
        for f in tier_fields:
            fb = fields_fb.get(f)
            if fb:
                c, t = fb["coverage"].split("/")
                tier_coverage += int(c)
                tier_total += int(t)
                if q_rank.get(fb["quality"], 0) > q_rank.get(worst_q, 0):
                    worst_q = fb["quality"]
        return {"coverage": f"{tier_coverage}/{tier_total}", "quality": worst_q}

    feedback_data = {
        "fields": fields_fb,
        "required": _tier_summary(_REQUIRED_FIELDS),
        "important": _tier_summary(_IMPORTANT_FIELDS),
        "optional": _tier_summary(
            tuple(
                f
                for f in _FEEDBACK_FIELDS
                if f not in _REQUIRED_FIELDS and f not in _IMPORTANT_FIELDS
            )
        ),
        "verdict": verdict,
        "verdict_notes": verdict_notes,
    }

    cfg["feedback"] = feedback_data
    action_log.append_to_list(
        board.log,
        "feedback",
        True,
        f"Feedback for {name!r}: verdict={verdict}",
    )
    save_board(slug, board)

    out.info("feedback", f"Recorded feedback for {name!r}: verdict={verdict}")
    for tier_name, _tier_fields in [
        ("Required", _REQUIRED_FIELDS),
        ("Important", _IMPORTANT_FIELDS),
    ]:
        tier = feedback_data.get(tier_name.lower(), {})
        cov = tier.get("coverage", "?")
        qual = tier.get("quality", "?")
        out.plain("feedback", f"  {tier_name}: {cov} ({qual})")
    # Warn if description rated clean but samples are suspiciously short
    desc_fb = fields_fb.get("description", {})
    if desc_fb.get("quality") == "clean":
        scraper_run = cfg.get("scraper_run") or {}
        run_data_fb = cfg.get("run") or {}
        samples = (
            scraper_run.get("description_samples") or run_data_fb.get("description_samples") or []
        )
        short = [s for s in samples if s.get("length", 0) < 200]
        if short and len(short) >= len(samples) // 2:
            out.warn(
                "feedback",
                f"{len(short)}/{len(samples)} sample descriptions are under "
                f"200 chars — verify they contain real job content, not just "
                f"titles or boilerplate",
            )

    if verdict == "unusable":
        out.warn("feedback", "Verdict is unusable — cannot submit")
    elif verdict == "poor":
        out.warn("feedback", "Verdict is poor — submit requires --force")


# ── Quality gates ──────────────────────────────────────────────────────


def run_quality_gates(
    ws,
    boards: list,
) -> tuple[list[str], list[str]]:
    """Check quality gates for submit. Returns (blockers, warnings)."""
    blockers: list[str] = []
    warnings: list[str] = []

    if not boards:
        blockers.append("No boards configured")
        return blockers, warnings

    if not ws.name:
        blockers.append("Company name not set")
    if not ws.website:
        blockers.append("Company website not set")

    from src.shared.constants import DISPLAY_LOCALES

    missing_locales = [loc for loc in DISPLAY_LOCALES if not ws.descriptions.get(loc)]
    if missing_locales:
        blockers.append(
            f"Missing description locales: {', '.join(missing_locales)}. "
            f'Fill with: ws set --description "..." --description-locale <locale>'
        )

    for b in boards:
        if not b.active_config:
            blockers.append(f"Board {b.alias}: no config selected")
            continue

        cfg = b.configs.get(b.active_config)
        if not cfg:
            blockers.append(f"Board {b.alias}: active config {b.active_config!r} not found")
            continue

        if cfg.get("status") != "tested":
            blockers.append(f"Board {b.alias}: config not tested")
        elif cfg.get("run", {}).get("jobs", 0) == 0:
            blockers.append(f"Board {b.alias}: 0 jobs found")

        fb = cfg.get("feedback")
        if not fb:
            blockers.append(f"Board {b.alias}: no feedback for {b.active_config!r}")
        elif fb.get("verdict") == "unusable":
            blockers.append(f"Board {b.alias}: verdict is unusable")
        elif fb.get("verdict") == "poor":
            blockers.append(f"Board {b.alias}: verdict is poor (use --force)")

        # Check description quality from stored samples
        scraper_run = cfg.get("scraper_run") or {}
        run_data = cfg.get("run") or {}
        desc_samples = (
            scraper_run.get("description_samples") or run_data.get("description_samples") or []
        )
        if desc_samples:
            short_count = sum(1 for s in desc_samples if s.get("length", 0) < 200)
            if short_count == len(desc_samples):
                warnings.append(
                    f"Board {b.alias}: all {len(desc_samples)} sample descriptions "
                    f"are under 200 chars — may be trivial (titles, boilerplate)"
                )
            elif short_count > len(desc_samples) // 2:
                warnings.append(
                    f"Board {b.alias}: {short_count}/{len(desc_samples)} sample "
                    f"descriptions are under 200 chars"
                )

    # Check for image artifacts (original files saved by ws set).
    # Skip when logos already exist on CDN (reconfig mode — URLs are HTTP).
    logo_is_remote = ws.logo_url.startswith("http") if ws.logo_url else False
    icon_is_remote = ws.icon_url.startswith("http") if ws.icon_url else False
    if not logo_is_remote or not icon_is_remote:
        from src.workspace.state import ws_dir

        artifacts = ws_dir(ws.slug) / "artifacts" / "company"
        has_logo = bool(list(artifacts.glob("logo_original.*"))) if artifacts.exists() else False
        has_icon = bool(list(artifacts.glob("icon_original.*"))) if artifacts.exists() else False
        if not has_logo and not logo_is_remote:
            warnings.append("No full logo image artifact found (logo_url)")
        if not has_icon and not icon_is_remote:
            warnings.append("No minified square logo image artifact found (icon_url)")

    return blockers, warnings
