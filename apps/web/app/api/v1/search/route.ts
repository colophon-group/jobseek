import { type NextRequest, NextResponse } from "next/server";
import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { parseSearchFilters } from "@/lib/actions/search-input";
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

  const parsed = await parseSearchFilters({ q, loc, occ, sen, tech, wm, locale });

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

  let salaryMinEur: number | undefined;
  let salaryMaxEur: number | undefined;
  if (sal) {
    const [minStr, maxStr] = sal.split("-");
    salaryMinEur = minStr ? parseInt(minStr, 10) : undefined;
    salaryMaxEur = maxStr ? parseInt(maxStr, 10) : undefined;
  }

  let experienceMin: number | undefined;
  let experienceMax: number | undefined;
  if (exp) {
    const [minStr, maxStr] = exp.split("-");
    experienceMin = minStr ? parseInt(minStr, 10) : undefined;
    experienceMax = maxStr ? parseInt(maxStr, 10) : undefined;
  }

  const searchParams = {
    locationIds,
    occupationIds,
    seniorityIds,
    technologyIds,
    workMode: parsed.workMode.length > 0 ? parsed.workMode : undefined,
    salaryMinEur,
    salaryMaxEur,
    experienceMin,
    experienceMax,
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
