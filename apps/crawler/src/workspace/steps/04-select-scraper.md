# Step: Select and Test Scraper

**Board {board_progress}**: `{board_url}`

The monitor returns URLs only — you need a scraper to extract job details from each page.

## Scraper types (in preference order)

Pick the **first type that works**. Types higher in the list are more resilient and require
less configuration:

| # | Type | What it does | Config | Best when |
|---|------|-------------|--------|-----------|
| 1 | `json-ld` | Parses `<script type="application/ld+json">` JobPosting schema | Zero-config | Page has schema.org markup (very common on ATS-hosted pages) |
| 2 | `nextdata` | Extracts from Next.js `__NEXT_DATA__` JSON | `path` + `fields` | Site is built with Next.js |
| 3 | `embedded` | Extracts from `<script>` blocks, JS variables, or callback patterns | `pattern`/`script_id`/`variable` + `path` + `fields` | Page has structured JSON embedded in HTML (AF_initDataCallback, window.__DATA__, etc.) |
| 4 | `api_sniffer` | Captures XHR/fetch JSON responses via Playwright | Minimal (auto-mapped `fields`) | Page is a SPA that loads job data via API calls |
| 5 | `dom` | Step-based extraction walking flattened HTML elements | `steps` array (complex) | No structured data available; last resort |

**Key rules:**
- `json-ld` > `embedded`/`nextdata` > `dom` for resilience
- `render: false` > `render: true` — only render when static HTML is empty
- If job URLs point to a **known ATS domain** (greenhouse.io, lever.co, ashbyhq.com, etc.), start with `json-ld` — ATS job pages almost always have JSON-LD markup

## Skip probing when the choice is obvious

You do **not** need to run `ws probe scraper` if you can determine the scraper type from context:

- **Job URLs are on a known ATS domain** (greenhouse.io, lever.co, etc.) → use `json-ld`
- **You already know the page has JSON-LD** (saw it during validation) → use `json-ld`
- **Monitor was `nextdata`** → use `nextdata` scraper with matching config
- **Probe results from monitor step already showed embedded data** → use `embedded`

In these cases, go directly to select and test:

```bash
ws select scraper json-ld
ws run scraper
```

## When in doubt, probe first

```bash
ws probe scraper
```

This tests all types with heuristic auto-config against sample URLs and shows a quality comparison.

**Do NOT blindly follow "Next:" suggestions.** If required fields show 0/N for the best
scraper, the heuristic config is wrong — not necessarily the scraper type.

A detected pattern (e.g., `AF_initDataCallback`, NextData) is a strong signal even when
auto-generated field mapping fails.

## If probe detects a pattern but fields are 0/N

**Do not skip this.** When the probe detects embedded data but mapping fails:

1. Download raw HTML: `curl -s <url> -o /tmp/page.html` (NOT WebFetch — it summarizes via LLM)
2. Search for the detected pattern in the raw HTML
3. Write a small Python script to parse and print the structure
4. Configure the `embedded` scraper with correct `pattern`, `path`, and `fields`
5. Run `ws help scraper embedded` for config format

## DOM scraper (last resort)

**Always prefer `render: false`** — only use `render: true` when static fetch is empty.

```bash
ws select scraper dom --config '{{"render": false, "steps": [...]}}'
ws run scraper
```

For dom scraper, inspect `flat.json` to verify DOM element order before writing steps.
Steps must follow DOM order (forward-only cursor) — wrong order silently skips fields.
Run `ws help scraper dom` for step format and `ws help steps` for the step key reference.

{rejected_configs}

## When done

```bash
ws task next --notes "<scraper type chosen, extraction stats, any issues>"
```
