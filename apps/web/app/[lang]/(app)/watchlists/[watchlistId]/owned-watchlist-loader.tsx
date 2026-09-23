import Link from "next/link";
import { notFound } from "next/navigation";
import { ChevronLeft } from "lucide-react";
import { getSession } from "@/lib/sessionCache";
import {
  getOwnedWatchlistById,
  getSharedWatchlistById,
  type WatchlistViewDetail,
} from "@/lib/services/watchlists";
import { buildWatchlistPageData } from "@/lib/services/watchlist-page-data";
import { getViewerJobLanguages } from "@/lib/actions/preferences";
import { canCreateWatchlist } from "@/lib/plans";
import { isWatchlistId } from "@/lib/watchlist-id";
import type { Locale } from "@/lib/i18n";
import {
  AiFilterNotFoundError,
  getAiFilterOwnerState,
  getSharedAiFilterState,
} from "@/lib/ai-filter/configuration-service";
import {
  listAiFilterDecisions,
  listSharedAiFilterDecisions,
} from "@/lib/ai-filter/decision-service";
import { AiFilterCandidateLoadError } from "@/lib/ai-filter/candidate-loader";
import type {
  AiFilterAcceptedPage,
  AiFilterUiState,
} from "@/lib/ai-filter/ui-contract";
import { WatchlistViewPage } from "@/components/watchlist/watchlist-view-page";
import { SessionWatchlistLoader } from "./session-watchlist-loader";

function viewDetail(
  detail: WatchlistViewDetail,
  isOwner: boolean,
): WatchlistViewDetail {
  return {
    id: detail.id,
    title: detail.title,
    description: detail.description,
    filters: detail.filters,
    companies: detail.companies,
    ...(isOwner ? { alertsEnabled: detail.alertsEnabled === true } : {}),
  };
}

async function getOptionalAiFilterState(input: {
  ownerId: string;
  watchlistId: string;
}): Promise<AiFilterUiState | null> {
  try {
    return await getAiFilterOwnerState(input);
  } catch (error) {
    if (error instanceof AiFilterNotFoundError) return null;
    throw error;
  }
}

async function getOptionalSharedAiFilterState(input: {
  watchlistId: string;
}): Promise<AiFilterUiState | null> {
  try {
    return await getSharedAiFilterState(input);
  } catch (error) {
    if (error instanceof AiFilterNotFoundError) return null;
    throw error;
  }
}

async function getInitialAcceptedPage(input: {
  ownerId?: string;
  watchlistId: string;
  state: AiFilterUiState | null;
}): Promise<AiFilterAcceptedPage | null> {
  if (!input.state?.enabled) return null;
  try {
    const page = input.ownerId
      ? await listAiFilterDecisions({
          ownerId: input.ownerId,
          watchlistId: input.watchlistId,
          bucket: "accepted",
          offset: 0,
          limit: 20,
        })
      : await listSharedAiFilterDecisions({
          watchlistId: input.watchlistId,
          offset: 0,
          limit: 20,
        });
    return {
      queryVersionId: input.state.queryVersionId,
      postings: page.decisions.map((decision) => decision.posting),
      total: page.total,
      nextOffset: page.nextOffset,
      hasMore: page.hasMore,
    };
  } catch (error) {
    // The watchlist shell and persisted request remain useful while the
    // ordered candidate reader is unavailable. The client result surface
    // will make one demand attempt and render its unavailable state instead
    // of turning a search dependency outage into a route-level crash.
    if (error instanceof AiFilterCandidateLoadError) return null;
    throw error;
  }
}

export async function OwnedWatchlistLoader({
  locale,
  watchlistId,
  overviewLabel,
}: {
  locale: Locale;
  watchlistId: string;
  overviewLabel: string;
}) {
  if (!isWatchlistId(watchlistId)) notFound();

  const session = await getSession();
  const ownedDetail = session
    ? await getOwnedWatchlistById(watchlistId, session.user.id)
    : null;
  const sharedDetail = ownedDetail
    ? null
    : await getSharedWatchlistById(watchlistId);
  const detail = ownedDetail ?? sharedDetail;
  if (!detail) {
    return (
      <SessionWatchlistLoader
        locale={locale}
        watchlistId={watchlistId}
        overviewLabel={overviewLabel}
      />
    );
  }
  const isOwner = ownedDetail !== null;

  const [jobLanguages, limit, aiFilterState] = await Promise.all([
    isOwner
      ? getViewerJobLanguages()
      : Promise.resolve(sharedDetail!.ownerJobLanguages),
    session
      ? canCreateWatchlist(session.user.id)
      : Promise.resolve({ allowed: true }),
    isOwner && session
      ? getOptionalAiFilterState({
          ownerId: session.user.id,
          watchlistId,
        })
      : getOptionalSharedAiFilterState({ watchlistId }),
  ]);

  const [data, initialAiAcceptedPage] = await Promise.all([
    buildWatchlistPageData({
      detail: viewDetail(detail, isOwner),
      locale,
      isOwner,
      limitReached: !limit.allowed,
      jobLanguages,
      // Shared is a permissions mode, not an anonymous-viewer mode. Signed-in
      // non-owners can browse the complete feed just like any other signed-in
      // viewer; only a genuinely anonymous request uses the capped snapshot.
      publicSnapshot: !isOwner && !session,
    }),
    getInitialAcceptedPage({
      ownerId: isOwner && session ? session.user.id : undefined,
      watchlistId,
      state: aiFilterState,
    }),
  ]);

  return (
    <div className="space-y-5">
      <Link
        href={`/${locale}/watchlists`}
        prefetch={false}
        className="inline-flex items-center gap-1.5 text-sm font-medium text-muted transition-colors hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-background motion-reduce:transition-none"
      >
        <ChevronLeft size={16} aria-hidden="true" />
        {overviewLabel}
      </Link>
      <WatchlistViewPage
        data={data}
        locale={locale}
        initialAiFilterState={aiFilterState}
        initialAiAcceptedPage={initialAiAcceptedPage}
      />
    </div>
  );
}
