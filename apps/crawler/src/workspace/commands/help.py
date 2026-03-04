"""ws help — on-demand reference docs for monitors, scrapers, and config."""

from __future__ import annotations

import click

# ── Topic text constants ─────────────────────────────────────────────────

INDEX = """\
Usage: ws help <topic>

Available topics:
  monitors          Monitor type overview + decision tree
  scrapers          Scraper type overview + field importance
  monitor <type>    Per-type reference (greenhouse, lever, ashby, sitemap, dom, ...)
  scraper <type>    Per-type reference (json-ld, nextdata, embedded, dom, api_sniffer)
  fields            Job data fields — types, formats, importance
  steps             DOM scraper step key reference
  actions           Browser action pipeline
  artifacts         Debug artifacts saved by ws commands
  troubleshooting   Common failures + what to try

Commands:
  ws probe monitor   Probe all monitor types for active board
  ws probe scraper   Probe all scraper types against sample URLs"""

MONITORS = """\
Monitor Types (cheapest first):

  Type              Cost    Returns         Scraper needed?
  ────────────────────────────────────────────────────────
  ashby             10      Full job data   No (skipped)
  greenhouse        10      Full job data   No (skipped)
  hireology         10      Full job data   No (skipped)
  lever             10      Full job data   No (skipped)
  recruitee         10      Full job data   No (skipped)
  rippling          10      Full job data   No (skipped)
  smartrecruiters   10      Full job data   No (skipped)
  workable          10      Full job data   No (skipped)
  workday           10      Full job data   No (skipped)
  pinpoint          10      Full job data   No (skipped)
  personio          10      Full job data   No (skipped)
  successfactors    10      Full job data   No (skipped)
  nextdata          20      URLs or full    If URL-only
  sitemap        50      URL set         Yes
  api_sniffer    80      URLs or full    If URL-only (no fields)
  dom            100     URL set         Yes

Decision tree (after ws probe monitor):
  1. Detected known ATS API (greenhouse/lever/ashby/etc)?  → Use it (no scraper needed)
  2. Detected nextdata?                → monitor: nextdata
  3. Detected sitemap?                 → monitor: sitemap, scraper: json-ld (or embedded)
  4. Detected api_sniffer?             → Use it (check if fields auto-mapped)
  5. Nothing detected?                 → monitor: dom, scraper: dom

All monitors support url_filter to include/exclude URLs by regex:
  "url_filter": "/jobs/"                          Include only
  "url_filter": {"include": "/jobs/", "exclude": "/blog/"}

  ws probe monitor                  Run monitor probe
  ws help monitor <type>            Detailed config reference
  ws help scrapers                  Scraper overview"""

SCRAPERS = """\
Scraper Types:

  Type           Fetch       Config needed?   Best for
  ───────────────────────────────────────────────────────────
  json-ld        Static      No               Sites with schema.org/JobPosting
  nextdata       Static/PW   Yes (fields)     Next.js sites with __NEXT_DATA__
  embedded       Static/PW   Yes (fields)     JS-embedded JSON (script tags, variables)
  dom            Static/PW   Yes (steps)      Custom HTML structure
  api_sniffer    Playwright  Optional (fields)  SPA/XHR job pages

  API monitors (greenhouse, lever, ashby, recruitee, rippling, workday, pinpoint,
  personio, successfactors) skip the scraper step entirely.
  api_sniffer scraper is auto-probed via Playwright in ws probe scraper.

  Probe first: ws probe scraper tries all types automatically against
  sample URLs. Heuristic configs are a starting point — refine based
  on probe quality stats.

  Try json-ld first — many sites embed JobPosting structured data for SEO.
  If json-ld returns empty fields, check page source for embedded JSON data
  (script tags, JS variables) → try embedded scraper. Fall back to dom last.

Field importance:
  Required     title — every job must have a title
  Required     description — HTML fragment, needed for display
  Important    locations — most jobs have at least one
  Important    job_location_type — "Remote", "Hybrid", "On-site"
  Optional     employment_type, date_posted, base_salary, skills,
               qualifications, responsibilities, valid_through

  Titles and descriptions must be N/N — 0/N on either = do not submit.
  Missing locations acceptable only if job_location_type is set (remote-only).
  See: ws help fields                  Full field reference

  ws probe scraper                  Run scraper probe
  ws help scraper <type>            Detailed config reference
  ws help steps                     DOM scraper step format"""

MONITOR_GREENHOUSE = """\
greenhouse — Greenhouse Public API

  API:      GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  Returns:  Full job data (title, HTML description, locations, date_posted)
            metadata: departments, education, requisition_id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs

  Config:
    {"token": "stripe"}

    token    Board identifier. Auto-filled by ws probe from:
             1. Direct URL (boards.greenhouse.io/{token})
             2. Inline JS scan for Greenhouse API references
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Greenhouse API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_HIREOLOGY = """\
hireology — Hireology Careers API

  API:      GET https://api.hireology.com/v2/public/careers/{slug}?page_size=500
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted)
            metadata: organization, job_family, id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     Single API call for most boards (page_size=500)

  Config:
    {"slug": "bristolhonda"}

    slug     Careers path slug. Auto-filled by ws probe from:
             1. Direct URL (careers.hireology.com/{slug})
             2. New domain ({slug}.hireology.careers)
             3. Inline HTML scan for Hireology API references
             4. Slug-based API probe

  Detection:  ws probe shows "Hireology API — slug: X, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser"""

