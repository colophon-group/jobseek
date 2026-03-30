# Location Normalization Plan

Replace free-form `locations text[]` on `job_posting` with structured `location_ids` referencing a GeoNames-seeded hierarchy.

## GeoNames Data

### Files to download

| File | URL | Size | Contents |
|------|-----|------|----------|
| `countryInfo.txt` | `download.geonames.org/export/dump/countryInfo.txt` | 30 kB | Countries with ISO codes, names, lat/lng |
| `admin1CodesASCII.txt` | `download.geonames.org/export/dump/admin1CodesASCII.txt` | 50 kB | Regions/states/cantons with codes |
| `cities15000.zip` | `download.geonames.org/export/dump/cities15000.zip` | 2 MB | Cities with population ≥ 15,000, lat/lng (~25k rows) |
| `alternateNamesV2.zip` | `download.geonames.org/export/dump/alternateNamesV2.zip` | 130 MB | Translated names in all languages |

Note: `cities15000.txt` and `admin1CodesASCII.txt` include GeoNames IDs that can be cross-referenced with the main `allCountries.txt` dump to get lat/lng for admin1 regions. Alternatively, the seed script can look up admin1 coordinates from the cities file (capital/largest city of each region).

### What we get

```
Country:  CH / Switzerland / Schweiz / Suisse / Svizzera       (lat: 47.00, lng: 8.00)
  Region: CH.ZH / Canton of Zurich / Kanton Zürich             (lat: 47.37, lng: 8.55)
    City: 2657896 / Zurich / Zürich / Zurigo                   (lat: 47.37, lng: 8.54)
```

- ~250 countries (with lat/lng centroids)
- ~4,500 admin1 regions (with lat/lng)
- ~25,000 cities with lat/lng (population ≥ 15k covers virtually all job posting locations)
- Translations in 50+ languages (we use en, de, fr, it)

## DB Schema

### `location` table

```sql
CREATE TYPE location_type AS ENUM ('macro', 'country', 'region', 'city');

CREATE TABLE location (
  id          integer PRIMARY KEY,  -- GeoNames ID (synthetic 1-9 for macro regions)
  parent_id   integer REFERENCES location(id),
  type        location_type NOT NULL,
  population  integer,              -- for disambiguation (larger city wins)
  lat         real,
  lng         real
);

CREATE INDEX idx_loc_parent ON location(parent_id);
CREATE INDEX idx_loc_type ON location(type);
```

### `location_macro_member` table

Many-to-many: macro regions → member countries (e.g. EMEA → 100+ countries, DACH → DE/AT/CH).

```sql
CREATE TABLE location_macro_member (
  macro_id   integer NOT NULL REFERENCES location(id) ON DELETE CASCADE,
  country_id integer NOT NULL REFERENCES location(id) ON DELETE CASCADE,
  PRIMARY KEY (macro_id, country_id)
);
```

Macro regions (synthetic IDs 1-9): EMEA, APAC, Americas, EU, DACH, LATAM, Nordics, MENA, Worldwide.

### `location_name` table

```sql
CREATE TABLE location_name (
  location_id integer NOT NULL REFERENCES location(id) ON DELETE CASCADE,
  locale      text NOT NULL,        -- 'en', 'de', 'fr', 'it'
  name        text NOT NULL,
  PRIMARY KEY (location_id, locale)
);
CREATE INDEX idx_locname_lower ON location_name(lower(name), locale);
```

### `job_posting` change

```sql
-- Replace locations text[] + job_location_type text with parallel arrays
ALTER TABLE job_posting ADD COLUMN location_ids integer[];
ALTER TABLE job_posting ADD COLUMN location_types text[];
CREATE INDEX idx_jp_location_ids ON job_posting USING GIN(location_ids);

-- CHECK constraint on location_types values
ALTER TABLE job_posting ADD CONSTRAINT chk_location_types
  CHECK (location_types <@ ARRAY['onsite', 'remote', 'hybrid']::text[]);

-- Validate parallel array lengths match
ALTER TABLE job_posting ADD CONSTRAINT chk_location_arrays_length
  CHECK (
    (location_ids IS NULL AND location_types IS NULL)
    OR array_length(location_ids, 1) = array_length(location_types, 1)
  );

-- After migration:
ALTER TABLE job_posting DROP COLUMN locations;
ALTER TABLE job_posting DROP COLUMN job_location_type;
```

