import { type NextRequest, NextResponse } from "next/server";
// Public REST routes import the plain service tier (`@/lib/services/*`)
// rather than the `"use server"` action modules (`@/lib/actions/*`). The
// service functions are functionally identical but avoid the
// server-action machinery (per-call RPC URL, serialization boundary,
// security IDs). See issue #3231 — public REST and internal RPC are now
// two distinct surfaces.
import { searchJobs, listTopCompanies } from "@/lib/services/search";
import { parseSearchFilters } from "@/lib/services/search-input";
import { isLocale, locales } from "@/lib/i18n";
import { checkRateLimit, apiResponse, siteUrl, exploreUrl } from "../_shared";

const MAX_COMPANIES = 5;
const MAX_POSTINGS_PER_COMPANY = 3;

/**
 * Parse the optional `lang=` query param into a validated list of job
 * document language codes. Distinct from the UI ``locale`` (i18n labels
 * + currency formatting) — ``lang`` filters by the language the posting
 * itself is written in (`job_posting.locales` in Typesense).
 *
 * - absent / empty → returns ``null`` (caller should pass ``[]`` to
 *   ``searchJobs`` / ``listTopCompanies`` so no language filter is
 *   applied — this is a public REST API, callers are stateless and
 *   should not be biased by the UI locale)
 * - comma-separated codes (e.g. ``de`` or ``de,fr``) → returns the
 *   validated subset. Unknown codes cause a ``400``.
 *
 * Validated against the same set of locales the UI supports
 * (`apps/web/src/lib/i18n.ts` :data:`locales`).
 */
function parseLangParam(raw: string | null): {
  ok: true;
  langs: string[] | null;
} | {
  ok: false;
  error: string;
} {
  if (raw === null) return { ok: true, langs: null };
  const parts = raw
    .split(",")
    .map((s) => s.trim())
    .filter(Boolean);
  if (parts.length === 0) {
    return {
      ok: false,
      error: `Invalid 'lang' param: must be a comma-separated list of language codes (${locales.join(", ")})`,
    };
  }
  const invalid = parts.filter((c) => !isLocale(c));
  if (invalid.length > 0) {
    return {
      ok: false,
      error: `Invalid 'lang' value(s): ${invalid.join(", ")}. Supported: ${locales.join(", ")}`,
    };
  }
  // Dedupe preserving the validated form
  return { ok: true, langs: Array.from(new Set(parts)) };
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
  if (!raw) return { ok: true, min: undefined, max: undefined };

  const parts = raw.split("-");
  if (parts.length > 2) {
    return { ok: false, error: `Invalid '${name}' param: expected min-max` };
  }

  const parseBound = (value: string): number | undefined => {
    const trimmed = value.trim();
    if (trimmed === "") return undefined;
    if (!/^\d+$/.test(trimmed)) return Number.NaN;
    return Number.parseInt(trimmed, 10);
  };

  const min = parseBound(parts[0] ?? "");
  const max = parseBound(parts[1] ?? "");
  if (Number.isNaN(min) || Number.isNaN(max)) {
    return {
      ok: false,
      error: `Invalid '${name}' param: bounds must be positive integers`,
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

export async function GET(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const q = sp.get("q") ?? undefined;
  const loc = sp.get("loc") ?? undefined;
  const occ = sp.get("occ") ?? undefined;
  const sen = sp.get("sen") ?? undefined;
  const tech = sp.get("tech") ?? undefined;
  const wm = sp.get("wm") ?? undefined;
  const etype = sp.get("etype") ?? undefined;
  const sal = sp.get("sal") ?? undefined;
  const exp = sp.get("exp") ?? undefined;
  const locale = sp.get("locale") ?? "en";

  const langParsed = parseLangParam(sp.get("lang"));
  if (!langParsed.ok) {
    return apiResponse({ error: langParsed.error }, { maxAge: 0 });
  }
  // `searchJobs` / `listTopCompanies` treat `languages: []` as "no
  // filter" (see `apps/web/src/lib/search/typesense-filters.ts` —
  // `filters.languages?.length` guards the locales clause).
  const languages = langParsed.langs ?? [];

  const parsed = await parseSearchFilters({ q, loc, occ, sen, tech, wm, etype, locale });

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

  const salaryRange = parseIntegerRangeParam("sal", sal);
  if (!salaryRange.ok) {
    return apiResponse({ error: salaryRange.error }, { maxAge: 0 });
  }

  const experienceRange = parseIntegerRangeParam("exp", exp);
  if (!experienceRange.ok) {
    return apiResponse({ error: experienceRange.error }, { maxAge: 0 });
  }

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

  const result =
    parsed.keywords.length > 0
      ? await searchJobs({ keywords: parsed.keywords, ...searchParams })
      : await listTopCompanies(searchParams);

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
