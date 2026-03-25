# DOM monitor — jobs listed inside a cross-origin iframe widget

## Symptom

The careers page embeds job listings via a third-party widget loaded in an
`<iframe>` (e.g. onlyfy.jobs, prescreen.io, or similar ATS widget providers).
The DOM monitor with `render: true` discovers some jobs from the parent page
but misses others that are only visible inside the iframe. The iframe may also
have a "Show more" / "Load more" button that must be clicked to reveal all jobs.

## Root cause

Cross-origin iframes are invisible to the parent page's DOM —
`document.querySelectorAll('a[href]')` only sees links in the main frame.
The standard `repeat` action clicks in the parent frame and cannot interact
with elements inside the iframe.

## Solution

Use the `frame` option on the `repeat` action to target clicks inside the
iframe. This:

1. Resolves the iframe via Playwright's frame API (crosses origin boundaries).
2. Clicks the "Show more" element inside the iframe using JS (bypasses overlays).
3. Measures link counts inside the frame for the stopping condition.
4. After all clicks, injects the frame's discovered links into the parent
   page DOM so the DOM monitor's link extractor can find them.

### Monitor config example

```json
{
  "render": true,
  "wait": "networkidle",
  "timeout": 30000,
  "actions": [
    {"action": "dismiss_overlays"},
    {"action": "repeat", "selector": "a.load-more", "frame": "iframe[src*=\"widget-provider\"]", "wait_ms": 3000}
  ],
  "url_filter": "\\?jh="
}
```

### Key points

- **`frame`** must be a CSS selector matching the `<iframe>` element in the
  parent page (e.g. `iframe[src*="onlyfy"]`, `iframe[src*="prescreen"]`).
- The **`selector`** is resolved inside the iframe, not the parent.
- Link injection copies *all* `<a href>` links from the frame into the parent
  as hidden elements — use `url_filter` to select only job links.
- If the widget uses `?jh=` or similar token params that map to a direct job
  URL on the widget provider's domain, configure the scraper to redirect
  (via an `evaluate` action) to the provider's full job page for extraction.
- Run `dismiss_overlays` before `repeat` to clear cookie banners on the parent
  page. Cookie banners inside the iframe are bypassed by using JS clicks.

## When to suspect this pattern

- The careers page loads an embedded widget (visible as an `<iframe>` in
  DevTools) from a third-party domain.
- `ws run monitor` with `render: true` finds fewer jobs than visible on the
  page, and the missing jobs are inside the iframe.
- The iframe has pagination or "Show more" controls.
- Common widget providers: onlyfy.jobs, prescreen.io, rexx-systems.com,
  erecruiter.net.
