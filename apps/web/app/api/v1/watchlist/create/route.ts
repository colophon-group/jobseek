import { type NextRequest, NextResponse } from "next/server";
// Public REST routes use the plain service tier — see issue #3231.
import { listTopCompanies, searchJobs } from "@/lib/services/search";
import { parseSearchFilters } from "@/lib/services/search-input";
import { checkRateLimit, apiResponse, siteUrl } from "../../_shared";

export async function GET(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const title = sp.get("title");
  if (!title) {
    return apiResponse(
      { error: "Missing required 'title' param" },
      { maxAge: 0 },
    );
  }

  const locale = sp.get("locale") ?? "en";
  const q = sp.get("q") ?? undefined;
  const loc = sp.get("loc") ?? undefined;
  const occ = sp.get("occ") ?? undefined;
  const sen = sp.get("sen") ?? undefined;
  const tech = sp.get("tech") ?? undefined;
  const wm = sp.get("wm") ?? undefined;
  const etype = sp.get("etype") ?? undefined;
  const sal = sp.get("sal") ?? undefined;
  const salcur = sp.get("salcur") ?? undefined;
  const exp = sp.get("exp") ?? undefined;
  const description = sp.get("description") ?? undefined;
  const companies = sp.get("companies") ?? undefined;

  // Resolve slugs to get matching counts for the preview
  const parsed = await parseSearchFilters({ q, loc, occ, sen, tech, wm, etype, locale });

  const locationIds =
    parsed.locations.length > 0 ? parsed.locations.map((l) => l.id) : undefined;
  const occupationIds =
    parsed.occupations.length > 0
      ? parsed.occupations.map((o) => o.id)
      : undefined;
  const seniorityIds =
    parsed.seniorities.length > 0
      ? parsed.seniorities.map((s) => s.id)
      : undefined;
  const technologyIds =
    parsed.technologies.length > 0
      ? parsed.technologies.map((t) => t.id)
      : undefined;

  let salaryMinEur: number | undefined;
  let salaryMaxEur: number | undefined;
  if (sal) {
    const [minStr, maxStr] = sal.split("-");
    salaryMinEur = minStr ? parseInt(minStr, 10) : undefined;
    salaryMaxEur = maxStr ? parseInt(maxStr, 10) : undefined;
  }

  let experienceMin: number | undefined;
  let experienceMax: number | undefined;
  if (exp) {
    const [minStr, maxStr] = exp.split("-");
    experienceMin = minStr ? parseInt(minStr, 10) : undefined;
    experienceMax = maxStr ? parseInt(maxStr, 10) : undefined;
  }

  const searchParams = {
    locationIds,
    occupationIds,
    seniorityIds,
    technologyIds,
    workMode: parsed.workMode.length > 0 ? parsed.workMode : undefined,
    employmentTypes:
      parsed.employmentTypes.length > 0 ? parsed.employmentTypes : undefined,
    salaryMinEur,
    salaryMaxEur,
    experienceMin,
    experienceMax,
    languages: [locale],
    locale,
    offset: 0,
    limit: 100,
  };

  // Get matching counts
  const result =
    parsed.keywords.length > 0
      ? await searchJobs({ keywords: parsed.keywords, ...searchParams })
      : await listTopCompanies(searchParams);

  const totalJobs = result.companies.reduce(
    (sum, c) => sum + c.activeMatches,
    0,
  );

  // Build the prefilled watchlist creation URL
  const createParams = new URLSearchParams();
  createParams.set("title", title);
  if (description) createParams.set("description", description);
  if (q) createParams.set("q", q);
  if (loc) createParams.set("loc", loc);
  if (occ) createParams.set("occ", occ);
  if (sen) createParams.set("sen", sen);
  if (tech) createParams.set("tech", tech);
  if (wm) createParams.set("wm", wm);
  if (etype) createParams.set("etype", etype);
  if (sal) createParams.set("sal", sal);
  if (salcur) createParams.set("salcur", salcur);
  if (exp) createParams.set("exp", exp);
  if (companies) createParams.set("companies", companies);

  return apiResponse(
    {
      url: siteUrl(
        `/${locale}/watchlists?${createParams.toString()}`,
      ),
      preview: {
        title,
        description: description ?? null,
        matchingCompanies: result.totalCompanies,
        matchingJobs: totalJobs,
      },
    },
    { rateLimit: rl },
  );
}
