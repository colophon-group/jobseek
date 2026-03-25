import { notFound } from "next/navigation";
import { getI18n } from "@lingui/react/server";
import { getCompanyBySlug, getCompanyPostings } from "@/lib/actions/company";
import { getPreferences } from "@/lib/actions/preferences";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { resolveJobLanguages } from "@/lib/job-languages";
import { siteConfig } from "@/content/config";
import { JsonLd, formatEmployeeCount } from "@/lib/seo";
import { firstOf, idsOrUndefined, parseRangeParam, getGeoFromHeaders } from "@/lib/search/params";
import type { Locale } from "@/lib/i18n";
import { CompanyPage } from "./company-page";

const PAGE_SIZE = 20;

type CompanyContentProps = {
  locale: Locale;
  slug: string;
  searchParams: Record<string, string | string[] | undefined>;
};

export async function CompanyContent({ locale, slug, searchParams }: CompanyContentProps) {
  const q = firstOf(searchParams.q);
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const show = firstOf(searchParams.show);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);

  const company = await getCompanyBySlug(slug, locale);
  if (!company) notFound();

  const { userLat, userLng } = await getGeoFromHeaders();
  const [parsed, prefs] = await Promise.all([
    parseSearchFilters({ q, loc, occ, sen, tech, locale, userLat, userLng }),
    getPreferences(),
  ]);

  const jobLanguages = prefs?.jobLanguages ?? [];
  const languages = resolveJobLanguages(jobLanguages, locale);
  const displayCurrency = prefs?.displayCurrency ?? "EUR";

  const locationIds = idsOrUndefined(parsed.locations);
  const occupationIds = idsOrUndefined(parsed.occupations);
  const seniorityIds = idsOrUndefined(parsed.seniorities);
  const technologyIds = idsOrUndefined(parsed.technologies);

  const salaryCurrencyParam = salcur ?? displayCurrency;
  const { min: salaryMinDisplay, max: salaryMaxDisplay } = parseRangeParam(sal);
  const salaryMinEur = salaryMinDisplay;
  const salaryMaxEur = salaryMaxDisplay;
  const { min: experienceMin, max: experienceMax } = parseRangeParam(exp);

  const postingsResult = await getCompanyPostings({
    companyId: company.id,
    keywords: parsed.keywords,
    locationIds,
    occupationIds,
    seniorityIds,
    technologyIds,
    salaryMinEur,
    salaryMaxEur,
    experienceMin,
    experienceMax,
    languages,
    locale,
    offset: 0,
    limit: PAGE_SIZE,
  });

  const orgJsonLd: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "Organization",
    name: company.name,
    ...(company.website && { url: company.website }),
    ...(company.description && { description: company.description }),
    ...(company.icon && { logo: company.icon }),
    ...(company.foundedYear && { foundingDate: String(company.foundedYear) }),
    ...(company.industryName && { industry: company.industryName }),
    ...(formatEmployeeCount(company.employeeCountRange) && {
      numberOfEmployees: {
        "@type": "QuantitativeValue",
        value: formatEmployeeCount(company.employeeCountRange),
      },
    }),
  };

  const i18n = getI18n()!;
  const breadcrumbJsonLd: Record<string, unknown> = {
    "@context": "https://schema.org",
    "@type": "BreadcrumbList",
    itemListElement: [
      { "@type": "ListItem", position: 1, name: i18n._({ id: "breadcrumb.home", message: "Home" }), item: `${siteConfig.url}/${locale}` },
      { "@type": "ListItem", position: 2, name: i18n._({ id: "breadcrumb.explore", message: "Explore" }), item: `${siteConfig.url}/${locale}/explore` },
      { "@type": "ListItem", position: 3, name: company.name },
    ],
  };

  return (
    <>
      <JsonLd data={orgJsonLd} />
      <JsonLd data={breadcrumbJsonLd} />
      <CompanyPage
        company={company}
        initialPostings={postingsResult.postings}
        initialActiveCount={postingsResult.activeCount}
        initialYearCount={postingsResult.yearCount}
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
        initialShowPostingId={show ?? null}
        displayCurrency={displayCurrency}
        locale={locale}
        jobLanguages={jobLanguages}
        languages={languages}
        userLat={userLat}
        userLng={userLng}
      />
    </>
  );
}
