import "server-only";

import { getCompanyBySlug } from "@/lib/services/company-detail";
import { resolveCompanyPageJobLanguages } from "@/lib/company-job-languages";
import type { CompanyPageData } from "@/lib/actions/company-page-data";
import type { ParsedSearchFilters } from "@/lib/services/search-input";

const PAGE_SIZE = 20;

const DEFAULT_DISPLAY_CURRENCY = "EUR";

const EMPTY_PARSED_FILTERS: ParsedSearchFilters = {
  keywords: [],
  locations: [],
  occupations: [],
  seniorities: [],
  technologies: [],
  workMode: [],
  employmentTypes: [],
};

/**
 * Public facts and anonymous defaults shared by the cached route and metadata.
 * No session, cookies or request headers enter this snapshot. Browser-direct
 * mode defers both lists; environments without it retain the server snapshot.
 * Unknown slugs return null so the route can render its not-found boundary.
 */
export async function fetchCompanyPageDefaults(params: {
  slug: string;
  locale: string;
}): Promise<CompanyPageData | null> {
  const { slug, locale } = params;

  const company = await getCompanyBySlug(slug, locale);
  if (!company) return null;

  const displayCurrency = DEFAULT_DISPLAY_CURRENCY;
  const { jobLanguages, languages } = resolveCompanyPageJobLanguages([], locale);

  // Browser-direct mode already reads fresh postings and peers after hydration.
  // Keep those lists out of the cached shell to avoid fetching, serializing and
  // rendering them a second time on every cold company/locale path. Retain the
  // anonymous server snapshot for environments where direct search is disabled.
  const postingsDeferred = process.env.NEXT_PUBLIC_TYPESENSE_DIRECT === "1";
  const [postingsResult, similarCompanies] = postingsDeferred
    ? [null, undefined]
    : await loadServerLists(company.id, company.industryId, languages, locale);

  return {
    company,
    postingsDeferred,
    similarCompanies,
    postings: postingsResult?.postings ?? [],
    activeCount: postingsResult?.activeCount ?? 0,
    yearCount: postingsResult?.yearCount ?? 0,
    truncated: postingsResult?.truncated,
    parsed: EMPTY_PARSED_FILTERS,
    displayCurrency,
    jobLanguages,
    languages,
    userLat: undefined,
    userLng: undefined,
    salaryCurrencyParam: displayCurrency,
    salaryMinDisplay: undefined,
    salaryMaxDisplay: undefined,
    experienceMin: undefined,
    experienceMax: undefined,
    showPostingId: null,
  };
}

async function loadServerLists(
  companyId: string,
  industryId: number | null,
  languages: string[],
  locale: string,
) {
  // Keep auth and personalized-search dependencies out of the direct-mode
  // company shell. This fallback is loaded only when direct search is disabled.
  const { getCompanyPostingsAnonymous, getSimilarCompanies } = await import("@/lib/services/company");
  return Promise.all([
    getCompanyPostingsAnonymous({
      companyId,
      keywords: [],
      languages,
      locale,
      offset: 0,
      limit: PAGE_SIZE,
    }),
    getSimilarCompanies(companyId, industryId, {
      offset: 0,
      limit: 10,
      locale,
    }),
  ]);
}
