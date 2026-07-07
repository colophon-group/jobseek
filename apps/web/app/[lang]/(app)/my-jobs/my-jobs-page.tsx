"use client";

import { useState, useEffect, useRef, useCallback, useMemo, useSyncExternalStore } from "react";
import { useSearchParams } from "next/navigation";
import Link from "next/link";
import { BarChart3, Briefcase, ChevronDown, ChevronRight } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  getMyJobs,
  type MyJobEntry,
  type ApplicationStatus,
} from "@/lib/actions/my-jobs";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { SortFilterBar, type SortBy, type GroupBy } from "@/components/my-jobs/sort-filter-bar";
import { MyJobRow } from "@/components/my-jobs/my-job-row";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";
import { MobileJobDetailDialog } from "@/components/search/mobile-job-detail-dialog";
import { useSavedJobs } from "@/components/providers/SavedJobsProvider";

const BATCH = 20;
const LS_KEY = "my-jobs-view";
const MY_JOBS_SKELETON_ROWS = [
  "job-row-skeleton-1",
  "job-row-skeleton-2",
  "job-row-skeleton-3",
  "job-row-skeleton-4",
  "job-row-skeleton-5",
] as const;

function useStatusGroupLabels(): Record<ApplicationStatus, string> {
  const { t } = useLingui();
  return {
    saved: t({ id: "myJobs.group.saved", comment: "Status group heading: saved jobs", message: "Saved" }),
    applied: t({ id: "myJobs.group.applied", comment: "Status group heading: applied jobs", message: "Applied" }),
    interviewing: t({ id: "myJobs.group.interviewing", comment: "Status group heading: interviewing", message: "Interviewing" }),
    offered: t({ id: "myJobs.group.offered", comment: "Status group heading: offered", message: "Offered" }),
    rejected: t({ id: "myJobs.group.rejected", comment: "Status group heading: rejected", message: "Rejected" }),
  };
}

const statusGroupOrder: ApplicationStatus[] = ["interviewing", "applied", "offered", "saved", "rejected"];

type ViewState = {
  sortBy: SortBy;
  groupBy: GroupBy;
  collapsed: Record<string, boolean>;
};

const DEFAULT_VIEW: ViewState = { sortBy: "status_changed_at", groupBy: "status", collapsed: {} };

// Cached so useSyncExternalStore returns stable references
let cachedViewState: ViewState | null = null;

function getViewSnapshot(): ViewState {
  if (cachedViewState) return cachedViewState;
  try {
    const raw = localStorage.getItem(LS_KEY);
    if (raw) cachedViewState = { ...DEFAULT_VIEW, ...JSON.parse(raw) };
  } catch {}
  return cachedViewState ?? DEFAULT_VIEW;
}

function getViewServerSnapshot(): ViewState {
  return DEFAULT_VIEW;
}

function subscribeViewState(_cb: () => void): () => void {
  // We drive updates imperatively via setViewState, not via storage events
  return () => {};
}

function saveViewState(state: ViewState) {
  cachedViewState = state;
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(state));
  } catch {}
}

