# R2 Description Storage Plan

Move job posting descriptions, localizations, extras, and metadata from PostgreSQL TOAST (382 MB) to R2 with version history via reverse diffs. Slim the `job_posting` table from 31 columns to 17. Normalize enum fields.

## R2 Layout

```
jobseek-assets/
  job/
    {posting-id}/
      en/
        latest.html          # current English description
        history.json          # reverse diffs for English
      de/
        latest.html          # current German description
        history.json          # reverse diffs for German
      ...                    # one dir per detected locale
      extras.json            # structured data (shared across locales)
```

Each locale is fully independent — its own `latest.html` and `history.json`. The crawler always knows the locale (language detection runs on scrape).

`history.json` — reverse diffs, most recent first:

```json
{
  "versions": [
    {
      "timestamp": "2026-03-10T14:00:00Z",
      "diff": "--- a\n+++ b\n@@ -3,2 +3,2 @@\n-Senior Engineer\n+Staff Engineer\n"
    }
  ]
}
```

`extras.json` — all structured data merged into one file:

```json
{
  "responsibilities": ["..."],
  "qualifications": ["..."],
  "skills": ["..."],
  "valid_through": "2026-04-08",
  "date_posted": "2026-03-01T00:00:00Z",
  "base_salary": {"min": 100000, "max": 150000, "currency": "USD"},
  "country_code": "US",
  "job_category": "Engineering",
  "job_family": "Software",
  "jobReqId": "R00000004169865",
  "raw_employment_type": "Full time",
  "raw_job_location_type": "TELECOMMUTE"
}
```

Original raw enum values are preserved in `extras.json` (`raw_employment_type`, `raw_job_location_type`) for building mapping dictionaries later.

## DB Changes

### Columns to drop (after migration)

| Column | Size | Reason |
|--------|------|--------|
| `description` | 114 MB | Moved to R2 `{locale}/latest.html` |
| `localizations` | 62 kB | Moved to R2 per-locale dirs |
| `extras` | 11 MB | Merged into R2 `extras.json` |
| `metadata` | 4.4 MB | Merged into R2 `extras.json` |
| `date_posted` | 239 kB | Moved to R2 `extras.json` |
| `base_salary` | 14 kB | Moved to R2 `extras.json` — re-add when reliably populated |
| `created_at` | 319 kB | Redundant with `first_seen_at` |
| `updated_at` | 319 kB | No consumer — crawler uses specific timestamps |
| `scrape_domain` | 360 kB | Derivable from `source_url` at query time |
| `last_scrape_error` | 152 kB | Not consumed — re-add if needed |
| `scrape_interval_hours` | 159 kB | Moved to `job_board` table |
| `lease_owner` | ~0 | Not read — `leased_until` alone handles stale leases |
| `delisted_at` | 13 kB | Redundant — `is_active = false` + `last_seen_at` sufficient |
| `delist_reason` | 31 kB | Not consumed |
| `relisted_at` | 3 kB | Not consumed |

```sql
ALTER TABLE job_posting DROP COLUMN description;
ALTER TABLE job_posting DROP COLUMN localizations;
ALTER TABLE job_posting DROP COLUMN extras;
ALTER TABLE job_posting DROP COLUMN metadata;
ALTER TABLE job_posting DROP COLUMN date_posted;
ALTER TABLE job_posting DROP COLUMN base_salary;
ALTER TABLE job_posting DROP COLUMN created_at;
ALTER TABLE job_posting DROP COLUMN updated_at;
ALTER TABLE job_posting DROP COLUMN scrape_domain;
ALTER TABLE job_posting DROP COLUMN last_scrape_error;
ALTER TABLE job_posting DROP COLUMN scrape_interval_hours;
ALTER TABLE job_posting DROP COLUMN lease_owner;
ALTER TABLE job_posting DROP COLUMN delisted_at;
ALTER TABLE job_posting DROP COLUMN delist_reason;
ALTER TABLE job_posting DROP COLUMN relisted_at;
ALTER TABLE job_posting DROP COLUMN job_location_type;
```

### Columns to convert

