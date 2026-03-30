# Company Enrichment Plan

Populate schema.org `Organization` fields for company pages, using automated sources (JSON-LD on company website, Wikidata) with agent fallback for gaps.

## Target Fields

### Required (gate the setup step)

| Field | DB column | Type | Source priority |
|---|---|---|---|
| `description` | `description` | `text` (exists) | JSON-LD → `<meta>` → agent writes it |
| `industry` | `industry` | `smallint` FK | Wikidata P452 → JSON-LD → agent picks from `industries.csv` |

### Opportunistic (auto-filled if found, never block on these)

| Field | DB column | Type | Source priority |
|---|---|---|---|
| `employee_count_range` | `employee_count_range` | `smallint` (enum) | Wikidata P1128 → JSON-LD `numberOfEmployees` |
| `founded_year` | `founded_year` | `smallint` | Wikidata P571 → JSON-LD `foundingDate` |
| `hq_location_id` | `hq_location_id` | `int FK → location` | Wikidata P159 → resolve via LocationResolver |

### Extras JSONB (for schema.org reconstruction, not queried)

| Field | JSON key | Type | Source |
|---|---|---|---|
| Social/profile links | `sameAs` | `string[]` | JSON-LD `sameAs` → Wikidata |
| Parent organization | `parentOrganization` | `{name, slug?, wikidata?}` | Wikidata P749 |
| Working languages | `knowsLanguage` | `string[]` | Aggregate from `job_posting.language` |
| Legal name | `legalName` | `string` | Wikidata P1448 |
| Stock ticker | `tickerSymbol` | `string` | Wikidata P414/P249 |
| Wikidata QID | `wikidataId` | `string` | Wikidata match |

### Schema changes

```sql
ALTER TABLE company ADD COLUMN industry smallint REFERENCES industry(id);
ALTER TABLE company ADD COLUMN employee_count_range smallint;
ALTER TABLE company ADD COLUMN founded_year smallint;
ALTER TABLE company ADD COLUMN hq_location_id int REFERENCES location(id);
ALTER TABLE company ADD COLUMN extras jsonb DEFAULT '{}';

CREATE INDEX idx_company_industry ON company (industry) WHERE industry IS NOT NULL;
```

## Industries: On-Disk CSV

Industries are managed as `apps/crawler/data/industries.csv`, reviewable in PRs. The agent can search and propose new entries.

### File: `data/industries.csv`

```csv
id,name,keywords
1,Technology,"software,internet,information technology,SaaS,cloud computing,AI,machine learning"
2,Financial Services,"banking,insurance,fintech,payments,investment,capital markets"
3,Healthcare,"health,medical,hospital,clinical,health care"
4,Manufacturing,"manufacturing,industrial,factory,production"
5,Retail & E-commerce,"retail,e-commerce,ecommerce,shopping,marketplace,consumer goods"
6,Media & Entertainment,"media,entertainment,publishing,gaming,streaming,film,music"
7,Telecommunications,"telecom,telecommunications,wireless,mobile network"
8,Energy,"energy,oil,gas,renewable,solar,wind,utilities,power"
9,Transportation & Logistics,"transportation,logistics,shipping,freight,supply chain,delivery"
10,Education,"education,edtech,learning,university,training,e-learning"
11,Real Estate & Construction,"real estate,construction,property,building,architecture"
12,Professional Services,"consulting,legal,accounting,advisory,staffing,recruitment"
13,Government & Public Sector,"government,public sector,defense,military,civic"
14,Agriculture & Food,"agriculture,food,farming,beverage,agritech"
15,Aerospace & Defense,"aerospace,defense,aviation,space,satellite"
16,Automotive,"automotive,vehicles,cars,mobility,EV,electric vehicle"
17,Hospitality & Tourism,"hospitality,hotel,tourism,travel,restaurant"
18,Pharmaceuticals & Biotech,"pharmaceutical,biotech,drug,life sciences,genomics"
19,Non-profit,"non-profit,nonprofit,NGO,charity,foundation"
20,Robotics,"robotics,automation,drones,autonomous"
```

No "Other" catch-all — if a company doesn't fit, the agent proposes a new industry.

### `ws help industries` — search tool

```
ws help industries                  # list all
ws help industries <query>          # fuzzy search by name/keywords
```

Searches `name` and `keywords` columns. Shows matching rows with IDs. If no match:

```
  no matching industry found
  to add a new one: ws add industry "<name>" --keywords "kw1,kw2,..."
```

### `ws add industry` — propose new entry

```
ws add industry "Robotics" --keywords "robotics,automation,drones,autonomous"
```

Appends a row to `data/industries.csv` with the next available ID. The new industry is committed alongside the company CSV changes, so it's reviewable in the same PR.

### Sync to DB

`industries.csv` is synced to an `industry` table (same pattern as `companies.csv` → `company`):

```sql
CREATE TABLE industry (
  id smallint PRIMARY KEY,
  name text NOT NULL UNIQUE
);
```

Keywords stay in the CSV only (used for matching during enrichment, not stored in DB). `sync.py` upserts `(id, name)` pairs.

### Mapping Wikidata/JSON-LD labels to industry IDs

