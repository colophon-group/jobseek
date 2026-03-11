"use client";

import { useEffect, useState } from "react";
import Image from "next/image";
import { Building2, ExternalLink, MapPin, X } from "lucide-react";
import { Trans } from "@lingui/react/macro";
import { getPostingDetail } from "@/lib/actions/search";
import type { PostingDetail } from "@/lib/actions/search";
import { SaveButton } from "@/components/search/save-button";
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
    <div className="flex h-full flex-col overflow-hidden rounded-md border border-divider bg-surface lg:sticky lg:top-[4.5rem] lg:h-[calc(100vh-5.5rem)]">
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

  return (
    <div className="space-y-4">
      {/* Company header */}
      <div className="flex items-center gap-3">
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
      </div>

      {/* Job title */}
      <h2 className="text-base font-bold leading-snug">{detail.title ?? "—"}</h2>

      {/* Meta row */}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-xs text-muted">
        {detail.employmentType && (
          <span className="capitalize">{detail.employmentType.replace(/_/g, " ")}</span>
        )}
        <span suppressHydrationWarning>{timeAgoShort(detail.firstSeenAt)}</span>
        <SaveButton postingId={detail.id} />
      </div>

      {/* Locations */}
      {detail.locations.length > 0 && (
        <div className="space-y-1">
          <p className="text-[10px] font-medium uppercase tracking-wider text-muted">
            <Trans id="search.detail.locations" comment="Locations heading in job detail">Locations</Trans>
          </p>
          <ul className="space-y-0.5">
            {detail.locations.map((loc, i) => (
              <li key={i} className="flex items-center gap-1.5 text-sm">
                <MapPin size={12} className="shrink-0 text-muted" />
                <span>{loc.name}</span>
                {loc.type !== "onsite" && (
                  <span className="rounded bg-border-soft px-1.5 py-0.5 text-[10px] capitalize text-muted">
                    {loc.type}
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Source link */}
      <a
        href={detail.sourceUrl}
        target="_blank"
        rel="noopener noreferrer"
        className="inline-flex items-center gap-1.5 text-sm text-accent hover:underline"
      >
        <Trans id="search.detail.viewOriginal" comment="Link to original job posting">View original posting</Trans>
        <ExternalLink size={12} />
      </a>

      {/* Description */}
      {detail.descriptionHtml && (
        <>
          <hr className="border-divider" />
          <div
            className="job-description max-w-none text-sm leading-relaxed"
            dangerouslySetInnerHTML={{ __html: detail.descriptionHtml }}
          />
        </>
      )}
    </div>
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