`location_ids` and `location_types` are parallel arrays. Each location has its own type:

```
location_ids:   [6252001, 2988507]   -- US, Paris
location_types: ['remote', 'onsite'] -- remote in US, onsite in Paris
```

### Query examples

**"Remote jobs in the US":**
```sql
SELECT * FROM job_posting
WHERE EXISTS (
  SELECT 1 FROM unnest(location_ids, location_types) AS t(lid, ltype)
  WHERE ltype = 'remote'
    AND lid IN (SELECT id FROM location WHERE id = 6252001 OR parent_id = 6252001
                UNION ALL
                SELECT c.id FROM location c JOIN location p ON c.parent_id = p.id
                WHERE p.parent_id = 6252001)
);
```

**"Onsite in Paris":**
```sql
SELECT * FROM job_posting
WHERE EXISTS (
  SELECT 1 FROM unnest(location_ids, location_types) AS t(lid, ltype)
  WHERE lid = 2988507 AND ltype = 'onsite'
);
```

**"All remote jobs":**
```sql
SELECT * FROM job_posting
WHERE 'remote' = ANY(location_types);
```

## Seed Script

`apps/crawler/scripts/seed_geonames.py`:

1. Download the 4 GeoNames files (cache locally in `apps/crawler/data/geonames/`)
2. Insert macro regions (hardcoded, synthetic IDs 1-9) with multilingual names
3. Parse `countryInfo.txt` → insert countries into `location` with lat/lng centroids
4. Populate `location_macro_member` (EMEA → countries, DACH → DE/AT/CH, etc.)
5. Parse `admin1CodesASCII.txt` → insert regions with `parent_id` = country
6. Parse `cities15000.txt` → insert cities with `parent_id` = region, lat/lng from GeoNames coordinates
7. Backfill admin1 lat/lng from the largest city in each region
8. Parse `alternateNamesV2.zip` → insert en/de/fr/it names into `location_name`

### GeoNames coordinate sources

| Level | Lat/lng source |
|-------|---------------|
| Country | `countryInfo.txt` columns 15-16 (centroid) |
| Region | Derived from largest city in region (by population) |
| City | `cities15000.txt` columns 5-6 (exact coordinates) |

Expected row counts:
- `location`: ~30k rows (~1 MB)
- `location_name`: ~120k rows (30k × 4 locales, ~5 MB)

## Matching Strategy

### Parsing free-form locations

Current `locations` values look like:
- `"Zurich, Switzerland"`
- `"Berlin"`
- `"Remote - US"`
- `"New York, NY"`
- `"Multiple Locations"`
- `"EMEA"`

### Matching pipeline

```
Input: "Zurich, Switzerland"
  1. Normalize whitespace, strip "& Other locations" suffix, strip trailing postal codes
  2. Check skip patterns (Multiple Locations, Distributed, etc.) and "<Country> Locations"
  3. Detect pure remote / standalone type markers (Hybrid, On-site, In-Office, In-Person)
  4. Extract type hints from parenthetical or inline markers (remote/hybrid/onsite)
  5. Handle state-prefixed format: "IL-Chicago" → tokens [Chicago, Illinois]
  6. Try full-string exact match (case-insensitive), then split by comma/dash/bullet/pipe
  7. For 2-letter tokens: resolve as alias → US state → ISO2 country (context disambiguates)
  8. For 3-letter tokens: resolve as ISO 3166-1 alpha-3 country code
  9. Multi-token: find city candidates + context (country/region), filter cities by ancestor
  10. Disambiguate by population (largest wins), break ties by type specificity (city > region > country)
```

### Special cases

