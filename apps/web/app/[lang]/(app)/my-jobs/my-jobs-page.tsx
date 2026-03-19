"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import { useSearchParams } from "next/navigation";
import { Briefcase, Loader2 } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import {
  getMyJobs,
  updateJobStatus,
  addInterview,
  type MyJobEntry,
  type ApplicationStatus,
} from "@/lib/actions/my-jobs";
import { SortFilterBar } from "@/components/my-jobs/sort-filter-bar";
import { MyJobRow } from "@/components/my-jobs/my-job-row";
import { MyJobDetailPanel } from "@/components/my-jobs/my-job-detail-panel";

type SortBy = "status_changed_at" | "saved_at" | "status" | "company_name";

const BATCH = 20;

export function MyJobsPage({
  initialJobs,
  initialTotal,
}: {
  initialJobs: MyJobEntry[];
  initialTotal: number;
}) {
  const searchParams = useSearchParams();
  const [jobs, setJobs] = useState(initialJobs);
  const [total, setTotal] = useState(initialTotal);
  const [isLoading, setIsLoading] = useState(false);
  const [exhausted, setExhausted] = useState(
    initialJobs.length >= initialTotal,
  );

  const [sortBy, setSortBy] = useState<SortBy>("status_changed_at");
  const [sortDir] = useState<"asc" | "desc">("desc");
  const [statusFilter, setStatusFilter] = useState<ApplicationStatus[]>([]);
  const [groupByCompany, setGroupByCompany] = useState(false);

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

  const scrollRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(false);

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

  // Reload when sort/filter changes
  const reload = useCallback(async () => {
    setIsLoading(true);
    try {
      const { jobs: newJobs, total: newTotal } = await getMyJobs({
        offset: 0,
        limit: BATCH,
        sortBy,
        sortDir,
        statusFilter: statusFilter.length > 0 ? statusFilter : undefined,
        groupByCompany,
      });
      setJobs(newJobs);
      setTotal(newTotal);
      setExhausted(newJobs.length >= newTotal);
    } finally {
      setIsLoading(false);
    }
  }, [sortBy, sortDir, statusFilter, groupByCompany]);

  // Watch for filter/sort changes and reload
  const isInitialMount = useRef(true);
  useEffect(() => {
    if (isInitialMount.current) {
      isInitialMount.current = false;
      return;
    }
    reload();
  }, [reload]);

  // Infinite scroll
  const handleLoadMore = useCallback(() => {
    if (loadingRef.current || exhausted) return;
    loadingRef.current = true;
    setIsLoading(true);

    getMyJobs({
      offset: jobs.length,
      limit: BATCH,
      sortBy,
      sortDir,
      statusFilter: statusFilter.length > 0 ? statusFilter : undefined,
      groupByCompany,
    })
      .then(({ jobs: more, total: newTotal }) => {
        setTotal(newTotal);
        if (more.length > 0) {
          setJobs((prev) => {
            const seen = new Set(prev.map((j) => j.id));
            return [...prev, ...more.filter((j) => !seen.has(j.id))];
          });
        }
        if (more.length < BATCH) setExhausted(true);
      })
      .finally(() => {
        setIsLoading(false);
        loadingRef.current = false;
      });
  }, [jobs.length, exhausted, sortBy, sortDir, statusFilter, groupByCompany]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = () => {
      if (loadingRef.current || exhausted) return;
      const nearBottom =
        el.scrollHeight - el.scrollTop - el.clientHeight < 100;
      if (nearBottom) handleLoadMore();
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [handleLoadMore, exhausted]);

  // Optimistic status update
  async function handleQuickStatusChange(
    entry: MyJobEntry,
    newStatus: ApplicationStatus,
  ) {
    const prevJobs = jobs;
    // Optimistic update
    setJobs((prev) =>
      prev.map((j) =>
        j.id === entry.id
          ? {
              ...j,
              status: newStatus,
              statusChangedAt: new Date().toISOString(),
            }
          : j,
      ),
    );

    const result = await updateJobStatus(entry.id, newStatus);
    if (!result.ok) {
      // Revert on error
      setJobs(prevJobs);
    }
  }

  async function handleQuickAddInterview(entry: MyJobEntry) {
    const result = await addInterview(entry.id, "phone_screen");
    if (result.ok) {
      // Reload to get updated data
      setJobs((prev) =>
        prev.map((j) =>
          j.id === entry.id
            ? {
                ...j,
                status: "interviewing" as ApplicationStatus,
                statusChangedAt: new Date().toISOString(),
                interviewCount: j.interviewCount + 1,
              }
            : j,
        ),
      );
    }
  }

  function handleDetailStatusChanged(
    savedJobId: string,
    newStatus: ApplicationStatus,
  ) {
    setJobs((prev) =>
      prev.map((j) =>
        j.id === savedJobId
          ? {
              ...j,
              status: newStatus,
              statusChangedAt: new Date().toISOString(),
            }
          : j,
      ),
    );
  }

  // Group jobs by company when enabled
  function renderJobList() {
    if (groupByCompany) {
      const groups = new Map<string, MyJobEntry[]>();
      for (const job of jobs) {
        const key = job.company.id;
        if (!groups.has(key)) groups.set(key, []);
        groups.get(key)!.push(job);
      }

      return Array.from(groups.entries()).map(([companyId, companyJobs]) => (
        <div key={companyId}>
          <div className="sticky top-0 z-10 bg-surface px-2 py-1.5 text-[11px] font-semibold uppercase tracking-wide text-muted">
            {companyJobs[0].company.name}
          </div>
          {companyJobs.map((entry) => (
            <MyJobRow
              key={entry.id}
              entry={entry}
              isSelected={selectedJob?.savedJobId === entry.id}
              onSelect={() => handleSelectJob(entry)}
              onStatusChange={(s) => handleQuickStatusChange(entry, s)}
              onAddInterview={() => handleQuickAddInterview(entry)}
            />
          ))}
        </div>
      ));
    }

    return jobs.map((entry) => (
      <MyJobRow
        key={entry.id}
        entry={entry}
        isSelected={selectedJob?.savedJobId === entry.id}
        onSelect={() => handleSelectJob(entry)}
        onStatusChange={(s) => handleQuickStatusChange(entry, s)}
        onAddInterview={() => handleQuickAddInterview(entry)}
      />
    ));
  }

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
    <div className="flex flex-col">
      <div className="mb-3 flex items-center justify-between">
        <h1 className="text-lg font-semibold">
          <Trans id="myJobs.title" comment="Title of the My Jobs page">
            My Jobs
          </Trans>
        </h1>
        <span className="text-xs text-muted">{total}</span>
      </div>

      <SortFilterBar
        sortBy={sortBy}
        onSortChange={(s) => setSortBy(s)}
        statusFilter={statusFilter}
        onStatusFilterChange={setStatusFilter}
        groupByCompany={groupByCompany}
        onGroupByCompanyChange={setGroupByCompany}
      />

      <div ref={scrollRef} className="mt-3 space-y-0.5">
        <Tooltip.Provider delayDuration={300} skipDelayDuration={100}>
          {renderJobList()}
        </Tooltip.Provider>

        {!exhausted && (
          <div className="flex h-8 items-center justify-center">
            {isLoading && (
              <Loader2 size={14} className="animate-spin text-muted" />
            )}
          </div>
        )}
      </div>
    </div>
  );

  if (!selectedJob) {
    return listColumn;
  }

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{listColumn}</div>
      <div className="hidden w-[420px] shrink-0 lg:block">
        <MyJobDetailPanel
          savedJobId={selectedJob.savedJobId}
          postingId={selectedJob.postingId}
          onClose={handleCloseDetail}
          onStatusChanged={handleDetailStatusChanged}
        />
      </div>
      {/* On small screens, show as an overlay */}
      <div
        className="fixed inset-0 z-50 bg-black/40 lg:hidden"
        onClick={handleCloseDetail}
      >
        <div
          className="absolute inset-y-0 right-0 w-full max-w-lg bg-surface shadow-xl"
          onClick={(e) => e.stopPropagation()}
        >
          <MyJobDetailPanel
            savedJobId={selectedJob.savedJobId}
            postingId={selectedJob.postingId}
            onClose={handleCloseDetail}
            onStatusChanged={handleDetailStatusChanged}
          />
        </div>
      </div>
    </div>
  );
}
