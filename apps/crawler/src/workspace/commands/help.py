"""ws help — on-demand reference docs for monitors, scrapers, and config."""

from __future__ import annotations

import click

# ── Topic text constants ─────────────────────────────────────────────────

INDEX = """\
Usage: ws help <topic>

Available topics:
  board             Board command quick reference (add/use/del/patterns)
  monitors          Monitor type overview + decision tree
  scrapers          Scraper type overview + field importance
  monitor <type>    Per-type reference (join, greenhouse, lever, rss, sitemap, dom, ...)
  scraper <type>    Per-type reference (json-ld, nextdata, embedded, dom, api_sniffer)
  fields            Job data fields — types, formats, importance
  steps             DOM scraper step key reference
  actions           Browser action pipeline
  feedback          Feedback command — verdicts, per-field quality, examples
  artifacts         Debug artifacts saved by ws commands
  industries        Industry IDs for company enrichment

Commands:
  ws probe monitor   Probe all monitor types for active board
  ws probe scraper   Probe all scraper types against sample URLs

Troubleshooting:
  ws task troubleshoot <query>   Search the knowledge base"""

BOARD = """\
Board Command Reference:

  Identifiers:
    alias       Short board name used by ws commands (e.g. careers, careers-gh)
    board_slug  Full slug stored in CSV/workspace (e.g. stripe-careers-gh)

  Most ws commands expect alias. If you pass board_slug, ws will try to
  resolve it back to alias automatically.

  Add:
    ws add board <alias> --url "<board-url>"
    ws add board careers-gh --url "https://job-boards.eu.greenhouse.io/acme"

  Use:
    ws use --board <alias-or-board_slug>
    ws use <company> <alias-or-board_slug>

  Remove:
    ws del board <alias-or-board_slug>
    ws del <company> board <alias-or-board_slug>

  Job-link pattern:
    ws set --board <alias-or-board_slug> --job-link-pattern "<regex>"

  Tips:
    - Single board alias: careers
    - Multi-board aliases: careers-us, careers-de, careers-gh
    - Prefer real listings board URLs over marketing landing pages
    - If setting job-link-pattern manually, start broad and include URL variants
      (numeric suffixes, query params), then tighten only after count checks
"""

MONITORS = """\
Monitor Types (cheapest first):

  Type              Cost    Returns         Scraper needed?
  ────────────────────────────────────────────────────────
  join              9       Full job data   No (skipped)
  apify_meta        10      Full job data   No (skipped)
  ashby             10      Full job data   No (skipped)
  bite              10      Full job data   No (skipped)
  breezy            10      Full job data   No (skipped)
  dvinci            10      Full job data   No (skipped)
  gem               10      Full job data   No (skipped)
  greenhouse        10      Full job data   No (skipped)
  hireology         10      Full job data   No (skipped)
  lever             10      Full job data   No (skipped)
  pinpoint          10      Full job data   No (skipped)
  recruitee         10      Full job data   No (skipped)
  rippling          10      Full job data   No (skipped)
  rss               10      Full job data   No (skipped)
  smartrecruiters   10      Full job data   No (skipped)
  softgarden        10      Full job data   No (skipped)
  traffit           10      Full job data   No (skipped)
  workable          10      Full job data   No (skipped)
  workday           10      Full job data   No (skipped)
  personio          10      Full/partial    If descriptions missing (fallback)
  umantis           15      URL set         Yes
  nextdata          20      URLs or full    If URL-only
  sitemap           50      URL set         Yes
  api_sniffer       80      URLs or full    If URL-only (no fields)
  dom               100     URL set         Yes

Interpretation guide (after ws probe monitor):
  1. Rich monitor detected (join/greenhouse/lever/rss/etc):
     strong signal, but validate sample content and coverage.
  2. nextdata / api_sniffer detected:
     inspect mapped fields before accepting.
  3. URL-only monitors (sitemap/umantis/dom):
     compare discovered count with visible listings and validate filters.
  4. Nothing detected:
     gather more evidence (rendered probe/deep probe) before deciding.

Config-first policy:
  Before switching monitor type, iterate config on the current plausible type:
  ws help monitor <type>  →  ws select monitor <type> --as <name> --config '{...}'  → ws run monitor

Evidence note:
  Probe suggestions are hypotheses. Prefer directly referenced site evidence
  over blind slug guesses when they conflict.

All monitors support url_filter to include/exclude URLs by regex:
  "url_filter": "/jobs/"                          Include only
  "url_filter": {"include": "/jobs/", "exclude": "/blog/"}

All monitors support url_transform to rewrite discovered URLs:
  "url_transform": {"find": "/profile/job_details/", "replace": "/jobs/"}
  Uses regex find/replace. Applied after url_filter.

Regex safety:
  Start broad, then tighten after validating count against the site.
  Include common URL variants (numeric suffixes, trailing slash, query params).

  ws probe monitor                  Run monitor probe
  ws help monitor <type>            Detailed config reference
  ws help scrapers                  Scraper overview"""

