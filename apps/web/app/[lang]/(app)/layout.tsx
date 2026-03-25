import type { ReactNode } from "react";

export const dynamic = "force-dynamic";

import { setI18n } from "@lingui/react/server";
import { getSession } from "@/lib/sessionCache";
import { isLocale, defaultLocale, loadCatalog, type Locale } from "@/lib/i18n";
import { LinguiClientProvider } from "@/components/LinguiProvider";
import { getPreferences } from "@/lib/actions/preferences";
import { getSavedJobStatuses } from "@/lib/actions/saved-jobs";
import { getStarredCompanyIds } from "@/lib/actions/starred-companies";
import { SessionProvider } from "@/components/SessionProvider";
import { SavedJobsProvider } from "@/components/SavedJobsProvider";
import { StarredCompaniesProvider } from "@/components/StarredCompaniesProvider";
import { BannerProvider } from "@/components/BannerProvider";
import { AppHeader } from "@/components/AppHeader";
import { CookieBanner } from "@/components/CookieBanner";
import { PreferencesInitializer } from "@/components/PreferencesInitializer";
import { SearchStateProvider } from "@/components/SearchStateProvider";
import { UpgradeBanner } from "@/components/UpgradeBanner";
import { WatchlistTipBanner } from "@/components/watchlist/watchlist-tip-banner";
import { SalaryDisplayProvider } from "@/components/SalaryDisplayProvider";

type Props = {
  params: Promise<{ lang: string }>;
  children: ReactNode;
};

export default async function AppLayout({ params, children }: Props) {
  const { lang } = await params;
  const locale: Locale = isLocale(lang) ? lang : defaultLocale;
  const { i18n, messages } = await loadCatalog(locale);
  setI18n(i18n);
  const session = await getSession();

  const [prefs, savedStatuses, starredIds] = await Promise.all([
    session ? getPreferences() : null,
    session ? getSavedJobStatuses() : [],
    session ? getStarredCompanyIds() : [],
  ]);

  return (
    <LinguiClientProvider locale={locale} messages={messages}>
    <SessionProvider user={session?.user ?? null}>
      <SavedJobsProvider initialStatuses={savedStatuses ?? []}>
      <StarredCompaniesProvider initialIds={starredIds ?? []}>
      <SearchStateProvider>
      <SalaryDisplayProvider displayCurrency={prefs?.displayCurrency ?? null} salaryPeriod={prefs?.salaryPeriod ?? null}>
      <BannerProvider serverDismissed={prefs?.dismissedBanners ?? []}>
        <div className="flex min-h-dvh flex-col">
          {prefs && (
            <PreferencesInitializer
              theme={prefs.theme}
              themeUpdatedAt={prefs.themeUpdatedAt?.toISOString() ?? null}
              locale={prefs.locale}
              localeUpdatedAt={prefs.localeUpdatedAt?.toISOString() ?? null}
              cookieConsent={prefs.cookieConsent}
            />
          )}
          <AppHeader />
          <div className="flex min-h-0 flex-1 flex-col md:pt-12">
            <CookieBanner aboveBottomBar serverConsent={prefs?.cookieConsent} />
            <UpgradeBanner aboveBottomBar />
            <WatchlistTipBanner aboveBottomBar />
            <main className="mx-auto w-full max-w-[1200px] px-4 py-8 pb-20 md:pb-8">
              {children}
            </main>
          </div>
        </div>
      </BannerProvider>
      </SalaryDisplayProvider>
      </SearchStateProvider>
      </StarredCompaniesProvider>
      </SavedJobsProvider>
    </SessionProvider>
    </LinguiClientProvider>
  );
}
