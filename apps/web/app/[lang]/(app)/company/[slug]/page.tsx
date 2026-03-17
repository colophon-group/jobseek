import { notFound } from "next/navigation";
import { headers } from "next/headers";
import type { Metadata } from "next";
import { initI18nForPage } from "@/lib/i18n";
import { getCompanyBySlug, getCompanyPostings } from "@/lib/actions/company";
import { getPreferences } from "@/lib/actions/preferences";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { resolveJobLanguages } from "@/lib/job-languages";
import { CompanyPage } from "./company-page";

const PAGE_SIZE = 20;

type Props = {
  params: Promise<{ lang: string; slug: string }>;
  searchParams: Promise<{ q?: string; loc?: string; occ?: string; sen?: string; tech?: string; show?: string; sal?: string; salcur?: string; exp?: string }>;
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

export default async function CompanyPageRoute({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const { slug } = await params;
  const { q, loc, occ, sen, tech, show, sal, salcur, exp } = await searchParams;

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

  return (
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
  );
}
