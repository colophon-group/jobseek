# Monitors and Scrapers

Monitors discover which jobs exist on a board. Scrapers extract details from individual job pages. Together they form the data pipeline.

## Monitors

A monitor takes a board config and returns either **full job data** (API monitors) or **URL sets** (page monitors).

### Monitor Types (ordered by cost)

| Type | Cost | Returns | When to Use |
|------|------|---------|-------------|
| `greenhouse` | Low | Full job data | Board is powered by Greenhouse |
| `lever` | Low | Full job data | Board is powered by Lever |
| `sitemap` | Medium | URL set | Site has an XML sitemap with job URLs |
| `discover` | High | URL set | JS-rendered SPA, no sitemap, no API |

Always prefer cheaper monitors. API monitors (greenhouse, lever) are the best case — they return complete job data in a single request, no scraper needed.

### greenhouse

Fetches from the Greenhouse public JSON API.

**API**: `GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true`

**Detection**: Three tiers:
1. Direct URL match (`boards.greenhouse.io/{token}`)
2. Page HTML scan for Greenhouse API references in inline JS
3. Slug-based API probe (derive slug from domain, hit the API)

**Config**:
```json
{"token": "stripe"}
```

The token is the board identifier. For direct Greenhouse URLs it's extracted from the URL. For custom domains, detection finds it in the page HTML or probes by company slug.

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

### discover

Playwright-based auto-discovery for JS-heavy career pages. This is the most expensive monitor type — it launches a headless browser, intercepts network requests, scores JSON responses to find the job list API, and paginates.

**Config**:
```json
{"wait": "networkidle", "pagination": "offset"}
```

**Returns**: URL set only. Needs a scraper to extract job details.

**When to use**: Only when no API monitor or sitemap is available. The agent should exhaust all other options first.

---

## Scrapers

A scraper takes a job page URL and returns structured job data. Only needed when the monitor returns URL-only results.

### Scraper Types

| Type | Needs fetch? | How it works |
|------|-------------|-------------|
| `greenhouse_api` | No | Data comes from monitor (passthrough) |
| `lever_api` | No | Data comes from monitor (passthrough) |
| `json-ld` | Yes (static) | Parses `<script type="application/ld+json">` |
| `html` | Yes (static) | CSS selectors → field mapping |
| `browser` | Yes (Playwright) | Renders JS, then extracts via selectors |

### greenhouse_api / lever_api

Passthrough scrapers — the monitor already provides full job data. No additional fetching needed.

### json-ld

Parses [schema.org/JobPosting](https://schema.org/JobPosting) JSON-LD from the page HTML. Many modern career sites embed this for SEO.

**Config**:
```json
{}
```

No config needed — the extractor handles all standard JobPosting fields automatically:
- `title` → title
- `description` → description (HTML)
- `jobLocation` → locations (handles single object or array)
- `baseSalary` → salary (currency, min, max, unit)
- `employmentType` → employment type
- `jobLocationType` → remote/hybrid/onsite
- `qualifications` / `skills` / `responsibilities` → lists
- `datePosted` / `validThrough` → dates

**When to use**: Try this first for any sitemap-discovered board. Many sites (Meta, LinkedIn, Indeed, Workable-powered) embed JSON-LD. Use `uv run python -m src.validate --probe-jsonld <url>` to check.

### html

CSS selector-based extraction for sites without JSON-LD.

**Config**:
```json
{
  "title": "h2.p1N2lc",
  "location": "span.vo5qdf",
  "description": "h3:contains('About') ~ p",
  "qualifications": "h3:contains('Minimum qualifications') ~ li",
  "responsibilities": "h3:contains('Responsibilities') ~ li"
}
```

Each key is a job field, each value is a CSS selector. The scraper fetches the page (static HTTP), parses the HTML, and extracts text from matching elements.

**When to use**: When the site has no JSON-LD but has a consistent HTML structure. The agent determines the right selectors during the research phase.

### browser

Playwright-based extraction for JS-heavy sites that don't render job details server-side.

**Config**:
```json
{
  "wait": "networkidle",
  "title": "[data-testid='job-title']",
  "location": "[data-testid='location']"
}
```

Same as `html` but launches a headless browser first to render JavaScript. The `wait` field controls when extraction starts (`networkidle`, `domcontentloaded`, or a specific selector).

**When to use**: Only when static HTML extraction fails. Common for Ashby, Workday, and Workable-powered sites.

---

## Choosing the Right Config

Decision tree for agents:

```
1. Is the board URL on greenhouse.io or detected as Greenhouse?
   → monitor: greenhouse, scraper: greenhouse_api

2. Is the board URL on lever.co or detected as Lever?
   → monitor: lever, scraper: lever_api

3. Does the site have an XML sitemap with job URLs?
   a. Do individual job pages have JSON-LD?
      → monitor: sitemap, scraper: json-ld
   b. Do job pages have consistent HTML structure?
      → monitor: sitemap, scraper: html

4. None of the above?
   a. Do job pages render without JS?
      → monitor: discover, scraper: json-ld or html
   b. Job pages need JS to render?
      → monitor: discover, scraper: browser
```

## Existing Code

Monitor implementations are adapted from the current crawler:

| New location | Source |
|-------------|--------|
| `src/core/monitors/greenhouse.py` | `src/monitor/crawler_types/greenhouse.py` |
| `src/core/monitors/lever.py` | `src/monitor/crawler_types/lever.py` |
| `src/core/monitors/sitemap.py` | `src/monitor/crawler_types/sitemap.py` |
| `src/core/monitors/discover.py` | `scripts/discover_jobs.py` (promoted) |
| `src/core/scrapers/jsonld.py` | `examples/flatten_url.py::extract_jsonld()` (promoted) |

---

## Troubleshooting

### Monitor returns fewer jobs than expected

1. Check if the website shows a total job count (e.g. "Showing 247 open positions")
2. `sitemap` monitor: the sitemap may not include all job URLs
   → Try `discover` monitor as fallback
3. `greenhouse`/`lever`: API may require a different token
   → Try alternative slugs derived from the URL or page HTML
4. `discover` monitor: pagination may not be working
   → Adjust pagination config (`offset` vs `page-number`)

### Monitor returns zero jobs

1. Verify the board URL is correct and loads in a browser
2. For `greenhouse`/`lever`: verify the token is correct (try hitting the API directly)
3. For `sitemap`: verify the sitemap contains job URLs (not just pages)
4. For `discover`: the page may need a specific wait strategy or user interaction

### Scraper extracts empty or wrong fields

1. `json-ld`: verify JSON-LD exists (`--probe-jsonld`) — some pages have partial JSON-LD that's missing fields
2. `html`: selectors may be wrong — inspect the page HTML, try different selectors
3. `browser`: page may need longer wait time or specific interaction
4. Consider switching scraper type (e.g. `json-ld` → `html` if JSON-LD is incomplete)

### None of the existing types work

When no existing monitor/scraper combination handles the site:

- Document what was tried and the specific failure mode
- Propose code changes with the `review-code` label
- Common cases: custom API format, non-standard pagination, client-side rendering with authentication
- See [01 — Agent Workflow: Escalating to Code Changes](./01-agent-workflow.md#escalating-to-code-changes) for the full process
