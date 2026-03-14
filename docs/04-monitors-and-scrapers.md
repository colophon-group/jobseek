# Monitors and Scrapers

Monitors discover which jobs exist on a board. Scrapers extract details from individual job pages. Together they form the data pipeline.

## Rich vs URL-Only Monitors

Monitors fall into two categories:

- **Rich monitors** return complete `DiscoveredJob` data (title, description, locations, etc.) in a single request. The batch processor inserts this directly — no scraper step is needed.
- **URL-only monitors** return a set of job page URLs. Each URL is then scraped individually to extract job details. Most URL-only monitors auto-configure their scraper (see `auto_scraper_type()` in `_compat.py`).

Cost implications:
- **Rich**: cost = one monitor invocation per cycle (~0.5–2s). No scraper cost.
- **URL-only**: cost = one monitor invocation + N × scraper cost per new job. First run scrapes all existing jobs (initial load: N × 0.3–4s depending on scraper type). Steady-state cost is low since only new jobs need scraping.

### Why no N+1 monitors

An earlier design had monitors that listed jobs (1 call) then fetched each detail page (N calls) in the same hourly cycle — the "N+1 pattern". This was removed because:

1. **Wasted requests**: Monitors run hourly but job details rarely change. Fetching N detail pages every hour wastes ~24× the necessary requests.
2. **Rate-limit risk**: Hammering detail endpoints hourly triggers rate-limiting and IP blocks.
3. **Slow cycles**: A board with 500 jobs takes minutes to poll instead of seconds.
4. **Fragile coupling**: Monitor failures (e.g. one detail page 404) could break the entire discovery cycle.

The fix: monitors return URLs only (1 cheap call), scrapers fetch details on a daily schedule (N calls, amortized). This is enforced by design — `register()` accepts `rich=True` (single-call full data) or `rich=False` (URL set). There is no mechanism for a monitor to make per-job detail requests. If a new ATS needs per-job detail fetching, implement it as a scraper, not inside the monitor.

## Monitors

A monitor takes a board config and returns either **full job data** (rich monitors) or **URL sets** (URL-only monitors).

### Monitor Types

| Type | Kind | Auto-scraper | When to Use |
|------|------|-------------|-------------|
| `amazon` | Rich | skip | Amazon Jobs |
| `ashby` | Rich | skip | Ashby ATS |
| `dvinci` | Rich | skip | d.vinci ATS |
| `gem` | Rich | skip | Gem ATS |
| `greenhouse` | Rich | skip | Greenhouse ATS |
| `hireology` | Rich | skip | Hireology ATS |
| `lever` | Rich | skip | Lever ATS |
| `pinpoint` | Rich | skip | Pinpoint ATS |
| `recruitee` | Rich | skip | Recruitee ATS |
| `rss` | Rich | skip | RSS 2.0 feeds (SuccessFactors, Teamtailor, etc.) |
| `traffit` | Rich | skip | Traffit ATS |
| `personio` | Rich* | — | Personio (*rich via XML feed, HTML fallback needs scraper) |
| `bite` | URL-only | bite | b-ite.com ATS |
| `breezy` | URL-only | json-ld (+dom fallback) | Breezy HR |
| `join` | URL-only | nextdata | JOIN (join.com) |
| `rippling` | URL-only | rippling | Rippling ATS |
| `smartrecruiters` | URL-only | smartrecruiters | SmartRecruiters ATS |
| `softgarden` | URL-only | json-ld | Softgarden ATS |
| `workable` | URL-only | workable | Workable ATS |
| `workday` | URL-only | workday | Workday ATS |
| `api_sniffer` | Rich* | skip/— | XHR/fetch capture (*rich when `fields` configured) |
| `nextdata` | URL-only* | skip/— | Next.js `__NEXT_DATA__` (*rich when `fields` configured) |
| `sitemap` | URL-only | — | Site has an XML sitemap with job URLs |
| `dom` | URL-only | — | Last resort — link extraction from page HTML |

Rich monitors return complete job data in a single request — no scraper needed. URL-only monitors with auto-scrapers need no manual scraper selection; the scraper is configured automatically. Monitors marked "—" require manual scraper selection.

### greenhouse

Fetches from the Greenhouse public JSON API.

