"""Configuration commands: set, add board, del board."""

from __future__ import annotations

import json
from pathlib import Path

import click

from src.shared.constants import SLUG_RE
from src.shared.csv_io import read_csv
from src.workspace import log as action_log
from src.workspace import output as out
from src.workspace.state import (
    Board,
    board_yaml_path,
    list_boards,
    load_board,
    load_workspace,
    resolve_slug,
    resolve_two_args,
    save_board,
    save_workspace,
    workspace_exists,
    ws_log_path,
)


@click.command(name="set")
@click.argument("slug", required=False)
@click.option("--name", help="Company display name")
@click.option("--website", help="Company homepage URL")
@click.option("--logo-url", help="Full primary logo image URL (direct file; transparent preferred)")
@click.option(
    "--icon-url",
    help="Minified square logo/icon image URL (direct file; transparent preferred)",
)
@click.option("--logo-candidate", type=int, help="Select full-logo candidate number")
@click.option("--icon-candidate", type=int, help="Select minified-logo candidate number")
@click.option("--board", "board_alias", help="Board alias for board-scoped settings")
@click.option(
    "--job-link-pattern",
    help="Regex pattern matching job-detail links on the selected board",
)
def set_(
    slug: str | None,
    name: str | None,
    website: str | None,
    logo_url: str | None,
    icon_url: str | None,
    logo_candidate: int | None,
    icon_candidate: int | None,
    board_alias: str | None,
    job_link_pattern: str | None,
):
    """Set company metadata in workspace."""
    slug = resolve_slug(slug)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)
    updates = []

    if name is not None:
        ws.name = name
        updates.append(f"name={name!r}")
    if website is not None:
        _check_duplicate_website(website, slug)
        ws.website = website
        updates.append(f"website={website!r}")
        _check_url("website", website)

    # Resolve candidates to URLs
    if logo_candidate is not None:
        logo_url = _resolve_candidate(slug, logo_candidate, "logo")
    if icon_candidate is not None:
        icon_url = _resolve_candidate(slug, icon_candidate, "icon")

    if logo_url is not None:
        ws.logo_url = logo_url
        updates.append("logo_url")
        _check_image("logo_url", logo_url, slug)
    if icon_url is not None:
        ws.icon_url = icon_url
        updates.append("icon_url")
        _check_image("icon_url", icon_url, slug)

    if job_link_pattern is not None:
        alias = board_alias or ws.active_board
        if not alias:
            out.die(
                "No active board. Provide --board <alias> or run: ws use --board <alias> first."
            )
        try:
            board = load_board(slug, alias)
        except FileNotFoundError:
            out.die(f"Board {alias!r} not found in workspace {slug!r}")
        board.job_link_pattern = job_link_pattern
        save_board(slug, board)
        updates.append(f"job_link_pattern[{alias}]")

        analysis = _inspect_board_job_links(board.url, job_link_pattern)
        _report_job_link_analysis(
            analysis,
            alias=alias,
            provided_pattern=True,
        )

    if not updates:
        out.die("Nothing to set. Provide at least one --option.")

    save_workspace(ws)

    if logo_url is not None or icon_url is not None:
        _show_final_logo_inspection_reminder(slug)

    # Auto-discover brand assets + career pages when website is set but no logo/icon provided
    effective_website = website or ws.website
    if (
        logo_url is None
        and icon_url is None
        and logo_candidate is None
        and icon_candidate is None
        and job_link_pattern is None
        and effective_website
    ):
        _discover_and_show_all(slug, effective_website)

    action_log.append(
        ws_log_path(slug),
        "set",
        True,
        f"Set {', '.join(updates)}",
    )
    out.info("workspace", f"Set {', '.join(updates)}")


def _normalize_url(url: str) -> str:
    """Normalize a URL for dedup comparison: lowercase scheme+host, strip trailing slash."""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    return urlunparse(
        (
            parsed.scheme.lower(),
            parsed.netloc.lower(),
            parsed.path.rstrip("/") or "/",
            parsed.params,
            parsed.query,
            "",  # drop fragment
        )
    )


