import { isLocale, defaultLocale } from "@/lib/i18n";
import { WatchlistsLoader } from "./watchlists-loader";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function WatchlistsRoute({ params }: Props) {
  const { lang } = await params;
  const locale = isLocale(lang) ? lang : defaultLocale;
  return <WatchlistsLoader locale={locale} />;
}