**API**: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`

**Detection**: Four tiers:
1. Direct URL match (`boards.greenhouse.io/{token}`)
2. Regional board URL match (`job-boards.<region>.greenhouse.io/{token}`)
3. Page HTML scan for Greenhouse API references / `urlToken` in inline JS
4. Slug-based API probe (derive slug from domain, hit the API)

**Config**:
```json
{"token": "stripe"}
```

The token is the board identifier. For direct or regional Greenhouse URLs it's
extracted from the URL path. For custom domains, detection finds it in page HTML
or probes by company slug. If probe picks the wrong token, set it manually:
`ws select monitor greenhouse --config '{"token":"<token>"}'`.

**Returns**: Full job data — title, HTML description, locations (from location + offices), departments, education, date posted. Cap: 10,000 jobs.

### lever

Fetches from the Lever Postings API with pagination.

**API**: `GET https://api.lever.co/v0/postings/{site}?limit=100&skip=N`

**Detection**: Same three-tier pattern as greenhouse, with Lever-specific URL patterns.

**Config**:
```json
{"token": "cloudflare"}
```

**Returns**: Full job data — title, HTML description (combined from description + lists + additional), locations, employment type, workplace type, salary range, team, department. Rate-limited to 2 req/sec. Cap: 10,000 jobs.

### sitemap

Parses XML sitemaps to discover job URLs.

**Discovery strategy** (tried in order):
1. Walk up the URL path trying `sitemap.xml` at each level
2. Try common non-standard paths (`/sitemaps/sitemapIndex`, etc.)
3. Parse `robots.txt` for `Sitemap:` directives
4. Handle sitemap indexes by finding job-related child sitemaps

**Config**:
```json
{"sitemap_url": "https://example.com/jobs/sitemap.xml"}
```

`sitemap_url` is optional — if omitted, the monitor auto-discovers it and caches the result in board metadata for future checks.

**Returns**: URL set only. Needs a scraper to extract job details.

### nextdata

Extracts job listings from Next.js sites using `__NEXT_DATA__` props.

