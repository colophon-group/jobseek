import { type NextRequest, NextResponse } from "next/server";
import {
  searchPublicWatchlists,
  getPopularWatchlists,
} from "@/lib/services/watchlists";
import {
  checkRateLimit,
  apiProviderUnavailableResponse,
  sharedApiResponse,
  parseApiLocale,
  siteUrl,
} from "../_shared";
import { withPublicApiObservability } from "@/lib/public-api-observability";

const MAX_RESULTS = 10;

async function handleGet(request: NextRequest) {
  const rl = await checkRateLimit(request);
  if (rl instanceof NextResponse) return rl;

  const sp = request.nextUrl.searchParams;
  const q = sp.get("q") ?? "";
  const locale = parseApiLocale(sp, rl);
  if (locale instanceof NextResponse) return locale;

  let result: Awaited<ReturnType<typeof searchPublicWatchlists>>;
  try {
    result = q
      ? await searchPublicWatchlists({
          query: q,
          offset: 0,
          limit: MAX_RESULTS,
          locale,
          failOnUnavailable: true,
        })
      : await getPopularWatchlists({
          offset: 0,
          limit: MAX_RESULTS,
          locale,
          failOnUnavailable: true,
        });
  } catch (error) {
    return apiProviderUnavailableResponse(
      "public_api_watchlists",
      rl,
      error,
    );
  }

  const watchlists = result.watchlists.map((w) => ({
    title: w.title,
    description: w.description,
    owner: w.ownerUsername ? `@${w.ownerUsername}` : w.ownerName,
    companyCount: w.companyCount,
    url: siteUrl(
      `/${locale}/${w.ownerUsername ?? w.ownerName}/${w.slug}`,
    ),
  }));

  return sharedApiResponse({ watchlists });
}

export const GET = withPublicApiObservability("watchlists", handleGet);
