import { notFound } from "next/navigation";
import type { Metadata } from "next";
import { initI18nForPage } from "@/lib/i18n";
import {
  getWatchlistByUserAndSlug,
  getWatchlistPostings,
} from "@/lib/actions/watchlists";
import { getSession } from "@/lib/sessionCache";
import { getUserPlan, PLAN_LIMITS, canCreateWatchlist } from "@/lib/plans";
import { resolveLocationSlugs } from "@/lib/actions/locations";
import { resolveOccupationSlugs, resolveSenioritySlugs, resolveTechnologySlugs } from "@/lib/actions/taxonomy";
import { WatchlistViewPage } from "./watchlist-view-page";

type Props = {
  params: Promise<{ lang: string; userSlug: string; watchlistSlug: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { userSlug, watchlistSlug } = await params;
  const detail = await getWatchlistByUserAndSlug(userSlug, watchlistSlug);
  if (!detail) return {};

  const ownerLabel = detail.owner.displayUsername ?? detail.owner.username ?? detail.owner.name;
  const title = `${detail.title} — @${ownerLabel}`;

  let description = detail.description;
  if (!description) {
    // Auto-generate from companies/filters
    const parts: string[] = [];
    if (detail.companies.length > 0) {
      const names = detail.companies.slice(0, 3).map((c) => c.name);
      if (detail.companies.length > 3) {
        names.push(`${detail.companies.length - 3} more`);
      }
      parts.push(`Jobs at ${names.join(", ")}`);
    }
    if (detail.filters.occupationSlugs?.length) {
      parts.push(detail.filters.occupationSlugs.map((s) => s.replace(/-/g, " ")).join(", "));
    }
    if (detail.filters.locationSlugs?.length) {
      parts.push(`in ${detail.filters.locationSlugs.slice(0, 2).map((s) => s.replace(/-/g, " ")).join(", ")}`);
    }
    description = parts.length > 0 ? parts.join(" · ") : `Job watchlist by @${ownerLabel}`;
  }

  return { title, description };
}

export default async function WatchlistRoute({ params }: Props) {
  const { lang, userSlug, watchlistSlug } = await params;
  await initI18nForPage(Promise.resolve({ lang }));

  const detail = await getWatchlistByUserAndSlug(userSlug, watchlistSlug);
  if (!detail) notFound();

  const session = await getSession();
  const isOwner = session?.user?.id === detail.owner.id;

  const [plan, limit] = await Promise.all([
    session ? getUserPlan(session.user.id) : ("free" as const),
    session ? canCreateWatchlist(session.user.id) : { allowed: false, current: 0, max: 0 },
  ]);
  const isPaidPlan = PLAN_LIMITS[plan].canReceiveAlerts;
  const limitReached = !limit.allowed;

  const filters = detail.filters;

  // Resolve filter slugs to display objects
  const [locMap, occMap, senMap, techMap] = await Promise.all([
    filters.locationSlugs?.length
      ? resolveLocationSlugs(filters.locationSlugs, lang)
      : Promise.resolve(new Map()),
    filters.occupationSlugs?.length
      ? resolveOccupationSlugs(filters.occupationSlugs, lang)
      : Promise.resolve(new Map()),
    filters.senioritySlugs?.length
      ? resolveSenioritySlugs(filters.senioritySlugs, lang)
      : Promise.resolve(new Map()),
    filters.technologySlugs?.length
      ? resolveTechnologySlugs(filters.technologySlugs)
      : Promise.resolve(new Map()),
  ]);

  const resolvedLocations = (filters.locationSlugs ?? [])
    .map((slug) => locMap.get(slug))
    .filter((l): l is NonNullable<typeof l> => l != null)
    .map((l) => ({ id: l.id, slug: l.slug, name: l.name, type: l.type as "macro" | "country" | "region" | "city", parentName: l.parentName }));

  const resolvedOccupations = (filters.occupationSlugs ?? [])
    .map((slug) => occMap.get(slug))
    .filter((o): o is NonNullable<typeof o> => o != null);

  const resolvedSeniorities = (filters.senioritySlugs ?? [])
    .map((slug) => senMap.get(slug))
    .filter((s): s is NonNullable<typeof s> => s != null);

  const resolvedTechnologies = (filters.technologySlugs ?? [])
    .map((slug) => techMap.get(slug))
    .filter((t): t is NonNullable<typeof t> => t != null);

  const { postings, total } = await getWatchlistPostings({
    companyIds: filters.anyCompany ? [] : detail.companies.map((c) => c.id),
    anyCompany: filters.anyCompany,
    offset: 0,
    limit: 20,
    keywords: filters.keywords,
    locationIds: resolvedLocations.map((l) => l.id),
    occupationIds: resolvedOccupations.map((o) => o.id),
    seniorityIds: resolvedSeniorities.map((s) => s.id),
    technologyIds: resolvedTechnologies.map((t) => t.id),
    salaryMin: filters.salaryMin,
    salaryMax: filters.salaryMax,
    experienceMin: filters.experienceMin,
    experienceMax: filters.experienceMax,
  });

  return (
    <WatchlistViewPage
      detail={detail}
      isOwner={isOwner}
      isPaidPlan={isPaidPlan}
      limitReached={limitReached}
      initialPostings={postings}
      initialTotal={total}
      locale={lang}
      resolvedLocations={resolvedLocations}
      resolvedOccupations={resolvedOccupations}
      resolvedSeniorities={resolvedSeniorities}
      resolvedTechnologies={resolvedTechnologies}
    />
  );
}
