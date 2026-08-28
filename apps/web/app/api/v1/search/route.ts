import { type NextRequest, NextResponse } from "next/server";
// Public REST routes import the plain service tier (`@/lib/services/*`)
// rather than the `"use server"` action modules (`@/lib/actions/*`). The
// service functions are functionally identical but avoid the
// server-action machinery (per-call RPC URL, serialization boundary,
// security IDs). See issue #3231 — public REST and internal RPC are now
// two distinct surfaces.
import { searchJobs, listTopCompanies } from "@/lib/services/search";
import { parseSearchFilters } from "@/lib/services/search-input";
import { parsePublicSearchLanguages } from "@/lib/search/language-param";
import { logExternalError } from "@/lib/safe-external-error";
import { withPublicApiObservability } from "@/lib/public-api-observability";
import { PUBLIC_SEARCH_QUERY_PARAMETERS } from "@jseek/mcp-server/public-api-contract";
import {
  checkRateLimit,
  apiResponse,
  PUBLIC_EMPLOYMENT_TYPE_VALUES,
  PUBLIC_WORK_MODE_VALUES,
  parseApiLocale,
  siteUrl,
  exploreUrl,
  type RateLimitInfo,
  validatePublicEnumListParam,
  validateResolvedPublicFilters,
} from "../_shared";

const MAX_COMPANIES = 5;
const MAX_POSTINGS_PER_COMPANY = 3;

function searchErrorResponse(
  error: string,
  status: 400 | 500,
  rateLimit: RateLimitInfo | null,
) {
  const response = apiResponse({ error }, { maxAge: 0, rateLimit, status });
  // Search errors must never be stored by a browser or shared cache. This is
  // stricter than merely setting max-age=0 and keeps a transient provider
  // outage (or a malformed request) from becoming a cached API response.
  response.headers.set("Cache-Control", "no-store");
  return response;
}

function parseIntegerRangeParam(
  name: "sal" | "exp",
  raw: string | undefined,
): {
  ok: true;
  min: number | undefined;
  max: number | undefined;
} | {
  ok: false;
  error: string;
} {
  if (raw === undefined) return { ok: true, min: undefined, max: undefined };

  const parts = raw.split("-");
  if (parts.length !== 2) {
    return { ok: false, error: `Invalid '${name}' param: expected min-max` };
  }

  const parseBound = (value: string): number | undefined => {
    const trimmed = value.trim();
    if (trimmed === "") return undefined;
    if (!/^\d+$/.test(trimmed)) return Number.NaN;
    const parsed = Number.parseInt(trimmed, 10);
    return Number.isSafeInteger(parsed) ? parsed : Number.NaN;
  };

  const min = parseBound(parts[0] ?? "");
  const max = parseBound(parts[1] ?? "");
  if (Number.isNaN(min) || Number.isNaN(max)) {
    return {
      ok: false,
      error: `Invalid '${name}' param: bounds must be non-negative integers`,
    };
  }

  if (min === undefined && max === undefined) {
    return {
      ok: false,
      error: `Invalid '${name}' param: at least one bound is required`,
    };
  }

  if (min !== undefined && max !== undefined && min > max) {
    return {
      ok: false,
      error: `Invalid '${name}' param: min cannot be greater than max`,
    };
  }

  return { ok: true, min, max };
}