MONITOR_LEVER = """\
lever — Lever Postings API

  API:      GET https://api.lever.co/v0/postings/{token}?limit=100&skip=N
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, base_salary)
            metadata: team, department, id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Rate:     0.5s sleep between pagination batches of 100

  Config:
    {"token": "cloudflare"}

    token    Company slug. Auto-filled by ws probe from:
             1. Direct URL (jobs.lever.co/{token})
             2. Inline JS scan for Lever API references
             3. Slug-based API probe

  Detection:  ws probe shows "Lever API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_SITEMAP = """\
sitemap — XML Sitemap Parser

  Returns:  URL set only (needs scraper)
  Cap:      10,000 URLs

  Config:
    {"sitemap_url": "https://example.com/jobs/sitemap.xml"}

    sitemap_url  Optional. If omitted, auto-discovers by:
                 1. Walking up the board URL path trying sitemap.xml at each level
                 2. Trying non-standard paths (/sitemaps/sitemapIndex, etc.)
                 3. Parsing robots.txt for Sitemap: directives
                 4. Resolving sitemap indexes (prefers job-related children)
                 Discovered URL is cached in board metadata for future runs.

  url_filter   Regex filter for discovered URLs (all monitors):
                 String: include pattern — "url_filter": "/jobs/"
                 Dict:   include + exclude —
                   "url_filter": {"include": "/jobs/", "exclude": "/blog/"}

  Detection:     ws probe shows "Sitemap — N URLs at <url>"
  Fewer URLs?    Sitemap may not list all job pages — try dom monitor
  UTM params:    Automatically stripped from discovered URLs

  Pair with:     json-ld (try first) or dom scraper"""

MONITOR_NEXTDATA = """\
nextdata — Next.js __NEXT_DATA__ Discovery

  Returns:  URL set (default) or full job data (if fields configured)
  Cap:      10,000 items

  Config (minimal — URL-only mode):
    {"path": "props.pageProps.positions", "url_template": "https://example.com/jobs/{id}"}

  Config (rich mode — full job data):
    {
      "path": "props.pageProps.positions",
      "url_template": "https://example.com/jobs/{id}",
      "fields": {"title": "name", "locations": "offices[].name"},
      "slug_fields": ["title"]
    }

    path          Dot-notation path to jobs array in __NEXT_DATA__ JSON
    url_template  URL template with {field_name} placeholders from each item
                  Special: {slug} built by slugifying + joining slug_fields
    fields        Dict mapping DiscoveredJob fields to item field paths
                  Supports dot notation (a.b.c), array index (a[0].b),
                  array wildcard (a[].b — extracts from all items)
    slug_fields   List of item fields to slugify + join for {slug} variable
    render        If true, use Playwright to render page (default: false)
    actions       Browser action pipeline (auto-enables render)
    url_filter    Regex filter for discovered URLs (see: ws help monitor sitemap)

  Detection:  ws probe shows "__NEXT_DATA__ — N items at <path>"
              If "(render)" shown, page needs Playwright to load data.
              Auto-searches common paths: props.pageProps.positions,
              props.pageProps.jobs, props.pageProps.openings,
              props.pageProps.allJobs, props.pageProps.data.positions,
              props.pageProps.data.jobs. Needs >= 5 items (all dicts).

  Pair with:  nextdata or json-ld scraper (if URL-only mode)

  Tip: Inspect nextdata.json artifact to see all available keys in each
  item before choosing your fields mapping. Map employment_type, date_posted,
  job_location_type, team/department if present — they come at no extra cost."""

MONITOR_DOM = """\
dom — Link Extraction (fallback)

  Returns:  URL set only (needs scraper)
  Cap:      10,000 URLs
  Cost:     Highest — use only as last resort.

  Config:
    {"render": true, "wait": "networkidle", "timeout": 30000}

    render       false (default) = static HTTP, true = Playwright
    wait         Wait strategy: "load" | "domcontentloaded" | "networkidle" (default) | "commit"
    timeout      Navigation timeout in ms (default: 30000)
    user_agent   Custom User-Agent string
    headless     Run headless (default: true)
    actions      Browser action pipeline (see: ws help actions)
    url_filter   Regex filter for discovered URLs (see: ws help monitor sitemap)

  Discovery:   Extracts all <a href> links, filters for URLs containing
               job/career/position/posting/opening/role/vacancy keywords.

  Detection:   ws probe checks static HTML for job links.
               If detected: shows "✓ N URLs". If not: shows "✗ Not detected".

  Pair with:   json-ld (try first) or dom scraper"""

MONITOR_ASHBY = """\
ashby — Ashby Job Board API

  API:      POST https://jobs.ashbyhq.com/api/non-user-graphql
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary)
            metadata: team, department, id
  Scraper:  Not needed (API returns full data, scraper step is skipped)

  Config:
    {"token": "company-slug"}

    token    Board identifier (company slug). Auto-filled by ws probe from:
             1. Direct URL (jobs.ashbyhq.com/{token})
             2. Inline JS scan for Ashby API references

  Detection:  ws probe shows "Ashby API — token: X, N jobs"
  Zero jobs?  Verify token — check the board URL is correct"""

MONITOR_RECRUITEE = """\
recruitee — Recruitee Careers Site API

  API:      GET https://{slug}.recruitee.com/api/offers
            GET https://{custom-domain}/api/offers  (custom domains)
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary)
            metadata: department, tags, category, id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     Single API call — no pagination needed

  Config:
    {"slug": "acme"}               # Standard domain
    {"api_base": "https://jobs.acme.com"}  # Custom domain

    slug       Company slug for {slug}.recruitee.com. Auto-filled by ws probe from:
               1. Direct URL ({slug}.recruitee.com)
               2. Inline HTML scan for recruitee markers
               3. Slug-based API probe (derives slug from domain)
    api_base   Full base URL for custom domains. Auto-filled when detected
               via HTML scan (e.g. karriere.herta.de → https://karriere.herta.de).

  Detection:  ws probe shows "Recruitee API — {slug}, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser
  Custom domains:  Recruitee supports custom domains (e.g. karriere.herta.de).
                   The API is at https://{custom-domain}/api/offers."""

MONITOR_SMARTRECRUITERS = """\
smartrecruiters — SmartRecruiters Posting API

  API:      GET https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset=0
            GET https://api.smartrecruiters.com/v1/companies/{token}/postings/{id}  (detail)
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary)
            metadata: department, function, experienceLevel
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     N+1 API calls (1 list + N detail requests, concurrency=10)

  Config:
    {"token": "smartrecruiters"}

    token    Company identifier. Auto-filled by ws probe from:
             1. Direct URL (jobs.smartrecruiters.com/{token})
             2. Inline JS scan for SmartRecruiters API references
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "SmartRecruiters API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_RIPPLING = """\
rippling — Rippling ATS Job Board API

  API:      GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
            GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}  (detail)
  Returns:  Full job data (title, HTML description, locations, employment_type,
            date_posted, base_salary)
            metadata: department, base_department, company
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     N+1 API calls (1 list + N detail requests, concurrency=10)

  Config:
    {"slug": "rippling"}

    slug     Board slug. Auto-filled by ws probe from:
             1. Direct URL (ats.rippling.com/{slug}/jobs)
             2. Inline HTML scan for Rippling ATS references
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Rippling API — slug: X, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser"""

MONITOR_PINPOINT = """\
pinpoint — Pinpoint HQ Postings API

  API:      GET https://{slug}.pinpointhq.com/postings.json
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, base_salary)
            metadata: department, division, requisition_id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     Single API call — returns all jobs, no pagination

  Config:
    {"slug": "workwithus"}

    slug     Company subdomain. Auto-filled by ws probe from:
             1. Direct URL ({slug}.pinpointhq.com)
             2. Inline HTML scan for pinpointhq.com references
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Pinpoint API — slug: X, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser"""

MONITOR_PERSONIO = """\
personio — Personio Public XML Feed

  API:      GET https://{slug}.jobs.personio.de/xml?language=en
  Returns:  Full job data (title, HTML description, locations, employment_type,
            date_posted)
            metadata: department, subcompany, recruitingCategory, seniority,
            yearsOfExperience, occupation, occupationCategory, keywords
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     Single XML request — returns all jobs, no pagination

  Config:
    {"slug": "sennder"}

    slug     Company subdomain. Auto-filled by ws probe from:
             1. Direct URL ({slug}.jobs.personio.de)
             2. Inline HTML scan for jobs.personio.de references
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Personio XML — slug: X, N jobs"
  Zero jobs?  Verify slug — try the XML URL directly in a browser"""

MONITOR_SUCCESSFACTORS = """\
successfactors — SAP SuccessFactors Career Site Builder (CSB)

  Feed:     GET https://{domain}/googlefeed.xml
  Format:   RSS 2.0 with Google Base namespace extensions
  Returns:  Full job data (title, HTML description, locations)
            metadata: id, employer, job_function, expiration_date
  Scraper:  Not needed (feed returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     Single request — returns all jobs, no pagination

  Config:
    {"feed_url": "https://jobs.sap.com/googlefeed.xml"}

    feed_url   RSS feed URL. Auto-filled by ws probe.
               Derived from board URL: {origin}/googlefeed.xml

  Detection:  ws probe shows "SuccessFactors RSS — {feed_url}, N jobs"
              Detected via page HTML markers (successfactors.eu, rmkcdn CDN,
              jobs2web.com references) or blind /googlefeed.xml probe.
  Zero jobs?  Verify the feed URL directly in a browser.
              Some sites serve /sitemap.xml as a proper sitemap — use
              /googlefeed.xml which is always the RSS feed."""

MONITOR_WORKABLE = """\
workable — Workable Posting API

  API:      POST https://apply.workable.com/api/v3/accounts/{token}/jobs
            GET  https://apply.workable.com/api/v2/accounts/{token}/jobs/{shortcode}  (detail)
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted)
            metadata: department
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     N+1 API calls (1 list + N detail requests, concurrency=10)
            List endpoint uses cursor pagination (token in POST body)

  Config:
    {"token": "neowork"}

    token    Company slug. Auto-filled by ws probe from:
             1. Direct URL (apply.workable.com/{token})
             2. Inline HTML scan for Workable references
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Workable API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_WORKDAY = """\
workday — Workday Job Board API

  API:      POST https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs
            GET  https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/job/{externalPath}
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted)
            metadata: jobReqId
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     N+1 API calls (1 list + N detail requests, concurrency=10)
            Max page size is 20 (API returns 400 for higher values)
            API caps results at 2000 per query — automatically splits by
            facet (e.g. job category) for companies with >2000 listings

  Config:
    {"company": "nvidia", "wd_instance": "wd5", "site": "NVIDIAExternalCareerSite"}

    company       Company subdomain. Auto-filled by ws probe from:
                  1. Direct URL ({company}.wd{N}.myworkdayjobs.com/{site})
                  2. Inline HTML scan for Workday markers
    wd_instance   Workday instance (e.g. wd1, wd5). Auto-filled from URL.
    site          Career site identifier. Auto-filled from URL path.

  URL format:   https://{company}.wd{N}.myworkdayjobs.com/{site}
                May include locale prefix: /en-US/{site} (stripped automatically)

  Detection:  ws probe shows "Workday API — {company}/{site}, N jobs"
  Zero jobs?  Verify URL — try the list API URL directly in a browser"""

