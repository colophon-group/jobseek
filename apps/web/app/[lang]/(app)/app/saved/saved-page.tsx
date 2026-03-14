"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import { useSearchParams } from "next/navigation";
import { Building2, Bookmark, Loader2 } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { timeAgoShort } from "@/lib/time";
import { getSavedJobs, type SavedJobEntry } from "@/lib/actions/saved-jobs";
import { useSavedJobs } from "@/components/SavedJobsProvider";
import { JobDetailPanel } from "@/components/search/job-detail-dialog";

const BATCH = 20;

export function SavedPage({
  initialJobs,
  initialTotal,
}: {
  initialJobs: SavedJobEntry[];
  initialTotal: number;
}) {
  const { t } = useLingui();
  const searchParams = useSearchParams();
  const [jobs, setJobs] = useState(initialJobs);
  const [_total, setTotal] = useState(initialTotal);
  const [isLoading, setIsLoading] = useState(false);
  const [exhausted, setExhausted] = useState(initialJobs.length >= initialTotal);
  const [showPostingId, setShowPostingId] = useState<string | null>(
    searchParams.get("show"),
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(false);
  const { isSaved, toggle } = useSavedJobs();

  function updateUrl(showId: string | null) {
    const url = new URL(window.location.href);
    if (showId) {
      url.searchParams.set("show", showId);
    } else {
      url.searchParams.delete("show");
    }
    window.history.replaceState(null, "", url.toString());
  }

  function handleOpenPosting(postingId: string) {
    setShowPostingId(postingId);
    updateUrl(postingId);
  }

  function handleClosePosting() {
    setShowPostingId(null);
    updateUrl(null);
  }

  // Keep unsaved jobs visible until reload/navigation so the user can re-save
  const visibleJobs = jobs;

  const handleLoadMore = useCallback(() => {
    if (loadingRef.current || exhausted) return;
    loadingRef.current = true;
    setIsLoading(true);

    getSavedJobs({ offset: jobs.length, limit: BATCH })
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
  }, [jobs.length, exhausted]);

  useEffect(() => {
    const el = scrollRef.current;
    if (!el) return;

    const onScroll = () => {
      if (loadingRef.current || exhausted) return;
      const nearBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 100;
      if (nearBottom) handleLoadMore();
    };

    el.addEventListener("scroll", onScroll, { passive: true });
    return () => el.removeEventListener("scroll", onScroll);
  }, [handleLoadMore, exhausted]);

  if (visibleJobs.length === 0 && !isLoading) {
    return (
      <div className="flex flex-col items-center gap-3 py-16 text-center text-muted">
        <Bookmark size={32} />
        <p className="text-sm">
          <Trans
            id="saved.empty"
            comment="Empty state message when no jobs are saved"
          >
            No saved jobs yet. Save jobs from search results to find them here.
          </Trans>
        </p>
      </div>
    );
  }

  const listColumn = (
    <div>
      <h1 className="mb-4 text-lg font-semibold">
        <Trans id="saved.title" comment="Title of the saved jobs page">
          Saved jobs
        </Trans>
      </h1>

      <div ref={scrollRef} className="space-y-1">
        {visibleJobs.map((entry) => (
          <button
            key={entry.id}
            type="button"
            onClick={() => handleOpenPosting(entry.posting.id)}
            className={`flex w-full items-center gap-3 rounded-md px-2 py-2 text-left transition-colors hover:bg-border-soft ${
              showPostingId === entry.posting.id ? "bg-border-soft" : ""
            }`}
          >
            {/* Company icon */}
            {entry.company.icon ? (
              <Image
                src={entry.company.icon}
                alt={entry.company.name}
                width={24}
                height={24}
                className="size-6 shrink-0 rounded"
              />
            ) : (
              <div className="flex size-6 shrink-0 items-center justify-center rounded bg-border-soft text-muted">
                <Building2 size={14} />
              </div>
            )}

            {/* Company name */}
            <span className="shrink-0 text-xs text-muted">
              {entry.company.name}
            </span>

            {/* Job title */}
            <span className="min-w-0 flex-1 truncate text-sm">
              {entry.posting.title ?? "—"}
            </span>

            {/* Save/unsave toggle */}
            <span
              role="button"
              onClick={(e) => {
                e.stopPropagation();
                toggle(entry.posting.id);
              }}
              className="shrink-0 cursor-pointer text-muted transition-opacity hover:opacity-70"
              aria-label={isSaved(entry.posting.id) ? t({ id: "saved.unsave.ariaLabel", comment: "Aria label for unsave button", message: "Unsave job" }) : t({ id: "saved.save.ariaLabel", comment: "Aria label for save button", message: "Save job" })}
            >
              <Bookmark
                size={14}
                className={isSaved(entry.posting.id) ? "fill-current" : ""}
              />
            </span>

            {/* Time since posted */}
            <span
              suppressHydrationWarning
              className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted"
            >
              {timeAgoShort(entry.posting.firstSeenAt)}
            </span>
          </button>
        ))}

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

  if (!showPostingId) {
    return listColumn;
  }

  return (
    <div className="flex gap-5">
      <div className="min-w-0 flex-1">{listColumn}</div>
      <div className="hidden w-[420px] shrink-0 lg:block">
        <JobDetailPanel postingId={showPostingId} onClose={handleClosePosting} />
      </div>
      {/* On small screens, show as an overlay */}
      <div className="fixed inset-0 z-50 bg-black/40 lg:hidden" onClick={handleClosePosting}>
        <div
          className="absolute inset-y-0 right-0 w-full max-w-lg bg-surface shadow-xl"
          onClick={(e) => e.stopPropagation()}
        >
          <JobDetailPanel postingId={showPostingId} onClose={handleClosePosting} />
        </div>
      </div>
    </div>
  );
}