| Column | Before | After | Reason |
|--------|--------|-------|--------|
| `status` | `text ('active' / 'delisted')` | `boolean is_active` | Only two states, 1 byte vs variable text |
| `title` + `language` | `text` + `text` | `titles text[]` + `locales text[]` | Parallel arrays, first element is default |
| `employment_type` | `text` (free-form) | `text` with CHECK | 5 values: `full_time`, `part_time`, `contract`, `internship`, `full_or_part` |
| `job_location_type` | dropped | moved to `location_types text[]` | Per-location type, parallel with `location_ids` (see Location Normalization Plan) |
| `scrape_failures` | `integer` | `smallint` | Never exceeds single digits |
| `missing_count` | `integer` | `smallint` | Never exceeds single digits |

```sql
-- status → is_active
ALTER TABLE job_posting ADD COLUMN is_active boolean NOT NULL DEFAULT true;
UPDATE job_posting SET is_active = (status = 'active');
ALTER TABLE job_posting DROP COLUMN status;

-- title + language → titles[] + locales[]
ALTER TABLE job_posting ADD COLUMN locales text[] NOT NULL DEFAULT '{}';
ALTER TABLE job_posting ADD COLUMN titles text[] NOT NULL DEFAULT '{}';
UPDATE job_posting SET
  locales = ARRAY[COALESCE(language, 'en')],
  titles = ARRAY[COALESCE(title, '')];
ALTER TABLE job_posting DROP COLUMN title;
ALTER TABLE job_posting DROP COLUMN language;

-- scrape_failures + missing_count → smallint
ALTER TABLE job_posting ALTER COLUMN scrape_failures TYPE smallint;
ALTER TABLE job_posting ALTER COLUMN missing_count TYPE smallint;
```

### Enum normalization

#### `employment_type` mapping

| Normalized | Raw values |
|-----------|------------|
| `full_time` | full-time, Full time, FULL_TIME, Full-time, Full Time, Full-Time, Permanent, CDI, Festanstellung, Emploi fixe, Impiego fisso, Employee / Full-Time, Permanent Full-Time, EoR / Full-time, Regular, unbefristet, permanent, Graduate, OTHER, OTHER_EMPLOYMENT_TYPE |
| `part_time` | part-time, Part time, PART_TIME, Part-time |
| `contract` | Contract, Contractor, CDD, Fixed Term, Temporary, TEMPORARY, Fixed Term (Fixed Term), Fixed Term / Full-Time, Temporary positions |
| `internship` | Internship, INTERN, Intern, Stage, Praktikum, Alternance, Lernende |
| `full_or_part` | Full Time or Part Time, FULL_TIME PART_TIME, Full-time Part-time, Permanent Full-Time or Part-Time, Temporary positions Full-time |

Raw value saved to R2 `extras.json` as `raw_employment_type` before normalization.

```sql
UPDATE job_posting SET employment_type = CASE
  WHEN lower(employment_type) IN ('full-time', 'full time', 'full_time', 'full-time', 'permanent', 'cdi', 'festanstellung', 'emploi fixe', 'impiego fisso', 'regular', 'unbefristet', 'permanent') THEN 'full_time'
  WHEN employment_type ILIKE '%employee%full%' THEN 'full_time'
  WHEN employment_type ILIKE '%eor%' THEN 'full_time'
  WHEN employment_type ILIKE 'permanent full-time' THEN 'full_time'
  WHEN lower(employment_type) IN ('graduate', 'other', 'other_employment_type', 'libéral') THEN 'full_time'
  WHEN lower(employment_type) IN ('part-time', 'part time', 'part_time') THEN 'part_time'
  WHEN lower(employment_type) IN ('contract', 'contractor', 'cdd', 'temporary', 'temporary positions') THEN 'contract'
  WHEN employment_type ILIKE 'fixed term%' THEN 'contract'
  WHEN employment_type ILIKE '%temporary%' THEN 'contract'
  WHEN lower(employment_type) IN ('internship', 'intern', 'stage', 'praktikum', 'alternance', 'lernende') THEN 'internship'
  WHEN employment_type ILIKE '%full%part%' OR employment_type ILIKE '%part%full%' THEN 'full_or_part'
  WHEN employment_type ILIKE 'full_time%part_time%' OR employment_type ILIKE 'part_time%full_time%' THEN 'full_or_part'
  ELSE 'full_time'
END
WHERE employment_type IS NOT NULL;
```

