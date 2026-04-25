"use client";

import { useEffect, useState, type ReactNode } from "react";
import { fetchAppBootstrap, type AppBootstrapData } from "@/lib/actions/bootstrap";
import { SessionProvider } from "@/components/SessionProvider";
import { SavedJobsProvider } from "@/components/SavedJobsProvider";
import { StarredCompaniesProvider } from "@/components/StarredCompaniesProvider";
import { QueueProvider } from "@/components/QueueProvider";
import { SalaryDisplayProvider } from "@/components/SalaryDisplayProvider";
import { BannerProvider } from "@/components/BannerProvider";
import { PreferencesInitializer } from "@/components/PreferencesInitializer";

export function AppBootstrapProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AppBootstrapData | null>(null);

  useEffect(() => {
    fetchAppBootstrap().then(setData);
  }, []);

  const isPending = data === null;
  const user = data?.user ?? null;
  const prefs = data?.prefs;

  return (
    <SessionProvider user={user} isPending={isPending}>
      <SavedJobsProvider initialStatuses={data?.savedStatuses}>
        <StarredCompaniesProvider initialIds={data?.starredIds}>
          <QueueProvider initialStatuses={data?.queueStatuses}>
            <SalaryDisplayProvider
              displayCurrency={prefs?.displayCurrency ?? null}
              salaryPeriod={prefs?.salaryPeriod ?? null}
            >
              <BannerProvider serverDismissed={prefs?.dismissedBanners}>
                {prefs && (
                  <PreferencesInitializer
                    theme={prefs.theme}
                    themeUpdatedAt={prefs.themeUpdatedAt ? String(prefs.themeUpdatedAt) : null}
                    locale={prefs.locale}
                    localeUpdatedAt={prefs.localeUpdatedAt ? String(prefs.localeUpdatedAt) : null}
                    cookieConsent={prefs.cookieConsent}
                  />
                )}
                {children}
              </BannerProvider>
            </SalaryDisplayProvider>
          </QueueProvider>
        </StarredCompaniesProvider>
      </SavedJobsProvider>
    </SessionProvider>
  );
}
