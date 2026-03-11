# Job Data Fields

Complete reference for all fields extracted from job postings. These fields are shared across all monitors and scrapers — the same structure is used whether data comes from an API monitor (Greenhouse, Lever, etc.), JSON-LD, embedded JSON, or DOM extraction.

## Field Reference

### Required Fields

These must extract for every job (N/N in `ws run scraper` output). 0/N on either is a blocker — do not submit.

#### `title` — Job title

| Property | Value |
|----------|-------|
| Type | `str` |
| Format | Plain text (no HTML) |
| DB column | `job_posting.title` (text) / `job_posting.titles` (text[]) |

Stored in both `title` (legacy single value) and `titles[]` (new, parallel to `locales[]`). After R2 migration completes, `title` will be dropped; use `titles[1]`.

Examples: `"Senior Software Engineer"`, `"Marketing Manager — EMEA"`

#### `description` — Job description

| Property | Value |
|----------|-------|
| Type | `str` |
| Format | **HTML fragment** (not a full document) |
| DB column | `job_posting.description` (text) — *being migrated to R2* |
| R2 path | `job/{posting_id}/{locale}/latest.html` |

Must preserve original structure (`<p>`, `<ul><li>`, `<h3>`, etc.). API monitors return HTML natively. DOM scraper steps use `html: true` to preserve structure.

During the R2 migration, descriptions are stored in both the DB column and R2. After migration, the DB column will be dropped and descriptions served from R2.

```html
<p>We're looking for a talented engineer...</p>
<h3>Responsibilities</h3>
<ul><li>Design scalable systems</li><li>Lead technical reviews</li></ul>
```

---

### Important Fields

Should extract when available. Missing locations are acceptable only if `job_location_type` is set (e.g. remote-only companies).

#### `locations` — Work locations

| Property | Value |
|----------|-------|
| Type | `list[str]` |
| Format | Array of human-readable location strings |
| DB column | `job_posting.locations` (text[]) |

One location per array element. Each string is a human-readable place name — not a structured object.

```python
["San Francisco, CA", "New York, NY"]
["London, UK"]
["Remote"]
["São Paulo, São Paulo, Brazil"]
```

How each source builds locations:

| Source | Extraction |
|--------|-----------|
| Greenhouse | `location.name` or `offices[].name` |
| Lever | `categories.allLocations[]` |
| Ashby | `location` + `secondaryLocations[]` |
| Workday | `location` + `additionalLocations[]` |
| Recruitee | `locations[].city` + `locations[].country` |
| Hireology | `locations[].city` + `locations[].state` |
| Personio | `office` field |
| SmartRecruiters | `location.city` + `location.region` + `location.country` |
| JSON-LD | `jobLocation[].name` or built from `address` (locality, region, country) |

#### `employment_type` — Employment arrangement

| Property | Value |
|----------|-------|
| Type | `str` |
| Format | Normalized enum value |
| DB column | `job_posting.employment_type` (text) |

Normalized values (applied by `enum_normalize.normalize_employment_type()`):

```
full_time    part_time    contract    internship    full_or_part
```

Raw values from each ATS are mapped to these normalized forms. The original value is preserved in R2 extras as `raw_employment_type`.

| Source | Field |
|--------|-------|
| Greenhouse | Not available via API |
| Lever | `categories.commitment` |
| Ashby | `employmentType` (normalized: `FullTime` → `Full-time`) |
| Workday | `timeType` |
| Recruitee | `employment_type` |
| Personio | `employmentType` + `schedule` (combined) |
| SmartRecruiters | `typeOfEmployment.label` |
| JSON-LD | `employmentType` |

#### `job_location_type` — Remote/hybrid/onsite

| Property | Value |
|----------|-------|
| Type | `str` |
| Format | Lowercase: `"remote"`, `"hybrid"`, `"onsite"` |
| DB column | `job_posting.job_location_type` (text) |

