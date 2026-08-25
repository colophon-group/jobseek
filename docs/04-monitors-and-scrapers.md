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
| `eightfold` | URL-only | eightfold | Eightfold AI sitemap-backed portals |
| `join` | URL-only | nextdata | JOIN (join.com) Next.js pages |
| `phenom` | URL-only | json-ld | Phenom People career sites |
| `accenture` | Rich | skip | Accenture careers API |
| `almacareer` | Rich | skip | AlmaCareer / Capybara GraphQL boards |
| `amazon` | Rich | skip | Amazon Jobs |
| `ashby` | Rich | skip | Ashby ATS |
| `avature` | URL-only | dom | Avature static listings and map data, with streamed pagination |
| `bamboohr` | Rich | api_sniffer | BambooHR summaries plus detail API enrichment |
| `beehire` | Rich | skip | Beehire public campaign API |
| `beisen` | Rich/hybrid | skip or DOM enrichment | Beisen modern public API + legacy server-rendered listings |
| `brassring` | Rich | skip | BrassRing/Infinite Talent TGnewUI browser-session search API |
| `candidatus` | URL-only | dom | Candidatus WinDev listings with browser-resolved detail postbacks |
| `cnstaff` | Rich | skip | CNStaff paginated public career-board JSON |
| `paycom` | Rich | paycom | Paycom public preview API plus detail API enrichment |
| `jazzhr` | URL-only | jazzhr | JazzHR static listing with JSON-LD/DOM detail composition |
| `jobbank104` | URL-only | json-ld | 104 Job Bank server-rendered company listings, proxy-capable for Cloudflare challenges |
| `jobstreet` | Rich + enrichment | jobstreet | JobStreet employer-scoped public search plus GraphQL detail enrichment |
| `jobvite` | URL-only | json-ld | Jobvite static listings, including branded career-site routes |
| `pageup` | Rich + enrichment | dom | PageUp static listings with streamed total-checked pagination and DOM description enrichment |
| `adp` | Rich + enrichment | adp | ADP Workforce Now public listing API + native detail/DOCX enrichment |
| `icims` | URL-only | json-ld | iCIMS server-rendered listings with bounded pagination |
| `infoniqa` | URL-only | — | Infoniqa jobexchange CSRF/session POST pagination with employer and total validation |
| `intervieweb` | URL-only | json-ld | Intervieweb/In-recruiting HTML plus CSRF-protected POST pagination |
| `gupy` | URL-only | json-ld | Gupy single-page NextData inventory |
| `cornerstone` | Rich | skip | Cornerstone bootstrap + regional paginated search API |
| `darwinbox` | Rich | skip | Darwinbox browser-session public jobs API |
| `dayforce` | Rich | skip | Dayforce browser-context public search BFF |
| `herp` | URL-only | json-ld | HERP Hire single static requisition listing |
| `hrmos` | URL-only | json-ld | HRMOS static listings with bounded pagination |
| `bite` | URL-only | bite | b-ite.com ATS |
| `breezy` | URL-only | json-ld (+dom fallback) | Breezy HR |
| `comeet` | Rich | skip | Comeet hosted data and Careers API |
| `curately` | Rich | skip | Curately tenant-scoped public search API with complete contractor job data |
| `deel` | Rich | skip | Deel ATS |
| `dvinci` | Rich | skip | d.vinci ATS |
| `earcu` | Rich | skip | eArcu live-vacancy XML feeds on branded career sites |
| `gem` | Rich | skip | Gem ATS |
| `inploi` | Rich + enrichment | json-ld | Inploi public candidate-experience search API plus JSON-LD description enrichment |
| `greenhouse` | Rich | skip | Greenhouse ATS |
| `headhunter` | Rich | headhunter | Proxy-routed HeadHunter employer summaries plus detail API enrichment |
| `hibob` | Rich | skip | HiBob public career sites |
| `hirehive` | Rich | skip | HireHive public Jobs API |
| `hireology` | Rich | skip | Hireology ATS |
| `turbohire` | Rich | skip | TurboHire token-authenticated public career API |
| `jarvi` | Rich | skip | Jarvi public careers API embeds |
| `jobylon` | Rich | skip | Jobylon iframe embeds |
| `jobs_ch` | URL-only | json-ld | jobs.ch employer profiles and paginated public search API |
| `keka` | Rich | skip | Keka career-portal bootstrap and public rich-jobs API |
| `lever` | Rich | skip | Lever ATS |
| `linkedin` | Rich | linkedin | LinkedIn guest-job summaries plus detail enrichment |
| `manatal` | Rich | skip | Manatal public Careers Page API |
| `mokahr` | Rich | skip | Mokahr ATS |
| `paylocity` | Rich | paylocity | Paylocity embedded summaries plus detail enrichment |
| `personio` | Conditional* | — | Personio XML feed; HTML fallback needs scraper |
| `pinpoint` | Rich | skip | Pinpoint ATS |
| `practicematch` | URL-only | json-ld | Proxy-routed PracticeMatch employer listings and form pagination |
| `recruitee` | Rich | skip | Recruitee ATS |
| `recruiterbox` | URL-only | json-ld | Recruiterbox / Trakstar Hire server-rendered listings |
| `taleo` | URL-only | json-ld | Taleo Business Edition total/cursor static listings |
| `rippling` | URL-only | rippling | Rippling ATS |
| `rss` | Rich/hybrid | skip or DOM enrichment | RSS feeds plus native legacy SuccessFactors DWR listings |
| `seamlesshiring` | Rich | skip | SeamlessHiring public candidate API |
| `smartrecruiters` | URL-only | smartrecruiters | SmartRecruiters ATS |
| `softgarden` | URL-only | json-ld | Softgarden ATS |
| `traffit` | Rich | skip | Traffit ATS |
| `typify` | Rich + enrichment | json-ld | Typify function-partitioned vacancy API plus JSON-LD description enrichment |
| `ukg` | Rich | embedded | UKG Pro public paginated search API plus embedded detail enrichment |
| `welcometothejungle` | Rich | skip | Welcome to the Jungle public jobs APIs |
| `workable` | URL-only | workable | Workable ATS |
| `workday` | URL-only | workday | Workday ATS |
| `ycombinator` | URL-only | json-ld | YC Jobs fallback pages |
| `notion` | URL-only | — | Public Notion job pages/databases |
| `oracle_hcm` | Rich | oracle_hcm | Oracle HCM listings plus description enrichment |
| `recruiter_co_kr` | Rich | skip | Recruiter.co.kr ATS |
| `umantis` | URL-only | — | Umantis server-rendered listings |
| `nextdata` | Conditional* | skip/— | Embedded JSON / Next.js data; rich when `fields` is configured |
| `talemetry` | URL-only | json-ld | Talemetry / Jobvite Career Sites with fail-closed result-range pagination |
| `talentbrew` | URL-only | json-ld | TalentBrew / Radancy search pages |
| `njoyn` | URL-only | — | Njoyn XWeb listings with session-bound form pagination |
| `sitemap` | URL-only | — | Site has an XML sitemap with job URLs |
| `inline` | Rich | skip | Single-page inline job listings |
| `kipt` | Rich | skip | NSC KIPT active PDF vacancy bulletins |
| `api_sniffer` | Conditional* | skip/— | XHR/fetch capture; rich when `fields` is configured |
| `dom` | Conditional* | — | Last resort — link extraction, or partial rich static listing rows with `rich_rows` |

