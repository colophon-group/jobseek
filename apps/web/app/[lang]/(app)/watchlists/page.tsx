import type { Metadata } from "next";
import { Suspense } from "react";
import { isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { WatchlistsLoader } from "./watchlists-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export const metadata: Metadata = {
  robots: { index: false, follow: false },
};

export default async function WatchlistsRoute({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);
  const loadingLabel = i18n._({
    id: "watchlists.load.loading",
    comment: "Accessible loading status for the private watchlists overview route",
    message: "Loading watchlists…",
  });
  const errorLabel = i18n._({
    id: "watchlists.load.error",
    comment: "Error shown when the watchlists overview cannot load after a retry",
    message: "We couldn't load your watchlists.",
  });
  const retryLabel = i18n._({
    id: "watchlists.load.retry",
    comment: "Button to retry loading the watchlists overview",
    message: "Try again",
  });
  return (
    <Suspense fallback={<WatchlistsFallback label={loadingLabel} />}>
      <WatchlistsLoader
        locale={locale}
        errorLabel={errorLabel}
        retryLabel={retryLabel}
      />
    </Suspense>
  );
}

function WatchlistsFallback({ label }: { label: string }) {
  return (
    <div
      className="flex items-center justify-center py-24"
      role="status"
    >
      <div className="h-8 w-8 rounded-full border-4 border-muted border-t-primary motion-safe:animate-spin" />
      <span className="sr-only">{label}</span>
    </div>
  );
}