def _check_duplicate_website(website: str, current_slug: str) -> None:
    """Error if another company already uses this website URL."""
    from src.shared.constants import get_data_dir

    companies_path = get_data_dir() / "companies.csv"
    if not companies_path.exists():
        return

    normalized = _normalize_url(website)
    _, rows = read_csv(companies_path)
    for row in rows:
        if row["slug"] == current_slug:
            continue
        existing = row.get("website", "")
        if existing and _normalize_url(existing) == normalized:
            out.die(
                f"Website URL already used by company {row['slug']!r}"
                f" ({row.get('name', '')}).\n"
                f"This is likely a duplicate. Use a different URL."
            )


def _check_duplicate_board_url(board_url: str, current_slug: str) -> None:
    """Error if another board already uses this URL."""
    from src.shared.constants import get_data_dir

    boards_path = get_data_dir() / "boards.csv"
    if not boards_path.exists():
        return

    normalized = _normalize_url(board_url)
    _, rows = read_csv(boards_path)
    for row in rows:
        if row.get("company_slug") == current_slug:
            continue
        existing = row.get("board_url", "")
        if existing and _normalize_url(existing) == normalized:
            out.die(
                f"Board URL already used by {row['board_slug']!r}"
                f" (company: {row['company_slug']!r}).\n"
                f"This is likely a duplicate. Use a different URL."
            )


def _resolve_candidate(slug: str, index: int, role: str) -> str:
    """Resolve a candidate index to a URL from candidates.json."""
    from src.workspace.state import ws_dir

    candidates_path = ws_dir(slug) / "artifacts" / "company" / "logo-candidates" / "candidates.json"
    if not candidates_path.exists():
        out.die(
            "No logo candidates found. Run 'ws set --website <url>' first to discover candidates."
        )

    candidates = json.loads(candidates_path.read_text())

    # Find candidate by index
    for c in candidates:
        if c["index"] == index:
            png_artifact_path = str(c.get("png_artifact_path", "") or "")
            original_artifact_path = str(
                c.get("original_artifact_path", "") or c.get("artifact_path", "")
            )
            for label, path in (
                ("PNG preview", png_artifact_path),
                ("original artifact", original_artifact_path),
            ):
                if path and Path(path).exists():
                    out.info(role, f"Selected candidate #{index} ({label}): {path}")
                    return path
            url = c.get("url", "")
            if url:
                out.info(role, f"Selected candidate #{index}: {url}")
                return url
            out.die(f"Candidate #{index} has no reachable artifact path or URL")

    out.die(f"Candidate #{index} not found. Available: {[c['index'] for c in candidates]}")
    return ""  # unreachable


def _fetch_homepage(website: str) -> tuple[str, str]:
    """Fetch homepage with browser-like headers. Returns (html, final_url)."""
    import httpx

    from src.workspace.logo_discover import _LOGO_HEADERS

    html = ""
    final_url = website
    try:
        resp = httpx.get(website, headers=_LOGO_HEADERS, follow_redirects=True, timeout=10)
        if resp.status_code >= 400:
            out.warn("fetch", f"Homepage returned HTTP {resp.status_code} — using fallbacks")
        else:
            html = resp.text
            final_url = str(resp.url)
    except Exception as e:
        out.warn("fetch", f"Could not fetch homepage: {e} — using fallbacks")

    return html, final_url


def _candidate_value(candidate: object, key: str, default: object = "") -> object:
    """Read candidate value from dataclass or dict."""
    if isinstance(candidate, dict):
        return candidate.get(key, default)
    return getattr(candidate, key, default)