**Config**:
```json
{
  "path": "props.pageProps.positions",
  "url_template": "https://example.com/jobs/{id}",
  "slug_fields": ["title"],
  "render": false,
  "actions": [],
  "fields": {
    "title": "title",
    "locations": "offices[].name",
    "metadata.team": "department.name"
  }
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `path` | Yes | Dot-path to the jobs array in `__NEXT_DATA__` JSON |
| `url_template` | Yes | URL template with `{field}` placeholders from each item |
| `slug_fields` | No | Fields to slugify and expose as `{slug}` in the template |
| `render` | No | `false` (default) for static HTTP, `true` for Playwright |
| `actions` | No | Browser action pipeline (see [Actions](#actions)); implies `render: true` |
| `fields` | No | Field mapping for rich mode (omit for URL-only) |

**Returns**: URL set or full data depending on whether `fields` is configured. May need a scraper for full job details.

**When to use**: When the career site is built with Next.js and embeds job data in `__NEXT_DATA__`.

### dom

Link extraction from career pages. By default (``render: false``) fetches via static HTTP and parses `<a>` tags. Set `render: true` to render with Playwright for JS-heavy SPAs.

**Config**:
```json
{
  "render": false,
  "actions": []
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `render` | No | `false` (default) for static HTTP, `true` for Playwright |
| `actions` | No | Browser action pipeline (see [Actions](#actions)); implies `render: true` |
| `wait` | No | Playwright wait strategy (only when rendering) |
| `timeout` | No | Playwright navigation timeout in ms (only when rendering) |
| `user_agent` | No | Custom User-Agent string (only when rendering) |
| `headless` | No | Run browser in headless mode, default `true` (only when rendering) |

Link discovery filters `<a href>` URLs containing job-related keywords (job, career, position, posting, opening, role, vacancy).

**Returns**: URL set only. Needs a scraper to extract job details.

**When to use**: Only when no API monitor, sitemap, or nextdata monitor is available. The agent should exhaust all other options first.

---

## Scrapers

A scraper takes a job page URL and returns structured job data. Only needed when the monitor returns URL-only results.

### Scraper Types

| Type | Fetch mode | How it works |
|------|-----------|-------------|
| `json-ld` | Static | Parses `<script type="application/ld+json">` |
| `nextdata` | Static or Playwright | Extracts from Next.js `__NEXT_DATA__` props |
| `embedded` | Static | Extracts from embedded JSON/JS data in page source |
| `dom` | Static or Playwright | Step-based extraction engine |
| `api_sniffer` | Playwright | Captures XHR/fetch API responses |

> **Note:** API monitors (ashby, greenhouse, lever, etc.) return full job data directly — no scraper is needed. The `scraper_type` column is left empty for these.

### json-ld

Parses [schema.org/JobPosting](https://schema.org/JobPosting) JSON-LD from the page HTML. Many modern career sites embed this for SEO.

**Config**:
```json
{}
```

No config needed — the extractor handles all standard [schema.org/JobPosting](https://schema.org/JobPosting) fields automatically. See [08 — Job Data Fields: Schema.org Mapping](./08-job-data-fields.md#schemaorg--json-ld-mapping) for the complete mapping table.

Key mappings: `title`/`name` → title, `description` → description (HTML), `jobLocation` → locations, `baseSalary` → `{currency, min, max, unit}` dict, `employmentType` → employment type, `jobLocationType` → remote/hybrid/onsite, `skills`/`responsibilities`/`qualifications` → lists, `datePosted`/`validThrough` → dates.

**When to use**: Try this first for any sitemap-discovered board. Many sites (Meta, LinkedIn, Indeed, Workable-powered) embed JSON-LD. Use `ws probe` to auto-detect, or `ws select scraper json-ld` and `ws run scraper` to test.

### nextdata

Extracts job details from Next.js `__NEXT_DATA__` page props.

**Config**:
```json
{
  "path": "props.pageProps.jobData",
  "render": false,
  "actions": [],
  "fields": {
    "title": "title",
    "description": "descriptionHtml",
    "locations": "locations[].name",
    "metadata.team": "department.name"
  }
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `path` | No | Dot-path to the job object in `__NEXT_DATA__` JSON |
| `fields` | Yes | Map of target field → source path in the job object (see [08 — Job Data Fields: Field Mapping](./08-job-data-fields.md#field-mapping-in-scrapers)) |
| `render` | No | `false` (default) for static HTTP, `true` for Playwright |
| `actions` | No | Browser action pipeline (see [Actions](#actions)); implies `render: true` |

**When to use**: When the career site is built with Next.js and individual job pages embed data in `__NEXT_DATA__`.

### dom

Step-based extraction engine. Supports two modes:

- **`render: false`** (default) — fetches via static HTTP (no browser needed)
- **`render: true`** — launches Playwright to render JS before extraction

**Config** (static mode):
```json
{
  "steps": [
    {"tag": "h1", "field": "title"},
    {"text": "Location", "offset": 1, "field": "location"},
    {"text": "About", "field": "description", "stop": "Requirements", "html": true}
  ]
}
```

**Config** (Playwright mode):
```json
{
  "render": true,
  "steps": [
    {"tag": "h1", "field": "title"},
    {"text": "Location", "offset": 1, "field": "location"},
    {"text": "About", "field": "description", "stop": "Requirements", "html": true}
  ],
  "wait": "networkidle",
  "actions": [{"action": "dismiss_overlays"}]
}
```

| Key | Required | Description |
|-----|----------|-------------|
| `steps` | Yes | Extraction steps (see [Step keys](#step-keys)) |
| `render` | No | `false` (default) for static HTTP, `true` for Playwright |
| `actions` | No | Browser action pipeline (see [Actions](#actions)); implies `render: true` |
| `wait` | No | Playwright wait strategy (only when rendering) |
| `timeout` | No | Playwright navigation timeout in ms (only when rendering) |
| `user_agent` | No | Custom User-Agent string (only when rendering) |
| `headless` | No | Run browser in headless mode, default `true` (only when rendering) |

#### Step keys

Each step in the `steps` array supports:

| Key | Description |
|-----|-------------|
| `tag` | Match by element tag name |
| `text` | Match by substring in element text |
| `attr` | Match by HTML attribute (`"key=substring"` or `"key"`) |
| `field` | Output field name (omit for anchor-only steps) |
| `offset` | Skip N elements after match before extracting (default 0) |
| `stop` | Stop collecting when element text contains this string |
| `stop_tag` | Stop collecting when element tag matches |
| `stop_count` | Max elements to collect in a range |
| `optional` | If true, suppress warning when step not found |
| `regex` | Regex with capture group; applied to extracted text |
| `split` | Split extracted text into a list on this delimiter |
| `html` | If true, preserve tag structure in range output as HTML |
| `from` | Override seek start position (e.g. 0 to search from beginning) |

**When to use**: For any site that needs step-based extraction. Use the default `render: false` when the page works without JavaScript; set `render: true` for JS-heavy SPAs (Ashby, Workday, Workable).

---

## Browser Config Keys

The following keys are standardized across all monitors and scrapers that support rendering:

| Key | Default | Description |
|-----|---------|-------------|
| `render` | `false` | `true` to render with Playwright, `false` for static HTTP |
| `actions` | `[]` | Action pipeline to run after page load (implies `render: true`) |
| `wait` | `"networkidle"` | Playwright wait strategy: `load`, `domcontentloaded`, `networkidle`, `commit` |
| `timeout` | `30000` | Playwright navigation timeout in milliseconds |
| `user_agent` | Chrome UA | Custom User-Agent string |
| `headless` | `true` | Run browser in headless mode |

If `actions` are configured with `render: false`, the system overrides to `render: true` and emits a misconfiguration warning.

### Actions

The action pipeline runs sequentially after page navigation, before content extraction. Each action has a 10-second timeout (configurable per-action via `"timeout"` key). Failures are logged as warnings and execution continues.

| Action | Keys | Description |
|--------|------|-------------|
| `dismiss_overlays` | — | Remove common cookie/consent banners |
| `click` | `selector` | Click the first element matching the CSS selector |
| `remove` | `selector` | Remove all elements matching the CSS selector from the DOM |
| `wait` | `ms` (default 1000) | Wait for a fixed duration |
| `evaluate` | `script` | Run arbitrary JavaScript on the page |

Example:
```json
{
  "actions": [
    {"action": "dismiss_overlays"},
    {"action": "click", "selector": "button.load-more"},
    {"action": "wait", "ms": 2000},
    {"action": "remove", "selector": ".cookie-banner"}
  ]
}
```

---

## Choosing the Right Config

Decision tree for agents (use `ws probe` to auto-detect):

```
1. Is the board on a known ATS (Greenhouse, Lever, Ashby, etc.)?
   → Use the corresponding API monitor (scraper not needed — returns full data)

2. Does the site have an XML sitemap with job URLs?
   a. Do individual job pages have JSON-LD?
      → monitor: sitemap, scraper: json-ld
   b. Do job pages have consistent HTML structure?
      → monitor: sitemap, scraper: dom

4. Is the site built with Next.js?
   → monitor: nextdata, scraper: nextdata or json-ld

5. None of the above?
   a. Do job pages render without JS?
      → monitor: dom, scraper: json-ld or dom
   b. Job pages need JS to render?
      → monitor: dom (render: true), scraper: dom (render: true)
```

## Existing Code

Monitor implementations are adapted from the current crawler:

| Location | Description |
|----------|-------------|
| `src/core/monitors/greenhouse.py` | Greenhouse JSON API monitor |
| `src/core/monitors/lever.py` | Lever Postings API monitor |
| `src/core/monitors/sitemap.py` | XML sitemap parser monitor |
| `src/core/monitors/nextdata.py` | Next.js `__NEXT_DATA__` monitor |
| `src/core/monitors/dom.py` | Link extraction monitor (static or Playwright) |
| `src/core/scrapers/jsonld.py` | JSON-LD extractor |
| `src/core/scrapers/dom.py` | Step-based scraper (static or Playwright) |
| `src/core/scrapers/nextdata.py` | Next.js data extractor |

---

## Troubleshooting

### Monitor returns fewer jobs than expected

1. Check if the website shows a total job count (e.g. "Showing 247 open positions")
2. `sitemap` monitor: the sitemap may not include all job URLs
   → Try `dom` or `nextdata` monitor as fallback
3. `greenhouse`/`lever`: API may require a different token
   → Try alternative slugs derived from the URL or page HTML
4. `dom` monitor: try `render: true` if the page needs JavaScript to show all links
5. Paginated boards (`dom` / `api_sniffer`): set `max_pages` so it clearly
   overshoots the expected real page count, then rely on early stop when no
   new jobs appear. Avoid conservative caps that silently undercount listings.

### Monitor returns zero jobs

1. Verify the board URL is correct and loads in a browser
2. For `greenhouse`/`lever`: verify the token is correct (try hitting the API directly)
3. For `sitemap`: verify the sitemap contains job URLs (not just pages)
4. For `dom`: try `render: true` and add actions if needed (e.g. cookie dismissal)

### Scraper extracts empty or wrong fields

1. `json-ld`: verify JSON-LD exists — some pages have partial JSON-LD that's missing fields
2. `dom`: check step config — use `ws run scraper` to test, examine `flat.json` artifact
3. `dom` with `render: true`: page may need longer wait time or specific actions
4. Consider switching scraper type (e.g. `json-ld` → `dom` if JSON-LD is incomplete)

### None of the existing types work

When no existing monitor/scraper combination handles the site:

- Document what was tried and the specific failure mode
- Propose code changes with the `review-code` label
- Common cases: custom API format, non-standard pagination, client-side rendering with authentication
- See [01 — Agent Workflow: Escalating to Code Changes](./01-agent-workflow.md#escalating-to-code-changes) for the full process
