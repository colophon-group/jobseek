import { type NextRequest, NextResponse } from "next/server";
import { suggestCompanies } from "@/lib/actions/company";
import { checkRateLimit, apiResponse, siteUrl } from "../_shared";

const MAX_RESULTS = 10;

export async function GET(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const q = sp.get("q");
  const locale = sp.get("locale") ?? "en";

  if (!q) {
    return apiResponse(
      { error: "Missing required 'q' param (company name query)" },
      { maxAge: 0 },
    );
  }

  const results = await suggestCompanies({ query: q });

  const companies = results.slice(0, MAX_RESULTS).map((c) => ({
    name: c.name,
    slug: c.slug,
    icon: c.icon,
    url: siteUrl(`/${locale}/company/${c.slug}`),
  }));

  return apiResponse({ companies }, { rateLimit: rl });
}