export function MyJobsPage({
  initialJobs,
  initialTotal,
}: {
  initialJobs: MyJobEntry[];
  initialTotal: number;
}) {
  const searchParams = useSearchParams();
  const lp = useLocalePath();
  const statusGroupLabels = useStatusGroupLabels();
  const [jobs, setJobs] = useState(initialJobs);
  const [total, setTotal] = useState(initialTotal);
  const [exhausted, setExhausted] = useState(
    initialJobs.length >= initialTotal,
  );

  const [mounted, setMounted] = useState(false);
  const storedView = useSyncExternalStore(subscribeViewState, getViewSnapshot, getViewServerSnapshot);
  const [viewOverride, setViewOverride] = useState<Partial<ViewState>>({});
  const viewState: ViewState = { ...storedView, ...viewOverride };
  const { sortBy, groupBy, collapsed } = viewState;
  const sortDir = "desc" as const;

  useEffect(() => setMounted(true), []);

  // Persist to localStorage immediately, debounce server persist
  const serverTimerRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  function updateView(patch: Partial<ViewState>) {
    setViewOverride((prev) => {
      const next = { ...storedView, ...prev, ...patch };
      saveViewState(next);
      clearTimeout(serverTimerRef.current);
      serverTimerRef.current = setTimeout(() => {
        // Background server persist placeholder
      }, 2000);
      return { ...prev, ...patch };
    });
  }

  const [selectedJob, setSelectedJob] = useState<{
    savedJobId: string;
    postingId: string;
  } | null>(() => {
    const show = searchParams.get("show");
    if (show) {
      const job = initialJobs.find((j) => j.id === show);
      if (job) return { savedJobId: job.id, postingId: job.posting.id };
    }
    return null;
  });

  function updateUrl(showId: string | null) {
    const url = new URL(window.location.href);
    if (showId) {
      url.searchParams.set("show", showId);
    } else {
      url.searchParams.delete("show");
    }
    window.history.replaceState(null, "", url.toString());
  }

  function handleSelectJob(entry: MyJobEntry) {
    setSelectedJob({ savedJobId: entry.id, postingId: entry.posting.id });
    updateUrl(entry.id);
  }

  function handleCloseDetail() {
    setSelectedJob(null);
    updateUrl(null);
  }

  // Reload when sort/group changes
  const reload = useCallback(async () => {
    const { jobs: newJobs, total: newTotal } = await getMyJobs({
      offset: 0,
      limit: BATCH,
      sortBy,
      sortDir,
      groupByCompany: groupBy === "company",
    });
    setJobs(newJobs);
    setTotal(newTotal);
    setExhausted(newJobs.length >= newTotal);
  }, [sortBy, sortDir, groupBy]);

  const isInitialMount = useRef(true);
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    reload();
  }, [reload]);

  // Infinite scroll
  async function handleLoadMore() {
    const { jobs: more, total: newTotal } = await getMyJobs({
      offset: jobs.length,
      limit: BATCH,
      sortBy,
      sortDir,
      groupByCompany: groupBy === "company",
    });
    setTotal(newTotal);
    if (more.length > 0) {
      setJobs((prev) => {
        const seen = new Set(prev.map((j) => j.id));
        return [...prev, ...more.filter((j) => !seen.has(j.id))];
      });
    }
    if (more.length < BATCH) setExhausted(true);
  }

  const { sentinelRef, isLoading } = useInfiniteScroll({ hasMore: !exhausted, load: handleLoadMore });

  // Sync local jobs list when status changes via the detail panel
  const { onStatusChange } = useSavedJobs();
  useEffect(() => {
    return onStatusChange((postingId, newStatus) => {
      setJobs((prev) =>
        prev.map((j) =>
          j.posting.id === postingId
            ? { ...j, status: newStatus as ApplicationStatus, statusChangedAt: new Date().toISOString() }
            : j,
        ),
      );
    });
  }, [onStatusChange]);

  function toggleGroup(key: string) {
    updateView({ collapsed: { ...collapsed, [key]: !collapsed[key] } });
  }

  // Build groups
  const groups = useMemo(() => {
    const map = new Map<string, { label: string; jobs: MyJobEntry[] }>();

    if (groupBy === "company") {
      for (const job of jobs) {
        const key = job.company.id;
        if (!map.has(key)) map.set(key, { label: job.company.name, jobs: [] });
        map.get(key)!.jobs.push(job);
      }
    } else {
      // Group by status, in a meaningful order
      for (const status of statusGroupOrder) {
        const matching = jobs.filter((j) => j.status === status);
        if (matching.length > 0) {
          map.set(status, { label: statusGroupLabels[status], jobs: matching });
        }
      }
    }

    return Array.from(map.entries());
  }, [jobs, groupBy, statusGroupLabels]);

  if (jobs.length === 0 && !isLoading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center text-muted">
        <Briefcase size={32} />
        <p className="text-sm">
          <Trans
            id="myJobs.empty"
            comment="Empty state message when no tracked jobs"
          >
            No tracked jobs yet. Save jobs from search results to start tracking
            your applications.
          </Trans>
        </p>
      </div>
    );
  }

  const listColumn = (
    <Tooltip.Provider delayDuration={300} skipDelayDuration={100}>
    <div className="flex flex-col">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">
          <Trans id="myJobs.title" comment="Title of the My Jobs page">
            My Jobs
          </Trans>
        </h1>
        <div className="flex items-center gap-2">
          <span className="text-xs text-muted">{total}</span>
          <Link
            href={lp("/my-jobs/stats")}
            className="inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-xs text-muted transition-colors hover:bg-border-soft hover:text-foreground"
          >
            <BarChart3 size={14} />
            <Trans id="myJobs.viewStats" comment="Link to application stats page">Application stats</Trans>
          </Link>
        </div>
      </div>

      {!mounted ? (
        <div className="mt-3 animate-pulse space-y-3">
          <div className="flex gap-2">
            <div className="h-7 w-32 rounded-md bg-border-soft" />
            <div className="h-7 w-24 rounded-md bg-border-soft" />
          </div>
          {MY_JOBS_SKELETON_ROWS.map((slot) => (
            <div key={slot} className="flex items-center gap-2.5 px-2 py-2">
              <div className="size-6 rounded bg-border-soft" />
              <div className="h-4 w-20 rounded bg-border-soft" />
              <div className="h-4 flex-1 rounded bg-border-soft" />
              <div className="h-3 w-8 rounded bg-border-soft" />
              <div className="size-2 rounded-full bg-border-soft" />
            </div>
          ))}
        </div>
      ) : (
      <>
      <SortFilterBar
        sortBy={sortBy}
        onSortChange={(s) => updateView({ sortBy: s })}
        groupBy={groupBy}
        onGroupByChange={(g) => updateView({ groupBy: g })}
      />

      <div className="mt-3">
        {groups.map(([key, group]) => {
          const isCollapsed = !!collapsed[key];
          return (
            <div key={key}>
              <button
                onClick={() => toggleGroup(key)}
                className="flex w-full cursor-pointer items-center gap-1.5 rounded px-2 py-1.5 text-left transition-colors hover:bg-border-soft/50"
              >
                {isCollapsed ? <ChevronRight size={12} className="shrink-0 text-muted" /> : <ChevronDown size={12} className="shrink-0 text-muted" />}
                <span className="text-[11px] font-semibold uppercase tracking-wide text-muted">{group.label}</span>
                <span className="text-[10px] text-muted">{group.jobs.length}</span>
              </button>
              {!isCollapsed && (
                <div className="space-y-0.5">
                  {group.jobs.map((entry) => (
                    <MyJobRow
                      key={entry.id}
                      entry={entry}
                      isSelected={selectedJob?.savedJobId === entry.id}
                      onSelect={() => handleSelectJob(entry)}
                    />
                  ))}
                </div>
              )}
            </div>
          );
        })}

        {!exhausted && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} />}
      </div>
      </>
      )}
    </div>
    </Tooltip.Provider>
  );

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{listColumn}</div>
      {selectedJob && (
        <>
          <div className="hidden w-[420px] shrink-0 lg:block" aria-hidden="true" />
          <div
            className="fixed top-[4.5rem] z-40 hidden w-[420px] lg:block"
            style={{ right: "max(1rem, calc((100vw - 1200px) / 2 + 1rem))", height: "calc(100vh - 5.5rem)" }}
          >
            <JobDetailPanel
              postingId={selectedJob.postingId}
              onClose={handleCloseDetail}
            />
          </div>
          <MobileJobDetailDialog
            postingId={selectedJob.postingId}
            onClose={handleCloseDetail}
          />
        </>
      )}
    </div>
  );
}