| Source | Field | Mapping |
|--------|-------|---------|
| Ashby | `workplaceType` | `Remote` → `remote`, `Hybrid` → `hybrid`, `OnSite` → `onsite` |
| Lever | `workplaceType` | Direct |
| Recruitee | Boolean flags | `remote` → `"remote"`, `hybrid` → `"hybrid"`, `on_site` → `"onsite"` |
| Hireology | `remote` (bool) | `true` → `"remote"` |
| Workday | `remoteType` | Contains "remote" → `"remote"`, contains "hybrid"/"flexible" → `"hybrid"` |
| SmartRecruiters | Location flags | `location.remote` / `location.hybrid` |
| JSON-LD | `jobLocationType` | Direct |

---

### Optional Fields

More data is better, but not at the cost of resilience or speed.

#### `date_posted` — Publication date

| Property | Value |
|----------|-------|
| Type | `str` |
| Format | ISO 8601 or any date string from the source (no normalization) |
| DB column | `job_posting.date_posted` (timestamptz) |

Examples: `"2024-03-01T10:30:00Z"`, `"2024-03-01"`, `"2025-01-15"`

| Source | Field |
|--------|-------|
| Greenhouse | `first_published` |
| Lever | Not available |
| Ashby | `publishedAt` |
| Workday | `startDate` |
| Recruitee | `published_at` |
| Hireology | `created_at` |
| Personio | `createdAt` |
| SmartRecruiters | `releasedDate` |
| JSON-LD | `datePosted` |

#### `base_salary` — Compensation

| Property | Value |
|----------|-------|
| Type | `dict` |
| Format | JSON object with 4 optional keys |
| DB column | `job_posting.base_salary` (jsonb) |

```python
{
    "currency": "USD",      # ISO 4217 code (str or None)
    "min": 100000,          # Minimum (int/float or None)
    "max": 150000,          # Maximum (int/float or None)
    "unit": "year"          # Period: "year", "month", "hour", "week" (str or None)
}
```

When both `min` and `max` are None, the entire field should be None (not an empty dict).

Examples:

```python
{"currency": "USD", "min": 100000, "max": 150000, "unit": "year"}   # Annual salary
{"currency": "EUR", "min": 25, "max": 35, "unit": "hour"}           # Hourly rate
{"currency": "GBP", "min": 8000, "max": 10000, "unit": "month"}     # Monthly salary
```

| Source | Fields | Unit Mapping |
|--------|--------|-------------|
| Lever | `salaryRange.currency/min/max/interval` | `per-year-salary` → `year`, etc. |
| Ashby | `compensationTierSummary[].currency/min/max/interval` | `hour`, `month`, `year` |
| Recruitee | `salary.currency/min/max/period` | `hour`, `month`, `year` |
| SmartRecruiters | `compensation.salary.currency/min/max/period` | Direct |
| Pinpoint | `compensation_currency/min_salary/max_salary` | Inferred from range size |
| Rippling | `salary.currency/min/max` | Inferred |
| JSON-LD | `baseSalary.currency`, `baseSalary.value.minValue/maxValue/unitText` | `.lower()` |
| Greenhouse | Not available | — |
| Workday | Not available | — |

#### `language` — Content language

| Property | Value |
|----------|-------|
| Type | `str` |
| Format | ISO 639-1 code (e.g. `"en"`, `"de"`, `"fr"`) |
| DB column | `job_posting.language` (text) / `job_posting.locales` (text[]) |

Detected automatically from the description using lingua-py, or provided by the monitor (Greenhouse API, Personio). When a monitor already knows the language, detection is skipped. Stored in both `language` (legacy) and `locales[]` (new array of all available locales).

#### `localizations` — All language versions

| Property | Value |
|----------|-------|
| Type | `dict` |
| Format | JSONB keyed by locale, each containing `{title, description, locations}` |
| DB column | `job_posting.localizations` (jsonb) |

```python
{
    "en": {"title": "Software Engineer", "description": "<p>...</p>", "locations": ["Berlin"]},
    "de": {"title": "Softwareentwickler", "description": "<p>...</p>", "locations": ["Berlin"]}
}
```

