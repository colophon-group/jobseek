"use client";

import { useEffect, useMemo, useState } from "react";
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

  // Build a stable key from search params, excluding UI-only params like "show"
  // so that opening a posting detail panel doesn't trigger a re-fetch.
  const searchKey = useMemo(() => {
    const filtered = new URLSearchParams();
    searchParams.forEach((value, key) => {
      if (key !== "show") filtered.set(key, value);
    });
    return filtered.toString();
  }, [searchParams]);

  useEffect(() => {
    setData(null);
    const sp: Record<string, string | undefined> = {};
    searchParams.forEach((value, key) => {
      sp[key] = value;
    });
    fetchExploreData({ searchParams: sp, locale }).then(setData);
  }, [searchKey, locale]);

  if (!data) return <ExploreSkeleton />;

  const { result, parsed, displayCurrency, jobLanguages, languages, userLat, userLng, salaryCurrencyParam, salaryMinDisplay, salaryMaxDisplay, experienceMin, experienceMax } = data;

  return (
    <SearchPage
      key={`${parsed.keywords.join(",")}-${parsed.locations.map((l) => l.id).join(",")}-${parsed.occupations.map((o) => o.id).join(",")}-${parsed.seniorities.map((s) => s.id).join(",")}-${parsed.technologies.map((t) => t.id).join(",")}`}
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
