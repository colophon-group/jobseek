import { type NextRequest, NextResponse } from "next/server";
// Public REST routes use the plain service tier — see issue #3231.
import { getPostingDetail } from "@/lib/services/search";
import { checkRateLimit, apiResponse, siteUrl } from "../_shared";

export async function GET(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const id = sp.get("id");
  const locale = sp.get("locale") ?? "en";

  if (!id) {
    return NextResponse.json(
      { error: "Missing required parameter: id" },
      { status: 400 },
    );
  }

  const detail = await getPostingDetail({ postingId: id, locale });

  if (!detail) {
    return NextResponse.json(
      { error: "Job posting not found" },
      { status: 404 },
    );
  }

  return apiResponse(
    {
      id: detail.id,
      title: detail.title,
      company: {
        name: detail.company.name,
        slug: detail.company.slug,
        icon: detail.company.icon,
        url: siteUrl(`/${locale}/company/${detail.company.slug}`),
      },
      locations: detail.locations.map((l) => ({
        name: l.name,
        type: l.type,
        geoType: l.geoType ?? null,
        parentName: l.parentName ?? null,
      })),
      seniority: detail.seniority
        ? { slug: detail.seniority.slug, name: detail.seniority.name }
        : null,
      technologies: detail.technologies.map((t) => ({ name: t.name })),
      salary:
        detail.salaryMin || detail.salaryMax
          ? {
              min: detail.salaryMin,
              max: detail.salaryMax,
              currency: detail.salaryCurrency,
              period: detail.salaryPeriod,
            }
          : null,
      experience:
        detail.experienceMin != null || detail.experienceMax != null
          ? { min: detail.experienceMin, max: detail.experienceMax }
          : null,
      employmentType: detail.employmentType,
      url: siteUrl(`/${locale}/company/${detail.company.slug}?show=${detail.id}`),
      firstSeenAt: detail.firstSeenAt,
    },
    { rateLimit: rl },
  );
}
