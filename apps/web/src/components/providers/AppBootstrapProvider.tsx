"use client";

import { useCallback, useEffect, useState, type ReactNode } from "react";
import { fetchAppBootstrap, type AppBootstrapData } from "@/lib/actions/bootstrap";
import { SessionProvider } from "@/components/providers/SessionProvider";
import { SavedJobsProvider } from "@/components/providers/SavedJobsProvider";
import { StarredCompaniesProvider } from "@/components/providers/StarredCompaniesProvider";
import { SalaryDisplayProvider } from "@/components/providers/SalaryDisplayProvider";
import { BannerProvider } from "@/components/providers/BannerProvider";
import { PreferencesInitializer } from "@/components/providers/PreferencesInitializer";
import { clearLoggedInHint, hasLoggedInHint } from "@/lib/client-cookies";

const ANON_BOOTSTRAP: AppBootstrapData = {
  user: null,
  prefs: null,
  savedStatuses: [],
  starredIds: [],
};

export function AppBootstrapProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<AppBootstrapData | null>(null);

  // Re-fetch the bootstrap payload and replace state in place. Used by
  // identity-mutation flows (e.g. `UsernameSection.handleSubmit` after
  // a successful `renameUsername`) so the SessionProvider stops
  // serving the pre-mutation `user.username` to client components
  // (#3022). Intentionally does NOT set `data = null` first — that
  // would flip `isPending = true` across every `useSession()` consumer
  // (header avatar, watchlist job list, etc.) and produce a transient
  // flicker. Replacing `data` once the new payload resolves is enough.
  const refresh = useCallback(async () => {
    const result = await fetchAppBootstrap();
    if (!result.user) clearLoggedInHint();
    setData(result);
  }, []);

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
    refresh();
  }, [refresh]);

  const isPending = data === null;
  const user = data?.user ?? null;
  const prefs = data?.prefs;

  return (
    <SessionProvider user={user} isPending={isPending} refresh={refresh}>
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
