"use client";

import { memo, useState, useRef, useMemo } from "react";
import Link from "next/link";
import { Trans, useLingui } from "@lingui/react/macro";
import { CompanyIcon } from "@/components/CompanyIcon";
import { useParams } from "next/navigation";
import { timeAgoShort } from "@/lib/time";
import { loadMorePostings } from "@/lib/actions/search";
import { useInfiniteScroll } from "@/lib/use-infinite-scroll";
import { InfiniteScrollSentinel } from "@/components/InfiniteScrollSentinel";
import { TruncationPrompt } from "@/components/TruncationPrompt";
import { TrackingDot } from "@/components/TrackingDot";
import { PendingJobIcon } from "@/components/PendingJobWarning";
import { SaveButton } from "@/components/search/save-button";
import { StarButton } from "@/components/search/star-button";
import { ScrollFade } from "@/components/ui/scroll-fade";
import { buildFilteredPath } from "@/lib/search/query-params";
import type { SerializableLocation, SerializableOccupation, SerializableSeniority, SerializableTechnology } from "@/lib/search/query-params";
import type { SearchResultCompany, SearchResultPosting, WorkMode } from "@/lib/search";

const POSTINGS_BATCH = 20;

interface CompanyCardProps {
  result: SearchResultCompany;
  keywords: string[];
  locationIds?: number[];
  locations?: SerializableLocation[];
  occupations?: SerializableOccupation[];
  seniorities?: SerializableSeniority[];
  technologies?: SerializableTechnology[];
  employmentTypes?: string[];
  workMode?: WorkMode[];
  salaryMinEur?: number;
  salaryMaxEur?: number;
  experienceMin?: number;
  experienceMax?: number;
  languages?: string[];
  onShowPosting?: (postingId: string) => void;
  selectedPostingId?: string | null;
}