def _format_candidate_tech(candidate: object) -> str:
    """Format technical diagnostics for display."""
    filename = str(_candidate_value(candidate, "filename", "") or "")
    size_bytes = _candidate_value(candidate, "file_size_bytes", None)
    width = _candidate_value(candidate, "width", None)
    height = _candidate_value(candidate, "height", None)
    ratio = _candidate_value(candidate, "aspect_ratio", None)
    square = _candidate_value(candidate, "is_square", None)
    transparent = _candidate_value(candidate, "has_transparency", None)
    ocr_text = str(_candidate_value(candidate, "ocr_text", "") or "")

    parts: list[str] = []
    if filename:
        parts.append(f"file={filename}")
    if isinstance(size_bytes, int):
        parts.append(f"bytes={size_bytes}")
    if isinstance(width, int) and isinstance(height, int):
        parts.append(f"dims={width}x{height}")
    if isinstance(ratio, (int, float)):
        parts.append(f"ar={ratio:.3f}")
    if isinstance(square, bool):
        parts.append(f"square={'yes' if square else 'no'}")
    if isinstance(transparent, bool):
        parts.append(f"alpha={'yes' if transparent else 'no'}")
    if ocr_text:
        normalized = " ".join(ocr_text.split())
        if len(normalized) > 36:
            normalized = normalized[:33] + "..."
        parts.append(f"ocr={normalized}")
    return "; ".join(parts)


def _candidate_paths(candidate: object) -> tuple[str, str]:
    """Return original and PNG artifact paths for a candidate."""
    original = str(
        _candidate_value(candidate, "original_artifact_path", "")
        or _candidate_value(candidate, "artifact_path", "")
    )
    png = str(_candidate_value(candidate, "png_artifact_path", "") or "")
    return original, png


def _show_candidate_inspection_reminder(candidate_dir: Path) -> None:
    """Nudge agents to manually inspect PNG candidate previews."""
    out.warn(
        "logos",
        "Manual visual inspection required: ws can rank/download candidates but cannot verify "
        "brand correctness.",
    )
    out.plain("logos", "Inspect PNG previews before selecting candidates:")
    out.plain("logos", f"  {candidate_dir}")
    out.plain("logos", "  Use candidate-*.png files for visual checks.")


def _show_final_logo_inspection_reminder(slug: str) -> None:
    """Nudge agents to verify final logo/icon artifacts after ws set."""
    from src.workspace.state import ws_dir

    artifact_dir = ws_dir(slug) / "artifacts" / "company"
    out.warn(
        "logos",
        "Manual visual inspection required: ws cannot confirm that selected assets are the "
        "correct full logo and minified icon.",
    )
    out.plain("logos", "Verify final PNG artifacts before continuing:")
    out.plain("logos", f"  {artifact_dir / 'logo.png'}")
    out.plain("logos", f"  {artifact_dir / 'icon.png'}")


def _show_logo_results(slug: str, html: str, final_url: str) -> None:
    """Discover logos from HTML, download artifacts, and display table."""
    from src.workspace.logo_discover import discover_logos, download_candidates
    from src.workspace.state import ws_dir

    candidates = discover_logos(html, final_url)
    if not candidates:
        out.warn("logos", "No candidates found")
        return

    # Download and save artifacts
    artifact_dir = ws_dir(slug) / "artifacts" / "company" / "logo-candidates"
    successful = download_candidates(candidates, artifact_dir)

    if not successful:
        out.warn("logos", "No candidates could be downloaded")
        return

    out.info("logos", f"Found {len(successful)} candidate(s):")
    print()

    # Display table
    rows = []
    for i, c in enumerate(successful, 1):
        original_path, png_path = _candidate_paths(c)
        rows.append(
            [
                str(i),
                c.role,
                f"{c.score:.2f}",
                ", ".join(c.sources),
                original_path,
                png_path,
                _format_candidate_tech(c),
            ]
        )

    out.table(["#", "Role", "Score", "Sources", "Original", "PNG", "Tech"], rows)
    print()
    out.plain(
        "logos",
        "Note: each candidate stores the original artifact and a PNG preview side-by-side.",
    )
    _show_candidate_inspection_reminder(artifact_dir)
    print()

    out.plain("logos", "Verify candidates visually, then select (logo=full, icon=minified):")
    out.plain("logos", "  ws set --logo-candidate 1 --icon-candidate 2")
    out.plain("logos", "Or provide your own URLs (logo_url=full, icon_url=minified square):")
    out.plain("logos", "  ws set --logo-url <url> --icon-url <url>")
    out.plain("logos", "Rules: direct image URLs, brand-correct assets, transparent preferred.")


