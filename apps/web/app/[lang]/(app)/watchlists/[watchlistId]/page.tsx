import { Suspense } from "react";
import type { Metadata } from "next";
import { defaultLocale, isLocale, loadCatalog } from "@/lib/i18n";
import { OwnedWatchlistLoader } from "./owned-watchlist-loader";

type Props = {
  params: Promise<{ lang: string; watchlistId: string }>;
};

export const metadata: Metadata = {
  robots: { index: false, follow: false },
  referrer: "no-referrer",
};

export default async function OwnedWatchlistRoute({ params }: Props) {
  const { lang, watchlistId } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);
  const loadingLabel = i18n._({
    id: "watchlists.load.loading",
    comment: "Accessible loading status for a private watchlist detail route",
    message: "Loading watchlists…",
  });
  const overviewLabel = i18n._({
    id: "watchlists.page.title",
    comment: "Link from an owned watchlist detail back to the private overview",
    message: "Watchlists",
  });

  return (
    <Suspense fallback={<OwnedWatchlistFallback label={loadingLabel} />}>
      <OwnedWatchlistLoader
        locale={locale}
        watchlistId={watchlistId}
        overviewLabel={overviewLabel}
      />
    </Suspense>
  );
}

function OwnedWatchlistFallback({ label }: { label: string }) {
  return (
    <div className="flex items-center justify-center py-24" role="status">
      <div className="size-8 rounded-full border-4 border-muted border-t-primary motion-safe:animate-spin" />
      <span className="sr-only">{label}</span>
    </div>
  );
}
