"""Crawl commands: probe, select monitor/scraper, run monitor/scraper."""

from __future__ import annotations

import asyncio
import json
import random
import time

import click

from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace.state import (
    load_board,
    load_workspace,
    resolve_slug,
    resolve_two_args,
    save_board,
    save_workspace,
    workspace_exists,
)


def _get_active_board(slug: str):
    """Load workspace and its active board. Dies if either is missing."""
    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")
    ws = load_workspace(slug)
    if not ws.active_board:
        out.die("No active board. Run: ws add board <alias> --url <url>")
    board = load_board(slug, ws.active_board)
    return ws, board


_MONITOR_PROBE_HINTS: dict[str, str] = {
    "sitemap": "Tip: may include non-job URLs \u2014 verify count, consider url_filter",
    "api_sniffer": "Tip: auto-maps fields from API response \u2014 verify field quality in probe metadata",
    "dom": "Tip: static detection only \u2014 try render: true if count seems low",
}


@click.command(name="monitor")
@click.argument("slug", required=False)
def probe_monitors(slug: str | None):
    """Probe all monitor types for the active board's URL."""
    slug = resolve_slug(slug)
    ws, board = _get_active_board(slug)

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
                "name": name, "detected": metadata is not None,
                "metadata": metadata, "comment": comment,
            } for name, metadata, comment in results
        ],
    )
    out.plain("artifacts", f"Saved: {probe_dir}")

    # Store probe results and print
    probe_summary_parts = []
    for name, metadata, comment in results:
        if metadata is not None:
            symbol = "\u2713"
            # Store suggested config for later use
            if board.monitor_type is None:
                pass  # Don't auto-select
        else:
            symbol = "\u2717"

        out.plain("probe", f"{name:<14}{symbol}  {comment}")
        # Per-type hint for detected monitors
        if metadata is not None and name in _MONITOR_PROBE_HINTS:
            out.plain("probe", f"  {_MONITOR_PROBE_HINTS[name]}")
        probe_summary_parts.append(f"{name} {symbol}")

    # Suggest best detected monitor
    best = next(((name, meta) for name, meta, _ in results if meta is not None), None)
    if best:
        out.next_step(f"ws select monitor {best[0]}")
    else:
        out.warn(
            "probe",
            "No monitors detected. Check the board URL or try "
            "ws select monitor dom --config '{\"render\": true}'",
        )

    # Log
    summary = ", ".join(probe_summary_parts)
    action_log.append_to_list(board.log, "probe monitor", True, summary)
    save_board(slug, board)

    # Store probe metadata for select monitor auto-config
    probe_data = {}
    for name, metadata, _comment in results:
        if metadata is not None:
            probe_data[name] = metadata
    if probe_data:
        board.monitor_config = board.monitor_config or {}
        board.monitor_config["_probe"] = probe_data
        save_board(slug, board)


@click.command(name="scraper")
@click.argument("slug", required=False)
@click.option("--url", "urls", multiple=True, help="Override sample URLs")
def probe_scraper(slug: str | None, urls: tuple[str, ...]):
    """Probe all scraper types against sample URLs."""
    slug = resolve_slug(slug)
    ws, board = _get_active_board(slug)

    # Guard: API monitors don't need scrapers
    api_monitors = {"ashby", "greenhouse", "lever"}
    is_rich_monitor = board.monitor_type in api_monitors or (
        board.monitor_type == "api_sniffer"
        and (board.monitor_config or {}).get("fields")
    )
    if is_rich_monitor:
        out.warn(
            "scraper",
            f"Monitor '{board.monitor_type}' returns full data \u2014 scraper not needed",
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

    # Limit to 3 URLs for probe
    target_urls = target_urls[:3]

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
                "name": name, "detected": metadata is not None,
                "metadata": metadata, "comment": comment,
            } for name, metadata, comment in results
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
    best_config = None
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
                best_config = config
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
            if best_config:
                out.next_step(f"ws select scraper {best_name} --config '{json.dumps(best_config)}'")
            else:
                out.next_step(f"ws select scraper {best_name}")

            # Heuristic warning for non-json-ld
            if best_name != "json-ld":
                out.warn("probe", "Heuristic config \u2014 verify fields, check for unmapped data")
    else:
        out.warn("probe", "No scrapers auto-detected. Try manual dom config:")
        out.plain("probe", "  ws help steps")

    # Log action
    probe_summary_parts = [
        f"{name} {'✓' if meta is not None else '✗'}"
        for name, meta, _ in results
    ]
    action_log.append_to_list(
        board.log, "probe scraper", True, ", ".join(probe_summary_parts),
    )
    save_board(slug, board)


