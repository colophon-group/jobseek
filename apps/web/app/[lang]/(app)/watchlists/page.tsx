import { Suspense } from "react";
import { Trans } from "@lingui/react/macro";
import { isLocale, defaultLocale } from "@/lib/i18n";
import { WatchlistsLoader } from "./watchlists-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function WatchlistsRoute({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return (
    <Suspense fallback={<WatchlistsFallback />}>
      <WatchlistsLoader locale={locale} />
    </Suspense>
  );
}

function WatchlistsFallback() {
  return (
    <div
      className="flex items-center justify-center py-24"
      role="status"
    >
      <div className="h-8 w-8 rounded-full border-4 border-muted border-t-primary motion-safe:animate-spin" />
      <span className="sr-only">
        <Trans id="watchlists.load.loading" comment="Accessible loading status for the private watchlists route">
          Loading watchlists…
        </Trans>
      </span>
    </div>
  );
}