function CompanyCardImpl({ result, keywords, locationIds, locations, occupations, seniorities, technologies, employmentTypes, workMode, salaryMinEur, salaryMaxEur, experienceMin, experienceMax, languages, onShowPosting, selectedPostingId }: CompanyCardProps) {
  const params = useParams();
  const locale = (params.lang as string) ?? "en";
  const { t } = useLingui();
  const { company, activeMatches, yearMatches } = result;

  const companyHref = buildFilteredPath(
    `/${locale}/company/${company.slug}`,
    keywords,
    locations ?? [],
    undefined,
    occupations,
    seniorities,
    technologies,
    workMode,
  );

  const [extraPostings, setExtraPostings] = useState<SearchResultPosting[]>([]);
  const [exhausted, setExhausted] = useState(false);
  const [isTruncated, setIsTruncated] = useState(false);
  const scrollRef = useRef<HTMLDivElement>(null);

  const allPostings = useMemo(() => {
    const seen = new Set<string>();
    const deduped = [...result.postings, ...extraPostings].filter((p) => {
      if (seen.has(p.id)) return false;
      seen.add(p.id);
      return true;
    });
    return sortPostingsByFreshness(deduped);
  }, [result.postings, extraPostings]);

  const hasMore = !exhausted && !isTruncated && allPostings.length < activeMatches;
  const offsetRef = useRef(result.postings.length);

  async function handleLoadMore() {
    const result = await loadMorePostings({
      companyId: company.id,
      keywords,
      locationIds,
      occupationIds: occupations?.map((o) => o.id),
      seniorityIds: seniorities?.map((s) => s.id),
      technologyIds: technologies?.map((t) => t.id),
      employmentTypes,
      workMode,
      salaryMinEur,
      salaryMaxEur,
      experienceMin,
      experienceMax,
      languages: languages ?? [locale],
      locale,
      offset: offsetRef.current,
      limit: POSTINGS_BATCH,
    });
    if (result.truncated) setIsTruncated(true);
    offsetRef.current += result.postings.length;
    if (result.postings.length > 0) {
      setExtraPostings((prev) => [...prev, ...result.postings]);
    }
    if (result.postings.length < POSTINGS_BATCH) {
      setExhausted(true);
    }
  }

  const { sentinelRef, isLoading } = useInfiniteScroll({ hasMore, load: handleLoadMore, root: scrollRef, rootMargin: "50px" });

  return (
    <div className="rounded-md border border-divider bg-surface p-4">
      {/* Header */}
      <div className="flex items-center gap-3">
        <Link href={companyHref} prefetch={false} className="flex items-center gap-3 transition-opacity hover:opacity-80">
          <CompanyIcon icon={company.icon} alt={company.name} size={32} />
          <span className="text-sm font-semibold">{company.name}</span>
        </Link>
        <StarButton companyId={company.id} />
      </div>

      {/* Stats */}
      <p className="mt-2 text-xs text-muted">
        {activeMatches} <Trans id="search.card.active" comment="Active matches label on company card">active</Trans>
        {" · "}
        {yearMatches} <Trans id="search.card.yearCount" comment="Yearly matches label on company card">in the last year</Trans>
      </p>

      {/* Divider */}
      <hr className="my-3 border-divider" />

      {/* Scrollable posting list */}
      <ScrollFade className="max-h-[184px]" scrollRef={scrollRef} deps={[allPostings.length]}>
          {allPostings.map((posting) => (
          // Un-nested layout (issue #3166): the row "open posting" button
          // and the SaveButton are siblings under a `relative` wrapper —
          // not a div[role=button] containing a nested <button>. The open
          // button is a positioned overlay covering the row's click area;
          // SaveButton sits in normal flex flow with `relative z-10` so
          // it stacks above the overlay and stays keyboard-reachable.
          // `min-h-7` (= 28px = current natural row height with py-1.5
          // + 14px text) locks vertical extent so the row can never cause
          // a layout shift inside the inner scroll container (closes
          // #3345). `[contain:layout]` isolates per-row layout so a
          // re-render of one row cannot reflow neighbouring rows.
          <div
            key={posting.id}
            data-posting-id={posting.id}
            data-first-seen-at={
              typeof posting.firstSeenAt === "string"
                ? posting.firstSeenAt
                : posting.firstSeenAt.toISOString()
            }
            className={`relative flex min-h-7 items-center gap-2 rounded px-1 py-1.5 transition-colors [contain:layout] ${posting.id === selectedPostingId ? "bg-primary/10" : "hover:bg-border-soft"} ${posting.isActive === false ? "opacity-50" : ""}`}
          >
            <button
              type="button"
              onClick={() => onShowPosting?.(posting.id)}
              aria-label={
                posting.title ??
                t({
                  id: "search.card.openPosting",
                  comment: "Aria label for opening a job posting from a company card row when the posting title is missing",
                  message: "Open job posting",
                })
              }
              className="absolute inset-0 z-0 cursor-pointer rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-primary"
            />
            <TrackingDot postingId={posting.id} />
            <span className="min-w-0 flex-1 truncate text-sm">{posting.title ?? "—"}</span>
            {posting.isActive === false && (
              <span className="shrink-0 rounded bg-border-soft px-1 py-0.5 text-[10px] text-muted">
                <Trans id="search.card.closed" comment="Label for inactive/closed job postings on company card">
                  Closed
                </Trans>
              </span>
            )}
            {posting.locations.length > 0 && (
              <span className={`shrink-0 text-xs text-muted ${posting.locations[0].geoType && posting.locations[0].geoType !== "city" ? "italic" : ""}`}>
                {posting.locations[0].name}
                {posting.locations.length > 1 && ` +${posting.locations.length - 1}`}
              </span>
            )}
            {!posting.title && (
              // `relative z-10` so the warning icon's tooltip trigger
              // remains above the absolute overlay button.
              <span className="relative z-10 inline-flex shrink-0">
                <PendingJobIcon />
              </span>
            )}
            <span className="relative z-10 shrink-0">
              <SaveButton postingId={posting.id} />
            </span>
            <span suppressHydrationWarning className="w-8 shrink-0 text-left text-[10px] tabular-nums text-muted">
              {timeAgoShort(posting.firstSeenAt)}
            </span>
          </div>
        ))}
        {hasMore && <InfiniteScrollSentinel sentinelRef={sentinelRef} isLoading={isLoading} size="sm" />}
        {!hasMore && isTruncated && <TruncationPrompt type="postings" />}
      </ScrollFade>
    </div>
  );
}

function firstSeenAtMs(posting: SearchResultPosting): number {
  const value =
    typeof posting.firstSeenAt === "string"
      ? Date.parse(posting.firstSeenAt)
      : posting.firstSeenAt.getTime();
  return Number.isFinite(value) ? value : 0;
}

