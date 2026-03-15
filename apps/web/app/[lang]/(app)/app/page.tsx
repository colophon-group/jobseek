import { Suspense } from "react";
import { headers } from "next/headers";
import { initI18nForPage } from "@/lib/i18n";
import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { SearchPage } from "./search-page";

const PAGE_SIZE = 10;

type Props = {
  params: Promise<{ lang: string }>;
  searchParams: Promise<{ q?: string; loc?: string; show?: string }>;
};

export default async function AppPage({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const { q, loc } = await searchParams;
  const h = await headers();
  const userLat = parseFloat(h.get("x-vercel-ip-latitude") ?? "");
  const userLng = parseFloat(h.get("x-vercel-ip-longitude") ?? "");
  const parsed = await parseSearchFilters({
    q,
    loc,
    locale,
    userLat: Number.isFinite(userLat) ? userLat : undefined,
    userLng: Number.isFinite(userLng) ? userLng : undefined,
  });

  const locationIds =
    parsed.locations.length > 0 ? parsed.locations.map((l) => l.id) : undefined;

  const result =
    parsed.keywords.length > 0
      ? await searchJobs({
          keywords: parsed.keywords,
          locationIds,
          language: locale,
          offset: 0,
          limit: PAGE_SIZE,
        })
      : await listTopCompanies({
          locationIds,
          language: locale,
          offset: 0,
          limit: PAGE_SIZE,
        });

  return (
    <div>
      <Suspense>
        <SearchPage
          key={`${parsed.keywords.join(",")}-${parsed.locations.map((l) => l.id).join(",")}`}
          initialCompanies={result.companies}
          initialTotalCompanies={result.totalCompanies}
          initialKeywords={parsed.keywords}
          initialLocations={parsed.locations}
          language={locale}
          userLat={Number.isFinite(userLat) ? userLat : undefined}
          userLng={Number.isFinite(userLng) ? userLng : undefined}
        />
      </Suspense>
    </div>
  );
}
