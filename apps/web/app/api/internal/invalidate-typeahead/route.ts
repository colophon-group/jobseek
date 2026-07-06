import { timingSafeEqual } from "node:crypto";

import { revalidateTag } from "next/cache";
import { NextResponse, type NextRequest } from "next/server";

import { invalidatePattern } from "@/lib/cache";
import {
  companyCsvDataCacheTag,
  typeaheadCompaniesCacheTag,
  typeaheadLocationsCacheTag,
  typeaheadOccupationsCacheTag,
  typeaheadSenioritiesCacheTag,
  typeaheadTechnologiesCacheTag,
} from "@/lib/cache-tags";

function _safeBearerEqual(presented: string | null, expected: string): boolean {
  if (presented === null) return false;
  const a = Buffer.from(presented);
  const b = Buffer.from(`Bearer ${expected}`);
  // timingSafeEqual requires equal-length buffers; length mismatch
  // is itself a timing oracle, but that leaks only the expected length
  // (a public-ish constant), not any token bytes.
  if (a.length !== b.length) return false;
  return timingSafeEqual(a, b);
}

// Closed list — every cache key prefix that would go stale after a
// `crawler sync` mutation (taxonomy renames, company CSV edits). Defined
// here (not in the caller) so the crawler can't accidentally request a
// sweep of an unrelated namespace. Mirrors the keys built in
// apps/web/src/lib/actions/{locations,taxonomy,company}.ts.
//
// company-slug: + company-similar: were added in #2715 — a company
// rename / industry change otherwise leaves /company/<slug> stale up to
// the 10-minute company-slug TTL. The posting-derived caches
// (company-top-locs:, company-locs-grouped:, company-postings:) key off
// job_posting data, which a CSV sync doesn't touch, so they're left to
// their TTLs.
const TYPEAHEAD_PREFIXES = [
  "loc-suggest:",
  "occ-suggest:",
  "sen-suggest:",
  "tech-suggest:",
  "company-suggest:",
  "company-slug:",
  "company-similar:",
] as const;

// `'use cache'` tags for the 5 typeahead slots migrated in #2907 plus
// the CSV-driven per-company tag covering `getCompanyBySlug` and
// `getSimilarCompanies` (migrated in #2884 bucket 4). Redis-prefix
// sweep below no longer hits these — the migrated functions write to
// Next's per-region runtime cache, not Redis. `revalidateTag` evicts
// those slots; `invalidatePattern` is kept for any rollout-window
// stragglers and to drain old keys.
//
// The `companyCsvDataCacheTag()` slot replaces the legacy
// `company-slug:` + `company-similar:` Redis prefixes — both shared
// the same trigger (a CSV sync that changes a company row), and a
// single shared tag is simpler to fire than thousands of per-slug
// `revalidateTag(companyCacheTag(slug))` calls. The per-slug
// `companyCacheTag(slug)` is reserved for future targeted invalidation
// (e.g., a server action that mutates a single company).
const INVALIDATE_TAGS = [
  typeaheadLocationsCacheTag(),
  typeaheadOccupationsCacheTag(),
  typeaheadSenioritiesCacheTag(),
  typeaheadTechnologiesCacheTag(),
  typeaheadCompaniesCacheTag(),
  companyCsvDataCacheTag(),
] as const;

/**
 * POST /api/internal/invalidate-typeahead
 *
 * Called by the crawler after `sync_typesense` to drop stale typeahead
 * suggestions and company-detail caches across all locales (see the
 * `TYPEAHEAD_PREFIXES` list for the full scope — kept under the original
 * route name so the crawler caller doesn't need a URL change).
 * Authenticated via a bearer token shared out-of-band
 * (`INTERNAL_REVALIDATE_TOKEN` env var on both sides).
 *
 * Returns 200 on success with `{ ok: true, deleted: { <prefix>: <n> } }`.
 * Returns 401 if the bearer token is missing or wrong.
 * Returns 503 if `INTERNAL_REVALIDATE_TOKEN` is unset on the server.
 *
 * The route is `nodejs` runtime so the @upstash/redis SDK works without
 * having to thread fetch-API differences through the cache helper.
 */
export async function POST(req: NextRequest): Promise<NextResponse> {
  const expected = process.env.INTERNAL_REVALIDATE_TOKEN;
  if (!expected) {
    return NextResponse.json(
      { error: "INTERNAL_REVALIDATE_TOKEN unset" },
      { status: 503 },
    );
  }

  const presented = req.headers.get("authorization");
  if (!_safeBearerEqual(presented, expected)) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  // Evict the `'use cache'` slots for migrated typeaheads + CSV-driven
  // company data. Pass "max" (Next 16) so the tag invalidation does not
  // expire — the tag is fired once per `crawler sync` and the slot
  // must drop until the next call refills it.
  const revalidatedTags: string[] = [];
  for (const tag of INVALIDATE_TAGS) {
    try {
      revalidateTag(tag, "max");
      revalidatedTags.push(tag);
    } catch (err) {
      // A revalidation hiccup must not fail the sweep — the legacy
      // Redis prefix loop below is the backstop for any straggler keys,
      // and the slot's 3600s TTL is the ultimate backstop.
      console.warn(
        "[invalidate-typeahead] revalidateTag failed",
        tag,
        err,
      );
    }
  }

  // Legacy Redis prefix sweep — all listed prefixes are now migrated to
  // `'use cache'`. The sweep is kept as a backstop to drain stragglers
  // from any rollout window where a region might still hold old Redis
  // entries (and as a safety net if a future PR re-introduces a Redis
  // path under one of these prefixes). Each call resolves to 0 deletes
  // in the steady state.
  const deleted: Record<string, number> = {};
  for (const prefix of TYPEAHEAD_PREFIXES) {
    deleted[prefix] = await invalidatePattern(prefix);
  }
  const total = Object.values(deleted).reduce((a, b) => a + b, 0);
  return NextResponse.json({
    ok: true,
    deleted,
    total,
    revalidatedTags,
  });
}