MONITOR_API_SNIFFER = """\
api_sniffer — XHR/Fetch API Capture (Playwright)

  Captures JSON API responses during page load via Playwright.
  Works for React SPAs, custom platforms, and any site that
  loads job data via internal JSON APIs.

  Returns:  Full job data (if fields auto-mapped) or URL set
  Cost:     80 — between sitemap (50) and dom (100)
  Requires: Playwright

  Config (auto-filled from ws probe monitor):
    {
      "api_url": "https://example.com/api/jobs",
      "method": "GET",
      "json_path": "results.jobs",
      "url_field": "url",
      "url_template": "https://example.com/jobs/{id}",
      "pagination": {
        "param_name": "offset",
        "style": "offset",
        "start_value": 0,
        "increment": 20,
        "location": "query"
      },
      "fields": {
        "title": "title",
        "description": "bodyHtml",
        "locations": "offices[].name",
        "employment_type": "type",
        "metadata.team": "department"
      }
    }

    api_url          Captured API endpoint URL (auto-filled)
    method           HTTP method: GET or POST (auto-filled)
    json_path        Dot-notation path to jobs array in response
    url_field        Field name containing job URL (if found)
    url_template     URL pattern with {field} placeholders (from DOM cross-ref)
    params           Query parameters merged into api_url at request time.
                     Auto-filled from the captured URL (empty and pagination
                     params stripped). Edit result_limit / per_page here to
                     increase page size, and update pagination.increment to match.
    request_headers  Cleaned request headers (auto-filled)
    post_data        POST body string (for POST APIs, null for GET)
    pagination       Pagination config (auto-detected from multiple requests)
    fields           Field mapping (same spec as nextdata: key, nested.key, array[].field)
                     When present → rich mode (scraper skipped)
                     When absent → URL-only (scraper needed)
    wait             Navigation wait strategy: "load", "domcontentloaded", or
                     "networkidle". Default: "load". Use "networkidle" for sites
                     where XHRs fire late; avoid it on heavy sites (analytics/ads).
    timeout          Navigation timeout in ms. Default: 20000.
    settle           Seconds to wait after navigation for late XHRs. Default: 3.

  Modes:
    Rich (fields present):  Returns list[DiscoveredJob], scraper skipped.
      Auto-mapped from API response during probe. Verify quality —
      auto-mapping may miss fields or map wrong keys.
    URL-only (no fields):   Returns set[str], needs scraper.
      URLs derived from url_field, url_template, or DOM cross-reference.

  Detection:  ws probe shows "API sniffer — N items, total: M, score: S at <url>"
  Zero jobs?  Verify api_url is correct, check if cookies/auth context is needed
              (page is navigated first to establish cookies), check pagination config.

  Tip: After ws select monitor api_sniffer, inspect the auto-filled config.
  If fields are auto-mapped, verify their quality in ws run monitor output.
  If fields are missing or wrong, adjust the fields mapping manually or
  remove fields entirely to use URL-only mode with a scraper.

  Page size: The auto-captured api_url may use a small page size (e.g.
  result_limit=10). If the API supports larger pages, edit api_url to
  increase the limit (e.g. result_limit=100) and update pagination.increment
  to match. This reduces the number of API calls needed to fetch all jobs."""