def _show_career_results(slug: str, html: str, final_url: str, homepage_url: str) -> None:
    """Discover career pages from HTML + blind probes, display results."""
    import asyncio

    from src.workspace.career_discover import discover_career_pages

    async def _run():
        import httpx

        from src.workspace.logo_discover import _LOGO_HEADERS

        client = httpx.AsyncClient(
            headers=_LOGO_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0),
            limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        )
        try:
            return await discover_career_pages(homepage_url, html, client)
        finally:
            await client.aclose()

    out.info("careers", "Discovering career pages...")
    candidates = asyncio.run(_run())

    if not candidates:
        out.warn("careers", "No boards detected")
        return

    out.info("careers", f"Found {len(candidates)} board(s):")
    print()

    rows = []
    for i, c in enumerate(candidates, 1):
        # Truncate URL for display
        display_url = c.url
        if len(display_url) > 55:
            display_url = display_url[:52] + "..."
        hub = str(c.job_link_hub) if c.job_link_hub is not None else "—"
        rows.append(
            [
                str(i),
                c.monitor_type,
                display_url,
                c.source,
                hub,
            ]
        )

    out.table(["#", "Monitor", "URL", "Source", "JobLinks"], rows)
    print()

    # Show "To add" suggestion for the top candidate
    top = candidates[0]
    out.plain("careers", f'To add:  ws add board careers --url "{top.url}"')


def _discover_and_show_all(slug: str, website: str) -> None:
    """Unified discovery: fetch homepage once, discover logos + career pages."""
    html, final_url = _fetch_homepage(website)
    _show_logo_results(slug, html, final_url)
    print()
    _show_career_results(slug, html, final_url, website)


def _discover_and_show_candidates(slug: str, website: str) -> None:
    """Fetch homepage, discover logo candidates, download, and display table."""
    html, final_url = _fetch_homepage(website)
    _show_logo_results(slug, html, final_url)


@click.command(name="logos")
@click.argument("slug", required=False)
def logos(slug: str | None):
    """Show full/minified logo candidates and current selection."""
    slug = resolve_slug(slug)
    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)

    # Current selection
    if ws.logo_url or ws.icon_url:
        out.info("logos", "Current selection:")
        if ws.logo_url:
            out.plain("logos", f"  full_logo (logo_url): {ws.logo_url}")
        if ws.icon_url:
            out.plain("logos", f"  minified_logo (icon_url): {ws.icon_url}")
        print()

    # Candidates from last discovery
    from src.workspace.state import ws_dir

    candidates_path = ws_dir(slug) / "artifacts" / "company" / "logo-candidates" / "candidates.json"
    if not candidates_path.exists():
        out.warn("logos", "No candidates discovered yet. Run: ws set --website <url>")
        return

    candidates = json.loads(candidates_path.read_text())
    if not candidates:
        out.warn("logos", "No candidates found")
        return

    out.info("logos", f"{len(candidates)} candidate(s):")
    print()

    rows = []
    for c in candidates:
        original_path, png_path = _candidate_paths(c)
        rows.append(
            [
                str(c["index"]),
                c.get("role", "?"),
                f"{c.get('score', 0):.2f}",
                ", ".join(c.get("sources", [])),
                original_path,
                png_path,
                _format_candidate_tech(c),
            ]
        )

    out.table(["#", "Role", "Score", "Sources", "Original", "PNG", "Tech"], rows)
    print()
    out.plain(
        "logos",
        "Note: each candidate stores the original artifact and a PNG preview side-by-side.",
    )
    _show_candidate_inspection_reminder(candidates_path.parent)
    print()

    out.plain("logos", "Select (logo=full, icon=minified):")
    out.plain("logos", "  ws set --logo-candidate 1 --icon-candidate 2")
    out.plain("logos", "Or provide URLs (logo_url=full, icon_url=minified square):")
    out.plain("logos", "  ws set --logo-url <url> --icon-url <url>")
    out.plain("logos", "Rules: direct image URLs, brand-correct assets, transparent preferred.")