Top-level `title`/`description`/`locations` always hold the English version when available. Frontend selects user's locale from `localizations` at display time.

Currently only populated by the Personio monitor (fetches per-language XML feeds).

#### `extras` — Structured supplementary data

| Property | Value |
|----------|-------|
| Type | `dict` |
| Format | Free-form JSONB with well-known optional keys |
| DB column | `job_posting.extras` (jsonb) |

Holds structured data that doesn't warrant its own column. Common keys:

| Key | Type | Sources | Notes |
|-----|------|---------|-------|
| `skills` | `list[str]` | JSON-LD, field mappings | `["Python", "PostgreSQL"]` |
| `responsibilities` | `list[str]` or `str` | JSON-LD, api_sniffer, embedded | One item per bullet |
| `qualifications` | `list[str]` or `str` | JSON-LD, api_sniffer, embedded | Falls back to `educationRequirements` |
| `valid_through` | `str` | JSON-LD | ISO 8601 date |

**Description enrichment:** When `responsibilities`, `qualifications`, or `skills` are in `extras`, they are automatically appended to the description HTML (as `<h3>` + `<ul>` sections) unless they already appear in the description text. This keeps descriptions self-contained while preserving structured access via `extras`.

#### `metadata` — ATS-specific fields

| Property | Value |
|----------|-------|
| Type | `dict` |
| Format | Free-form key-value pairs |
| DB column | `job_posting.metadata` (jsonb) |

Holds additional fields that don't fit the standard schema. Common keys:

| Key | Type | Sources |
|-----|------|---------|
| `department` | `str` | Greenhouse, Lever, Ashby, Personio, SmartRecruiters, Recruitee |
| `team` | `str` | Lever, Recruitee |
| `id` | `str` | Greenhouse, Lever, Ashby, Hireology, Personio, Recruitee |
| `requisition_id` | `str` | Greenhouse |
| `jobReqId` | `str` | Workday |
| `organization` | `str` | Hireology |
| `job_family` | `str` | Hireology |
| `function` | `str` | SmartRecruiters |
| `experienceLevel` | `str` | SmartRecruiters |
| `seniority` | `str` | Personio |
| `occupation` | `str` | Personio |
| `yearsOfExperience` | `str` | Personio |
| `keywords` | `list[str]` | Personio |
| `tags` | `list[str]` | Recruitee |
| `category` | `str` | Recruitee |

Scraper field mappings use `metadata.{key}` prefix to extract into metadata:

```json
{
  "fields": {
    "title": "title",
    "metadata.department": "department.name",
    "metadata.team": "teamName"
  }
}
```

---

## Schema.org / JSON-LD Mapping

