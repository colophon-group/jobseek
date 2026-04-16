"use server";

import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { parseSearchFilters, type ParsedSearchFilters } from "@/lib/actions/search-input";
import { getPreferences } from "@/lib/actions/preferences";
import { getViewerLanguages } from "@/lib/viewer";
import { firstOf, idsOrUndefined, parseRangeParam, getGeoFromHeaders } from "@/lib/search/params";
import type { SearchResponse } from "@/lib/search";

const PAGE_SIZE = 10;

export interface ExploreData {
  result: SearchResponse;
  parsed: ParsedSearchFilters;
  displayCurrency: string;
  jobLanguages: string[];
  languages: string[];
  userLat: number | undefined;
  userLng: number | undefined;
  salaryCurrencyParam: string;
  salaryMinDisplay: number | undefined;
  salaryMaxDisplay: number | undefined;
  experienceMin: number | undefined;
  experienceMax: number | undefined;
}

export async function fetchExploreData(params: {
  searchParams: Record<string, string | string[] | undefined>;
  locale: string;
}): Promise<ExploreData> {
  const { searchParams, locale } = params;

  const q = firstOf(searchParams.q);
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);

  const { userLat, userLng } = await getGeoFromHeaders();

  const [parsed, prefs, languages] = await Promise.all([
    parseSearchFilters({ q, loc, occ, sen, tech, locale, userLat, userLng }),
    getPreferences(),
    getViewerLanguages(locale),
  ]);

  const jobLanguages = prefs?.jobLanguages ?? [];
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

  return {
    result,
    parsed,
    displayCurrency,
    jobLanguages,
    languages,
    userLat,
    userLng,
    salaryCurrencyParam,
    salaryMinDisplay,
    salaryMaxDisplay,
    experienceMin,
    experienceMax,
  };
}