@click.command(name="discover")
@click.argument("slug", required=False)
def discover(slug: str | None):
    """Discover logos and career pages from company homepage."""
    slug = resolve_slug(slug)
    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    ws = load_workspace(slug)
    if not ws.website:
        out.die("No website set. Run: ws set --website <url>")

    _discover_and_show_all(slug, ws.website)


def _check_url(label: str, url: str) -> None:
    """Advisory URL reachability check."""
    try:
        import httpx

        from src.workspace.logo_discover import _LOGO_HEADERS

        resp = httpx.head(url, headers=_LOGO_HEADERS, follow_redirects=True, timeout=10)
        if resp.status_code < 400:
            final = str(resp.url)
            if final != url:
                out.warn(label, f"Redirects to {final}")
            else:
                out.info(label, f"Reachable ({resp.status_code})")
        else:
            out.warn(label, f"HTTP {resp.status_code}")
    except Exception as e:
        out.warn(label, f"Could not reach: {e}")


def _check_image(label: str, url: str, slug: str) -> None:
    """Download image, convert to PNG, and save as workspace artifact."""
    # Handle local file paths (e.g., embedded SVG artifact paths)
    if url.startswith("/") or url.startswith("."):
        path = Path(url)
        if path.exists():
            data = path.read_bytes()
            ct = "image/svg+xml" if path.suffix == ".svg" else "image/png"
            out.info(label, f"Local file: {path.name}, {len(data):,} bytes")
            png_path = save_image_to_path(slug, label, data, ct)
            if png_path:
                out.info(label, f"Saved: {png_path}")
            return
        out.warn(label, f"File not found: {url}")
        return

    try:
        import httpx

        from src.workspace.logo_discover import _LOGO_HEADERS

        resp = httpx.get(url, headers=_LOGO_HEADERS, follow_redirects=True, timeout=10)
        ct = resp.headers.get("content-type", "")
        size = len(resp.content)
        if not ("image" in ct or "svg" in ct):
            out.warn(label, f"Not an image: {ct}, {size:,} bytes")
            return

        out.info(label, f"{ct}, {size:,} bytes")

        # Save as PNG artifact for visual verification
        png_path = save_image_to_path(slug, label, resp.content, ct)
        if png_path:
            out.info(label, f"Saved: {png_path}")
    except Exception as e:
        out.warn(label, f"Could not fetch: {e}")


def save_image_to_path(slug: str, label: str, data: bytes, content_type: str) -> Path | None:
    """Save original image and a PNG preview under workspace artifacts.

    Saves two files:
    - ``{name}_original.{ext}`` — original bytes in original format (for R2 upload)
    - ``{name}.png`` — PNG rasterization for agent visual verification (stored alongside original)

    Args:
        slug: Workspace slug.
        label: Image label ("logo_url" full or "icon_url" minified) — used to derive filename.
        data: Raw image bytes.
        content_type: HTTP content-type header value.

    Returns:
        Path to saved PNG preview file, or None on failure.
    """
    from src.workspace.state import ws_dir

    artifact_dir = ws_dir(slug) / "artifacts" / "company"
    artifact_dir.mkdir(parents=True, exist_ok=True)

    # label is "logo_url" or "icon_url" → filename "logo" or "icon"
    name = label.replace("_url", "")
    ext = _ext_from_content_type(content_type)
    png_path = artifact_dir / f"{name}.png"

    # Always save the original bytes in original format
    original_path = artifact_dir / f"{name}_original{ext}"
    original_path.write_bytes(data)

    if ext == ".svg":
        try:
            import cairosvg  # type: ignore[import-untyped]

            cairosvg.svg2png(bytestring=data, write_to=str(png_path))
            return png_path
        except ImportError:
            out.warn(label, "cairosvg not installed — saved raw SVG (no PNG preview)")
            return original_path
        except Exception as e:
            out.warn(label, f"SVG PNG conversion failed: {e}")
            return original_path

    try:
        import io

        from PIL import Image

        img = Image.open(io.BytesIO(data))
        # Convert to RGBA to handle transparency, then save as PNG
        if img.mode not in ("RGBA", "RGB"):
            img = img.convert("RGBA")
        img.save(png_path, "PNG")
        return png_path
    except ImportError:
        out.warn(label, "Pillow not installed — saved raw file (no PNG conversion)")
        return original_path
    except Exception as e:
        out.warn(label, f"PNG conversion failed: {e}")
        return original_path


