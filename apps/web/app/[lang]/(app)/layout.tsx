import type { ReactNode } from "react";

import { AppBootstrapProvider } from "@/components/providers/AppBootstrapProvider";
import { AppHeader } from "@/components/AppHeader";
import { CookieBanner } from "@/components/CookieBanner";
import { SearchStateProvider } from "@/components/providers/SearchStateProvider";
import { UpgradeBanner } from "@/components/UpgradeBanner";
import { WatchlistTipBanner } from "@/components/watchlist/watchlist-tip-banner";
import { BackToTop } from "@/components/ui/back-to-top";
import { SkipToContentLink } from "@/components/SkipToContentLink";

type Props = {
  children: ReactNode;
};

// i18n is initialized once in the parent `[lang]/layout.tsx` (loadCatalog +
// setI18n + <LinguiClientProvider>); this layout no longer redoes that work.
// See #2883.
export default async function AppLayout({ children }: Props) {
  return (
    <AppBootstrapProvider>
      <SearchStateProvider>
        <SkipToContentLink />
        <div className="flex min-h-dvh flex-col">
          <AppHeader />
          <div className="flex min-h-0 flex-1 flex-col md:pt-12">
            <CookieBanner aboveBottomBar />
            <UpgradeBanner aboveBottomBar />
            <WatchlistTipBanner aboveBottomBar />
            <main
              id="main-content"
              className="mx-auto w-full max-w-[1200px] px-4 py-8 pb-20 md:pb-8"
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
