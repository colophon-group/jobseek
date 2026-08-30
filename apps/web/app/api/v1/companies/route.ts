import { type NextRequest, NextResponse } from "next/server";
// Public REST routes import the plain service tier (`@/lib/services/*`)
// rather than the `"use server"` action modules (`@/lib/actions/*`). See
// issues #3231 / #3331.
import { suggestCompanies } from "@/lib/services/company";
import { CACHE_TTL_LONG } from "@/lib/cache-ttl";
import { withPublicApiObservability } from "@/lib/public-api-observability";
import {
  checkRateLimit,
  apiResponse,
  apiProviderUnavailableResponse,
  sharedApiResponse,
  parseApiLocale,
  siteUrl,
} from "../_shared";

const MAX_RESULTS = 10;

async function handleGet(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const q = sp.get("q");
  const locale = parseApiLocale(sp, rl);
  if (locale instanceof NextResponse) return locale;

  if (!q) {
    return apiResponse(
      { error: "Missing required 'q' param (company name query)" },
      { status: 400 },
    );
  }

  let results: Awaited<ReturnType<typeof suggestCompanies>>;
  try {
    results = await suggestCompanies({
      query: q,
      failOnUnavailable: true,
    });
  } catch (error) {
    return apiProviderUnavailableResponse(
      "public_api_companies",
      rl,
      error,
    );
  }

  const companies = results.slice(0, MAX_RESULTS).map((c) => ({
    name: c.name,
    slug: c.slug,
    icon: c.icon,
    url: siteUrl(`/${locale}/company/${c.slug}`),
  }));

  // Autocomplete suggestions are very stable (a single new company per few
  // days, slug shape never changes). Bumped from the 300s default to 1h
  // for higher CDN reuse on common queries — see issue #2644.
  return sharedApiResponse({ companies }, { maxAge: CACHE_TTL_LONG });
}

export const GET = withPublicApiObservability("companies", handleGet);
