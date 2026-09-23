"use server";

import { inArray } from "drizzle-orm";
import { db } from "@/db";
import { company } from "@/db/schema";
import { getViewerJobLanguages } from "@/lib/actions/preferences";
import { isLocale, type Locale } from "@/lib/i18n";
import {
  PENDING_WATCHLIST_LIMIT,
  isPendingWatchlistIntent,
  type PendingWatchlistEntry,
  type PendingWatchlistIntent,
} from "@/lib/pending-watchlist";
import type { SearchWatchlistDraft } from "@/lib/search/watchlist-draft";
import { normalizeCreateWatchlistInput } from "@/lib/services/watchlist-input";
import { buildWatchlistPageData } from "@/lib/services/watchlist-page-data";
import {
  getSharedWatchlistById,
  getWatchlistActivityPreviewsForDrafts,
} from "@/lib/services/watchlists";
import { isWatchlistId } from "@/lib/watchlist-id";

type SessionWatchlistCompany = {
  id: string;
  name: string;
  slug: string;
  icon: string | null;
};

async function materializeSessionWatchlist(
  intent: PendingWatchlistIntent,
): Promise<{
  draft: SearchWatchlistDraft;
  sourceCompanies?: SessionWatchlistCompany[];
} | null> {
  if (intent.kind === "clone") {
    const source = await getSharedWatchlistById(intent.watchlistId);
    if (!source) return null;
    return {
      sourceCompanies: source.companies,
      draft: {
        title: intent.title ?? source.title,
        ...(source.description ? { description: source.description } : {}),
        companyIds: source.companies.map((candidate) => candidate.id),
        filters: source.filters,
        isPublic: false,
      },
    };
  }

  const normalized = normalizeCreateWatchlistInput(intent.draft);
  if (!normalized.ok || normalized.value.isPublic !== false) return null;
  return {
    draft: {
      ...normalized.value,
      description: normalized.value.description,
      filters: normalized.value.filters ?? {},
      isPublic: false,
    },
  };
}

async function hydrateCompanies(companyIds: string[]): Promise<SessionWatchlistCompany[]> {
  if (companyIds.length === 0) return [];
  const rows = await db
    .select({
      id: company.id,
      name: company.name,
      slug: company.slug,
      icon: company.icon,
    })
    .from(company)
    .where(inArray(company.id, companyIds));
  const byId = new Map(rows.map((candidate) => [candidate.id, candidate]));
  return companyIds
    .map((id) => byId.get(id))
    .filter((candidate): candidate is (typeof rows)[number] => candidate != null);
}

export async function getSessionWatchlistActivityPreviews(input: {
  entries: PendingWatchlistEntry[];
  locale: string;
}) {
  if (
    !isLocale(input.locale) ||
    !Array.isArray(input.entries) ||
    input.entries.length > PENDING_WATCHLIST_LIMIT ||
    input.entries.some((entry) => (
      !entry ||
      !isWatchlistId(entry.id) ||
      !isPendingWatchlistIntent(entry.intent)
    ))
  ) {
    return { error: "invalid_input" as const };
  }

  const [materialized, jobLanguages] = await Promise.all([
    Promise.all(input.entries.map(async (entry) => ({
      id: entry.id,
      watchlist: await materializeSessionWatchlist(entry.intent),
    }))),
    getViewerJobLanguages(),
  ]);
  const drafts = materialized.flatMap(({ id, watchlist }) => (
    watchlist
      ? [{
          id,
          filters: watchlist.draft.filters,
          companyIds: watchlist.draft.companyIds,
        }]
      : []
  ));

  return {
    previews: await getWatchlistActivityPreviewsForDrafts(
      drafts,
      input.locale,
      jobLanguages,
    ),
  };
}

export async function getSessionWatchlistPageData(input: {
  sessionWatchlistId: string;
  locale: Locale;
  intent: PendingWatchlistIntent;
}) {
  if (
    !isWatchlistId(input.sessionWatchlistId) ||
    !isPendingWatchlistIntent(input.intent)
  ) {
    return { error: "invalid_input" as const };
  }

  const materialized = await materializeSessionWatchlist(input.intent);
  if (!materialized) return { error: "not_found" as const };
  const { draft } = materialized;
  const companies = materialized.sourceCompanies ?? await hydrateCompanies(draft.companyIds);

  const jobLanguages = await getViewerJobLanguages();
  const data = await buildWatchlistPageData({
    detail: {
      id: input.sessionWatchlistId,
      title: draft.title,
      description: draft.description ?? null,
      filters: draft.filters,
      companies,
      alertsEnabled: false,
    },
    locale: input.locale,
    isOwner: true,
    limitReached: false,
    jobLanguages,
    publicSnapshot: false,
  });

  return { data, draft };
}
