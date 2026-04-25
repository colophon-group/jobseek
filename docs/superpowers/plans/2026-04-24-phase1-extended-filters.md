# Phase 1 Extended — Filter Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix broken explore filters by syncing taxonomy Typesense collections from production to local, and move ExcludeTitlePills inside the collapsible Filters panel.

**Architecture:** A one-shot Python script reads all taxonomy documents from production Typesense using the search API and upserts them into local Typesense. A separate UI change threads ExcludeTitlePills through AdvancedSearchPanel instead of rendering it above the panel in SearchToolbar.

**Tech Stack:** Python (typesense client, uv), TypeScript/React (Next.js), Typesense

---

## File Structure

**Create:**
- `scripts/typesense-taxonomy-sync-local.py` — reads 5 taxonomy collections from prod Typesense, writes to local Typesense

**Modify:**
- `apps/web/src/components/search/advanced-search-panel.tsx` — add 3 props + render ExcludeTitlePills inside expanded panel
- `apps/web/src/components/search/search-toolbar.tsx` — remove ExcludeTitlePills block, pass 3 props to AdvancedSearchPanel, remove unused imports

---

## Task 1: Write the taxonomy sync script

**Files:**
- Create: `scripts/typesense-taxonomy-sync-local.py`

- [ ] **Step 1: Create the script**

```python
"""Sync Typesense taxonomy collections from production to local Typesense.

Reads all documents from production using PROD_TYPESENSE_SEARCH_KEY (search
access is sufficient — uses q=* pagination). Writes to local Typesense using
the local admin key.

Run once after `docker compose up`, repeat whenever taxonomy feels stale.

Usage (from repo root):
    PROD_TYPESENSE_SEARCH_KEY=<key> uv run python scripts/typesense-taxonomy-sync-local.py

Optional env vars (all have defaults):
    PROD_TYPESENSE_HOST     (default: typesense.colophon-group.org)
    PROD_TYPESENSE_PORT     (default: 443)
    PROD_TYPESENSE_PROTOCOL (default: https)
    TYPESENSE_HOST          (default: localhost)
    TYPESENSE_PORT          (default: 8108)
    TYPESENSE_PROTOCOL      (default: http)
    TYPESENSE_ADMIN_KEY     (default: local_dev_typesense_key)
"""
from __future__ import annotations

import os
import sys
import time

import typesense

COLLECTIONS = ["technology", "seniority", "occupation", "location", "company"]
PAGE_SIZE = 250
BATCH_SIZE = 250


def _build_prod_client() -> typesense.Client:
    key = os.environ.get("PROD_TYPESENSE_SEARCH_KEY")
    if not key:
        print("Error: PROD_TYPESENSE_SEARCH_KEY is required")
        print("  Get it from Vercel env vars or apps/crawler/.env.local on Hetzner")
        sys.exit(1)
    return typesense.Client({
        "nodes": [{
            "host": os.environ.get("PROD_TYPESENSE_HOST", "typesense.colophon-group.org"),
            "port": os.environ.get("PROD_TYPESENSE_PORT", "443"),
            "protocol": os.environ.get("PROD_TYPESENSE_PROTOCOL", "https"),
        }],
        "api_key": key,
        "connection_timeout_seconds": 30,
    })


def _build_local_client() -> typesense.Client:
    return typesense.Client({
        "nodes": [{
            "host": os.environ.get("TYPESENSE_HOST", "localhost"),
            "port": os.environ.get("TYPESENSE_PORT", "8108"),
            "protocol": os.environ.get("TYPESENSE_PROTOCOL", "http"),
        }],
        "api_key": os.environ.get("TYPESENSE_ADMIN_KEY", "local_dev_typesense_key"),
        "connection_timeout_seconds": 10,
    })


def _fetch_all(prod: typesense.Client, name: str) -> list[dict]:
    """Paginate through all documents in a collection using q=* search."""
    docs: list[dict] = []
    page = 1
    while True:
        result = prod.collections[name].documents.search({
            "q": "*",
            "query_by": "name",
            "per_page": PAGE_SIZE,
            "page": page,
        })
        hits = result.get("hits") or []
        if not hits:
            break
        docs.extend(hit["document"] for hit in hits)
        found = result.get("found", 0)
        if len(docs) >= found:
            break
        page += 1
    return docs


def _upsert_all(local: typesense.Client, name: str, docs: list[dict]) -> int:
    """Upsert documents into local collection in batches. Returns error count."""
    errors = 0
    for i in range(0, len(docs), BATCH_SIZE):
        batch = docs[i : i + BATCH_SIZE]
        results = local.collections[name].documents.import_(batch, {"action": "upsert"})
        errors += sum(
            1 for r in results if isinstance(r, dict) and not r.get("success", True)
        )
    return errors


def main() -> None:
    prod = _build_prod_client()
    local = _build_local_client()

    # Verify local Typesense is reachable
    try:
        local.collections.retrieve()
    except Exception as e:
        print(f"Cannot reach local Typesense at localhost:8108 — is Docker running? ({e})")
        sys.exit(1)

    t0 = time.monotonic()
    total_errors = 0

    for name in COLLECTIONS:
        print(f"  {name}...", end=" ", flush=True)
        try:
            docs = _fetch_all(prod, name)
        except Exception as e:
            print(f"SKIP (prod read failed: {e})")
            continue

        if not docs:
            print("0 docs (collection empty on prod)")
            continue

        errors = _upsert_all(local, name, docs)
        total_errors += errors
        status = f"{errors} errors" if errors else "ok"
        print(f"{len(docs)} docs — {status}")

    elapsed = time.monotonic() - t0
    print(f"\nDone in {elapsed:.1f}s  (total errors: {total_errors})")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Get the production Typesense search key**

The search key is in Vercel project env vars (variable name: `TYPESENSE_SEARCH_KEY`). Run:

```bash
# Option A — from Vercel CLI if linked
cd apps/web && vercel env pull /tmp/vercel-env.txt && grep TYPESENSE /tmp/vercel-env.txt