SCRAPER_JSONLD = """\
json-ld — Schema.org JobPosting Extractor

  Fetch:    Static HTTP only (no render/actions support)
  Config:   None needed

  Parses <script type="application/ld+json"> blocks for JobPosting data.
  Handles @graph arrays and nested structures automatically.
  Uses the first JSON-LD block that contains a JobPosting.

  Fields extracted (from schema.org properties):
    title          ← title or name
    description    ← description (preserved as HTML if contains tags)
    locations      ← jobLocation (single or array, builds from address parts)
    employment_type ← employmentType
    job_location_type ← jobLocationType
    date_posted    ← datePosted
    valid_through  ← validThrough
    base_salary    ← baseSalary (currency/min/max/unit)
    skills         ← skills
    responsibilities ← responsibilities
    qualifications ← qualifications or educationRequirements

  When to use:  Try first for any URL-only monitor. Many career sites
                (Workable, Lever-hosted, Indeed, LinkedIn) embed JSON-LD.

  Empty fields?  Page may have partial or no JSON-LD. Try dom scraper."""

SCRAPER_NEXTDATA = """\
nextdata — Next.js __NEXT_DATA__ Page Extractor

  Fetch:    Static HTTP (or Playwright with render: true)
  Config:
    {
      "path": "props.pageProps.jobData",
      "fields": {"title": "name", "locations": "offices[].name",
                 "description": "content"}
    }

    path      Dot-notation path to job object in __NEXT_DATA__ (optional,
              uses root data if omitted)
    fields    Dict mapping JobContent fields to extraction paths:
              - Dot notation: "a.b.c"
              - Array index: "items[0].name"
              - Array wildcard: "offices[].name" (extracts from all)
              Target fields: title, description, locations, employment_type,
              job_location_type, date_posted, valid_through, qualifications,
              responsibilities, skills. Prefix with "metadata." for extras.
    render    Use Playwright (default: false)
    actions   Browser action pipeline (auto-enables render)

  When to use:  When job pages are Next.js and embed data in __NEXT_DATA__.
  Empty result? Verify path points to the right data with browser devtools.

  Tip: Before finalizing config, inspect the full nextdata.json artifact
  (saved by ws run monitor or ws probe monitor) for additional mappable fields.
  Look for employment_type, date_posted, job_location_type, team/department
  — these often exist in the raw data but aren't mapped by default."""

