import { cookies } from "next/headers";
import { ViewerTimezoneCookie } from "@/components/ViewerTimezoneCookie";
import { getMyJobsStats, type StatsData } from "@/lib/actions/my-jobs-stats";
import {
  isValidViewerTimeZone,
  VIEWER_TIME_ZONE_COOKIE,
} from "@/lib/viewer-tz";
import { StatsPage } from "./stats-page";

/**
 * Load stats in the initial server response using the browser-maintained IANA
 * timezone cookie. A direct first visit has no trustworthy timezone yet, so
 * it renders the normal spinner while the cookie component writes the browser
 * value and performs one RSC refresh. It never renders UTC-bucketed data into
 * a non-UTC browser heatmap and never needs a mount-time Server Action.
 */
export async function StatsLoader({ locale: _locale }: { locale: string }) {
  const cookieStore = await cookies();
  const timeZone = cookieStore.get(VIEWER_TIME_ZONE_COOKIE)?.value;

  if (!isValidViewerTimeZone(timeZone)) {
    return (
      <>
        <ViewerTimezoneCookie serverTimeZone={null} refreshWhenChanged />
        <div className="flex items-center justify-center py-24">
          <div className="h-8 w-8 animate-spin rounded-full border-4 border-muted border-t-primary" />
        </div>
      </>
    );
  }

  const data: StatsData = await getMyJobsStats({ tz: timeZone });

  return (
    <>
      <ViewerTimezoneCookie
        serverTimeZone={timeZone}
        refreshWhenChanged
      />
      {/*
       * router.refresh() preserves mounted Client Component state. Key the
       * stateful stats subtree by the validated bucket timezone so a viewer
       * who moved zones cannot keep the previous heatmap after the refreshed
       * RSC payload arrives.
       */}
      <StatsPage key={timeZone} initial={data} />
    </>
  );
}
