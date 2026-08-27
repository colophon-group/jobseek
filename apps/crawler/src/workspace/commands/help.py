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
  troubleshooting   Troubleshooting tips + case study reference
  industries        Industry IDs for company enrichment
  occupations       Occupation taxonomy — slugs, display names, alias counts
  seniority         Seniority levels — slugs, display names, alias counts

Commands:
  ws probe monitor   Probe all monitor types for active board
  ws probe scraper   Probe all scraper types against sample URLs

Troubleshooting & case studies:
  ws task troubleshoot <query>       Search the knowledge base
  ws task troubleshoot --view <file> View full KB entry (useful for case studies)
  ws task casestudy --company ...    Record a case study from a complex setup"""

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

  Type              Cost    Returns           Scraper needed?
  ──────────────────────────────────────────────────────────────
  eightfold         8       Hybrid rich+URL   eightfold enrich (description only)
  join              9       Job URLs          Auto-configured
  almacareer        10      Full job data     No (skipped)
  adp               10      Full/partial      Auto-enriched
  ashby             10      Full job data     No (skipped)
  avature           10      Job URLs          Auto-configured DOM
  bamboohr          10      Full/partial      Auto-enriched
  beisen            10      Full/partial      Auto skip/DOM enrich
  bite              10      Job URLs          Auto-configured
  brassring         10      Full job data     No (skipped)
  breezy            10      Job URLs          Auto-configured
  cnstaff           10      Full job data     No (skipped)
  comeet            10      Full job data     No (skipped)
  computrabajo      10      Job URLs          Auto-configured JSON-LD
  cornerstone       10      Full job data     No (skipped)
  darwinbox         10      Full job data     No (skipped)
  dayforce          10      Full job data     No (skipped)
  deel              10      Full job data     No (skipped)
  dvinci            10      Full job data     No (skipped)
  earcu             10      Full job data     No (skipped)
  gem               10      Full job data     No (skipped)
  greenhouse        10      Full job data     No (skipped)
  gupy              10      Job URLs          Auto-configured
  headhunter        10      Full/partial      Auto-enriched
  beehire           10      Full job data     No (skipped)
  hibob             10      Full job data     No (skipped)
  hirehive          10      Full job data     No (skipped)
  hireology         10      Full job data     No (skipped)
  turbohire         10      Full job data     No (skipped)
  herp              10      Job URLs          Auto-configured
  hrmos             10      Job URLs          Auto-configured
  icims             10      Job URLs          Auto-configured
  intervieweb       10      Job URLs          Auto-configured
  jarvi             10      Full job data     No (skipped)
  jazzhr            10      Job URLs          Auto-configured
  jobbank104        10      Job URLs          Auto-configured JSON-LD
  jobstreet         10      Full/partial      Auto-enriched
  johdi             10      Job URLs          Auto-configured
  pageup            10      Full/partial      Auto-enriched DOM
  keka              10      Full job data     No (skipped)
  lever             10      Full job data     No (skipped)
  linkedin          10      Full/partial      Auto-enriched
  manatal           10      Full job data     No (skipped)
  paycom            10      Full/partial      Auto-enriched
  paylocity         10      Full/partial      Auto-enriched
  pinpoint          10      Full job data     No (skipped)
  recruitee         10      Full job data     No (skipped)
  recruiterbox      10      Job URLs          Auto-configured
  rippling          10      Job URLs          Auto-configured
  rss               10      Full job data     No (skipped)
  seamlesshiring    10      Full job data     No (skipped)
  smartrecruiters   10      Job URLs          Auto-configured
  softgarden        10      Job URLs          Auto-configured
  traffit           10      Full job data     No (skipped)
  ukg               10      Full/partial      Auto-enriched
  unifr             10      Full or PDF URLs  skip/pdf (fixed source)
  workable          10      Job URLs          Auto-configured
  welcometothejungle 10      Full job data     No (skipped)
  workday           10      Job URLs          Auto-configured
  personio          10      Full/partial      If descriptions missing (fallback)
  practicematch     10      Job URLs          Auto-configured
  prospective       10      Full job data     No (skipped)
  notion            15      Job URLs          Auto-configured
  recruiter_co_kr   15      Full job data     No (skipped)
  umantis           15      Full/partial      Description enrichment
  nextdata          20      URLs or full      If URL-only
  talemetry         45      URL set           Yes
  talentbrew        45      URL set           Yes
  sitemap           50      URL set           Yes
  kipt              60      Full job data     No (skipped)
  api_sniffer       80      URLs or full      If URL-only (no fields)
  njoyn             80      Job URLs          DOM scraper
  dom               100     URL set           Yes

Interpretation guide (after ws probe monitor):
  1. Rich monitor detected (join/greenhouse/lever/rss/etc):
     strong signal, but validate sample content and coverage.
  2. nextdata / api_sniffer detected:
     inspect mapped fields before accepting.
  3. URL/partial monitors (sitemap/umantis/dom):
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

Rich monitors support job_filter to include/exclude discovered job content:
  "job_filter": {"exclude": "(?i)subsidiary name"}
  Matches title, description, locations, and metadata. URL-only monitors
  cannot apply this filter.

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
  phuketall      Static      No               PhuketAll employer job pages
  veryeast       Static      No               VeryEast employer job pages
  onlyfy         Static      No               Onlyfy/Prescreen job pages
  paycor         Static      No               Paycor/Newton legacy job pages
  pdf            Static      No               PDF job descriptions
  dom            Static/PW   Yes (steps)      Custom HTML structure
  api_sniffer    HTTP/PW     Optional (fields)  SPA/XHR or direct API
  adp            API         No               ADP Workforce Now detail + DOCX attachments
  workable       API         No               Workable job pages (auto-configured)
  workday        API         No               Workday job pages (auto-configured)
  johdi          API         No               Johdi Suite offer details (auto-configured)

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
  Important    job_location_type — "remote", "hybrid", "onsite"
  Optional     employment_type, date_posted, base_salary, skills,
               qualifications, responsibilities, valid_through
  Auto         language — ISO 639-1, auto-detected or monitor-provided

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

MONITOR_ACCENTURE = """\
accenture — Accenture Career API (dedicated monitor)

  API:      POST /api/accenture/elastic/findjobs (multipart form data)
            POST /api/accenture/jobsearch/result (FR/BR variant)
  Returns:  Full job data (title, HTML description, locations,
            job_location_type, date_posted)
            metadata: businessArea, careerLevel, guid
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 per query; auto-partitions by businessArea then careerLevel

  Config:
    {"country": "India", "language": "en", "site": "in-en"}
    {"country": "France", "language": "fr", "site": "fr-fr",
     "endpoint": "jobsearch/result"}

  Notes:
    - Launches browser once to get cookies, then uses httpx for speed
    - Page size 500, pagination ceiling 50k (startIndex >= 50000 returns empty)
    - totalHits caps at 10k cosmetically; pagination works up to 50k
    - FR/BR use jobsearch/result endpoint (captured via route interception)
    - When 50k ceiling is hit, partitions by businessArea (discovered from data)
    - If a single area also exceeds 50k, sub-partitions by careerLevel"""

MONITOR_BITE = """\
bite — BITE GmbH ATS (Job Search API, widget key auth)

  Search: POST https://jobs.b-ite.com/api/v1/postings/search
  Returns:  Job URLs only (https://{domain}/jobposting/{hash})
  Scraper:  Auto-configured (bite) — fetches details on daily scrape schedule
  Cap:      50,000 jobs
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

MONITOR_DEEL = """\
deel — Deel ATS Job Board API

  API:      Settings: GET /deelapi/guest/ats/organizations/{slug}/career_page_settings
            Postings: GET /deelapi/guest/ats/organizations/{org_id}/
                      job_boards/{board_id}/job_postings
  Returns:  Full job data (title, HTML description, locations, employment_type,
            date_posted, base_salary)
            metadata: team, department, id
  Scraper:  Not needed (API returns full data, scraper step is skipped)

  Config:
    {"slug": "klarna"}

    slug       Company URL slug on jobs.deel.com. Auto-filled by ws probe from
               the board URL (jobs.deel.com/{slug}).
    org_id     Organization UUID. Auto-resolved from career_page_settings.
    board_id   Job board UUID. Auto-resolved from career_page_settings.

  Detection:  ws probe shows "Deel API — slug: X, N jobs"
  Zero jobs?  Verify slug — try visiting jobs.deel.com/{slug} in a browser"""

MONITOR_DVINCI = """\
dvinci — d.vinci ATS (Public JSON API, no auth)

  API:      GET https://{slug}.dvinci-hr.com/jobPublication/list.json
  Returns:  Full job data (title, HTML description, locations, employment_type,
            date_posted, base_salary)
            metadata: contract_period, reference, categories, department
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs
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
  Cap:      50,000 jobs
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

MONITOR_COMEET = """\
comeet — Comeet hosted data and Careers API monitor

  Sources:  COMPANY_POSITIONS_DATA embedded in hosted/custom board HTML, or
            GET https://www.comeet.co/careers-api/2.0/company/{company_id}/positions
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted, responsibilities, qualifications)
            metadata: uid, department, experience_level, company_name, time_updated
  Scraper:  Not needed (one board/API request returns all full job records)
  Cap:      50,000 jobs

  Config:   None required for hosted boards. Identifiers are derived from:
            https://www.comeet.com/jobs/{company}/{board_id}
            Custom career sites use public credentials embedded in their HTML:
            {"company_id": "67.007", "token": "PUBLIC_EMBED_TOKEN"}

  Detection:  ws probe shows "Comeet — embedded data: company/board, N jobs"
              or "Comeet — API company: X, N jobs"
  Zero jobs?  An empty embedded list or API array is a valid active board."""

MONITOR_CVWAREHOUSE = """\
cvwarehouse — CVWarehouse hosted careers monitor

  Source:    Hosted tenant pages such as https://acme.cvw.io/
  Returns:   Full job data from the provider's unfiltered section and embedded
             detail documents: title, HTML description, location, schedule,
             remote policy, language, and provider job ID metadata.
  Scraper:   Not needed. All localized details are embedded in the listing pages.
  Cap:       10,000 jobs across at most 20 advertised locales.

  Config:    None required. The monitor detects the largest category tile as
             the unfiltered all-vacancies section and follows every locale.
             An auto-detected {"section": "..."} may be persisted explicitly.

  Detection: ws probe shows "CVWarehouse hosted board — N jobs".
  Safety:    Advertised and localized unique-job counts must match; mismatches
             fail the monitor cycle instead of delisting unseen jobs."""

MONITOR_HIBOB = """\
hibob — HiBob public career-site monitor

  Source:   GET https://{tenant}.careers.hibob.com/api/job-ad
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted, salary, responsibilities,
            qualifications, benefits, and department metadata)
  Scraper:  Not needed (one request returns all complete job records)
  Cap:      50,000 jobs

  Config:   None required for https://{tenant}.careers.hibob.com boards.
            Optional override: {"origin": "https://acme.careers.hibob.com"}

  Detection:  ws probe verifies the public /api/job-ad payload and reports
              its current job count.
  Zero jobs?  A valid jobAdDetails: [] payload is an active empty board."""

MONITOR_BEEHIRE = """\
beehire — Beehire public career-page monitor

  Source:   GET https://app.beehire.com/users/getPublicCampaigns/{slug}
  Returns:  Full job data (title, HTML description, location, contract type,
            remote policy, posting date, language, and stable invite URL)
  Scraper:  Not needed (one request returns every public campaign)
  Cap:      50,000 jobs

  Config:   None required for https://app.beehire.com/career/{slug} boards.
            Optional override: {"slug": "acme"}

  Detection:  ws probe verifies the public campaigns payload and reports its
              current job count.
  Zero jobs?  A valid campaigns: [] payload is an active empty board."""

MONITOR_JOHDI = """\
johdi — Johdi Suite embedded careers monitor

  Source:   Public list and per-offer APIs at ats.johdisuite.ch
  Returns:  Stable provider-ID offer URLs from the complete active inventory.
            A constant route slug keeps the official deep link functional;
            title/locale slug changes cannot churn job identity.
  Scraper:  johdi (auto-configured) fetches structured offer details on the
            normal scrape schedule
  Cap:      50,000 jobs

  Config:   company_key, flow, and locale. All are auto-detected from the
            #ats-offers widget embedded on a custom careers page. Configured
            identity must keep matching that exact live widget on every run.

  Detection:  ws probe verifies the public offers payload and reports its
              current job count.
  Safety:    Listing/detail JSON is bounded, non-redirecting, and content-type
             checked. Duplicate/invalid IDs and mismatched detail IDs fail.
  Zero jobs?  A valid [] response from the matched widget is an active empty board."""

MONITOR_EIGHTFOLD = """\
eightfold — Eightfold AI Careers Portal (hybrid sitemap + PCSX)

  Sources:  Sitemap (URL authority) + PCSX API (rich data, incremental)
  Returns:  Sitemap URL set + partial rich data (title, locations, date_posted,
            job_location_type, department, ats_job_id) for new/updated jobs
  Scraper:  json-ld in "enrich" mode to fill description only
            (PCSX doesn't return descriptions)
  Cost:     8 — cheapest monitor type

  How it works:
    1. Fetch the sitemap (canonical URL set — drives gone detection)
    2. Probe PCSX API availability (cached in board metadata after first run)
    3. If PCSX enabled: paginate newest-first via postedTs DESC, stopping
       early when all items on a page are older than the stored watermark
       (high-water mark). First run + weekly interval = full pagination.
    4. Correlate PCSX positions to sitemap URLs by numeric job id
    5. Emit partial rich data (keyed on canonical sitemap URL) + the full
       sitemap URL set so gone detection continues to work

  PCSX-disabled tenants (403 "PCSX is not enabled"): fall back to sitemap-only
  mode automatically — same behaviour as the pre-hybrid monitor. Known
  disabled tenants: bayer, american-express, hsbc, stmicroelectronics,
  symetra, vale, zebra.

  Eightfold AI powers careers portals for 170+ enterprises including
  Starbucks, HSBC, Microsoft, Kering, Citigroup, Micron, etc.

  Config (monitor_config):
    {"url_filter": "/careers/job/"}               Filter non-job URLs (recommended)
    {"sitemap_url": "https://custom.com/sitemap.xml"}  Override auto-derived URL
    {"pcsx_watermark": {"auto_full_crawl": false}}     Await manual backfill
                                                        (used for very large boards
                                                        like Starbucks — see below)

  Config (scraper_config):
    {"enrich": ["description"]}                   REQUIRED for PCSX-enabled tenants
                                                   so json-ld fills in descriptions
                                                   (PCSX doesn't return them).
                                                   Optional for PCSX-disabled
                                                   tenants (no effect if PCSX is
                                                   skipped).

  Watermark state (runtime-written in ``metadata.pcsx_watermark``):
    max_ts               Highest postedTs seen — drives incremental stop
    last_full_at         Last full crawl time (for 7-day refresh cadence)
    last_incremental_at  Last successful incremental run
    enabled              Cached probe result (true/false)
    auto_full_crawl      If false, skip automatic full crawl on first run
    extra                Host + domain (derived from sitemap URLs)

  Manual backfill for large boards (Starbucks-scale):
    Board CSVs with ``monitor_config.pcsx_watermark.auto_full_crawl: false``
    skip the initial full crawl in scheduled runs. Operator triggers it
    manually with:

        uv run crawler board <slug> --pcsx-full-crawl

    This bypasses the watermark and does a full PCSX pagination (can take
    30-60 minutes for very large boards). On success the watermark is
    populated and subsequent scheduled runs do fast incremental top-ups.

  URL patterns:
    *.eightfold.ai subdomains:  starbucks.eightfold.ai/careers
    White-label custom domains: careers.kering.com, apply.careers.microsoft.com
    Redirecting subdomains:     netflix.eightfold.ai → explore.jobs.netflix.net

  Detection:  ws probe shows "Eightfold AI — N jobs at {sitemap_url}"
              Detects *.eightfold.ai domains, HTML markers (eightfold.ai, pcsx),
              and PCSX API endpoint probe for white-label domains.

  Observability:
    eightfold.full_crawl_start       First run or weekly re-sync starting
    eightfold.full_crawl_done        Completed, N positions fetched
    eightfold.incremental_start      Steady-state polling, max_ts=<unix>
    eightfold.incremental_done       Completed, N positions fetched
    eightfold.pcsx_disabled          Tenant returned 403 / probe failed
    eightfold.pcsx_fetch_failed      Rate limit (405) or retry exhaustion —
                                      sitemap-only fallback, watermark preserved
    eightfold.pcsx_unmatched         N PCSX positions had no sitemap match
                                      (usually sitemap lag for fresh jobs)
    eightfold.awaiting_manual_backfill  auto_full_crawl=false + no watermark

  Why the hybrid design?  Two problems in the old sitemap-only path:
    1. JSON-LD in schema.org HTML has known quality issues on Eightfold —
       stale datePosted, malformed addressRegion, missing jobLocationType.
       PCSX returns clean standardizedLocations, accurate postedTs, and
       explicit workLocationOption.
    2. Steady-state crawls become O(new jobs) instead of O(total jobs)
       via watermark-based early termination — the monitor typically
       fetches only the first ~5-20 PCSX pages per cycle instead of
       hundreds.

  Zero jobs?  Verify the sitemap URL exists: curl {host}/careers/sitemap.xml
              Check url_filter isn't too restrictive."""

MONITOR_GEM = """\
gem — Gem ATS Job Board API

  API:      GET https://api.gem.com/job_board/v0/{slug}/job_posts/
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted)
            metadata: department
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs
  Note:     Single API call — no pagination, no auth needed

  Config:
    {"token": "caffeine-ai"}

    token    Board slug from jobs.gem.com/{slug}. Auto-filled by ws probe from:
             1. Direct URL (jobs.gem.com/{slug})
             2. Inline HTML scan for jobs.gem.com or __GEM_TRACKING_CONTEXT__
             3. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Gem API — slug: {token}, N jobs"
  Zero jobs?  Verify slug — try the API URL directly in a browser"""

MONITOR_INPLOI = """\
inploi — Inploi candidate-experience API

  API:      GET https://api.inploi.com/search/results
  Returns:  Job URLs, titles, locations, employment/workplace types, dates,
            and public salary data
  Scraper:  Auto-configured JSON-LD enrichment for descriptions
  Cap:      50,000 jobs

  Config (auto-filled by probe):
    {"api_key": "pk_...", "segment_id": "248"}

  api_key     Public SDK publishable key embedded in the career page
  segment_id  Default job-search segment embedded in the Inploi widget

  Detection:  ws probe verifies the public key and segment against the API.
  Zero jobs?  Re-run the probe; the site may have changed its segment ID."""

MONITOR_TYPIFY = """\
typify — Typify partitioned vacancy API

  API:      POST /api/vacancies on the career-site origin
  Returns:  Job URLs, titles, and locations
  Scraper:  Auto-configured JSON-LD enrichment for descriptions
  Cap:      50,000 jobs

  Config:   None required. The monitor re-reads the board page every cycle.

  Typify's large unfiltered response is not stably ordered. This monitor
  discovers every live job-function filter, fetches each complete partition,
  and fails closed unless their unique union matches the API's advertised
  total. New function filters are therefore picked up automatically.

  Detection:  ws probe verifies the widget marker and same-origin public API.
  Zero jobs?  Re-run the probe; the widget or API route may have changed."""

MONITOR_JARVI = """\
jarvi — Jarvi public careers API

  Detects the Jarvi SDK embedded on a company's careers page and reads its
  public API key from the data-public-api-key attribute.

  Rich monitor — returns title, description, location, employment type,
  workplace type, publication date, public salary data, qualifications,
  and responsibilities. No scraper is needed.

  Config (auto-filled by probe):
    {"public_api_key": "...", "currency": "EUR"}

  The board URL remains the company's careers page. Job URLs use Jarvi's
  stable ?q=<short-id>/<title-slug> deep-link format."""

MONITOR_GREENHOUSE = """\
greenhouse — Greenhouse Public API

  API:      GET https://boards-api.greenhouse.io/v1/boards/{token}/jobs?content=true
  Returns:  Full job data (title, HTML description, locations, date_posted)
            metadata: departments, education, requisition_id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs

  Config:
    {"token": "stripe"}

    token    Board identifier. Auto-filled by ws probe from:
             1. Direct URL (boards.greenhouse.io/{token})
             2. Regional board URL (job-boards.<region>.greenhouse.io/{token})
             3. Inline JS scan for Greenhouse API references / urlToken
             4. Slug-based API probe (derives slug from domain)

  Detection:  ws probe shows "Greenhouse API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_HIREHIVE = """\
hirehive — HireHive Public Jobs API

  API:      GET https://{slug}.hirehive.com/api/v2/jobs?page=1&page_size=100
  Returns:  Full job data (title, HTML description, location, employment_type,
            date_posted, language, base_salary)
            metadata: id, category, experience
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs
  Note:     Uses the public tenant API instead of the Cloudflare-rate-limited
            hosted careers HTML.

  Config:
    {"slug": "acme"}

    slug     HireHive tenant. Auto-filled by ws probe from:
             1. Direct URL ({slug}.hirehive.com)
             2. Inline HTML scan for embedded HireHive links

  Detection:  ws probe shows "HireHive API — slug: X, N jobs"
  Zero jobs?  Verify slug — try /api/v2/jobs?page=1&page_size=1 directly"""

MONITOR_HIREOLOGY = """\
hireology — Hireology Careers API

  API:      GET https://api.hireology.com/v2/public/careers/{slug}?page_size=500
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted)
            metadata: organization, job_family, id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs
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

MONITOR_CURATELY = """\
curately — Curately Public Career API

  API:      GET  /QADemoCurately/getByShortName/{short_name}
            POST /QADemoCurately/sovrenjobsearch
  Returns:  Full job data (title, HTML description, location, employment_type,
            job_location_type, date_posted, optional hourly pay range)
            metadata: id, assignment dates, raw work/job/hour codes
  Scraper:  Not needed (the list API returns full data)
  Cap:      50,000 jobs

  Config:
    {"short_name":"bms","client_id":6,"days_back":180,
     "currency":"USD","salary_unit":"hour","language":"en"}

    short_name  Tenant path under careers.curately.ai/jobs/. Auto-detected.
    client_id   Public numeric client id. Auto-resolved from short_name.
    days_back   Public-search age window (default 180, matching the portal).
    currency / salary_unit
                Optional; both are required to emit the numeric pay range.
    language    Optional ISO language code for single-language tenants.

  Detection:  ws probe verifies the public tenant identity, then reports the
              unfiltered search total. Pagination uses Curately's next offset
              and fails closed if the advertised total changes or a page is
              skipped."""

MONITOR_TURBOHIRE = """\
turbohire — TurboHire Public Career API

  API:      GET /api/token/noauth, POST /api/careerpagev2/filteredjobs,
            GET /api/publicjobs?jobId=... on thapi.azurewebsites.net
  Returns:  Full job data (title, complete HTML description, locations,
            employment_type, date_posted, language, skills)
            metadata: id, job_code, department, client_name, experience range
  Scraper:  Not needed (detail API returns full data, scraper step is skipped)
  Cap:      50,000 jobs
  Note:     Fetches TurboHire's short-lived public token once per cycle and
            retrieves details with bounded concurrency.

  Config:
    {"org_id": "4d757ba0-3d57-448a-b82c-238ed87ac90f"}

    org_id   Organization UUID. Auto-filled from /careerpage/{org_id} or
             /dashboardv2?orgId={org_id} URLs.

  Detection:  ws probe shows "TurboHire API — organization: X, N jobs"
  Zero jobs?  Verify the organization UUID and that the public career page is published"""

MONITOR_LEVER = """\
lever — Lever Postings API

  API:      GET https://api.lever.co/v0/postings/{token}?limit=100&skip=N
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, base_salary)
            metadata: team, department, id
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs
  Rate:     0.5s sleep between pagination batches of 100

  Config:
    {"token": "cloudflare"}

    token    Company slug. Auto-filled by ws probe from:
             1. Direct URL (jobs.lever.co/{token})
             2. Inline JS scan for Lever API references
             3. Slug-based API probe

  Detection:  ws probe shows "Lever API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_LINKEDIN = """\
linkedin — LinkedIn public guest-jobs endpoints

  Listing:  GET https://www.linkedin.com/jobs-guest/jobs/api/
            seeMoreJobPostings/search?f_C={company_id}&start=N
  Returns:  Rich summaries (URL, title, location, date)
  Scraper:  Auto-configured (linkedin) — enriches description and work types
  Cap:      1,000 jobs
  Scope:    Worldwide, newest first (avoids location/ranking truncation)
  Note:     Intended for companies whose official careers link points only to
            LinkedIn. Prefer a first-party ATS whenever one exists.

  Config:
    {"company_id": "109559449", "company_slug": "damora-therapeutics",
     "keywords": "Damora Therapeutics",
     "canonical_numeric_job_urls": false,
     "source_ownership_excluded_country_codes": ["THA"]}

    company_id    Numeric LinkedIn company ID (the f_C search-filter value).
                  Auto-resolved for active company jobs pages during probing.
    company_slug  Optional exact company slug used to reject unrelated cards.
    keywords      Optional company-name search term for tenants where LinkedIn's
                  company filter returns only a ranked subset without it. The
                  monitor unions both queries and marks the cycle partial so
                  varying guest-search subsets cannot tombstone valid jobs.
    canonical_numeric_job_urls
                  Optional boolean, default false. Emits stable numeric-ID URLs
                  only for a new board or after an ID-preserving migration.
                  Never enable on an existing board without migrating its
                  persisted title-bearing source URLs first.
    source_ownership_excluded_country_codes
                  Optional ISO-3166 alpha-3 countries owned by another configured
                  provider. Country resolution uses LinkedIn's exact English
                  country field and fails closed when missing or ambiguous.

  Identity: By default, validated title-bearing paths retain legacy identity.
            The opt-in numeric mode uses /jobs/view/{numeric_id}; locale and
            title paths then become presentation aliases only.

  Detection:  ws probe shows "LinkedIn guest jobs — company: X, N jobs"
  Zero jobs?  Verify the f_C value; an empty company board is valid."""

MONITOR_HEADHUNTER = """\
headhunter — HeadHunter employer vacancies API

  Listing:  GET https://api.hh.ru/vacancies?employer_id={employer_id}&page=N
  Detail:   GET https://api.hh.ru/vacancies/{vacancy_id}
  Returns:  Rich summaries (URL, title, location, employment_type,
            job_location_type, date_posted, base_salary and metadata)
  Scraper:  Auto-configured (headhunter) — hydrates the description and all
            detail fields on the daily scrape schedule
  Cap:      2,000 jobs (upstream deep-pagination limit; returned as a
            non-destructive truncated result). Page/count/ID drift fails closed.
  Note:     HeadHunter's ddos-guard blocks some datacenter networks. Detected
            employer boards therefore set proxy=true automatically and use the
            crawler's configured static proxy transport.

  Config:
    {"employer_id": "4556149", "host": "hh.ru", "proxy": true}

    employer_id  Numeric ID from hh.ru/employer/{id}. Auto-filled by probe.
    host         Public site selected by the board URL (for example hh.ru,
                 rabota.by, or hh.kz). Normally inferred automatically.
    proxy        Keep enabled for production crawler egress.

  Detection:  ws probe shows "HeadHunter API — employer: X, N jobs" when
              directly reachable, or "(proxy required)" when ddos-guard blocks
              the probe host.
  Zero jobs?  Verify employer_id and that the public employer page has openings."""

MONITOR_JOBYLON = """\
jobylon — Jobylon (Nordic ATS, inline-embed widget)

  Source:   GET https://cdn.jobylon.com/jobs/companies/{company_id}/embed/v2/
            GET https://cdn.jobylon.com/jobs/company-groups/{group_id}/embed/v2/
  Returns:  Rich job data — title, locations, language, date_posted,
            job_location_type (TELECOMMUTE when labeled Remote).
            metadata: id, company_id, company, function, experience,
            employment_type_label, workspace, departments, to_date,
            published_date_raw.
  Scraper:  Not needed for title/location (rich).  Pair with a json-ld
            scraper + ``enrich: ["description"]`` when descriptions are
            wanted — detail pages under emp.jobylon.com serve
            ``application/ld+json`` JobPosting.
  Cap:      50,000 jobs
  Note:     The embed endpoint returns a server-rendered AngularJS
            widget whose inline ``<script>`` body assigns the full
            job list to ``JBL.embed_v2['jobs']`` as a JS object literal
            (unquoted keys).  No separate JSON API exists — the widget
            ships with the entire dataset per request.
            Unknown customer IDs 404 (treated as ``BoardGoneError``).

  Config:
    {"company_id": "1955"}
    {"company_group_id": "241"}

    company_id         Jobylon numeric customer id (single-brand
                       customers, e.g. McDonald's Sverige = 1955).
    company_group_id   Numeric multi-brand group id (e.g. McDonald's
                       Danmark uses group 241).  Takes precedence over
                       company_id when both are set.

  Detection:  ws probe shows "Jobylon embed — company: X, N jobs"
              or "Jobylon embed — company-group: X, N jobs".
              Detects via direct cdn.jobylon.com URL or iframe
              reference in the page HTML.
  Zero jobs?  Verify the customer id by loading the embed URL directly
              in a browser.  Stale/disabled customers return HTTP 404."""

MONITOR_JOIN = """\
join — JOIN (join.com) Next.js Monitor

  Source:    Next.js __NEXT_DATA__ on join.com/companies/{slug}
  Returns:   Job URLs (scraper fetches details separately on daily schedule)
  Scraper:   Auto-configured (nextdata) — config needed in board CSV
  Cap:      50,000 jobs
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

MONITOR_SITEMAP = """\
sitemap — XML Sitemap Parser

  Returns:  URL set only (needs scraper)
  Cap:      50,000 URLs

  Config:
    {"sitemap_url": "https://example.com/jobs/sitemap.xml", "proxy": true}

    sitemap_url  Optional. If omitted, auto-discovers by:
                 1. Walking up the board URL path trying sitemap.xml at each level
                 2. Trying non-standard paths (/sitemaps/sitemapIndex, etc.)
                 3. Parsing robots.txt for Sitemap: directives
                 4. Recursively resolving sitemap indexes (prefers job-related children)
                 Discovered URL is cached in board metadata for future runs.

    proxy        Optional. Route sitemap, index, and child requests through
                 the configured proxy provider when live 403/429 WAF evidence
                 shows that direct crawler egress is blocked. Mirror this in
                 the scraper config when detail pages use the same origin.
                 Configured sitemap URLs retry and fail closed on persistent
                 403/429 responses instead of being reported as not found.

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

MONITOR_PHENOM = """\
phenom — Phenom People Careers Platform (sitemap + json-ld)

  Returns:  URL set only (needs scraper)
  Cap:      50,000 URLs (inherited from sitemap)

  Phenom tenants (e.g. careers.marriott.com, careers.nike.com) expose a
  sitemap-index at /sitemap.xml with per-language child sitemaps named
  sitemap-<hex>-<lang>.xml. The sitemap is the authoritative URL set.
  Rich job data comes from the detail page's JSON-LD JobPosting script,
  extracted by the json-ld scraper.

  Why a dedicated monitor vs. plain sitemap:
    • Phenom-specific can_handle fingerprint (child naming) avoids
      mis-detecting generic XML sitemaps as Phenom during ws probe.
    • Filters discovered URLs to job detail pages (contain "/job/" or
      "?job_id="), dropping site root, "/jobs" index, language pages.

  No API is used. Phenom's /api/get-jobs has no per-job timestamp and
  no sort-by-recency, so there's no meaningful incremental signal; the
  sitemap URL set + last_seen_at in Postgres already give _DIFF_BATCH
  everything it needs for new/relisted/gone classification.

  Config:
    {}  — no configuration required. sitemap_url is derived from the
          board URL's scheme+host (board metadata stores the discovered
          URL for future runs, as with the sitemap monitor).

  Scrapers:
    json-ld (default) — works for marriott, nike, nordstrom, elevance,
                        mondelez. Detail page returns JSON-LD natively.
    json-ld + render:true — for mcdonalds-* and nationwide detail pages
                            where Playwright render is needed before
                            the JobPosting <script> appears in DOM.

  Browser flags on the board (persistent_context, channel=chrome,
  proxy) only matter for the scraper path; the monitor itself uses
  plain httpx and runs in ~5 seconds regardless of job count."""

MONITOR_TALENTBREW = """\
talentbrew — TalentBrew / Radancy Search Results

  Returns:  URL set only (needs scraper)
  Cap:      50,000 URLs

  TalentBrew search pages render job links in #search-results-list and expose
  pagination metadata on #search-results:
    data-total-job-results, data-total-pages, data-records-per-page

  Why a dedicated monitor vs. plain sitemap:
    Some TalentBrew tenants publish incomplete sitemaps. The search results
    page is the complete listing source and can be paged with ?p=N.

  Config:
    {}  — no configuration required
    {"page_size": 1000}  Optional AJAX page size (default 1000, max 10000).
    {"max_pages": 500}   Optional safety cap, rarely needed.
    {"page_max_chars": 8000000}
                         Optional first-page/pagination HTML read cap
                         (default 5,000,000; max 25,000,000).
    {"proxy": true}      Route WAF-gated listing and pagination requests
                         through the configured proxy provider.

  Detection:  ws probe shows "TalentBrew/Radancy — N jobs across M pages"
              Looks for TalentBrew/Radancy static markers plus #search-results.

  Pair with:  json-ld (try first) or dom scraper"""

MONITOR_NJOYN = """\
njoyn — Njoyn XWeb browser monitor

  Returns:  Complete URL set (needs scraper)
  Cost:     Browser pagination; one session per board cycle

  Njoyn listings paginate by submitting a session-bound form. The monitor
  clicks the live NEXT control in one browser context, collects every page,
  and verifies the URL count against the visible "Search Results (N)" total.
  A repeated page, bot challenge, max-page cap, or count mismatch fails the
  cycle rather than returning a partial URL set.

  Config:
    {"persistent_context": true, "headless": false, "stealth": true,
     "proxy": true, "max_pages": 100, "page_wait_ms": 1000}

    max_pages       Safety cap (default/system cap 200)
    page_wait_ms    Delay after each form submission (default 1000)
    proxy           Route the browser through the configured proxy provider

  Detection:  *.njoyn.com/.../xweb/XWeb.asp listing URLs
  Pair with:  dom scraper rendered in a warmed Njoyn session"""

MONITOR_PROSPECTIVE = """\
prospective — Prospective CareerCenter HTML form monitor

  Returns:  Full localized job data with durable application identity
  Cost:     10; server-rendered GET + POST pagination, no browser

  Prospective CareerCenter pages submit offset pagination through a standard
  HTML form. This monitor is the fallback for tenants whose public JSON medium
  endpoint is unavailable. It validates the provider medium, form contract,
  pagination progress, same-origin detail links, and configured filter values.
  It enriches each detail's JSON-LD and resolves its application URL. Locale
  variants sharing that durable application identity become one posting with
  localizations instead of duplicate title-bearing detail URLs.

  Config:
    {"medium_id": "1000613",
     "filters": {"filter_10": ["1082961", "1082964"]},
     "application_identity": {
       "link_texts": ["Apply", "Bewerben"],
       "source_url_allowlist": "^https://apply\\.example/jobs/[1-9]\\d*$",
       "canonical_url_allowlist": "^https://apply\\.example/jobs/[1-9]\\d*$",
       "locale_priority": ["en", "de", "fr", "it"],
       "concurrency": 8}}

    medium_id   Optional numeric provider identity; fails if the page changes.
    filters     Optional exact allowlist of repeated CareerCenter form values.
                Every configured value must still exist in the live select;
                missing values fail the cycle rather than broadening scope.
    application_identity
                Required fail-closed detail identity contract. link_texts must
                select exactly one application link per detail. The raw link
                and final resolving URL must fully match their separate
                allowlists. locale_priority chooses top-level content while
                retaining every locale under localizations. Detail fetch
                concurrency is bounded to 1-16.

  Detection:  form#careercenter-form plus /careercenter/<id>/assets/ marker
  Pair with:  no scraper (rich monitor)"""

MONITOR_CANDIDATUS = """\
candidatus — Candidatus / WinDev browser monitor

  Returns:  Complete stable detail-URL set (needs scraper)
  Cost:     Browser navigation; one WinDev postback per advertised job

  Candidatus listings expose job cards as JavaScript postbacks instead of
  crawlable links. The monitor clicks every validated title control, records
  its stable /annonce-emploi,... URL, and fails closed if the listing changes,
  a card is missing, or two cards resolve to the same URL.

  Config:
    {"max_jobs": 500, "timeout": 30000}

    max_jobs    Per-board safety cap (default 1000, maximum 1000)
    timeout     Navigation timeout in milliseconds

  Detection:  carrieres.candidatus.com/site-emploi,... WinDev listings
  Scraper:    auto-configured DOM extraction for title, location, description"""

MONITOR_TALEMETRY = """\
talemetry — Talemetry / Jobvite Career Sites

  Returns:  Complete URL set only (needs scraper)
  Cap:      50,000 URLs

  Talemetry pages render same-origin /jobs/<id>-<slug> links inside
  .jobs-section__list and publish a textual count such as
  "Showing 1-25 of 85 results". The monitor follows ?page=N and validates
  every page range and the final total. Any missing, repeated, or inconsistent
  page fails the cycle instead of returning a partial set to gone detection.

  Config:
    {}  — no configuration required
    {"max_pages": 500}   Optional safety ceiling; exceeding it fails closed.
    {"page_max_chars": 8000000}
                         Optional HTML read cap (default 5,000,000;
                         max 25,000,000).
    {"proxy": true}      Route listing and pagination requests through the
                         configured proxy provider.
    {"transport": "jobs_json"}
                         Use TTC Portals' first-party /search/jobs.json feed
                         with strict total, page-size, ID, and URL checks.

  Detection:  requires Talemetry Career Sites markers, an authoritative
              result range, and matching same-origin job links.

  Pair with:  json-ld (the parser repairs Talemetry's known missing comma
              between datePosted and hiringOrganization)"""

MONITOR_PRACTICEMATCH = """\
practicematch — PracticeMatch Employer Landing Pages

  Returns:  URL set only (needs scraper)
  Cap:      50,000 URLs

  PracticeMatch employer pages render physician page 1 in HTML and paginate
  physician plus advanced-practitioner results through the site's form API.
  The monitor extracts the page's facility/site identity and follows both
  result streams until they are exhausted.

  Config:
    {"proxy": true}       Auto-filled during detection. PracticeMatch drops
                           direct datacenter connections, so production uses
                           the configured static proxy transport.
    {"max_pages": 2000}  Optional safety cap per profession stream.

  Pair with:  json-ld (auto-configured with proxy=true)"""

MONITOR_NEXTDATA = """\
nextdata — Next.js __NEXT_DATA__ Discovery

  Returns:  URL set (default) or full job data (if fields configured)
  Cap:      50,000 items

  Config (minimal — URL-only mode):
    {"path": "props.pageProps.positions", "url_template": "https://example.com/jobs/{id}"}

  Config (rich mode — full job data):
    {
      "path": "props.pageProps.positions",
      "url_template": "https://example.com/jobs/{id}",
      "fields": {"title": "name", "locations": "offices[].name"},
      "slug_fields": ["title"]
    }

    path           Dot-notation path to jobs array in __NEXT_DATA__ JSON
    url_template   URL template with {field_name} placeholders from each item
                   Special: {slug} built by slugifying + joining slug_fields
    fields         Dict mapping DiscoveredJob fields to item field paths
                   Supports dot notation (a.b.c), array index (a[0].b),
                   array wildcard (a[].b — extracts from all items)
    slug_fields    List of item fields to slugify + join for {slug} variable
    render         If true, use Playwright to render page (default: false)
    actions        Browser action pipeline (auto-enables render)
    wait           Navigation wait strategy (Playwright only)
    wait_fallback  Fallback load state checked on the current document after
                   Page.goto timeout (Playwright only). Default:
                   "domcontentloaded". Set to null to opt out.
    timeout        Navigation timeout in ms (Playwright only)
    ignore_locations
                   Discard provider JSON-LD and meta locations. Use only when
                   the published value is demonstrably wrong, together with a
                   board-scoped defaults.locations replacement.
    defaults       Default fields applied after extraction. For example:
                   {"ignore_locations": true,
                    "defaults": {"locations": ["Zurich, Switzerland"]}}
    url_filter     Regex filter for discovered URLs (see: ws help monitor sitemap)
    url_transform  Regex find/replace to rewrite URLs (see: ws help monitor sitemap)
    source         Embedded source: nextdata (default), reactrouter, rsc,
                   phenom_canvas, or browser. The browser source evaluates a
                   JSON-serializable client-side jobs variable after render.
    browser_expression
                   JavaScript expression used when source=browser, for example
                   "({jobs: jobList})". Requires Playwright; actions run before
                   evaluation. Use only data exposed by the public board.
    pagination     Page metadata mapping. Example:
                   {"path":"jobsData.meta","page_count":"totalPages",
                    "page_param":"page"}

  Detection:  ws probe shows "__NEXT_DATA__ — N items at <path>"
              If "(render)" shown, page needs Playwright to load data.
              Auto-searches common paths: props.pageProps.positions,
              props.pageProps.jobs, props.pageProps.openings,
              props.pageProps.allJobs, props.pageProps.data.positions,
              props.pageProps.data.jobs, and common RSC equivalents including
              jobsData.data. Needs >= 5 items (all dicts).

  Pair with:  nextdata or json-ld scraper (if URL-only mode)

  Tip: Inspect nextdata.json artifact to see all available keys in each
  item before choosing your fields mapping. Map employment_type, date_posted,
  job_location_type, team/department if present — they come at no extra cost."""

MONITOR_MOKAHR = """\
mokahr — Mokahr ATS (Chinese recruitment platform)

  Returns:  Rich (title, locations, date_posted, employment_type)
  Cost:     Low — paginated API with AES-128-CBC decryption, no browser.

  Mokahr is a Chinese ATS, normally hosted on app.mokahr.com but also
  available through company-owned custom domains. The API encrypts responses
  with AES-128-CBC using a per-response key and a per-site IV embedded
  in the SPA HTML. The monitor handles decryption transparently.

  Auto-detected from standard Mokahr paths and custom-domain SPA bootstrap data.

  Config:
    org_id      Organisation slug (e.g. "zte")
    site_id     Numeric site ID (e.g. 47588)
    locale      API locale (default "zh-CN")
    partitions  Optional bounded list of additional official sites for the
                same organisation. Each item contains exact board_url and
                site_id values. The monitor validates every site's identity
                and count, then unions active jobs by stable provider ID in
                primary-board / list order so overlapping sites do not emit
                duplicate or locale-dependent URLs.

  Example multi-site group:
    {"org_id":"group","site_id":100,
     "partitions":[
       {"board_url":"https://jobs.brand.test/social-recruitment/group/101",
        "site_id":101}]}

  Safety: Every page must carry a successful encrypted envelope, the exact
          configured organisation identity, a stable advertised total, and
          unique bounded provider IDs. Explicit zero inventories are confirmed
          twice. Only status=open rows are emitted; closed/paused rows are
          counted but filtered."""

MONITOR_RECRUITER_CO_KR = """\
recruiter_co_kr — Recruiter.co.kr ATS (Korean, Public JSON API)

  List:     POST https://api-recruiter.recruiter.co.kr/position/v1/jobflex
  Detail:   GET  https://api-recruiter.recruiter.co.kr/position/v2/jobflex/{sn}
  Returns:  Rich (title, HTML description, date_posted, employment_type,
            language=ko, metadata: classification, tags, valid_through,
            announcement_type, recruitment_type)
  Scraper:  Not needed (detail API returns full HTML)
  Browser:  No — HTTP-only
  Cost:     15 (paginated list + concurrent detail fetches)
  Cap:      50,000 jobs

  Tenants are identified via a `prefix: {slug}.recruiter.co.kr` request
  header — the API itself lives at a shared origin. Board URLs follow the
  pattern https://{slug}.recruiter.co.kr/career/home. Monitor defaults to
  only OPEN + IN_SUBMISSION postings; set include_closed to widen.

  Config:
    {"slug": "mcdonalds"}
    {"slug": "mcdonalds", "include_closed": true}

    slug             Customer subdomain. Auto-derived from board URL.
    include_closed   Include postings with openStatus != OPEN or
                     submissionStatus == POST_SUBMISSION (default false).

  Detection:  ws probe shows "Recruiter.co.kr — slug: X, N jobs"
  Zero jobs?  Check openStatus filters — try include_closed."""

MONITOR_CNSTAFF = """\
cnstaff — CNStaff public career boards

  Listing:  GET https://{tenant}.cnstaff.com/recruit?n=1&p={page}
  Returns:  Rich (title, HTML description, location, date_posted,
            responsibilities, qualifications, language=zh)
  Scraper:  Not needed (the paginated listing response is complete)
  Browser:  No — HTTP-only
  Cost:     10
  Cap:      50,000 jobs

  CNStaff serves an HTML page for normal navigation and complete JSON job
  records from the same route when the public pagination parameters are
  present. The monitor validates totals across every 15-job page and fails
  closed if the inventory changes during a crawl.

  Config: none. The tenant is derived from the exact unfiltered board URL:
    https://{tenant}.cnstaff.com/recruit

  Detection: ws probe verifies the first JSON page and reports its total.
  Zero jobs: a valid board returns total=0 and an empty list."""

MONITOR_NOTION = """\
notion — Notion Site Job Pages

  Returns:  URL set only (needs scraper)
  Cost:     Low — two lightweight API calls, no browser rendering.

  Detects public *.notion.site career pages and enumerates job listings
  via Notion's internal API (/api/v3). Supports two patterns:
  - Sub-pages: child pages, including pages inside layout/transclusion blocks
  - Databases: rows in embedded collection_view blocks (gallery, table, board)

  No config required — auto-resolves page structure from the board URL.

  Config:
    include_nested    Include pages nested inside job pages (default: false)
    collection_index  Zero-based index to select one database when page
                      has multiple (default: use all)
    title_exclude     Regex — exclude rows whose title matches.
                      Example: "Stay up to date|Coming soon"
    property_filter   Filter rows by collection property values:
                      {"exclude": {"Department": "Archived"}}
                      {"include": {"Status": "Open"}}
                      Property names matched case-insensitively.
    url_filter        Regex filter on output URLs (all monitors)

  Detection:   Probe checks for *.notion.site URL, enumerates sub-pages
               and collection databases via API.
  Pair with:   notion scraper (extracts title, description, locations,
               employment_type from page blocks and collection properties)"""

MONITOR_INLINE = """\
inline — Single-Page Extraction (rich)

  Returns:  Full job data (title, description, locations, etc.)
  Scraper:  Not needed (skipped)
  Cost:     60
  Browser:  Only when render: true

  For career pages that list all jobs inline on a single URL with no
  individual job links (e.g., Squarespace, Webflow sites).  Extracts
  multiple jobs by running step-based extraction repeatedly — the cursor
  advances through the page, pulling one job per iteration.

  Each job gets a synthetic URL: {board_url}?_jid={title-slug}-{hash}

  Config:
    {
      "fetch_urls": [
        "https://company.example/jobs",
        "https://render.example.com/company/jobs",
        {
          "url": "https://reader.example.com/company/jobs",
          "headers": {"X-No-Cache": "true"}
        }
      ],
      "fetch_contains": "Open positions",
      "empty_selector": ".empty-state:not(.hidden)",
      "empty_text": "No vacancies are currently available",
      "item_boundary_tag": "h3",
      "preserve_single_location": true,
      "render": true,
      "steps": [
        {"tag": "h3", "field": "title"},
        {"text": "Location", "offset": 1, "field": "location", "optional": true},
        {"tag": "p", "field": "description", "html": true, "stop_tag": "h3"}
      ],
      "defaults": {
        "description": "<p>Evergreen role description.</p>",
        "employment_type": "full_time"
      },
      "valid_through_patterns": [
        {"regex": "Deadline: (\\d{2}\\.\\d{2}\\.\\d{4})", "format": "%d.%m.%Y"},
        {"regex": "Deadline: ([A-Za-z]+ \\d{1,2}, \\d{4})", "format": "%B %d, %Y"}
      ],
      "exclude_expired": true,
      "defaults_by_title": {
        "Account Manager": {"locations": ["USA, Remote"]}
      }
    }

    Click-only detail cards use an automatically bounded item per click:
    {
      "render": true,
      "detail_click_selector": ".job-card .more",
      "detail_content_selector": ".expanded-job:has-text(\"Location:\")",
      "detail_identity_selector": ".job-card [data-job-id]",
      "detail_identity_attribute": "data-job-id",
      "detail_identity_regex": "^job-(\\d+)$",
      "steps": [
        {"tag": "h3", "field": "title"},
        {"text": "Location", "field": "location"},
        {"tag": "p", "field": "description", "html": true, "to_end": true}
      ]
    }

    render       true = Playwright, false = static HTTP (default: false)
    detail_click_selector
                 Playwright selector for click-only job-card controls. The
                 inline monitor reloads the listing, clicks each match in
                 order, and extracts one expanded job per control. Requires
                 render=true and detail_content_selector. Each expanded detail
                 is automatically bounded; omit item_boundary_tag.
    detail_content_selector
                 Playwright selector that must match exactly one expanded
                 detail container after each click. Only that container is
                 passed to the extraction steps, which keeps navigation and
                 application-flow text out of job descriptions. Nested article
                 or reserved synthetic-boundary markup fails closed.
    detail_identity_selector
                 Playwright selector that must match one provider-identity
                 element per click control, in the same order. The complete
                 identity sequence is checked again after every page reload.
    detail_identity_attribute
                 Attribute read from every identity element before clicks.
    detail_identity_regex
                 Full-match regex with exactly one capture group containing
                 the stable provider ID. Missing, duplicate, changed, or
                 non-matching identities fail the cycle closed. Stable IDs,
                 not titles or card positions, become synthetic _jid values.
                 Identities remain out-of-band from provider detail HTML and
                 are revalidated when consumed.
    source_identity_selector
                 CSS selector for one provider-owned stable identity element
                 per job on an ordinary inline listing, in extraction order.
    source_identity_attribute
                 Attribute containing each raw source identity.
    source_identity_regex
                 Full-match regex with exactly one capture group containing
                 the stable ID. Missing, duplicate, non-matching, or count-
                 mismatched identities fail closed and replace title-derived
                 synthetic _jid values.
    fetch_urls   Optional ordered URLs used only to read the page. Each entry is
                 a URL string or {"url": ..., "headers": {...}} object. Headers
                 are scoped to that exact candidate and are never forwarded to
                 other hosts. Failed URLs fall through to the next equivalent
                 representation. Synthetic job URLs remain on board_url.
    fetch_contains
                 Required text that every accepted representation must contain.
                 A response without it falls through to the next fetch URL.
    empty_selector
                 Bounded CSS selector for the visibly active empty-state
                 element. Required with empty_text. The selector must exclude
                 CSS-hidden variants (for example, with :not(.hidden)).
    empty_text   Authoritative text required inside empty_selector. A matching
                 selected element returns a healthy empty result after any
                 configured section boundaries have been validated. If neither
                 this state nor an accepted job is found, the cycle fails
                 closed, including when every extracted title was excluded or
                 expired.
    require_zero_proof
                 true = require an accepted job or an authoritative
                 empty_selector/empty_text match. Use for boards where a silent
                 zero would otherwise retire valid jobs after page-shape drift.
                 Default: false.
    description_from_title
                 true = reuse each extracted title as its description when the
                 source exposes one project/position text field. Default: false.
    positions_per_listing
                 Integer from 1 to 20. Expands one authoritative aggregate row
                 into that many jobs with deterministic numbered identity
                 suffixes. Use only when the source explicitly states a count.
                 Default: 1.
    item_boundary_tag
                 Optional HTML tag that starts each posting (for example h2).
                 Each repeated step run is restricted to one such block, so an
                 optional field cannot consume content from the next posting.
    synthetic_identity_field
                 Optional extracted field containing a provider-stable identity
                 for ordinary static inline rows. The identity, rather than the
                 mutable title or row order, becomes the synthetic _jid. Missing,
                 non-scalar, or duplicate values fail the cycle closed. Cannot
                 be combined with click-card identity or positions_per_listing.
    section_start
                 Optional fail-closed start marker for pages that retain
                 multiple application rounds or opportunity categories.
                 Configure together with section_end; both are exclusive.
                 Each accepts the step match keys tag, text, attr, and
                 match_regex, for example {"text":
                 "Deadline, 1st November"}. Each marker must match exactly
                 once; ambiguous duplicate markers fail closed.
    section_end  Required partner to section_start. Extraction fails when
                 either marker disappears or the end does not follow the
                 start, preventing stale or unrelated sections from leaking.
    preserve_single_location
                 When true, keep an extracted location string as one value
                 instead of splitting it at commas (default: false).
    steps        Extraction steps run once per job (see: ws help steps).
                 The first step with a field (usually title) is the stop
                 condition — when it can't find a match, extraction ends.
                 A non-empty list is mandatory when the explicit empty-state
                 contract is configured; missing steps fail closed.
    defaults     Default field values applied when extracted value is absent.
                 Supports: description, locations (list), employment_type,
                 job_location_type, date_posted, valid_through.
    defaults_by_title
                 Exact extracted title -> defaults mapping for pages that omit
                 per-role fields. Supports the same fields as defaults; title
                 defaults override global defaults, but extracted values win.
    exclude_titles
                 Exact extracted titles to omit after parsing.
    exclude_title_regex
                 Bounded regex matching extracted titles to omit. Use this for
                 mixed opportunity pages that publish non-employment entries
                 such as calls for tender alongside jobs.
    exclude_description_regex
                 Bounded regex matching the normalized visible description
                 text to omit. Use this for unpublished placeholder content so
                 a role becomes discoverable as soon as its description is
                 replaced with substantive text.
    valid_through_regex
                 Optional regex with a capture group used to read a deadline
                 from the extracted description. A description deadline takes
                 precedence over a board default.
    valid_through_patterns
                 Ordered list of {"regex": ..., "format": ...} objects for
                 pages that publish deadlines in multiple formats. Cannot be
                 combined with valid_through_regex. Extracted fields take
                 precedence, then these patterns, then the board default.
    valid_through_format
                 Python strptime format for non-ISO deadlines. English ordinal
                 day suffixes are ignored (for example, 29th -> 29).
    exclude_expired
                 When true, omit opportunities after their valid_through date.
                 The deadline is inclusive and compared in UTC. Missing or
                 invalid deadlines fail the cycle closed instead of publishing
                 potentially expired opportunities. Accepted opportunities
                 preserve the normalized deadline in extras.
    + browser keys (wait, wait_fallback, timeout, user_agent, etc. — see
      `ws help scraper dom` for the full list; wait_fallback defaults to
      "domcontentloaded" and retries once on Page.goto timeout, set to null
      to opt out)

  Step design tips:
    - Steps run in a loop: after extracting one job, the cursor is where
      the next job starts.  Design steps so the last step's cursor ends
      just before the next job's title.
    - Use stop_tag to limit description collection: {"stop_tag": "h3"}
      stops before the next job's heading. When job boundaries use multiple
      heading tags, pass a list: {"stop_tag": ["h3", "h4"]}.
    - Use optional: true for fields that may not appear on every job.

  Detection:   Not auto-detected. Select manually after inspecting the page.
               Best for small boards (< 50 jobs) with consistent HTML structure."""

MONITOR_KIPT = """\
kipt — NSC KIPT PDF vacancy bulletins (rich)

  Returns:  Full job data (one posting per position in active PDF bulletins)
  Scraper:  Not needed (skipped)
  Cost:     60

  Dedicated monitor for the National Science Center Kharkiv Institute of
  Physics and Technology. The official board archives dated PDF bulletins,
  and each PDF may announce multiple positions. The monitor keeps only
  unexpired bulletins, splits vacancy lines into stable synthetic job URLs,
  and includes the common requirements and application instructions.

  Config:
    max_age_days       Bulletin lifetime (default: 30)
    default_location   Location applied to published positions
                       (default: "Kharkiv, Ukraine")

  Detection:   Official kipt.kharkov.ua vacancy page with dated vacancy PDFs.
  Fields:      title, description, locations, date_posted, language, metadata."""

MONITOR_DOM = """\
dom — Link or Static Listing-Row Extraction (fallback)

  Returns:  URL set, or partial rich rows when rich_rows is configured
  Cap:      50,000 URLs
  Cost:     Highest — use only as last resort.

  Config:
    {"render": true, "wait": "networkidle", "timeout": 30000}

    render         false (default) = static HTTP, true = Playwright
    wait           Wait strategy: "load" | "domcontentloaded" | "networkidle" (default) | "commit"
    wait_fallback  Fallback load state checked on the current document after
                   Page.goto timeout. Default: "domcontentloaded" (applied
                   automatically). Set to null to opt out. The check is capped
                   at 5s and does not reload the page. Use for SPA sites where
                   "networkidle" never settles (persistent analytics/
                   telemetry requests).
    timeout        Navigation timeout in ms (default: 30000)
    user_agent     Custom User-Agent string
    headless       Run headless (default: true)
    proxy          Route traffic through the configured proxy provider. Use for
                   origins that block the crawler's datacenter IP.
    encoding       Optional Python codec name for legacy static HTML whose
                   declared charset is unsupported or incorrect (for example,
                   "euc_jp"). Ignored when render=true.
    request_headers
                   Optional public static HTTP headers: Accept,
                   Accept-Language, Cache-Control, Pragma, and User-Agent.
                   Applied to the listing, pagination, and linked-PDF checks.
                   Secret, cookie, and transport headers are rejected, and
                   redirects must remain same-origin. Requires render=false.
    fetch_url_transform
                   Optional {find, replace} regex rewrite for the listing URL
                   used by static HTTP. The official board URL remains the
                   configured identity; combine with url_transform to turn
                   links from a read-only rendering gateway back into their
                   canonical URLs. The rewrite must match exactly once and
                   produce an absolute HTTP(S) URL. Static single-page
                   discovery only; incompatible with render and pagination.
    retry_statuses Static HTTP status-to-retry-count map, for provider-specific
                   transient responses only (HTTP 400-599, maximum 5 retries).
    persistent_context
                   Use a real browser profile shape for anti-bot challenges.
                   Usually pair with channel: "chrome" and headless: false.
    channel        Browser channel, typically "chrome" with persistent_context.
    stealth        Use Chromium's less-detectable new headless mode.
    warmup_url     Visit this URL in the same browser context before the board.
    actions        Browser action pipeline (see: ws help actions)
    include_board_url
                   Include the board URL itself as a discovered job after a
                   successful fetch. Use only when the board URL is a direct
                   job document (for example, a PDF), not a listing page.
    link_selector  Optional CSS selector for the job anchors themselves.
                   Matching links are trusted as jobs, so this is useful when
                   stable job-card markup exists but URLs lack job keywords.
                   Example: "li.job-card a.details-link"
    empty_selector Optional CSS selector for a stable, explicit empty-state
                   element. When configured, a zero-link page succeeds only
                   if this selector matches; otherwise the cycle fails closed.
                   Requires link_selector and single-page extraction.
    empty_text     Optional case-insensitive text that must occur inside the
                   matched empty_selector. Use when the element exists for
                   both empty and non-empty counts (for example, "0 jobs").
    empty_states   Optional list of 1-4 selector-specific empty states, each
                   with selector and exact_text. A zero-link page succeeds
                   only when one selector matches and its normalized text is
                   exactly equal to exact_text. An entry may also pair
                   required_link_selector with required_link_url_pattern;
                   that state then requires at least one selected anchor and
                   every selected href must fully match the regex. Do not
                   combine with the legacy empty_selector/empty_text pair.
    advertised_total
                   Exact total contract for static link-selector discovery:
                   {"selector": "h2.total", "regex": "^(\\d+) jobs$"}.
                   The regex must contain one decimal capture group. Every
                   matching marker must agree, and the discovered URL count
                   after pagination must equal it exactly. This authenticates
                   zero-job pages and fails closed on a truncated tail before
                   currentness filters such as require_jsonld_jobposting run.
    require_jsonld_jobposting
                   Fetch each discovered detail URL and retain it only when
                   the current page contains schema.org JobPosting JSON-LD.
                   Detail fetch errors fail the monitor cycle; 404/410 and
                   pages without JobPosting data are omitted. Limited to 500
                   discovered URLs. Use for small provider listings that keep
                   stale profile links after an opening closes. Incompatible
                   with rich_rows.
    require_unexpired_pdf
                   Fetch each discovered PDF and retain it only when a
                   configured application deadline has not passed:
                   {"pattern": "Applications .* by (\\d{1,2} [A-Za-z]+ \\d{4})",
                    "date_format": "%d %B %Y"}.
                   The pattern must capture the deadline. Missing, invalid,
                   non-PDF, oversized, or unreadable documents fail the cycle;
                   404/410 documents and expired deadlines are omitted. Limited
                   to 100 URLs; each document is streamed with 20 MB, 200-page,
                   and 2,000,000-character extraction caps. Incompatible with
                   rich_rows. English ordinal day suffixes (1st, 2nd, 3rd,
                   4th) are normalized before parsing.
    require_pdf_text
                   Exhaustively classify each discovered PDF by extracted text:
                   {"include": "employer regex", "exclude": "member regex"}.
                   Use this to scope a mixed official opportunities directory
                   to the configured employer. Each document must match exactly
                   one regex; ambiguous or unknown documents fail the cycle
                   instead of silently producing a partial or zero inventory.
                   Non-PDF, oversized, or unreadable documents also fail the
                   cycle. Limited to 100 URLs and incompatible with rich_rows.
    exclude_detail_selector
                   Fetch each discovered detail URL and omit it when this CSS
                   selector matches. Use when a first-party board mixes unique
                   email-only roles with postings mirrored by another configured
                   ATS, for example:
                   "a[href*=\"apply.workable.com\"][href*=\"/j/\"]".
                   Detail fetch errors fail the cycle; 404/410 pages are omitted.
                   Limited to 500 discovered URLs and incompatible with rich_rows.
    rich_rows      Optional static listing-row extraction. It supports the
                   ordinary sequential pagination config shown above:
                   {"row_selector": ".job", "link_selector": ".job-title a",
                    "location_selectors": [".job-location", ".job-country"],
                    "total_selector": ".jobs-total .total"}
                   When the row itself is the job anchor, omit link_selector:
                   {"row_selector": "a.job[href]", "title_selector": ".title",
                    "location_selectors": [".location"]}.
                   Optional metadata_selectors maps stable row labels into
                   discovered-job metadata for job_filter classification:
                   {"metadata_selectors": {"opportunity_type": ".type"}}
                   Optional section_start and section_end limit rows to the
                   authoritative category between two markers. Both are
                   exclusive and fail closed if page structure drifts:
                   {"section_start": {"selector": "h3", "text": "Jobs"},
                    "section_end": {"selector": "h3#student-projects"}}.
                   Optional active_urls and inactive_urls form an exact,
                   bounded lifecycle partition. Both lists are required
                   together. Active URLs are published, known inactive URLs
                   are ignored, and any selected URL in neither list fails the
                   cycle closed for review. Query strings and fragments are
                   stripped before classification and identity generation;
                   configure undecorated URLs. This is intended for
                   authoritative pages that retain expired document links.
                   Rows whose URL is stored directly on the row can instead use
                   {"row_selector": "tr[data-href]", "link_attr": "data-href",
                    "title_selector": "td.title",
                    "location_selectors": ["td.city", "td.country"]}.
                   The selected link or title node text becomes the title;
                   location components are joined in selector order. Every
                   configured field is strict. Set
                   "allow_missing_locations": true only when some listing
                   rows intentionally omit location and the detail scraper
                   enriches it; those rows return locations=null. Otherwise
                   markup drift fails the cycle instead of publishing a partial
                   authoritative result. Incompatible with rendering,
                   browser or partitioned pagination, and include_board_url.
                   total_selector additionally requires an exact non-negative
                   count equal to the accepted unique rows and is limited to
                   single-page extraction.
                   Requires a real detail scraper (not skip) with scraper_config
                   {"enrich": ["description"]}; otherwise the partial-rich
                   runtime path will not schedule description scraping.
    url_filter     Regex filter for discovered URLs (see: ws help monitor sitemap)
                   Keep patterns broad enough to include URL variants
    url_transform  Regex find/replace to rewrite URLs (see: ws help monitor sitemap)
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
    pagination.transient_403
                            Retry HTTP 401/403 and fail the cycle if they persist
                            instead of accepting a partial crawl (default: false).
                            Enable for WAF-gated boards whose proxy may be
                            blocked or throttled between pagination requests.
    pagination.url_template Format string with {page} placeholder for path-based
                            pagination (e.g. "https://example.com/jobs/page/{page}").
                            When set, replaces param_name-based URL building.
                            Useful for sites that use path segments instead of
                            query parameters for pagination.

    Fetching starts at start + increment (page 1 is the board URL itself).
    Stops when: no new links found, fetch fails, or max_pages reached.
    High max_pages is usually safe: small boards terminate early via "no new links".
    Works with both render: false (httpx) and render: true (Playwright).

  Iframe widgets (onlyfy, prescreen, etc.):
    When jobs are listed inside a third-party <iframe>, use the repeat action
    with a "frame" option to click "Show more" inside the iframe:
    {
      "render": true,
      "actions": [
        {"action": "dismiss_overlays"},
        {"action": "repeat", "selector": "a.load-more",
         "frame": "iframe[src*=\\"widget-domain\\"]", "wait_ms": 3000}
      ],
      "url_filter": "\\\\?jh="
    }
    The repeat action injects frame links into the parent page for discovery.
    See: ws task troubleshoot (KB: dom-monitor-jobs-inside-cross-origin-iframe-widget)

  Vagas.com employer boards:
    URLs matching trabalheconosco.vagas.com.br/<employer>/oportunidades are
    auto-configured with their stable ``pagina`` pagination, job-detail URL
    filter, and ``proxy: true``. Vagas.com may reject crawler-host geographies
    with Cloudflare error 1005 before a browser context can be established.
    Pair with ``json-ld`` and ``{"proxy": true}`` for detail enrichment.

  Dualoo portals:
    ``jobs.dualoo.com/portal/<id>`` pages are auto-configured with the stable
    ``a.jobElement`` selector, a same-portal UUID detail filter, and
    ``require_jsonld_jobposting: true``. Their complete detail data is read by
    the auto-configured ``json-ld`` scraper.

  Lucca/Poplee boards:
    Listing roots on ``*.luccasoftware.com/<tenant>`` are auto-configured with
    strict static rich-row selectors. Titles and locations come from the
    server-rendered cards, while the auto-configured DOM scraper enriches the
    complete description from stable detail-page test IDs.

  Discovery:   Extracts links matching link_selector when configured. Otherwise
               extracts all <a href> links and filters for URLs containing
               job/career/position/posting/opening/role/vacancy keywords.

  VAGAS.com:   trabalheconosco.vagas.com.br/{tenant} is detected without a
               page fetch because the origin blocks datacenter egress. The
               generated preset uses the complete /oportunidades listing,
               ?pagina=N pagination, proxy transport, and an auto-configured
               proxy-backed json-ld scraper.

  Dualoo:      jobs.dualoo.com/portal/{id} is detected from static HTML and
               paired with json-ld detail extraction.

  Detection:   ws probe checks static HTML for job links.
               If detected: shows "✓ N URLs". If not: shows "✗ Not detected".
               LinkedIn job-detail links are automatically filtered and
               rewritten for the dedicated linkedin scraper.

  Pair with:   json-ld (try first), linkedin, or dom scraper"""

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
  Cap:      50,000 jobs
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

MONITOR_RECRUITERBOX = """\
recruiterbox — Recruiterbox / Trakstar Hire static listing monitor

  Listing:  GET https://{tenant}.hire.trakstar.com/?limit=100&p={page}
  Legacy:   https://{tenant}.recruiterbox.com redirects to Trakstar Hire
  Returns:  Job URLs from server-rendered HTML
  Scraper:  Auto-configured (json-ld) for title, description, location, and dates
  Cost:     10 (HTTP only; no browser)
  Cap:      50,000 jobs

  Config:
    {"tenant": "acme"}

  Reuses the shared DOM link extractor, HTTP retry policy, and truncation guard.
  The monitor reads the listing's authoritative total, requests 100 rows per
  page, canonicalizes legacy URLs to hire.trakstar.com, and suppresses removals
  if any page is missing, malformed, duplicated, or changes total mid-run.

  Detection:  Direct Recruiterbox/Trakstar URLs or explicit links in career-page HTML.
              Blind tenant guessing is disabled.
  Zero jobs?  An active listing with authoritative total 0 is valid. Trakstar's
              branded inactive-account page is treated as BoardGone.
  Upstream:   ats-scrapers is inventory input only. Jobseek does not import,
              execute, or depend on upstream scraper code."""

MONITOR_KEKA = """\
keka — Keka public career-portal API

  Listing:  GET https://{tenant}.keka.com/careers[/{portal}]
  Jobs:     GET /careers/api/embedjobs/{portal}/active/{identifier}
  Returns:  Full job data (title, safe HTML description, locations,
            employment type, posting date, salary, and metadata)
  Scraper:  Not needed (public endpoint returns full data; skipped)
  Cost:     10 (HTTP only; no browser)
  Cap:      50,000 jobs and a 25 MB jobs payload

  Config:
    {"tenant":"acme","portal":"default",
     "identifier":"11111111-1111-4111-8111-111111111111"}

  The listing bootstrap supplies the stable organization identifier. Discovery
  checks configured tenant/portal/identifier against the URL and live bootstrap,
  then fetches the authoritative all-active-jobs array in one request. Any
  malformed/duplicate record or cap breach fails the whole run, preventing
  partial-result removals. Named portals such as /careers/amfm are supported.

  Detection:  Direct or explicitly linked *.keka.com/careers URLs only; no
              blind tenant guessing. An exact Keka forbidden-portal redirect
              and authoritative listing 404/410 are treated as BoardGone.
  Upstream:   ats-scrapers is inventory input only. Jobseek does not import,
              execute, or depend on upstream scraper code."""

MONITOR_AVATURE = """\
avature — Avature public static listing monitor

  Listing:  https://{host}/{optional-locale}/{portal}/SearchJobs
  Map:      https://{host}/{optional-locale}/{portal}/SearchJobsMaps
  Config:   {"listing_url":"https://acme.avature.net/careers/SearchJobs",
             "portal_id":"4"}

  Returns stable JobDetail, FolderDetail, or PipelineDetail URLs. The monitor
  follows Avature's explicit static next link; it does not use the capped RSS
  feed. Branded custom domains are accepted only after live avature.portal.*
  metadata validation. Direct *.avature.net URLs and links found on the
  company career page are auto-detected without tenant slug guessing.

  Detail scraper: auto-configured shared DOM scraper. ws may refine its DOM
  steps for localized or heavily customized portal templates after sampling.
  A first-page 404/410 is definitive gone; 202/401/403/406 and transport
  failures remain transient. Configure the normal proxy option for WAF-gated
  portals.
"""

MONITOR_MANATAL = """\
manatal — Manatal Careers Page API

  API:      GET https://www.careers-page.com/api/v1.0/c/{slug}/jobs/
  Returns:  Full job data (title, HTML description, location)
  Scraper:  Not needed (skipped)
  Cost:     10
  Browser:  No

  Config:
    {"slug": "care-vietnam"}

    slug    careers-page.com path slug. Auto-detected from board URL.

  Detection:  ws probe shows "Manatal API — slug: X, N jobs"
  Empty boards are authoritative: the public API returns count=0.
"""

MONITOR_SEAMLESSHIRING = """\
seamlesshiring — SeamlessHiring Candidate API

  API:      GET https://{tenant}.seamlesshiring.com/v2/jobs/job-list
  Returns:  Full job data (title, HTML description, location,
            employment_type, job_location_type, date_posted)
  Scraper:  Not needed (skipped)
  Cost:     10
  Browser:  No

  Config:
    {"tenant": "carenigeria"}

    tenant    SeamlessHiring subdomain. Auto-detected from board URL.

  Detection:  ws probe shows "SeamlessHiring API — tenant: X, N jobs"
  Empty boards are authoritative: the candidate API returns total=0.
"""

MONITOR_INTERVIEWEB = """\
intervieweb — Intervieweb / In-recruiting career sites

  Returns:  Complete job-detail URL set
  Scraper:  Auto-configured JSON-LD
  Cost:     10

  Intervieweb embeds the first result page in the career-page HTML and loads
  later pages through a CSRF-protected POST endpoint. The monitor resolves the
  current endpoint and token on every run and walks every advertised page.

  Config:   No provider config required. Generic url_filter/url_transform and
            proxy options remain available.

  Detection: Direct *.intervieweb.it career pages containing the
             url-for-announces, vacancyListCareer, and researchAnnounces
             protocol markers.

  Pair with: json-ld (auto-configured). Intervieweb detail pages publish
             structured title, location, description, and posting dates.
"""


MONITOR_BRASSRING = """\
brassring — BrassRing / Infinite Talent TGnewUI

  Listing:  https://{host}/TGnewUI/Search/Home/Home?partnerid={id}&siteid={id}
  Returns:  Full job data (title, HTML description, location, posting date,
            department metadata, and stable job-detail URL)
  Scraper:  Skipped — the public search responses are rich
  Cost:     10 (one browser session; bounded 50-row API pages)

  Config:   partner_id and site_id are auto-detected from the board URL.

  The monitor submits an unfiltered empty search, captures the first-party
  MatchedJobs response, and walks every advertised result page. Pagination
  fails closed when totals change, a page is skipped, or required fields are
  malformed, preventing an incomplete cycle from delisting the missing tail.
  Boards may use branded hosts; the TGnewUI route and numeric partner/site IDs
  are the provider fingerprint.
"""


MONITOR_UKG = """\
ukg — UKG Pro public recruiting API

  Listing:  https://{host}/{tenant}/JobBoard/{board_id}
  Search:   POST {listing}/JobBoardView/LoadSearchResults
  Config:   {"host":"recruiting.ultipro.com","tenant":"ACM1000ACME",
             "board_id":"11111111-1111-4111-8111-111111111111"}

  Returns rich summaries from bounded, streamed API pages: title, locations,
  employment and workplace type, posting date, brief description, and stable
  OpportunityDetail URLs. The shared embedded scraper enriches only the full
  Description field from UKG's CandidateOpportunityDetail JSON constructor.

  Detection accepts direct or explicitly linked public UKG board URLs on
  recruiting*.ultipro.com and recruiting.ultipro.ca. It never guesses tenant
  or board UUIDs. First-page 404/410 is definitive gone; transient auth, rate
  limit, transport, and server failures fail the run without removing jobs.
  Pagination is capped at 50,000 opportunities.

  Upstream ats-scrapers is inventory input only. Jobseek neither imports nor
  executes upstream scraper code.
"""


MONITOR_JOBVITE = """\
jobvite — Jobvite public static listing monitor

  Listing:  https://jobs.jobvite.com/{tenant}
  Branded:  /careers/{tenant}, /{tenant}/jobs/positions
  Returns:  Canonical /{tenant}/job/{id} detail URLs
  Scraper:  Auto-configured shared json-ld scraper
  Cost:     10 (HTTP only; no browser)
  Cap:      50,000 jobs and 5 MB listing HTML

  Config:
    {"tenant":"acme","listing_url":"https://jobs.jobvite.com/acme"}

  The monitor reuses the shared DOM link extractor, HTTP retry policy, and
  bot-challenge guard. It validates Jobvite's first-party careersiteName
  marker before accepting empty results. Branded landing pages are resolved
  only through explicit same-tenant Jobs links, never tenant guessing.

  Detection: Direct Jobvite listing/detail URLs or explicit Jobvite links in
             career-page HTML. Unknown-tenant redirects are BoardGone;
             transient status, transport, empty-body, and malformed-page
             failures do not remove jobs.
  Upstream:  ats-scrapers is inventory input only. Jobseek does not import,
             execute, or depend on upstream scraper code.
"""


MONITOR_PAGEUP = """\
pageup — PageUp public static listing monitor

  Listing:  https://careers.pageuppeople.com/{instance}/{source}/{locale}
  Returns:  Rich title summaries plus stable /job/{id}/{slug} detail URLs
  Scraper:  Auto-configured shared static DOM description enrichment
  Cost:     10 (HTTP only; no browser)
  Cap:      50,000 jobs, 500 jobs/page, 5 MB/page

  Config:
    {"instance":873,"source_pointer":"cw","locale":"en-us",
     "listing_url":"https://careers.pageuppeople.com/873/cw/en-us"}

  Each page is checked against PageUp's first-party PU.Jobs.source identity
  when present, stable visible titles, exact page size, and explicit next-page
  remaining count. Branded proxy templates that omit PU.Jobs.source are accepted
  only with non-empty same-board job links; they can never assert an empty board.
  Duplicate responsive-layout anchors are collapsed. Missing, overlapping,
  conflicting, or changing pages fail the run instead of removing jobs. Pages
  stream in bounded batches to keep worker heartbeats live.

  The monitor never fetches per-job details. The shared DOM scraper enriches
  only descriptions on its normal schedule and recognizes PageUp's explicit
  jobnotfound redirect as a permanent gone signal.

  Detection: Direct PageUp listing/detail URLs or explicit PageUp links in a
             career page. Instance IDs are never guessed.
  Zero jobs? A first-party listing with authoritative total 0 is valid.
  Upstream:  ats-scrapers is inventory input only. Jobseek does not import,
             execute, or depend on upstream scraper code.
"""


MONITOR_TALEO = """\
taleo — Taleo Business Edition static listing monitor

  Listing:  GET https://{host}/{partition}/ats/careers/v2/searchResults
  Returns:  Canonical requisition URLs from server-rendered HTML
  Scraper:  Auto-configured (json-ld) for full JobPosting details
  Cost:     10 (HTTP only; no browser)
  Cap:      50,000 jobs

  Config:
    {"host":"phe.tbe.taleo.net","partition":"phe01","org":"ACME","cws":1}

  Reuses the shared DOM link extractor and HTTP retry policy. Each ten-row
  page is checked against Taleo's authoritative total or exact next-row cursor;
  missing, duplicated, redirected, or malformed child pages fail the run
  instead of removing jobs. Both official TBE v2 listing themes are supported.
  Initial discovery follows only validated same-organization migrations across
  official Taleo clusters and records the resolved identity in ws.

  Detection: Direct Taleo TBE URLs or explicit links in career-page HTML.
             Blind organization guessing is disabled.
  Zero jobs? An active listing with authoritative total 0 is valid.
  Upstream:  ats-scrapers is inventory input only. Jobseek does not import,
             execute, or depend on upstream scraper code."""

MONITOR_BEISEN = """\
beisen — Beisen modern API + legacy static listing monitor

  Modern:  POST https://{tenant}.zhiye.com/api/Jobad/GetJobAdPageList
  Legacy:  GET  https://{tenant}.zhiye.com/{Social|index}?PageIndex={page}
  Returns:  Full job data for modern portals; partial rich data for legacy portals
  Scraper:  Modern is skipped; legacy auto-reuses DOM description enrichment
  Cost:     10 (HTTP only; no browser)
  Cap:      50,000 jobs

  Modern config:
    {"tenant":"acme","variant":"modern","portal_id":"...","tenant_id":123}

  Legacy config:
    {"tenant":"acme","variant":"legacy","listing_path":"/Social",
     "legacy_template":"standard"}

  Detection verifies the public portal bootstrap and records its exact generation.
  Modern discovery uses 1,000-row sequential API pages and needs no per-job fetch.
  Legacy discovery validates every advertised HTML page, returns title/location/date,
  and lets the existing DOM scraper enrich only descriptions on newly seen jobs.

  Upstream: ats-scrapers is inventory input only. Jobseek does not import, execute,
            or depend on upstream scraper code."""

MONITOR_SMARTRECRUITERS = """\
smartrecruiters — SmartRecruiters Posting API (URL-only or localized rich data)

  API:      GET https://api.smartrecruiters.com/v1/companies/{token}/postings?limit=100&offset=0
  Returns:  URL set by default; configured exact-jobId locale collapse returns rich data
  Scraper:  Auto-configured (smartrecruiters) for the default URL-only mode
  Cap:      50,000 jobs

  Config:
    {"token": "smartrecruiters"}
    {"token": "HMGroup",
     "canonical_job_id_url_template": "https://career.hm.com/job/{job_id}/",
     "language_preference": ["en", "de", "fr", "it"]}

    token    Company identifier. Auto-filled by ws probe from:
             1. Direct URL (jobs.smartrecruiters.com/{token})
             2. Inline JS scan for SmartRecruiters API references
             3. Slug-based API probe (derives slug from domain)

    canonical_job_id_url_template
             Optional, narrowly opt-in stable identity for tenants that publish
             locale variants. Fetches every detail, groups only by exact jobId,
             and returns one rich job with localizations. The template must be
             absolute HTTPS and contain exactly one {job_id} placeholder.
    language_preference
             Optional ISO 639-1 primary-language order for localized mode.

  Detection:  ws probe shows "SmartRecruiters API — token: X, N jobs"
  Zero jobs?  Verify token — try the API URL directly in a browser"""

MONITOR_SOFTGARDEN = """\
softgarden — Softgarden ATS (HTML scraping, no auth)

  Listing:  GET https://{slug}.softgarden.io
  Returns:  Job detail URLs (built from inline JS job IDs)
  Scraper:  Auto-configured (json-ld) — extracts JSON-LD JobPosting from detail pages
  Cap:      50,000 jobs
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

MONITOR_ALMACAREER = """\
almacareer — AlmaCareer (Capybara) career portals (CZ + SK)

  API:      POST https://api.capybara.lmc.cz/api/graphql/widget
            (single GraphQL endpoint shared by CZ and SK)
  Returns:  Full job data (title, HTML description, locations, employment_type,
            date_posted, base_salary, language)
            metadata: id, country (cz|sk), company_name, fields, professions
  Scraper:  Not needed (API returns full HTML via content.htmlContent)
  Cap:      50,000 jobs

  Covers:
    CZ      *.jobs.cz       (LMC / Profesia / Jobs.cz)
    SK      *.topjobs.sk    (Profesia SK)

  Note:     The GraphQL endpoint requires a per-tenant widgetId + x-api-key.
            Both are extracted automatically from
            https://{host}/assets/js/script.min.js (embedded in each
            tenant's Capybara bundle).
            Pagination runs at 10 items per page (server-side cap) — the
            monitor walks every page and then fetches each jobAd's full
            htmlContent concurrently.

  Config:
    {"slug": "mcdonalds", "country": "cz"}
    {"host": "mcdonalds.topjobs.sk"}

    slug     Customer subdomain (e.g. "mcdonalds"). Auto-filled by ws probe
             from the board URL.
    country  "cz" for *.jobs.cz or "sk" for *.topjobs.sk. Auto-filled.
    host     Optional explicit override (e.g. for custom domains).
    widget_id / api_key / detail_path
             Optional pre-seeded overrides from ws probe — otherwise
             re-fetched each monitor cycle from the tenant's script.min.js.

  Detection:  ws probe shows "AlmaCareer (Capybara) — <slug> [<CC>], N jobs"
  Zero jobs?  Verify the tenant serves jobs on the listing page (the React
              bundle may render 0 when all widgets are empty).
              Check https://{host}/assets/js/script.min.js responds 200."""

MONITOR_TRAFFIT = """\
traffit — TRAFFIT ATS (Public JSON API, no auth)

  API:      GET https://{slug}.traffit.com/public/job_posts/published
  Headers:  X-Request-Page-Size, X-Request-Current-Page (pagination)
  Returns:  Full job data (title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary, language)
            extras: requirements, responsibilities, benefits (HTML)
            metadata: reference, department
  Scraper:  Not needed (API returns full data, scraper step is skipped)
  Cap:      50,000 jobs
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
  Returns:  Partial job data (URL, title, location, employment type)
  Cap:      50,000 URLs
  Note:     Paginated HTML listing pages (10 per page).
            Pagination via tc{tableNr}=p{page} query params.
            1,000+ customers in DACH (Switzerland, Germany, Austria).
            Each customer has a unique HTML template on detail pages, so a
            scraper is still required for descriptions.

  Config:
    {"customer_id": "2698"}
    {"customer_id": "5181", "region": "de"}
    {"customer_id": "3040", "listing_path": "/Jobs/3?CompanyID=32",
     "strict_listing_contract": true,
     "expected_employer": "Example University",
     "employer_field_id": "column_value_1184173",
     "empty_state_text": "No entries were found."}

    customer_id  Numeric customer ID from URL. Auto-filled by ws probe from:
                 1. Direct URL (recruitingapp-{ID}[.de].umantis.com)
                 2. Page HTML scan for Umantis markers
                 No blind probe — customer IDs are numeric, not derivable.
    region       Subdomain region: "" for .umantis.com, "de" for
                 .de.umantis.com. Auto-filled from URL.
    listing_path Override listing page path (default: /Jobs/All)
                 Filtered/shared-tenant URLs keep their path and query
                 automatically (for example, CompanyID=32).
    strict_listing_contract
                 Fail closed unless navigation totals/ranges and exact
                 token-bearing next links prove the complete inventory.
    Identity     Numeric provider vacancy IDs are emitted through the stable
                 /Vacancies/{id}/Description route. Umantis redirects that
                 route to an available locale, so /1, /2, /3, and /4 aliases
                 cannot create distinct postings or an unusable scrape URL.
                 Configure DOM scraping with same_origin_redirects=true so
                 every locale redirect hop fails closed outside the tenant.
    expected_employer
                 Exact employer text required in the configured listing field
                 and authoritative detail metadata (strict mode).
    employer_field_id
                 Stable listing column-value element ID containing the
                 employer (strict mode; for example column_value_1184173).
    empty_state_text
                 Visible no-results text required together with an advertised
                 zero total (strict mode; hidden or script-only text is
                 rejected).

  Detection:  ws probe shows "Umantis — ID: X, N jobs"
  Zero jobs?  Verify customer_id — visit the listing URL directly
  Pair with:  json-ld (try first) or dom scraper with enrich: [description]"""

MONITOR_EARCU = """\
earcu — eArcu live-vacancy XML feed

  Feed:     GET {portal-prefix}/allvacancies/
  Returns:  Full job data (URL, title, description, locations, publication date)
  Scraper:  Not needed (feed returns full data)
  Cap:      50,000 jobs
  Note:     Uses eArcu's live-only vacancy feed. This remains accessible when
            the browser search route is protected by AWS WAF and avoids the
            soft-200 "Vacancy Closed" response used by inactive detail pages.

  Config:
    {"feed_url": "https://careers.example.com/jobs/allvacancies/"}

    feed_url  Public eArcu allvacancies XML URL. Auto-filled by ws probe
              from listing URLs such as /jobs/vacancy/find/results/.

  Detection:  ws probe shows "eArcu live-vacancy feed — N jobs at URL"
  Zero jobs?  A valid empty <positions> feed means the board currently has
              no advertised vacancies."""

MONITOR_RIPPLING = """\
rippling — Rippling ATS Job Board API

  API:      GET https://api.rippling.com/platform/api/ats/v1/board/{slug}/jobs
  Returns:  Job posting URLs (https://ats.rippling.com/{slug}/jobs/{uuid})
  Scraper:  Auto-configured (rippling) — detail API fetches full data daily
  Cap:      50,000 jobs
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
  Cap:      50,000 jobs
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
  Cap:      50,000 jobs
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
rss — RSS 2.0 Feed Monitor + legacy SuccessFactors (presets: successfactors, teamtailor, generic)

  Feed:     GET {feed_url}
  Returns:  Feeds: full job data. Legacy SuccessFactors: title, location,
            posting date, and stable URL; static DOM enriches description.
            metadata: id and preset-specific fields
  Scraper:  Feeds are skipped. Legacy SuccessFactors automatically uses the
            static DOM scraper scoped to .joqReqDescription.
  Cap:      50,000 jobs
  Note:     One monitor type with multiple ATS presets:
            - successfactors: /googlefeed.xml (Google Base namespace)
              or native static DWR pagination for /career?company=... boards
            - teamtailor: /jobs.rss (offset-paginated)
            - generic: standard RSS 2.0 (manual feed URL)

  Config:
    {"preset": "successfactors", "feed_url": "https://jobs.sap.com/googlefeed.xml"}
    {"preset": "successfactors", "fetch_company": true,
     "job_filter": {"exclude": "(?i)subsidiary name"}}
    {"preset": "successfactors", "variant": "legacy",
     "host": "career5.successfactors.eu", "company": "1657261P"}
    {"preset": "teamtailor", "feed_url": "https://company.teamtailor.com/jobs.rss"}
    {"preset": "generic", "feed_url": "https://example.com/jobs.rss"}

    preset     Feed parser preset. Auto-detected when possible.
               Defaults to "generic" when not set.
    feed_url   RSS URL. For known presets, ws probe can auto-fill this from
               the board URL; for generic feeds set it explicitly.
    variant    SuccessFactors only: "feed" or "legacy". Legacy identity and
               listing_url are auto-filled from strict SAP board URLs.
    fetch_company  SuccessFactors feed only: fetch each public detail page and
               store tenant customfield1 in metadata.company. Use job_filter
               with field=metadata.company for mixed-tenant career sites.
               Enrichment fails closed when a detail page cannot be read.
    detail_fields  SuccessFactors feed only: map metadata keys to tenant
               data-careersite-propertyid values (for example service=dept).
               Configured properties are required and fail closed when absent.

  Detection:  ws probe shows labels like:
              "SuccessFactors RSS — <feed_url>, N jobs"
              "SuccessFactors legacy DWR — company: X @ host, N jobs"
              "Teamtailor RSS — <feed_url>, N jobs"
              "RSS (generic) — <feed_url>, N jobs"
  Zero jobs?  Verify feed_url directly in a browser and confirm it returns
              job items (not an empty feed or non-RSS endpoint)."""

MONITOR_WORKABLE = """\
workable — Workable Posting API

  API:      POST https://apply.workable.com/api/v3/accounts/{token}/jobs
  Returns:  Job URLs (scraper fetches details separately on daily schedule)
  Scraper:  Auto-configured (workable) — no manual selection needed
  Cap:      50,000 jobs
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
  Cap:      50,000 jobs
  Note:     Monitor discovers URLs only via the list API (lightweight, hourly).
            A dedicated workday scraper fetches full details (title, description,
            locations, etc.) from the detail API on a daily schedule.
            Max page size is 20 (API returns 400 for higher values).
            API caps results at 2000 per query — automatically splits by
            facet (e.g. job category) for companies with >2000 listings.
            Multi-site: discovers all tenant job sites via robots.txt and
            aggregates jobs from every site. Set "all_sites": false to
            monitor only the configured site. If robots.txt omits an official
            site, use an explicit ordered "sites" list. Cross-site title,
            locale, and copy-suffix variants collapse by requisition ID while
            retaining the first site's valid URL for scraping.

  Config:
    {"company": "nvidia", "wd_instance": "wd5", "site": "NVIDIAExternalCareerSite"}

    company       Company subdomain. Auto-filled by ws probe from:
                  1. Direct URL ({company}.wd{N}.myworkdayjobs.com/{site})
                  2. Inline HTML scan for Workday markers
    wd_instance   Workday instance (e.g. wd1, wd5). Auto-filled from URL.
    site          Career site identifier. Auto-filled from URL path.
    all_sites     Discover all tenant sites via robots.txt (default: true).
                  Set false to monitor only the configured site.
    sites         Optional ordered list of 1-20 exact official site tokens.
                  The first entry must match site. Overrides robots.txt and
                  cannot be combined with all_sites=false or search_text.
    split_facet   Optional provider facetParameter proven exhaustive for this
                  tenant (for example, "Location_Country"). Use only when the
                  automatic largest-value facet omits unclassified jobs. The
                  monitor fails closed if this facet disappears, contains a
                  capped value, or any resulting group is incomplete.

  URL format:   https://{company}.wd{N}.myworkdayjobs.com/{site}
                May include locale prefix: /en-US/{site} (stripped automatically)

  Detection:  ws probe shows "Workday API — {company}/{site}, N jobs"
  Zero jobs?  Verify URL — try the list API URL directly in a browser"""

MONITOR_PAYLOCITY = """\
paylocity — Paylocity embedded job data

  Listing:  GET https://{tenant}recruiting.paylocity.com/Recruiting/Jobs/All/{id}/...
  Returns:  Rich summaries (URL, title, location, date, department)
  Scraper:  Auto-configured (paylocity) — enriches description and work types
  Note:     Jobs are decoded from window.pageData in server-rendered HTML.
            No browser or Job Feed API key is required. Empty boards are valid
            and remain detectable with a 0-job count.

  Config:   None needed — the board URL is used directly.
            Optional {"proxy": true} routes rate-limited or WAF-gated boards
            through the configured proxy provider.

  Detection:  ws probe shows "Paylocity embedded data — N jobs"
  Zero jobs?  Confirm window.pageData.Jobs is empty; search-engine counts may
              lag after postings close."""

MONITOR_ADP = """\
adp — ADP Workforce Now public listing API

  Listing:  GET https://workforcenow.adp.com/mascsr/default/mdf/recruitment/recruitment.html?...
  Search:   GET https://workforcenow.adp.com/mascsr/default/careercenter/public/events/staffing/v1/job-requisitions
  Returns:  Rich listing data in complete 20-job pages
  Scraper:  Auto-configured native ADP detail scraper, including DOCX attachments
  Browser:  Not required. Both listing and detail APIs are public HTTP endpoints.
  Note:     Reuses Jobseek's shared API HTTP transport, retry classifier, and
            pagination engine. Count drift, invalid rows, duplicates, and the
            50,000-job cap suppress tombstoning. No upstream scraper code or
            runtime dependency is used.

  Config:
    {"cid": "00000000-0000-0000-0000-000000000000",
     "cc_id": "19000101_000001", "locale": "en_US"}

    cid      ADP client UUID.
    cc_id    Career-center identifier.
    locale   ADP underscore locale. All fields are auto-filled only from a
             direct or explicitly linked Workforce Now board URL; no blind
             company-name or slug guessing is performed.

  Detection:  ws probe shows "ADP Workforce Now API — cid: X, career center: Y, N jobs"
  Zero jobs?  A valid response reports totalNumber=0 and jobRequisitions=[]."""

MONITOR_PAYCOM = """\
paycom — Paycom public portal API

  Bootstrap: GET https://www.paycomonline.net/v4/ats/web.php/portal/{token}/career-page
  Listing:   POST the validated regional /api/ats/job-posting-previews/search endpoint
  Returns:   Rich summaries (URL, title, description preview, location, work type)
  Scraper:   Auto-configured Paycom API scraper — enriches authoritative details
  Note:      The short-lived public session token and regional API origin are
             read from each portal's server-rendered config. Both monitor and
             scraper reuse the same validated bootstrap. The API origin is
             restricted to HTTPS Paycom hosts. Listing pagination is bounded,
             retried, count-checked, and does not hydrate details per job.

  Config:
    {"token": "0123456789abcdef0123456789abcdef"}

    token   32-character portal ID. Auto-filled only from direct or explicitly
            linked Paycom public portal URLs; no blind token guessing.

  Detection:  ws probe shows "Paycom API — portal: TOKEN, N jobs"
  Zero jobs?  Confirm the preview API reports count 0 after a valid bootstrap."""

MONITOR_BAMBOOHR = """\
bamboohr — BambooHR public careers API

  Listing:  GET https://{tenant}.bamboohr.com/careers/list
  Returns:  Rich summaries (URL, title, location, employment type, department)
  Scraper:  Auto-configured API preset — enriches description, posting date,
            and authoritative detail fields on the normal scrape schedule
  Note:     The listing is one public JSON request per board. No browser,
            tenant-specific field mapping, or upstream scraper dependency is
            required. Empty boards remain detectable with a 0-job count.

  Config:
    {"tenant": "acme"}

    tenant                    BambooHR subdomain. Auto-filled from direct or
                              explicitly linked
                              https://{tenant}.bamboohr.com/careers URLs.
                              Jobseek does not make blind tenant guesses.
    description_include_regex
                              Optional regex applied to normalized plain text
                              from each job's detail description. Use for a
                              shared group tenant when only one employer brand
                              belongs on the board.
                              Enabling it adds one detail API request per job;
                              filtering is limited to boards with at most 500
                              listed jobs and 1,000-character patterns.
                              Any detail failure fails the cycle rather than
                              risking false delisting.

  Detection:  ws probe shows "BambooHR API — tenant: X, N jobs"
  Zero jobs?  Confirm /careers/list returns an empty result array."""

MONITOR_JAZZHR = """\
jazzhr — JazzHR / ApplyToJob static listing

  Listing:  GET https://{tenant}.applytojob.com/apply/jobs
  Returns:  Canonical job detail URLs from one server-rendered HTML response
  Scraper:  Auto-configured JazzHR scraper — existing JSON-LD parser first,
            existing DOM parser fallback for older themes
  Note:     No browser, pagination, or upstream scraper dependency is needed.
            Shared retry handles empty responses, 202/403 WAF responses, 429,
            and 5xx failures without turning them into successful empty cycles.

  Config:
    {"tenant": "acme"}

    tenant   ApplyToJob subdomain. Auto-filled from direct or explicitly linked
             JazzHR URLs; Jobseek does not make blind tenant guesses.
    proxy    Optional. Enable only when live 403/WAF evidence requires it, and
             mirror it into the scraper config for detail requests.

  Detection:  ws probe shows "JazzHR static listing — tenant: X, N jobs"
  Zero jobs?  A valid page still contains the job_listings_wrapper marker."""

MONITOR_JOBBANK104 = """\
jobbank104 — 104 Job Bank company listing

  Listing:  GET https://www.104.com.tw/company/{token}
  Returns:  Canonical https://www.104.com.tw/job/{job_id} detail URLs
  Scraper:  Auto-configured JSON-LD scraper
  Note:     Uses the public server-rendered employer page instead of 104's
            Cloudflare-guarded private JSON endpoints. Enable proxy for both
            monitor and scraper when crawler egress receives a challenge.
            Count drift produces a truncation-safe result, preventing false
            delisting when a larger employer page is only partially rendered.

  Config:
    {"token": "auzu36g", "proxy": true}

    token  Company identifier from /company/{token}. Auto-filled only from an
           exact unfiltered www.104.com.tw company URL.
    proxy  Routes company and detail requests through the configured provider.

  Detection:  ws probe shows "104 Job Bank company listing — token: X, N jobs"
  Zero jobs?  A valid page explicitly advertises 工作機會(0)."""

MONITOR_COMPUTRABAJO = """\
computrabajo — Computrabajo employer profile

  Listing:  GET https://{country}.computrabajo.com/empresas/ofertas-de-trabajo-de-{slug}-{company_id}
  Returns:  Canonical Computrabajo job-detail URLs from all ?p=N pages
  Scraper:  Auto-configured JSON-LD scraper
  Note:     Use the exact unfiltered employer URL. The server-rendered listing
            exposes 20 jobs per page and an explicit authoritative total.

  Config:   No monitor config required.

  Detection:  ws probe shows "Computrabajo employer profile — company: ID, N jobs"
  Zero jobs?  A valid employer page must explicitly report 0 Ofertas de trabajo."""

MONITOR_JOBSTREET = """\
jobstreet — JobStreet employer profile

  Listing:  GET https://{market}.jobstreet.com/api/jobsearch/v5/search
  Detail:   POST https://{market}.jobstreet.com/graphql
  Returns:  Rich summaries (title, location, employment/work-arrangement type,
            posting date, salary when shown)
  Scraper:  Auto-configured JobStreet scraper hydrates complete HTML descriptions
            through the public anonymous GraphQL detail query
  Note:     Use the canonical unfiltered employer URL ending in
            /companies/{slug}-{company_id}/jobs. Legacy *-jobs search routes
            are Cloudflare-guarded and are not accepted as board identities.
            Malaysia (my) and Singapore (sg) employer profiles are supported.

  Config:
    {"company_id": "175608148114568", "organisation_id": "744981"}

    company_id       Public company-profile ID. Auto-filled from the URL.
    organisation_id  Employer search ID. Auto-resolved and verified by probe;
                     optional in hand-written configuration.

  Detection:  ws probe shows "JobStreet employer profile — company: ID, N jobs"
  Zero jobs?  The employer-scoped search API must report totalCount=0."""

MONITOR_ICIMS = """\
icims — iCIMS server-rendered listings

  Listing:  GET https://{host}/jobs/search?ss=1&in_iframe=1
  Returns:  Stable https://{host}/jobs/{id}/job?in_iframe=1 detail URLs
  Scraper:  Auto-configured JSON-LD scraper
  Note:     Pagination is read from the listing and fetched sequentially
            because iCIMS page state is session-sensitive. Every advertised
            page must succeed before discovery
            is authoritative. Duplicate or capped pages suppress tombstoning.
            JavaScript redirects to custom migrated sites fail detection rather
            than being treated as an empty iCIMS board.

  Config:
    {"host": "careers-acme.icims.com"}

    host    Exact single-label *.icims.com public portal host. Auto-filled from
            direct or explicitly linked iCIMS URLs; no blind host guessing.
            This is host-wide. Filtered regional listing URLs are rejected
            rather than silently widened; use a scoped generic DOM board when
            preserving listing filters is required.

  Detection:  ws probe shows "iCIMS static listing — host: X, N jobs"
  Zero jobs?  A valid empty page still contains the iCIMS_ListingsPage marker."""

MONITOR_HERP = """\
herp — HERP Hire server-rendered listing

  Listing:  GET https://herp.careers/v1/{slug}
  Returns:  Canonical https://herp.careers/v1/{slug}/{job_id} detail URLs
  Scraper:  Auto-configured JSON-LD scraper
  Note:     One static HTML response contains every open requisition. The
            monitor reuses shared strict retry, DOM link extraction, challenge
            detection, and truncation handling; no browser or upstream scraper
            dependency is required.

  Config:
    {"slug": "acme"}

    slug    HERP company slug. Auto-filled only from direct or explicitly
            linked herp.careers/v1/{slug} URLs; no blind slug guessing.

  Detection:  ws probe shows "HERP static listing — slug: X, N jobs"
  Zero jobs?  A valid page still contains the requisition-list container."""

MONITOR_GUPY = """\
gupy — Gupy NextData listing

  Listing:  GET https://{tenant}.gupy.io/
  Returns:  Canonical https://{tenant}.gupy.io/jobs/{job_id} detail URLs
  Scraper:  Auto-configured JSON-LD scraper
  Note:     Reuses Jobseek's shared NextData parser and URL builder for the
            complete server-embedded jobs array, plus shared strict retry,
            challenge detection, and truncation handling. No browser or
            upstream scraper dependency is required.

  Config:
    {"tenant": "acme"}

    tenant  Gupy company subdomain. Auto-filled only from direct or
            explicitly linked *.gupy.io URLs; no blind tenant guessing.

  Detection:  ws probe shows "Gupy NextData listing — tenant: X, N jobs"
  Zero jobs?  A valid page still contains matching NextData career metadata
              and an empty jobs array."""

MONITOR_CORNERSTONE = """\
cornerstone — Cornerstone public career-site API

  Listing:  GET  https://{tenant}.csod.com/ux/ats/careersite/{site_id}/home?c={corp}
  Search:   POST https://{region}.api.csod.com/rec-job-search/external/jobs
  Returns:  Full job data streamed in 100-job pages; no detail fan-out
  Scraper:  None (rich monitor)
  Note:     Validates the short-lived public bootstrap token and regional API
            origin, refreshes once on authorization expiry, and uses shared
            strict POST retry and truncation-safe streaming. No browser or
            upstream scraper dependency is required.

  Config:
    {"tenant": "acme", "site_id": 1, "corp": "acme"}

    tenant   Single-label *.csod.com tenant.
    site_id  Positive career-site ID from the canonical URL.
    corp     Corporation query value from ?c=. Auto-filled only from direct
             or explicitly linked canonical Cornerstone URLs; no blind
             tenant guessing.

  Detection:  ws probe shows "Cornerstone API — tenant: X, site: N, M jobs"
  Zero jobs?  A valid API response reports totalCount=0 and requisitions=[]."""

MONITOR_DAYFORCE = """\
dayforce — Dayforce public career-site API

  Listing:  GET  https://jobs.dayforcehcm.com/{tenant}/{portal}
  Search:   POST https://jobs.dayforcehcm.com/api/geo/{tenant}/jobposting/search
  Returns:  Full job data streamed in 25-job pages; no detail fan-out
  Scraper:  None (rich monitor)
  Browser:  Required. Dayforce's public same-origin BFF rejects stateless HTTP
            replay, so the monitor reuses Jobseek's browser fetch transport.
  Note:     Validates server-rendered site identity over HTTP before browser
            startup, preserves tenant+portal as the complete board identity,
            and uses shared retry, TDM, and truncation handling. No upstream
            scraper code or runtime dependency is used.

  Config:
    {"tenant": "acme", "portal": "CANDIDATEPORTAL"}

    tenant  Dayforce client namespace.
    portal  Case-preserving career-site code. Auto-filled only from direct or
            explicitly linked jobs.dayforcehcm.com URLs; no blind guessing.

  Detection:  ws probe shows "Dayforce API — tenant: X, portal: Y"
  Zero jobs?  A valid search response reports maxCount=0 and jobPostings=[]."""

MONITOR_DARWINBOX = """\
darwinbox — Darwinbox public career-site API

  Listing:  GET  https://{host}/ms/candidatev2/{company_id}/careers
  Search:   POST https://{host}/ms/candidateapi/job/alljobs
  Returns:  Full job data streamed in 100-job pages; no detail fan-out
  Scraper:  None (rich monitor)
  Browser:  Required. Cloudflare rejects stateless replay, so the monitor
            reuses Jobseek's browser fetch transport after opening the public
            same-origin career portal.
  Note:     Validates strict Darwinbox host, portal, response, count, identity,
            and pagination invariants. Incomplete or drifting runs are marked
            truncated to prevent false delisting. The upstream ats-scrapers
            project is inventory-only and is never imported or executed.

  Config:
    {"host": "acme.darwinbox.in", "company_id": "main"}

    host        Full single-tenant *.darwinbox.in or *.darwinbox.com host.
    company_id  Public portal route/API identity (normally "main").

  Detection:  ws probe shows "Darwinbox API — host: X, company: Y"
  Zero jobs?  A valid response reports job_counts=0 and data=[]."""

MONITOR_HRMOS = """\
hrmos — HRMOS server-rendered listings

  Listing:  GET https://hrmos.co/pages/{tenant}/jobs?page={page}
  Returns:  Canonical https://hrmos.co/pages/{tenant}/jobs/{job_id} detail URLs
  Scraper:  Auto-configured JSON-LD scraper
  Note:     Fetches every advertised page sequentially with shared strict
            retry, DOM link extraction, challenge detection, count-drift
            protection, and truncation-safe results. No browser or upstream
            scraper dependency is required.

  Config:
    {"tenant": "acme"}

    tenant  HRMOS company identifier. Auto-filled only from direct or
            explicitly linked hrmos.co/pages/{tenant}/jobs URLs; no blind
            tenant guessing.

  Detection:  ws probe shows "HRMOS static listing — tenant: X, N jobs"
  Zero jobs?  A valid page still contains the jsi-joblist container and count."""

MONITOR_API_SNIFFER = """\
api_sniffer — Direct API Replay or XHR/Fetch Capture

  Replays a configured api_url over plain HTTP by default. Without api_url,
  captures JSON API responses during page load via Playwright. Set
  browser: true only when replay requires page cookies/browser execution.
  Set proxy: true when the API blocks direct crawler egress.

  Returns:  Full job data (if fields auto-mapped) or URL set
  Cost:     80 — between sitemap (50) and dom (100)
  Requires: Playwright only for auto-discovery or browser: true replay

  Config (auto-filled from ws probe monitor):
    {
      "api_url": "https://example.com/api/jobs",
      "method": "GET",
      "json_path": "results.jobs",
      "url_field": "url",
      "url_template": "https://example.com/jobs/{id}",
      "url_template_fields": {"public_id": "customFields[0].value"},
      "slug_fields": ["title"],
      "item_filter": {
        "include": {"provider.owner": ["Internal"]},
        "exclude": {"attributes.country": ["USA"]},
        "exclude_regex": {"provider.name": ["^External(?: Agency)?$"]},
        "require_regex": {"provider.apply_id": "^[0-9a-f-]{36}$"},
        "dedupe_by": ["provider.tenant_id", "provider.apply_id"]
      },
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

    api_url          Captured API endpoint URL (auto-filled). This exact key
                     selects direct HTTP replay; legacy "url" is ignored by
                     runtime code and rejected by CSV validation.
    method           HTTP method: GET or POST (auto-filled)
    json_path        Dot-notation path to jobs array in response
    url_field        Field name containing job URL (if found)
    url_template     URL pattern with {field} placeholders (from DOM cross-ref)
    url_template_fields
                     Optional placeholder aliases for nested item values.
                     Values use the same field-path syntax as fields. Example:
                     {"public_id": "customFields[0].value"}
                     makes {public_id} available inside url_template alongside
                     top-level scalar fields.
    slug_fields      Optional item paths slugified and joined into the {slug}
                     URL-template placeholder.
    item_filter      Optional ``api_url`` request/replay source partition,
                     applied after pagination. ``include`` maps item paths to
                     accepted exact string values; items with missing, null,
                     non-string, or non-matching scalar/list values are omitted.
                     ``exclude`` maps item paths to exact string values;
                     matching scalar or list values are omitted.
                     ``exclude_regex`` maps item paths to bounded regular-
                     expression lists and omits matching scalar or list values.
                     ``require_regex`` maps item paths to bounded regular
                     expressions. Every item remaining in scope must carry a
                     non-empty string that fully matches, otherwise the monitor
                     fails before deduplication or empty-result handling.
                     ``dedupe_by`` is a list of stable identifier
                     paths and retains the first item for each complete,
                     non-empty compound identity. Items missing any identity
                     part remain distinct. A short upstream response remains
                     truncated after filtering. Auto-discovery configs reject
                     this option instead of silently ignoring it.
                     ``dedupe_preference`` makes that representative selection
                     deterministic. It requires ``path``, ordered
                     ``preferred_values``, and ``fallback_by`` paths beginning
                     with ``path``. Preferred values win in declared order;
                     remaining ties use the fallback strings lexically.
    params           Query parameters merged into api_url at request time.
                     Auto-filled from the captured URL (empty and pagination
                     params stripped). Edit result_limit / per_page here to
                     increase page size, and update pagination.increment to match.
    request_headers  Cleaned request headers (auto-filled)
    post_data        POST body string (for POST APIs, null for GET)
                     JSON objects/arrays are serialized compactly at runtime,
                     avoiding nested JSON-string escaping in boards.csv.
    post_data_refresh
                     Refresh short-lived POST fields from the board page before
                     every crawl. ``fields`` maps each POST field to a regex with
                     exactly one capture group; ``source_url`` optionally
                     overrides the board URL. The page fetch shares the API
                     client's cookies. Example:
                       {"fields": {"nonce": "data-nonce=\\\"([^\\\"]+)\\\""}}
    empty_response   Optional mapping of response paths to exact scalar values
                     or an exact ``[]`` empty-list marker that authoritatively
                     identify a successful empty result.
                     When configured, a missing job list fails unless all
                     markers match. Example:
                       {"status": 201, "label": "No offer available"}
    require_pdf_pattern
                     Require linked PDF text to match this bounded regex.
                     Non-matching documents fail the cycle for operator
                     classification. Must be paired with require_unexpired_pdf.
    require_unexpired_pdf
                     Bounded PDF deadline gate with ``pattern`` (capture group)
                     and ``date_format``. Matching documents whose deadline
                     passed are omitted; missing or malformed deadlines fail
                     the cycle. Must be paired with require_pdf_pattern.
    pagination       Pagination config (auto-detected from multiple requests)
                     style is "offset" or "page" for ordinary pagination.
                     Use "cumulative_limit" when a load-more API accepts only
                     an increasing limit and repeats the earlier result prefix;
                     the monitor makes one bounded request using the advertised
                     total instead of accumulating duplicate pages.
    pagination_convergence
                     Optional bounded full-pass proof for an unstable "offset"
                     or "page" inventory. Requires max_passes (3-8),
                     required_no_growth_passes (2 through max_passes - 1), and
                     either item_filter.dedupe_by or ``identity_by``. The latter
                     can name a raw
                     provider-row identity independently of logical output
                     dedupe; these identities must be complete and occur once
                     per pass. ``stable_fields`` (valid only with identity_by)
                     limits cross-pass comparison to relevant item paths, so
                     unprojected content drift does not invalidate the proof.
                     A cycle is authoritative only when the raw identity count
                     exactly equals the unchanged advertised total and the
                     required consecutive passes add no identity. Missing,
                     duplicate, conflicting, or excess identities fail closed.
    url_field_match  Optional exact cross-field validation for url_field during
                     pagination convergence. ``pattern`` must use named capture
                     groups and ``fields`` must map exactly those names to item
                     paths. Every URL must fully match and every captured value
                     must equal its item field; malformed or mismatched rows
                     make the cycle truncated and are not emitted.
    fields           Field mapping (same spec as nextdata: key, nested.key, array[].field)
                     When present → rich mode (scraper skipped)
                     When absent → URL-only (scraper needed)
    wait             Navigation wait strategy: "load", "domcontentloaded", or
                     "networkidle". Default: "load". Use "networkidle" for sites
                     where XHRs fire late; avoid it on heavy sites (analytics/ads).
    wait_fallback    Fallback load state checked on the current document after
                     Page.goto timeout. Default: "domcontentloaded". Set to
                     null to opt out.
                     Note: sniffer monitors depend on network activity to
                     capture XHRs — an early fallback may miss late-loading
                     responses. If API discovery regresses, set to null.
    timeout          Navigation timeout in ms. Default: 20000.
    settle           Seconds to wait after navigation for late XHRs. Default: 3.

  Modes:
    Direct HTTP (api_url present, browser absent/false):
      Fetches the API without opening Playwright. proxy: true routes the
      per-board client through the configured proxy provider.
    Browser replay (api_url present, browser: true):
      Opens the board page for cookies/auth, then replays the API in-browser.
    Auto-discovery (api_url absent):
      Opens Playwright and captures XHR/fetch responses during page load and
      fallback interactions. A timed-out navigation with no usable document
      fails the cycle rather than returning an authoritative empty result.
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
              Prospective CareerCenter pages are detected from their embedded
              medium ID and replayed through the public JSON jobs endpoint.
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

MONITOR_WELCOMETOTHEJUNGLE = """\
welcometothejungle — Welcome to the Jungle public jobs APIs

  Board:    https://www.welcometothejungle.com/<locale>/companies/<slug>/jobs
  Returns:  Full job data (title, HTML description, locations, contract,
            remote policy, posting date, salary, skills and qualifications)
  Scraper:  Not needed (skipped)
  Cost:     10

  The monitor resolves the visible company slug through WTTJ's public
  organization endpoint, queries the public search-only Algolia jobs index,
  deduplicates marketplace/language mirrors, and hydrates each active job from
  WTTJ's public organization job endpoint.

  Config (auto-detected):
    {"slug": "wojo", "locale": "fr", "organization_slug": "nextdoor"}

  slug               Public slug from the company page URL.
  locale             Two-letter URL locale used for canonical job URLs.
  organization_slug  Internal WTTJ slug; may be a legacy company name and is
                     auto-resolved when omitted."""

SCRAPER_JSONLD = """\
json-ld — Structured JobPosting Extractor

  Fetch:    Static HTTP (default) or Playwright (render: true)
  Config:   No field mapping needed

  Parses <script type="application/ld+json"> blocks for JobPosting data.
  Handles @graph arrays and nested structures automatically.
  Uses the first JSON-LD block that contains a JobPosting.
  If JSON-LD is absent, supports explicit job-title/job-description/job-city
  metadata, including primary and secondary locations, working mode, posting
  date, requisition ID, job function, and experience level.

  Optional runtime config:
    render         Use Playwright (default: false)
    actions        Browser action pipeline (auto-enables render)
    wait           Navigation wait strategy (Playwright only)
    wait_fallback  Fallback load state checked on the current document after
                   Page.goto timeout (Playwright only). Default:
                   "domcontentloaded". Set to null to opt out.
    timeout        Navigation timeout in ms (Playwright only)
    ignore_address_region
                   Omit addressRegion while retaining addressLocality and
                   addressCountry. Use only when a provider demonstrably
                   publishes incorrect regions across otherwise valid jobs.

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

  Empty fields?  Page may have partial or unsupported structured metadata.
                 Try dom scraper."""

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
              List of paths: concatenate multiple sources into one field.
                "description": ["intro", "sections[*].content", "footer"]
              Constants (=prefix): literal values for separators or headings.
                "description": ["intro", "=<h3>Details</h3>", "body"]
              Template iteration: {"each": "path[*]", "wrap": "<h3>{title}</h3>\\n{body}"}
                Iterates array of objects, fills {field} placeholders.
              Value mapping: {"path": "type", "map": {"REMOTE": "remote"}}
                Maps values through a lookup dict. Unmapped values → null.
              Target fields: title, description, locations, employment_type,
              job_location_type, date_posted, valid_through, qualifications,
              responsibilities, skills. Prefix with "metadata." for extras.
    render        Use Playwright (default: false)
    actions       Browser action pipeline (auto-enables render)
    wait          Navigation wait strategy (Playwright only)
    wait_fallback Fallback load state checked on the current document after
                  Page.goto timeout (Playwright only). Default:
                  "domcontentloaded". Set to null to opt out.
    timeout       Navigation timeout in ms (Playwright only)

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
              List of paths: concatenate multiple sources into one field.
                "description": ["intro", "sections[*].content", "footer"]
              Constants (=prefix): literal values for separators or headings.
                "description": ["intro", "=<h3>Details</h3>", "body"]
              Template iteration: {"each": "path[*]", "wrap": "<h3>{title}</h3>\\n{body}"}
                Iterates array of objects, fills {field} placeholders.
              Value mapping: {"path": "type", "map": {"REMOTE": "remote"}}
                Maps values through a lookup dict. Unmapped values → null.
              Target fields: title, description, locations, employment_type,
              job_location_type, date_posted, valid_through, qualifications,
              responsibilities, skills. Prefix with "metadata." for extras.
    render        Use Playwright (default: false)
    actions       Browser action pipeline (auto-enables render)
    wait          Navigation wait strategy (Playwright only)
    wait_fallback Fallback load state checked on the current document after
                  Page.goto timeout (Playwright only). Default:
                  "domcontentloaded". Set to null to opt out.
    timeout       Navigation timeout in ms (Playwright only)

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

    steps          Extraction step list (see: ws help steps)
    render         false (default) = static HTTP, true = Playwright
    wait           Wait strategy (Playwright only): load | domcontentloaded
                   | networkidle (default) | commit
    wait_fallback  Fallback load state checked on the current document after
                   Page.goto timeout. Default: "domcontentloaded" (applied
                   automatically). Set to null to opt out. Use for SPA sites
                   where "networkidle" never settles.
    timeout        Navigation timeout in ms (default: 30000)
    user_agent     Custom User-Agent
    headless       Run headless (default: true)
    proxy          Route traffic through the configured proxy provider. Use for
                   origins that block the crawler's datacenter IP.
    fetch_url_transform
                   Optional {find, replace} regex rewrite for the URL used to
                   read the detail page. The canonical posting URL remains
                   unchanged. The rewrite must match exactly once and produce
                   an absolute HTTP(S) URL.
    encoding       Optional Python codec name for legacy static HTML whose
                   declared charset is unsupported or incorrect (for example,
                   "euc_jp"). Ignored when render=true.
    persistent_context
                   Use a real browser profile shape for anti-bot challenges.
                   Usually pair with channel: "chrome" and headless: false.
    channel        Browser channel, typically "chrome" with persistent_context.
    stealth        Use Chromium's less-detectable new headless mode.
    warmup_url     Visit this URL in the same browser context before the job.
    actions        Browser action pipeline (see: ws help actions)
    scope          Optional CSS selector that limits extraction to the job body
    include_document_title
                   With scope, prepend the document <title> for extraction
    include_document_description
                   With scope, prepend meta description text for extraction
    document_fallback
                   Static-only per-format configs for detail URLs that may
                   download PDF or DOCX files instead of returning HTML:
                   {"pdf": {...}, "docx": {...}}. PDF keys match
                   `ws help scraper pdf`; DOCX supports title_source: "text",
                   title_pattern, location_pattern, and defaults. HTML
                   responses continue through the configured DOM steps.

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

  Two modes:
    Browser mode (default): Playwright opens page, captures XHR/fetch responses.
    HTTP mode:  Direct httpx request to a known API endpoint — no Playwright.
                Enable by setting api_url in scraper_config.

  Config (browser mode):
    {"fields": {"title": "name", "description": "content"}}

  Config (HTTP mode):
    {"api_url": "https://api.example.com/jobs/{id}",
     "method": "GET", "json_path": "data.job",
     "fields": {"title": "name", "description": "content"}}

    api_url   API endpoint URL. Supports {id} and other named placeholders.
              By default, {id} is the job page URL's last path segment.
    url_pattern
              Optional regex matched against the job page URL. Named capture
              groups become placeholders in api_url and post_body. Use this
              when an ID is stored in a query parameter, for example:
              "[?&]itemId=(?P<item_id>[^&#]+)" makes {item_id} available.
    method    HTTP method: "GET" (default) or "POST".
    post_body POST request body (JSON string). Supports {id} placeholder.
    json_path jmespath expression to navigate to the job object in the response.
    request_headers  Dict of HTTP headers to include in the request.
    enrich    List of field names to fetch from the detail API when the
              monitor already provides partial data (e.g. ["description"]).
              Only those fields are scraped; others come from the monitor.

    fields    Optional. Dict mapping JobContent fields to JSON response keys.
              Same spec as nextdata: key, nested.key, array[].field.
              If omitted, auto-maps heuristically from captured response.
              List of paths: concatenate multiple sources into one field.
                "description": ["intro", "sections[*].content", "footer"]
              Constants (=prefix): literal values for separators or headings.
                "description": ["intro", "=<h3>Details</h3>", "body"]
              Template iteration: {"each": "path[*]", "wrap": "<h3>{title}</h3>\\n{body}"}
                Iterates array of objects, fills {field} placeholders.
              Value mapping: {"path": "type", "map": {"REMOTE": "remote"}}
                Maps values through a lookup dict. Unmapped values → null.
              Target fields: title, description, locations, employment_type,
              job_location_type, date_posted, valid_through, qualifications,
              responsibilities, skills. Prefix with "metadata." for extras.
    wait          (Browser mode only) Navigation wait strategy: "load",
                  "domcontentloaded", or "networkidle". Default: "load".
    wait_fallback (Browser mode only) Fallback load state checked on the current
                  document after Page.goto timeout. Default: "domcontentloaded".
                  Set to null to opt out. The check does not start a second
                  navigation, so already captured XHRs and DOM state are kept.
    timeout       (Browser mode only) Navigation timeout in ms. Default: 20000.
    settle        (Browser mode only) Seconds to wait for late XHRs. Default: 3.

  Auto-probed via Playwright in ws probe scraper. Requires Playwright.
  Can also be manually selected: ws select scraper api_sniffer
  SEEK AU/NZ job URLs are auto-probed through SEEK's public GraphQL detail
  endpoint, avoiding Cloudflare-blocked direct detail-page navigation.

  How it works (browser mode):
    1. Opens job page with Playwright
    2. Captures all JSON responses during page load
    3. Finds the best single-job response (dict with title + description keys)
    4. Extracts fields using config mapping or heuristic matching

  How it works (HTTP mode):
    1. Sends request to api_url (with {id} substituted from job URL)
    2. Navigates response via json_path to the job object
    3. Extracts fields using config mapping

  When to use:  Job pages are SPAs that load content via XHR/fetch.
                Use HTTP mode when the API endpoint is known (faster, no browser).
                Use browser mode when the endpoint must be discovered at runtime.

  Empty result? Browser mode: page may not load data via XHR — try json-ld/dom.
                HTTP mode: check api_url, json_path, and request headers."""

FIELDS = """\
Job Data Fields — types, formats, importance

  Monitors return DiscoveredJob, scrapers return JobContent. Both use the
  same core fields (all nullable). JobContent adds valid_through.

  Importance:
    Required     title             str       Plain text job title
    Required     description       str       HTML fragment (<p>, <ul>, <h3>, etc.)
    Important    locations         [str]     List of location strings
    Important    job_location_type str       "remote", "hybrid", "onsite"
    Optional     employment_type   str       "full_time", "part_time", "contract", etc.
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
    - base_salary dict: {"currency": "USD", "min": 100000, "max": 150000, "unit": "year"}
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

  Tip: If descriptions are shorter than expected (missing sections like
  "What You'll Do" or "Requirements"), the source data likely splits
  content across multiple fields. Use a list spec to concatenate:
    "description": ["introHtml", "sections[*].content", "closingHtml"]
  Use =prefix constants for HTML headings between sections:
    "description": ["intro", "=<h3>Requirements</h3>", "requirements"]
  Use each+wrap for arrays of titled sections:
    "description": ["intro", {"each": "sections[*]", "wrap": "<h3>{heading}</h3>\\n{body}"}]
  Use map spec for value mapping (boolean/enum conversion):
    "job_location_type": {"path": "homeOffice", "map": {"True": "remote"}}
    Unmapped values produce null (not passthrough).
  Decode APIs that return HTML as entities before storing descriptions:
    "description": {"path": "body", "html_unescape": true}
  Use enrich to scrape only specific fields for rich monitors:
    "enrich": ["description"] — fetches only description from detail pages.
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
    match_regex Require a regex match against the element text
    attr        Match by attribute: "key=substring" or "key" (presence)
    field       Output field name. Omit for anchor-only steps (move cursor)
    offset      Skip N elements after match before extracting (default: 0)
    optional    If true, skip silently when not found (default: false)
    from        Override seek start (e.g. 0 to search from beginning)

  Range extraction (collect multiple elements):
    stop        Stop when element text contains this string
    stop_tag    Stop when element tag matches. Accepts one tag or a list
                (for example, ["h2", "h3"])
    stop_attr   Stop when an element attribute matches (same format as attr)
    stop_regex  Stop when element text matches this regex
    stop_count  Max elements to collect
    to_end      Collect through the final element. Use with scope when the
                complete scoped container is the field value.
    html        If true, preserve HTML tags in output (groups <li> in <ul>)

  Post-processing:
    regex       Regex with capture group — extracts group(1)
    date_input_format
                Explicit strptime format for a source date; emits YYYY-MM-DD
    split       Split result into list on this delimiter

  Matching:
    - All conditions (tag + text + match_regex + attr) must match (AND logic)
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
    {"tag": "p", "field": "description", "stop_tag": ["h2", "h3"], "html": true}
    {"tag": "p", "match_regex": "^\\d+\\.", "field": "title", "regex": "^\\d+\\.\\s*(.+)"}
    {"tag": "time", "field": "date_posted", "date_input_format": "%d-%m-%Y"}
    {"tag": "span", "attr": "class=salary", "field": "salary", "regex": "\\\\$(\\\\d[\\\\d,]+)"}"""

ACTIONS = """\
Browser Action Pipeline — pre-extraction actions for Playwright

  Actions run sequentially after page navigation, before extraction.
  Each action has a 10s timeout (configurable per-action). Failures
  log a warning and continue unless the action sets ``"required": true``.
  Required actions fail the board cycle instead of accepting partial output.

  Used in: dom monitor, dom scraper, nextdata monitor/scraper (with render: true)

  Action types:
    {"action": "click", "selector": "button.load-more"}
        Click first matching element (no-op if not found)

    {"action": "wait_for", "selector": "article.job", "state": "visible"}
        Wait for the first matching element to reach a Playwright locator
        state. State defaults to "visible"; "attached", "hidden", and
        "detached" are also supported. Prefer this over a fixed wait when
        a rendered page exposes a stable readiness selector.

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
          frame      CSS selector for an <iframe> to target (optional).
                     When set, clicks happen inside the iframe (using JS
                     to bypass cross-origin overlays), link counts are
                     measured inside the frame, and after all clicks the
                     frame's links are injected into the parent page so
                     the DOM monitor can discover them.
          force      If true, use Playwright force-click (default: false).
                     Useful when overlays intercept clicks.
        Use for "Load More" / "Show More" buttons on infinite-scroll pages.
        Use frame option when the job listing widget runs inside an iframe
        (e.g. onlyfy, prescreen, or other embedded ATS widgets).

    {"action": "paginate_collect", "next_selector": "a.next", "max_pages": 50,
     "wait_ms": 5000}
        Click through pagination that replaces the current page and collect links
        from every visited page. The collected links are injected into the final
        DOM so the monitor returns the complete URL set.
        Options:
          next_selector       CSS selector for the enabled next-page control
                              (required for non-SuccessFactors layouts)
          max_pages           Max pagination clicks (default: 50)
          wait_ms             Ms to wait after each click (default: 5000)
          page_size_selector  Optional CSS selector for a page-size dropdown
          page_size           Optional value to select before pagination
          force               If true, use Playwright force-click. Useful when
                              consent overlays intercept page controls.
        Use this instead of repeat when each click replaces the current result
        page, including JSF/Visualforce postback pagination. Make next_selector
        exclude the disabled last-page control so pagination terminates cleanly.
        Pagination errors, no-progress clicks, and page-cap exhaustion fail the
        board cycle rather than accepting a partial inventory.
        For example, Prospective boards use the disabled-aware selector
        #button-forward:not(.disableClick).

  Per-action timeout:
    {"action": "click", "selector": ".btn", "timeout": 5}
        Override default 10s timeout (value in seconds)

  Fail-closed action:
    {"action": "click", "selector": ".page-size-50", "required": true}
        Propagate missing-selector, timeout, and execution failures. Use this
        for pagination or enrichment steps whose failure makes the resulting
        URL/content set incomplete. ``paginate_collect`` is always fail-closed.

  Example pipelines:
    "actions": [
      {"action": "dismiss_overlays"},
      {"action": "click", "selector": "button[data-load-all]"},
      {"action": "wait_for", "selector": "article.job"}
    ]

    "actions": [
      {"action": "repeat", "selector": "button.load-more", "max": 30, "wait_ms": 1500}
    ]

    # Collect links across full-page or SPA next-page navigation:
    "actions": [
      {"action": "paginate_collect",
       "next_selector": "#button-forward:not(.disableClick)",
       "wait_ms": 1500, "max_pages": 50}
    ]

    # Click "Show more" inside a cross-origin iframe widget:
    "actions": [
      {"action": "dismiss_overlays"},
      {"action": "repeat", "selector": "a.infinite-next",
       "frame": "iframe[src*=\\"onlyfy\\"]", "wait_ms": 3000}
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
    verified empty    If the official board explicitly says there are no openings
                      and a stable ATS/feed returns a valid empty source, record
                      `ws feedback --verified-empty-board --verdict acceptable`.
                      Do not use this for an unexplained empty DOM page.
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
    → Set max_pages and max_items with ~50% headroom above current job
      count — job volume grows over time and caps should not silently
      truncate new postings
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
    → ws task fail --reason "..." to enter coding mode

  Case studies:
    The KB also contains case studies — end-to-end narratives of how
    complex boards were configured.  Search finds them alongside
    troubleshooting entries.  Use --view to read the full study:
      ws task troubleshoot "lever description split"
      ws task troubleshoot --view spotify-api-sniffer-nextdata.md"""

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

  Verified empty boards:
    --verified-empty-board accepts a tested 0-job config only when the
    official board explicitly has no current openings and a stable ATS/feed
    source was independently verified.  It requires verdict=acceptable and
    forbids per-field ratings; put the evidence in --verdict-notes.

  Examples:
    ws feedback --verdict good --verdict-notes "All fields extracted cleanly"
    ws feedback my-cfg --verdict acceptable --verdict-notes "Locations noisy" \\
        --locations noisy --locations-notes "Missing city for some postings"
    ws feedback --verdict poor --verdict-notes "Description truncated" \\
        --description unusable
    ws feedback --verified-empty-board --verdict acceptable \\
        --verdict-notes "Official board empty; valid Teamtailor RSS verified"
"""

# ── Lookup tables ────────────────────────────────────────────────────────

MONITOR_YCOMBINATOR = """\
ycombinator — YCombinator Jobs (last resort, HTML scraping)

  ⚠ LAST RESORT — all companies eventually outgrow YC and migrate to a
    dedicated ATS (Greenhouse, Lever, Ashby, etc.). Only use this monitor
    when no real ATS board exists for the company.

  Listing:  GET https://www.ycombinator.com/companies/{slug}/jobs
  Returns:  Job detail URLs (extracted from HTML href attributes)
  Scraper:  Auto-configured (json-ld) — extracts JSON-LD JobPosting from detail pages
  Note:     Server-rendered HTML, no pagination observed.
            Job URLs follow /companies/{slug}/jobs/{alphanumeric_id}-{title-slug}.
            Cross-company links on the page are filtered out.

  Config:
    {"slug": "typewise"}

    slug    Company slug on ycombinator.com. Auto-detected from board URL
            (ycombinator.com/companies/{slug}/jobs).

  Detection:  ws probe shows "YCombinator — slug: X, N jobs
              (last resort — prefer a dedicated ATS if available)"
  Zero jobs?  Company may have migrated off YC — check for a real ATS board."""

MONITOR_CARDS: dict[str, str] = {
    "accenture": MONITOR_ACCENTURE,
    "almacareer": MONITOR_ALMACAREER,
    "amazon": MONITOR_AMAZON,
    "bite": MONITOR_BITE,
    "breezy": MONITOR_BREEZY,
    "cnstaff": MONITOR_CNSTAFF,
    "comeet": MONITOR_COMEET,
    "curately": MONITOR_CURATELY,
    "cvwarehouse": MONITOR_CVWAREHOUSE,
    "deel": MONITOR_DEEL,
    "dvinci": MONITOR_DVINCI,
    "eightfold": MONITOR_EIGHTFOLD,
    "gem": MONITOR_GEM,
    "inploi": MONITOR_INPLOI,
    "typify": MONITOR_TYPIFY,
    "greenhouse": MONITOR_GREENHOUSE,
    "beehire": MONITOR_BEEHIRE,
    "hibob": MONITOR_HIBOB,
    "hirehive": MONITOR_HIREHIVE,
    "hireology": MONITOR_HIREOLOGY,
    "turbohire": MONITOR_TURBOHIRE,
    "jarvi": MONITOR_JARVI,
    "jobylon": MONITOR_JOBYLON,
    "johdi": MONITOR_JOHDI,
    "join": MONITOR_JOIN,
    "lever": MONITOR_LEVER,
    "linkedin": MONITOR_LINKEDIN,
    "manatal": MONITOR_MANATAL,
    "headhunter": MONITOR_HEADHUNTER,
    "ashby": MONITOR_ASHBY,
    "adp": MONITOR_ADP,
    "avature": MONITOR_AVATURE,
    "ukg": MONITOR_UKG,
    "bamboohr": MONITOR_BAMBOOHR,
    "beisen": MONITOR_BEISEN,
    "brassring": MONITOR_BRASSRING,
    "candidatus": MONITOR_CANDIDATUS,
    "paycom": MONITOR_PAYCOM,
    "jazzhr": MONITOR_JAZZHR,
    "jobbank104": MONITOR_JOBBANK104,
    "computrabajo": MONITOR_COMPUTRABAJO,
    "jobstreet": MONITOR_JOBSTREET,
    "jobvite": MONITOR_JOBVITE,
    "pageup": MONITOR_PAGEUP,
    "icims": MONITOR_ICIMS,
    "infoniqa": """\
infoniqa — Infoniqa jobexchange form-pagination monitor

  Returns:  Canonical detail URLs from the CSRF/session-bound POST workflow
  Scraper:  Select a detail-page scraper explicitly
  Cost:     10
  Browser:  No

  Config:
    {"employer_name": "EHL Hotelfachschule Passugg"}

  The initial HTML is only a pre-hydration shell and never proves zero. The
  monitor validates the configured employer heading and logo, starts the real
  search POST, drains hasNextJobOffers/showNextJobOffers pagination, and checks
  the exact unique URL count against two independent provider count markers.
  A zero result is accepted only when both counts are zero and hasNextJobOffers
  explicitly returns false. Detail URLs must remain on the configured origin.
""",
    "intervieweb": MONITOR_INTERVIEWEB,
    "gupy": MONITOR_GUPY,
    "cornerstone": MONITOR_CORNERSTONE,
    "darwinbox": MONITOR_DARWINBOX,
    "dayforce": MONITOR_DAYFORCE,
    "herp": MONITOR_HERP,
    "hrmos": MONITOR_HRMOS,
    "recruitee": MONITOR_RECRUITEE,
    "recruiterbox": MONITOR_RECRUITERBOX,
    "keka": MONITOR_KEKA,
    "taleo": MONITOR_TALEO,
    "rippling": MONITOR_RIPPLING,
    "smartrecruiters": MONITOR_SMARTRECRUITERS,
    "softgarden": MONITOR_SOFTGARDEN,
    "traffit": MONITOR_TRAFFIT,
    "earcu": MONITOR_EARCU,
    "umantis": MONITOR_UMANTIS,
    "workable": MONITOR_WORKABLE,
    "welcometothejungle": MONITOR_WELCOMETOTHEJUNGLE,
    "workday": MONITOR_WORKDAY,
    "paylocity": MONITOR_PAYLOCITY,
    "pinpoint": MONITOR_PINPOINT,
    "practicematch": MONITOR_PRACTICEMATCH,
    "personio": MONITOR_PERSONIO,
    "rss": MONITOR_RSS,
    "seamlesshiring": MONITOR_SEAMLESSHIRING,
    "sitemap": MONITOR_SITEMAP,
    "talemetry": MONITOR_TALEMETRY,
    "talentbrew": MONITOR_TALENTBREW,
    "njoyn": MONITOR_NJOYN,
    "prospective": MONITOR_PROSPECTIVE,
    "phenom": MONITOR_PHENOM,
    "nextdata": MONITOR_NEXTDATA,
    "notion": MONITOR_NOTION,
    "oracle_hcm": """\
oracle_hcm — Oracle Cloud HCM REST API monitor

  Auto-detected for URLs matching oraclecloud.com/hcmUI/CandidateExperience.
  Uses the recruitingCEJobRequisitions REST API — no browser needed.

  Board metadata (auto-detected from URL):
    host    Oracle HCM tenant hostname (e.g. "jpmc.fa.oraclecloud.com")
    site    Career site identifier (e.g. "CX_1001", "CampusHiring")

  Rich monitor — returns title, location, date, employment_type.
  Pair with oracle_hcm scraper + enrich: ["description"] for descriptions.
  Handles pagination automatically via finder param offset suffix.

  Optional monitor_config:
    offset_overlap   Number of rows (0-199) to overlap between 200-row pages.
                     Use for very large, high-churn boards where Oracle's
                     offset result set changes during a cycle. The overlap
                     prevents small left shifts from skipping active jobs.
    total_count_tolerance
                     Allowed difference between Oracle's advertised total and
                     the final accessible rows. Use only for a verified tenant
                     whose TotalJobsCount consistently overstates its tail.
    duplicate_row_tolerance
                     Allowed duplicate IDs inside individual response pages.
                     Use only for a verified tenant whose TotalJobsCount counts
                     repeated database rows. Cross-page duplicates remain
                     governed by offset_overlap.""",
    "dom": MONITOR_DOM,
    "inline": MONITOR_INLINE,
    "unifr": """\
unifr — University of Fribourg authoritative source monitor

  Returns:  Full job data for the central FR/DE vacancy widget and bounded
            first-party faculty inventories; PDF URLs for law and RSD.
  Scraper:  skip for rich sources; pdf for law and RSD
  Cost:     10
  Browser:  No

  Config:   {"source": "central"}

  This is a fixed-origin monitor for the University of Fribourg configuration.
  It unions the French and German central inventories by numeric provider ID,
  validates every detail response against that ID and University authority,
  and fails closed on pagination, source inventory drift, unsafe zero results,
  expired departmental listings, or broken source-owned identities.
""",
    "jobs_ch": """\
jobs_ch — JobCloud Employer Profiles (jobs.ch / jobup.ch)

  Returns:  Job detail URLs from the public JobCloud search API
  Scraper:  json-ld (auto-configured)
  Cost:     10
  Browser:  No

  Auto-detected for jobs.ch employer-profile URLs in German, French, or
  English, and jobup.ch profiles in French or English. The portal-specific
  numeric or UUID company ID and locale are inferred from the URL. Pagination
  is exhaustive and checked against totalHits. An explicit API result with
  totalHits=0 is a verified empty board.

  Optional config:
    {"company_id": "134466", "locale": "fr"}
    {"company_id": "6099", "locale": "en", "portal": "jobup"}
    {"document_company_id": "852"}

  Some migrated employer profiles use one UUID as the search filter while
  result documents retain a legacy numeric employer ID. The probe reports
  document_company_id when it detects this alias. Discovery still requires
  every returned document to match that exact configured identity.
""",
    "kipt": MONITOR_KIPT,
    "api_sniffer": MONITOR_API_SNIFFER,
    "mokahr": MONITOR_MOKAHR,
    "recruiter_co_kr": MONITOR_RECRUITER_CO_KR,
    "ycombinator": MONITOR_YCOMBINATOR,
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

SCRAPER_PAYLOCITY = """\
paylocity — Paylocity server-rendered detail scraper

  Page:     GET https://{tenant}recruiting.paylocity.com/Recruiting/Jobs/Details/{id}
  Returns:  title, HTML description, locations, employment_type,
            job_location_type
  Config:   None needed. Optional {"proxy": true} routes WAF-gated detail
            requests through the configured proxy provider.
  Note:     Auto-configured when selecting the paylocity monitor. The detail
            content is server-rendered despite Paylocity's surrounding
            unsupported-browser warning, so Playwright is not required.
"""

SCRAPER_PAYCOR = """\
paycor — Paycor/Newton server-rendered detail scraper

  Page:     GET https://recruitingbypaycor.com/career/JobIntroduction.action?...&id={id}
  Returns:  title, complete HTML description, locations,
            metadata (job_id, openings)
  Config:   None needed.
  Note:     Legacy Newton templates use a generic visible page heading and
            nested tables. This scraper reads the stable gnewtonJob* fields
            directly over static HTTP, so Playwright is not required.
"""

SCRAPER_PHUKETALL = """\
phuketall — PhuketAll employer-board detail scraper

  Page:     GET https://www.phuketall.com/(en/)jobs/{employer}-{job}-phuket/{slug}.html
  Returns:  title, complete HTML description, location, employment_type,
            date_posted and department metadata
  Config:   None needed.
  Note:     Reads PhuketAll's stable structural classes and supports canonical
            Thai field labels, so no browser or translated page is required.
            Detail requests accept only the exact HTTPS provider origin and
            provider job identity, follow at most two identity-preserving
            redirects, and stream the complete response under a 2 MiB cap.
"""

SCRAPER_VERYEAST = """\
veryeast — VeryEast (最佳东方) employer-board detail scraper

  Page:     GET https://job.veryeast.cn/{employer}/{job}
  Returns:  title, complete multi-section HTML description, location,
            employment_type, posting/expiry dates and job metadata
  Config:   None needed.
  Note:     Pair with a strict DOM monitor for the employer listing page.
            The complete server-rendered detail is fetched under a 2 MiB cap;
            oversized pages fail instead of being silently truncated.
"""

SCRAPER_LINKEDIN = """\
linkedin — LinkedIn public guest-job detail scraper

  API:      GET https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{id}
  Returns:  title, HTML description, locations, employment_type,
            job_location_type, metadata (seniority, function, industries)
  Config:   None needed — derives the numeric ID from the public job URL
  Note:     Auto-configured when selecting the linkedin monitor. Runs on the
            daily scrape schedule rather than every monitor cycle.
"""

SCRAPER_HEADHUNTER = """\
headhunter — HeadHunter vacancy detail API scraper

  API:      GET https://api.hh.ru/vacancies/{vacancy_id}
  Returns:  title, HTML description, locations, employment_type,
            job_location_type, date_posted, base_salary, skills and metadata
  Config:   proxy=true plus the standard enrich list (auto-configured)
  Note:     Derives the numeric ID from hh.ru/vacancy/{id}. Runs through the
            configured proxy on the daily scrape schedule so the hourly
            employer monitor remains a single paginated listing pass.
"""

SCRAPER_JOBSTREET = """\
jobstreet — JobStreet vacancy detail GraphQL scraper

  API:      POST https://my.jobstreet.com/graphql
  Returns:  title, complete HTML description, locations, employment type,
            posting date, base salary, expiration date and employer metadata
  Config:   Standard enrich list (auto-configured)
  Note:     Derives the numeric ID from my.jobstreet.com/job/{id}. Runs on the
            normal scrape schedule so the hourly employer monitor performs
            only the company-scoped listing pass.
"""

SCRAPER_ONLYFY = """\
onlyfy — Onlyfy/Prescreen server-rendered detail scraper

  Page:     Public https://{tenant}.onlyfy.jobs/{locale}/job/{handle} URL
  Fetches:  /job/show/{handle}/full?lang={locale}&mode=candidate
  Returns:  title, HTML description, locations
  Config:   language (optional; otherwise derived from the public URL)
  Note:     The public Next.js page is a client shell. This scraper uses the
            stable server-rendered candidate endpoint directly, so Playwright
            is not required.
"""

SCRAPER_PAYCOM = """\
paycom — Paycom public detail API scraper

  Bootstrap: GET the job's public Paycom portal for its short-lived session
  API:       GET the validated regional /api/ats/job-postings/{id} endpoint
  Returns:   title, HTML description and qualifications, locations,
             employment/workplace type, date, salary, and job metadata
  Config:    None needed — portal token and job ID come from the canonical URL
  Note:      Auto-configured with the paycom monitor. It reuses the monitor's
             bootstrap validation and shared HTTP retry path; no browser or
             upstream scraper dependency is required.
"""

SCRAPER_JAZZHR = """\
jazzhr — JazzHR JSON-LD with DOM fallback

  Page:     GET https://{tenant}.applytojob.com/apply/jobs/details/{id}
  Returns:  Standard JobPosting title, HTML description, locations,
            employment/workplace type, date, salary, and structured extras
  Config:   None needed. Optional proxy:true only for verified WAF tenants.
  Note:     Fetches once, then reuses Jobseek's JSON-LD parser. Older themes
            missing JobPosting schema fall back in-memory to the standard
            JazzHR h1.job_title and div.job_description DOM structure.
            No browser or upstream dependency is required.
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

SCRAPER_EIGHTFOLD = """\
eightfold — Eightfold.ai Detail scraper (JSON-LD + position API fallback)

  Fast path: fetch the HTML page and parse schema.org/JobPosting from the
             inlined <script type="application/ld+json"> block. This is the
             same path the generic json-ld scraper uses.
  Fallback:  if the page does not contain a JobPosting block, call the
             public position API:
               GET https://{tenant}.eightfold.ai/api/apply/v2/jobs/{id}?domain={d}
             which returns title, locations, HTML description, t_create, and
             ats_job_id for every active position id.

  Returns:  title, HTML description, locations, date_posted, metadata
            (ats_job_id, display_job_id, department, business_unit).
  Config:   None needed — job id and domain are parsed from the URL.
  Note:     Auto-configured when selecting the eightfold monitor. Fallback
            adds a second HTTP call only for pages missing JSON-LD, so the
            happy-path cost is unchanged.
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

SCRAPER_JOHDI = """\
johdi — Johdi Suite offer-detail API scraper

  API:      GET https://ats.johdisuite.ch/api/company/{company_key}/
            publicationFlows/{flow}/offer/{id}/{locale}
  Returns:  title, HTML description, locations, employment_type,
            date_posted, language, activity range, and application metadata
  Config:   company_key, flow, and locale (auto-filled by the johdi monitor)
  Safety:   The bounded JSON detail ID must exactly match the stable URL ID.
  Note:     Runs on the normal scrape schedule, not every monitor cycle.
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

SCRAPER_PDF = """\
pdf — PDF document scraper

  Downloads PDF files and extracts text content using pypdf.
  Used for companies that host job descriptions as PDF files
  (e.g. on Webflow CDN) rather than HTML pages.

  Returns:  title, HTML description, locations (when configured)
  Config:   title_source ("url" or "text"), title_pattern (regex),
            location_pattern (regex applied to PDF text),
            location_url_pattern (fallback regex applied to the decoded filename),
            fields_pattern (regex with named title/location groups for
            table-like PDF layouts; named values take precedence and the
            field-specific patterns remain fallbacks),
            repair_split_initial (opt-in repair for M\\nechanical-style artefacts),
            ocr (opt-in fallback for image-only PDFs),
            ocr_languages (Tesseract languages, default "eng"),
            ocr_scale (integer PDF render scale 1-4, default 2; OCR is
            limited to 20 pages and 30 million rendered pixels per page),
            defaults (missing-only JobContent fields; extracted values win,
            e.g. {"locations": ["Lausanne, Switzerland"]}; field types,
            canonical enums, ISO dates, and salary shapes are validated),
            request_headers (optional allowlisted public download headers;
            redirects must remain same-origin)
  Note:     Typically paired with a dom monitor using url_filter to
            discover PDF links on the careers page.
"""

SCRAPER_SKIP = """\
skip — Placeholder scraper (auto-configured)

  Monitors that return full job data auto-configure this scraper to signal
  that the scraper step should be skipped. Never selected manually.
"""

SCRAPER_MOKAHR = """\
mokahr — Mokahr ATS Detail API scraper

  API:      POST https://<board-origin>/api/outer/ats-apply/website/job
  Returns:  title, HTML description (jobDescription), locations,
            employment_type (mapped from commitment), date_posted,
            metadata (department)
  Config:   locale (optional, default "zh-CN") — passed to the detail API
  Note:     Pair with the mokahr monitor and declare
            scraper_config: {"enrich": ["description"]} in boards.csv.
            The Mokahr listing API returns metadata only — descriptions
            live on the encrypted detail endpoint, decrypted via the
            per-site AES IV (extracted from the SPA's init-data attribute)
            and per-response key (necromancer field). No browser needed.
"""

SCRAPER_NOTION = """\
notion — Notion Page API scraper

  API:      POST https://{subdomain}.notion.site/api/v3/loadPageChunk
  Returns:  title, HTML description, locations, employment_type,
            job_location_type, metadata (team/department)
  Config:   property_map (optional) — custom mapping of Notion property
            names to job fields. Default auto-maps common names:
            Location→locations, Department→metadata.team,
            Employment Type→employment_type
  Note:     Auto-configured when selecting the notion monitor.
            Extracts block content as structured HTML (headings, lists,
            paragraphs) and collection properties (location, department).
"""

SCRAPER_CARDS: dict[str, str] = {
    "json-ld": SCRAPER_JSONLD,
    "nextdata": SCRAPER_NEXTDATA,
    "embedded": SCRAPER_EMBEDDED,
    "phuketall": SCRAPER_PHUKETALL,
    "veryeast": SCRAPER_VERYEAST,
    "onlyfy": SCRAPER_ONLYFY,
    "dom": SCRAPER_DOM,
    "api_sniffer": SCRAPER_API_SNIFFER,
    "taleo": """\
taleo — Oracle Taleo Enterprise detail scraper

  Parses the bounded api.fillList payload embedded in public Taleo Enterprise
  jobdetail.ftl pages. No browser is needed for detail scraping.

  Pair with an api_sniffer monitor for /careersection/<id>/jobsearch.ftl boards.
  Taleo Business Edition (*.tbe.taleo.net) continues to use json-ld.

  Config: none
  Returns: title, HTML description and qualifications, location,
           employment_type, job_location_type when tagged, valid_through,
           business-area and requisition metadata.""",
    "adp": """\
adp — ADP Workforce Now Detail API scraper

  Fetches a requisition from ADP's public career-center detail API. If the
  requisition stores its job description in a DOCX attachment, the scraper
  downloads the attachment and converts its paragraphs, headings, lists, and
  tables to HTML. No browser needed.

  Pair with an api_sniffer listing monitor whose job URL contains ccId, cid,
  lang, and jobId query parameters.

  Config: {"enrich": ["description"]}

  Available fields: title, description, locations, employment_type,
  date_posted, base_salary, requisition metadata.""",
    "pdf": SCRAPER_PDF,
    "notion": SCRAPER_NOTION,
    "oracle_hcm": """\
oracle_hcm — Oracle Cloud HCM Detail API scraper

  Fetches job details from recruitingCEJobRequisitionDetails REST API.
  No browser needed — pure HTTP.

  Auto-detected for Oracle HCM job URLs. Uses host + site from board metadata.

  Available fields: Title, PrimaryLocation, ExternalPostedStartDate,
  ExternalDescriptionStr, ExternalQualificationsStr,
  ExternalResponsibilitiesStr, JobSchedule, WorkplaceTypeCode.

  Best used with enrich: ["description"] — monitor provides title/location/date,
  scraper fills in description from the detail API.""",
    "skip": SCRAPER_SKIP,
    "linkedin": SCRAPER_LINKEDIN,
    "headhunter": SCRAPER_HEADHUNTER,
    "jobstreet": SCRAPER_JOBSTREET,
    "paycom": SCRAPER_PAYCOM,
    "jazzhr": SCRAPER_JAZZHR,
    "paycor": SCRAPER_PAYCOR,
    "paylocity": SCRAPER_PAYLOCITY,
    "bite": SCRAPER_BITE,
    "mokahr": SCRAPER_MOKAHR,
    "rippling": SCRAPER_RIPPLING,
    "johdi": SCRAPER_JOHDI,
    "smartrecruiters": SCRAPER_SMARTRECRUITERS,
    "workable": SCRAPER_WORKABLE,
    "workday": SCRAPER_WORKDAY,
    "eightfold": SCRAPER_EIGHTFOLD,
}


def _show_occupations() -> None:
    """Display occupation taxonomy from data/occupations.csv."""
    import polars as pl

    from src.shared.constants import get_data_dir

    path = get_data_dir() / "occupations.csv"
    if not path.exists():
        print("No occupations found in data/occupations.csv")
        return

    df = pl.read_csv(path, infer_schema_length=0)
    print("Occupation Taxonomy")
    print("Managed in data/occupations.csv — enricher outputs free-text, resolver maps to slug\n")
    print(f"  {'Slug':<35} {'EN':<25} {'Aliases':>7}")
    print(f"  {'─' * 35} {'─' * 25} {'─' * 7}")

    for row in df.iter_rows(named=True):
        slug = row["slug"]
        en = row.get("en", "")
        aliases_raw = row.get("aliases", "")
        alias_count = len([a for a in aliases_raw.split("|") if a.strip()]) if aliases_raw else 0
        print(f"  {slug:<35} {en:<25} {alias_count:>7}")

    print(f"\n  {len(df)} occupations total")
    print("\n  CLI: ws taxonomy search occupations <query>")
    print("       ws taxonomy validate occupations")


def _show_seniority() -> None:
    """Display seniority taxonomy from data/seniority.csv."""
    import polars as pl

    from src.shared.constants import get_data_dir

    path = get_data_dir() / "seniority.csv"
    if not path.exists():
        print("No seniority levels found in data/seniority.csv")
        return

    df = pl.read_csv(path, infer_schema_length=0)
    print("Seniority Taxonomy")
    print("Managed in data/seniority.csv — detected from title patterns\n")
    print(f"  {'Slug':<15} {'EN':<20} {'Aliases':>7}")
    print(f"  {'─' * 15} {'─' * 20} {'─' * 7}")

    for row in df.iter_rows(named=True):
        slug = row["slug"]
        en = row.get("en", "")
        aliases_raw = row.get("aliases", "")
        alias_count = len([a for a in aliases_raw.split("|") if a.strip()]) if aliases_raw else 0
        print(f"  {slug:<15} {en:<20} {alias_count:>7}")

    print(f"\n  {len(df)} seniority levels total")
    print("\n  CLI: ws taxonomy search seniority <query>")
    print("       ws taxonomy validate seniority")


def _show_industries() -> None:
    """Display industry taxonomy from data/industries.csv."""
    import polars as pl

    from src.shared.constants import get_data_dir

    path = get_data_dir() / "industries.csv"
    if not path.exists():
        print("No industries found in data/industries.csv")
        return

    df = pl.read_csv(path, infer_schema_length=0)
    name_header = "EN" if "en" in df.columns else "Name"
    name_col = "en" if "en" in df.columns else "name"
    de_col = "de" if "de" in df.columns else None
    print("Industry Taxonomy")
    print("Managed in data/industries.csv — set per company with: ws set --industry <id>\n")
    print(f"  {'ID':>3}  {name_header:<30}" + (f" {'DE':<30}" if de_col else ""))
    print(f"  {'──':>3}  {'─' * 30}" + (f" {'─' * 30}" if de_col else ""))

    for row in df.iter_rows(named=True):
        ind_id = row["id"]
        name = row.get(name_col, "")
        de = row.get(de_col, "") if de_col else ""
        print(f"  {ind_id:>3}  {name:<30}" + (f" {de:<30}" if de_col else ""))

    print(f"\n  {len(df)} industries total")
    print("\n  CLI: ws taxonomy search industries <query>")
    print("       ws taxonomy validate industries")

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
    if topic == "occupations":
        _show_occupations()
        return
    if topic == "seniority":
        _show_seniority()
        return
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
