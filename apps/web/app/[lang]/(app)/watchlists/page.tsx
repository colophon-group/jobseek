import { initI18nForPage } from "@/lib/i18n";
import { getUserWatchlists, type WatchlistSummary } from "@/lib/actions/watchlists";
import { getSession } from "@/lib/sessionCache";
import { canCreateWatchlist } from "@/lib/plans";
import { WatchlistsPage } from "./watchlists-page";

type Props = {
  params: Promise<{ lang: string }>;
};

export default async function WatchlistsRoute({ params }: Props) {
  await initI18nForPage(params);
  const session = await getSession();

  const [watchlists, limit] = session
    ? await Promise.all([
        getUserWatchlists(),
        canCreateWatchlist(session.user.id),
      ])
    : [[] as WatchlistSummary[], { allowed: false, current: 0, max: 0 }];

  const username = session?.user?.username ?? null;

  return (
    <WatchlistsPage
      initialWatchlists={watchlists}
      username={username}
      limitReached={!limit.allowed}
    />
  );
}
