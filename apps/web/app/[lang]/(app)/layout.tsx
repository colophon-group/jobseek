import type { ReactNode } from "react";

import { AppBootstrapProvider } from "@/components/providers/AppBootstrapProvider";
import { AppHeader } from "@/components/AppHeader";
import { CookieBanner } from "@/components/CookieBanner";
import { SearchStateProvider } from "@/components/providers/SearchStateProvider";
import { ViewerTimezoneCookie } from "@/components/ViewerTimezoneCookie";
import { UpgradeBanner } from "@/components/UpgradeBanner";
import { BackToTop } from "@/components/ui/back-to-top";
import { SkipToContentLink } from "@/components/SkipToContentLink";
import { getCurrencyRates } from "@/lib/services/search";

type Props = {
  children: ReactNode;
};

// i18n is initialized once in the parent `[lang]/layout.tsx` (loadCatalog +
// setI18n + <LinguiClientProvider>); this layout no longer redoes that work.
// See #2883.
export default async function AppLayout({ children }: Props) {
  // Resolve the hours-cached table as part of the shared server shell. Passing
  // it into the client provider removes one uncached Server Action POST from
  // every app-page mount while retaining the same EUR fallback behavior.
  const currencyRates = await getCurrencyRates();

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
