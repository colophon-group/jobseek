import {
  companyCsvDataCacheTag,
  typeaheadCompaniesCacheTag,
  typeaheadLocationsCacheTag,
  typeaheadOccupationsCacheTag,
  typeaheadSenioritiesCacheTag,
  typeaheadTechnologiesCacheTag,
} from "@/lib/cache-tags";

export const CACHE_NAMESPACE = {
  LOCATION_SUGGESTIONS: "loc-suggest",
  OCCUPATION_SUGGESTIONS: "occ-suggest",
  SENIORITY_SUGGESTIONS: "sen-suggest",
  TECHNOLOGY_SUGGESTIONS: "tech-suggest",
  COMPANY_SUGGESTIONS: "company-suggest",
  COMPANY_DETAIL: "company-slug",
  COMPANY_SIMILAR: "company-similar",
} as const;

export type CacheNamespace = (typeof CACHE_NAMESPACE)[keyof typeof CACHE_NAMESPACE];

export function cachePrefix(namespace: CacheNamespace): string {
  return `${namespace}:`;
}

export function buildCacheKey(
  namespace: CacheNamespace,
  ...parts: readonly (string | number)[]
): string {
  return [namespace, ...parts].join(":");
}

export function companyDetailCacheKey(slug: string, locale: string): string {
  return buildCacheKey(CACHE_NAMESPACE.COMPANY_DETAIL, slug, locale);
}

// Legacy Redis prefixes to sweep after crawler CSV/taxonomy sync. These
// prefixes mirror the cache namespaces that used to be minted by Redis
// `cached()` callers and remain as a rollout-window backstop for migrated
// `'use cache'` slots.
export const CACHE_PREFIXES_INVALIDATED_ON_SYNC = [
  cachePrefix(CACHE_NAMESPACE.LOCATION_SUGGESTIONS),
  cachePrefix(CACHE_NAMESPACE.OCCUPATION_SUGGESTIONS),
  cachePrefix(CACHE_NAMESPACE.SENIORITY_SUGGESTIONS),
  cachePrefix(CACHE_NAMESPACE.TECHNOLOGY_SUGGESTIONS),
  cachePrefix(CACHE_NAMESPACE.COMPANY_SUGGESTIONS),
  cachePrefix(CACHE_NAMESPACE.COMPANY_DETAIL),
  cachePrefix(CACHE_NAMESPACE.COMPANY_SIMILAR),
] as const;

export const CACHE_TAGS_INVALIDATED_ON_SYNC = [
  typeaheadLocationsCacheTag(),
  typeaheadOccupationsCacheTag(),
  typeaheadSenioritiesCacheTag(),
  typeaheadTechnologiesCacheTag(),
  typeaheadCompaniesCacheTag(),
  companyCsvDataCacheTag(),
] as const;