# Option B — copy from Hetzner crawler machine
# ssh -i ~/.ssh/hetzner_deploy root@<WORKER_IP> 'grep TYPESENSE_SEARCH_KEY /home/deploy/.env'
```

- [ ] **Step 3: Run the script**

From the repo root (not apps/crawler — the script uses `typesense` which is a crawler dep):

```bash
cd apps/crawler
PROD_TYPESENSE_SEARCH_KEY=<key-from-step-2> uv run python ../../scripts/typesense-taxonomy-sync-local.py
```

Expected output:
```
  technology... 186 docs — ok
  seniority... 9 docs — ok
  occupation... 66 docs — ok
  location... <N> docs — ok
  company... <N> docs — ok

Done in X.Xs  (total errors: 0)
```

- [ ] **Step 4: Verify filters work in the browser**

1. `pnpm dev` (if not already running)
2. Open `http://localhost:3000/en/explore`
3. Click **Filters** → **Technology** → type "React" → options appear
4. Select React → results update to React jobs
5. Repeat for **Level** (seniority), **Role** (occupation), **Location**

- [ ] **Step 5: Commit**

```bash
git add scripts/typesense-taxonomy-sync-local.py
git commit -m "feat: add local taxonomy sync script (prod Typesense → local)"
```

---

## Task 2: Move ExcludeTitlePills inside the Filters panel

**Files:**
- Modify: `apps/web/src/components/search/advanced-search-panel.tsx`
- Modify: `apps/web/src/components/search/search-toolbar.tsx`

### Step 1: Update `advanced-search-panel.tsx`

- [ ] **Step 1a: Add `Trans` to the lingui import (line 4)**

Change:
```tsx
import { useLingui } from "@lingui/react/macro";
```
To:
```tsx
import { useLingui, Trans } from "@lingui/react/macro";
```

- [ ] **Step 1b: Add ExcludeTitlePills import after the existing imports (after line 14)**

Add this line after `import type { HistogramFilters } from "@/lib/search";`:
```tsx
import { ExcludeTitlePills } from "./exclude-title-pills";
```