`enrich_company()` tries to match the raw industry label (from Wikidata P452 or JSON-LD) against the `keywords` column in `industries.csv`. The match is case-insensitive substring. If no match, the `industry` field is left blank and the agent is prompted to pick or add one.

## Data Sources

### 1. JSON-LD on company homepage

Fetch `website` URL, extract `<script type="application/ld+json">`, find `@type: "Organization"` (or `Corporation`, `LocalBusiness`, etc.).

Fields: `description`, `foundingDate`, `numberOfEmployees`, `sameAs`, `address`.

Reuse `_JsonLdExtractor` pattern from `src/core/scrapers/jsonld.py`.

### 2. Wikidata SPARQL

Query by official website URL (P856), fall back to label search by name:

```sparql
SELECT ?item ?itemLabel ?inception ?employees ?industryLabel ?hqLabel ?hq
       ?parentLabel ?parent WHERE {
  ?item wdt:P856 <{website}> .
  OPTIONAL { ?item wdt:P571 ?inception }
  OPTIONAL { ?item wdt:P1128 ?employees }
  OPTIONAL { ?item wdt:P452 ?industry }
  OPTIONAL { ?item wdt:P159 ?hq }
  OPTIONAL { ?item wdt:P749 ?parent }
  SERVICE wikibase:label { bd:serviceParam wikibase:language "en" }
}
LIMIT 5
```

Endpoint: `https://query.wikidata.org/sparql` with `Accept: application/json`.

### 3. `<meta>` tags (fallback for description)

Extract `<meta name="description">` or `<meta property="og:description">` when no JSON-LD Organization block found.

## Core Module: `src/core/company_enrich.py`

```python
@dataclass
class CompanyMeta:
    description: str | None
    industry_id: int | None           # matched from industries.csv
    industry_raw: str | None          # raw label from source (for agent review)
    employee_count_range: int | None  # enum bucket ID
    founded_year: int | None
    hq_location_name: str | None      # raw string, for resolver
    same_as: list[str]
    parent_org_name: str | None
    legal_name: str | None
    ticker_symbol: str | None
    wikidata_id: str | None
    sources: dict[str, str]           # field → "jsonld" | "wikidata" | "meta"
    tier: str                         # "A" | "B" | "C"

async def enrich_company(website: str, name: str, http: httpx.AsyncClient) -> CompanyMeta
```

Steps:
1. `GET {website}` → parse HTML
2. Extract JSON-LD Organization block
3. Extract `<meta>` description as fallback
4. Query Wikidata SPARQL by P856 (website URL)
5. If no Wikidata hit by URL, try label search with `name`
6. Merge: Wikidata wins for structured fields, JSON-LD/meta wins for `description` and `sameAs`
7. Match industry label → `industries.csv` keywords → `industry_id`
8. Map `numberOfEmployees` → range bucket
9. Classify tier

## Tier Classification

| Tier | Criteria | Agent action on required fields |
|---|---|---|
| **A — Full** | Both `description` and `industry_id` resolved | Confirm they look right. Done. |
| **B — Partial** | One of `description`/`industry_id` resolved | Fill the missing one manually. |
| **C — Nothing** | Neither resolved | Write `description` from About page. Pick industry with `ws help industries <query>`. |

The tier determines **how much manual effort the agent spends on the two required fields**. Opportunistic fields are never worth manual effort regardless of tier.

## Integration: Inline in `ws set --website`

Enrichment runs automatically when the agent sets the website, alongside existing logo/career-page discovery. No separate command.

### Changes to `ws set` (`src/workspace/commands/config.py`)

After `_discover_and_show_all()` (logo/career discovery), add enrichment:

```python
if website is not None and ws.name:
    meta = asyncio.run(enrich_company(ws.website, ws.name))
    _apply_enrichment(ws, meta)
    save_workspace(ws)
    _show_enrichment_results(meta)
```

New options for manual override:

```
ws set --description "..."
ws set --industry <id>
```

### Workspace state changes (`src/workspace/state.py`)

Add enrichment fields to `Workspace` dataclass:

```python
@dataclass
class Workspace:
    # ... existing fields ...

    # Enrichment (populated by ws set --website, written to CSV on submit)
    description: str = ""
    industry: int | None = None
    employee_count_range: int | None = None
    founded_year: int | None = None
    hq_location_name: str = ""
    extras: dict[str, Any] = field(default_factory=dict)
    enrichment_tier: str = ""
```

Serialized under `company.enrichment` in `workspace.yaml`.

### Gate change (`src/workspace/workflow.py`)

The `company_complete` gate currently checks `name`, `website`, `branch`. Add `description` and `industry`:

```python
"company_complete": lambda ws, boards: bool(
    ws.name and ws.website and ws.branch
    and ws.description and ws.industry
),
```

This means the agent cannot advance past setup until both required fields are set — either auto-filled by enrichment or manually via `ws set`.

### Output format

When enrichment runs after `ws set --website`:

