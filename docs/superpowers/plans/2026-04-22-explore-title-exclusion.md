# Explore Title-Keyword Exclusion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a user-editable `excludeTitles` filter on the Explore page that hides jobs whose title contains any of the specified keywords, with word-boundary case-insensitive matching, URL-state persistence, and save-as-watchlist compatibility.

**Architecture:** A new `excludeTitles` field is added to `WatchlistFilters` and `SearchFilters` types. Pure helpers (`escapeRegex`, `buildExcludeTitleRegex`, `parseExcludeParam`, `serializeExcludeParam`) carry the regex logic. `TypesenseSearchProvider.search()` over-fetches by 1.5× and applies the regex to each company's `postings[]` array, dropping matching postings and dropping companies that become empty. The Explore page reads/writes the `exclude` URL param alongside `q`, `loc`, etc., renders a new `ExcludeTitlePills` input inside `SearchToolbar`, and passes the array through `searchJobs()` to the provider.

**Tech Stack:** Next.js 15, React, TypeScript, Vitest + @testing-library/react, Typesense 27.1 (already deployed), existing Drizzle schema (no migration).

**Scope note (MVP simplification vs. spec):** The spec allowed one follow-up Typesense fetch if post-filtering leaves a short page. This plan implements only the initial over-fetch pass — short pages are returned as-is. The follow-up-fetch optimization is trivial to add later and is explicitly deferred to keep the MVP pagination math simple. All other spec sections are implemented as written.

---

## File Structure

**Create:**
- `apps/web/src/lib/search/exclude-title.ts` — pure helpers: `escapeRegex`, `buildExcludeTitleRegex`, `parseExcludeParam`, `serializeExcludeParam`, `MAX_EXCLUDE_TITLES`
- `apps/web/src/lib/search/__tests__/exclude-title.test.ts` — unit tests for all helpers
- `apps/web/src/lib/search/__tests__/typesense-exclude-filter.test.ts` — unit tests for the provider-level post-filter
- `apps/web/src/components/search/exclude-title-pills.tsx` — `ExcludeTitlePills` React component
- `apps/web/src/components/search/__tests__/exclude-title-pills.test.tsx` — component tests

**Modify:**
- `apps/web/src/lib/actions/watchlists.ts` — add `excludeTitles?: string[]` to `WatchlistFilters`
- `apps/web/src/lib/search/types.ts` — add `excludeTitles?: string[]` to `SearchFilters`
- `apps/web/src/lib/search/typesense.ts` — implement post-filter inside `search()`
- `apps/web/src/lib/actions/search.ts` — add `excludeTitles` to `searchJobs()` params and thread through
- `apps/web/src/lib/actions/search-input.ts` — add `excludeTitles` to `ParsedSearchFilters` + `parseSearchFilters()`
- `apps/web/src/lib/actions/explore-data.ts` — read `exclude` URL param + thread `excludeTitles` into `parseSearchFilters` and `searchJobs`
- `apps/web/src/lib/search/query-params.ts` — emit `exclude` in `buildFilterQuery` / `buildFilteredPath`
- `apps/web/src/components/SearchStateProvider.tsx` — extend `SearchStateSnapshot` + `buildCacheKey` with `excludeTitles`
- `apps/web/app/[lang]/(app)/explore/search-page.tsx` — read/write `exclude` URL param, add state/refs, pass through runSearch
- `apps/web/app/[lang]/(app)/explore/explore-content.tsx` — pass `initialExcludeTitles` prop
- `apps/web/src/components/search/search-toolbar.tsx` — render `ExcludeTitlePills` and active exclusion chips

---

## Task 1: Pure helpers — type, regex, URL param parse/serialize

**Files:**
- Create: `apps/web/src/lib/search/exclude-title.ts`
- Create: `apps/web/src/lib/search/__tests__/exclude-title.test.ts`
- Modify: `apps/web/src/lib/actions/watchlists.ts` (type extension)
- Modify: `apps/web/src/lib/search/types.ts` (type extension)

- [ ] **Step 1: Write failing tests for helpers**