- [ ] **Step 1c: Add three optional props to `AdvancedSearchPanelProps` (after `histogramFilters?` on line 43)**

```tsx
  histogramFilters?: HistogramFilters;
  excludeTitles?: string[];
  onAddExcludeTitle?: (keyword: string) => void;
  onRemoveExcludeTitle?: (keyword: string) => void;
```

- [ ] **Step 1d: Destructure the new props in the function signature (after `histogramFilters` on line 69)**

```tsx
  histogramFilters,
  excludeTitles,
  onAddExcludeTitle,
  onRemoveExcludeTitle,
```

- [ ] **Step 1e: Replace the `{expanded && ...}` block (lines 142–181) with a version that includes ExcludeTitlePills**

Replace:
```tsx
      {expanded && (
        <div className="mt-2 flex flex-wrap gap-2">
```

With:
```tsx
      {expanded && (
        <>
        <div className="mt-2 flex flex-wrap gap-2">
```

And close the entire expanded block (before the modals) by replacing the closing `)}` of the inner div with:
```tsx
        </div>
        {onAddExcludeTitle && (
          <div className="mt-3 space-y-1.5">
            <div className="text-xs text-muted">
              <Trans
                id="search.excludeTitles.label"
                comment="Section label for title exclusion input"
              >
                Hide jobs with these words in the title
              </Trans>
            </div>
            <ExcludeTitlePills
              keywords={excludeTitles ?? []}
              onAdd={onAddExcludeTitle}
              onRemove={onRemoveExcludeTitle ?? (() => {})}
            />
          </div>
        )}
        </>
      )}
```

The full `{expanded && ...}` block after the edit (lines 142–181 region) should look like:

```tsx
      {expanded && (
        <>
          <div className="mt-2 flex flex-wrap gap-2">
            <button onClick={() => setLocationModalOpen(true)} className={btnClass}>
              <MapPin size={14} className="shrink-0 text-muted" />
              {t({ id: "search.advanced.location", comment: "Label for location filter in advanced search", message: "Location" })}
            </button>
            <button onClick={() => setOccupationModalOpen(true)} className={btnClass}>
              <Briefcase size={14} className="shrink-0 text-muted" />
              {t({ id: "search.advanced.role", comment: "Label for role/occupation filter in advanced search", message: "Role" })}
            </button>
            <button onClick={() => setSeniorityModalOpen(true)} className={btnClass}>
              <BarChart3 size={14} className="shrink-0 text-muted" />
              {t({ id: "search.advanced.level", comment: "Label for seniority/level filter in advanced search", message: "Level" })}
            </button>
            {onAddTechnology && (
              <button onClick={() => setTechnologyModalOpen(true)} className={btnClass}>
                <Code2 size={14} className="shrink-0 text-muted" />
                {t({ id: "search.advanced.technology", comment: "Label for technology filter in advanced search", message: "Technology" })}
              </button>
            )}
            {onToggleEmploymentType && (
              <button onClick={() => setEmploymentTypeModalOpen(true)} className={btnClass}>
                <CalendarDays size={14} className="shrink-0 text-muted" />
                {t({ id: "search.advanced.employmentType", comment: "Label for employment type filter in advanced search", message: "Type" })}
              </button>
            )}
            {onSalaryChange && (
              <button onClick={() => setSalaryModalOpen(true)} className={btnClass}>
                <DollarSign size={14} className="shrink-0 text-muted" />
                {t({ id: "search.advanced.salary", comment: "Label for salary filter in advanced search", message: "Salary" })}
              </button>
            )}
            {onExperienceChange && (
              <button onClick={() => setExperienceModalOpen(true)} className={btnClass}>
                <Clock size={14} className="shrink-0 text-muted" />
                {t({ id: "search.advanced.experience", comment: "Label for experience filter in advanced search", message: "Experience" })}
              </button>
            )}
          </div>
          {onAddExcludeTitle && (
            <div className="mt-3 space-y-1.5">
              <div className="text-xs text-muted">
                <Trans
                  id="search.excludeTitles.label"
                  comment="Section label for title exclusion input"
                >
                  Hide jobs with these words in the title
                </Trans>
              </div>
              <ExcludeTitlePills
                keywords={excludeTitles ?? []}
                onAdd={onAddExcludeTitle}
                onRemove={onRemoveExcludeTitle ?? (() => {})}
              />
            </div>
          )}
        </>
      )}
```

