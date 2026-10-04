"use client";

import { useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { fetchAppBootstrap, type AppBootstrapData } from "@/lib/actions/bootstrap";
import { SessionProvider, type AccountStatus } from "@/components/providers/SessionProvider";
import { SavedJobsProvider } from "@/components/providers/SavedJobsProvider";
import { StarredCompaniesProvider } from "@/components/providers/StarredCompaniesProvider";
import { SalaryDisplayProvider } from "@/components/providers/SalaryDisplayProvider";
import { BannerProvider } from "@/components/providers/BannerProvider";
import { PreferencesInitializer } from "@/components/providers/PreferencesInitializer";
import { clearLoggedInHint, hasLoggedInHint, readCookieValue, LOGGED_IN_COOKIE, LOGGED_IN_HINT_CHANGED } from "@/lib/client-cookies";
import { authClient, listenForAccountIdentityChanges, notifyAccountIdentityChanged } from "@/lib/auth-client";
import type { CurrencyRate } from "@/lib/actions/search";

// Preserve the existing four-second loading bound; retries remain user initiated.
const ACCOUNT_REQUEST_TIMEOUT_MS = 4_000;
const ACCOUNT_RETRY_INTERVAL_MS = 5_000;

const ANON_BOOTSTRAP: AppBootstrapData = {
  user: null,
  plan: "free",
  prefs: null,
  savedStatuses: [],
  starredIds: [],
};

export function AppBootstrapProvider({
  children,
  initialCurrencyRates,
}: {
  children: ReactNode;
  initialCurrencyRates: CurrencyRate[];
}) {
  const [data, setData] = useState<AppBootstrapData | null>(null);
  const [accountStatus, setAccountStatus] = useState<AccountStatus>("pending");
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [identityRevision, setIdentityRevision] = useState(0);
  const [canRetry, setCanRetry] = useState(false);
  const generation = useRef(0);
  const currentHint = useRef<string | null>(null);
  const verifiedData = useRef<AppBootstrapData | null>(null);
  const activeRequest = useRef<{ promise: Promise<void>; cancel: () => void } | null>(null);
  const retryAfter = useRef(0);
  const retryTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  // Hints only delimit identity contexts. They never supply an identity or
  // permission, and a response from an earlier context must not restore it.
  const readHint = useCallback(() => hasLoggedInHint()
    ? readCookieValue(document.cookie, LOGGED_IN_COOKIE) ?? ""
    : null, []);

  const invalidate = useCallback(() => {
    generation.current += 1;
    activeRequest.current?.cancel();
    activeRequest.current = null;
    if (retryTimer.current !== null) clearTimeout(retryTimer.current);
    currentHint.current = readHint();
    if (verifiedData.current?.user) setIdentityRevision((revision) => revision + 1);
    verifiedData.current = null;
    setData(null);
    setIsRefreshing(false);
    setAccountStatus("unavailable");
    setCanRetry(true);
    retryAfter.current = 0;
  }, [readHint]);

  const invalidateAccount = useCallback(() => {
    invalidate();
    notifyAccountIdentityChanged();
  }, [invalidate]);

  const checkHint = useCallback(() => {
    if (readHint() === currentHint.current) return;
    invalidate();
    // Hint removal invalidates verified identity without an RPC. The same
    // zero-request anonymous path is used on initial loads without a hint.
    if (currentHint.current === null) {
      verifiedData.current = ANON_BOOTSTRAP;
      setData(ANON_BOOTSTRAP);
      setAccountStatus("ready");
      setCanRetry(false);
    }
  }, [invalidate, readHint]);

  // Every attempt uses the existing action. Coalesce concurrent callers and
  // bound the wait; a timed-out transport can finish, but cannot commit state.
  const refresh = useCallback((): Promise<void> => {
    checkHint();
    if (activeRequest.current) return activeRequest.current.promise;
    const requestGeneration = ++generation.current;
    const hint = currentHint.current;
    const retained = verifiedData.current?.user ? verifiedData.current : null;
    setData(retained);
    setIsRefreshing(true);
    setCanRetry(false);
    setAccountStatus((status) => status === "unavailable" ? status : "pending");
    retryAfter.current = Date.now() + ACCOUNT_RETRY_INTERVAL_MS;

    let cancel!: () => void;
    let timeout: ReturnType<typeof setTimeout>;
    const cancelled = new Promise<never>((_, reject) => {
      cancel = () => reject(new Error("Account request superseded"));
      timeout = setTimeout(() => reject(new Error("Account request timed out")), ACCOUNT_REQUEST_TIMEOUT_MS);
    });
    const promise = Promise.race([fetchAppBootstrap(), cancelled]).then((result) => {
      if (generation.current !== requestGeneration) return;
      if (readHint() !== hint) { checkHint(); return; }
      if (!result.user) {
        currentHint.current = null;
        clearLoggedInHint();
      }
      if (verifiedData.current?.user && verifiedData.current.user.id !== result.user?.id) {
        setIdentityRevision((revision) => revision + 1);
      }
      verifiedData.current = result;
      setData(result);
      setAccountStatus("ready");
    }).catch(() => {
      if (generation.current !== requestGeneration) return;
      if (readHint() !== hint) { checkHint(); return; }
      setData(retained);
      setAccountStatus("unavailable");
      retryTimer.current = setTimeout(() => {
        if (generation.current === requestGeneration) setCanRetry(true);
      }, Math.max(0, retryAfter.current - Date.now()));
    }).finally(() => {
      clearTimeout(timeout);
      if (generation.current !== requestGeneration) return;
      activeRequest.current = null;
      setIsRefreshing(false);
    });
    activeRequest.current = { promise, cancel };
    return promise;
  }, [checkHint, readHint]);

  const retry = useCallback(async () => {
    if (!canRetry || activeRequest.current || Date.now() < retryAfter.current) return;
    await refresh();
  }, [canRetry, refresh]);

  useEffect(() => {
    let active = true;
    currentHint.current = readHint();
    const dispatchGeneration = generation.current;
    const dispatchHint = currentHint.current;
    if (currentHint.current === null) {
      verifiedData.current = ANON_BOOTSTRAP;
      setData(ANON_BOOTSTRAP);
      setAccountStatus("ready");
    } else {
      // Strict Mode disposes its first effect before this microtask runs.
      // A context invalidated before dispatch must wait for an explicit retry.
      queueMicrotask(() => {
        if (!active || generation.current !== dispatchGeneration) return;
        if (readHint() !== dispatchHint) { checkHint(); return; }
        void refresh();
      });
    }
    // Listen only to the local auth mutation signal. Subscribing to the SDK's
    // session atom/useSession would mount its network refresh manager; this
    // plain signal has no fetch-on-mount behavior.
    const stopIdentityListener = listenForAccountIdentityChanges(invalidate);
    const stopAuthListener = authClient.$store.atoms.$sessionSignal.listen(invalidate);
    // Cookie changes are checked on browser lifecycle events and before each
    // attempt/response. None of these listeners starts a request or retry.
    window.addEventListener(LOGGED_IN_HINT_CHANGED, checkHint);
    window.addEventListener("focus", checkHint);
    window.addEventListener("pageshow", checkHint);
    return () => {
      active = false;
      generation.current += 1;
      activeRequest.current?.cancel();
      activeRequest.current = null;
      if (retryTimer.current !== null) clearTimeout(retryTimer.current);
      stopIdentityListener();
      stopAuthListener();
      window.removeEventListener(LOGGED_IN_HINT_CHANGED, checkHint);
      window.removeEventListener("focus", checkHint);
      window.removeEventListener("pageshow", checkHint);
    };
  }, [checkHint, invalidate, readHint, refresh]);

  const isPending = data === null;
  const user = data?.user ?? null;
  const prefs = data?.prefs;

  return (
    <SessionProvider
      user={user}
      plan={data?.plan ?? "free"}
      preferences={prefs ?? null}
      isPending={isPending}
      refresh={refresh}
      accountStatus={accountStatus}
      isRefreshing={isRefreshing}
      canRetry={canRetry}
      retry={retry}
      invalidate={invalidateAccount}
    >
      <SavedJobsProvider key={identityRevision} initialStatuses={data?.savedStatuses}>
        <StarredCompaniesProvider key={identityRevision} initialIds={data?.starredIds}>
          <SalaryDisplayProvider
            initialRates={initialCurrencyRates}
            displayCurrency={prefs?.displayCurrency ?? null}
            salaryPeriod={prefs?.salaryPeriod ?? null}
            persistLocally={!isPending && user === null}
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
