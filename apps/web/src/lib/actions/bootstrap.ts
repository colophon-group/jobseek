"use server";

import { getSession } from "@/lib/sessionCache";
import { getPreferences } from "@/lib/actions/preferences";
import { getSavedJobStatuses, type SavedJobStatus } from "@/lib/actions/saved-jobs";
import { getStarredCompanyIds } from "@/lib/actions/starred-companies";
import { getQueueStatuses } from "@/lib/actions/queue";

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
  queueStatuses: Array<{ postingId: string; queued: boolean; queueId?: string; analyzed: boolean }>;
};

export async function fetchAppBootstrap(): Promise<AppBootstrapData> {
  const session = await getSession();
  if (!session) {
    return { user: null, prefs: null, savedStatuses: [], starredIds: [], queueStatuses: [] };
  }

  const [prefs, savedStatuses, starredIds, queueStatuses] = await Promise.all([
    getPreferences(),
    getSavedJobStatuses(),
    getStarredCompanyIds(),
    getQueueStatuses(),
  ]);

  return {
    user: session.user as SessionUser,
    prefs: prefs as AppPreferences | null,
    savedStatuses,
    starredIds,
    queueStatuses,
  };
}
