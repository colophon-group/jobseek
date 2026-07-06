"use server";

// Thin `"use server"` wrapper around the plain company service tier from
// `@/lib/services/company`. The service module holds the implementation
// and is the boundary used by public REST route handlers under
// `apps/web/app/api/v1/*` — see issues #3231 / #3331.
//
// Keep these as declared async wrappers, not `export { foo } from "..."`.
// Next.js / Turbopack scans `"use server"` files for declared async
// functions and turns them into server-action references. Plain re-exports
// can disappear from the server-action transform and break client imports.

import * as service from "@/lib/services/company";

export async function suggestCompanies(
  ...args: Parameters<typeof service.suggestCompanies>
): ReturnType<typeof service.suggestCompanies> {
  return service.suggestCompanies(...args);
}

export async function searchCompaniesForWatchlist(
  ...args: Parameters<typeof service.searchCompaniesForWatchlist>
): ReturnType<typeof service.searchCompaniesForWatchlist> {
  return service.searchCompaniesForWatchlist(...args);
}

export async function suggestIndustries(
  ...args: Parameters<typeof service.suggestIndustries>
): ReturnType<typeof service.suggestIndustries> {
  return service.suggestIndustries(...args);
}

export async function getCompanyBySlug(
  ...args: Parameters<typeof service.getCompanyBySlug>
): ReturnType<typeof service.getCompanyBySlug> {
  return service.getCompanyBySlug(...args);
}

export async function getSimilarCompanies(
  ...args: Parameters<typeof service.getSimilarCompanies>
): ReturnType<typeof service.getSimilarCompanies> {
  return service.getSimilarCompanies(...args);
}

export async function getCompanyPostings(
  ...args: Parameters<typeof service.getCompanyPostings>
): ReturnType<typeof service.getCompanyPostings> {
  return service.getCompanyPostings(...args);
}

export async function getCompanyPostingsAnonymous(
  ...args: Parameters<typeof service.getCompanyPostingsAnonymous>
): ReturnType<typeof service.getCompanyPostingsAnonymous> {
  return service.getCompanyPostingsAnonymous(...args);
}

export async function getCompanyTopLocations(
  ...args: Parameters<typeof service.getCompanyTopLocations>
): ReturnType<typeof service.getCompanyTopLocations> {
  return service.getCompanyTopLocations(...args);
}

export async function getCompanyLocationsGrouped(
  ...args: Parameters<typeof service.getCompanyLocationsGrouped>
): ReturnType<typeof service.getCompanyLocationsGrouped> {
  return service.getCompanyLocationsGrouped(...args);
}

export async function getCompanyLocationsGroupedWithMacros(
  ...args: Parameters<typeof service.getCompanyLocationsGroupedWithMacros>
): ReturnType<typeof service.getCompanyLocationsGroupedWithMacros> {
  return service.getCompanyLocationsGroupedWithMacros(...args);
}

export type {
  CompanySuggestion,
  CompanyListEntry,
  IndustrySuggestion,
  CompanyDetail,
  SimilarCompany,
  SimilarCompaniesPage,
  CompanyPostingsParams,
  CompanyLocation,
  CompanyLocationWithAliases,
  CompanyRegionGroup,
  GroupedCompanyLocations,
  CompanyMacroRegion,
  CompanyLocationsResponse,
} from "@/lib/services/company";