The JSON-LD scraper (`json-ld`) maps [schema.org/JobPosting](https://schema.org/JobPosting) fields automatically. No configuration needed.

| schema.org Field | → | Our Field | Notes |
|-----------------|---|-----------|-------|
| `title` or `name` | → | `title` | Falls back to `name` if `title` missing |
| `description` | → | `description` | Preserved as HTML |
| `jobLocation` | → | `locations` | Array; extracts `name` or builds from `address` components |
| `employmentType` | → | `employment_type` | Direct |
| `jobLocationType` | → | `job_location_type` | Direct |
| `datePosted` | → | `date_posted` | Direct |
| `validThrough` | → | `extras.valid_through` | Direct |
| `baseSalary` | → | `base_salary` | Extracts `currency`, `value.minValue`, `value.maxValue`, `value.unitText` |
| `skills` | → | `extras.skills` | Converted to list if string |
| `responsibilities` | → | `extras.responsibilities` | Converted to list if string; also appended to description |
| `qualifications` | → | `extras.qualifications` | Falls back to `educationRequirements`; also appended to description |

### Location Extraction from JSON-LD

The `jobLocation` field can be:
- A single object or an array of objects
- Each object may have `name` (used directly) or `address` (built from parts)

```json
{
  "jobLocation": [
    {"@type": "Place", "name": "San Francisco, CA"},
    {
      "@type": "Place",
      "address": {
        "@type": "PostalAddress",
        "addressLocality": "London",
        "addressRegion": "England",
        "addressCountry": "UK"
      }
    }
  ]
}
```

Result: `["San Francisco, CA", "London, England, UK"]`

### Salary Extraction from JSON-LD

```json
{
  "baseSalary": {
    "@type": "MonetaryAmount",
    "currency": "USD",
    "value": {
      "@type": "QuantitativeValue",
      "minValue": 100000,
      "maxValue": 150000,
      "unitText": "YEAR"
    }
  }
}
```

Result: `{"currency": "USD", "min": 100000, "max": 150000, "unit": "year"}`

The `unitText` value is lowercased. A scalar `value` (instead of object) sets both `min` and `max` to the same number.

---

## Field Mapping in Scrapers

The `nextdata`, `embedded`, and `api_sniffer` scrapers use a `fields` config to map source data keys to our standard fields.

### Syntax

```json
{
  "fields": {
    "<our_field>": "<source_path>"
  }
}
```

Where `<our_field>` is one of:
- `title`, `description`, `locations`, `employment_type`, `job_location_type`
- `date_posted`, `base_salary`
- `skills`, `responsibilities`, `qualifications`, `valid_through` — stored in `extras` JSONB
- `metadata.<key>` — extracted into the metadata dict under the given key

And `<source_path>` is a dot-path with optional array indexing:
- `title` — top-level key
- `department.name` — nested key
- `offices[].name` — array map (extract `name` from each element)
- `locations[0].city` — array index

### Example

Given source data:
```json
{
  "title": "Senior Engineer",
  "descriptionHtml": "<p>Join us...</p>",
  "offices": [{"name": "NYC"}, {"name": "London"}],
  "department": {"name": "Engineering"},
  "workType": "Full-time",
  "remotePolicy": "hybrid"
}
```

Config:
```json
{
  "fields": {
    "title": "title",
    "description": "descriptionHtml",
    "locations": "offices[].name",
    "employment_type": "workType",
    "job_location_type": "remotePolicy",
    "metadata.department": "department.name"
  }
}
```

### Auto-Detection Heuristics

When probing, scrapers try to auto-detect field mappings by matching common key names:

| Our Field | Candidate Source Keys |
|-----------|----------------------|
| `title` | `title`, `name`, `jobTitle`, `job_title`, `position` |
| `description` | `description`, `content`, `descriptionHtml`, `body`, `jobDescription` |
| `locations` | `location`, `locations`, `office`, `offices` |
| `employment_type` | `employmentType`, `employment_type`, `type`, `jobType` |
| `job_location_type` | `locationType`, `workplaceType`, `remoteType` |
| `date_posted` | `datePosted`, `date_posted`, `posted_at`, `published_at`, `createdAt` |

These heuristics often need manual correction — a detected pattern with wrong field mapping should be fixed by inspecting the raw data, not by switching scraper types.

---

## Summary Table

| Field | Type | Priority | DB Type | HTML? | Notes |
|-------|------|----------|---------|-------|-------|
| `title` | `str` | Required | text | No | Plain text |
| `description` | `str` | Required | text | **Yes** | Preserve structure; enriched with extras |
| `locations` | `list[str]` | Important | text[] | No | One string per location |
| `employment_type` | `str` | Important | text | No | Normalized: `full_time`, `part_time`, `contract`, `internship`, `full_or_part` |
| `job_location_type` | `str` | Important | text | No | `remote`/`hybrid`/`onsite` |
| `date_posted` | `str` | Optional | timestamptz | No | ISO 8601 preferred |
| `base_salary` | `dict` | Optional | jsonb | No | `{currency, min, max, unit}` |
| `language` | `str` | Auto | text | No | ISO 639-1; detected or monitor-provided |
| `localizations` | `dict` | Optional | jsonb | No | Keyed by locale |
| `extras` | `dict` | Optional | jsonb | No | `{skills, responsibilities, qualifications, valid_through}` |
| `metadata` | `dict` | Optional | jsonb | No | Free-form, ATS-specific |
