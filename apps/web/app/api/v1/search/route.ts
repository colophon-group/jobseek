import { type NextRequest } from "next/server";
import { searchJobs, listTopCompanies } from "@/lib/actions/search";
import { parseSearchFilters } from "@/lib/actions/search-input";
import { checkRateLimit, apiResponse, siteUrl, exploreUrl } from "../_shared";

const MAX_COMPANIES = 5;
const MAX_POSTINGS_PER_COMPANY = 3;

export async function GET(request: NextRequest) {
  const limited = await checkRateLimit(request);
  if (limited) return limited;

  const sp = request.nextUrl.searchParams;
  const q = sp.get("q") ?? undefined;
  const loc = sp.get("loc") ?? undefined;
  const occ = sp.get("occ") ?? undefined;
  const sen = sp.get("sen") ?? undefined;
  const tech = sp.get("tech") ?? undefined;
  const sal = sp.get("sal") ?? undefined;
  const exp = sp.get("exp") ?? undefined;
  const locale = sp.get("locale") ?? "en";

  const parsed = await parseSearchFilters({ q, loc, occ, sen, tech, locale });

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
    salaryMinEur,
    salaryMaxEur,
    experienceMin,
    experienceMax,
    languages: [locale],
    locale,
    offset: 0,
    limit: MAX_COMPANIES,
  };

  const result =
    parsed.keywords.length > 0
      ? await searchJobs({ keywords: parsed.keywords, ...searchParams })
      : await listTopCompanies(searchParams);

  const companies = result.companies.slice(0, MAX_COMPANIES).map((c) => ({
    name: c.company.name,
    slug: c.company.slug,
    icon: c.company.icon,
    url: siteUrl(`/${locale}/company/${c.company.slug}`),
    activeJobs: c.activeMatches,
    topPostings: c.postings.slice(0, MAX_POSTINGS_PER_COMPANY).map((p) => ({
      id: p.id,
      title: p.title,
      location: p.locations.map((l) => l.name).join(", ") || null,
      url: siteUrl(
        `/${locale}/company/${c.company.slug}?show=${p.id}`,
      ),
    })),
  }));

  return apiResponse({
    companies,
    totalCompanies: result.totalCompanies,
    moreAt: exploreUrl(sp, locale),
  });
}