```
  enrichment tier: B (Wikidata found, no JSON-LD Organization)

  ✓ description:   "Acme Corp builds developer tools for cloud infrastructure." (meta)
  ✗ industry:      raw: "software development" — no match in industries.csv
  · employees:     1,001–5,000 (wikidata)
  · founded:       2015 (wikidata)
  · hq:            San Francisco, California, US (wikidata)
  · sameAs:        linkedin.com/company/acme, github.com/acme (wikidata)

  required:
    industry not set — find a match:  ws help industries <query>
                       or add new:    ws add industry "<name>" --keywords "..."
```

For tier C:
```
  enrichment tier: C (no structured data found)

  ✗ description:   not found
  ✗ industry:      not found

  required — set before advancing:
    ws set --description "..."
    ws help industries <query>  →  ws set --industry <id>
```

### Step instruction changes (`01-setup.md`)

Add after logo verification, before "When done":

```markdown
## 5. Company metadata

When you set the website, company data is automatically fetched from the
website's JSON-LD and Wikidata. **Description and industry are required.**

Review the enrichment output:
- If `description` is filled: verify it's factual. Override with
  `ws set --description "..."` if it's marketing fluff.
- If `description` is missing: read the homepage or About page and write
  a single factual sentence about what the company does.
- If `industry` is filled: verify it's correct. Override with
  `ws set --industry <id>` if wrong.
- If `industry` is missing: search with `ws help industries <query>`.
  If no match exists, add one: `ws add industry "<name>" --keywords "..."`.

Opportunistic fields (`employees`, `founded`, `hq`, `sameAs`) are set
automatically when found. Do not research these manually.
```

## CSV and Sync Changes

### New file: `data/industries.csv`

Tracked in git. Synced to `industry` table by `sync.py`. Agent can add rows via `ws add industry`.

### `companies.csv` — new columns

```
slug,name,website,logo_url,icon_url,logo_type,description,industry,employee_count_range,founded_year,hq_location_id,extras
```

`industry` is a smallint ID referencing `industries.csv`. `extras` is a JSON string.

### `csvtool.py` — extend `company_add()`

Accept new kwargs: `description`, `industry`, `employee_count_range`, `founded_year`, `hq_location_id`, `extras`.

### `sync.py` — extend

1. Sync `industries.csv` → `industry` table (before companies, since FK depends on it)
2. Extend company upsert with new columns, same `COALESCE` pattern

### Submit flow

No new steps. `ws submit` passes enrichment fields through existing `company_add()` path.

## Schema.org Reconstruction (frontend)

On company pages, render `<script type="application/ld+json">`:

```json
{
  "@context": "https://schema.org",
  "@type": "Organization",
  "name": "Stripe",
  "url": "https://stripe.com",
  "logo": "https://jobseek-assets.../stripe/logo.svg",
  "description": "Stripe is a financial infrastructure platform for businesses.",
  "foundingDate": "2010",
  "numberOfEmployees": {
    "@type": "QuantitativeValue",
    "minValue": 5001,
    "maxValue": 10000
  },
  "industry": "Financial Technology",
  "address": {
    "@type": "PostalAddress",
    "addressLocality": "San Francisco",
    "addressRegion": "California",
    "addressCountry": "US"
  },
  "sameAs": [
    "https://www.linkedin.com/company/stripe",
    "https://github.com/stripe",
    "https://twitter.com/stripe"
  ]
}
```

`industry` smallint FK → `industry.name` for display. `hq_location_id` → address components via `location` parent chain.

## Employee Count Range Enum

Stored as `smallint` in `company.employee_count_range`. Not a CSV — too stable to warrant one.

```
1 = 1–10
2 = 11–50
3 = 51–200
4 = 201–500
5 = 501–1,000
6 = 1,001–5,000
7 = 5,001–10,000
8 = 10,001+
```

## Backfill

One-off script for existing companies:

```bash
uv run python scripts/backfill_company_enrichment.py          # fetch + write
uv run python scripts/backfill_company_enrichment.py --dry-run # preview
```

Iterates companies with a website, calls `enrich_company()`, updates DB directly. Rate-limits Wikidata (1 req/sec). For companies where auto-enrichment doesn't resolve `industry` or `description`, logs them for manual review — but does NOT block (backfill is best-effort for existing data).

## Implementation Order

1. **`data/industries.csv`** — seed file with initial industries
2. **DB migration** — `industry` table, new columns on `company`, extras JSONB
3. **`src/core/company_enrich.py`** — JSON-LD + Wikidata + merge + tier + industry matching
4. **`ws help industries` / `ws add industry`** — search and add commands
5. **Workspace state** — add enrichment fields to `Workspace`, `to_dict`/`from_dict`
6. **`ws set` integration** — auto-enrich on `--website`, add `--description`/`--industry` options
7. **Gate update** — require `description` + `industry` in `company_complete`
8. **Step instructions** — update `01-setup.md`
9. **CSV + sync** — extend `companies.csv`, `csvtool.py`, `sync.py` (including `industries.csv` sync)
10. **Backfill script** — enrich existing companies (best-effort)
11. **Frontend** — render schema.org Organization JSON-LD on company pages
12. **Frontend** — display industry, size, founded, HQ on company cards/pages

Steps 1–9 are the core pipeline. Steps 10–12 follow independently.