SCRAPERS = """\
Scraper Types:

  Type           Fetch       Config needed?   Best for
  ───────────────────────────────────────────────────────────
  json-ld        Static/PW   No (optional render)  Sites with schema.org/JobPosting
  nextdata       Static/PW   Yes (fields)     Next.js sites with __NEXT_DATA__
  embedded       Static/PW   Yes (fields)     JS-embedded JSON (script tags, variables)
  dom            Static/PW   Yes (steps)      Custom HTML structure
  api_sniffer    Playwright  Optional (fields)  SPA/XHR job pages
  workable       API         No               Workable job pages (auto-configured)
  workday        API         No               Workday job pages (auto-configured)

  Many monitors auto-configure the scraper — ws select monitor will tell you
  if the scraper step is skipped. You only reach this step when manual
  selection is needed.

  api_sniffer scraper is auto-probed via Playwright in ws probe scraper.

  Probe first: ws probe scraper tries all types automatically against
  sample URLs. Heuristic configs are starting evidence, not final truth.
  Confirm with extracted sample content.

  Try json-ld first — many sites embed JobPosting structured data for SEO.
  If json-ld returns empty fields, check page source for embedded JSON data
  (script tags, JS variables) → try embedded scraper. Fall back to dom last.

Config-first policy:
  Before switching scraper type, iterate config on the current plausible type:
  ws help scraper <type>  →  ws select scraper <type> --config '{...}'  → ws run scraper

Field importance:
  Required     title — every job must have a title
  Required     description — HTML fragment, needed for display
  Important    locations — most jobs have at least one
  Important    job_location_type — "Remote", "Hybrid", "On-site"
  Optional     employment_type, date_posted, base_salary, skills,
               qualifications, responsibilities, valid_through

  Titles and descriptions should reach full coverage before submit.
  Missing locations acceptable only if job_location_type is set (remote-only).
  See: ws help fields                  Full field reference

  ws probe scraper                  Run scraper probe
  ws help scraper <type>            Detailed config reference
  ws help steps                     DOM scraper step format"""

MONITOR_AMAZON = """\
amazon — Amazon Jobs API

  API:      GET https://www.amazon.jobs/en/search.json?result_limit=100&offset=N
  Returns:  Full job data (title, HTML description, locations, employment_type,
            date_posted, base_salary)
            metadata: id_icims, job_category, job_family, business_category,
            company_name
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs (API caps at 10k per query; auto-partitions by country)

  Config:
    {}                                         All jobs worldwide
    {"country": "DEU"}                         Single country (ISO 3166-1 alpha-3)
    {"category": "software-development"}       Single job category
    {"business_category": "amazon-web-services"}  Single team/division

  Notes:
    - Max 100 results per page, max 10,000 per query (offset >= 10000 errors)
    - When total exceeds 10k, the monitor partitions by country code
    - Country codes: ISO 3166-1 alpha-3 (USA, DEU, GBR, IND, JPN, etc.)
    - No date-range filter available; sort=recent orders by creation date
    - Job URL constructed from job_path field in API response"""

MONITOR_BITE = """\
bite — BITE GmbH ATS (Job Search API, widget key auth)

  Search: POST https://jobs.b-ite.com/api/v1/postings/search
  Returns:  Job URLs only (https://{domain}/jobposting/{hash})
  Scraper:  Auto-configured (bite) — fetches details on daily scrape schedule
  Cap:      10,000 jobs
  Note:     Requires a 40-char hex "Job Listing Key" embedded in widget JS.
            Key is extracted from listing JS at cs-assets.b-ite.com.
            6,500+ customers in DACH. Pitchman portals (multi-employer
            aggregators like jobs.drk.de) are NOT handled — out of scope.

  Config:
    {"key": "9d6d3e33a4d7cc7c319d0ccb38cf695f6c3c4172"}
    {"key": "...", "locale": "en", "channel": 0}

    key       40-char hex API key. Auto-filled by ws probe from:
              1. Page HTML scan for data-bite-jobs-api-listing widget attribute
              2. Listing JS fetch from cs-assets.b-ite.com/{customer}/jobs-api/
              3. Key extraction from createClient({key: ...}) pattern
    locale    API locale for job content (default: "de") — passed to scraper
    channel   API channel parameter (default: 0)

  Detection:  ws probe shows "BITE API — customer: X, N jobs"
  Zero jobs?  Verify key — the listing JS may have changed format"""