SCRAPER_EMBEDDED = """\
embedded — Generalized Embedded Data Extractor

  Fetch:    Static HTTP (or Playwright with render: true)
  Config:
    {
      "script_id": "app-data",
      "path": "job",
      "fields": {"title": "title", "description": "body",
                 "locations": "offices[].name"}
    }

    Data source (one of, checked in priority order):
      script_id    ID of a <script> tag containing JSON
      pattern      Regex matching up to start of JSON (e.g. AF_initDataCallback)
      variable     JS variable name (e.g. window.__DATA__)

    path      jmespath expression to navigate to job object (optional)
    fields    Dict mapping JobContent fields to jmespath expressions:
              - Named keys: "title", "category.name"
              - Array wildcard: "offices[].name"
              - Positional index: "[1]", "[9][*][2]"
              Target fields: title, description, locations, employment_type,
              job_location_type, date_posted, valid_through, qualifications,
              responsibilities, skills. Prefix with "metadata." for extras.
    render    Use Playwright (default: false)
    actions   Browser action pipeline (auto-enables render)

  When to use:  Sites with structured job data embedded in JavaScript
                that isn't Next.js __NEXT_DATA__ (use nextdata for that).
                Examples: Google Wiz (AF_initDataCallback), custom SPAs
                with window.__DATA__ assignments, or named <script> blocks.

  Empty result? Verify the data source (script_id/pattern/variable) matches
                the page content. Check path navigates to the right object.
                Use jmespath syntax for field expressions.

  Tip: nextdata scraper is syntactic sugar for embedded with
       script_id: "__NEXT_DATA__" pre-filled."""

SCRAPER_DOM = """\
dom — Step-based Extraction Engine

  Fetch:    Static HTTP (default) or Playwright (render: true)
  Config:
    {
      "steps": [
        {"tag": "h1", "field": "title"},
        {"text": "Location", "offset": 1, "field": "location"},
        {"text": "About", "field": "description", "stop": "Requirements", "html": true}
      ],
      "render": true,
      "wait": "networkidle"
    }

    steps     Extraction step list (see: ws help steps)
    render    false (default) = static HTTP, true = Playwright
    wait      Wait strategy (Playwright only): load | domcontentloaded
              | networkidle (default) | commit
    timeout   Navigation timeout in ms (default: 30000)
    user_agent  Custom User-Agent
    headless  Run headless (default: true)
    actions   Browser action pipeline (see: ws help actions)

  Target fields: title, description, locations, employment_type,
  job_location_type, date_posted, valid_through, qualifications,
  responsibilities, skills. Prefix with "metadata." for extras.

  When to use:  Sites without JSON-LD or __NEXT_DATA__, where you need
                step-based field extraction from page HTML.
  Prefer render: false when page content loads without JavaScript.

  See: ws help steps     Full step format reference
  See: ws help actions   Browser action pipeline"""

