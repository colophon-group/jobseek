"use client";

import { useEffect, useState } from "react";
import { useSearchParams } from "next/navigation";
import { fetchCompanyPageData, type CompanyPageData } from "@/lib/actions/company-page-data";
import { CompanySkeleton } from "@/components/search/company-skeleton";
import { CompanyPage } from "./company-page";

type CompanyContentProps = {
  locale: string;
  slug: string;
};

function CompanyNotFound() {
  return (
    <div className="flex flex-col items-center justify-center py-24 text-center">
      <h1 className="text-2xl font-bold">Company not found</h1>
      <p className="mt-2 text-muted">The company you are looking for does not exist or has been removed.</p>
    </div>
  );
}

export function CompanyContent({ locale, slug }: CompanyContentProps) {
  const searchParams = useSearchParams();
  const [data, setData] = useState<CompanyPageData | null | "not-found">(null);

  // Fetch initial data once on mount. After that, CompanyPage owns all
  // filter changes and searches — URL sync via replaceState is for
  // bookmarkability only and does not trigger a re-fetch here.
  useEffect(() => {
    window.scrollTo(0, 0);
    const sp: Record<string, string | undefined> = {};
    searchParams.forEach((value, key) => {
      sp[key] = value;
    });
    fetchCompanyPageData({ slug, searchParams: sp, locale }).then((result) => {
      setData(result ?? "not-found");
    });
  }, []);

  if (data === null) return <CompanySkeleton />;
  if (data === "not-found") return <CompanyNotFound />;

  return (
    <CompanyPage
      company={data.company}
      initialPostings={data.postings}
      initialActiveCount={data.activeCount}
      initialYearCount={data.yearCount}
      initialTruncated={data.truncated}
      initialKeywords={data.parsed.keywords}
      initialLocations={data.parsed.locations}
      initialOccupations={data.parsed.occupations}
      initialSeniorities={data.parsed.seniorities}
      initialTechnologies={data.parsed.technologies}
      initialWorkMode={data.parsed.workMode}
      initialSalaryCurrency={data.salaryCurrencyParam !== data.displayCurrency ? data.salaryCurrencyParam : undefined}
      initialSalaryMin={data.salaryMinDisplay}
      initialSalaryMax={data.salaryMaxDisplay}
      initialExperienceMin={data.experienceMin}
      initialExperienceMax={data.experienceMax}
      initialShowPostingId={data.showPostingId}
      displayCurrency={data.displayCurrency}
      locale={locale}
      jobLanguages={data.jobLanguages}
      languages={data.languages}
      userLat={data.userLat}
      userLng={data.userLng}
    />
  );
}
