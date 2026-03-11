"use client";

import { useState, useEffect, useRef, useCallback } from "react";
import Image from "next/image";
import { Building2, Bookmark, Loader2 } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { timeAgoShort } from "@/lib/time";
import { getSavedJobs, type SavedJobEntry } from "@/lib/actions/saved-jobs";
import { useSavedJobs } from "@/components/SavedJobsProvider";

const BATCH = 20;

export function SavedPage({
  initialJobs,
  initialTotal,
}: {
  initialJobs: SavedJobEntry[];
  initialTotal: number;
}) {
  const [jobs, setJobs] = useState(initialJobs);
  const [_total, setTotal] = useState(initialTotal);
  const [isLoading, setIsLoading] = useState(false);
  const [exhausted, setExhausted] = useState(initialJobs.length >= initialTotal);
  const scrollRef = useRef<HTMLDivElement>(null);
  const loadingRef = useRef(false);
  const { isSaved, toggle } = useSavedJobs();

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

  return (
    <div>
      <h1 className="mb-4 text-lg font-semibold">
        <Trans id="saved.title" comment="Title of the saved jobs page">
          Saved jobs
        </Trans>
      </h1>

      <div ref={scrollRef} className="space-y-1">
        {visibleJobs.map((entry) => (
          <div
            key={entry.id}
            className="flex items-center gap-3 rounded-md px-2 py-2 transition-colors hover:bg-border-soft"
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

            {/* Job title (links to source) */}
            <a
              href={entry.posting.sourceUrl}
              target="_blank"
              rel="noopener noreferrer"
              className="min-w-0 flex-1 truncate text-sm hover:underline"
            >
              {entry.posting.title ?? "—"}
            </a>

            {/* Save/unsave toggle */}
            <button
              onClick={() => toggle(entry.posting.id)}
              className="shrink-0 cursor-pointer text-muted transition-opacity hover:opacity-70"
              aria-label={isSaved(entry.posting.id) ? "Unsave job" : "Save job"}
            >
              <Bookmark
                size={14}
                className={isSaved(entry.posting.id) ? "fill-current" : ""}
              />
            </button>

            {/* Time since posted */}
            <span
              suppressHydrationWarning
              className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted"
            >
              {timeAgoShort(entry.posting.firstSeenAt)}
            </span>
          </div>
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
}
