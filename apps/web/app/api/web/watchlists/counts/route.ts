import { NextResponse } from "next/server";

import { locales } from "@/lib/i18n";
import { getSessionUserIdFromHeaders } from "@/lib/sessionCache";
import { getUserWatchlistCountsForUser } from "@/lib/services/watchlists";

export async function GET(request: Request) {
  const userId = await getSessionUserIdFromHeaders(request.headers);
  if (!userId) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const requestedLocale = new URL(request.url).searchParams.get("locale");
  const locale = requestedLocale && locales.includes(requestedLocale as (typeof locales)[number])
    ? requestedLocale
    : "en";
  const counts = await getUserWatchlistCountsForUser(userId, locale);

  return NextResponse.json(
    { counts },
    { headers: { "Cache-Control": "private, no-store" } },
  );
}
