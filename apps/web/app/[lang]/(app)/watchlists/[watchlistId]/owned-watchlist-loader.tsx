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
import { WatchlistViewPage } from "../../[userSlug]/[watchlistSlug]/watchlist-view-page";

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
  const detail = ownedDetail ?? await getSharedWatchlistById(watchlistId);
  if (!detail) notFound();
  const isOwner = ownedDetail !== null;

  const [jobLanguages, limit] = await Promise.all([
    getViewerJobLanguages(),
    session
      ? canCreateWatchlist(session.user.id)
      : Promise.resolve({ allowed: true }),
  ]);

  const data = await buildWatchlistPageData({
    detail: viewDetail(detail, isOwner),
    locale,
    isOwner,
    limitReached: !limit.allowed,
    jobLanguages,
    publicSnapshot: !isOwner,
  });

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
        detail={data.detail}
        isOwner={isOwner}
        limitReached={data.limitReached}
        initialPostings={data.postings}
        initialTotal={data.total}
        yearTotal={data.yearTotal}
        initialSearchUnavailable={data.searchUnavailable}
        locale={locale}
        resolvedLocations={data.resolvedLocations}
        resolvedOccupations={data.resolvedOccupations}
        resolvedSeniorities={data.resolvedSeniorities}
        resolvedTechnologies={data.resolvedTechnologies}
        jobLanguages={data.jobLanguages}
        languages={data.languages}
        initialPostingFilters={data.browserPostingFilters ?? null}
      />
    </div>
  );
}