Create `apps/web/src/lib/search/__tests__/exclude-title.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import {
  escapeRegex,
  buildExcludeTitleRegex,
  parseExcludeParam,
  serializeExcludeParam,
  MAX_EXCLUDE_TITLES,
} from "@/lib/search/exclude-title";

describe("escapeRegex", () => {
  it("escapes regex metacharacters", () => {
    expect(escapeRegex("a.b*c+d?e^f$g(h)i[j]k{l}m|n\\o")).toBe(
      "a\\.b\\*c\\+d\\?e\\^f\\$g\\(h\\)i\\[j\\]k\\{l\\}m\\|n\\\\o",
    );
  });

  it("leaves ordinary characters alone", () => {
    expect(escapeRegex("senior engineer")).toBe("senior engineer");
  });
});

describe("buildExcludeTitleRegex", () => {
  it("returns null for empty input", () => {
    expect(buildExcludeTitleRegex([])).toBeNull();
  });

  it("matches whole words case-insensitively", () => {
    const re = buildExcludeTitleRegex(["senior", "staff"])!;
    expect(re.test("Senior Engineer")).toBe(true);
    expect(re.test("STAFF ENGINEER")).toBe(true);
    expect(re.test("senior")).toBe(true);
  });

  it("does NOT match inside other words (word boundary)", () => {
    const re = buildExcludeTitleRegex(["senior"])!;
    expect(re.test("Seniority Product Lead")).toBe(false);
    expect(re.test("presenior")).toBe(false);
  });

  it("supports multi-word phrases", () => {
    const re = buildExcludeTitleRegex(["head of"])!;
    expect(re.test("Head of Product")).toBe(true);
    expect(re.test("Regional Head of Sales")).toBe(true);
    expect(re.test("Heading of Design")).toBe(false);
  });

  it("escapes regex metacharacters in keywords", () => {
    const re = buildExcludeTitleRegex(["c++", "sr."])!;
    expect(re.test("C++ Developer")).toBe(true);
    expect(re.test("Sr. Manager")).toBe(true);
  });
});

describe("parseExcludeParam", () => {
  it("returns empty array for undefined", () => {
    expect(parseExcludeParam(undefined)).toEqual([]);
  });

  it("returns empty array for empty string", () => {
    expect(parseExcludeParam("")).toEqual([]);
  });

  it("splits on commas and trims", () => {
    expect(parseExcludeParam("senior, staff ,principal")).toEqual([
      "senior",
      "staff",
      "principal",
    ]);
  });

  it("drops empty tokens", () => {
    expect(parseExcludeParam("senior,,staff,")).toEqual(["senior", "staff"]);
  });

  it("dedupes case-insensitively (keeps first occurrence)", () => {
    expect(parseExcludeParam("Senior,senior,SENIOR,staff")).toEqual([
      "Senior",
      "staff",
    ]);
  });

  it("caps at MAX_EXCLUDE_TITLES", () => {
    const tokens = Array.from({ length: MAX_EXCLUDE_TITLES + 10 }, (_, i) => `kw${i}`);
    const parsed = parseExcludeParam(tokens.join(","));
    expect(parsed).toHaveLength(MAX_EXCLUDE_TITLES);
    expect(parsed[0]).toBe("kw0");
  });
});

describe("serializeExcludeParam", () => {
  it("returns undefined for empty array", () => {
    expect(serializeExcludeParam([])).toBeUndefined();
  });

  it("joins with commas", () => {
    expect(serializeExcludeParam(["senior", "staff"])).toBe("senior,staff");
  });

  it("round-trips through parseExcludeParam", () => {
    const input = ["senior", "head of", "staff"];
    const serialized = serializeExcludeParam(input)!;
    expect(parseExcludeParam(serialized)).toEqual(input);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && pnpm test src/lib/search/__tests__/exclude-title.test.ts`
Expected: FAIL with "Cannot find module '@/lib/search/exclude-title'"

- [ ] **Step 3: Implement helpers**

Create `apps/web/src/lib/search/exclude-title.ts`:

```ts
/**
 * Pure helpers for title-exclusion keyword filtering.
 *
 * Used on both sides: URL param round-trip on the client and regex
 * construction for post-Typesense filtering on the server.
 */

export const MAX_EXCLUDE_TITLES = 50;

/** Escape a string so it can be safely embedded in a RegExp pattern. */
export function escapeRegex(input: string): string {
  return input.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

/**
 * Build a single case-insensitive regex matching any of the given keywords as
 * whole words (word-boundary on both ends). Returns null when keywords is
 * empty so callers can use truthy checks to short-circuit.
 *
 * The `\b` boundary handles single-word and multi-word keywords correctly:
 *   - `senior` → matches "Senior Engineer" but not "Seniority"
 *   - `head of` → matches "Head of Product" but not "Heading of Design"
 */
export function buildExcludeTitleRegex(keywords: string[]): RegExp | null {
  if (keywords.length === 0) return null;
  const alternation = keywords.map(escapeRegex).join("|");
  return new RegExp(`\\b(?:${alternation})\\b`, "i");
}

/**
 * Parse the URL `exclude=` param into a deduped, trimmed, capped array.
 * Case-insensitive dedupe keeps first occurrence.
 */
export function parseExcludeParam(raw: string | undefined): string[] {
  if (!raw) return [];
  const seen = new Set<string>();
  const out: string[] = [];
  for (const token of raw.split(",")) {
    const trimmed = token.trim();
    if (!trimmed) continue;
    const key = trimmed.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(trimmed);
    if (out.length >= MAX_EXCLUDE_TITLES) break;
  }
  return out;
}

/**
 * Serialize an array of keywords into a comma-separated URL param value, or
 * undefined when empty so callers can conditionally emit the param.
 */
export function serializeExcludeParam(keywords: string[]): string | undefined {
  if (keywords.length === 0) return undefined;
  return keywords.join(",");
}
```

- [ ] **Step 4: Extend WatchlistFilters type**

Edit `apps/web/src/lib/actions/watchlists.ts:30-42`. Replace the existing `WatchlistFilters` type with:

```ts
export type WatchlistFilters = {
  keywords?: string[];
  excludeTitles?: string[];
  locationSlugs?: string[];
  occupationSlugs?: string[];
  senioritySlugs?: string[];
  technologySlugs?: string[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  anyCompany?: boolean;
};
```

- [ ] **Step 5: Extend SearchFilters type**

Edit `apps/web/src/lib/search/types.ts:30-42`. Replace the existing `SearchFilters` interface with:

```ts
export interface SearchFilters {
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  excludeTitles?: string[];
  languages: string[];
  locale: string;
}
```

- [ ] **Step 6: Run tests + type-check to verify they pass**

Run: `cd apps/web && pnpm test src/lib/search/__tests__/exclude-title.test.ts`
Expected: PASS, all 15+ assertions green.