SCRAPER_API_SNIFFER = """\
api_sniffer — XHR/Fetch API Capture (single page)

  Fetch:    Playwright only (opens page, captures XHR/fetch responses)
  Config:
    {"fields": {"title": "name", "description": "content"}}

    fields    Optional. Dict mapping JobContent fields to JSON response keys.
              Same spec as nextdata: key, nested.key, array[].field.
              If omitted, auto-maps heuristically from captured response.
              Target fields: title, description, locations, employment_type,
              job_location_type, date_posted, valid_through, qualifications,
              responsibilities, skills. Prefix with "metadata." for extras.
    wait      Navigation wait strategy: "load", "domcontentloaded", or
              "networkidle". Default: "load". Use "networkidle" for sites
              where XHRs fire late; avoid it on heavy sites (analytics/ads).
    timeout   Navigation timeout in ms. Default: 20000.
    settle    Seconds to wait after navigation for late XHRs. Default: 3.

  Auto-probed via Playwright in ws probe scraper. Requires Playwright.
  Can also be manually selected: ws select scraper api_sniffer

  How it works:
    1. Opens job page with Playwright
    2. Captures all JSON responses during page load
    3. Finds the best single-job response (dict with title + description keys)
    4. Extracts fields using config mapping or heuristic matching

  When to use:  Job pages are SPAs that load content via XHR/fetch.
                Typical sign: other scrapers in ws probe scraper find nothing,
                page source is empty/minimal, but the page renders full content
                in browser.

  Empty result? The page may not load single-job data via XHR — the content
                may be embedded in the initial HTML via SSR. Try json-ld or dom."""

FIELDS = """\
Job Data Fields — types, formats, importance

  Monitors return DiscoveredJob, scrapers return JobContent. Both use the
  same core fields (all nullable). JobContent adds valid_through.

  Importance:
    Required     title             str       Plain text job title
    Required     description       str       HTML fragment (<p>, <ul>, <h3>, etc.)
    Important    locations         [str]     List of location strings
    Important    job_location_type str       "Remote", "Hybrid", "On-site"
    Optional     employment_type   str       "Full-time", "Part-time", "Contract", etc.
    Optional     date_posted       str       ISO 8601 date (YYYY-MM-DD)
    Optional     valid_through     str       ISO 8601 date (scraper only, not in DiscoveredJob)
    Optional     base_salary       dict      {currency, min, max, unit}
    Optional     skills            [str]     List of skill strings
    Optional     responsibilities  [str]     List of bullet-point strings
    Optional     qualifications    [str]     List of bullet-point strings
    Optional     metadata          dict      Arbitrary key-value pairs (team, dept, etc.)

  Notes:
    - description is always HTML, never plain text. API monitors (greenhouse,
      lever) return HTML natively. Scrapers must produce HTML too.
    - locations is a list even for single-location jobs: ["New York, NY"]
    - base_salary dict: {"currency": "USD", "min": 100000, "max": 150000, "unit": "YEAR"}
    - responsibilities and qualifications are plain-text lists (one item per
      bullet point), NOT HTML.
    - metadata is a catch-all dict for fields that don't fit the schema
      (e.g. team, department). Use "metadata.team" in dom scraper steps or
      nextdata field mappings.
    - API monitors also populate metadata: greenhouse stores departments,
      education, requisition_id; lever stores team, department, id.

  Quality checks:
    ws run monitor shows quality stats for rich data (API monitors):
      "Quality: 135/138 title, 120/138 description, 125/138 locations"
    ws run scraper shows extraction stats for scraped pages:
      "3/3 titles, 3/3 descriptions, 2/3 locations"
    Titles and descriptions must be N/N — 0/N on either = do not submit.
    Missing locations acceptable only if job_location_type is set
    (e.g. remote-only companies). Otherwise iterate on scraper config."""

STEPS = """\
Extraction Steps — DOM scraper step format

  Steps walk a flattened list of HTML elements sequentially. The cursor
  advances forward after each match. Each step finds an element and
  extracts its text into a named field.

  Step keys:
    tag         Match by HTML tag name (e.g. "h1", "li", "p")
    text        Match by substring in element text (case-insensitive)
    attr        Match by attribute: "key=substring" or "key" (presence)
    field       Output field name. Omit for anchor-only steps (move cursor)
    offset      Skip N elements after match before extracting (default: 0)
    optional    If true, skip silently when not found (default: false)
    from        Override seek start (e.g. 0 to search from beginning)

  Range extraction (collect multiple elements):
    stop        Stop when element text contains this string
    stop_tag    Stop when element tag matches
    stop_count  Max elements to collect
    html        If true, preserve HTML tags in output (groups <li> in <ul>)

  Post-processing:
    regex       Regex with capture group — extracts group(1)
    split       Split result into list on this delimiter

  Matching:
    - All conditions (tag + text + attr) must match (AND logic)
    - Text matching normalizes Unicode punctuation to ASCII
    - Cursor advances forward after each step; use "from": 0 to reset

  DOM order:
    Steps MUST follow the order elements appear in the HTML, not logical
    importance. The cursor only moves forward. If step B appears before
    step A in the DOM, step A will be silently skipped (optional) or warn
    (required). Inspect flat.json to see actual element order. Use
    "from": 0 to reset the cursor when a field is above earlier steps.
    Correct DOM order is critical for extraction completeness — wrong
    order means silently missing fields.

  Examples:
    {"tag": "h1", "field": "title"}
    {"text": "Location", "offset": 1, "field": "location"}
    {"text": "About", "field": "description", "stop": "Requirements", "html": true}
    {"tag": "li", "field": "skills", "stop_tag": "h2", "split": ","}
    {"tag": "span", "attr": "class=salary", "field": "salary", "regex": "\\\\$(\\\\d[\\\\d,]+)"}"""

