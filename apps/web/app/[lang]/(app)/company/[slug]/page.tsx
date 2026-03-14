import { notFound } from "next/navigation";
import { headers } from "next/headers";
import type { Metadata } from "next";
import { initI18nForPage } from "@/lib/i18n";
import { getCompanyBySlug, getCompanyPostings, getCompanyTopLocations } from "@/lib/actions/company";
import { resolveLocationSlugs } from "@/lib/actions/locations";
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
): Promise<FilterItem[]> {
  const filters: FilterItem[] = [];
  if (loc) {
    const slugs = loc.split(",").map((s) => s.trim()).filter(Boolean);
    if (slugs.length > 0) {
      const resolved = await resolveLocationSlugs(slugs, locale);
      for (const slug of slugs) {
        const r = resolved.get(slug);
        if (r) {
          filters.push({ kind: "location", id: r.id, slug: r.slug, name: r.name, type: r.type });
        }
      }
    }
  }
  if (q) {
    for (const kw of q.split(",").map((s) => s.trim()).filter(Boolean)) {
      filters.push({ kind: "keyword", value: kw });
    }
  }
  return filters;
}

export default async function CompanyPageRoute({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const { slug } = await params;
  const { q, loc, show } = await searchParams;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) notFound();

  const initialFilters = await parseFilters(q, loc, locale);
  const keywords = initialFilters.filter((f) => f.kind === "keyword").map((f) => f.value);
  const locationIds = initialFilters
    .filter((f) => f.kind === "location")
    .map((f) => f.id);

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

  const h = await headers();
  const userLat = parseFloat(h.get("x-vercel-ip-latitude") ?? "");
  const userLng = parseFloat(h.get("x-vercel-ip-longitude") ?? "");

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
      userLat={Number.isFinite(userLat) ? userLat : undefined}
      userLng={Number.isFinite(userLng) ? userLng : undefined}
    />
  );
}