Run: `cd apps/web && pnpm tsc --noEmit`
Expected: PASS with no errors (the type extensions don't break any existing consumers because `excludeTitles` is optional).

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/lib/search/exclude-title.ts \
        apps/web/src/lib/search/__tests__/exclude-title.test.ts \
        apps/web/src/lib/actions/watchlists.ts \
        apps/web/src/lib/search/types.ts
git commit -m "feat(search): add excludeTitles type + pure helpers"
```

---

## Task 2: Post-filter inside `TypesenseSearchProvider.search()`

**Files:**
- Modify: `apps/web/src/lib/search/typesense.ts:211-263`
- Create: `apps/web/src/lib/search/__tests__/typesense-exclude-filter.test.ts`

- [ ] **Step 1: Write failing tests for the post-filter**

Create `apps/web/src/lib/search/__tests__/typesense-exclude-filter.test.ts`:

```ts
import { describe, it, expect } from "vitest";
import { applyExcludeTitleFilter } from "@/lib/search/typesense";
import type { SearchResultCompany } from "@/lib/search/types";

function company(id: string, titles: string[]): SearchResultCompany {
  return {
    company: { id, name: id, slug: id, icon: null },
    activeMatches: titles.length,
    yearMatches: titles.length,
    postings: titles.map((title, i) => ({
      id: `${id}-${i}`,
      title,
      firstSeenAt: new Date(0),
      relevanceScore: 0,
      locations: [],
      isActive: true,
    })),
  };
}

describe("applyExcludeTitleFilter", () => {
  it("returns input unchanged when excludeTitles is empty", () => {
    const input = [company("c1", ["Senior Engineer"])];
    expect(applyExcludeTitleFilter(input, [])).toBe(input);
  });

  it("drops postings whose title matches any keyword (whole word)", () => {
    const input = [
      company("c1", ["Senior Engineer", "Junior Engineer", "Staff Engineer"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior", "staff"]);
    expect(result).toHaveLength(1);
    expect(result[0].postings.map((p) => p.title)).toEqual(["Junior Engineer"]);
  });

  it("preserves totals (activeMatches/yearMatches) because they are company-wide", () => {
    const input = [
      company("c1", ["Senior Engineer", "Junior Engineer", "Staff Engineer"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior"]);
    expect(result[0].activeMatches).toBe(3);
    expect(result[0].yearMatches).toBe(3);
  });

  it("drops companies whose postings all match", () => {
    const input = [
      company("c1", ["Senior Engineer", "Staff Engineer"]),
      company("c2", ["Junior Engineer"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior", "staff"]);
    expect(result.map((c) => c.company.id)).toEqual(["c2"]);
  });

  it("is case-insensitive and uses word boundaries", () => {
    const input = [
      company("c1", ["SENIOR Engineer", "Seniority Coach"]),
    ];
    const result = applyExcludeTitleFilter(input, ["senior"]);
    expect(result[0].postings.map((p) => p.title)).toEqual(["Seniority Coach"]);
  });

  it("handles null titles by keeping them (nothing to match)", () => {
    const input: SearchResultCompany[] = [
      {
        company: { id: "c1", name: "c1", slug: "c1", icon: null },
        activeMatches: 1,
        yearMatches: 1,
        postings: [
          { id: "p1", title: null, firstSeenAt: new Date(0), relevanceScore: 0, locations: [] },
        ],
      },
    ];
    const result = applyExcludeTitleFilter(input, ["senior"]);
    expect(result).toHaveLength(1);
    expect(result[0].postings).toHaveLength(1);
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && pnpm test src/lib/search/__tests__/typesense-exclude-filter.test.ts`
Expected: FAIL with "applyExcludeTitleFilter is not a function" or similar export error.

- [ ] **Step 3: Implement `applyExcludeTitleFilter` + integrate into `search()`**

Edit `apps/web/src/lib/search/typesense.ts`. Near the top of the file (after existing imports, before the existing class), add:

```ts
import { buildExcludeTitleRegex } from "@/lib/search/exclude-title";

/**
 * Drop postings whose title matches any excludeTitles keyword (word boundary,
 * case-insensitive). Companies whose postings all match are dropped entirely.
 * activeMatches/yearMatches are left untouched — they represent company-wide
 * totals beyond the preview array, so we cannot honestly decrement them.
 */
export function applyExcludeTitleFilter(
  companies: SearchResultCompany[],
  excludeTitles: string[],
): SearchResultCompany[] {
  const re = buildExcludeTitleRegex(excludeTitles);
  if (!re) return companies;
  const out: SearchResultCompany[] = [];
  for (const c of companies) {
    const postings = c.postings.filter((p) => !(p.title && re.test(p.title)));
    if (postings.length === 0) continue;
    out.push({ ...c, postings });
  }
  return out;
}
```

Make sure `SearchResultCompany` is imported in that file (it likely is — check the existing imports and add if missing).

Then modify the existing `search()` method at `apps/web/src/lib/search/typesense.ts:211-263` to over-fetch and apply the filter. Replace the method body with:

```ts
async search(
  params: SearchFilters & {
    keywords: string[];
    offset: number;
    limit: number;
  },
): Promise<SearchResponse> {
  try {
    const { keywords, offset, limit, locationIds, excludeTitles } = params;
    const filterStr = buildFilterString(params);
    const activeFilter = `is_active:true${filterStr ? " && " + filterStr : ""}`;
    const client = getSearchClient();

    const hasExclusions = (excludeTitles?.length ?? 0) > 0;
    const overFetch = hasExclusions
      ? Math.min(Math.ceil(limit * 1.5), 100)
      : limit;

    const result: TsSearchResponse<JobPostingDoc> = await client
      .collections<JobPostingDoc>("job_posting")
      .documents()
      .search({
        q: keywords.join(" "),
        query_by: "title",
        filter_by: activeFilter,
        sort_by: "_text_match:desc,first_seen_at:desc",
        group_by: "company_id",
        group_limit: 10,
        per_page: overFetch,
        page: Math.floor(offset / limit) + 1,
        typo_tokens_threshold: 1,
        drop_tokens_threshold: 1,
        facet_by: "company_id",
        facet_strategy: "exhaustive",
        max_facet_values: 1,
      });

    const totalCompanies =
      result.facet_counts?.[0]?.stats?.total_values ?? 0;

    const groupedHits = (result.grouped_hits ?? []) as GroupedHit[];
    const companyIds = groupedHits.map(
      (g: GroupedHit) => g.hits[0].document.company_id,
    );
    const yearCountMap = await fetchYearCountsFiltered(
      companyIds,
      filterStr,
      keywords.join(" "),
    );

    const mapped = mapGroupedHits(
      groupedHits,
      totalCompanies,
      yearCountMap,
      locationIds,
    );

    if (!hasExclusions) return mapped;

    const filteredCompanies = applyExcludeTitleFilter(
      mapped.companies,
      excludeTitles ?? [],
    ).slice(0, limit);

    return { ...mapped, companies: filteredCompanies };
  } catch (err) {
    console.error("[typesense] search error", err);
    return emptyResponse();
  }
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/web && pnpm test src/lib/search/__tests__/typesense-exclude-filter.test.ts`
Expected: PASS, all 6 assertions green.

Run: `cd apps/web && pnpm tsc --noEmit`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/search/typesense.ts \
        apps/web/src/lib/search/__tests__/typesense-exclude-filter.test.ts
git commit -m "feat(search): post-filter Typesense results by excludeTitles"
```

---

## Task 3: Thread `excludeTitles` through `searchJobs()` and `parseSearchFilters()`

**Files:**
- Modify: `apps/web/src/lib/actions/search.ts:200-232`
- Modify: `apps/web/src/lib/actions/search-input.ts:10-16,125-134`
- Modify: `apps/web/src/lib/search/query-params.ts`

- [ ] **Step 1: Extend `searchJobs()` params**

Edit `apps/web/src/lib/actions/search.ts:200-232`. Replace the `searchJobs` function with:

```ts
export async function searchJobs(params: {
  keywords: string[];
  locationIds?: number[];
  occupationIds?: number[];
  seniorityIds?: number[];
  technologyIds?: number[];
  employmentTypes?: string[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  excludeTitles?: string[];
  languages: string[];
  locale: string;
  offset: number;
  limit: number;
}): Promise<SearchResponse> {
  const userId = await getSessionUserId();

  // Enforce truncation for unauthenticated users
  if (!userId && params.offset >= ANON_MAX_COMPANIES) {
    return { companies: [], totalCompanies: 0, truncated: true };
  }

  const result = await getSearchProvider().search(params);

  if (!userId && params.offset + result.companies.length >= ANON_MAX_COMPANIES) {
    return { ...result, truncated: true };
  }

  return result;
}
```

- [ ] **Step 2: Extend `ParsedSearchFilters` + `parseSearchFilters()`**

Edit `apps/web/src/lib/actions/search-input.ts:10-16` — replace the `ParsedSearchFilters` interface with:

```ts
export interface ParsedSearchFilters {
  keywords: string[];
  excludeTitles: string[];
  locations: ParsedSearchLocation[];
  occupations: { id: number; slug: string; name: string }[];
  seniorities: { id: number; slug: string; name: string }[];
  technologies: { id: number; slug: string; name: string }[];
}
```

Edit `apps/web/src/lib/actions/search-input.ts:125-134` — add `exclude` to the params signature. Change the function signature to:

```ts
export async function parseSearchFilters(params: {
  q?: string;
  exclude?: string;
  loc?: string;
  occ?: string;
  sen?: string;
  tech?: string;
  locale: string;
  userLat?: number;
  userLng?: number;
}): Promise<ParsedSearchFilters> {
```

At the top of the file, add the import:

```ts
import { parseExcludeParam } from "@/lib/search/exclude-title";
```

Find the existing `return { keywords: ..., locations: ..., occupations: ..., seniorities: ..., technologies: ... }` block at the end of `parseSearchFilters` and add `excludeTitles`:

```ts
return {
  keywords,
  excludeTitles: parseExcludeParam(params.exclude),
  locations,
  occupations,
  seniorities,
  technologies,
};
```

(If you are unsure where that return is, run `grep -n "return {" apps/web/src/lib/actions/search-input.ts` — there is one returning the ParsedSearchFilters object at the bottom of `parseSearchFilters`.)

- [ ] **Step 3: Extend URL query-param serializer**

Edit `apps/web/src/lib/search/query-params.ts`. Both `buildFilterQuery` and `buildFilteredPath` need to accept and emit the `exclude` param.

Add a new optional argument to `buildFilterQuery` (append after existing params for backwards compat):

```ts
export function buildFilterQuery(
  keywords: string[],
  locations: SerializableLocation[],
  occupations?: SerializableOccupation[],
  seniorities?: SerializableSeniority[],
  technologies?: SerializableTechnology[],
  excludeTitles?: string[],
): string {
  const params = new URLSearchParams();
  if (keywords.length > 0) params.set("q", keywords.join(","));
  if (locations.length > 0) {
    params.set("loc", locations.map((l) => l.slug).join(","));
  }
  if (occupations && occupations.length > 0) {
    params.set("occ", occupations.map((o) => o.slug).join(","));
  }
  if (seniorities && seniorities.length > 0) {
    params.set("sen", seniorities.map((s) => s.slug).join(","));
  }
  if (technologies && technologies.length > 0) {
    params.set("tech", technologies.map((t) => t.slug).join(","));
  }
  if (excludeTitles && excludeTitles.length > 0) {
    params.set("exclude", excludeTitles.join(","));
  }
  return params.toString();
}
```

Do the same for `buildFilteredPath`: add `excludeTitles?: string[]` as a final optional param and emit `exclude` when non-empty (mirror the `buildFilterQuery` logic exactly).

- [ ] **Step 4: Type-check + run existing tests**

Run: `cd apps/web && pnpm tsc --noEmit`
Expected: PASS. (The optional `excludeTitles` additions don't break existing callers.)

Run: `cd apps/web && pnpm test`
Expected: PASS — all existing tests continue to pass, and the new helper/provider tests from Tasks 1 and 2 remain green.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/lib/actions/search.ts \
        apps/web/src/lib/actions/search-input.ts \
        apps/web/src/lib/search/query-params.ts
git commit -m "feat(search): thread excludeTitles through server actions + URL helpers"
```

---

## Task 4: `ExcludeTitlePills` component

**Files:**
- Create: `apps/web/src/components/search/exclude-title-pills.tsx`
- Create: `apps/web/src/components/search/__tests__/exclude-title-pills.test.tsx`

- [ ] **Step 1: Write failing component tests**

Create `apps/web/src/components/search/__tests__/exclude-title-pills.test.tsx`:

```tsx
import { describe, it, expect, vi } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";
import { ExcludeTitlePills } from "@/components/search/exclude-title-pills";

// Minimal Lingui stub matching what @lingui/react/macro produces
vi.mock("@lingui/react/macro", () => ({
  useLingui: () => ({ t: (opts: { message: string }) => opts.message }),
}));

describe("ExcludeTitlePills", () => {
  it("renders existing keywords as dismissable chips", () => {
    render(
      <ExcludeTitlePills
        keywords={["senior", "staff"]}
        onAdd={() => {}}
        onRemove={() => {}}
      />,
    );
    expect(screen.getByText("senior")).toBeDefined();
    expect(screen.getByText("staff")).toBeDefined();
  });

  it("calls onRemove with the keyword when its × button is clicked", () => {
    const onRemove = vi.fn();
    render(
      <ExcludeTitlePills
        keywords={["senior"]}
        onAdd={() => {}}
        onRemove={onRemove}
      />,
    );
    fireEvent.click(screen.getByLabelText(/remove/i));
    expect(onRemove).toHaveBeenCalledWith("senior");
  });

  it("calls onAdd with trimmed input on form submit", () => {
    const onAdd = vi.fn();
    render(
      <ExcludeTitlePills keywords={[]} onAdd={onAdd} onRemove={() => {}} />,
    );
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "  principal  " } });
    fireEvent.submit(input.closest("form")!);
    expect(onAdd).toHaveBeenCalledWith("principal");
    expect(input.value).toBe("");
  });

  it("does not call onAdd for empty input", () => {
    const onAdd = vi.fn();
    render(
      <ExcludeTitlePills keywords={[]} onAdd={onAdd} onRemove={() => {}} />,
    );
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.submit(input.closest("form")!);
    expect(onAdd).not.toHaveBeenCalled();
  });

  it("does not call onAdd for case-insensitive duplicates", () => {
    const onAdd = vi.fn();
    render(
      <ExcludeTitlePills
        keywords={["Senior"]}
        onAdd={onAdd}
        onRemove={() => {}}
      />,
    );
    const input = screen.getByRole("textbox") as HTMLInputElement;
    fireEvent.change(input, { target: { value: "SENIOR" } });
    fireEvent.submit(input.closest("form")!);
    expect(onAdd).not.toHaveBeenCalled();
  });
});
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd apps/web && pnpm test src/components/search/__tests__/exclude-title-pills.test.tsx`
Expected: FAIL with module-not-found error.

- [ ] **Step 3: Implement the component**

Create `apps/web/src/components/search/exclude-title-pills.tsx`:

```tsx
"use client";

import { useState, useRef } from "react";
import { X, EyeOff } from "lucide-react";
import { useLingui } from "@lingui/react/macro";

interface ExcludeTitlePillsProps {
  keywords: string[];
  onAdd: (keyword: string) => void;
  onRemove: (keyword: string) => void;
}

export function ExcludeTitlePills({
  keywords,
  onAdd,
  onRemove,
}: ExcludeTitlePillsProps) {
  const { t } = useLingui();
  const [inputValue, setInputValue] = useState("");
  const inputRef = useRef<HTMLInputElement>(null);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    const trimmed = inputValue.trim();
    if (!trimmed) return;
    if (keywords.some((k) => k.toLowerCase() === trimmed.toLowerCase())) return;
    onAdd(trimmed);
    setInputValue("");
  };

  const placeholder = t({
    id: "search.excludeTitles.addPlaceholder",
    comment: "Placeholder in the exclude-title input",
    message: "Hide titles with...",
  });

  const removeLabel = t({
    id: "search.excludeTitles.remove",
    comment: "Aria label for removing an exclude-title pill",
    message: "Remove excluded title",
  });

  return (
    <div className="flex flex-wrap items-center gap-2">
      {keywords.map((kw) => (
        <span
          key={kw}
          className="inline-flex items-center gap-1 rounded-full bg-muted/10 px-3 py-1 text-sm text-muted"
        >
          <EyeOff size={12} className="shrink-0" />
          {kw}
          <button
            onClick={() => onRemove(kw)}
            className="ml-0.5 rounded-full p-0.5 transition-colors hover:bg-muted/20 cursor-pointer"
            aria-label={removeLabel}
          >
            <X size={12} />
          </button>
        </span>
      ))}
      <form onSubmit={handleSubmit} className="inline-flex">
        <div className="inline-flex items-center gap-1 rounded-full border border-dashed border-border-soft px-3 py-1">
          <EyeOff size={14} className="shrink-0 text-muted" />
          <div className="relative inline-grid items-center">
            <span className="invisible col-start-1 row-start-1 whitespace-pre text-sm">
              {inputValue || placeholder}
            </span>
            <input
              ref={inputRef}
              type="text"
              size={1}
              value={inputValue}
              onChange={(e) => setInputValue(e.target.value)}
              placeholder={placeholder}
              className="col-start-1 row-start-1 w-full min-w-0 bg-transparent text-sm outline-none placeholder:text-muted"
            />
          </div>
        </div>
      </form>
    </div>
  );
}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd apps/web && pnpm test src/components/search/__tests__/exclude-title-pills.test.tsx`
Expected: PASS, all 5 assertions green.

- [ ] **Step 5: Commit**

```bash
git add apps/web/src/components/search/exclude-title-pills.tsx \
        apps/web/src/components/search/__tests__/exclude-title-pills.test.tsx
git commit -m "feat(ui): add ExcludeTitlePills component"
```

---

## Task 5: Wire `excludeTitles` into `SearchPage` state + URL sync

**Files:**
- Modify: `apps/web/app/[lang]/(app)/explore/search-page.tsx`
- Modify: `apps/web/src/components/SearchStateProvider.tsx` (only if it tracks filter state; verify first — skip if the provider is URL-agnostic)

This task is the biggest because `search-page.tsx` is 777 lines and every filter has parallel state/ref/URL/runSearch hooks. Work carefully and mirror what `keywords` does everywhere.

- [ ] **Step 1: Add the prop to `SearchPageProps`**

Edit `apps/web/app/[lang]/(app)/explore/search-page.tsx:25-47`. Inside the `SearchPageProps` interface, add one line after `initialKeywords`:

```ts
  initialKeywords: string[];
  initialExcludeTitles: string[];
```

Also add it to the function destructuring signature (`apps/web/app/[lang]/(app)/explore/search-page.tsx:49-66` area — directly mirror the existing `initialKeywords` pattern).

- [ ] **Step 2: Add state + ref for `excludeTitles`**

Near line 87 where `const [keywords, setKeywords] = useState<string[]>(...)` is declared, add an analogous block immediately below it:

```ts
const [excludeTitles, setExcludeTitles] = useState<string[]>(
  shouldRestore ? (cached.excludeTitles ?? []) : initialExcludeTitles,
);
```

Near line 144 where `const keywordsRef = useRef(keywords);` is declared, add:

```ts
const excludeTitlesRef = useRef(excludeTitles);
```

Near line 158 where `keywordsRef.current = keywords;` is assigned, add:

```ts
excludeTitlesRef.current = excludeTitles;
```

- [ ] **Step 3: Read `exclude` from URL in the navigation `useEffect`**

Inside the `useEffect` that parses URL params (around `apps/web/app/[lang]/(app)/explore/search-page.tsx:204-237`), add one line after the existing param reads:

```ts
const exclude = searchParams.get("exclude") ?? undefined;
```

Update the `parseSearchFilters` call to pass `exclude`:

```ts
parseSearchFilters({ q, exclude, loc, occ, sen, tech, locale, userLat, userLng }).then((parsed) => {
  setKeywords(parsed.keywords); keywordsRef.current = parsed.keywords;
  setExcludeTitles(parsed.excludeTitles); excludeTitlesRef.current = parsed.excludeTitles;
  setLocations(parsed.locations); locationsRef.current = parsed.locations;
  setOccupations(parsed.occupations); occupationsRef.current = parsed.occupations;
  setSeniorities(parsed.seniorities); senioritiesRef.current = parsed.seniorities;
  setTechnologies(parsed.technologies); technologiesRef.current = parsed.technologies;
  runSearch();
});
```

- [ ] **Step 4: Include `excludeTitles` in every `searchJobs`/`listTopCompanies` call**

There are several `searchJobs(...)` calls in this file (see `grep -n "searchJobs" apps/web/app/\[lang\]/\(app\)/explore/search-page.tsx`). For EACH call, add `excludeTitles: excludeTitlesRef.current,` to the params object. The same applies to any `listTopCompanies` calls in this file.

Example:

```ts
await searchJobs({
  keywords: kws,
  locationIds,
  occupationIds,
  seniorityIds,
  technologyIds,
  employmentTypes: etypes,
  salaryMinEur: salMinEur,
  salaryMaxEur: salMaxEur,
  experienceMin: expMin,
  experienceMax: expMax,
  excludeTitles: excludeTitlesRef.current,
  languages,
  locale,
  offset,
  limit: PAGE_SIZE,
});
```

- [ ] **Step 5: Add `handleAddExcludeTitle` / `handleRemoveExcludeTitle` and URL sync**

Find the existing keyword-handling callbacks (search for `setKeywords` in the file). Below them, add:

```ts
const handleAddExcludeTitle = useCallback((keyword: string) => {
  const current = excludeTitlesRef.current;
  if (current.some((k) => k.toLowerCase() === keyword.toLowerCase())) return;
  const next = [...current, keyword];
  setExcludeTitles(next);
  excludeTitlesRef.current = next;
  updateUrl();
  runSearch();
}, [updateUrl, runSearch]);

const handleRemoveExcludeTitle = useCallback((keyword: string) => {
  const next = excludeTitlesRef.current.filter((k) => k !== keyword);
  setExcludeTitles(next);
  excludeTitlesRef.current = next;
  updateUrl();
  runSearch();
}, [updateUrl, runSearch]);
```

(If the file uses a different callback-creation pattern — e.g. inline arrow functions inside JSX — follow that convention instead; mirror the existing `onRemoveKeyword` handler shape exactly.)

Find `updateUrl` in the file (grep it). Inside `updateUrl`, wherever the other filter params (`q`, `loc`, `occ`, …) are serialized, add:

```ts
if (excludeTitlesRef.current.length > 0) {
  url.searchParams.set("exclude", excludeTitlesRef.current.join(","));
} else {
  url.searchParams.delete("exclude");
}
```

- [ ] **Step 6: Extend `SearchStateSnapshot` + `buildCacheKey` so the in-memory cache respects exclusions**

Edit `apps/web/src/components/SearchStateProvider.tsx:14-30`. Add one field to `SearchStateSnapshot`:

```ts
export interface SearchStateSnapshot {
  keywords: string[];
  excludeTitles: string[];
  locations: SelectedLocation[];
  // ... rest unchanged
}
```

Edit `apps/web/src/components/SearchStateProvider.tsx:32-47`. Replace `buildCacheKey` so it incorporates exclusions in the cache key (otherwise two queries that differ only in exclusions would share a cache entry):

```ts
export function buildCacheKey(
  keywords: string[],
  locationIds: number[],
  occupationIds?: number[],
  seniorityIds?: number[],
  technologyIds?: number[],
  excludeTitles?: string[],
): string {
  const parts = [
    [...keywords].sort().join(","),
    [...locationIds].sort().join(","),
    [...(occupationIds ?? [])].sort().join(","),
    [...(seniorityIds ?? [])].sort().join(","),
    [...(technologyIds ?? [])].sort().join(","),
    [...(excludeTitles ?? [])].map((s) => s.toLowerCase()).sort().join(","),
  ];
  return parts.join("|");
}
```

Now in `apps/web/app/[lang]/(app)/explore/search-page.tsx`, find every `buildCacheKey(...)` call (grep) and add `excludeTitlesRef.current` (or `excludeTitles`) as the new trailing argument. Find the cache-restore block (the `cached.keywords` reference around line 350) and read `cached.excludeTitles ?? []` wherever you read `cached.keywords`. Find the snapshot-write site that builds `SearchStateSnapshot` (grep for an object literal containing `keywords` and `companies` together) and add `excludeTitles: excludeTitlesRef.current` so future restores include it.

Find the `onClearAll` handler (grep `setKeywords([])`). Add analogous resets immediately after:

```ts
setExcludeTitles([]);
excludeTitlesRef.current = [];
```

- [ ] **Step 7: Update `fetchExploreData` (the actual server-side parse + search caller)**

Edit `apps/web/src/lib/actions/explore-data.ts:33-94`. The Explore page route hits `fetchExploreData`, which is where `parseSearchFilters` and `searchJobs` are wired together for the initial server-rendered payload — `explore-content.tsx` is a thin client wrapper above it.

After line 40 (the existing `const exp = firstOf(searchParams.exp);`), add:

```ts
const exclude = firstOf(searchParams.exclude);
```

Update the `parseSearchFilters` call at line 45:

```ts
parseSearchFilters({ q, exclude, loc, occ, sen, tech, locale, userLat, userLng }),
```

Update the `searchJobs(...)` call (lines 66-80) to add `excludeTitles: parsed.excludeTitles` to the params object. Leave the `listTopCompanies(...)` branch alone — see the MVP scope note below.

**MVP scope note for the `listTopCompanies` branch:** `listTopCompanies` is hit when the user has no keyword query. Since the Phase 1 use case is "filter while searching," exclusions are intentionally NOT applied to the top-companies default view in this MVP. If we wanted them there too, we would need to thread `excludeTitles` into both the `listTopCompanies` server action and the provider method (mirroring Task 2's changes). That is explicitly deferred.

- [ ] **Step 7b: Update `ExploreContent` to pass `initialExcludeTitles`**

Edit `apps/web/app/[lang]/(app)/explore/explore-content.tsx`. Find the `<SearchPage …/>` render (grep `initialKeywords={parsed.keywords}`) and add a sibling prop:

```tsx
initialExcludeTitles={parsed.excludeTitles}
```

`parsed` comes from `fetchExploreData` (which we just updated in step 7), so this prop is now populated automatically — no further wrapper changes needed.

- [ ] **Step 8: Type-check**

Run: `cd apps/web && pnpm tsc --noEmit`
Expected: PASS. If it fails, the error message will point to a callsite you missed (likely a `searchJobs` or `listTopCompanies` call in `search-page.tsx`). Add `excludeTitles: excludeTitlesRef.current` to that call.

- [ ] **Step 9: Run tests**

Run: `cd apps/web && pnpm test`
Expected: PASS — no existing tests regress; Tasks 1/2/4 tests stay green.

- [ ] **Step 10: Commit**

```bash
git add apps/web/app/[lang]/\(app\)/explore/search-page.tsx \
        apps/web/app/[lang]/\(app\)/explore/explore-content.tsx \
        apps/web/src/components/SearchStateProvider.tsx \
        apps/web/src/lib/actions/explore-data.ts
git commit -m "feat(explore): wire excludeTitles state + URL sync into SearchPage"
```

---

## Task 6: Render `ExcludeTitlePills` in `SearchToolbar` + manual UI smoke test

**Files:**
- Modify: `apps/web/src/components/search/search-toolbar.tsx`
- Modify: `apps/web/src/components/search/advanced-search-panel.tsx` (only if exclusions belong inside the panel — verify by reading the panel first; otherwise keep exclusions in the toolbar top-level)

- [ ] **Step 1: Add `ExcludeTitlePills` to `SearchToolbar` props + render**

Edit `apps/web/src/components/search/search-toolbar.tsx`. In the `SearchToolbarProps` interface add:

```ts
excludeTitles: string[];
onAddExcludeTitle: (keyword: string) => void;
onRemoveExcludeTitle: (keyword: string) => void;
```

Inside the component, import `ExcludeTitlePills`:

```ts
import { ExcludeTitlePills } from "@/components/search/exclude-title-pills";
```

Find the existing chip-row section (the `{hasFilters && (<div …>…)}` block around line 154). Update `hasFilters` to also consider exclusions:

```ts
const hasFilters =
  keywords.length > 0 ||
  excludeTitles.length > 0 ||
  locations.length > 0 ||
  occupations.length > 0 ||
  seniorities.length > 0 ||
  (technologies?.length ?? 0) > 0 ||
  salaryMin != null ||
  salaryMax != null ||
  experienceMin != null ||
  experienceMax != null;
```

Above the existing `<AdvancedSearchPanel …/>` render, add a new section:

```tsx
<div className="space-y-2">
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

Make sure `Trans` is imported from `@lingui/react/macro` at the top of the file — if it's not already imported, add it.

- [ ] **Step 2: Pass the new props from `SearchPage` to `SearchToolbar`**

Edit `apps/web/app/[lang]/(app)/explore/search-page.tsx`. Find where `<SearchToolbar …/>` is rendered (search `SearchToolbar`). Add:

```tsx
excludeTitles={excludeTitles}
onAddExcludeTitle={handleAddExcludeTitle}
onRemoveExcludeTitle={handleRemoveExcludeTitle}
```

- [ ] **Step 3: Type-check**

Run: `cd apps/web && pnpm tsc --noEmit`
Expected: PASS.

- [ ] **Step 4: Extract Lingui strings**

Run: `cd apps/web && pnpm extract`
Expected: Output mentions new strings `search.excludeTitles.addPlaceholder`, `search.excludeTitles.remove`, `search.excludeTitles.label`. These get added to `.po` files. No need to translate — Lingui falls back to the `message:` default.

- [ ] **Step 5: Run the full test suite**

Run: `cd apps/web && pnpm test`
Expected: PASS.

- [ ] **Step 6: Manual smoke test on the dev server**

Start Typesense + Postgres locally per your dev setup, then:

```bash
cd apps/web && pnpm dev
```

In the browser at `http://localhost:3000/en/explore`:

1. Verify the "Hide jobs with these words in the title" section appears above the filter panel.
2. Type `senior` and press Enter. A pill with an eye-off icon should appear and URL should update to include `?exclude=senior`.
3. Results should visibly change — titles starting with "Senior" should no longer appear.
4. Copy the URL, open in a new tab — the filter should load from URL with the pill already present.
5. Remove the pill via × — URL `exclude` param is gone and results include "Senior" titles again.
6. Add multiple keywords (`senior`, `staff`, `principal`) — all three pills render and URL contains `?exclude=senior,staff,principal`.
7. Click the existing "Save as watchlist" button. Inspect the resulting row in the DB (`select filters from watchlist where slug='...';`) — the `filters` JSONB contains `"excludeTitles": ["senior","staff","principal"]`.

If any step fails, stop and debug before continuing.

- [ ] **Step 7: Commit**

```bash
git add apps/web/src/components/search/search-toolbar.tsx \
        apps/web/app/[lang]/\(app\)/explore/search-page.tsx \
        apps/web/src/locales/
git commit -m "feat(explore): render ExcludeTitlePills in toolbar + active-chip integration"
```

---

## Final Review

After all 6 tasks commit cleanly, run a full check:

```bash
cd apps/web
pnpm tsc --noEmit         # type-check
pnpm test                 # unit + component tests
pnpm build                # production build (runs lingui compile + next build)
```

If everything is green, request a final code review per `CLAUDE.md` conventions (inline-execution final review, no per-task reviews since this is ≤6 tasks).

## Spec Traceability

| Spec section | Covered by |
|---|---|
| §Data Model — `WatchlistFilters.excludeTitles` | Task 1 step 4 |
| §Data Model — `SearchFilters.excludeTitles` | Task 1 step 5 |
| §Data Model — rules (word boundary, ≤50, case-insensitive dedupe, multi-word) | Task 1 (helpers + tests) |
| §URL State — `?exclude=...` param | Task 1, Task 3, Task 5 |
| §Components — exclusion input | Task 4, Task 6 |
| §Components — active-filter chips | Task 4 (chips are inside the component) |
| §Components — result-count disclosure | Deferred — non-blocking UX polish; copy can be added in a follow-up by editing the existing count label in `search-results.tsx` or `search-toolbar.tsx` |
| §Data Flow — over-fetch 1.5× | Task 2 |
| §Data Flow — regex build with word boundary | Task 1 + Task 2 |
| §Error Handling — escapeRegex | Task 1 |
| §Error Handling — empty/whitespace stripping | Task 1 (`parseExcludeParam`) |
| §Error Handling — 50-keyword cap | Task 1 (`MAX_EXCLUDE_TITLES`) |
| §Save-as-Watchlist Compatibility | Task 1 step 4 (type extension makes it free) |
| §Testing — unit | Tasks 1, 2, 4 (Vitest suites) |
| §Testing — integration/Playwright | Task 6 manual smoke test; full Playwright suite deferred |
| §Deferred — watchlist editor UI, per-user defaults, "For You" page, alert-filters.yaml unification | Not in this plan — intentionally |
