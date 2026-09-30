import type { WatchlistFilters } from "@/lib/watchlist-matcher-contract";
import type { SelectedLocation, WorkMode } from "@/lib/search/types";

type TaxonomyItem = { slug: string; name: string };

export type SearchWatchlistDraft = {
  title: string;
  companyIds: string[];
  filters: WatchlistFilters;
  isPublic: false;
};

const GENERATED_TITLE_MAX_LENGTH = 100;

function boundedTitle(value: string): string {
  let title = value.slice(0, GENERATED_TITLE_MAX_LENGTH).trimEnd();
  const finalCodeUnit = title.charCodeAt(title.length - 1);
  if (finalCodeUnit >= 0xd800 && finalCodeUnit <= 0xdbff) {
    title = title.slice(0, -1).trimEnd();
  }
  return title;
}

/** Build the exact persisted scope shared by Save search and precise matching. */
export function buildSearchWatchlistDraft(input: {
  fallbackTitle: string;
  keywords: string[];
  locations: SelectedLocation[];
  occupations: TaxonomyItem[];
  seniorities: TaxonomyItem[];
  technologies?: TaxonomyItem[];
  employmentTypes?: string[];
  workMode?: WorkMode[];
  salaryMin?: number;
  salaryMax?: number;
  salaryCurrency?: string;
  experienceMin?: number;
  experienceMax?: number;
  companyScope?: { id: string; name: string };
  unresolvedLocationSlugs?: string[];
  unresolvedOccupationSlugs?: string[];
  unresolvedSenioritySlugs?: string[];
  unresolvedTechnologySlugs?: string[];
}): SearchWatchlistDraft {
  const parts: string[] = [];
  if (input.companyScope) parts.push(input.companyScope.name);
  if (input.keywords.length > 0) parts.push(input.keywords.join(", "));
  if (input.locations.length > 0) {
    parts.push(input.locations.map((location) => location.name).join(", "));
  }
  if (input.occupations.length > 0) {
    parts.push(input.occupations.map((occupation) => occupation.name).join(", "));
  }

  const filters: WatchlistFilters = {
    anyCompany: input.companyScope == null,
  };
  if (input.keywords.length > 0) filters.keywords = input.keywords;

  const locationSlugs = [
    ...input.locations.map((location) => location.slug),
    ...(input.unresolvedLocationSlugs ?? []),
  ];
  const occupationSlugs = [
    ...input.occupations.map((occupation) => occupation.slug),
    ...(input.unresolvedOccupationSlugs ?? []),
  ];
  const senioritySlugs = [
    ...input.seniorities.map((seniority) => seniority.slug),
    ...(input.unresolvedSenioritySlugs ?? []),
  ];
  const technologySlugs = [
    ...(input.technologies ?? []).map((technology) => technology.slug),
    ...(input.unresolvedTechnologySlugs ?? []),
  ];

  if (locationSlugs.length > 0) filters.locationSlugs = locationSlugs;
  if (occupationSlugs.length > 0) filters.occupationSlugs = occupationSlugs;
  if (senioritySlugs.length > 0) filters.senioritySlugs = senioritySlugs;
  if (technologySlugs.length > 0) filters.technologySlugs = technologySlugs;
  if (input.employmentTypes?.length) filters.employmentType = input.employmentTypes;
  if (input.workMode?.length) filters.workMode = input.workMode;
  if (input.salaryMin != null) filters.salaryMin = input.salaryMin;
  if (input.salaryMax != null) filters.salaryMax = input.salaryMax;
  if (input.salaryCurrency) filters.salaryCurrency = input.salaryCurrency;
  if (input.experienceMin != null) filters.experienceMin = input.experienceMin;
  if (input.experienceMax != null) filters.experienceMax = input.experienceMax;

  return {
    title: boundedTitle(parts.length > 0 ? parts.join(" · ") : input.fallbackTitle),
    companyIds: input.companyScope ? [input.companyScope.id] : [],
    filters,
    isPublic: false,
  };
}
