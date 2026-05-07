import { timingSafeEqual } from "node:crypto";

import { NextResponse, type NextRequest } from "next/server";

import { invalidatePattern } from "@/lib/cache";

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

  const deleted: Record<string, number> = {};
  for (const prefix of TYPEAHEAD_PREFIXES) {
    deleted[prefix] = await invalidatePattern(prefix);
  }
  const total = Object.values(deleted).reduce((a, b) => a + b, 0);
  return NextResponse.json({ ok: true, deleted, total });
}
