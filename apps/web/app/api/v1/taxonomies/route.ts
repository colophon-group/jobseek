import { type NextRequest, NextResponse } from "next/server";
// Public REST routes import the plain service tier (`@/lib/services/*`)
// rather than the `"use server"` action modules (`@/lib/actions/*`). The
// service functions are functionally identical but avoid the
// server-action machinery (per-call RPC URL, serialization boundary,
// security IDs). See issues #3231 / #3329 / #3331.
import {
  getAllSeniorities,
  getAllOccupationsGrouped,
  getAllTechnologiesGrouped,
} from "@/lib/services/taxonomy";
import { suggestIndustries } from "@/lib/services/company";
import { CACHE_TTL_LONG } from "@/lib/cache-ttl";
import { withPublicApiObservability } from "@/lib/public-api-observability";
import {
  checkRateLimit,
  apiResponse,
  apiProviderUnavailableResponse,
  sharedApiResponse,
  parseApiLocale,
} from "../_shared";

const VALID_TYPES = ["seniority", "occupations", "technologies", "industries"] as const;

async function handleGet(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const type = sp.get("type") as (typeof VALID_TYPES)[number] | null;
  const locale = parseApiLocale(sp, rl);
  if (locale instanceof NextResponse) return locale;

  if (!type || !VALID_TYPES.includes(type)) {
    return apiResponse(
      { error: `Missing or invalid 'type' param. Valid: ${VALID_TYPES.join(", ")}` },
      { status: 400 },
    );
  }

  let items: unknown;

  try {
    switch (type) {
      case "seniority": {
        const data = await getAllSeniorities(locale, undefined, {
          failOnUnavailable: true,
        });
        items = data.map((s) => ({ slug: s.slug, name: s.name }));
        break;
      }
      case "occupations": {
        const data = await getAllOccupationsGrouped(locale, undefined, {
          failOnUnavailable: true,
        });
        items = data;
        break;
      }
      case "technologies": {
        const data = await getAllTechnologiesGrouped(undefined, {
          failOnUnavailable: true,
        });
        items = data;
        break;
      }
      case "industries": {
        const data = await suggestIndustries({
          query: "",
          locale,
          failOnUnavailable: true,
        });
        items = data.map((i) => ({ id: i.id, name: i.name }));
        break;
      }
    }
  } catch (error) {
    return apiProviderUnavailableResponse(
      "public_api_taxonomies",
      rl,
      error,
    );
  }

  return sharedApiResponse({ type, items }, { maxAge: CACHE_TTL_LONG });
}

export const GET = withPublicApiObservability("taxonomies", handleGet);