ACTIONS = """\
Browser Action Pipeline — pre-extraction actions for Playwright

  Actions run sequentially after page navigation, before extraction.
  Each action has a 10s timeout (configurable per-action). Failures
  log a warning and continue.

  Used in: dom monitor, dom scraper, nextdata monitor/scraper (with render: true)

  Action types:
    {"action": "click", "selector": "button.load-more"}
        Click first matching element (no-op if not found)

    {"action": "wait", "ms": 2000}
        Wait N milliseconds (default: 1000)

    {"action": "remove", "selector": ".cookie-banner"}
        Remove all matching elements from DOM

    {"action": "evaluate", "script": "window.scrollTo(0, 99999)"}
        Execute arbitrary JavaScript

    {"action": "dismiss_overlays"}
        Remove common cookie/consent banners (8 built-in selectors)

  Per-action timeout:
    {"action": "click", "selector": ".btn", "timeout": 5}
        Override default 10s timeout (value in seconds)

  Example pipeline:
    "actions": [
      {"action": "dismiss_overlays"},
      {"action": "click", "selector": "button[data-load-all]"},
      {"action": "wait", "ms": 2000}
    ]"""

ARTIFACTS = """\
Debug Artifacts — files saved by ws commands

  All artifacts are saved under:
    .workspace/<slug>/artifacts/<board_alias>/<category>/run-<timestamp>/

  Categories: probe, scraper-probe, monitor, scraper


  ws probe monitor            → artifacts/<alias>/probe/run-<ts>/
  ─────────────────────────────────────────────────────────────────
    probe.json         Array of detection results, one per monitor type.
                       Each: {name, detected, metadata, comment}.
                       Shows which monitors detected the board and why others
                       failed. The metadata dict auto-fills config when you
                       run ws select monitor.


  ws probe scraper            → artifacts/<alias>/scraper-probe/run-<ts>/
  ─────────────────────────────────────────────────────────────────
    probe.json         Array of scraper detection results.
                       Each: {name, detected, metadata, comment}.
                       Metadata includes heuristic config and quality stats
                       (titles, descriptions, locations counts).
                       Use config from metadata to ws select scraper.


  ws run monitor              → artifacts/<alias>/monitor/run-<ts>/
  ─────────────────────────────────────────────────────────────────
    jobs.json          Discovered jobs. If monitor returns rich data: all
                       DiscoveredJob objects with full fields. If URL-only:
                       first 100 URLs as [{url: "..."}] objects.
                       Compare count against website to verify completeness.

    quality.json       Field completeness report (rich data monitors only).
                       {total, fields: {title: {count, pct}, ...}}.
                       Quick check that API data has expected fields.

    response.json      Raw API response (greenhouse/lever monitors).
                       Full JSON returned by the API. Inspect to verify
                       token, check field availability, debug parsing.

    sitemap.xml        Raw sitemap XML (sitemap monitor).
                       Inspect to verify URLs are job pages, not blog posts.

    nextdata.json      Raw __NEXT_DATA__ blob (nextdata monitor).
                       Inspect to find the correct path to the jobs array
                       and available field names for config.

    page.html          Raw board page HTML (dom monitor).
                       Inspect to find job link patterns and verify that
                       static fetch captures the content (vs needing render).

    api_sniff.json     Captured API exchanges (api_sniffer monitor).
                       Shows detected API URL, method, items found, and score.
                       Inspect to verify correct API was selected.

    http_log.json      All HTTP requests/responses with status codes and
                       headers. Debug connectivity, redirects, rate limits.

    events.jsonl       Structlog events (one JSON object per line).
                       Detailed timing, warnings, and error traces from
                       the monitor run. Check for rate-limit or timeout issues.


  ws run scraper              → artifacts/<alias>/scraper/run-<ts>/
  ─────────────────────────────────────────────────────────────────
  Default: 3 URLs randomly sampled from monitor's stored results.
  Override with: ws run scraper --url <URL> --url <URL>

    sample-0.json      Extracted job content for first sample URL.
    sample-1.json      (one file per sample URL tested)
    sample-2.json      Each: {id, url, title, description, locations, ...}.
                       Inspect to see exactly what the scraper extracted
                       and which fields are missing or malformed.

    sample-0.html      Raw page HTML for each sample URL (static HTTP fetch
    sample-1.html      before scraping). Compare against extracted data to
    sample-2.html      debug missing fields. Note: for render=true scrapers,
                       this is the static HTML — the scraper sees Playwright-
                       rendered content which may differ.

    flat.json          Flattened DOM element tree (dom scraper only).
                       Array of [{tag, text, attrs}, ...] for every element.
                       This is what walk_steps() searches through.
                       Use it to find the right tag/text/attr selectors for
                       your extraction steps. Saved once per run (contains
                       the last sample URL's data).

    quality.json       Per-URL and aggregate field completeness.
                       {total, fields: {title: {count, pct}, ...},
                        per_url: [{url, fields: {title: true, ...}}]}.
                       Pinpoints which URLs have missing data.

    http_log.json      HTTP requests/responses during scraping.
    events.jsonl       Structlog events from the scraper run.


  Notes:
    - Artifacts persist until ws del or manual deletion.
    - .workspace/ is gitignored — artifacts never get committed.
    - Each run creates a new timestamped directory, so you can compare
      successive runs when iterating on config.
    - Path is printed to stdout after each command:
      "Saved: .workspace/<slug>/artifacts/<alias>/monitor/run-20250101T120000\""""