MONITOR_DVINCI = """\
dvinci — d.vinci ATS (Public JSON API, no auth)

  API:      GET https://{slug}.dvinci-hr.com/jobPublication/list.json
  Returns:  Full job data (title, HTML description, locations, employment_type,
            date_posted, base_salary)
            metadata: contract_period, reference, categories, department
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     API is fully public — no authentication required.
            Primarily DACH region (Germany, Austria, Switzerland).

  Config:
    {"slug": "at-careers"}

    slug     Customer subdomain. Auto-filled by ws probe from:
             1. Direct URL ({slug}.dvinci-hr.com)
             2. Page HTML scan for d.vinci markers (dvinciVersion meta,
                ng-app="dvinci.apps.Dvinci", DvinciData variable)
             No blind slug probe — subdomains are custom names.

  Detection:  ws probe shows "d.vinci API — slug: X, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser"""

MONITOR_BREEZY = """\
breezy — Breezy HR Public Listing Endpoint

  Listing:  GET https://{portal}/json
  Returns:  Job detail URLs (built from listing JSON)
  Scraper:  Auto-configured (json-ld) — extracts JSON-LD JobPosting from detail pages
  Cap:      10,000 jobs
  Note:     Single HTTP call to listing endpoint.
            Detail URLs built as https://{portal}/p/{friendly_id}.

  Config:
    {"portal_url": "https://acme.breezy.hr"}
    {"slug": "acme"}  # shorthand for https://{slug}.breezy.hr

    portal_url  Optional explicit Breezy portal URL/origin.
                Useful for custom domain pages embedding a Breezy board.
                Auto-filled by ws probe when detected.
    slug        Optional Breezy slug shorthand.

  Detection:  ws probe shows "Breezy — https://{portal}, N jobs"
  Zero jobs?  Valid board with no open postings still returns 0 jobs.
  False positives:  Redirects to marketing.breezy.hr are rejected unless
                    /json validates as a real listing endpoint."""

MONITOR_GEM = """\
gem — Gem ATS Job Board API

  API:      GET https://api.gem.com/job_board/v0/{slug}/job_posts/
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted)
            metadata: department
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     Single API call — no pagination, no auth needed

  Config:
    {"token": "caffeine-ai"}

    token    Board slug from jobs.gem.com/{slug}. Auto-filled by ws probe from:
             1. Direct URL (jobs.gem.com/{slug})
             2. Inline HTML scan for jobs.gem.com or __GEM_TRACKING_CONTEXT__
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Gem API — slug: {token}, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser"""

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
             2. Regional board URL (job-boards.<region>.greenhouse.io/{token})
             3. Inline JS scan for Greenhouse API references / urlToken
             4. Slug-based API probe (derives slug from domain)

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

MONITOR_JOIN = """\
join — JOIN (join.com) Next.js Monitor

  Source:    Next.js __NEXT_DATA__ on join.com/companies/{slug}
  Returns:   Job URLs (scraper fetches details separately on daily schedule)
  Scraper:   Auto-configured (nextdata) — config needed in board CSV
  Cap:       10,000 jobs
  Note:      Pre-configured nextdata monitor for JOIN.
             Listing pages contain jobs at:
             props.pageProps.initialState.jobs.items
             JOIN paginates by ?page=N (typically 5 jobs per page).

  Config:
    {"slug": "acme"}

    slug               Company slug from URL path /companies/{slug}.
                       Auto-filled by ws probe and auto-derived from URL.

  Detection:  ws probe shows "JOIN — slug: X, N jobs"
              Requires join.com URL + detectable __NEXT_DATA__ job list.
  Zero jobs?  Verify board URL is join.com/companies/{slug} and not a
              marketing landing page."""

