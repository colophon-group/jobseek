"use server";

// Thin `"use server"` wrapper around the pure service tier from
// `@/lib/services/search`. The service module holds the implementation
// (no `"use server"` directive) and is the boundary used by public REST
// route handlers under `apps/web/app/api/v1/*` — see issue #3231.
//
// Why declared async wrappers instead of `export { foo } from "..."`?
// Next.js / Turbopack processes `"use server"` files by scanning for
// **declared** async functions and converting each into a server-action
// reference. Pure `export { foo } from "..."` re-exports yield zero
// exports from the wrapper's perspective and break production builds at
// every client component that imports from `@/lib/actions/search`
// (PR #3335 build regression). So we declare each wrapper explicitly
// and delegate to the service implementation.
//
// Type re-exports remain plain `export type { ... } from "..."` because
// they are erased at compile-time and don't go through the
// server-action transform.
//
// Why a wrapper at all (vs. having UI callers import the service
// directly)? Existing client callers (form actions, server-action
// invocations from client components) still want these as server
// actions, and a single import path in the UI avoids a sprawling
// migration. The REST route handlers go straight to
// `@/lib/services/search` so they don't pay the server-action
// machinery cost (per-call RPC URL, serialization boundary, security
// IDs) for what is already a public REST surface.

import * as service from "@/lib/services/search";

export async function getPostingDetail(
  ...args: Parameters<typeof service.getPostingDetail>
): ReturnType<typeof service.getPostingDetail> {
  return service.getPostingDetail(...args);
}

export async function searchJobs(
  ...args: Parameters<typeof service.searchJobs>
): ReturnType<typeof service.searchJobs> {
  return service.searchJobs(...args);
}

export async function listTopCompanies(
  ...args: Parameters<typeof service.listTopCompanies>
): ReturnType<typeof service.listTopCompanies> {
  return service.listTopCompanies(...args);
}

export async function listTopCompaniesAnonymous(
  ...args: Parameters<typeof service.listTopCompaniesAnonymous>
): ReturnType<typeof service.listTopCompaniesAnonymous> {
  return service.listTopCompaniesAnonymous(...args);
}

export async function getCurrencyRates(
  ...args: Parameters<typeof service.getCurrencyRates>
): ReturnType<typeof service.getCurrencyRates> {
  return service.getCurrencyRates(...args);
}

export async function getSalaryHistogram(
  ...args: Parameters<typeof service.getSalaryHistogram>
): ReturnType<typeof service.getSalaryHistogram> {
  return service.getSalaryHistogram(...args);
}

export async function getExperienceHistogram(
  ...args: Parameters<typeof service.getExperienceHistogram>
): ReturnType<typeof service.getExperienceHistogram> {
  return service.getExperienceHistogram(...args);
}

export async function getMorePostings(
  ...args: Parameters<typeof service.getMorePostings>
): ReturnType<typeof service.getMorePostings> {
  return service.getMorePostings(...args);
}

export type {
  PostingDetail,
  CurrencyRate,
  SalaryBucket,
  ExperienceBucket,
} from "@/lib/services/search";
