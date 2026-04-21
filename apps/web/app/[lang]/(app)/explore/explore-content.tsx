"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { fetchExploreData, type ExploreData } from "@/lib/actions/explore-data";
import { ExploreSkeleton } from "@/components/search/explore-skeleton";
import { SearchPage } from "./search-page";

type ExploreContentProps = {
  locale: string;
};

export function ExploreContent({ locale }: ExploreContentProps) {
  const searchParams = useSearchParams();
  const [data, setData] = useState<ExploreData | null>(null);

  // Fetch initial data once on mount. After that, SearchPage owns all
  // filter changes and searches — URL sync via replaceState is for
  // bookmarkability only and does not trigger a re-fetch here.
  useEffect(() => {
    window.scrollTo(0, 0);
    const sp: Record<string, string | undefined> = {};
    searchParams.forEach((value, key) => {
      sp[key] = value;
    });
    fetchExploreData({ searchParams: sp, locale }).then(setData);
  }, []);

  if (!data) return <ExploreSkeleton />;

  const { result, parsed, displayCurrency, jobLanguages, languages, userLat, userLng, salaryCurrencyParam, salaryMinDisplay, salaryMaxDisplay, experienceMin, experienceMax } = data;

  return (
    <SearchPage
      initialCompanies={result.companies}
      initialTotalCompanies={result.totalCompanies}
      initialTruncated={result.truncated}
      initialKeywords={parsed.keywords}
      initialLocations={parsed.locations}
      initialOccupations={parsed.occupations}
      initialSeniorities={parsed.seniorities}
      initialTechnologies={parsed.technologies}
      initialSalaryCurrency={salaryCurrencyParam !== displayCurrency ? salaryCurrencyParam : undefined}
      initialSalaryMin={salaryMinDisplay}
      initialSalaryMax={salaryMaxDisplay}
      initialExperienceMin={experienceMin}
      initialExperienceMax={experienceMax}
      locale={locale}
      displayCurrency={displayCurrency}
      jobLanguages={jobLanguages}
      languages={languages}
      userLat={userLat}
      userLng={userLng}
    />
  );
}
