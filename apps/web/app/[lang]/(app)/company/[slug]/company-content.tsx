import { notFound } from "next/navigation";
import { headers } from "next/headers";
import { getI18n } from "@lingui/react/server";
import { getCompanyBySlug, getCompanyPostings } from "@/lib/actions/company";
import { getPreferences } from "@/lib/actions/preferences";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { resolveJobLanguages } from "@/lib/job-languages";
import { siteConfig } from "@/content/config";
import { JsonLd, formatEmployeeCount } from "@/lib/seo";
import type { Locale } from "@/lib/i18n";
import { CompanyPage } from "./company-page";

const PAGE_SIZE = 20;

/** Pick the first value when a search param appears multiple times. */
function firstOf(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

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

  const h = await headers();
  const userLat = parseFloat(h.get("x-vercel-ip-latitude") ?? "");
  const userLng = parseFloat(h.get("x-vercel-ip-longitude") ?? "");
  const parsedUserLat = Number.isFinite(userLat) ? userLat : undefined;
  const parsedUserLng = Number.isFinite(userLng) ? userLng : undefined;
  const [parsed, prefs] = await Promise.all([
    parseSearchFilters({ q, loc, occ, sen, tech, locale, userLat: parsedUserLat, userLng: parsedUserLng }),
    getPreferences(),
  ]);

  const jobLanguages = prefs?.jobLanguages ?? [];
  const languages = resolveJobLanguages(jobLanguages, locale);

  const displayCurrency = prefs?.displayCurrency ?? "EUR";
  const keywords = parsed.keywords;
  const locationIds = parsed.locations.map((l) => l.id);
  const occupationIds = parsed.occupations.map((o) => o.id);
  const seniorityIds = parsed.seniorities.map((s) => s.id);
  const technologyIds = parsed.technologies.map((t) => t.id);

  // Parse salary filter: sal=50000-120000, salcur=USD
  let salaryMinEur: number | undefined;
  let salaryMaxEur: number | undefined;
  let salaryMinDisplay: number | undefined;
  let salaryMaxDisplay: number | undefined;
  const salaryCurrencyParam = salcur ?? displayCurrency;
  if (sal) {
    const [minStr, maxStr] = sal.split("-");
    salaryMinDisplay = minStr ? parseInt(minStr, 10) : undefined;
    salaryMaxDisplay = maxStr ? parseInt(maxStr, 10) : undefined;
    salaryMinEur = salaryMinDisplay;
    salaryMaxEur = salaryMaxDisplay;
  }

  // Parse experience filter: exp=3-10
  let experienceMin: number | undefined;
  let experienceMax: number | undefined;
  if (exp) {
    const [minStr, maxStr] = exp.split("-");
    experienceMin = minStr ? parseInt(minStr, 10) : undefined;
    experienceMax = maxStr ? parseInt(maxStr, 10) : undefined;
  }

  const postingsResult = await getCompanyPostings({
    companyId: company.id,
    keywords,
    locationIds: locationIds.length > 0 ? locationIds : undefined,
    occupationIds: occupationIds.length > 0 ? occupationIds : undefined,
    seniorityIds: seniorityIds.length > 0 ? seniorityIds : undefined,
    technologyIds: technologyIds.length > 0 ? technologyIds : undefined,
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
      userLat={parsedUserLat}
      userLng={parsedUserLng}
    />
    </>
  );
}
