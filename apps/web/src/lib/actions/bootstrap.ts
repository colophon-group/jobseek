"use server";

import { getSession } from "@/lib/sessionCache";
import { getPreferences } from "@/lib/actions/preferences";
import { getSavedJobStatuses, type SavedJobStatus } from "@/lib/actions/saved-jobs";
import { getStarredCompanyIds } from "@/lib/actions/starred-companies";

export type SessionUser = {
  id: string;
  email: string;
  name: string;
  image?: string | null;
  emailVerified: boolean;
  username?: string | null;
  displayUsername?: string | null;
};

export type AppPreferences = {
  theme?: "light" | "dark";
  themeUpdatedAt?: Date | null;
  locale?: string;
  localeUpdatedAt?: Date | null;
  cookieConsent?: boolean;
  displayCurrency?: string;
  salaryPeriod?: string | null;
  dismissedBanners?: string[];
  jobLanguages?: string[];
};

export type AppBootstrapData = {
  user: SessionUser | null;
  prefs: AppPreferences | null;
  savedStatuses: SavedJobStatus[];
  starredIds: string[];
};

export async function fetchAppBootstrap(): Promise<AppBootstrapData> {
  const session = await getSession();
  if (!session) {
    return { user: null, prefs: null, savedStatuses: [], starredIds: [] };
  }

  const [prefs, savedStatuses, starredIds] = await Promise.all([
    getPreferences(),
    getSavedJobStatuses(),
    getStarredCompanyIds(),
  ]);

  return {
    user: session.user as SessionUser,
    prefs: prefs as AppPreferences | null,
    savedStatuses,
    starredIds,
  };
}
