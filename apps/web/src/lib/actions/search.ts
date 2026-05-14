"use server";

// Thin `"use server"` wrapper re-exporting the pure service tier from
// `@/lib/services/search`. The service module holds the implementation
// (no `"use server"` directive) and is the boundary used by public REST
// route handlers under `apps/web/app/api/v1/*` — see issue #3231.
//
// Why re-export instead of just importing in the routes? Existing client
// callers (form actions, server-action invocations from client components)
// still want these as server actions, and a single import path in the UI
// avoids a sprawling migration. The route handlers go straight to
// `@/lib/services/search` so they don't pay the server-action machinery
// cost (per-call RPC URL, serialization boundary, security IDs) for what
// is already a public REST surface.
//
// Type exports remain here for backwards compatibility with the
// `import type { PostingDetail } from "@/lib/actions/search"` callers; they
// are also exported from `@/lib/services/search` (the new canonical source).

export {
  getPostingDetail,
  searchJobs,
  listTopCompanies,
  listTopCompaniesAnonymous,
  getCurrencyRates,
  getSalaryHistogram,
  getExperienceHistogram,
  loadMorePostings,
} from "@/lib/services/search";

export type {
  PostingDetail,
  CurrencyRate,
  SalaryBucket,
  ExperienceBucket,
} from "@/lib/services/search";
