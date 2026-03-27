import type { ReactNode } from "react";

import { setI18n } from "@lingui/react/server";
import { isLocale, defaultLocale, loadCatalog, type Locale } from "@/lib/i18n";
import { LinguiClientProvider } from "@/components/LinguiProvider";
import { AppBootstrapProvider } from "@/components/AppBootstrapProvider";
import { AppHeader } from "@/components/AppHeader";
import { CookieBanner } from "@/components/CookieBanner";
import { SearchStateProvider } from "@/components/SearchStateProvider";
import { UpgradeBanner } from "@/components/UpgradeBanner";
import { WatchlistTipBanner } from "@/components/watchlist/watchlist-tip-banner";

type Props = {
  params: Promise<{ lang: string }>;
  children: ReactNode;
};

export default async function AppLayout({ params, children }: Props) {
  const { lang } = await params;
  const locale: Locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n, messages } = await loadCatalog(locale);
  setI18n(i18n);

  return (
    <LinguiClientProvider locale={locale} messages={messages}>
    <AppBootstrapProvider>
      <SearchStateProvider>
        <div className="flex min-h-dvh flex-col">
          <AppHeader />
          <div className="flex min-h-0 flex-1 flex-col md:pt-12">
            <CookieBanner aboveBottomBar />
            <UpgradeBanner aboveBottomBar />
            <WatchlistTipBanner aboveBottomBar />
            <main className="mx-auto w-full max-w-[1200px] px-4 py-8 pb-20 md:pb-8">
              {children}
            </main>
          </div>
        </div>
      </SearchStateProvider>
    </AppBootstrapProvider>
    </LinguiClientProvider>
  );
}
