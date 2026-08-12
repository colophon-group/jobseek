import type { ExploreData } from "@/lib/actions/explore-page-data";
import { resolveJobLanguages } from "@/lib/job-languages";
import { parseExploreSearchLanguages } from "@/lib/search/language-param";
import { parseOfflineSearchFilters } from "@/lib/search/offline-filters";

function parseRangeParam(value: string | undefined): {
  min: number | undefined;
  max: number | undefined;
} {
  if (!value) return { min: undefined, max: undefined };
  const [minValue, maxValue] = value.split("-");
  const min = minValue ? Number.parseInt(minValue, 10) : undefined;
  const max = maxValue ? Number.parseInt(maxValue, 10) : undefined;
  return {
    min: Number.isFinite(min) ? min : undefined,
    max: Number.isFinite(max) ? max : undefined,
  };
}

/**
 * Build a no-results degraded snapshot from the browser URL alone.
 *
 * This is the terminal client boundary for a rejected Explore Server Action.
 * It must never restore the queryless prerendered result set under a filtered
 * URL, because doing so visually broadens the user's request.
 */
export function buildUnavailableExploreData(params: {
  initialData?: ExploreData;
  locale: string;
  searchParams: Record<string, string | undefined>;
}): ExploreData {
  const { initialData, locale, searchParams } = params;
  const displayCurrency = initialData?.displayCurrency ?? "EUR";
  const jobLanguages = initialData?.jobLanguages ?? [];
  const parsedLanguage = parseExploreSearchLanguages(searchParams.lang);
  const languageOverride = parsedLanguage.ok ? parsedLanguage.languages : null;
  const languages = languageOverride ?? resolveJobLanguages(jobLanguages, locale);
  const salary = parseRangeParam(searchParams.sal);
  const experience = parseRangeParam(searchParams.exp);

  return {
    result: { companies: [], totalCompanies: 0, degraded: true },
    parsed: parseOfflineSearchFilters(searchParams),
    displayCurrency,
    jobLanguages,
    languages,
    languageOverride,
    userLat: initialData?.userLat,
    userLng: initialData?.userLng,
    salaryCurrencyParam: searchParams.salcur ?? displayCurrency,
    salaryMinDisplay: salary.min,
    salaryMaxDisplay: salary.max,
    experienceMin: experience.min,
    experienceMax: experience.max,
  };
}
