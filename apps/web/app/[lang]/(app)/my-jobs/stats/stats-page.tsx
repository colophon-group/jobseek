"use client";

import { useState, useCallback } from "react";
import { Trans } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { BackLink } from "@/components/BackLink";
import { SankeyFunnel } from "@/components/my-jobs/sankey-funnel-lazy";
import { ActivityHeatmap } from "@/components/my-jobs/activity-heatmap";
import { getStats, type StatsData } from "@/lib/actions/my-jobs-stats";
import { getViewerTz } from "@/lib/viewer-tz";

export function StatsPage({ initial }: { initial: StatsData }) {
  const lp = useLocalePath();
  const [data, setData] = useState(initial);
  const [from, setFrom] = useState("");
  const [to, setTo] = useState("");
  const [loading, setLoading] = useState(false);

  const handleFilter = useCallback(async (newFrom: string, newTo: string) => {
    setLoading(true);
    try {
      // Pass the viewer's resolved IANA timezone so day bucketing and
      // the `from`/`to` calendar-day filter both resolve at the user's
      // local midnight, not Postgres-server midnight. See #3199.
      const result = await getStats({
        from: newFrom || undefined,
        to: newTo || undefined,
        tz: getViewerTz(),
      });
      setData(result);
    } finally {
      setLoading(false);
    }
  }, []);

  return (
    <div className="space-y-6">
      <div>
        <BackLink href={lp("/my-jobs")}>
          <Trans id="myJobs.stats.back" comment="Back link to my jobs">My Jobs</Trans>
        </BackLink>
        <h1 className="mt-1 text-lg font-semibold">
          <Trans id="myJobs.stats.title" comment="Stats page title">
            Application Stats
          </Trans>
        </h1>
      </div>

      {/* Activity heatmap */}
      <section>
        <p className="mb-3 text-xs text-muted">
          {data.activityTotal} <Trans id="myJobs.stats.activitySubtitle" comment="Subtitle showing total applications in past year">applications in the past year</Trans>
        </p>
        <ActivityHeatmap data={data.activity} />
      </section>

      {/* Period-based stats */}
      <section className="rounded-md border border-divider bg-surface p-4 space-y-5">
        {/* Date range filter */}
        <div className="flex flex-wrap items-center gap-2">
          <span className="text-xs font-semibold">
            <Trans id="myJobs.stats.period" comment="Period filter heading">Period</Trans>
          </span>
          <input
            type="date"
            value={from}
            onChange={(e) => {
              setFrom(e.target.value);
              handleFilter(e.target.value, to);
            }}
            className="rounded-md border border-border-soft bg-surface px-2.5 py-1.5 text-xs"
          />
          <span className="text-xs text-muted">–</span>
          <input
            type="date"
            value={to}
            onChange={(e) => {
              setTo(e.target.value);
              handleFilter(from, e.target.value);
            }}
            className="rounded-md border border-border-soft bg-surface px-2.5 py-1.5 text-xs"
          />
          {(from || to) && (
            <button
              onClick={() => {
                setFrom("");
                setTo("");
                handleFilter("", "");
              }}
              className="cursor-pointer rounded-md px-2 py-1.5 text-xs text-muted transition-colors hover:bg-border-soft hover:text-foreground"
            >
              <Trans id="myJobs.stats.clearDates" comment="Clear date filter">Clear</Trans>
            </button>
          )}
          {loading && (
            <span className="text-[10px] text-muted">
              <Trans id="myJobs.stats.loading" comment="Loading indicator">Loading…</Trans>
            </span>
          )}
        </div>

        {/* Pipeline Sankey */}
        <div>
          {data.funnel.saved === 0 ? (
            <p className="py-8 text-center text-sm text-muted">
              <Trans id="myJobs.stats.empty" comment="Empty state for stats">
                Save some jobs and track your applications to see stats here.
              </Trans>
            </p>
          ) : (
            <SankeyFunnel data={data.funnel} />
          )}
        </div>
      </section>
    </div>
  );
}