export function sortPostingsByFreshness(
  postings: SearchResultPosting[],
): SearchResultPosting[] {
  if (postings.length <= 1) return postings;
  return [...postings].sort((a, b) => {
    const byTime = firstSeenAtMs(b) - firstSeenAtMs(a);
    if (byTime !== 0) return byTime;
    if (a.id === b.id) return 0;
    return a.id < b.id ? -1 : 1;
  });
}

// --- Memoization (issue #3198) -----------------------------------------------
// CompanyCard is rendered in a list by SearchResults; without memoization,
// every filter mutation in the parent SearchPage re-renders all N cards (10 on
// initial page, up to 50+ with infinite scroll), each with its own internal
// state machine and 3-5 child components. The parent passes arrays
// (`keywords`, `locationIds`, `locations`, ...) that are reconstructed on
// every render in the parent JSX (`locations.map((l) => l.id)`), so default
// `React.memo` referential equality always fails.
//
// We compare each prop explicitly. For arrays we use a stable shallow
// equality check on the relevant identity (IDs for taxonomy items, raw
// strings for keywords/workMode/employmentTypes/languages). For functions
// (`onShowPosting`) we compare by reference — callers MUST stabilize with
// `useCallback` (search-results.tsx + search-page.tsx do so), otherwise we'd
// hide stale-closure bugs.
//
// IMPORTANT: returning `true` here skips the render. If we omit a prop that
// genuinely changed, the UI will go stale. Every prop in `CompanyCardProps`
// must be reflected below.

function arraysShallowEqual<T>(a: readonly T[] | undefined, b: readonly T[] | undefined): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i] !== b[i]) return false;
  }
  return true;
}

function idArraysEqual<T extends { id: string | number }>(
  a: readonly T[] | undefined,
  b: readonly T[] | undefined,
): boolean {
  if (a === b) return true;
  if (a === undefined || b === undefined) return false;
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) {
    if (a[i].id !== b[i].id) return false;
  }
  return true;
}

/**
 * Exported for unit testing only (see __tests__/company-card.test.tsx).
 * Not part of the public component API.
 */
export function companyCardPropsEqual(prev: CompanyCardProps, next: CompanyCardProps): boolean {
  // `result` is a reference-identity check — the search runner replaces the
  // companies array on every search response, so a new reference always
  // signals genuinely new data. We don't deep-walk postings here.
  if (prev.result !== next.result) return false;

  // Primitives.
  if (prev.salaryMinEur !== next.salaryMinEur) return false;
  if (prev.salaryMaxEur !== next.salaryMaxEur) return false;
  if (prev.experienceMin !== next.experienceMin) return false;
  if (prev.experienceMax !== next.experienceMax) return false;
  if (prev.selectedPostingId !== next.selectedPostingId) return false;

  // Function identity — callers stabilize via `useCallback`. If a parent
  // forgets, we'll over-render; we will NOT hide stale closures here.
  if (prev.onShowPosting !== next.onShowPosting) return false;

  // Arrays of primitives (strings / WorkMode literals).
  if (!arraysShallowEqual(prev.keywords, next.keywords)) return false;
  if (!arraysShallowEqual(prev.locationIds, next.locationIds)) return false;
  if (!arraysShallowEqual(prev.employmentTypes, next.employmentTypes)) return false;
  if (!arraysShallowEqual(prev.workMode, next.workMode)) return false;
  if (!arraysShallowEqual(prev.languages, next.languages)) return false;

  // Arrays of taxonomy objects — compare by `id` only. Cards use
  // `.map((x) => x.id)` plus `name`/`slug` for the inline-link
  // `companyHref`, but `name`/`slug` for a given id never change at
  // runtime in this app (they come from server-rendered taxonomy data).
  if (!idArraysEqual(prev.locations, next.locations)) return false;
  if (!idArraysEqual(prev.occupations, next.occupations)) return false;
  if (!idArraysEqual(prev.seniorities, next.seniorities)) return false;
  if (!idArraysEqual(prev.technologies, next.technologies)) return false;

  return true;
}

export const CompanyCard = memo(CompanyCardImpl, companyCardPropsEqual);
