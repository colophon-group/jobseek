"use server";

import {
  getWatchlistByUserAndSlug,
  getWatchlistPostings,
  getWatchlistPostingYearCount,
} from "@/lib/actions/watchlists";
import { getSession } from "@/lib/sessionCache";
import { getUserPlan, PLAN_LIMITS, canCreateWatchlist } from "@/lib/plans";
import { resolveLocationSlugs } from "@/lib/actions/locations";
import { resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "@/lib/actions/taxonomy";
import { getPreferences } from "@/lib/actions/preferences";
import { getViewerLanguages } from "@/lib/viewer";
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

  const [plan, limit, prefs, languages] = await Promise.all([
    session ? getUserPlan(session.user.id) : ("free" as const),
    session ? canCreateWatchlist(session.user.id) : { allowed: false, current: 0, max: 0 },
    session ? getPreferences() : Promise.resolve(null),
    getViewerLanguages(locale),
  ]);
  const isPaidPlan = PLAN_LIMITS[plan].canReceiveAlerts;
  const limitReached = !limit.allowed;

  const jobLanguages = prefs?.jobLanguages ?? [];

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

  const sharedCountsParams = {
    companyIds: filters.anyCompany ? [] : detail.companies.map((c) => c.id),
    anyCompany: filters.anyCompany,
    keywords: filters.keywords,
    locationIds: resolvedLocations.map((l) => l.id),
    occupationIds: resolvedOccupations.map((o) => o.id),
    seniorityIds: resolvedSeniorities.map((s) => s.id),
    technologyIds: resolvedTechnologies.map((t) => t.id),
    salaryMin: filters.salaryMin,
    salaryMax: filters.salaryMax,
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
