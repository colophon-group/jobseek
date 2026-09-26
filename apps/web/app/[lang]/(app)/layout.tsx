import type { ReactNode } from "react";
import { cacheLife } from "next/cache";

import { AppBootstrapProvider } from "@/components/providers/AppBootstrapProvider";
import { AppHeader } from "@/components/AppHeader";
import { CookieBanner } from "@/components/CookieBanner";
import { SearchStateProvider } from "@/components/providers/SearchStateProvider";
import { ViewerTimezoneCookie } from "@/components/ViewerTimezoneCookie";
import { UpgradeBanner } from "@/components/UpgradeBanner";
import { BackToTop } from "@/components/ui/back-to-top";
import { SkipToContentLink } from "@/components/SkipToContentLink";
import { getCurrencyRates } from "@/lib/services/search";
import { CACHE_TTL_DAY } from "@/lib/cache-ttl";

type Props = {
  children: ReactNode;
};

async function getDisplayCurrencySnapshot() {
  "use cache";
  // ECB rates update daily. Keep the display snapshot on that cadence so the
  // shared layout does not shorten daily company/explore shells to one hour.
  // Server-side salary filters still use the hourly getCurrencyRates service.
  cacheLife({ revalidate: CACHE_TTL_DAY, expire: CACHE_TTL_DAY * 7 });
  return getCurrencyRates();
}

// i18n is initialized once in the parent `[lang]/layout.tsx` (loadCatalog +
// setI18n + <LinguiClientProvider>); this layout no longer redoes that work.
// See #2883.
export default async function AppLayout({ children }: Props) {
  // Resolve the daily display snapshot as part of the shared server shell. Passing
  // it into the client provider removes one uncached Server Action POST from
  // every app-page mount while retaining the same EUR fallback behavior.
  const currencyRates = await getDisplayCurrencySnapshot();

  return (
    <AppBootstrapProvider initialCurrencyRates={currencyRates}>
      <SearchStateProvider>
        <ViewerTimezoneCookie />
        <SkipToContentLink />
        <div className="flex min-h-dvh flex-col">
          <AppHeader />
          <div className="flex min-h-0 flex-1 flex-col md:pt-12">
            <CookieBanner aboveBottomBar />
            <UpgradeBanner aboveBottomBar />
            <main
              id="main-content"
              tabIndex={-1}
              className="mx-auto w-full max-w-[1200px] scroll-mt-12 px-4 py-8 pb-20 md:pb-8"
            >
              {children}
            </main>
          </div>
          <BackToTop />
        </div>
      </SearchStateProvider>
    </AppBootstrapProvider>
  );
}
