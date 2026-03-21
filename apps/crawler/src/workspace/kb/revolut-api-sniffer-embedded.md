---
type: case-study
company: revolut
monitor: api_sniffer
scraper: embedded
summary: "Cloudflare blocks headless browsers — stealth mode (--headless=new) solves it"
tags: [cloudflare, stealth, headless-new, browser-detection]
---
# Revolut — Cloudflare blocks headless, stealth mode solves it

## Setup
- Monitor: api_sniffer (browser mode with `stealth: true`)
- Scraper: embedded

## Key decisions
- Standard Playwright headless browsing triggers Cloudflare's bot detection — all requests blocked
- Enabling `stealth: true` in monitor config switches Chrome to `--headless=new` mode
- `--headless=new` is Chrome's newer headless implementation that is harder for bot detectors to fingerprint
- Once past Cloudflare, the API endpoint is straightforward to capture
- Scraper uses embedded JSON extraction (no additional stealth needed for detail pages)

## Config
```json
{
  "monitor_config": {
    "stealth": true
  }
}
```

## Lesson
If a site returns Cloudflare challenge pages or blocks headless browsers entirely,
try `stealth: true` first. This is the simplest fix and works for many Cloudflare-protected
sites. The `--headless=new` flag makes Chrome's headless mode behave more like a real browser.