Rich monitors return complete job data in a single request — no scraper needed. URL-only monitors with auto-scrapers need no manual scraper selection; the scraper is configured automatically. Monitors marked "—" require manual scraper selection. Conditional monitors return rich data only under the condition named in the table; otherwise they need a scraper or runtime coverage check.

`headhunter`, `jobstreet`, `linkedin`, and `paylocity` are partial-rich exceptions: their
listing responses provide clean summary fields while their auto-configured
scrapers hydrate the remaining detail fields on the daily schedule.

LinkedIn preserves validated title-bearing paths by default for compatibility
with persisted source URLs. New boards may opt into stable numeric
`/jobs/view/{id}` identities with `canonical_numeric_job_urls: true`; existing
boards require an ID-preserving migration before enabling it. When a regional
provider owns one country, `source_ownership_excluded_country_codes` accepts
exact ISO-3166 alpha-3 codes. The monitor resolves LinkedIn's English country
field exactly and fails closed if the field is missing, unknown, or ambiguous.

### rss / SuccessFactors

Modern SuccessFactors Career Site Builder boards keep the existing
`{"preset":"successfactors","variant":"feed"}` path and stream the
first-party `/googlefeed.xml` response. Legacy shared SAP boards use the same
`rss` monitor with `variant: "legacy"`, a strict `host` + case-sensitive
`company` identity, and native static DWR pagination. DWR response text is
parsed as a restricted declaration/assignment graph and is never evaluated as
JavaScript. Counts, page envelopes, IDs, detail hosts, and page overlap must
all agree; malformed or partial responses fail the run.

