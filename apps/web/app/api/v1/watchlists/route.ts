import { type NextRequest, NextResponse } from "next/server";
import {
  searchPublicWatchlists,
  getPopularWatchlists,
} from "@/lib/actions/watchlists";
import { checkRateLimit, apiResponse, siteUrl } from "../_shared";

const MAX_RESULTS = 10;

export async function GET(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const q = sp.get("q") ?? "";
  const locale = sp.get("locale") ?? "en";

  const result = q
    ? await searchPublicWatchlists({
        query: q,
        offset: 0,
        limit: MAX_RESULTS,
      })
    : await getPopularWatchlists({ offset: 0, limit: MAX_RESULTS });

  const watchlists = result.watchlists.map((w) => ({
    title: w.title,
    description: w.description,
    owner: w.ownerUsername ? `@${w.ownerUsername}` : w.ownerName,
    companyCount: w.companyCount,
    url: siteUrl(
      `/${locale}/${w.ownerUsername ?? w.ownerName}/${w.slug}`,
    ),
  }));

  return apiResponse({ watchlists }, { rateLimit: rl });
}
