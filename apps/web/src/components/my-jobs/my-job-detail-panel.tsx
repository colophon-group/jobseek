"use client";

import { useEffect, useState, useCallback } from "react";
import Image from "next/image";
import Link from "next/link";
import { Building2, X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import { useLocalePath } from "@/lib/useLocalePath";
import { getPostingDetail } from "@/lib/actions/search";
import type { PostingDetail } from "@/lib/actions/search";
import {
  getMyJobDetail,
  updateJobStatus,
  addInterview,
  updateInterview,
  deleteInterview,
} from "@/lib/actions/my-jobs";
import {
  APPLICATION_STATUSES,
  type MyJobDetail,
  type ApplicationStatus,
  type InterviewType,
} from "@/lib/actions/my-jobs-types";
import { StatusBadge } from "./status-badge";
import { withUtmSource } from "@/lib/utm";
import { sanitizeJobHtml } from "@/lib/sanitize";
import { InterviewList } from "./interview-list";
import { timeAgoShort } from "@/lib/time";
import { SaveButton } from "@/components/search/save-button";
import { ScrollFade } from "@/components/ui/scroll-fade";

function useStatusOptionLabels(): Record<ApplicationStatus, string> {
  const { t } = useLingui();
  return {
    saved: t({ id: "myJobs.statusOption.saved", comment: "Status option in dropdown: saved", message: "Saved" }),
    applied: t({ id: "myJobs.statusOption.applied", comment: "Status option in dropdown: applied", message: "Applied" }),
    interviewing: t({ id: "myJobs.statusOption.interviewing", comment: "Status option in dropdown: interviewing", message: "Interviewing" }),
    offered: t({ id: "myJobs.statusOption.offered", comment: "Status option in dropdown: offered", message: "Offered" }),
    rejected: t({ id: "myJobs.statusOption.rejected", comment: "Status option in dropdown: rejected", message: "Rejected" }),
  };
}

interface MyJobDetailPanelProps {
  savedJobId: string;
  postingId: string;
  onClose: () => void;
  onStatusChanged?: (savedJobId: string, newStatus: ApplicationStatus) => void;
}

export function MyJobDetailPanel({
  savedJobId,
  postingId,
  onClose,
  onStatusChanged,
}: MyJobDetailPanelProps) {
  const [postingDetail, setPostingDetail] = useState<PostingDetail | null>(
    null,
  );
  const [jobDetail, setJobDetail] = useState<MyJobDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  const { t } = useLingui();
  const statusOptionLabels = useStatusOptionLabels();
  const changePlaceholder = t({ id: "myJobs.detail.changePlaceholder", comment: "Placeholder in status change dropdown", message: "Change..." });

  const loadData = useCallback(async () => {
    setLoading(true);
    setError(false);
    const locale = document.documentElement.lang || "en";

    try {
      const [posting, detail] = await Promise.all([
        getPostingDetail({ postingId, locale }),
        getMyJobDetail(savedJobId),
      ]);

      if (!posting || !detail) {
        setError(true);
        return;
      }

      // Show structured data immediately
      setPostingDetail(posting);
      setJobDetail(detail);
      setLoading(false);

      // Fetch description in background
      if (posting.descriptionUrl && !posting.descriptionHtml) {
        fetch(posting.descriptionUrl)
          .then((r) => r.ok ? r.text() : null)
          .then((html) => {
            if (!html) return;
            setPostingDetail((prev) => prev ? { ...prev, descriptionHtml: html } : prev);
          })
          .catch(() => {});
      }
      return;
    } catch {
      setError(true);
    } finally {
      setLoading(false);
    }
  }, [postingId, savedJobId]);

  useEffect(() => {
    loadData();
  }, [loadData]);

  async function handleStatusChange(newStatus: ApplicationStatus) {
    if (!jobDetail) return;
    const result = await updateJobStatus(savedJobId, newStatus);
    if (result.ok) {
      setJobDetail((prev) =>
        prev
          ? {
              ...prev,
              status: newStatus,
              statusChangedAt: new Date().toISOString(),
            }
          : prev,
      );
      onStatusChanged?.(savedJobId, newStatus);
      // Reload to get updated interview data etc.
      const detail = await getMyJobDetail(savedJobId);
      if (detail) setJobDetail(detail);
    }
  }

  async function handleAddInterview(type: InterviewType) {
    const result = await addInterview(savedJobId, type);
    if (result.ok) {
      const detail = await getMyJobDetail(savedJobId);
      if (detail) {
        setJobDetail(detail);
        onStatusChanged?.(savedJobId, detail.status as ApplicationStatus);
      }
    }
  }

  async function handleUpdateInterview(
    id: string,
    updates: { type?: InterviewType; scheduledAt?: string | null },
  ) {
    await updateInterview(id, updates);
    const detail = await getMyJobDetail(savedJobId);
    if (detail) setJobDetail(detail);
  }

  async function handleDeleteInterview(id: string) {
    await deleteInterview(id);
    const detail = await getMyJobDetail(savedJobId);
    if (detail) {
      setJobDetail(detail);
      onStatusChanged?.(savedJobId, detail.status as ApplicationStatus);
    }
  }

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-md border border-divider bg-surface lg:sticky lg:top-[4.5rem] lg:h-[calc(100vh-5.5rem)]">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-divider px-4 py-2.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          <Trans
            id="myJobs.detail.title"
            comment="Job detail panel title in My Jobs"
          >
            Job Details
          </Trans>
        </span>
        <button
          onClick={onClose}
          className="cursor-pointer rounded p-1 text-muted hover:bg-border-soft hover:text-foreground"
        >
          <X size={14} />
        </button>
      </div>

      {/* Body */}
      <ScrollFade wrapperClassName="flex-1 min-h-0" className="px-4 py-4">
        {loading && <DetailSkeleton />}
        {error && (
          <p className="py-12 text-center text-sm text-muted">
            <Trans
              id="myJobs.detail.notFound"
              comment="Posting not found message"
            >
              Posting not found.
            </Trans>
          </p>
        )}
        {postingDetail && jobDetail && !loading && (
          <div className="space-y-4">
            <PostingContent detail={postingDetail} />

            <hr className="border-divider" />

            {/* Status section */}
            <div className="space-y-2">
              <h3 className="text-[10px] font-medium uppercase tracking-wider text-muted">
                <Trans
                  id="myJobs.detail.status"
                  comment="Status section heading"
                >
                  Status
                </Trans>
              </h3>
              <div className="flex items-center gap-2">
                <StatusBadge status={jobDetail.status} />
                {jobDetail.status !== "rejected" && (
                  <select
                    value=""
                    onChange={(e) => {
                      if (e.target.value) {
                        handleStatusChange(
                          e.target.value as ApplicationStatus,
                        );
                      }
                    }}
                    className="rounded border border-border-soft bg-surface px-1.5 py-0.5 text-xs text-muted"
                  >
                    <option value="">
                      {changePlaceholder}
                    </option>
                    {APPLICATION_STATUSES.filter(
                      (s) => s !== jobDetail.status,
                    ).map((s) => (
                      <option key={s} value={s}>
                        {statusOptionLabels[s]}
                      </option>
                    ))}
                  </select>
                )}
              </div>
            </div>

            <hr className="border-divider" />

            {/* Interviews */}
            <InterviewList
              interviews={jobDetail.interviews}
              onAdd={handleAddInterview}
              onUpdate={handleUpdateInterview}
              onDelete={handleDeleteInterview}
            />

            {/* Description (below tracker sections) */}
            {postingDetail.descriptionHtml ? (
              <>
                <hr className="border-divider" />
                <div
                  className="job-description max-w-none text-sm leading-relaxed"
                  dangerouslySetInnerHTML={{
                    __html: sanitizeJobHtml(postingDetail.descriptionHtml),
                  }}
                />
              </>
            ) : postingDetail.descriptionUrl ? (
              <div className="space-y-2 py-4">
                <div className="h-3 w-full animate-pulse rounded bg-border-soft" />
                <div className="h-3 w-5/6 animate-pulse rounded bg-border-soft" />
                <div className="h-3 w-4/6 animate-pulse rounded bg-border-soft" />
              </div>
            ) : null}
          </div>
        )}
      </ScrollFade>
    </div>
  );
}