#### `job_location_type` mapping

| Normalized | Raw values |
|-----------|------------|
| `onsite` | onsite, office |
| `remote` | remote, TELECOMMUTE, Remote |
| `hybrid` | hybrid, office remote, remote office |

Raw `job_location_type` values saved to R2 `extras.json` as `raw_job_location_type`. Column dropped — location types move to per-location `location_types text[]` (see Location Normalization Plan).

```sql
-- CHECK constraint on employment_type
ALTER TABLE job_posting ADD CONSTRAINT chk_employment_type
  CHECK (employment_type IN ('full_time', 'part_time', 'contract', 'internship', 'full_or_part'));
```

### Columns to add to `job_board`

```sql
ALTER TABLE job_board ADD COLUMN scrape_interval_hours integer NOT NULL DEFAULT 24;
```

### Indexes to drop

| Index | Size | Reason |
|-------|------|--------|
| `idx_jp_search_vector` | 49 MB | Expression index references `description` column — must be dropped |
| `idx_jp_locations` | 2.9 MB | 0 scans since stats reset — unused |
| `idx_jp_employment_type` | 1.9 MB | 0 scans since stats reset — unused |
| `idx_jp_status_active` | 1.2 MB | Replaced by `is_active` index |
| `idx_jp_last_seen_active` | 1.3 MB | References `status` column — rebuild for `is_active` |
| `idx_jp_language` | 1.3 MB | Replaced by `locales` |

```sql
DROP INDEX idx_jp_search_vector;
DROP INDEX idx_jp_locations;
DROP INDEX idx_jp_employment_type;
DROP INDEX idx_jp_status_active;
DROP INDEX idx_jp_last_seen_active;
DROP INDEX idx_jp_language;
CREATE INDEX idx_jp_active ON job_posting (is_active) WHERE is_active = true;
```

### Final schema (17 columns)

After R2 migration + location normalization (see `LOCATION_NORMALIZATION_PLAN.md`):

| # | Column | Type | Constraint | Purpose |
|---|--------|------|-----------|---------|
| 1 | `id` | `uuid` | PK | PK |
| 2 | `company_id` | `uuid` | not null, FK | Display |
| 3 | `board_id` | `uuid` | nullable, FK | Monitor diff logic |
| 4 | `locales` | `text[]` | not null | Available locales, first = default |
| 5 | `titles` | `text[]` | not null | Localized titles, parallel with `locales` |
| 6 | `location_ids` | `integer[]` | nullable | FK refs to `location` table |
| 7 | `location_types` | `text[]` | nullable, CHECK | Parallel with `location_ids`: onsite, remote, hybrid |
| 8 | `employment_type` | `text` | nullable, CHECK | full_time, part_time, contract, internship, full_or_part |
| 9 | `source_url` | `text` | unique, not null | Dedup |
| 10 | `is_active` | `boolean` | not null | Filtering |
| 11 | `first_seen_at` | `timestamptz` | not null | Display (doubles as created_at) |
| 12 | `last_seen_at` | `timestamptz` | nullable | Lifecycle |
| 13 | `next_scrape_at` | `timestamptz` | nullable | Scheduler |
| 14 | `last_scraped_at` | `timestamptz` | nullable | Scheduler |
| 15 | `leased_until` | `timestamptz` | nullable | Scheduler |
| 16 | `scrape_failures` | `smallint` | not null | Scheduler |
| 17 | `missing_count` | `smallint` | not null | Scheduler |

## Expected Impact

| Metric | Before | After | Saved |
|--------|--------|-------|-------|
| Columns | 31 | 17 | 14 |
| `description` | 114 MB | 0 | 114 MB |
| `extras` + `metadata` | 15.4 MB | 0 | 15.4 MB |
| Other dropped columns | ~1.6 MB | 0 | 1.6 MB |
| TOAST overhead | 382 MB | ~5 MB | ~377 MB |
| Indexes dropped | 57.7 MB | ~0.5 MB | ~57 MB |
| **DB total** | **534 MB** | **~65 MB** | **~469 MB** |
| R2 storage | 0 | ~130 MB | — |

## Crawler Flow (on scrape)

Per locale (same logic for every language detected):

