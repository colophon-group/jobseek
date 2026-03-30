# Scraper Unification: Config-Driven Detail Fetching

Replace dedicated API scrapers with a single config-driven scraper that can
express URL construction, field mapping, transforms, and composition.

## Motivation

We have 5 dedicated scrapers (workday, workable, smartrecruiters, rippling, bite)
that each do the same thing: take a job page URL, derive an API URL, fetch JSON,
and map fields to `JobContent`. They're 50-100 lines each, straightforward, but
every new ATS means a new scraper file + registration + tests.

A config-driven scraper would let us onboard new ATS platforms without code
changes — just a row in boards.csv.

## Current Blockers

### 1. URL derivation from job page URLs

Every dedicated scraper parses the public job URL with regex and reconstructs an
API endpoint URL. Examples:

- **Workday**: `https://company.wd3.myworkdayjobs.com/en-US/site/job/loc/title_REQ123`
  → `https://company.wd3.myworkdayjobs.com/wday/cxs/company/site/job/loc/title_REQ123`
- **Workable**: `https://apply.workable.com/acme/j/ABC123/`
  → `https://apply.workable.com/api/v2/accounts/acme/jobs/ABC123`
- **Rippling**: `https://ats.rippling.com/acme/jobs/abc-def-123`
  → `https://api.rippling.com/platform/api/ats/v1/board/acme/jobs/abc-def-123`

This needs a config like:
```json
{
  "url_parse": "https://(?P<company>[^.]+)\\.wd\\d+\\.myworkdayjobs\\.com/.+?/(?P<site>[^/]+)/job/(?P<path>.+)",
  "api_url": "https://{company}.wd3.myworkdayjobs.com/wday/cxs/{company}/{site}/job/{path}"
}
```

Doable but not trivial — each ATS has a different URL structure, and some need
query parameters or headers too.

### 2. Multi-field description composition

Most scrapers build the description from multiple response fields:

- **Workable**: `description` + `requirements` + `benefits` (3 HTML fragments concatenated)
- **SmartRecruiters**: `companyDescription` + `jobDescription` + `qualifications` + `additionalInformation` (4 sections with `<h3>` headers)
- **Rippling**: `description.company` + `description.role` (2 parts)

A simple `"description": "path.to.field"` mapping can't express this. Would need
something like:
```json
{
  "description": {
    "compose": ["jobAd.sections.companyDescription.text",
                 "jobAd.sections.jobDescription.text",
                 "jobAd.sections.qualifications.text"],
    "separator": ""
  }
}
```

### 3. Location composition from nested objects

Workable, Bite, and SmartRecruiters build location strings from structured address
objects:

```json
{"city": "Berlin", "region": "Berlin", "country": "Germany"}
→ "Berlin, Germany"
```

With deduplication (Workday merges primary + additional locations). jmespath can
extract arrays but can't compose `"city, country"` from object fields or dedupe.

### 4. Salary parsing

SmartRecruiters, Rippling, and Bite each extract salary with custom logic:
min/max from nested paths, currency from a sibling field, frequency string
normalized to unit (`PER_YEAR` → `year`, `HOURLY` → `hour`).

Would need a salary config block with normalization rules:
```json
{
  "salary": {
    "min": "compensation.min",
    "max": "compensation.max",
    "currency": "compensation.currency",
    "unit": "compensation.period",
    "unit_map": {"PER_YEAR": "year", "PER_HOUR": "hour"}
  }
}
```

Note: the nextdata monitor already has `base_salary` config with `unit_map` and
`divisor` — this pattern could be generalized.

### 5. Enum mapping

Raw API values need normalization: `SALARIED_FT` → `Full-time`, `REMOTE` → `remote`.
The batch processor's `enum_normalize.py` already handles employment_type and
job_location_type normalization post-scrape, so this is partially solved. But
some scrapers do their own mapping for values that don't pass through the
normalizer cleanly.

## What Already Exists

- **`fields` mapping** in nextdata/api_sniffer monitors — supports jmespath dot
  paths, array indexing, fallback via `||` operator
- **`base_salary` config** in nextdata monitor — min/max/currency/unit with
  `unit_map` and `divisor`
- **`enum_normalize.py`** in batch processor — normalizes employment_type and
  job_location_type post-scrape, so scrapers don't need to handle most enum cases
- **`embedded` scraper** — generic JSON extraction from page source with regex
  pattern + field mapping

## Possible Design

A unified `api` scraper type with config:

```json
{
  "url_parse": "<regex with named groups>",
  "api_url": "<template with {group} placeholders>",
  "method": "GET",
  "headers": {},
  "params": {},
  "fields": {
    "title": "name",
    "description": {"compose": ["desc.company", "desc.role"]},
    "locations": "workLocations[].city",
    "employment_type": "employmentType",
    "date_posted": "createdOn"
  },
  "salary": {
    "min": "pay.min", "max": "pay.max",
    "currency": "pay.currency", "unit": "pay.frequency",
    "unit_map": {"PER_YEAR": "year"}
  }
}
```

This covers ~80% of cases. The remaining 20% (location composition, description
section headers, deduplication) would need either:
- A small set of built-in transform functions (`compose`, `join_object`, `dedup`)
- Or acceptance that those fields come through slightly raw and get cleaned
  downstream

## Why Not Now

1. **5 scrapers is manageable** — the maintenance cost is low, each is simple and
   well-tested
2. **The config language risks becoming a DSL** — every new ATS will surface a
   new edge case that pushes the config further toward a programming language
3. **enum_normalize.py handles the biggest pain point** — employment type and
   location type normalization already happens post-scrape, reducing what scrapers
   need to do
4. **Higher priorities exist** — the N+1 elimination was the urgent win (24x
   request reduction); scraper unification is a convenience improvement

## When to Revisit

- When we hit ~10+ dedicated scrapers and the pattern is clearly stable
- When a new ATS onboarding is blocked waiting for a code deploy
- When we've built the `url_parse` + `api_url` template engine for another reason