Legacy listing batches are marked hybrid and automatically enrich only the
description through the static DOM scraper scoped to `.joqReqDescription`.
This preserves already-scraped content on touched rows while keeping the
hourly monitor free of N+1 detail requests. SAP legacy and modern shared hosts
use normal ATS throttling and do not require the browser worker.

### api_sniffer

`api_sniffer` has two distinct transports. A config with `api_url` uses plain
HTTP by default; set `browser: true` only when the request needs page cookies
or browser execution. A config without `api_url` uses Playwright to discover
XHR/fetch responses from the board page. The top-level endpoint key must be
`api_url`: the legacy key `url` is ignored by runtime code and rejected by CSV
validation. Set `proxy: true` when the direct API blocks Hetzner egress; the
per-board HTTP client then uses the configured proxy provider without moving
the monitor back to the browser queue.

When a configured `api_url` overlaps authoritative regional boards,
`item_filter` can partition the completed request/replay item list by exact
scalar/list field values and deduplicate the retained partition by a complete
non-empty compound provider identity. Items missing any identity part remain
distinct. Filtering runs after pagination and preserves an incomplete upstream
total, so it cannot turn a short response into an authoritative complete cycle.
Auto-discovery configs reject `item_filter` instead of silently ignoring it.

If browser auto-discovery times out and leaves no usable document body,
fallback interactions fail the monitor cycle with a stable error. They do not
turn the navigation failure into an authoritative empty result. An API
response captured before the DOM became unavailable remains usable.

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
4. Recursively handle sitemap indexes by finding job-related child sitemaps

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

Link extraction from career pages. By default (``render: false``) fetches via static HTTP and parses `<a>` tags. Set `render: true` to render with Playwright for JS-heavy SPAs. Static, single-page listings can opt into strict partial-rich row extraction when their cards expose stable titles and location components.