def _ext_from_content_type(ct: str) -> str:
    """Map content-type to file extension."""
    ct = ct.lower().split(";")[0].strip()
    mapping = {
        "image/png": ".png",
        "image/jpeg": ".jpg",
        "image/gif": ".gif",
        "image/svg+xml": ".svg",
        "image/webp": ".webp",
        "image/x-icon": ".ico",
        "image/vnd.microsoft.icon": ".ico",
    }
    return mapping.get(ct, ".bin")


def _inspect_board_job_links(url: str, provided_pattern: str | None):
    """Fetch board page and analyze outgoing job-link patterns."""
    import asyncio

    async def _run():
        import httpx

        from src.workspace.job_links import (
            analyze_job_links,
            fetch_page_for_job_link_analysis,
        )
        from src.workspace.logo_discover import _LOGO_HEADERS

        client = httpx.AsyncClient(
            headers=_LOGO_HEADERS,
            follow_redirects=True,
            timeout=httpx.Timeout(20.0),
            limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
        )
        try:
            fetched = await fetch_page_for_job_link_analysis(
                url,
                client,
                allow_render_fallback=True,
            )
        finally:
            await client.aclose()

        if not fetched.html:
            from src.workspace.job_links import JobLinkPatternAnalysis

            return JobLinkPatternAnalysis(
                board_url=url,
                final_url=fetched.final_url,
                fetch_mode=fetched.fetch_mode,
                provided_pattern=provided_pattern,
                warnings=fetched.warnings + ["Could not read board page HTML for link analysis."],
            )

        analysis = analyze_job_links(
            fetched.final_url,
            fetched.html,
            provided_pattern=provided_pattern,
        )
        analysis.final_url = fetched.final_url
        analysis.fetch_mode = fetched.fetch_mode
        analysis.warnings.extend(fetched.warnings)
        return analysis

    try:
        return asyncio.run(_run())
    except Exception as exc:
        from src.workspace.job_links import JobLinkPatternAnalysis

        return JobLinkPatternAnalysis(
            board_url=url,
            final_url=url,
            provided_pattern=provided_pattern,
            warnings=[f"Job-link analysis failed: {exc}"],
        )


def _report_job_link_analysis(analysis, *, alias: str, provided_pattern: bool) -> None:
    """Print analysis summary and guidance for job-link pattern handling."""
    if analysis.final_url and analysis.final_url != analysis.board_url:
        out.warn("board", f"Board URL redirects to {analysis.final_url}")

    out.info(
        "board",
        (
            "Outgoing links: "
            f"{analysis.outgoing_links_total} | likely job links: "
            f"{analysis.job_links_total}"
        ),
    )

    for warning in analysis.warnings:
        out.warn("board", warning)

    if analysis.fetch_mode == "render":
        out.warn(
            "board",
            "Used browser-rendered HTML for link analysis (JS-only/bot-protected page). "
            "Results may still be incomplete.",
        )

    if provided_pattern:
        out.info(
            "board",
            f"Pattern matches {analysis.matched_job_links} likely job links "
            f"({analysis.matched_outgoing_links} outgoing links matched).",
        )
        if analysis.matched_job_links == 0:
            out.warn(
                "board",
                "Provided pattern matched 0 likely job links. "
                "Verify the board URL or refine --job-link-pattern.",
            )
        return

    if analysis.pattern:
        out.info("board", f"Inferred job link pattern: {analysis.pattern}")
        out.info(
            "board",
            f"Pattern coverage: {analysis.matched_job_links} likely job links "
            f"({analysis.matched_outgoing_links} outgoing links matched).",
        )
    else:
        out.warn(
            "board",
            "Could not infer a reliable job-link pattern. "
            "This page may not be a real job board, or it has too few linked jobs.",
        )
        out.plain(
            "board",
            f"Provide manually: ws set --board {alias} --job-link-pattern '<regex>'",
        )