| Pattern | Handling |
|---------|----------|
| `"Remote"` / `"Fully Remote"` | No location, type=remote |
| `"Remote - US"` / `"Remote, US"` | US country + type=remote |
| `"Spain - Remote"` / `"UK - Remote"` | Country + type=remote |
| `"EMEA"` / `"APAC"` / `"DACH"` | Resolved as macro region locations |
| `"EU (Remote)"` | EU macro + type=remote |
| `"Multiple Locations"` / `"Distributed"` | Skip — no meaningful location data |
| `"India Locations"` | Skip — "<Country> Locations" pattern |
| `"Berlin, Germany"` | City match → Berlin (DE) |
| `"New York, NY"` | City match → New York, state match → NY |
| `"Bengaluru, IN"` | IN → India (not Indiana) via ancestor context |
| `"Berlin, DE, 10557"` | Strip postal code → match Berlin + DE (Germany) |
| `"SG"` / `"LU"` | ISO2 country (not US state) → Singapore, Luxembourg |
| `"London & Other locations"` | Strip suffix → match London |
| `"SF • NY • US"` | Bullet separator → multi-token matching |
| `"Hybrid"` / `"In-Office"` | Standalone type marker, no location |

## Crawler Integration

On scrape, after extracting locations:

```python
raw_locations = content.locations           # ["Zurich, Switzerland (onsite)", "US (remote)"]
resolved = resolve_locations(raw_locations) # [(2657896, 'onsite'), (6252001, 'remote')]
location_ids = [r[0] for r in resolved]     # [2657896, 6252001]
location_types = [r[1] for r in resolved]   # ['onsite', 'remote']
# Store raw in extras.json, normalized ids + types in DB
```

The resolver runs against an in-memory index loaded from the `location` + `location_name` tables at startup. No per-request DB queries.

Location type detection heuristics:
- Explicit markers: "Remote", "On-site", "Hybrid" in the location string
- `job_location_type` from scraper output (applied to all locations if per-location type not available)
- Default: `onsite` if no signal

## Frontend Read

Localized location display:

```sql
SELECT ln.name
FROM unnest(jp.location_ids) AS lid
JOIN location_name ln ON ln.location_id = lid AND ln.locale = $1
ORDER BY array_position(jp.location_ids, lid)
```

Or fetch location hierarchy for breadcrumb display:

```sql
WITH RECURSIVE chain AS (
  SELECT id, parent_id, type FROM location WHERE id = $1
  UNION ALL
  SELECT l.id, l.parent_id, l.type
  FROM location l JOIN chain c ON l.id = c.parent_id
)
SELECT c.type, ln.name
FROM chain c
JOIN location_name ln ON ln.location_id = c.id AND ln.locale = $2
ORDER BY CASE c.type WHEN 'country' THEN 1 WHEN 'region' THEN 2 WHEN 'city' THEN 3 END;
```

## Migration (existing data)

1. Run seed script to populate `location` + `location_name` from GeoNames
2. Run matching script against all existing `locations text[]` values
3. Set `location_ids` on matched rows
4. Log unmatched locations for manual review
5. After verification: drop `locations` and `job_location_type` columns

## Expected Size

| Table | Rows | Size |
|-------|------|------|
| `location` | ~30k | ~1 MB |
| `location_name` | ~120k | ~5 MB |
| `location_ids` on job_posting | 40k | ~200 kB |
| GIN index on `location_ids` | — | ~500 kB |

Total: ~7 MB. Replaces the current `locations text[]` (2.3 MB) + `idx_jp_locations` GIN (2.9 MB) = 5.2 MB. Comparable size but structured and multilingual.

## Implementation Order

1. Create `location` + `location_name` tables (Drizzle migration)
2. Build seed script (`scripts/seed_geonames.py`)
3. Run seed to populate reference data
4. Build location resolver (`src/core/location_resolve.py`)
5. Migrate existing `locations text[]` → `location_ids`
6. Integrate resolver into crawler scrape flow
7. Update frontend to query `location_name` by locale
8. Drop `locations text[]` and `job_location_type text` columns
