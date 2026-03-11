import { Suspense } from "react";
import { headers } from "next/headers";
import { initI18nForPage } from "@/lib/i18n";
import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { SearchPage } from "./search-page";
import type { SelectedLocation } from "@/components/search/location-pills";

const PAGE_SIZE = 10;

type Props = {
  params: Promise<{ lang: string }>;
  searchParams: Promise<{ q?: string; loc?: string; show?: string }>;
};

function parseLocations(loc: string | undefined): SelectedLocation[] {
  if (!loc) return [];
  return loc
    .split(";")
    .map((entry) => {
      const [idStr, name, type, parentName] = entry.split(":");
      const id = Number(idStr);
      if (!id || !name) return null;
      return {
        id,
        name,
        type: (type || "city") as SelectedLocation["type"],
        parentName: parentName || null,
      };
    })
    .filter((l): l is SelectedLocation => l !== null);
}

export default async function AppPage({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const { q, loc } = await searchParams;
  const keywords = q
    ? q
        .split(",")
        .map((s) => s.trim())
        .filter(Boolean)
    : [];
  const locations = parseLocations(loc);
  const locationIds =
    locations.length > 0 ? locations.map((l) => l.id) : undefined;

  const result =
    keywords.length > 0
      ? await searchJobs({
          keywords,
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

  // Read geo headers for proximity-aware suggestions
  const h = await headers();
  const userLat = parseFloat(h.get("x-vercel-ip-latitude") ?? "");
  const userLng = parseFloat(h.get("x-vercel-ip-longitude") ?? "");

  return (
    <div className="py-8">
      <Suspense>
        <SearchPage
          key={`${keywords.join(",")}-${locations.map((l) => l.id).join(",")}`}
          initialCompanies={result.companies}
          initialTotalCompanies={result.totalCompanies}
          initialKeywords={keywords}
          initialLocations={locations}
          language={locale}
          userLat={Number.isFinite(userLat) ? userLat : undefined}
          userLng={Number.isFinite(userLng) ? userLng : undefined}
        />
      </Suspense>
    </div>
  );
}