**Config**:
```json
{
  "render": false,
  "rich_rows": {
    "row_selector": ".job",
    "link_selector": ".job-title a",
    "location_selectors": [".job-location", ".job-country"]
  }
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
| `rich_rows` | No | Strict static row selectors for URL/title and optional joined location components; incompatible with rendering and pagination |

Link discovery filters `<a href>` URLs containing job-related keywords (job, career, position, posting, opening, role, vacancy).
With `rich_rows`, anchor text is the title and each configured location selector
must match every row; missing rows or fields fail closed. Because this mode is
partial-rich and does not extract descriptions, it requires a real detail
scraper (not `skip`) whose config includes `"enrich": ["description"]`.
Enrichment fills the detail-only field without overwriting listing values.
Oleeo/TalentLink vacancy boards use a provider preset that accepts their
authoritative empty state and limits discovery to same-origin `/opp/` detail
links, excluding board-switcher, event, and talent-bank navigation.

**Returns**: URL set by default, or partial rich jobs with `rich_rows`. Both
paths need a scraper; `rich_rows` specifically requires description enrichment.

**When to use**: Only when no API monitor, sitemap, or nextdata monitor is available. The agent should exhaust all other options first.

---

## Scrapers

A scraper takes a job page URL and returns structured job data. Only needed when the monitor returns URL-only results.

### Scraper Types

| Type | Fetch mode | How it works |
|------|-----------|-------------|
| `adp` | Static | Fetches ADP Workforce Now detail records and DOCX job-description attachments |
| `api_sniffer` | Playwright | Captures XHR/fetch API responses on job detail pages |
| `bite` | Static | Fetches BITE detail JSON |
| `dom` | Static or Playwright | Step-based extraction engine |
| `eightfold` | Static | JSON-LD extraction with Eightfold position API fallback |
| `embedded` | Static | Extracts from embedded JSON/JS data in page source |
| `headhunter` | Static | Fetches proxy-routed HeadHunter vacancy detail JSON |
| `jobstreet` | Static | Fetches JobStreet vacancy detail GraphQL data |
| `json-ld` | Static | Parses `<script type="application/ld+json">` |
| `linkedin` | Static | Fetches LinkedIn public guest-job detail fragments |
| `mokahr` | Static | Fetches and decrypts Mokahr detail API records |
| `nextdata` | Static or Playwright | Extracts from Next.js `__NEXT_DATA__` props |
| `notion` | Static | Loads Notion blocks through Notion's internal API |
| `onlyfy` | Static | Fetches Onlyfy/Prescreen server-rendered candidate pages |
| `oracle_hcm` | Static | Fetches Oracle HCM detail REST responses |
| `paycom` | Static | Bootstraps a Paycom portal and fetches its regional detail API |
| `paycor` | Static | Parses Paycor/Newton server-rendered detail fields |
| `jazzhr` | Static | Parses JobPosting JSON-LD with a DOM fallback for older JazzHR themes |
| `paylocity` | Static | Parses Paylocity server-rendered detail pages |
| `pdf` | Static | Downloads PDFs and extracts text content |
| `phuketall` | Static | Parses PhuketAll employer job pages from an exact HTTPS provider identity under a 2 MiB response cap, including canonical Thai field labels |
| `rippling` | Static | Fetches Rippling detail API records |
| `skip` | No fetch | Explicit no-scrape marker for rich monitors that already returned complete job data |
| `smartrecruiters` | Static | Fetches SmartRecruiters detail API records |
| `taleo` | Static | Parses the bounded `api.fillList` payload embedded in Taleo Enterprise detail pages |
| `veryeast` | Static | Parses every VeryEast description section and Chinese structured field from detail pages under a 2 MiB response cap |
| `workable` | Static | Fetches Workable detail API records |
| `workday` | Static | Fetches Workday detail API records |

> **Note:** API monitors (ashby, greenhouse, lever, etc.) return full job data directly — no scraper is needed. The `scraper_type` column is left empty for these, or set to `skip` when an explicit no-scrape marker is useful.

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
| `wait_for` | `selector`, `state` (default `visible`) | Wait until the first matching element reaches a Playwright locator state |
| `remove` | `selector` | Remove all elements matching the CSS selector from the DOM |
| `wait` | `ms` (default 1000) | Wait for a fixed duration |
| `evaluate` | `script` | Run arbitrary JavaScript on the page |

Example:
```json
{
  "actions": [
    {"action": "dismiss_overlays"},
    {"action": "click", "selector": "button.load-more"},
    {"action": "wait_for", "selector": "article.job"},
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
| `src/core/monitors/headhunter.py` | Proxy-routed HeadHunter employer API monitor |
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