1. Scraper produces description HTML, language detection gives `locale`
2. Download `job/{id}/{locale}/latest.html` from R2
3. If identical → no-op
4. If first scrape for this locale:
   - Upload `{locale}/latest.html`
   - Upload `{locale}/history.json` with `{"versions": []}`
   - Upload `extras.json` (extras + metadata + date_posted + base_salary + raw enum values merged)
   - Append locale + title to `locales[]` / `titles[]` in DB if new locale
5. If changed:
   - Compute reverse diff (new → old) via `difflib.unified_diff`
   - Download `{locale}/history.json`, prepend diff entry, re-upload
   - Upload new `{locale}/latest.html`
   - Re-upload `extras.json` if extras changed
   - Update `titles[]` at matching position if title changed
6. Normalize `employment_type` before DB write, resolve locations to `location_ids` + `location_types`

R2 path is deterministic from posting ID — no DB column needed.

## Frontend Read

```
GET https://jobseek-assets.colophon-group.org/job/{id}/{locale}/latest.html
  → if 404, fall back to /job/{id}/{locales[0]}/latest.html
```

Title lookup: `titles[array_position(locales, user_locale)]` in SQL, or ship both arrays and index in JS.

Extras (loaded on demand for detail view):
```
GET https://jobseek-assets.colophon-group.org/job/{id}/extras.json
```

## History Reconstruction

Per locale — apply reverse patches sequentially:
`latest → patch_n → patch_n-1 → ... → original`

Single GET to `{locale}/history.json` retrieves full history for that locale.

## Migration (existing data)

Bulk script to:
1. Read all `job_posting` rows where `description IS NOT NULL`
2. Upload as `job/{id}/{language}/latest.html` (using the `language` column)
3. For rows with `localizations` JSONB: extract each locale key, upload as `job/{id}/{locale}/latest.html`
4. Merge `extras` + `metadata` + `date_posted` + `base_salary` + raw enum values → upload as `job/{id}/extras.json`
5. Upload `{locale}/history.json` with `{"versions": []}` for each
6. Normalize `employment_type` and `job_location_type` in DB
7. Convert `title` + `language` → `titles[]` + `locales[]`
8. Convert `status` → `is_active`
9. After verification: drop columns and indexes

## Search Vector Strategy

The current `idx_jp_search_vector` is a GIN index on an expression that includes `description`. After dropping `description`, options:

1. **Rebuild without description** — index on `titles[1]` + `employment_type` only. Simpler, smaller index (~3 MB). Description search not supported.
2. **Stored tsvector column** — pre-compute and store a `search_vector tsvector` column at scrape time (before description moves to R2). ~49 MB but queryable.
3. **External search** — move full-text search to a dedicated service (e.g. Meilisearch, Typesense) fed from R2 content.

Recommend option 1 for now, upgrade to 3 if search usage grows.

## Implementation Order

1. Add R2 upload module in crawler (`src/core/description_store.py`) ✅
2. Create bulk migration script ✅
3. Integrate into scrape flow — upload on first scrape and on change
4. Add enum normalization to crawler (before DB write)
5. Update frontend to fetch from R2 URL with locale fallback
6. Run bulk migration (upload to R2 + save raw enum values in extras.json)
7. Normalize `employment_type` and `job_location_type` in DB
8. Add `scrape_interval_hours` to `job_board`
9. Convert `status` → `is_active`
10. Convert `title` + `language` → `titles[]` + `locales[]`
11. Convert `scrape_failures` and `missing_count` to `smallint`
12. Drop columns: `description`, `localizations`, `extras`, `metadata`, `date_posted`, `base_salary`, `created_at`, `updated_at`, `scrape_domain`, `last_scrape_error`, `scrape_interval_hours`, `lease_owner`, `delisted_at`, `delist_reason`, `relisted_at`, `status`, `title`, `language`
13. Drop indexes: `idx_jp_search_vector`, `idx_jp_locations`, `idx_jp_employment_type`, `idx_jp_status_active`, `idx_jp_last_seen_active`, `idx_jp_language`
14. Create `idx_jp_active` on `is_active`
15. Rebuild search vector index on `titles[1]` + `employment_type` only
16. Update all crawler SQL queries for renamed/removed columns