MONITOR_APIFY_META = """\
apify_meta — Apify-backed Meta Careers monitor

  Source:    Existing Apify actor run for a Meta Careers scraper actor
  Returns:   Full job data (title, HTML description, locations,
             employment_type, job_location_type, date_posted)
             extras: responsibilities, qualifications
             metadata: teams, sub_teams
  Scraper:   Not needed (monitor returns full data, scraper step is skipped)
  Cap:       Controlled by the Apify actor / dataset
  Note:      Starts the configured Apify actor, waits for completion, then
             maps the resulting dataset into canonical DiscoveredJob records.

  Config:
    {"actor_id": "myuser/meta-careers-scraper"}
    {"actor_id": "myuser/meta-careers-scraper", "max_jobs": 250}
    {"actor_id": "myuser/meta-careers-scraper", "fetch_descriptions": false}

    actor_id            Required Apify actor ID.
    max_jobs            Optional limit passed to the actor. 0 means all jobs.
    fetch_descriptions  Whether the actor should fetch descriptions
                        (default: true).

  Environment:
    APIFY_TOKEN         Required. Used to start and poll the actor run.

  Detection:  Not auto-detected by ws probe. Use when a board is explicitly
              backed by an Apify actor and you want rich monitor output.
  Zero jobs?  Verify actor_id and inspect the actor's latest dataset in Apify."""

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

  url_transform  Regex find/replace to rewrite discovered URLs:
                   "url_transform": {"find": "/profile/job_details/", "replace": "/jobs/"}
                   Use when the sitemap lists non-public or redirect URLs that
                   need mapping to the canonical public job page.

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
    url_transform Regex find/replace to rewrite URLs (see: ws help monitor sitemap)

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
                 Keep patterns broad enough to include URL variants
    url_transform Regex find/replace to rewrite URLs (see: ws help monitor sitemap)
                 (numeric suffixes, trailing slash, query params)

  Pagination (multi-page career sites):
    {
      "render": false,
      "url_filter": "/jobs/",
      "pagination": {"param_name": "page", "max_pages": 10000}
    }

    pagination.param_name   Query parameter name (required)
    pagination.start        First page's param value (default: 1)
    pagination.increment    Step per page (default: 1)
    pagination.max_pages    Hard limit (default: 10000, system cap: 10000)
                            Set this to a value that greatly overshoots the
                            expected real page count; low caps silently undercount.
    pagination.browser      If true, fetch via page.evaluate(fetch(...)) inside
                            Playwright context — preserves cookies (default: false)

    Fetching starts at start + increment (page 1 is the board URL itself).
    Stops when: no new links found, fetch fails, or max_pages reached.
    High max_pages is usually safe: small boards terminate early via "no new links".
    Works with both render: false (httpx) and render: true (Playwright).

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
               3. Explicit blind-probe mode only (domain-derived slug guess)
    api_base   Full base URL for custom domains. Auto-filled when detected
               via HTML scan (e.g. karriere.herta.de → https://karriere.herta.de).

  Detection:  ws probe shows "Recruitee API — {slug}, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser
  Custom domains:  Recruitee supports custom domains (e.g. karriere.herta.de).
                   The API is at https://{custom-domain}/api/offers."""

MONITOR_SMARTRECRUITERS = """\
smartrecruiters — SmartRecruiters Posting API (URL-only)

  API:      GET https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset=0
  Returns:  URL set only — constructs URLs as https://jobs.smartrecruiters.com/{token}/{posting_id}
  Scraper:  Auto-configured (smartrecruiters) — fetches details on daily schedule
  Cap:      10,000 jobs

  Config:
    {"token": "smartrecruiters"}

    token    Company identifier. Auto-filled by ws probe from:
             1. Direct URL (jobs.smartrecruiters.com/{token})
             2. Inline JS scan for SmartRecruiters API references
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "SmartRecruiters API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_SOFTGARDEN = """\
softgarden — Softgarden ATS (HTML scraping, no auth)

  Listing:  GET https://{slug}.softgarden.io
  Returns:  Job detail URLs (built from inline JS job IDs)
  Scraper:  Auto-configured (json-ld) — extracts JSON-LD JobPosting from detail pages
  Cap:      10,000 jobs
  Note:     Single HTTP call to listing page.
            Listing page embeds job IDs in inline JavaScript.
            Detail URLs built as https://{slug}.softgarden.io/job/{id}?l=en.
            Largest uncovered ATS in DACH (~2,000+ customers).

  Config:
    {"slug": "hapaglloyd"}
    {"slug": "hapaglloyd", "job_url_pattern": "{base}/job/{id}?l=de"}

    slug             Customer subdomain. Auto-filled by ws probe from:
                     1. Direct URL ({slug}.softgarden.io)
                     2. Page HTML scan for Softgarden markers
                        (softgarden.io/assets/, tracker.softgarden.de,
                        matomo.softgarden.io, powered by softgarden)
                     No blind slug probe — subdomains are custom names.
    job_url_pattern  URL pattern for detail pages (optional).
                     Default: {base}/job/{id}?l=en
                     Change ?l=de for German-language pages.

  Detection:  ws probe shows "Softgarden — slug: X, N jobs"
  Zero jobs?  Verify slug — visit https://{slug}.softgarden.io directly"""

MONITOR_TRAFFIT = """\
traffit — TRAFFIT ATS (Public JSON API, no auth)

  API:      GET https://{slug}.traffit.com/public/job_posts/published
  Headers:  X-Request-Page-Size, X-Request-Current-Page (pagination)
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary, language)
            extras: requirements, responsibilities, benefits (HTML)
            metadata: reference, department
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     API is fully public — no authentication required.
            Primarily Poland/CEE region.

  Config:
    {"slug": "mycompany"}

    slug     Customer subdomain. Auto-filled by ws probe from:
             1. Direct URL ({slug}.traffit.com)
             2. Page HTML scan for TRAFFIT markers (cdn3.traffit.com,
                traffit-an-list, data-name="traffit")
             No blind slug probe — subdomains are custom names.

  Detection:  ws probe shows "TRAFFIT API — slug: X, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser"""

MONITOR_UMANTIS = """\
umantis — Umantis ATS (Haufe Group / Abacus)

  Listing:  GET https://recruitingapp-{ID}[.de].umantis.com/Jobs/All
  Returns:  URL set only (needs scraper)
  Cap:      10,000 URLs
  Note:     Paginated HTML listing pages (10 per page).
            Pagination via tc{tableNr}=p{page} query params.
            1,000+ customers in DACH (Switzerland, Germany, Austria).
            Each customer has a unique HTML template on detail pages —
            no shared structured data (no JSON-LD).

  Config:
    {"customer_id": "2698"}
    {"customer_id": "5181", "region": "de"}

    customer_id  Numeric customer ID from URL. Auto-filled by ws probe from:
                 1. Direct URL (recruitingapp-{ID}[.de].umantis.com)
                 2. Page HTML scan for Umantis markers
                 No blind probe — customer IDs are numeric, not derivable.
    region       Subdomain region: "" for .umantis.com, "de" for
                 .de.umantis.com. Auto-filled from URL.
    listing_path Override listing page path (default: /Jobs/All)

  Detection:  ws probe shows "Umantis — ID: X, N jobs"
  Zero jobs?  Verify customer_id — visit the listing URL directly
  Pair with:  json-ld (try first) or dom scraper"""

MONITOR_RIPPLING = """\
rippling — Rippling ATS Job Board API

  API:      GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
  Returns:  Job posting URLs (https://ats.rippling.com/{slug}/jobs/{uuid})
  Scraper:  Auto-configured (rippling) — detail API fetches full data daily
  Cap:      10,000 jobs
  Note:     Single API call — returns all jobs, no pagination

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
personio — Personio XML Feed + HTML Fallback

  API:      GET https://{slug}.jobs.personio.{de,com}/xml?language={language}
  Fallback: Parses RSC-embedded JSON from the HTML listing page
  Returns:  Full job data via XML (title, HTML description, locations,
            employment_type, date_posted).
            Via HTML fallback: all fields except description.
            metadata: department, subcompany, recruitingCategory, seniority,
            yearsOfExperience, occupation, occupationCategory, keywords
  Scraper:  Not needed when XML available with descriptions (skipped).
            When HTML fallback is used or descriptions are missing, scraper needed.
  Cap:      10,000 jobs
  Note:     Tries both .personio.de and .personio.com domains automatically.
            Some tenants only serve .com and/or have no XML feed.
            Many tenants have descriptions in only one language (e.g. DE only).
            The monitor auto-backfills from other languages.

  Config:
    {"slug": "acme"}
    {"slug": "acme", "language": "de", "backfill_languages": ["en"]}

    slug                Company subdomain. Auto-filled by ws probe.
    language            Primary XML feed language (default: "en").
                        Auto-discovered: ws probe checks EN and DE coverage
                        and picks the language with the most descriptions.
    backfill_languages  List of fallback languages to fill in missing
                        descriptions (default: ["de"]). Set to [] to disable.
                        Auto-discovered from coverage analysis.

  Detection:  ws probe shows "Personio XML — slug: X, N jobs"
              or "Personio HTML — slug: X, N jobs" (fallback)
              Also shows language coverage (e.g. "en: 11/19 desc, de: 13/19 desc")
  Zero jobs?  Verify slug — try the listing page in a browser"""

MONITOR_RSS = """\
rss — RSS 2.0 Feed Monitor (presets: successfactors, teamtailor, generic)

  Feed:     GET {feed_url}
  Returns:  Full job data (title, HTML description, locations, date_posted)
            metadata: id and preset-specific fields
  Scraper:  Not needed (feed returns full data, scraper step is skipped)
  Cap:      10,000 jobs
  Note:     One monitor type with multiple ATS presets:
            - successfactors: /googlefeed.xml (Google Base namespace)
            - teamtailor: /jobs.rss (offset-paginated)
            - generic: standard RSS 2.0 (manual feed URL)

  Config:
    {"preset": "successfactors", "feed_url": "https://jobs.sap.com/googlefeed.xml"}
    {"preset": "teamtailor", "feed_url": "https://company.teamtailor.com/jobs.rss"}
    {"preset": "generic", "feed_url": "https://example.com/jobs.rss"}

    preset     Feed parser preset. Auto-detected when possible.
               Defaults to "generic" when not set.
    feed_url   RSS URL. For known presets, ws probe can auto-fill this from
               the board URL; for generic feeds set it explicitly.

  Detection:  ws probe shows labels like:
              "SuccessFactors RSS — <feed_url>, N jobs"
              "Teamtailor RSS — <feed_url>, N jobs"
              "RSS (generic) — <feed_url>, N jobs"
  Zero jobs?  Verify feed_url directly in a browser and confirm it returns
              job items (not an empty feed or non-RSS endpoint)."""

MONITOR_WORKABLE = """\
workable — Workable Posting API

  API:      POST https://apply.workable.com/api/v3/accounts/{token}/jobs
  Returns:  Job URLs (scraper fetches details separately on daily schedule)
  Scraper:  Auto-configured (workable) — no manual selection needed
  Cap:      10,000 jobs
  Note:     Monitor discovers URLs only via the list API (lightweight, hourly).
            A dedicated workable scraper fetches full details (title, description,
            locations, etc.) from the detail API on a daily schedule.
            List endpoint uses cursor pagination (token in POST body).

  Config:
    {"token": "neowork"}

    token    Company slug. Auto-filled by ws probe from:
             1. Direct URL (apply.workable.com/{token})
             2. Inline HTML scan for Workable references
             3. Explicit blind-probe mode only (domain-derived slug guess)

  Detection:  ws probe shows "Workable API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_WORKDAY = """\
workday — Workday Job Board API

  API:      POST https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/jobs
  Returns:  Job URLs (scraper fetches details separately on daily schedule)
  Scraper:  Auto-configured (workday) — no manual selection needed
  Cap:      10,000 jobs
  Note:     Monitor discovers URLs only via the list API (lightweight, hourly).
            A dedicated workday scraper fetches full details (title, description,
            locations, etc.) from the detail API on a daily schedule.
            Max page size is 20 (API returns 400 for higher values).
            API caps results at 2000 per query — automatically splits by
            facet (e.g. job category) for companies with >2000 listings.
            Multi-site: discovers all tenant job sites via robots.txt and
            aggregates jobs from every site. Set "all_sites": false to
            monitor only the configured site.

  Config:
    {"company": "nvidia", "wd_instance": "wd5", "site": "NVIDIAExternalCareerSite"}

    company       Company subdomain. Auto-filled by ws probe from:
                  1. Direct URL ({company}.wd{N}.myworkdayjobs.com/{site})
                  2. Inline HTML scan for Workday markers
    wd_instance   Workday instance (e.g. wd1, wd5). Auto-filled from URL.
    site          Career site identifier. Auto-filled from URL path.
    all_sites     Discover all tenant sites via robots.txt (default: true).
                  Set false to monitor only the configured site.

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
    HTML string mode:       When json_path resolves to a string (not a list),
      the content is treated as an HTML fragment. URLs are extracted via
      url_regex (or default href matching). Pagination fetches additional
      pages and extracts URLs from each HTML string.
      Use for APIs that return HTML fragments inside JSON (e.g. WordPress
      get-jobs.php, PHP endpoints returning rendered HTML in a JSON wrapper).

      Example (WordPress PHP API returning HTML in JSON):
        {
          "api_url": "https://example.com/get-jobs.php",
          "params": {"ajax": "1"},
          "json_path": "postings.jobs",
          "total_path": "postings.size",
          "url_regex": "href=\"(/jobdetail/\\?jobId=\\d+)\"",
          "pagination": {"param_name": "spage", "style": "page",
                         "start_value": 1, "increment": 1, "location": "query"}
        }

      url_regex    Regex with one capture group to extract URLs from the HTML
                   string. Default: matches all href attribute values.

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

  Fetch:    Static HTTP (default) or Playwright (render: true)
  Config:   No field mapping needed

  Parses <script type="application/ld+json"> blocks for JobPosting data.
  Handles @graph arrays and nested structures automatically.
  Uses the first JSON-LD block that contains a JobPosting.

  Optional runtime config:
    render    Use Playwright (default: false)
    actions   Browser action pipeline (auto-enables render)
    wait      Navigation wait strategy (Playwright only)
    timeout   Navigation timeout in ms (Playwright only)

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

    {"action": "repeat", "selector": "button.load-more", "max": 50, "wait_ms": 2000}
        Click an element repeatedly until no new <a href> links appear.
        Stops when: selector disappears, no new links after click, or max reached.
        Default timeout: 300s (vs normal action's 10s).
        Options:
          selector   CSS selector to click (required)
          max        Max iterations (default: 50)
          wait_ms    Ms to wait after each click (default: 2000)
        Use for "Load More" / "Show More" buttons on infinite-scroll pages.

  Per-action timeout:
    {"action": "click", "selector": ".btn", "timeout": 5}
        Override default 10s timeout (value in seconds)

  Example pipelines:
    "actions": [
      {"action": "dismiss_overlays"},
      {"action": "click", "selector": "button[data-load-all]"},
      {"action": "wait", "ms": 2000}
    ]

    "actions": [
      {"action": "repeat", "selector": "button.load-more", "max": 30, "wait_ms": 1500}
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

  Configuration exploration policy (before switching type):
    → Do not switch monitor/scraper type after the first bad run unless there is
      a hard mismatch (wrong platform/domain, unsupported endpoint, explicit non-detection)
    → For a plausible type, try at least one concrete config variant and re-run
    → Preserve attempts with named configs when available:
      ws select monitor <type> --as <name> --config '{...}'
      ws select config <name>
      ws reject-config <name> --reason "..."
    → Record what was tried and why it failed before changing type

  Monitor returns 0 jobs:
    greenhouse/lever  Verify token — open the API URL directly in browser
    sitemap           Sitemap may only have blog/page URLs, not jobs → try dom
    nextdata          Path may be wrong — check __NEXT_DATA__ in browser devtools
    dom               Try render: true, or check that links contain job keywords
    api_sniffer       Verify api_url, check if site needs cookies (board page
                      navigated first), try different board URL

  Monitor returns fewer jobs than expected:
    → Compare against website's displayed total ("Showing N positions")
    → For paginated monitors, raise max_pages first so it significantly
      overshoots expected pages, then re-run before switching type
    → Do not optimize for low page caps — completeness comes first
    → If using url_filter/job-link-pattern, test without it (or with a broader
      regex) to catch over-strict filters that drop valid variants
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
    → ws task fail --reason "..." to enter coding mode"""

FEEDBACK = """\
Feedback Command Reference:

  ws feedback [<config>] --verdict <verdict> --verdict-notes "..."

  Records extraction quality feedback for the active (or named) scraper
  configuration.  Feedback is MANDATORY before ws submit.

  Verdicts:
    good        All required + important fields extracted cleanly
    acceptable  Required fields clean; some important fields noisy/absent
    poor        Submit requires --force; significant quality issues
    unusable    Cannot submit at all

  Per-field quality options (override auto-populated values):
    --title <q>              Required field
    --description <q>        Required field
    --locations <q>          Important field (--locations-notes "...")
    --employment-type <q>    Important field (--employment-type-notes "...")
    --job-location-type <q>  Important field (--job-location-type-notes "...")
    --date-posted <q>        Optional field
    --base-salary <q>        Optional field
    --skills <q>             Optional field
    --qualifications <q>     Optional field
    --responsibilities <q>   Optional field
    --valid-through <q>      Optional field

  Quality values: clean, noisy, unusable, absent

  Auto-population:
    Field quality is auto-populated from ws run monitor / ws run scraper
    coverage data.  Pass explicit --<field> options only to override.

  Examples:
    ws feedback --verdict good --verdict-notes "All fields extracted cleanly"
    ws feedback my-cfg --verdict acceptable --verdict-notes "Locations noisy" \\
        --locations noisy --locations-notes "Missing city for some postings"
    ws feedback --verdict poor --verdict-notes "Description truncated" \\
        --description unusable"""

# ── Lookup tables ────────────────────────────────────────────────────────

MONITOR_CARDS: dict[str, str] = {
    "amazon": MONITOR_AMAZON,
    "apify_meta": MONITOR_APIFY_META,
    "bite": MONITOR_BITE,
    "breezy": MONITOR_BREEZY,
    "dvinci": MONITOR_DVINCI,
    "gem": MONITOR_GEM,
    "greenhouse": MONITOR_GREENHOUSE,
    "hireology": MONITOR_HIREOLOGY,
    "join": MONITOR_JOIN,
    "lever": MONITOR_LEVER,
    "ashby": MONITOR_ASHBY,
    "recruitee": MONITOR_RECRUITEE,
    "rippling": MONITOR_RIPPLING,
    "smartrecruiters": MONITOR_SMARTRECRUITERS,
    "softgarden": MONITOR_SOFTGARDEN,
    "traffit": MONITOR_TRAFFIT,
    "umantis": MONITOR_UMANTIS,
    "workable": MONITOR_WORKABLE,
    "workday": MONITOR_WORKDAY,
    "pinpoint": MONITOR_PINPOINT,
    "personio": MONITOR_PERSONIO,
    "rss": MONITOR_RSS,
    "sitemap": MONITOR_SITEMAP,
    "nextdata": MONITOR_NEXTDATA,
    "dom": MONITOR_DOM,
    "api_sniffer": MONITOR_API_SNIFFER,
}

SCRAPER_SMARTRECRUITERS = """\
smartrecruiters — SmartRecruiters Detail API scraper

  API:      GET https://api.smartrecruiters.com/v1/companies/{token}/postings/{posting_id}
  Returns:  title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary,
            metadata (department, function, experienceLevel)
  Config:   None needed — token from board config, posting_id parsed from URL
  Note:     Auto-configured when selecting the smartrecruiters monitor.
            Runs on the daily scrape schedule (not every monitor cycle).
"""

SCRAPER_WORKABLE = """\
workable — Workable Detail API scraper

  API:      GET https://apply.workable.com/api/v2/accounts/{slug}/jobs/{shortcode}
  Returns:  title, HTML description, locations, employment_type,
            job_location_type, date_posted, metadata (department)
  Config:   None needed — parses the job URL to derive API parameters
  Note:     Auto-configured when selecting the workable monitor.
            Runs on the daily scrape schedule (not every monitor cycle).
"""

SCRAPER_WORKDAY = """\
workday — Workday Detail API scraper

  API:      GET https://{company}.{wd_instance}.myworkdayjobs.com/wday/cxs/{company}/{site}/job/{path}
  Returns:  title, HTML description, locations, employment_type,
            job_location_type, date_posted, metadata (jobReqId)
  Config:   None needed — parses the job URL to derive API parameters
  Note:     Auto-configured when selecting the workday monitor.
            Runs on the daily scrape schedule (not every monitor cycle).
"""

SCRAPER_RIPPLING = """\
rippling — Rippling Detail API scraper

  API:      GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs/{uuid}
  Returns:  title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary,
            metadata (department, base_department, company)
  Config:   None needed — slug from board config, uuid parsed from URL
  Note:     Auto-configured when selecting the rippling monitor.
            Runs on the daily scrape schedule (not every monitor cycle).
"""

SCRAPER_BITE = """\
bite — BITE GmbH ATS Detail API scraper

  API:      GET https://jobs.b-ite.com/jobposting/{hash}/json?locale={locale}&contentRendered=true
  Returns:  title, HTML description, locations, employment_type,
            date_posted, base_salary, language,
            metadata (reference, employer)
  Config:   locale from board config (default: "de") — used for API query param
            and language field
  Note:     Auto-configured when selecting the bite monitor.
            Runs on the daily scrape schedule (not every monitor cycle).
            Hash (40-42 char hex) is extracted from the job URL.
"""

SCRAPER_SKIP = """\
skip — Placeholder scraper (auto-configured)

  Monitors that return full job data auto-configure this scraper to signal
  that the scraper step should be skipped. Never selected manually.
"""

SCRAPER_CARDS: dict[str, str] = {
    "json-ld": SCRAPER_JSONLD,
    "nextdata": SCRAPER_NEXTDATA,
    "embedded": SCRAPER_EMBEDDED,
    "dom": SCRAPER_DOM,
    "api_sniffer": SCRAPER_API_SNIFFER,
    "skip": SCRAPER_SKIP,
    "bite": SCRAPER_BITE,
    "rippling": SCRAPER_RIPPLING,
    "smartrecruiters": SCRAPER_SMARTRECRUITERS,
    "workable": SCRAPER_WORKABLE,
    "workday": SCRAPER_WORKDAY,
}


def _show_industries() -> None:
    """Display industry IDs from data/industries.csv."""
    from src.core.enrich.company import _load_industries

    industries = _load_industries()
    if not industries:
        print("No industries found in data/industries.csv")
        return

    print("Industry IDs for company enrichment")
    print("Use with: ws set --industry <id>\n")
    print(f"  {'ID':>3}  Name")
    print(f"  {'──':>3}  {'─' * 30}")
    for ind in industries:
        print(f"  {ind['id']:>3}  {ind['name']}")

    print("\nEmployee count range buckets (for --employee-count-range):")
    print("  1: 1-10       2: 11-50      3: 51-200     4: 201-500")
    print("  5: 501-1,000  6: 1,001-5,000  7: 5,001-10,000  8: 10,001+")


TOPIC_MAP: dict[str, str] = {
    "board": BOARD,
    "monitors": MONITORS,
    "scrapers": SCRAPERS,
    "fields": FIELDS,
    "steps": STEPS,
    "actions": ACTIONS,
    "artifacts": ARTIFACTS,
    "troubleshooting": TROUBLESHOOTING,
    "feedback": FEEDBACK,
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

    # Dynamic topics
    if topic == "industries":
        _show_industries()
        return

    # Simple topic lookup
    if topic in TOPIC_MAP:
        print(TOPIC_MAP[topic])
        return

    print(f"Unknown topic: {topic!r}")
    print()
    print(INDEX)
    raise SystemExit(1)