function PostingContent({ detail }: { detail: PostingDetail }) {
  const { company } = detail;
  const lp = useLocalePath();

  return (
    <>
      {/* Company header */}
      <div className="flex items-center gap-3">
        <Link
          href={lp(`/company/${company.slug}`)}
          className="flex items-center gap-3 transition-opacity hover:opacity-80"
        >
          {company.icon ? (
            <Image
              src={company.icon}
              alt={company.name}
              width={36}
              height={36}
              sizes="36px"
              className="size-9 shrink-0 rounded"
            />
          ) : (
            <div className="flex size-9 shrink-0 items-center justify-center rounded bg-border-soft text-muted">
              <Building2 size={20} />
            </div>
          )}
          <span className="text-sm font-semibold">{company.name}</span>
        </Link>
        <a
          href={withUtmSource(detail.sourceUrl)}
          target="_blank"
          rel="noopener noreferrer"
          className="ml-auto inline-flex items-center gap-1.5 rounded-full border border-primary bg-primary px-4 py-1.5 text-sm font-semibold text-primary-contrast transition-opacity hover:opacity-90"
        >
          <Trans
            id="search.detail.apply"
            comment="Apply button linking to original job posting"
          >
            Apply
          </Trans>
        </a>
      </div>

      {/* Job title */}
      <h2 className="text-base font-bold leading-snug">
        {detail.title ?? "—"}
      </h2>

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        {detail.employmentType && (
          <span className="capitalize">
            {detail.employmentType.replace(/_/g, " ")}
          </span>
        )}
        <span suppressHydrationWarning>
          {timeAgoShort(detail.firstSeenAt)}
        </span>
        <SaveButton postingId={detail.id} />
      </div>
    </>
  );
}

function DetailSkeleton() {
  return (
    <div className="animate-pulse space-y-4">
      <div className="flex items-center gap-3">
        <div className="size-9 rounded bg-border-soft" />
        <div className="h-4 w-28 rounded bg-border-soft" />
      </div>
      <div className="h-5 w-3/4 rounded bg-border-soft" />
      <div className="flex gap-3">
        <div className="h-3 w-16 rounded bg-border-soft" />
        <div className="h-3 w-10 rounded bg-border-soft" />
      </div>
      <hr className="border-divider" />
      <div className="h-3 w-20 rounded bg-border-soft" />
      <div className="h-6 w-16 rounded bg-border-soft" />
    </div>
  );
}
