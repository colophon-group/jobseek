"""Temporary read-only public-page probe; remove before merging this PR."""
from __future__ import annotations

import asyncio
import json
import re
import subprocess

import httpx
from playwright.async_api import async_playwright

URLS = [
    "https://www.roboa.ch/career/jobs/rss.xml",
    "https://roboa-lp.webflow.io/career",
    "https://roboa-lp.webflow.io/career/jobs/software-intern",
    "https://roboa-lp.webflow.io/career/jobs/field-hardware-engineer",
    "https://roboa-lp.webflow.io/career/jobs/service-und-produktionstechniker",
]

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"


def summarize(label, url, status, body, headers=None):
    print(json.dumps({
        "transport": label,
        "url": url,
        "status": status,
        "length": len(body),
        "title": re.findall(r"<title[^>]*>(.*?)</title>", body, re.S)[:1],
        "job_links": sorted(set(re.findall(r'[/]career/jobs/[^"<>\\s]+', body)))[:8],
        "webflow_site": re.findall(r'data-wf-site="([^"]+)', body),
        "h1": re.findall(r"<h1[^>]*>(.*?)</h1>", body, re.S),
        "has_software_intern": "Software Intern" in body,
        "prefix": re.sub(r"\\s+", " ", body[:400]),
        "response_headers": {k: v for k, v in (headers or {}).items() if k.lower() in {"server", "cf-mitigated", "content-type", "location"}},
    }), flush=True)


async def main():
    for url in URLS:
        for label, headers in [
            ("httpx-default", {}),
            ("httpx-browser-ua", {"User-Agent": UA}),
            ("httpx-browser-headers", {"User-Agent": UA, "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8", "Accept-Language": "en-US,en;q=0.9"}),
        ]:
            try:
                async with httpx.AsyncClient(follow_redirects=True, timeout=20) as client:
                    r = await client.get(url, headers=headers)
                    summarize(label, str(r.url), r.status_code, r.text, dict(r.headers))
            except Exception as e:
                print(json.dumps({"transport": label, "url": url, "error": type(e).__name__}), flush=True)
        for http in ["--http1.1", "--http2"]:
            r = subprocess.run(["curl", "--silent", "--show-error", "--location", "--max-time", "20", http, "--user-agent", UA, "--write-out", "\\nSTATUS:%{http_code}", url], capture_output=True, text=True)
            summarize("curl" + http, url, r.stdout.rsplit("STATUS:", 1)[-1], r.stdout.rsplit("STATUS:", 1)[0])
    async with async_playwright() as p:
        browser = await p.chromium.launch(channel="chrome", headless=True)
        for url in [URLS[0], URLS[2]]:
            page = await browser.new_page()
            try:
                response = await page.goto(url, wait_until="domcontentloaded", timeout=30000)
                await page.wait_for_timeout(5000)
                summarize("chromium", page.url, response.status if response else None, await page.content(), await response.all_headers() if response else {})
            except Exception as e:
                print(json.dumps({"transport": "chromium", "url": url, "error": type(e).__name__}), flush=True)
            await page.close()
        await browser.close()


asyncio.run(main())
