import { NextResponse, type NextRequest } from "next/server";

import { invalidatePattern } from "@/lib/cache";

export const runtime = "nodejs";

// Closed list — every cache key prefix that would go stale after a
// `crawler sync` taxonomy mutation. Defined here (not in the caller) so
// the crawler can't accidentally request a sweep of an unrelated namespace.
// Mirrors the keys built in apps/web/src/lib/actions/{locations,taxonomy,company}.ts.
const TYPEAHEAD_PREFIXES = [
  "loc-suggest:",
  "occ-suggest:",
  "sen-suggest:",
  "tech-suggest:",
  "company-suggest:",
] as const;

/**
 * POST /api/internal/invalidate-typeahead
 *
 * Called by the crawler after `sync_typesense` to drop stale typeahead
 * suggestions across all locales. Authenticated via a bearer token shared
 * out-of-band (`INTERNAL_REVALIDATE_TOKEN` env var on both sides).
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
  if (presented !== `Bearer ${expected}`) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const deleted: Record<string, number> = {};
  for (const prefix of TYPEAHEAD_PREFIXES) {
    deleted[prefix] = await invalidatePattern(prefix);
  }
  const total = Object.values(deleted).reduce((a, b) => a + b, 0);
  return NextResponse.json({ ok: true, deleted, total });
}
