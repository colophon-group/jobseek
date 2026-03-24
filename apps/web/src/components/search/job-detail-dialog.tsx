"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import Link from "next/link";
import { BarChart3, Building2, CalendarDays, ChevronDown, ChevronUp, Clock, Code2, DollarSign, MapPin, X } from "lucide-react";
import { Trans, useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import { tooltipClass } from "@/components/ui/tooltip-styles";
import { useLocalePath } from "@/lib/useLocalePath";
import { getPostingDetail } from "@/lib/actions/search";
import type { PostingDetail } from "@/lib/actions/search";
import { SaveButton } from "@/components/search/save-button";
import { useSavedJobs } from "@/components/SavedJobsProvider";
import { PendingJobBanner } from "@/components/PendingJobWarning";
import { withUtmSource } from "@/lib/utm";

import { InterviewList } from "@/components/my-jobs/interview-list";
import { updateJobStatus, getMyJobDetail, addInterview, updateInterview, deleteInterview } from "@/lib/actions/my-jobs";
import type { ApplicationStatus, InterviewEntry, InterviewType } from "@/lib/actions/my-jobs-types";
import { usePageActions } from "@/components/SearchStateProvider";
import { useSalaryDisplay } from "@/components/SalaryDisplayProvider";
import { timeAgoShort } from "@/lib/time";

interface JobDetailPanelProps {
  postingId: string | null;
  onClose: () => void;
}

export function JobDetailPanel({ postingId, onClose }: JobDetailPanelProps) {
  const [detail, setDetail] = useState<PostingDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  useEffect(() => {
    if (!postingId) {
      setDetail(null);
      return;
    }

    setLoading(true);
    setError(false);
    const locale = document.documentElement.lang || "en";

    getPostingDetail({ postingId, locale })
      .then(async (d) => {
        if (!d) { setError(true); return; }
        // Fetch description client-side to avoid Cloudflare challenge
        if (d.descriptionUrl && !d.descriptionHtml) {
          try {
            const resp = await fetch(d.descriptionUrl);
            if (resp.ok) {
              d.descriptionHtml = await resp.text();
            }
          } catch {
            // Description is optional, continue without it
          }
        }
        setDetail(d);
      })
      .catch(() => setError(true))
      .finally(() => setLoading(false));
  }, [postingId]);

  if (!postingId) return null;

  return (
    <div className="flex h-full flex-col overflow-hidden rounded-md border border-divider bg-surface lg:h-[calc(100vh-5.5rem)]">
      {/* Header */}
      <div className="flex shrink-0 items-center justify-between border-b border-divider px-4 py-2.5">
        <span className="text-xs font-semibold uppercase tracking-wide text-muted">
          <Trans id="search.detail.title" comment="Job detail panel title">Job Details</Trans>
        </span>
        <button
          onClick={onClose}
          className="rounded p-1 text-muted hover:bg-border-soft hover:text-foreground"
        >
          <X size={14} />
        </button>
      </div>

      {/* Body */}
      <div className="flex-1 overflow-y-auto px-4 py-4">
        {loading && <DetailSkeleton />}
        {error && (
          <p className="py-12 text-center text-sm text-muted">
            <Trans id="search.detail.notFound" comment="Posting not found message">Posting not found.</Trans>
          </p>
        )}
        {detail && !loading && <DetailContent detail={detail} />}
      </div>
    </div>
  );
}

function DetailContent({ detail }: { detail: PostingDetail }) {
  const { company } = detail;
  const lp = useLocalePath();
  const pageActions = usePageActions();
  const salary = useSalaryDisplay();
  const { t } = useLingui();
  const filterByPrefix = t({ id: "search.detail.filterByPrefix", comment: "Tooltip prefix for clickable filter pills, followed by the value name", message: "Filter by" });
  const { getStatus, getSavedJobId, setStatus: setTrackingStatus } = useSavedJobs();
  const trackingStatus = getStatus(detail.id);
  const savedJobId = getSavedJobId(detail.id);

  const [interviews, setInterviews] = useState<InterviewEntry[]>([]);
  const [interviewsLoaded, setInterviewsLoaded] = useState(false);

  // Fetch interviews when the posting is saved
  useEffect(() => {
    if (!savedJobId || interviewsLoaded) return;
    getMyJobDetail(savedJobId).then((d) => {
      if (d) setInterviews(d.interviews);
      setInterviewsLoaded(true);
    });
  }, [savedJobId, interviewsLoaded]);

  async function handleStatusChange(newStatus: ApplicationStatus) {
    if (!savedJobId) return;
    setTrackingStatus(detail.id, newStatus);
    await updateJobStatus(savedJobId, newStatus);
  }

  async function handleAddInterview(type: InterviewType) {
    if (!savedJobId) return;
    const result = await addInterview(savedJobId, type);
    if (result.ok && result.interview) {
      setInterviews((prev) => [...prev, result.interview!]);
      // Server auto-transitions applied → interviewing
      if (trackingStatus === "applied" || trackingStatus === "saved") {
        setTrackingStatus(detail.id, "interviewing");
      }
    }
  }

  async function handleUpdateInterview(id: string, updates: { type?: InterviewType; scheduledAt?: string | null }) {
    await updateInterview(id, updates);
    setInterviews((prev) => prev.map((i) => i.id === id ? { ...i, ...updates } : i));
  }

  async function handleDeleteInterview(id: string) {
    await deleteInterview(id);
    const remaining = interviews.filter((i) => i.id !== id);
    setInterviews(remaining);
    if (remaining.length === 0 && trackingStatus === "interviewing") {
      setTrackingStatus(detail.id, "applied");
      if (savedJobId) await updateJobStatus(savedJobId, "applied");
    }
  }

  return (
    <Tooltip.Provider delayDuration={300}>
    <div className="space-y-4">
      {(!detail.title || !detail.descriptionHtml) && <PendingJobBanner />}

      {/* Company header */}
      <div className="flex items-center gap-3">
        <Link href={lp(`/company/${company.slug}`)} prefetch={false} className="flex items-center gap-3 transition-opacity hover:opacity-80">
          {company.icon ? (
            <Image
              src={company.icon}
              alt={company.name}
              width={36}
              height={36}
              className="size-9 shrink-0 rounded"
            />
          ) : (
            <div className="flex size-9 shrink-0 items-center justify-center rounded bg-border-soft text-muted">
              <Building2 size={20} />
            </div>
          )}
          <span className="text-sm font-semibold">{company.name}</span>
        </Link>
        <div className="ml-auto flex shrink-0 items-center gap-2">
          <span suppressHydrationWarning className="text-[10px] tabular-nums text-muted">{timeAgoShort(detail.firstSeenAt)}</span>
          <SaveButton postingId={detail.id} />
          <a
            href={withUtmSource(detail.sourceUrl)}
            target="_blank"
            rel="noopener noreferrer"
            className="inline-flex items-center gap-1.5 rounded-full border border-primary bg-primary px-3 py-1 text-xs font-semibold text-primary-contrast transition-opacity hover:opacity-90"
          >
            <Trans id="search.detail.viewPosting" comment="Link to the original job posting">View posting</Trans>
          </a>
        </div>
      </div>

      {/* Job title */}
      <h2 className="text-base font-bold leading-snug">{detail.title ?? "—"}</h2>

      {/* Info pills — clickable to add as search filter */}
      {(detail.employmentType || detail.seniority || detail.experienceMin != null || detail.salaryMin != null || detail.salaryMax != null) && (
        <div className="flex flex-wrap items-center gap-1.5">
          {detail.employmentType && (
            <FilterPill
              icon={<CalendarDays size={11} />}
              label={detail.employmentType.replace(/_/g, " ")}
              tooltip={`${filterByPrefix} ${detail.employmentType.replace(/_/g, " ")}`}
              capitalize
              onClick={pageActions?.addEmploymentType ? () => pageActions.addEmploymentType!(detail.employmentType!) : undefined}
            />
          )}
          {detail.seniority && (
            <FilterPill
              icon={<BarChart3 size={11} />}
              label={detail.seniority.name}
              tooltip={`${filterByPrefix} ${detail.seniority.name}`}
              onClick={pageActions ? () => pageActions.addSeniority(detail.seniority!) : undefined}
            />
          )}
          {(detail.experienceMin != null || detail.experienceMax != null) && (
            <FilterPill
              icon={<Clock size={11} />}
              label={formatExperience(detail.experienceMin, detail.experienceMax)}
              tooltip={`${filterByPrefix} ${formatExperience(detail.experienceMin, detail.experienceMax)}`}
              onClick={pageActions?.setExperienceFilter ? () => pageActions.setExperienceFilter!(detail.experienceMin ?? undefined, detail.experienceMax ?? undefined) : undefined}
            />
          )}
          {(detail.salaryMin != null || detail.salaryMax != null) && (
            <FilterPill
              icon={<DollarSign size={11} />}
              label={salary.format(detail.salaryMin, detail.salaryMax, detail.salaryCurrency, detail.salaryPeriod)}
              tooltip={t({ id: "search.detail.filterBySalary", comment: "Tooltip for salary filter pill", message: "Filter by this salary range" })}
              onClick={pageActions?.setSalaryFilter ? () => pageActions.setSalaryFilter!(detail.salaryCurrency ?? "EUR", detail.salaryMin ?? undefined, detail.salaryMax ?? undefined) : undefined}
            />
          )}
        </div>
      )}

      {/* Locations */}
      {detail.locations.length > 0 && (
        <LocationList locations={detail.locations} onClickLocation={pageActions ? (loc) => pageActions.addLocation({ id: loc.id, name: loc.name, slug: "", type: (loc.geoType ?? "city") as "city" | "region" | "country" | "macro", parentName: loc.parentName ?? null }) : undefined} />
      )}

      {/* Extracted details (collapsed) */}
      {detail.technologies.length > 0 && (
        <ExtractedDetails technologies={detail.technologies} onAddTechnology={pageActions?.addTechnology} />
      )}

      {/* Application tracker — status selector + interviews */}
      {savedJobId && trackingStatus && (
        <div className="rounded-md border border-divider bg-surface-alt/50 px-3 py-2 space-y-2">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
            <Trans id="search.detail.applicationTracker" comment="Application tracker section heading in job detail">Application tracker</Trans>
          </p>
          {!interviewsLoaded ? (
            <div className="animate-pulse space-y-2">
              <div className="flex gap-1">
                {Array.from({ length: 4 }, (_, i) => <div key={i} className="h-5 w-16 rounded-full bg-border-soft" />)}
              </div>
              <div className="h-4 w-24 rounded bg-border-soft" />
            </div>
          ) : (
            <>
              <StatusSelector status={trackingStatus as ApplicationStatus} hasInterviews={interviews.length > 0} onChange={handleStatusChange} />
              {trackingStatus !== "saved" && (
                <InterviewList
                  interviews={interviews}
                  onAdd={handleAddInterview}
                  onUpdate={handleUpdateInterview}
                  onDelete={handleDeleteInterview}
                />
              )}
            </>
          )}
        </div>
      )}

      {/* Description */}
      {detail.descriptionHtml && (
        <>
          {!detail.technologies.length && !savedJobId && <hr className="border-divider" />}
          <div
            className="job-description max-w-none text-sm leading-relaxed"
            dangerouslySetInnerHTML={{ __html: detail.descriptionHtml }}
          />
        </>
      )}
    </div>
    </Tooltip.Provider>
  );
}

const LOCATIONS_COLLAPSED = 3;

function LocationList({ locations, onClickLocation }: { locations: PostingDetail["locations"]; onClickLocation?: (loc: PostingDetail["locations"][number]) => void }) {
  const [expanded, setExpanded] = useState(false);
  const { t } = useLingui();
  const filterByPrefix = t({ id: "search.detail.filterByPrefix", comment: "Tooltip prefix for clickable filter pills, followed by the value name", message: "Filter by" });
  const collapsible = locations.length > LOCATIONS_COLLAPSED;
  const visible = collapsible && !expanded ? locations.slice(0, LOCATIONS_COLLAPSED) : locations;

  function formatLocation(loc: PostingDetail["locations"][number]) {
    if (loc.parentName) return `${loc.name}, ${loc.parentName}`;
    return loc.name;
  }

  return (
    <div className="space-y-1">
      <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
        <Trans id="search.detail.locations" comment="Locations heading in job detail">Locations</Trans>
      </p>
      <ul className="space-y-0.5">
        {visible.map((loc, i) => (
          <li key={i} className="flex items-center gap-1.5 text-sm">
            <MapPin size={12} className="shrink-0 text-muted" />
            {onClickLocation ? (
              <Tooltip.Root>
                <Tooltip.Trigger asChild>
                  <span
                    role="button"
                    tabIndex={0}
                    onClick={() => onClickLocation(loc)}
                    onKeyDown={(e) => { if (e.key === "Enter") onClickLocation(loc); }}
                    className="cursor-pointer rounded px-0.5 transition-colors hover:text-primary hover:underline"
                  >
                    {formatLocation(loc)}
                  </span>
                </Tooltip.Trigger>
                <Tooltip.Portal>
                  <Tooltip.Content className={tooltipClass} sideOffset={6}>
                    {filterByPrefix} {loc.name}
                  </Tooltip.Content>
                </Tooltip.Portal>
              </Tooltip.Root>
            ) : (
              <span>{formatLocation(loc)}</span>
            )}
            {loc.type !== "onsite" && (
              <span className="rounded bg-border-soft px-1.5 py-0.5 text-[10px] capitalize text-muted">
                {loc.type}
              </span>
            )}
          </li>
        ))}
      </ul>
      {collapsible && (
        <button
          onClick={() => setExpanded((v) => !v)}
          className="flex cursor-pointer items-center gap-1 text-xs text-muted transition-colors hover:text-foreground"
        >
          {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
          {expanded ? (
            <Trans id="search.detail.showLessLocations" comment="Button to collapse location list">
              Show less
            </Trans>
          ) : (
            <Trans id="search.detail.showMoreLocations" comment="Button to show remaining collapsed locations">
              {locations.length - LOCATIONS_COLLAPSED} more
            </Trans>
          )}
        </button>
      )}
    </div>
  );
}

function ExtractedDetails({
  technologies,
  onAddTechnology,
}: {
  technologies: { id: number; name: string }[];
  onAddTechnology?: (tech: { id: number; slug: string; name: string }) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const { t } = useLingui();
  const filterByPrefix = t({ id: "search.detail.filterByPrefix", comment: "Tooltip prefix for clickable filter pills, followed by the value name", message: "Filter by" });

  return (
    <div className="rounded-md border border-divider bg-surface-alt/50 px-3 py-2">
      <button
        onClick={() => setExpanded((v) => !v)}
        className="flex cursor-pointer items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-muted transition-colors hover:text-foreground"
      >
        {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
        <Trans id="search.detail.details" comment="Collapsible section heading for extracted job details">
          Details
        </Trans>
      </button>
      {expanded && (
        <div className="mt-2 space-y-2">
          {technologies.length > 0 && (
            <div>
              <p className="mb-1 flex items-center gap-1 text-[10px] font-medium uppercase tracking-wider text-muted">
                <Code2 size={10} />
                <Trans id="search.detail.technologies" comment="Technologies sub-heading in details section">
                  Technologies
                </Trans>
              </p>
              <div className="flex flex-wrap gap-1">
                {technologies.map((tech) => {
                  const slug = tech.name.toLowerCase().replace(/[^a-z0-9]+/g, "-");
                  const tag = (
                    <span
                      key={tech.id}
                      role={onAddTechnology ? "button" : undefined}
                      tabIndex={onAddTechnology ? 0 : undefined}
                      onClick={onAddTechnology ? () => onAddTechnology({ id: tech.id, slug, name: tech.name }) : undefined}
                      onKeyDown={onAddTechnology ? (e) => { if (e.key === "Enter") onAddTechnology({ id: tech.id, slug, name: tech.name }); } : undefined}
                      className={`rounded bg-border-soft px-1.5 py-0.5 text-[11px] text-muted ${onAddTechnology ? "cursor-pointer transition-colors hover:bg-primary/10 hover:text-primary" : ""}`}
                    >
                      {tech.name}
                    </span>
                  );
                  if (!onAddTechnology) return tag;
                  return (
                    <Tooltip.Root key={tech.id}>
                      <Tooltip.Trigger asChild>{tag}</Tooltip.Trigger>
                      <Tooltip.Portal>
                        <Tooltip.Content className={tooltipClass} sideOffset={6}>
                          {filterByPrefix} {tech.name}
                        </Tooltip.Content>
                      </Tooltip.Portal>
                    </Tooltip.Root>
                  );
                })}
              </div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

const statusBaseStyles = {
  saved: { dot: "bg-muted", active: "bg-muted/20 text-foreground" },
  applied: { dot: "bg-sky-400 dark:bg-sky-500", active: "bg-sky-100 text-sky-700 dark:bg-sky-900/40 dark:text-sky-300" },
  interviewing: { dot: "bg-amber-400 dark:bg-amber-500", active: "bg-amber-100 text-amber-700 dark:bg-amber-900/40 dark:text-amber-300" },
  offered: { dot: "bg-emerald-400 dark:bg-emerald-500", active: "bg-emerald-100 text-emerald-700 dark:bg-emerald-900/40 dark:text-emerald-300" },
  rejected: { dot: "bg-rose-400 dark:bg-rose-500", active: "bg-rose-100 text-rose-700 dark:bg-rose-900/40 dark:text-rose-300" },
};

function StatusSelector({ status, hasInterviews, onChange }: { status: ApplicationStatus; hasInterviews: boolean; onChange: (s: ApplicationStatus) => void }) {
  const { t } = useLingui();
  const statusLabels = {
    saved: t({ id: "search.tracker.notApplied", comment: "Application tracker status: not applied", message: "Not applied" }),
    applied: t({ id: "search.tracker.applied", comment: "Application tracker status: applied", message: "Applied" }),
    interviewing: t({ id: "search.tracker.interviewing", comment: "Application tracker status: interviewing", message: "Interviewing" }),
    offered: t({ id: "search.tracker.offer", comment: "Application tracker status: offer", message: "Offer" }),
    rejected: t({ id: "search.tracker.rejected", comment: "Application tracker status: rejected", message: "Rejected" }),
  };

  // Show "Interviewing" instead of "Applied" when interviews exist
  const appliedOption = hasInterviews
    ? { value: "applied" as ApplicationStatus, label: statusLabels.interviewing, ...statusBaseStyles.interviewing }
    : { value: "applied" as ApplicationStatus, label: statusLabels.applied, ...statusBaseStyles.applied };

  const options = [
    { value: "saved" as ApplicationStatus, label: statusLabels.saved, ...statusBaseStyles.saved },
    appliedOption,
    { value: "offered" as ApplicationStatus, label: statusLabels.offered, ...statusBaseStyles.offered },
    { value: "rejected" as ApplicationStatus, label: statusLabels.rejected, ...statusBaseStyles.rejected },
  ];

  return (
    <div className="flex items-center gap-1">
      {options.map((opt) => {
        const selected = status === opt.value || (opt.value === "applied" && status === "interviewing");
        return (
          <button
            key={opt.value}
            onClick={() => onChange(opt.value)}
            className={`inline-flex cursor-pointer items-center gap-1 rounded-full px-2 py-0.5 text-[10px] font-medium transition-colors ${
              selected
                ? opt.active
                : "text-muted hover:bg-border-soft"
            }`}
          >
            <span className={`inline-block size-1.5 rounded-full ${selected ? opt.dot : "bg-transparent"}`} />
            {opt.label}
          </button>
        );
      })}
    </div>
  );
}

function formatExperience(min: number | null, max: number | null): string {
  if (min != null && max != null) return `${min}–${max}y`;
  if (min != null) return `${min}y+`;
  if (max != null) return `≤${max}y`;
  return "";
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
      <div className="space-y-1">
        <div className="h-2.5 w-16 rounded bg-border-soft" />
        <div className="h-3.5 w-40 rounded bg-border-soft" />
        <div className="h-3.5 w-36 rounded bg-border-soft" />
      </div>
      <div className="h-3 w-32 rounded bg-border-soft" />
      <hr className="border-divider" />
      <div className="space-y-2">
        {Array.from({ length: 6 }, (_, i) => (
          <div key={i} className="h-3 rounded bg-border-soft" style={{ width: `${65 + Math.random() * 35}%` }} />
        ))}
      </div>
    </div>
  );
}

function FilterPill({
  icon,
  label,
  tooltip,
  capitalize,
  onClick,
}: {
  icon: React.ReactNode;
  label: string;
  tooltip: string;
  capitalize?: boolean;
  onClick?: () => void;
}) {
  const pill = (
    <span
      role={onClick ? "button" : undefined}
      tabIndex={onClick ? 0 : undefined}
      onClick={onClick}
      onKeyDown={onClick ? (e) => { if (e.key === "Enter") onClick(); } : undefined}
      className={`inline-flex items-center gap-1 rounded-full bg-border-soft px-2 py-0.5 text-[11px] text-muted ${onClick ? "cursor-pointer transition-colors hover:bg-primary/10 hover:text-primary active:bg-primary/15" : ""} ${capitalize ? "capitalize" : ""}`}
    >
      <span className="shrink-0">{icon}</span>
      {label}
    </span>
  );

  if (!onClick) return pill;

  return (
    <Tooltip.Root>
      <Tooltip.Trigger asChild>{pill}</Tooltip.Trigger>
      <Tooltip.Portal>
        <Tooltip.Content className={tooltipClass} sideOffset={6}>
          {tooltip}
        </Tooltip.Content>
      </Tooltip.Portal>
    </Tooltip.Root>
  );
}


