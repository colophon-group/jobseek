"use client";

import { useEffect, useState, type ReactNode } from "react";
import { fetchAppBootstrap, type AppBootstrapData } from "@/lib/actions/bootstrap";
import { SessionProvider } from "@/components/SessionProvider";
import { SavedJobsProvider } from "@/components/SavedJobsProvider";
import { StarredCompaniesProvider } from "@/components/StarredCompaniesProvider";
import { SalaryDisplayProvider } from "@/components/SalaryDisplayProvider";
import { BannerProvider } from "@/components/BannerProvider";
import { PreferencesInitializer } from "@/components/PreferencesInitializer";
import { clearLoggedInHint, hasLoggedInHint } from "@/lib/client-cookies";

const ANON_BOOTSTRAP: AppBootstrapData = {
  user: null,
  prefs: null,
  savedStatuses: [],
  starredIds: [],
};

export function AppBootstrapProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AppBootstrapData | null>(null);

  useEffect(() => {
    // Skip the server-action RPC when the `logged_in` hint cookie is
    // absent — the vast majority of traffic is anonymous, and this path
    // produces zero Vercel function invocations. The real session
    // cookie is httpOnly so we cannot read it from JS; the non-httpOnly
    // hint is maintained by the Better Auth `after` hook (see auth.ts)
    // on every sign-in / sign-out / session-revocation. See #2246.
    if (!hasLoggedInHint()) {
      setData(ANON_BOOTSTRAP);
      return;
    }
    fetchAppBootstrap().then((result) => {
      // Self-heal a stale hint: if the server says we have no session
      // but our hint said we did, drop the hint so subsequent page
      // loads don't keep paying for the same empty round-trip.
      if (!result.user) clearLoggedInHint();
      setData(result);
    });
  }, []);

  const isPending = data === null;
  const user = data?.user ?? null;
  const prefs = data?.prefs;

  return (
    <SessionProvider user={user} isPending={isPending}>
      <SavedJobsProvider initialStatuses={data?.savedStatuses}>
        <StarredCompaniesProvider initialIds={data?.starredIds}>
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
        </StarredCompaniesProvider>
      </SavedJobsProvider>
    </SessionProvider>
  );
}