TROUBLESHOOTING = """\
Troubleshooting:

  Monitor returns 0 jobs:
    greenhouse/lever  Verify token — open the API URL directly in browser
    sitemap           Sitemap may only have blog/page URLs, not jobs → try dom
    nextdata          Path may be wrong — check __NEXT_DATA__ in browser devtools
    dom               Try render: true, or check that links contain job keywords
    api_sniffer       Verify api_url, check if site needs cookies (board page
                      navigated first), try different board URL

  Monitor returns fewer jobs than expected:
    → Compare against website's displayed total ("Showing N positions")
    → sitemap may not list all jobs — try dom or nextdata
    → greenhouse/lever may need a different token

  Scraper extracts empty fields:
    → Start with ws probe scraper to see which types work
    json-ld     Page has partial or no JSON-LD → try embedded or dom scraper
    nextdata    Data structure differs per page → check path + fields
    embedded    Verify data source (script_id/pattern/variable) matches page →
                check path + fields with browser DevTools
    dom         Selectors don't match → inspect page HTML, adjust steps
    api_sniffer Page may not load job data via XHR — try json-ld or dom instead

  Debugging with artifacts (ws help artifacts):
    → Every ws probe monitor / ws probe scraper / ws run saves debug files
    → Monitor: inspect raw source (response.json, sitemap.xml, nextdata.json, page.html)
    → Scraper: compare sample-N.html against sample-N.json to find missing fields
    → DOM scraper: read flat.json to find correct tag/text/attr selectors for steps
    → HTTP issues: check http_log.json for status codes, redirects, rate limits
    → Artifacts path is printed after each command

  Nothing works after trying all types:
    → Document what was tried and the specific failure
    → ws del, then propose code changes on fix-crawler/ branch
    → See AGENTS.md "Escalate to Code Changes" section"""

# ── Lookup tables ────────────────────────────────────────────────────────

MONITOR_CARDS: dict[str, str] = {
    "greenhouse": MONITOR_GREENHOUSE,
    "hireology": MONITOR_HIREOLOGY,
    "lever": MONITOR_LEVER,
    "ashby": MONITOR_ASHBY,
    "recruitee": MONITOR_RECRUITEE,
    "rippling": MONITOR_RIPPLING,
    "smartrecruiters": MONITOR_SMARTRECRUITERS,
    "workable": MONITOR_WORKABLE,
    "workday": MONITOR_WORKDAY,
    "pinpoint": MONITOR_PINPOINT,
    "personio": MONITOR_PERSONIO,
    "successfactors": MONITOR_SUCCESSFACTORS,
    "sitemap": MONITOR_SITEMAP,
    "nextdata": MONITOR_NEXTDATA,
    "dom": MONITOR_DOM,
    "api_sniffer": MONITOR_API_SNIFFER,
}

SCRAPER_CARDS: dict[str, str] = {
    "json-ld": SCRAPER_JSONLD,
    "nextdata": SCRAPER_NEXTDATA,
    "embedded": SCRAPER_EMBEDDED,
    "dom": SCRAPER_DOM,
    "api_sniffer": SCRAPER_API_SNIFFER,
}

TOPIC_MAP: dict[str, str] = {
    "monitors": MONITORS,
    "scrapers": SCRAPERS,
    "fields": FIELDS,
    "steps": STEPS,
    "actions": ACTIONS,
    "artifacts": ARTIFACTS,
    "troubleshooting": TROUBLESHOOTING,
}


# ── Click command ────────────────────────────────────────────────────────


@click.command("help")
@click.argument("topic", required=False)
@click.argument("subtype", required=False)
def help_cmd(topic: str | None, subtype: str | None) -> None:
    """Show reference docs for monitors, scrapers, and config."""
    if not topic:
        print(INDEX)
        return

    # "ws help monitor <type>" / "ws help scraper <type>"
    if topic == "monitor":
        if not subtype:
            print("Usage: ws help monitor <type>")
            print(f"  Types: {', '.join(MONITOR_CARDS)}")
            return
        if subtype not in MONITOR_CARDS:
            print(f"Unknown monitor type: {subtype!r}")
            print(f"  Valid types: {', '.join(MONITOR_CARDS)}")
            raise SystemExit(1)
        print(MONITOR_CARDS[subtype])
        return

    if topic == "scraper":
        if not subtype:
            print("Usage: ws help scraper <type>")
            print(f"  Types: {', '.join(SCRAPER_CARDS)}")
            return
        if subtype not in SCRAPER_CARDS:
            print(f"Unknown scraper type: {subtype!r}")
            print(f"  Valid types: {', '.join(SCRAPER_CARDS)}")
            raise SystemExit(1)
        print(SCRAPER_CARDS[subtype])
        return

    # Simple topic lookup
    if topic in TOPIC_MAP:
        print(TOPIC_MAP[topic])
        return

    print(f"Unknown topic: {topic!r}")
    print()
    print(INDEX)
    raise SystemExit(1)