_MONITOR_CONFIG_HINTS = {
    "greenhouse": "Requires: token (auto-filled from probe)",
    "lever": "Requires: token (auto-filled from probe)",
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
@click.option("--config", "config_json", help="Monitor config JSON")
def select_monitor(slug_or_type: str, type_: str | None, config_json: str | None):
    """Set monitor type for the active board."""
    slug, type_ = resolve_two_args(slug_or_type, type_)
    ws, board = _get_active_board(slug)

    # Validate type against registry
    from src.core.monitors import get_discoverer

    try:
        get_discoverer(type_)
    except ValueError as e:
        out.die(str(e))

    config = {}
    if config_json:
        config = json.loads(config_json)
    elif board.monitor_config and "_probe" in board.monitor_config:
        # Auto-fill from probe results
        probe_data = board.monitor_config["_probe"]
        if type_ in probe_data:
            config = {k: v for k, v in probe_data[type_].items()}
            out.info("monitor", f"Auto-filled config from probe: {json.dumps(config)}")

    # Clean up probe data from config
    clean_config = {k: v for k, v in config.items() if k != "_probe"}

    board.monitor_type = type_
    board.monitor_config = clean_config
    ws.progress["monitor_selected"] = True

    save_board(slug, board)
    save_workspace(ws)

    action_log.append_to_list(board.log, "select monitor", True, f"Selected monitor: {type_}")
    save_board(slug, board)

    out.info("monitor", f"Selected monitor: {type_}")
    if clean_config:
        out.plain("monitor", f"Config: {json.dumps(clean_config)}")
    elif type_ in _MONITOR_CONFIG_HINTS:
        out.plain("monitor", f"Config: {_MONITOR_CONFIG_HINTS[type_]}")
    out.next_step("ws run monitor")


# Fields checked in quality reports for DiscoveredJob (monitor rich data)
_MONITOR_QUALITY_FIELDS = [
    "title", "description", "locations", "employment_type",
    "job_location_type", "date_posted", "base_salary",
    "skills", "responsibilities", "qualifications",
]

# Fields checked in quality reports for JobContent (scraper extraction)
_SCRAPER_QUALITY_FIELDS = [
    "title", "description", "locations", "employment_type",
    "job_location_type", "date_posted", "valid_through", "base_salary",
    "skills", "responsibilities", "qualifications",
]

# Core fields always shown in terminal output
_CORE_FIELDS = ("title", "description", "locations")


@click.command(name="monitor")
@click.argument("slug", required=False)
def run_monitor(slug: str | None):
    """Test-crawl the active board with its selected monitor."""
    slug = resolve_slug(slug)
    ws, board = _get_active_board(slug)

    if not board.monitor_type:
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
                    board.monitor_type,
                    board.monitor_config or None,
                    http,
                    artifact_dir=run_dir,
                    pw=pw,
                )
                elapsed = time.monotonic() - start
            return result, elapsed, http_log
        finally:
            await http.aclose()

    result, elapsed, http_log = asyncio.run(_run())

    # Save HTTP log and structlog events
    save_http_log(run_dir, http_log)
    save_events(run_dir, log_events)

    job_count = len(result.urls)
    has_rich = result.jobs_by_url is not None

    # Store results in board
    board.monitor_run = {
        "jobs": job_count,
        "time": round(elapsed, 1),
        "has_rich_data": has_rich,
        "sample_urls": random.sample(sorted(result.urls), min(10, len(result.urls))),
        "ran_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    ws.progress["monitor_tested"] = True

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
            count = sum(1 for j in jobs if getattr(j, field, None))
            pct = round(count / total * 100) if total else 0
            quality["fields"][field] = {"count": count, "pct": pct}
        save_quality(run_dir, quality)
        board.monitor_run["quality"] = {f: v["count"] for f, v in quality["fields"].items()}

    out.plain("artifacts", f"Saved: {run_dir}")

    save_board(slug, board)
    save_workspace(ws)

    # Print results
    if result.filtered_count:
        out.plain("monitor", f"URL filter: {result.filtered_count} URLs removed")

    if job_count == 0:
        out.warn(
            "monitor",
            f"0 jobs in {elapsed:.1f}s \u2014 check board URL or try a different monitor type",
        )
    else:
        out.info("monitor", f"{job_count} jobs in {elapsed:.1f}s")

    # Count verification prompt — completeness matters most
    if job_count > 0:
        out.plain(
            "monitor",
            "Verify: compare this count against the website's displayed job total",
        )

    # url_filter tip for sitemap monitors without a filter
    if (
        board.monitor_type == "sitemap"
        and job_count > 0
        and not (board.monitor_config or {}).get("url_filter")
    ):
        sample_urls = sorted(result.urls)[:20]
        if sample_urls:
            from urllib.parse import urlparse
            from os.path import commonprefix

            paths = [urlparse(u).path for u in sample_urls]
            prefix = commonprefix(paths)
            # Trim to last full segment
            if prefix and "/" in prefix:
                prefix = prefix[:prefix.rindex("/") + 1]
            if prefix and prefix != "/" and len(prefix) >= 3:
                matching = sum(1 for u in sample_urls if urlparse(u).path.startswith(prefix))
                if matching / len(sample_urls) >= 0.8:
                    out.plain(
                        "monitor",
                        f"Tip: {matching}/{len(sample_urls)} sample URLs share prefix "
                        f"\"{prefix}\" \u2014 consider url_filter for reliability",
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
        # API monitors skip scraper (including api_sniffer with fields)
        api_monitors = {"ashby", "greenhouse", "lever"}
        is_rich_api = board.monitor_type in api_monitors or (
            board.monitor_type == "api_sniffer"
            and (board.monitor_config or {}).get("fields")
        )
        if is_rich_api:
            out.plain("monitor", "Skipping scraper — monitor returns full job data")
            ws.progress["scraper_selected"] = True
            ws.progress["scraper_tested"] = True
            save_workspace(ws)
    else:
        out.plain("monitor", "Rich data: no (URLs only, needs scraper)")

    if result.urls:
        sample = next(iter(result.urls))
        out.plain("monitor", f"Sample: {sample}")
    if result.jobs_by_url:
        sample_job = next(iter(result.jobs_by_url.values()))
        if sample_job.title:
            out.plain("monitor", f"Sample title: {sample_job.title}")

    action_log.append_to_list(
        board.log,
        "run monitor",
        True,
        f"{job_count} jobs in {elapsed:.1f}s ({'rich data' if has_rich else 'URLs only'})",
    )
    save_board(slug, board)

    if not has_rich:
        if board.monitor_type == "nextdata":
            out.next_step("ws select scraper nextdata")
        else:
            out.next_step("ws select scraper json-ld")
    else:
        out.next_step("ws submit")


@click.command(name="scraper")
@click.argument("slug_or_type")
@click.argument("type_", required=False)
@click.option("--config", "config_json", help="Scraper config JSON")
def select_scraper(slug_or_type: str, type_: str | None, config_json: str | None):
    """Set scraper type for the active board."""
    slug, type_ = resolve_two_args(slug_or_type, type_)
    ws, board = _get_active_board(slug)

    # Validate type against registry
    from src.core.scrapers import get_scraper

    try:
        get_scraper(type_)
    except ValueError as e:
        out.die(str(e))

    config = json.loads(config_json) if config_json else {}

    board.scraper_type = type_
    board.scraper_config = config
    ws.progress["scraper_selected"] = True

    save_board(slug, board)
    save_workspace(ws)

    action_log.append_to_list(board.log, "select scraper", True, f"Selected scraper: {type_}")
    save_board(slug, board)

    out.info("scraper", f"Selected scraper: {type_}")
    if config:
        out.plain("scraper", f"Config: {json.dumps(config)}")
    elif type_ in _SCRAPER_CONFIG_HINTS:
        out.plain("scraper", f"Config: {_SCRAPER_CONFIG_HINTS[type_]}")
    out.next_step("ws run scraper")


@click.command(name="scraper")
@click.argument("slug", required=False)
@click.option("--url", "urls", multiple=True, help="Specific URLs to scrape (repeatable)")
def run_scraper(slug: str | None, urls: tuple[str, ...]):
    """Test-scrape sample job pages from the active board."""
    slug = resolve_slug(slug)
    ws, board = _get_active_board(slug)

    if not board.scraper_type:
        out.die("No scraper selected. Run: ws select scraper <type>")

    # Determine which URLs to scrape
    target_urls: list[str] = list(urls)
    if not target_urls:
        # Use all stored samples (already randomly selected by run monitor)
        target_urls = board.monitor_run.get("sample_urls", [])
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
        from playwright.async_api import async_playwright

        from src.core.scrape import scrape_one
        from src.shared.http import create_logging_http_client

        http, http_log = create_logging_http_client()
        results = []
        try:
            async with async_playwright() as pw:
                for i, url in enumerate(target_urls):
                    job_id = f"sample-{i}"
                    start = time.monotonic()
                    content = await scrape_one(
                        url, board.scraper_type, board.scraper_config or None, http,
                        artifact_dir=run_dir, job_id=job_id, pw=pw,
                    )
                    elapsed = time.monotonic() - start
                    results.append((url, content, elapsed))
            return results, http_log
        finally:
            await http.aclose()

    results, http_log = asyncio.run(_run())

    # Save HTTP log and structlog events
    save_http_log(run_dir, http_log)
    save_events(run_dir, log_events)

    # Aggregate stats
    titles_found = sum(1 for _, c, _ in results if c.title)
    locations_found = sum(1 for _, c, _ in results if c.locations)
    descs_found = sum(1 for _, c, _ in results if c.description)
    times = [e for _, _, e in results]
    avg_time = sum(times) / len(times) if times else 0

    board.scraper_run = {
        "count": len(results),
        "avg_time": round(avg_time, 1),
        "titles": titles_found,
        "descriptions": descs_found,
        "locations": locations_found,
        "ran_at": __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    ws.progress["scraper_tested"] = True

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
            present = bool(getattr(content, field, None))
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
            } for f in _SCRAPER_QUALITY_FIELDS
        },
        "per_url": quality_per_url,
    }
    save_quality(run_dir, quality)
    board.scraper_run["quality"] = {f: v for f, v in quality_totals.items()}

    out.plain("artifacts", f"Saved: {run_dir}")

    save_board(slug, board)
    save_workspace(ws)

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
    print()

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
        "title", "locations", "description", "employment_type",
        "job_location_type", "date_posted", "valid_through",
        "qualifications", "responsibilities", "skills", "metadata",
    ]

    if results:
        print()
        out.plain("scraper", "Extracted content:")
        for field_name in _SAMPLE_FIELDS:
            values = []
            for _, content, _ in results:
                val = getattr(content, field_name, None)
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
        out.warn("scraper", "No titles extracted \u2014 try a different scraper type")
        out.next_step(f"ws select scraper {alt_scraper}")
    elif descs_found == 0:
        out.warn("scraper", "No descriptions extracted \u2014 try a different scraper type")
        out.next_step(f"ws select scraper {alt_scraper}")
    elif titles_found < total or descs_found < total:
        parts = []
        if titles_found < total:
            parts.append(f"{titles_found}/{total} titles")
        if descs_found < total:
            parts.append(f"{descs_found}/{total} descriptions")
        out.warn("scraper", f"{', '.join(parts)} — check scraper config or try a different type")
        out.next_step("ws submit")
    else:
        out.next_step("ws submit")
