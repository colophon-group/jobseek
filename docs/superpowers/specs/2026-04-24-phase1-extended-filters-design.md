# Phase 1 Extended — Filter Fix Design

## Goal

Fix non-functional explore filters (technology, seniority, occupation, location) and move the "exclude titles" input inside the Filters panel.

## Problem

All taxonomy filter modals (`suggestTechnologies`, `suggestSeniorities`, `suggestOccupations`, `suggestLocations`) query **Typesense taxonomy collections** — not Postgres. Local Typesense only has `job_posting` data; the `technology`, `seniority`, `occupation`, and `location` collections are empty. Selecting a filter in a modal does nothing because no taxonomy options are returned.

Secondary: `ExcludeTitlePills` is rendered above the Filters panel in `SearchToolbar`, but belongs inside the collapsible Filters panel.

## Architecture

### Part 1 — Taxonomy sync script

**New file:** `scripts/typesense-taxonomy-sync-local.py`

One-shot script that mirrors the 5 taxonomy Typesense collections from production to local. Modeled after the existing `scripts/typesense-backfill-local.py`.

**Flow:**
1. Load production Typesense credentials from `apps/crawler/.env.local` (`TYPESENSE_HOST`, `TYPESENSE_ADMIN_KEY`, `TYPESENSE_PORT`, `TYPESENSE_PROTOCOL`)
2. Load local Typesense config from `apps/web/.env.local` (`TYPESENSE_SEARCH_KEY=local_dev_typesense_key`, host=localhost, port=8108)
3. For each collection — `technology`, `seniority`, `occupation`, `location`, `company` — paginate through all production documents using `q=*` with page-based pagination (250 docs/page) using the production admin key
4. Batch-import all documents into the local Typesense collection using `action=upsert`

**Collections synced:** `technology`, `seniority`, `occupation`, `location`, `company`

**No DB dependency.** Pure Typesense-to-Typesense copy. Run once after `docker compose up`, and again whenever taxonomy feels stale.

**Usage:**
```bash
cd apps/crawler && uv run python ../../scripts/typesense-taxonomy-sync-local.py
```

### Part 2 — ExcludeTitlePills relocation

**Modified files:**
- `apps/web/src/components/search/search-toolbar.tsx`
- `apps/web/src/components/search/advanced-search-panel.tsx`

**Change:**
1. Remove the `ExcludeTitlePills` block (and its label div) from `SearchToolbar`
2. Add three props to `AdvancedSearchPanel`: `excludeTitles: string[]`, `onAddExcludeTitle: (kw: string) => void`, `onRemoveExcludeTitle: (kw: string) => void`
3. Render `ExcludeTitlePills` inside the expanded panel, below the filter buttons, with the same "Hide jobs with these words in the title" label

No state changes. Same callbacks, same data flow. Only the render location changes.

## Data Flow (Post-Fix)

```
User clicks filter modal (e.g. Technology)
  → suggestTechnologies() queries local Typesense "technology" collection (now populated)
  → Returns options (React, TypeScript, etc.)
  → User selects → handleAddTechnology() → runSearch() → buildFilterString()
  → Typesense job_posting filtered by technology_ids → results update
```

## Testing

- Run `typesense-taxonomy-sync-local.py` once
- Open `/en/explore`, click Filters → Technology modal → type "React" → options appear
- Select React → results filter to React jobs
- Repeat for Seniority, Occupation, Location
- ExcludeTitlePills appears inside the expanded Filters panel, not above it
- "Clear all" still clears excludeTitles

## Out of Scope

- Salary and experience filters already work (no taxonomy lookup needed)
- Employment type filter works independently
- No changes to the search backend or Typesense filter logic
