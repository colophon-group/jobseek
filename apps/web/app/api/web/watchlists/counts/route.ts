import { NextResponse } from "next/server";

import { locales } from "@/lib/i18n";
import { getSessionUserId } from "@/lib/sessionCache";
import { getUserWatchlistCounts } from "@/lib/services/watchlists";

export async function GET(request: Request) {
  if (!(await getSessionUserId())) {
    return NextResponse.json({ error: "unauthorized" }, { status: 401 });
  }

  const requestedLocale = new URL(request.url).searchParams.get("locale");
  const locale = requestedLocale && locales.includes(requestedLocale as (typeof locales)[number])
    ? requestedLocale
    : "en";
  const counts = await getUserWatchlistCounts(locale);

  return NextResponse.json(
    { counts },
    { headers: { "Cache-Control": "private, no-store" } },
  );
}
