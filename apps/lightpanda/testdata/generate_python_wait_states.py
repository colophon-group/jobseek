"""Freeze original Chromium wait_for behavior using local, offline HTML only."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import structlog
from playwright.async_api import async_playwright
from src.shared.browser import run_actions


async def main() -> None:
    structlog.configure(
        processors=[lambda logger, method, event_dict: {}],
        logger_factory=structlog.ReturnLoggerFactory(),
    )
    cases = [
        (
            "visible-contents-preserved-space",
            '<div id="target" style="display:contents;white-space:pre">   </div>',
            "visible",
        ),
        (
            "visible-contents-nbsp",
            '<div id="target" style="display:contents">&nbsp;</div>',
            "visible",
        ),
        (
            "hidden-contents-zero-percent-font",
            '<div id="target" style="display:contents;font-size:0%">Role</div>',
            "hidden",
        ),
        (
            "hidden-contents-zero-rem-font",
            '<div id="target" style="display:contents;font-size:0rem">Role</div>',
            "hidden",
        ),
        ("default-visible", '<div id="target">Role</div>', None),
        (
            "visible-opacity-zero",
            '<div id="target" style="opacity:0">Role</div>',
            "visible",
        ),
        (
            "hidden-display-none",
            '<div id="target" style="display:none">Role</div>',
            "hidden",
        ),
        (
            "hidden-visibility",
            '<div id="target" style="visibility:hidden">Role</div>',
            "hidden",
        ),
        ("hidden-absent", "<div>Role</div>", "hidden"),
        (
            "visible-zero-box",
            '<div id="target" style="width:0;height:0;padding:0"></div>',
            "visible",
        ),
        (
            "visible-hidden",
            '<div id="target" style="visibility:hidden">Role</div>',
            "visible",
        ),
        (
            "first-hidden",
            '<div id="target" style="display:none">First</div><div id="target">Second</div>',
            "visible",
        ),
        (
            "visible-display-contents",
            '<div id="target" style="display:contents"><span>Role</span></div>',
            "visible",
        ),
        (
            "visible-contents-text",
            '<div id="target" style="display:contents">Role</div>',
            "visible",
        ),
        (
            "hidden-empty-contents",
            '<div id="target" style="display:contents"></div>',
            "hidden",
        ),
        (
            "attached-hidden",
            '<div id="target" style="display:none">Role</div>',
            "attached",
        ),
        ("detached-absent", "<div>Role</div>", "detached"),
        (
            "hidden-contents-space",
            '<div id="target" style="display:contents">   </div>',
            "hidden",
        ),
        (
            "hidden-contents-child",
            '<div id="target" style="display:contents"><span style="display:none">Role</span></div>',
            "hidden",
        ),
        (
            "hidden-contents-zero-font",
            '<div id="target" style="display:contents;font-size:0">Role</div>',
            "hidden",
        ),
        (
            "contents-hidden-parent",
            '<div id="target" style="display:contents;visibility:hidden">Role</div>',
            "visible",
        ),
    ]
    rows = []
    async with async_playwright() as pw:
        browser = await pw.chromium.launch(headless=True)
        try:
            for name, html, state in cases:
                page = await browser.new_page()
                action = {
                    "action": "wait_for",
                    "selector": "#target",
                    "required": True,
                    "timeout": 0.25,
                }
                if state is not None:
                    action["state"] = state
                await page.set_content(html)
                error = None
                try:
                    await run_actions(page, [action])
                except TimeoutError as exc:
                    error = type(exc).__name__
                rows.append(
                    {
                        "name": name,
                        "html": html,
                        "actions": [action],
                        "success": error is None,
                    }
                )
                await page.close()
        finally:
            await browser.close()
    Path(__file__).with_name("python_wait_states.json").write_text(
        json.dumps(rows, indent=2) + "\n"
    )
    print(
        json.dumps(
            {"original_cases": len(rows), "success": sum(x["success"] for x in rows)}
        )
    )


if __name__ == "__main__":
    asyncio.run(main())
