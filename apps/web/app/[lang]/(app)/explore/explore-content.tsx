import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { getPreferences } from "@/lib/actions/preferences";
import { resolveJobLanguages } from "@/lib/job-languages";
import type { Locale } from "@/lib/i18n";
import { SearchPage } from "./search-page";

const PAGE_SIZE = 10;

/** Pick the first value when a search param appears multiple times (?q=a&q=b). */
function firstOf(v: string | string[] | undefined): string | undefined {
  return Array.isArray(v) ? v[0] : v;
}

type ExploreContentProps = {
  locale: Locale;
  searchParams: Record<string, string | string[] | undefined>;
  userLat: number | undefined;
  userLng: number | undefined;
};

export async function ExploreContent({ locale, searchParams, userLat, userLng }: ExploreContentProps) {
  const q = firstOf(searchParams.q);
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);

  const [parsed, prefs] = await Promise.all([
    parseSearchFilters({
      q, loc, occ, sen, tech, locale,
      userLat,
      userLng,
    }),
    getPreferences(),
  ]);

  const jobLanguages = prefs?.jobLanguages ?? [];
  const languages = resolveJobLanguages(jobLanguages, locale);
  const displayCurrency = prefs?.displayCurrency ?? "EUR";

  const locationIds =
    parsed.locations.length > 0 ? parsed.locations.map((l) => l.id) : undefined;
  const occupationIds =
    parsed.occupations.length > 0 ? parsed.occupations.map((o) => o.id) : undefined;
  const seniorityIds =
    parsed.seniorities.length > 0 ? parsed.seniorities.map((s) => s.id) : undefined;
  const technologyIds =
    parsed.technologies.length > 0 ? parsed.technologies.map((t) => t.id) : undefined;

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

  const result =
    parsed.keywords.length > 0
      ? await searchJobs({
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
        })
      : await listTopCompanies({
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

  return (
    <SearchPage
      key={`${parsed.keywords.join(",")}-${parsed.locations.map((l) => l.id).join(",")}-${parsed.occupations.map((o) => o.id).join(",")}-${parsed.seniorities.map((s) => s.id).join(",")}-${parsed.technologies.map((t) => t.id).join(",")}`}
      initialCompanies={result.companies}
      initialTotalCompanies={result.totalCompanies}
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
