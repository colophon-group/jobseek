"use server";

import {
  getWatchlistByUserAndSlug,
  getWatchlistPostings,
  getWatchlistPostingYearCount,
} from "@/lib/actions/watchlists";
import { getCurrencyRates } from "@/lib/actions/search";
import { getSession } from "@/lib/sessionCache";
import { getUserPlan, PLAN_LIMITS, canCreateWatchlist } from "@/lib/plans";
import { resolveLocationSlugs } from "@/lib/actions/locations";
import { resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "@/lib/actions/taxonomy";
import { getPreferences } from "@/lib/actions/preferences";
import { readAnonJobLanguagesCookie } from "@/lib/anon-preferences";
import { resolveJobLanguages } from "@/lib/job-languages";
import { convertToEur } from "@/lib/salary";
import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";

export interface WatchlistPageData {
  detail: NonNullable<Awaited<ReturnType<typeof getWatchlistByUserAndSlug>>>;
  isOwner: boolean;
  isPaidPlan: boolean;
  limitReached: boolean;
  postings: WatchlistPostingEntry[];
  total: number;
  /** Count of postings first seen in the last year matching the same filters (active or inactive). */
  yearTotal: number;
  resolvedLocations: { id: number; slug: string; name: string; type: "macro" | "country" | "region" | "city"; parentName: string | null }[];
  resolvedOccupations: { id: number; slug: string; name: string }[];
  resolvedSeniorities: { id: number; slug: string; name: string }[];
  resolvedTechnologies: { id: number; slug: string; name: string }[];
  jobLanguages: string[];
  languages: string[];
}

export async function fetchWatchlistPageData(params: {
  userSlug: string;
  watchlistSlug: string;
  locale: string;
}): Promise<WatchlistPageData | null> {
  const { userSlug, watchlistSlug, locale } = params;

  const detail = await getWatchlistByUserAndSlug(userSlug, watchlistSlug);
  if (!detail) return null;

  const session = await getSession();
  const isOwner = session?.user?.id === detail.owner.id;

  // Auth users persist `jobLanguages` in `user_preferences`; anon
  // users mirror it into a cookie (issue #2850 + `anon-preferences.ts`).
  // Read whichever applies so the watchlist body filters consistently.
  const [plan, limit, prefs, anonJobLangs] = await Promise.all([
    session ? getUserPlan(session.user.id) : ("free" as const),
    session ? canCreateWatchlist(session.user.id) : { allowed: false, current: 0, max: 0 },
    session ? getPreferences() : Promise.resolve(null),
    session ? Promise.resolve(null) : readAnonJobLanguagesCookie(),
  ]);
  const isPaidPlan = PLAN_LIMITS[plan].canReceiveAlerts;
  const limitReached = !limit.allowed;

  const jobLanguages = prefs?.jobLanguages ?? anonJobLangs ?? [];
  const languages = resolveJobLanguages(jobLanguages, locale);

  const filters = detail.filters;

  const [locMap, occMap, senMap, techMap] = await Promise.all([
    filters.locationSlugs?.length
      ? resolveLocationSlugs(filters.locationSlugs, locale)
      : Promise.resolve(new Map()),
    filters.occupationSlugs?.length
      ? resolveOccupationSlugs(filters.occupationSlugs, locale)
      : Promise.resolve(new Map()),
    filters.senioritySlugs?.length
      ? resolveSenioritySlugs(filters.senioritySlugs, locale)
      : Promise.resolve(new Map()),
    filters.technologySlugs?.length
      ? resolveTechnologySlugs(filters.technologySlugs)
      : Promise.resolve(new Map()),
  ]);

  const resolvedLocations = (filters.locationSlugs ?? [])
    .map((slug) => locMap.get(slug))
    .filter((l): l is NonNullable<typeof l> => l != null)
    .map((l) => ({ id: l.id, slug: l.slug, name: l.name, type: l.type as "macro" | "country" | "region" | "city", parentName: l.parentName ?? null }));

  const resolvedOccupations = (filters.occupationSlugs ?? [])
    .map((slug) => occMap.get(slug))
    .filter((o): o is NonNullable<typeof o> => o != null);

  const resolvedSeniorities = (filters.senioritySlugs ?? [])
    .map((slug) => senMap.get(slug))
    .filter((s): s is NonNullable<typeof s> => s != null);

  const resolvedTechnologies = (filters.technologySlugs ?? [])
    .map((slug) => techMap.get(slug))
    .filter((t): t is NonNullable<typeof t> => t != null);

  // Re-validate workMode strings before letting them flow into the
  // Typesense filter — JSONB column means we can't trust the shape.
  // Mirrors the same guard in the client-side editor (issue #3037).
  const WORK_MODE_VALUES = new Set(["onsite", "hybrid", "remote"] as const);
  const validatedWorkMode = (filters.workMode ?? []).filter(
    (m): m is "onsite" | "hybrid" | "remote" => WORK_MODE_VALUES.has(m),
  );
  // `getCurrencyRates` is cache-backed (`cacheLife("hours")`), so this is
  // cheap when the cache is warm. Only fetched when salary filters are set
  // to avoid unnecessary work on watchlists without salary constraints.
  // Full rationale in issue #3178 and PR #3298.
  const salaryCurrency = filters.salaryCurrency ?? "EUR";
  const rates =
    filters.salaryMin != null || filters.salaryMax != null
      ? await getCurrencyRates()
      : [];
  const salaryMinEur = convertToEur(filters.salaryMin, salaryCurrency, rates);
  const salaryMaxEur = convertToEur(filters.salaryMax, salaryCurrency, rates);

  const sharedCountsParams = {
    companyIds: filters.anyCompany ? [] : detail.companies.map((c) => c.id),
    anyCompany: filters.anyCompany,
    keywords: filters.keywords,
    locationIds: resolvedLocations.map((l) => l.id),
    occupationIds: resolvedOccupations.map((o) => o.id),
    seniorityIds: resolvedSeniorities.map((s) => s.id),
    technologyIds: resolvedTechnologies.map((t) => t.id),
    workMode: validatedWorkMode.length > 0 ? validatedWorkMode : undefined,
    employmentType: filters.employmentType?.length ? filters.employmentType : undefined,
    salaryMin: salaryMinEur,
    salaryMax: salaryMaxEur,
    experienceMin: filters.experienceMin,
    experienceMax: filters.experienceMax,
    languages,
  };
  const [{ postings, total }, yearTotal] = await Promise.all([
    getWatchlistPostings({ ...sharedCountsParams, offset: 0, limit: 20 }),
    getWatchlistPostingYearCount(sharedCountsParams),
  ]);

  return {
    detail,
    isOwner,
    isPaidPlan,
    limitReached,
    postings,
    total,
    yearTotal,
    resolvedLocations,
    resolvedOccupations,
    resolvedSeniorities,
    resolvedTechnologies,
    jobLanguages,
    languages,
  };
}
