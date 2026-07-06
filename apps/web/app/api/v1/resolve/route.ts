import { type NextRequest, NextResponse } from "next/server";
// Public REST routes import the plain service tier (`@/lib/services/*`)
// rather than the `"use server"` action modules (`@/lib/actions/*`). The
// service functions are functionally identical but avoid the
// server-action machinery (per-call RPC URL, serialization boundary,
// security IDs). See issues #3231 / #3329 / #3331.
import { suggestLocations } from "@/lib/services/locations";
import {
  suggestOccupations,
  suggestSeniorities,
  suggestTechnologies,
} from "@/lib/services/taxonomy";
import { suggestIndustries } from "@/lib/services/company";
import { CACHE_TTL_LONG } from "@/lib/cache-ttl";
import { slugifyTitle } from "@/lib/watchlist-slug";
import { checkRateLimit, apiResponse } from "../_shared";

const VALID_TYPES = [
  "locations",
  "occupations",
  "seniority",
  "technologies",
  "industries",
] as const;

export async function GET(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const type = sp.get("type") as (typeof VALID_TYPES)[number] | null;
  const q = sp.get("q");
  const locale = sp.get("locale") ?? "en";

  if (!type || !VALID_TYPES.includes(type)) {
    return apiResponse(
      {
        error: `Missing or invalid 'type' param. Valid: ${VALID_TYPES.join(", ")}`,
      },
      { maxAge: 0, status: 400 },
    );
  }

  if (!q || q.trim().length < 2) {
    return apiResponse(
      { error: "Missing or too short 'q' param (min 2 chars)" },
      { maxAge: 0, status: 400 },
    );
  }

  let matches: { slug: string; name: string; type?: string; parentName?: string | null }[];

  switch (type) {
    case "locations": {
      const data = await suggestLocations({ query: q, locale });
      matches = data.map((l) => ({
        slug: l.slug,
        name: l.name,
        type: l.type,
        parentName: l.parentName,
      }));
      break;
    }
    case "occupations": {
      const data = await suggestOccupations({ query: q, locale });
      matches = data.map((o) => ({ slug: o.slug, name: o.name }));
      break;
    }
    case "seniority": {
      const data = await suggestSeniorities({ query: q, locale });
      matches = data.map((s) => ({ slug: s.slug, name: s.name }));
      break;
    }
    case "technologies": {
      const data = await suggestTechnologies({ query: q, locale });
      matches = data.map((t) => ({ slug: t.slug, name: t.name }));
      break;
    }
    case "industries": {
      // The `industry` table has no `slug` column today, but the response
      // contract is uniform across taxonomies: callers expect `slug` to
      // be a URL-stable slug-shaped string. Derive one from the localized
      // display name with the same canonical slugifier the rest of the
      // app uses, falling back to the numeric id if the name slugifies
      // to empty (e.g., all-symbol pathological input). See issue #3228.
      // Note: `/api/v1/search` does not currently accept an industry
      // filter, so this is a response-shape fix; no roundtrip breakage.
      const data = await suggestIndustries({ query: q, locale });
      matches = data.map((i) => ({
        slug: slugifyTitle(i.name) || String(i.id),
        name: i.name,
      }));
      break;
    }
  }

  // Resolve responses (taxonomy/location autocomplete) are stable — the
  // taxonomy collections change on a daily-deploy cadence at most. Bumped
  // from the 300s default to 1h for higher CDN reuse on common queries.
  // See issue #2644 + alignment with /api/v1/taxonomies which is already 1h.
  return apiResponse(
    {
      type,
      query: q,
      matches: matches.slice(0, 10),
    },
    { maxAge: CACHE_TTL_LONG, rateLimit: rl },
  );
}