async function handleGet(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const rawParams = Object.fromEntries(
    PUBLIC_SEARCH_QUERY_PARAMETERS.map((name) => [
      name,
      sp.get(name) ?? undefined,
    ]),
  ) as Record<(typeof PUBLIC_SEARCH_QUERY_PARAMETERS)[number], string | undefined>;
  const { q, loc, occ, sen, tech, wm, etype, sal, exp, lang } = rawParams;

  const locale = parseApiLocale(sp, rl);
  if (locale instanceof NextResponse) return locale;

  for (const [name, raw, supported] of [
    ["wm", wm, PUBLIC_WORK_MODE_VALUES],
    ["etype", etype, PUBLIC_EMPLOYMENT_TYPE_VALUES],
  ] as const) {
    const invalid = validatePublicEnumListParam(name, raw, supported, rl);
    if (invalid) return invalid;
  }

  const langParsed = parsePublicSearchLanguages(lang);
  if (!langParsed.ok) {
    return searchErrorResponse(langParsed.error, 400, rl);
  }
  // `searchJobs` / `listTopCompanies` treat `languages: []` as "no
  // filter" (see `apps/web/src/lib/search/typesense-filters.ts` —
  // `filters.languages?.length` guards the locales clause).
  const languages = langParsed.languages ?? [];

  const salaryRange = parseIntegerRangeParam("sal", sal);
  if (!salaryRange.ok) {
    return searchErrorResponse(salaryRange.error, 400, rl);
  }

  const experienceRange = parseIntegerRangeParam("exp", exp);
  if (!experienceRange.ok) {
    return searchErrorResponse(experienceRange.error, 400, rl);
  }

  let parsed: Awaited<ReturnType<typeof parseSearchFilters>>;
  try {
    parsed = await parseSearchFilters({ q, loc, occ, sen, tech, wm, etype, locale });
  } catch (error) {
    logExternalError(
      "error",
      { service: "typesense", operation: "public_api_search" },
      error,
    );
    return searchErrorResponse("Search service unavailable", 500, rl);
  }
  const unresolved = validateResolvedPublicFilters(parsed, rl);
  if (unresolved) return unresolved;

  const locationIds =
    parsed.locations.length > 0 ? parsed.locations.map((l) => l.id) : undefined;
  const occupationIds =
    parsed.occupations.length > 0
      ? parsed.occupations.map((o) => o.id)
      : undefined;
  const seniorityIds =
    parsed.seniorities.length > 0
      ? parsed.seniorities.map((s) => s.id)
      : undefined;
  const technologyIds =
    parsed.technologies.length > 0
      ? parsed.technologies.map((t) => t.id)
      : undefined;

  const searchParams = {
    locationIds,
    occupationIds,
    seniorityIds,
    technologyIds,
    workMode: parsed.workMode.length > 0 ? parsed.workMode : undefined,
    employmentTypes:
      parsed.employmentTypes.length > 0 ? parsed.employmentTypes : undefined,
    salaryMinEur: salaryRange.min,
    salaryMaxEur: salaryRange.max,
    experienceMin: experienceRange.min,
    experienceMax: experienceRange.max,
    languages,
    locale,
    offset: 0,
    limit: MAX_COMPANIES,
  };

  let result: Awaited<ReturnType<typeof listTopCompanies>>;
  try {
    result =
      parsed.keywords.length > 0
        ? await searchJobs({ keywords: parsed.keywords, ...searchParams })
        : await listTopCompanies(searchParams);
  } catch (error) {
    logExternalError(
      "error",
      { service: "typesense", operation: "public_api_search" },
      error,
    );
    return searchErrorResponse("Search service unavailable", 500, rl);
  }

  const companies = result.companies.slice(0, MAX_COMPANIES).map((c) => ({
    name: c.company.name,
    slug: c.company.slug,
    icon: c.company.icon,
    url: siteUrl(`/${locale}/company/${c.company.slug}`),
    activeJobs: c.activeMatches,
    topPostings: c.postings.slice(0, MAX_POSTINGS_PER_COMPANY).map((p) => ({
      id: p.id,
      title: p.title,
      location: p.locations.map((l) => l.name).join(", ") || null,
      url: siteUrl(
        `/${locale}/company/${c.company.slug}?show=${p.id}`,
      ),
    })),
  }));

  return apiResponse(
    {
      companies,
      totalCompanies: result.totalCompanies,
      moreAt: exploreUrl(sp, locale),
    },
    { rateLimit: rl },
  );
}

export const GET = withPublicApiObservability("search", handleGet);