### Step 2: Update `search-toolbar.tsx`

- [ ] **Step 2a: Remove `ExcludeTitlePills` from imports (line 8)**

Remove this line:
```tsx
import { ExcludeTitlePills } from "@/components/search/exclude-title-pills";
```

- [ ] **Step 2b: Remove `Trans` from the lingui import (line 3)**

Change:
```tsx
import { useLingui, Trans } from "@lingui/react/macro";
```
To:
```tsx
import { useLingui } from "@lingui/react/macro";
```

- [ ] **Step 2c: Remove the ExcludeTitlePills block (lines 135–146)**

Remove this entire block from inside the `return` JSX:
```tsx
      <div className="space-y-1.5">
        <div className="text-xs text-muted">
          <Trans id="search.excludeTitles.label" comment="Section label for title exclusion input">
            Hide jobs with these words in the title
          </Trans>
        </div>
        <ExcludeTitlePills
          keywords={excludeTitles}
          onAdd={onAddExcludeTitle}
          onRemove={onRemoveExcludeTitle}
        />
      </div>
```

- [ ] **Step 2d: Pass the 3 new props to AdvancedSearchPanel**

In the `<AdvancedSearchPanel ...>` JSX block (which starts at the line after the removed block), add these three props:
```tsx
        excludeTitles={excludeTitles}
        onAddExcludeTitle={onAddExcludeTitle}
        onRemoveExcludeTitle={onRemoveExcludeTitle}
```

The full updated `<AdvancedSearchPanel>` call should be:
```tsx
      <AdvancedSearchPanel
        locale={locale}
        userLat={userLat}
        userLng={userLng}
        locations={locations}
        occupations={occupations}
        seniorities={seniorities}
        technologies={technologies}
        salaryCurrency={salaryCurrency ?? "EUR"}
        salaryMin={salaryMin}
        salaryMax={salaryMax}
        experienceMin={experienceMin}
        experienceMax={experienceMax}
        onAddLocation={onAddLocation}
        onRemoveLocation={onRemoveLocation}
        onAddOccupation={onAddOccupation}
        onRemoveOccupation={onRemoveOccupation}
        onAddSeniority={onAddSeniority}
        onRemoveSeniority={onRemoveSeniority}
        onAddTechnology={onAddTechnology}
        onRemoveTechnology={onRemoveTechnology}
        employmentTypes={employmentTypes}
        onToggleEmploymentType={onToggleEmploymentType}
        onSalaryChange={onSalaryChange}
        onExperienceChange={onExperienceChange}
        histogramFilters={histogramFilters}
        excludeTitles={excludeTitles}
        onAddExcludeTitle={onAddExcludeTitle}
        onRemoveExcludeTitle={onRemoveExcludeTitle}
      />
```

### Step 3: Verify

- [ ] **Step 3a: TypeScript check**

```bash
cd apps/web && pnpm tsc --noEmit 2>&1 | head -30
```
Expected: no errors

- [ ] **Step 3b: Manual browser verification**

1. Open `http://localhost:3000/en/explore`
2. Click **Filters** — the panel expands
3. Confirm ExcludeTitlePills input appears **inside** the expanded panel (after Location/Role/Level/Technology/etc. buttons)
4. Confirm there is **no** ExcludeTitlePills input above the Filters button
5. Add "Manager" as an excluded title — it filters results and appears in the pill row
6. Click **Clear all** — clears excluded titles along with other filters

- [ ] **Step 3c: Commit**

```bash
git add apps/web/src/components/search/advanced-search-panel.tsx \
        apps/web/src/components/search/search-toolbar.tsx
git commit -m "feat: move ExcludeTitlePills inside the Filters panel"
```
