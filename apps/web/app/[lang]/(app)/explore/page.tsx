import { Suspense } from "react";
import { headers } from "next/headers";
import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog, initI18nForPage } from "@/lib/i18n";
import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { getPreferences } from "@/lib/actions/preferences";
import { resolveJobLanguages } from "@/lib/job-languages";
import { siteConfig } from "@/content/config";
import { buildAlternates } from "@/lib/seo";
import { SearchPage } from "./search-page";

const PAGE_SIZE = 10;

type Props = {
  params: Promise<{ lang: string }>;
  searchParams: Promise<{ q?: string; loc?: string; occ?: string; sen?: string; tech?: string; show?: string; sal?: string; salcur?: string; exp?: string }>;
};

export async function generateMetadata({ params }: Props): Promise<Metadata> {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  const title = i18n.t({ id: "explore.meta.title", message: "Explore Jobs" });
  const description = i18n.t({
    id: "explore.meta.description",
    message: "Search jobs across hundreds of companies. Create watchlists to track new openings and get alerts.",
  });

  return {
    title,
    description,
    alternates: buildAlternates("/explore", locale),
    openGraph: {
      title,
      description,
      url: `${siteConfig.url}/${locale}/explore`,
      type: "website",
    },
  };
}

export default async function AppPage({ params, searchParams }: Props) {
  const locale = await initI18nForPage(params);
  const { q, loc, occ, sen, tech, sal, salcur, exp } = await searchParams;
  const h = await headers();
  const userLat = parseFloat(h.get("x-vercel-ip-latitude") ?? "");
  const userLng = parseFloat(h.get("x-vercel-ip-longitude") ?? "");
  const [parsed, prefs] = await Promise.all([
    parseSearchFilters({
      q,
      loc,
      occ,
      sen,
      tech,
      locale,
      userLat: Number.isFinite(userLat) ? userLat : undefined,
      userLng: Number.isFinite(userLng) ? userLng : undefined,
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
    // Convert to EUR for backend query (done client-side normally, but for SSR initial load
    // we need the rates here — we'll pass display values to the client and let it convert)
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
    <div>
      <Suspense>
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
          userLat={Number.isFinite(userLat) ? userLat : undefined}
          userLng={Number.isFinite(userLng) ? userLng : undefined}
        />
      </Suspense>
    </div>
  );
}
