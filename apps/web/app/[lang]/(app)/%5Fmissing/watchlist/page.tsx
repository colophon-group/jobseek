import type { Metadata } from "next";
import { isLocale, defaultLocale, loadCatalog } from "@/lib/i18n";
import { WatchlistNotFoundState } from "../../[userSlug]/[watchlistSlug]/watchlist-not-found";

export const metadata: Metadata = {
  robots: { index: false, follow: true },
};

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function MissingWatchlistPage({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n } = await loadCatalog(locale);

  return (
    <WatchlistNotFoundState
      lang={locale}
      title={i18n._({
        id: "watchlist.notFound.title",
        comment: "Heading shown when the watchlist URL doesn't resolve to a public watchlist",
        message: "Watchlist not found",
      })}
      message={i18n._({
        id: "watchlist.notFound.body",
        comment: "Body text for the watchlist-not-found page; explains the watchlist is either gone or private",
        message: "This watchlist does not exist or is not public.",
      })}
      browseLabel={i18n._({
        id: "watchlist.notFound.browse",
        comment: "Recovery action on the watchlist-not-found page",
        message: "Browse watchlists",
      })}
    />
  );
}
