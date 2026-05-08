"use server";

import { getCompanyBySlug, getCompanyPostings, type CompanyDetail } from "@/lib/actions/company";
import { parseSearchFilters, type ParsedSearchFilters } from "@/lib/actions/search-input";
import { getPreferences } from "@/lib/actions/preferences";
import { readAnonJobLanguagesCookie } from "@/lib/anon-preferences";
import { getSession } from "@/lib/sessionCache";
import { resolveJobLanguages } from "@/lib/job-languages";
import { firstOf, idsOrUndefined, parseRangeParam, getGeoFromHeaders } from "@/lib/search/params";
import type { SearchResultPosting } from "@/lib/search";

const PAGE_SIZE = 20;

export interface CompanyPageData {
  company: CompanyDetail;
  postings: SearchResultPosting[];
  activeCount: number;
  yearCount: number;
  truncated?: boolean;
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
  showPostingId: string | null;
}

export async function fetchCompanyPageData(params: {
  slug: string;
  searchParams: Record<string, string | string[] | undefined>;
  locale: string;
}): Promise<CompanyPageData | null> {
  const { slug, searchParams, locale } = params;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) return null;

  const q = firstOf(searchParams.q);
  const loc = firstOf(searchParams.loc);
  const occ = firstOf(searchParams.occ);
  const sen = firstOf(searchParams.sen);
  const tech = firstOf(searchParams.tech);
  const show = firstOf(searchParams.show);
  const sal = firstOf(searchParams.sal);
  const salcur = firstOf(searchParams.salcur);
  const exp = firstOf(searchParams.exp);

  const { userLat, userLng } = await getGeoFromHeaders();

  // Auth users persist `jobLanguages` in `user_preferences`; anon users
  // mirror it into a cookie (issue #2850 + `anon-preferences.ts`).
  const session = await getSession();
  const [parsed, prefs, anonJobLangs] = await Promise.all([
    parseSearchFilters({ q, loc, occ, sen, tech, locale, userLat, userLng }),
    session ? getPreferences() : Promise.resolve(null),
    session ? Promise.resolve(null) : readAnonJobLanguagesCookie(),
  ]);

  const jobLanguages = prefs?.jobLanguages ?? anonJobLangs ?? [];
  const displayCurrency = prefs?.displayCurrency ?? "EUR";
  const languages = resolveJobLanguages(jobLanguages, locale);

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

  return {
    company,
    postings: postingsResult.postings,
    activeCount: postingsResult.activeCount,
    yearCount: postingsResult.yearCount,
    truncated: postingsResult.truncated,
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
    showPostingId: show ?? null,
  };
}