@click.command(name="board")
@click.argument("slug_or_alias")
@click.argument("alias", required=False)
@click.option("--url", required=True, help="Board URL")
@click.option(
    "--job-link-pattern",
    default=None,
    help="Regex pattern matching job-detail links on this board page",
)
def add_board(
    slug_or_alias: str,
    alias: str | None,
    url: str,
    job_link_pattern: str | None,
):
    """Add a board to workspace."""
    slug, alias = resolve_two_args(slug_or_alias, alias)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    # Check for double-prefix
    if alias.startswith(f"{slug}-"):
        board_slug = alias
        out.warn(
            "board",
            f"Alias {alias!r} already prefixed — board slug will be {alias!r}. "
            f"Did you mean {alias.removeprefix(f'{slug}-')!r}?",
        )
    else:
        board_slug = f"{slug}-{alias}"

    if not SLUG_RE.match(board_slug):
        out.die(f"Invalid board slug: {board_slug!r}")

    _check_duplicate_board_url(url, slug)

    ws = load_workspace(slug)
    board = Board(alias=alias, slug=board_slug, url=url)
    analysis = _inspect_board_job_links(url, job_link_pattern)

    if job_link_pattern is not None:
        board.job_link_pattern = job_link_pattern
    elif analysis.pattern:
        board.job_link_pattern = analysis.pattern

    save_board(slug, board)

    # Auto-activate
    ws.active_board = alias
    save_workspace(ws)

    action_log.append(
        ws_log_path(slug),
        "add board",
        True,
        f"Added board {alias} — {url}",
    )

    # Append to board's embedded log
    action_log.append_to_list(board.log, "add board", True, f"Added board {alias} — {url}")
    save_board(slug, board)

    out.info("board", f"Added board {board_slug} — {url}")
    _report_job_link_analysis(
        analysis,
        alias=alias,
        provided_pattern=job_link_pattern is not None,
    )
    if board.job_link_pattern:
        out.plain("board", f"Job link pattern: {board.job_link_pattern}")
    out.plain("board", f"Active board: {board_slug}")
    out.next_step("ws probe monitor")


@click.command(name="board")
@click.argument("slug_or_alias")
@click.argument("alias", required=False)
def del_board(slug_or_alias: str, alias: str | None):
    """Remove a board from workspace."""
    slug, alias = resolve_two_args(slug_or_alias, alias)

    if not workspace_exists(slug):
        out.die(f"Workspace {slug!r} not found")

    path = board_yaml_path(slug, alias)
    if not path.exists():
        out.die(f"Board {alias!r} not found in workspace {slug!r}")

    path.unlink()
    out.info("board", f"Removed board {alias!r}")

    # Switch active board if needed
    ws = load_workspace(slug)
    if ws.active_board == alias:
        remaining = list_boards(slug)
        ws.active_board = remaining[0].alias if remaining else ""
        save_workspace(ws)
        if ws.active_board:
            out.plain("board", f"Active board switched to: {ws.active_board}")
        else:
            out.plain("board", "No boards remaining")

    # Keep task workflow pointer consistent after board removal.
    from src.workspace.workflow import _load_wf_from_disk, _save_wf_to_disk

    wf = _load_wf_from_disk(slug)
    changed = False
    if wf.current_board == alias:
        wf.current_board = ws.active_board or None
        changed = True
    if alias in wf.completed_boards:
        wf.completed_boards = [a for a in wf.completed_boards if a != alias]
        changed = True
    if changed:
        _save_wf_to_disk(slug, wf)
