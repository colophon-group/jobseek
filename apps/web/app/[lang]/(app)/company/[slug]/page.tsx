import { notFound } from "next/navigation";
import { headers } from "next/headers";
import type { Metadata } from "next";
import { initI18nForPage } from "@/lib/i18n";
import { getCompanyBySlug, getCompanyPostings, getCompanyTopLocations } from "@/lib/actions/company";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { CompanyPage } from "./company-page";
import type { FilterItem } from "@/components/search/filter-bar";

const PAGE_SIZE = 20;

type Props = {
  params: Promise<{ lang: string; slug: string }>;
  searchParams: Promise<{ q?: string; loc?: string; show?: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { slug, lang } = await params;
  const company = await getCompanyBySlug(slug, lang);
  if (!company) return {};
  return {
    title: `Jobs at ${company.name}`,
    description: company.description ?? `Browse open positions at ${company.name}`,
  };
}

async function parseFilters(
  q: string | undefined,
  loc: string | undefined,
  locale: string,
  userLat?: number,
  userLng?: number,
): Promise<FilterItem[]> {
  const parsed = await parseSearchFilters({ q, loc, locale, userLat, userLng });
  const filters: FilterItem[] = [];
  for (const location of parsed.locations) {
    filters.push({ kind: "location", id: location.id, slug: location.slug, name: location.name, type: location.type });
  }
  for (const keyword of parsed.keywords) {
    filters.push({ kind: "keyword", value: keyword });
  }
  return filters;
}

export default async function CompanyPageRoute({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const { slug } = await params;
  const { q, loc, show } = await searchParams;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) notFound();

  const h = await headers();
  const userLat = parseFloat(h.get("x-vercel-ip-latitude") ?? "");
  const userLng = parseFloat(h.get("x-vercel-ip-longitude") ?? "");
  const parsedUserLat = Number.isFinite(userLat) ? userLat : undefined;
  const parsedUserLng = Number.isFinite(userLng) ? userLng : undefined;
  const initialFilters = await parseFilters(q, loc, locale, parsedUserLat, parsedUserLng);
  const keywords = initialFilters.filter((f) => f.kind === "keyword").map((f) => f.value);
  const locationIds = initialFilters.filter((f) => f.kind === "location").map((f) => f.id);

  const [postingsResult, topLocationsResult] = await Promise.all([
    getCompanyPostings({
      companyId: company.id,
      keywords,
      locationIds: locationIds.length > 0 ? locationIds : undefined,
      language: locale,
      offset: 0,
      limit: PAGE_SIZE,
    }),
    getCompanyTopLocations(company.id, locale),
  ]);
  const topLocations = topLocationsResult.locations;
  const totalLocationCount = topLocationsResult.totalCount;

  return (
    <CompanyPage
      company={company}
      initialPostings={postingsResult.postings}
      initialActiveCount={postingsResult.activeCount}
      initialYearCount={postingsResult.yearCount}
      initialFilters={initialFilters}
      initialShowPostingId={show ?? null}
      topLocations={topLocations}
      totalLocationCount={totalLocationCount}
      language={locale}
      userLat={parsedUserLat}
      userLng={parsedUserLng}
    />
  );
}
