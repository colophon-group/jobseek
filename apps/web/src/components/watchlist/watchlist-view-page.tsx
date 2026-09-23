"use client";

import { useState, useRef, useEffect, useCallback, useMemo } from "react";
import { AlertTriangle, Check, Loader2, Building2, Copy, Pencil, Share2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useLingui } from "@lingui/react/macro";
import * as Tooltip from "@radix-ui/react-tooltip";
import * as AlertDialog from "@radix-ui/react-alert-dialog";
import type { WatchlistFilters } from "@/lib/actions/watchlists";
import {
  updateWatchlist,
  copySharedWatchlist,
} from "@/lib/actions/watchlists";
import { CompanyPill } from "@/components/watchlist/company-pill";
import { CompanySearchModal } from "@/components/watchlist/company-search-modal";
import { WatchlistActionBar } from "@/components/watchlist/watchlist-action-bar";
import { WatchlistJobList } from "@/components/watchlist/watchlist-job-list";
import { FilterPillsReadOnly } from "@/components/search/filter-pills-readonly";
import { AdvancedSearchPanel } from "@/components/search/advanced-search-panel";
import { AiSearchFilter } from "@/components/search/ai-search-filter";
import type { SelectedLocation } from "@/lib/search/types";
import type { HistogramFilters, WorkMode } from "@/lib/search";
import { mergeWatchlistTaxonomySlugs } from "@/lib/watchlist-utils";
import { useSalaryRates } from "@/components/providers/SalaryDisplayProvider";
import { convertToEur } from "@/lib/salary";
import { Button } from "@/components/ui/Button";
import { tooltipClass, tooltipWarningClass } from "@/components/ui/tooltip-styles";
import { useSession } from "@/components/providers/SessionProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { withAuthReturnPath } from "@/lib/auth-return";
import { copyTextToClipboard } from "@/lib/copy-text-to-clipboard";
import type {
  AiFilterAcceptedPage,
  AiFilterUiState,
} from "@/lib/ai-filter/ui-contract";
import {
  readPendingWatchlists,
  removePendingWatchlist,
  stagePendingWatchlistEntry,
  updatePendingWatchlist,
} from "@/lib/pending-watchlist";
import type { SearchWatchlistDraft } from "@/lib/search/watchlist-draft";
import type { WatchlistPageData } from "@/lib/services/watchlist-page-data";

// Sentinel set used to re-validate the JSONB-stored `workMode` strings
// before they reach Typesense. The watchlist column accepts arbitrary
// JSON, so older clients (or hand-written DB writes) could leave
// garbage here that would otherwise corrupt the `location_types:[…]`
// filter. Issue #3037 (mirrors the same defensive guard documented on
// `WatchlistFilters.workMode`).
const WORK_MODE_VALUES = new Set<WorkMode>(["onsite", "hybrid", "remote"]);

type Company = { id: string; name: string; slug: string; icon: string | null };
type TaxonomyItem = { id: number; slug: string; name: string };
type WatchlistChanges = {
  title?: string;
  description?: string | null;
  companyIds?: string[];
  filters?: WatchlistFilters;
};

/**
 * All editable controls use one persistence contract. Browser-backed and
 * persisted watchlists differ only at this boundary, so UI behavior cannot
 * silently drift as controls are added or changed.
 */
function useWatchlistPersistence(
  watchlistId: string,
  sessionWatchlistId?: string,
) {
  return useCallback(async (changes: WatchlistChanges): Promise<boolean> => {
    if (!sessionWatchlistId) {
      const result = await updateWatchlist({ watchlistId, ...changes });
      return !("error" in result);
    }

    const entry = readPendingWatchlists().find(
      (candidate) => candidate.id === sessionWatchlistId,
    );
    if (entry?.intent.kind !== "create") return false;

    const draft: SearchWatchlistDraft = {
      ...entry.intent.draft,
      ...(changes.title !== undefined ? { title: changes.title } : {}),
      ...(changes.companyIds !== undefined ? { companyIds: changes.companyIds } : {}),
      ...(changes.filters !== undefined ? { filters: changes.filters } : {}),
    };
    if (changes.description !== undefined) {
      if (changes.description) draft.description = changes.description;
      else delete draft.description;
    }
    return updatePendingWatchlist(sessionWatchlistId, {
      kind: "create",
      draft,
    });
  }, [sessionWatchlistId, watchlistId]);
}

function SharedWatchlistCloneAction({
  watchlistId,
  watchlistTitle,
  limitReached,
  hasAiFilter,
  onErrorChange,
}: {
  watchlistId: string;
  watchlistTitle: string;
  limitReached: boolean;
  hasAiFilter: boolean;
  onErrorChange: (error: string) => void;
}) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const { isLoggedIn, plan } = useSession();
  const [busy, setBusy] = useState(false);
  const [aiWarningOpen, setAiWarningOpen] = useState(false);
  const [limitRaceReached, setLimitRaceReached] = useState(false);
  const [limitOpen, setLimitOpen] = useState(false);
  const limitCloseRef = useRef<ReturnType<typeof setTimeout>>(undefined);
  const cloneBlocked = limitReached || limitRaceReached;
  const shouldWarnAboutNarrowedCopy = hasAiFilter && plan !== "unlimited";
  const label = t({
    id: "watchlists.actions.clone",
    comment: "Action to clone an unlisted shared watchlist into the signed-in user's account",
    message: "Clone",
  });
  const limitLabel = t({
    id: "watchlists.card.limitReached",
    comment: "Warning shown when cloning would exceed the account watchlist limit",
    message: "Maximum of 10 watchlists reached",
  });

  useEffect(() => () => clearTimeout(limitCloseRef.current), []);

  function showLimitTooltip() {
    clearTimeout(limitCloseRef.current);
    setLimitOpen(true);
    limitCloseRef.current = setTimeout(() => setLimitOpen(false), 3_000);
  }

  async function performClone() {
    if (busy) return;
    if (cloneBlocked) {
      showLimitTooltip();
      return;
    }
    setBusy(true);
    onErrorChange("");
    try {
      const result = await copySharedWatchlist(watchlistId);
      if ("error" in result) {
        if (result.error === "limit_reached") {
          setLimitRaceReached(true);
          showLimitTooltip();
        } else {
          onErrorChange(t({ id: "watchlists.actions.cloneFailed", comment: "Error shown when an unlisted shared watchlist cannot be cloned", message: "Could not clone this watchlist." }));
        }
        return;
      }
      router.push(lp(`/watchlists/${result.id}`));
    } catch {
      onErrorChange(t({
        id: "watchlists.actions.cloneFailed",
        comment: "Error shown when an unlisted shared watchlist cannot be cloned",
        message: "Could not clone this watchlist.",
      }));
    } finally {
      setBusy(false);
    }
  }

  function continueClone() {
    if (!isLoggedIn) {
      const staged = stagePendingWatchlistEntry({
        kind: "clone",
        watchlistId,
        title: watchlistTitle,
      });
      router.push(staged
        ? lp(`/watchlists/${staged.id}`)
        : withAuthReturnPath(lp("/sign-in"), lp(`/watchlists/${watchlistId}`)));
      return;
    }
    void performClone();
  }

  const button = (
    <Button
      type="button"
      size="sm"
      className={`gap-2 ${cloneBlocked ? "!cursor-not-allowed !opacity-50 hover:!opacity-50" : ""}`}
      onClick={() => {
        if (cloneBlocked) {
          showLimitTooltip();
          return;
        }
        if (shouldWarnAboutNarrowedCopy) {
          setAiWarningOpen(true);
          return;
        }
        continueClone();
      }}
      disabled={busy}
      aria-disabled={cloneBlocked || undefined}
      aria-label={label}
    >
      {busy
        ? <Loader2 size={15} className="motion-safe:animate-spin" aria-hidden="true" />
        : <Copy size={15} aria-hidden="true" />}
      {label}
    </Button>
  );

  return (
    <>
      <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
        <Tooltip.Root
          open={cloneBlocked && limitOpen}
          onOpenChange={(open) => {
            if (!cloneBlocked) return;
            clearTimeout(limitCloseRef.current);
            setLimitOpen(open);
          }}
        >
          <Tooltip.Trigger asChild>{button}</Tooltip.Trigger>
          <Tooltip.Portal>
            <Tooltip.Content
              className={`${tooltipWarningClass} flex items-center gap-1.5`}
              side="top"
              sideOffset={6}
            >
              <AlertTriangle size={12} className="shrink-0" aria-hidden="true" />
              <span role="status" aria-live="polite">{limitLabel}</span>
            </Tooltip.Content>
          </Tooltip.Portal>
        </Tooltip.Root>
      </Tooltip.Provider>
      <AlertDialog.Root open={aiWarningOpen} onOpenChange={setAiWarningOpen}>
        <AlertDialog.Portal>
          <AlertDialog.Overlay className="fixed inset-0 z-50 bg-black/40 data-[state=open]:animate-in data-[state=open]:fade-in-0 motion-reduce:animate-none" />
          <AlertDialog.Content className="fixed left-1/2 top-1/2 z-50 w-[calc(100%-2rem)] max-w-sm -translate-x-1/2 -translate-y-1/2 rounded-xl border border-border-soft bg-surface p-5 shadow-xl data-[state=open]:animate-in data-[state=open]:fade-in-0 data-[state=open]:zoom-in-95 motion-reduce:animate-none">
            <AlertDialog.Title className="text-base font-semibold">
              {t({
                id: "watchlists.clone.narrowedFree.title",
                comment: "Title warning a free user that a shared narrowed feed is not copied",
                message: "Clone without narrowed results?",
              })}
            </AlertDialog.Title>
            <AlertDialog.Description className="mt-2 text-sm leading-relaxed text-muted">
              {t({
                id: "watchlists.clone.narrowedFree.description",
                comment: "Explanation that a free clone keeps ordinary filters but cannot use the shared narrowed feed",
                message: "Your copy will keep the standard filters. On Free, you won’t be able to set up or view its narrowed feed.",
              })}
            </AlertDialog.Description>
            <div className="mt-5 flex justify-end gap-2">
              <AlertDialog.Cancel asChild>
                <button className="cursor-pointer rounded-md border border-border-soft px-3 py-1.5 text-sm font-medium transition-colors hover:bg-border-soft">
                  {t({ id: "common.actions.cancel", comment: "Cancel cloning a shared narrowed watchlist", message: "Cancel" })}
                </button>
              </AlertDialog.Cancel>
              <AlertDialog.Action asChild>
                <button
                  onClick={continueClone}
                  className="cursor-pointer rounded-md bg-primary px-3 py-1.5 text-sm font-medium text-primary-contrast transition-opacity hover:opacity-90"
                >
                  {t({
                    id: "watchlists.clone.narrowedFree.confirm",
                    comment: "Confirm cloning only the standard filters from a shared narrowed watchlist",
                    message: "Clone standard filters",
                  })}
                </button>
              </AlertDialog.Action>
            </div>
          </AlertDialog.Content>
        </AlertDialog.Portal>
      </AlertDialog.Root>
    </>
  );
}

function SharedWatchlistShareAction({ watchlistId }: { watchlistId: string }) {
  const { t } = useLingui();
  const lp = useLocalePath();
  const [shareState, setShareState] = useState<"idle" | "copying" | "copied" | "error">("idle");
  const [tooltipRequestedOpen, setTooltipRequestedOpen] = useState(false);
  const shareResetRef = useRef<ReturnType<typeof setTimeout>>(undefined);

  useEffect(() => () => clearTimeout(shareResetRef.current), []);

  const actionLabel = t({
    id: "watchlists.actions.share",
    comment: "Action to share a watchlist by unlisted link",
    message: "Share",
  });
  const feedbackLabel = shareState === "copied"
    ? t({
      id: "watchlists.actions.copied",
      comment: "Confirmation after copying an unlisted watchlist link",
      message: "Link copied",
    })
    : t({
      id: "watchlists.actions.shareFailed",
      comment: "Error after an unlisted watchlist link cannot be copied",
      message: "Copy failed",
    });
  const feedbackOpen = shareState === "copied" || shareState === "error";
  const buttonLabel = feedbackOpen ? feedbackLabel : actionLabel;

  async function handleShare() {
    if (shareState === "copying") return;
    clearTimeout(shareResetRef.current);
    setShareState("copying");
    try {
      const url = new URL(
        lp(`/watchlists/${watchlistId}`),
        window.location.origin,
      ).toString();
      await copyTextToClipboard(url);
      setShareState("copied");
    } catch {
      setShareState("error");
    }
    shareResetRef.current = setTimeout(() => setShareState("idle"), 2_500);
  }

  return (
    <Tooltip.Provider delayDuration={0} skipDelayDuration={300}>
      <Tooltip.Root
        open={feedbackOpen || tooltipRequestedOpen}
        onOpenChange={setTooltipRequestedOpen}
      >
        <Tooltip.Trigger asChild>
          <button
            type="button"
            className={`inline-flex items-center justify-center rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground ${shareState === "copying" ? "cursor-wait" : "cursor-pointer"}`}
            onClick={() => void handleShare()}
            aria-busy={shareState === "copying" || undefined}
            aria-disabled={shareState === "copying" || undefined}
            aria-label={buttonLabel}
          >
            {shareState === "copying" ? (
              <Loader2 size={16} className="motion-safe:animate-spin" aria-hidden="true" />
            ) : shareState === "copied" ? (
              <Check size={16} aria-hidden="true" />
            ) : (
              <Share2 size={16} aria-hidden="true" />
            )}
          </button>
        </Tooltip.Trigger>
        <Tooltip.Portal>
          <Tooltip.Content
            className={`${shareState === "error" ? tooltipWarningClass : tooltipClass} flex items-center gap-1.5`}
            side="top"
            sideOffset={6}
          >
            {feedbackOpen ? (
              <span role="status" aria-live="polite">{feedbackLabel}</span>
            ) : actionLabel}
          </Tooltip.Content>
        </Tooltip.Portal>
      </Tooltip.Root>
    </Tooltip.Provider>
  );
}

function SharedWatchlistActions({
  watchlistId,
  watchlistTitle,
  limitReached,
  hasAiFilter,
}: {
  watchlistId: string;
  watchlistTitle: string;
  limitReached: boolean;
  hasAiFilter: boolean;
}) {
  const [cloneError, setCloneError] = useState("");

  return (
    <div className="flex flex-col items-end gap-1.5 self-end sm:self-auto">
      <div className="flex flex-wrap items-center justify-end gap-2">
        <SharedWatchlistShareAction watchlistId={watchlistId} />
        <SharedWatchlistCloneAction
          watchlistId={watchlistId}
          watchlistTitle={watchlistTitle}
          limitReached={limitReached}
          hasAiFilter={hasAiFilter}
          onErrorChange={setCloneError}
        />
      </div>
      {cloneError ? (
        <span className="max-w-56 text-right text-xs text-error" role="alert">
          {cloneError}
        </span>
      ) : null}
    </div>
  );
}

export function WatchlistViewPage({
  data,
  locale,
  initialAiFilterState = null,
  initialAiAcceptedPage = null,
  sessionWatchlistId,
}: {
  data: WatchlistPageData;
  locale: string;
  initialAiFilterState?: AiFilterUiState | null;
  initialAiAcceptedPage?: AiFilterAcceptedPage | null;
  /** Browser-backed watchlists use the normal view with a local persistence adapter. */
  sessionWatchlistId?: string;
}) {
  const {
    detail,
    isOwner,
    limitReached,
    postings: initialPostings,
    total: initialTotal,
    truncated: initialTruncated,
    yearTotal,
    searchUnavailable: initialSearchUnavailable,
    resolvedLocations,
    resolvedOccupations,
    resolvedSeniorities,
    resolvedTechnologies,
    jobLanguages,
    languages,
    browserPostingFilters: initialPostingFilters = null,
  } = data;
  const { t } = useLingui();
  const currencyRates = useSalaryRates();
  const { plan, isLoggedIn, isPending: isSessionPending, refresh } = useSession();
  const ownerRefreshAttemptedRef = useRef(false);
  const isSessionWatchlist = sessionWatchlistId !== undefined;
  const canManage = isOwner && (isLoggedIn || isSessionWatchlist);
  const [aiCandidateCount, setAiCandidateCount] = useState<number | undefined>(
    initialSearchUnavailable ? undefined : initialTotal,
  );
  const [aiFilterState, setAiFilterState] = useState<AiFilterUiState | null>(
    initialAiFilterState,
  );
  const [aiMatchCount, setAiMatchCount] = useState<number>(
    initialAiAcceptedPage?.total ?? initialAiFilterState?.counts.accepted ?? 0,
  );
  const [aiDrawerOpen, setAiDrawerOpen] = useState(false);
  const scopeRevisionRef = useRef(0);
  const [scopeRevision, setScopeRevision] = useState(0);
  const [persistedScopeRevision, setPersistedScopeRevision] = useState(0);
  const persistWatchlistChanges = useWatchlistPersistence(
    detail.id,
    sessionWatchlistId,
  );

  useEffect(() => {
    // The server can still hold a valid httpOnly session when the readable
    // login hint is missing. Reconcile that split once on an owner-rendered
    // route; controls remain read-only until the client confirms identity.
    if (
      !isOwner ||
      isSessionWatchlist ||
      isLoggedIn ||
      isSessionPending ||
      ownerRefreshAttemptedRef.current
    ) return;
    ownerRefreshAttemptedRef.current = true;
    void refresh().catch(() => {
      // Keep the page read-only. Every mutation remains server-authorized.
    });
  }, [isLoggedIn, isOwner, isSessionPending, isSessionWatchlist, refresh]);

  function beginScopeMutation(): number {
    const revision = scopeRevisionRef.current + 1;
    scopeRevisionRef.current = revision;
    setScopeRevision(revision);
    return revision;
  }

  // ── Editable title ──
  const [title, setTitle] = useState(detail.title);
  const [editingTitle, setEditingTitle] = useState(false);
  const [savingTitle, setSavingTitle] = useState(false);
  const [mutationError, setMutationError] = useState("");
  const titleInputRef = useRef<HTMLInputElement>(null);
  const persistedTitleRef = useRef(detail.title);
  const titleSaveInFlightRef = useRef(false);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  const updateErrorMessage = useCallback(() => t({
    id: "watchlists.updateFailed",
    comment: "Error shown when an edit to an owned watchlist cannot be saved",
    message: "Could not save your changes.",
  }), [t]);

  useEffect(() => {
    if (editingTitle) titleInputRef.current?.focus();
  }, [editingTitle]);

  const saveTitle = useCallback(async () => {
    if (titleSaveInFlightRef.current) return;
    const trimmed = title.trim();
    if (!trimmed || trimmed === persistedTitleRef.current) {
      setTitle(persistedTitleRef.current);
      setEditingTitle(false);
      return;
    }
    titleSaveInFlightRef.current = true;
    setSavingTitle(true);
    setMutationError("");
    try {
      if (!await persistWatchlistChanges({ title: trimmed })) {
        throw new Error("watchlist_write_failed");
      }
      persistedTitleRef.current = trimmed;
      setTitle(trimmed);
    } catch {
      setTitle(persistedTitleRef.current);
      setMutationError(updateErrorMessage());
    } finally {
      titleSaveInFlightRef.current = false;
      setSavingTitle(false);
      setEditingTitle(false);
    }
  }, [title, persistWatchlistChanges, updateErrorMessage]);

  // ── Editable description ──
  const [description, setDescription] = useState(detail.description ?? "");
  const [editingDescription, setEditingDescription] = useState(false);
  const [savingDescription, setSavingDescription] = useState(false);
  const descriptionRef = useRef<HTMLTextAreaElement>(null);
  const persistedDescriptionRef = useRef(detail.description ?? "");
  const descriptionSaveInFlightRef = useRef(false);

  useEffect(() => {
    if (editingDescription) {
      const el = descriptionRef.current;
      if (el) {
        el.focus();
        el.selectionStart = el.value.length;
      }
    }
  }, [editingDescription]);

  const saveDescription = useCallback(async () => {
    if (descriptionSaveInFlightRef.current) return;
    const trimmed = description.trim();
    if (trimmed === persistedDescriptionRef.current) {
      setEditingDescription(false);
      return;
    }
    descriptionSaveInFlightRef.current = true;
    setSavingDescription(true);
    setMutationError("");
    try {
      if (!await persistWatchlistChanges({ description: trimmed || null })) {
        throw new Error("watchlist_write_failed");
      }
      persistedDescriptionRef.current = trimmed;
      setDescription(trimmed);
    } catch {
      setDescription(persistedDescriptionRef.current);
      setMutationError(updateErrorMessage());
    } finally {
      descriptionSaveInFlightRef.current = false;
      setSavingDescription(false);
      setEditingDescription(false);
    }
  }, [description, persistWatchlistChanges, updateErrorMessage]);

  // ── Editable companies ──
  const [companies, setCompanies] = useState<Company[]>(detail.companies);
  const [anyCompany, setAnyCompany] = useState(detail.filters.anyCompany ?? false);
  const [companyModalOpen, setCompanyModalOpen] = useState(false);
  const companyMutationInFlightRef = useRef(false);

  async function applyCompanyMutation(
    nextCompanies: Company[],
  ) {
    if (companyMutationInFlightRef.current) return;
    const scopeRevision = beginScopeMutation();
    const previousCompanies = companies;
    companyMutationInFlightRef.current = true;
    setMutationError("");
    setCompanies(nextCompanies);
    try {
      const persisted = await persistWatchlistChanges({
        companyIds: nextCompanies.map((candidate) => candidate.id),
      });
      if (!persisted) throw new Error("company_update_failed");
    } catch {
      setCompanies(previousCompanies);
      setMutationError(updateErrorMessage());
    } finally {
      if (mountedRef.current) setPersistedScopeRevision(scopeRevision);
      companyMutationInFlightRef.current = false;
    }
  }

  function handleToggleCompany(company: Company) {
    if (companyMutationInFlightRef.current) return;
    const exists = companies.some((c) => c.id === company.id);
    if (exists) {
      const next = companies.filter((candidate) => candidate.id !== company.id);
      void applyCompanyMutation(next);
    } else {
      const next = [...companies, company];
      void applyCompanyMutation(next);
    }
  }

  function handleRemoveCompany(companyId: string) {
    if (companyMutationInFlightRef.current) return;
    const next = companies.filter((company) => company.id !== companyId);
    void applyCompanyMutation(next);
  }

  function handleClearAllCompanies() {
    if (companyMutationInFlightRef.current) return;
    void applyCompanyMutation([]);
  }

  // ── Editable filters (using resolved objects) ──
  const [keywords, setKeywords] = useState<string[]>(detail.filters.keywords ?? []);
  const [locations, setLocations] = useState<SelectedLocation[]>(resolvedLocations);
  const [occupations, setOccupations] = useState<TaxonomyItem[]>(resolvedOccupations);
  const [seniorities, setSeniorities] = useState<TaxonomyItem[]>(resolvedSeniorities);
  const [technologies, setTechnologies] = useState<TaxonomyItem[]>(resolvedTechnologies);
  const [salaryCurrency, setSalaryCurrency] = useState<string>(detail.filters.salaryCurrency ?? "EUR");
  const [salaryMin, setSalaryMin] = useState<number | undefined>(detail.filters.salaryMin);
  const [salaryMax, setSalaryMax] = useState<number | undefined>(detail.filters.salaryMax);
  const [salaryFilterEdited, setSalaryFilterEdited] = useState(false);
  const [experienceMin, setExperienceMin] = useState<number | undefined>(detail.filters.experienceMin);
  const [experienceMax, setExperienceMax] = useState<number | undefined>(detail.filters.experienceMax);
  // Issue #3037 — close the watchlist/explore filter-parity gap. Both
  // arrays are seeded defensively: workMode strings come from JSONB so
  // we re-validate against `WORK_MODE_VALUES` before letting them flow
  // into Typesense (mirrors the comment on `WatchlistFilters.workMode`),
  // and employmentType values pass through untouched (handled raw by
  // `buildFilterString` downstream).
  const [workMode, setWorkMode] = useState<WorkMode[]>(
    (detail.filters.workMode ?? []).filter((m): m is WorkMode => WORK_MODE_VALUES.has(m as WorkMode)),
  );
  const [employmentTypes, setEmploymentTypes] = useState<string[]>(detail.filters.employmentType ?? []);

  const histogramFilters: HistogramFilters = useMemo(() => ({
    locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
    occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
    seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
    technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
    // workMode + employmentTypes flow through so the modal-side facet
    // helpers can cross-filter their counts against each other (#3032).
    // The advanced-search-panel strips the active dimension before
    // passing this object down to the matching modal — e.g. the
    // work-mode modal sees `employmentTypes` but NOT `workMode`, so
    // counts represent "what would I see if I toggled this mode on".
    workMode: workMode.length > 0 ? workMode : undefined,
    employmentTypes: employmentTypes.length > 0 ? employmentTypes : undefined,
    languages: languages.length > 0 ? languages : undefined,
  }), [locations, occupations, seniorities, technologies, workMode, employmentTypes, languages]);

  // Persist filters through the shared adapter (debounced and flushed on unmount).
  const saveFiltersTimeout = useRef<ReturnType<typeof setTimeout>>(undefined);
  const pendingFiltersRef = useRef<{
    filters: WatchlistFilters;
    scopeRevision: number;
  } | null>(null);
  const filterSaveChainRef = useRef<Promise<void>>(Promise.resolve());

  function enqueueFilterSave(
    updated: WatchlistFilters,
    reportError: boolean,
    scopeRevision: number,
  ) {
    filterSaveChainRef.current = filterSaveChainRef.current.then(async () => {
      try {
        if (!await persistWatchlistChanges({ filters: updated })) {
          throw new Error("watchlist_write_failed");
        }
        if (mountedRef.current) setPersistedScopeRevision(scopeRevision);
      } catch {
        if (reportError && mountedRef.current) {
          setMutationError(updateErrorMessage());
        }
      }
    });
  }

  useEffect(() => {
    return () => {
      clearTimeout(saveFiltersTimeout.current);
      const pending = pendingFiltersRef.current;
      pendingFiltersRef.current = null;
      if (pending) {
        enqueueFilterSave(pending.filters, false, pending.scopeRevision);
      }
    };
  }, [persistWatchlistChanges]);
  function persistFilters(updated: WatchlistFilters) {
    const scopeRevision = beginScopeMutation();
    clearTimeout(saveFiltersTimeout.current);
    pendingFiltersRef.current = { filters: updated, scopeRevision };
    saveFiltersTimeout.current = setTimeout(() => {
      pendingFiltersRef.current = null;
      enqueueFilterSave(updated, true, scopeRevision);
    }, 500);
  }

  function handleToggleAnyCompany() {
    const next = !anyCompany;
    setAnyCompany(next);
    persistFilters(buildFilters({ ac: next }));
  }

  function buildFilters(overrides: Partial<{
    kw: string[]; locs: SelectedLocation[]; occs: TaxonomyItem[];
    sens: TaxonomyItem[]; techs: TaxonomyItem[];
    wm: WorkMode[]; et: string[];
    salCur: string; salMin: number | undefined; salMax: number | undefined;
    expMin: number | undefined; expMax: number | undefined;
    ac: boolean;
  }> = {}): WatchlistFilters {
    const kw = overrides.kw ?? keywords;
    const locs = overrides.locs ?? locations;
    const occs = overrides.occs ?? occupations;
    const sens = overrides.sens ?? seniorities;
    const techs = overrides.techs ?? technologies;
    const wm = overrides.wm ?? workMode;
    const et = overrides.et ?? employmentTypes;
    const ac = overrides.ac ?? anyCompany;
    return {
      keywords: kw.length > 0 ? kw : undefined,
      locationSlugs: mergeWatchlistTaxonomySlugs(
        detail.filters.locationSlugs,
        resolvedLocations,
        locs,
      ),
      occupationSlugs: mergeWatchlistTaxonomySlugs(
        detail.filters.occupationSlugs,
        resolvedOccupations,
        occs,
      ),
      senioritySlugs: mergeWatchlistTaxonomySlugs(
        detail.filters.senioritySlugs,
        resolvedSeniorities,
        sens,
      ),
      technologySlugs: mergeWatchlistTaxonomySlugs(
        detail.filters.technologySlugs,
        resolvedTechnologies,
        techs,
      ),
      workMode: wm.length > 0 ? wm : undefined,
      employmentType: et.length > 0 ? et : undefined,
      salaryCurrency: overrides.salCur ?? salaryCurrency,
      salaryMin: "salMin" in overrides ? overrides.salMin : salaryMin,
      salaryMax: "salMax" in overrides ? overrides.salMax : salaryMax,
      experienceMin: "expMin" in overrides ? overrides.expMin : experienceMin,
      experienceMax: "expMax" in overrides ? overrides.expMax : experienceMax,
      anyCompany: ac || undefined,
    };
  }

  // Filter callbacks
  function onRemoveKeyword(kw: string) {
    const next = keywords.filter((k) => k !== kw);
    setKeywords(next);
    persistFilters(buildFilters({ kw: next }));
  }
  function onAddLocation(loc: SelectedLocation) {
    const next = [...locations, loc];
    setLocations(next);
    persistFilters(buildFilters({ locs: next }));
  }
  function onRemoveLocation(id: number) {
    const next = locations.filter((l) => l.id !== id);
    setLocations(next);
    persistFilters(buildFilters({ locs: next }));
  }
  function onAddOccupation(occ: TaxonomyItem) {
    const next = [...occupations, occ];
    setOccupations(next);
    persistFilters(buildFilters({ occs: next }));
  }
  function onRemoveOccupation(id: number) {
    const next = occupations.filter((o) => o.id !== id);
    setOccupations(next);
    persistFilters(buildFilters({ occs: next }));
  }
  function onAddSeniority(sen: TaxonomyItem) {
    const next = [...seniorities, sen];
    setSeniorities(next);
    persistFilters(buildFilters({ sens: next }));
  }
  function onRemoveSeniority(id: number) {
    const next = seniorities.filter((s) => s.id !== id);
    setSeniorities(next);
    persistFilters(buildFilters({ sens: next }));
  }
  function onAddTechnology(tech: TaxonomyItem) {
    const next = [...technologies, tech];
    setTechnologies(next);
    persistFilters(buildFilters({ techs: next }));
  }
  function onRemoveTechnology(id: number) {
    const next = technologies.filter((t) => t.id !== id);
    setTechnologies(next);
    persistFilters(buildFilters({ techs: next }));
  }
  function onToggleEmploymentType(type: string) {
    const next = employmentTypes.includes(type)
      ? employmentTypes.filter((t) => t !== type)
      : [...employmentTypes, type];
    setEmploymentTypes(next);
    persistFilters(buildFilters({ et: next }));
  }
  function onToggleWorkMode(mode: WorkMode) {
    const next = workMode.includes(mode)
      ? workMode.filter((m) => m !== mode)
      : [...workMode, mode];
    setWorkMode(next);
    persistFilters(buildFilters({ wm: next }));
  }
  function onSalaryChange(currency: string, min: number | undefined, max: number | undefined) {
    setSalaryCurrency(currency);
    setSalaryMin(min);
    setSalaryMax(max);
    setSalaryFilterEdited(true);
    persistFilters(buildFilters({ salCur: currency, salMin: min, salMax: max }));
  }
  function onExperienceChange(min: number | undefined, max: number | undefined) {
    setExperienceMin(min);
    setExperienceMax(max);
    persistFilters(buildFilters({ expMin: min, expMax: max }));
  }
  function onClearAll() {
    setKeywords([]);
    setLocations([]);
    setOccupations([]);
    setSeniorities([]);
    setTechnologies([]);
    setWorkMode([]);
    setEmploymentTypes([]);
    setSalaryMin(undefined);
    setSalaryMax(undefined);
    setSalaryFilterEdited(true);
    setExperienceMin(undefined);
    setExperienceMax(undefined);
    persistFilters({});
  }

  const hasFilters =
    keywords.length > 0 ||
    locations.length > 0 ||
    occupations.length > 0 ||
    seniorities.length > 0 ||
    technologies.length > 0 ||
    workMode.length > 0 ||
    employmentTypes.length > 0 ||
    salaryMin != null ||
    salaryMax != null ||
    experienceMin != null ||
    experienceMax != null;
  const postingSnapshotKey = JSON.stringify([
    initialTotal,
    yearTotal,
    initialPostings,
    jobLanguages,
    languages,
  ]);
  const salaryMinEur = salaryFilterEdited || !initialPostingFilters
    ? convertToEur(salaryMin, salaryCurrency, currencyRates)
    : initialPostingFilters.salaryMin;
  const salaryMaxEur = salaryFilterEdited || !initialPostingFilters
    ? convertToEur(salaryMax, salaryCurrency, currencyRates)
    : initialPostingFilters.salaryMax;
  const aiScopeKey = JSON.stringify({
    companyIds: anyCompany ? [] : companies.map((company) => company.id).sort(),
    anyCompany,
    keywords,
    locationIds: locations.map((location) => location.id).sort((a, b) => a - b),
    occupationIds: occupations.map((occupation) => occupation.id).sort((a, b) => a - b),
    seniorityIds: seniorities.map((seniority) => seniority.id).sort((a, b) => a - b),
    technologyIds: technologies.map((technology) => technology.id).sort((a, b) => a - b),
    workMode,
    employmentTypes,
    salaryMinEur,
    salaryMaxEur,
    experienceMin,
    experienceMax,
    languages,
  });
  const previousAiScopeKeyRef = useRef(aiScopeKey);
  useEffect(() => {
    if (previousAiScopeKeyRef.current === aiScopeKey) return;
    previousAiScopeKeyRef.current = aiScopeKey;
    setAiCandidateCount(undefined);
  }, [aiScopeKey]);
  const hasAiFilterScope = hasFilters || (!anyCompany && companies.length > 0);
  const postingFilters = {
    companyIds: anyCompany ? [] : companies.map((company) => company.id),
    anyCompany,
    keywords: keywords.length > 0 ? keywords : undefined,
    locationIds: locations.length > 0 ? locations.map((location) => location.id) : undefined,
    occupationIds: occupations.length > 0 ? occupations.map((occupation) => occupation.id) : undefined,
    seniorityIds: seniorities.length > 0 ? seniorities.map((seniority) => seniority.id) : undefined,
    technologyIds: technologies.length > 0 ? technologies.map((technology) => technology.id) : undefined,
    workMode: workMode.length > 0 ? workMode : undefined,
    employmentType: employmentTypes.length > 0 ? employmentTypes : undefined,
    salaryMin: salaryMinEur,
    salaryMax: salaryMaxEur,
    experienceMin,
    experienceMax,
    languages: languages.length > 0 ? languages : undefined,
  };
  const initialDrawerAiPage =
    scopeRevision === 0 &&
    aiFilterState?.queryVersionId === initialAiFilterState?.queryVersionId
      ? initialAiAcceptedPage
      : null;
  const handleAiResultStateChange = useCallback((state: {
    candidateCount: number | undefined;
    unavailable: boolean;
  }) => {
    setAiCandidateCount(
      state.unavailable ? undefined : state.candidateCount,
    );
  }, []);
  const aiFilterControl = canManage || (!isOwner && aiFilterState?.enabled === true) ? (
    <AiSearchFilter
      isSubscribed={!isOwner || (canManage && plan === "unlimited")}
      hasSearchFilters={hasAiFilterScope}
      candidateCount={aiCandidateCount}
      isSearchPending={aiCandidateCount === undefined}
      watchlistId={detail.id}
      initialQuery={aiFilterState?.enabled ? aiFilterState.query : null}
      narrowedResultCount={aiMatchCount}
      onStateChange={canManage
        ? (state) => {
            setAiFilterState(state);
            if (!state) setAiMatchCount(0);
          }
        : undefined}
      presentation="drawer"
      readOnly={!canManage}
      onDrawerOpenChange={setAiDrawerOpen}
      drawerContent={(isOpen) => aiFilterState?.enabled ? (
        <WatchlistJobList
          key={`${postingSnapshotKey}:narrowed`}
          resultMode="narrowed"
          filters={postingFilters}
          initialPostings={[]}
          initialTotal={0}
          yearTotal={yearTotal}
          initialSearchUnavailable={initialSearchUnavailable}
          jobLanguages={jobLanguages}
          locale={locale}
          aiFilterState={aiFilterState}
          initialAiAcceptedPage={initialDrawerAiPage}
          onAiFilterStateChange={canManage ? setAiFilterState : undefined}
          onAiMatchCountChange={setAiMatchCount}
          aiFilterScopeKey={aiScopeKey}
          aiFilterScopeReady={isOpen && (
            !canManage || scopeRevision === persistedScopeRevision
          )}
          candidateTotal={aiCandidateCount}
          aiFilterReadOnly={!canManage}
          sharedSnapshot={!isOwner}
        />
      ) : null}
    />
  ) : undefined;

  return (
    <div className="space-y-6">
      {isSessionWatchlist ? (
        <p className="flex items-start gap-2 rounded-md border border-warning-border/60 bg-warning-bg px-3 py-2 text-xs leading-relaxed text-warning" role="status">
          <AlertTriangle size={14} className="mt-0.5 shrink-0" aria-hidden="true" />
          {t({
            id: "watchlists.pending.viewDisclaimer",
            comment: "Passive disclaimer on a browser-backed watchlist before login",
            message: "Saved in this browser until you log in. Sharing and alerts are unavailable. It may be lost if this tab is closed or browser data is cleared.",
          })}
        </p>
      ) : null}
      {mutationError ? (
        <p className="rounded-md border border-error/30 bg-error-bg px-3 py-2 text-sm text-error" role="alert">
          {mutationError}
        </p>
      ) : null}
      {/* Configuration area */}
      <div className="space-y-4 rounded-lg bg-surface p-4 ring-1 ring-inset ring-border-soft">
        {/* Header */}
        <div className={canManage
          ? "flex items-start justify-between gap-4"
          : "flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between sm:gap-4"}
        >
          <div className="min-w-0 flex-1">
            {canManage && editingTitle ? (
              <div className="flex items-center gap-2">
                <input
                  ref={titleInputRef}
                  type="text"
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") void saveTitle();
                    if (e.key === "Escape") {
                      setTitle(persistedTitleRef.current);
                      setEditingTitle(false);
                    }
                  }}
                  onBlur={() => void saveTitle()}
                  maxLength={100}
                  className="w-full rounded-md border border-border-soft bg-transparent px-2 py-1 text-xl font-semibold outline-none focus:border-primary"
                />
                {savingTitle && <Loader2 size={16} className="animate-spin text-muted" />}
              </div>
            ) : (
              <h1 className="text-xl font-semibold">
                {canManage ? (
                  <button
                    type="button"
                    className="group/title -mx-2 -my-1 rounded px-2 py-1 text-left transition-colors hover:bg-border-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
                    onClick={() => setEditingTitle(true)}
                    title={t({ id: "watchlists.view.editTitle", comment: "Tooltip for clicking to edit watchlist title", message: "Click to rename" })}
                  >
                    {title}
                    <Pencil
                      size={14}
                      className="ml-2 inline-block text-muted opacity-0 transition-opacity group-hover/title:opacity-100 group-focus-visible/title:opacity-100"
                      aria-hidden="true"
                    />
                  </button>
                ) : title}
              </h1>
            )}
          </div>
          {canManage ? (
            <WatchlistActionBar
              watchlistId={detail.id}
              alertsEnabled={detail.alertsEnabled === true}
              accountRequired={isSessionWatchlist}
              onDelete={isSessionWatchlist
                ? () => removePendingWatchlist(detail.id)
                : undefined}
            />
          ) : !isOwner ? (
            <SharedWatchlistActions
              watchlistId={detail.id}
              watchlistTitle={detail.title}
              limitReached={limitReached}
              hasAiFilter={aiFilterState?.enabled === true}
            />
          ) : null}
        </div>

        {/* Description */}
        {canManage ? (
          editingDescription ? (
            <div className="flex items-start gap-2">
              <textarea
                ref={descriptionRef}
                value={description}
                onChange={(e) => setDescription(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === "Enter" && !e.shiftKey) {
                    e.preventDefault();
                    void saveDescription();
                  }
                  if (e.key === "Escape") {
                    setDescription(persistedDescriptionRef.current);
                    setEditingDescription(false);
                  }
                }}
                onBlur={() => void saveDescription()}
                maxLength={1000}
                rows={5}
                className="w-full resize-y rounded-md border border-border-soft bg-transparent px-2 py-1 text-sm text-muted outline-none focus:border-primary"
                placeholder={t({ id: "watchlists.view.descriptionPlaceholder", comment: "Placeholder for watchlist description textarea", message: "Describe this watchlist..." })}
              />
              {savingDescription && <Loader2 size={14} className="mt-1.5 animate-spin text-muted" />}
            </div>
          ) : description ? (
            <button
              type="button"
              className="group/desc -mx-2 -my-1 flex w-full items-start gap-2 rounded px-2 py-1 text-left text-sm text-muted transition-colors hover:bg-border-soft focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary focus-visible:ring-offset-2 focus-visible:ring-offset-surface"
              onClick={() => setEditingDescription(true)}
              title={t({ id: "watchlists.view.editDescription", comment: "Tooltip for clicking to edit watchlist description", message: "Click to edit description" })}
            >
              <span className="line-clamp-6 whitespace-pre-wrap">{description}</span>
              <Pencil size={12} className="mt-0.5 shrink-0 text-muted opacity-0 transition-opacity group-hover/desc:opacity-100 group-focus-visible/desc:opacity-100" aria-hidden="true" />
            </button>
          ) : (
            <button
              type="button"
              onClick={() => setEditingDescription(true)}
              className="flex items-center gap-1.5 cursor-pointer text-sm text-muted/60 transition-colors hover:text-muted"
            >
              <Pencil size={12} />
              {t({ id: "watchlists.view.addDescription", comment: "Link to add a description to the watchlist", message: "Add description" })}
            </button>
          )
        ) : description ? (
          <p className="whitespace-pre-wrap text-sm text-muted">{description}</p>
        ) : null}

        {/* Companies */}
        <div className="space-y-2 !mt-6">
          {canManage && (
            <div className="flex items-center gap-2">
              <button
                onClick={() => setCompanyModalOpen(true)}
                disabled={anyCompany}
                className="flex cursor-pointer items-center gap-2 rounded-md border border-dashed border-border-soft px-3 py-1.5 text-sm text-muted transition-colors hover:border-primary/30 hover:text-foreground disabled:opacity-40 disabled:cursor-not-allowed"
              >
                <Building2 size={14} className="shrink-0 text-muted" />
                {t({ id: "watchlists.view.addCompany", comment: "Button to open company search modal", message: "Company" })}
              </button>
              <button
                type="button"
                onClick={handleToggleAnyCompany}
                className={`rounded-md px-3 py-1.5 text-sm font-medium transition-colors cursor-pointer ${
                  anyCompany
                    ? "bg-primary text-primary-contrast"
                    : "border border-border-soft text-muted hover:border-primary/30 hover:text-foreground"
                }`}
              >
                {t({ id: "watchlists.view.anyCompany", comment: "Toggle to show jobs from all companies", message: "Any company" })}
              </button>
            </div>
          )}
          {!anyCompany && companies.length > 0 && (
            <div className="flex flex-wrap items-center gap-1.5">
              {companies.map((c) => (
                <CompanyPill
                  key={c.id}
                  company={c}
                  onRemove={canManage ? handleRemoveCompany : undefined}
                />
              ))}
              {canManage && companies.length > 1 && (
                <button
                  onClick={handleClearAllCompanies}
                  className="cursor-pointer text-xs text-muted transition-colors hover:text-foreground"
                >
                  {t({ id: "watchlists.view.clearAllCompanies", comment: "Button to remove all companies from watchlist", message: "Clear all" })}
                </button>
              )}
            </div>
          )}
          {canManage && (
            <CompanySearchModal
              open={companyModalOpen}
              onOpenChange={setCompanyModalOpen}
              selected={companies}
              onToggle={handleToggleCompany}
              onClearAll={handleClearAllCompanies}
              locale={locale}
              watchlistFilters={{
                keywords: keywords.length > 0 ? keywords : undefined,
                locationIds: locations.length > 0 ? locations.map((l) => l.id) : undefined,
                occupationIds: occupations.length > 0 ? occupations.map((o) => o.id) : undefined,
                seniorityIds: seniorities.length > 0 ? seniorities.map((s) => s.id) : undefined,
                technologyIds: technologies.length > 0 ? technologies.map((t) => t.id) : undefined,
                salaryMin: salaryMinEur,
                salaryMax: salaryMaxEur,
                experienceMin,
                experienceMax,
                languages: languages.length > 0 ? languages : undefined,
              }}
            />
          )}
        </div>

        {/* Filters */}
        {canManage ? (
          <div className="space-y-3">
            <AdvancedSearchPanel
                locale={locale}
                locations={locations}
                occupations={occupations}
                seniorities={seniorities}
                technologies={technologies}
                salaryCurrency={salaryCurrency}
                salaryMin={salaryMin}
                salaryMax={salaryMax}
                experienceMin={experienceMin}
                experienceMax={experienceMax}
                onAddLocation={onAddLocation}
                onRemoveLocation={onRemoveLocation}
                onAddOccupation={onAddOccupation}
                onRemoveOccupation={onRemoveOccupation}
                onAddSeniority={onAddSeniority}
                onRemoveSeniority={onRemoveSeniority}
                onAddTechnology={onAddTechnology}
                onRemoveTechnology={onRemoveTechnology}
                employmentTypes={employmentTypes}
                onToggleEmploymentType={onToggleEmploymentType}
                workMode={workMode}
                onToggleWorkMode={onToggleWorkMode}
                onSalaryChange={onSalaryChange}
                onExperienceChange={onExperienceChange}
                histogramFilters={histogramFilters}
            />
            <FilterPillsReadOnly
              filters={buildFilters()}
              locations={locations}
              occupations={occupations}
              seniorities={seniorities}
              technologies={technologies}
              workMode={workMode}
              employmentType={employmentTypes}
              onRemoveKeyword={onRemoveKeyword}
              onRemoveLocation={(loc) => onRemoveLocation(loc.id)}
              onRemoveOccupation={(occ) => onRemoveOccupation(occ.id)}
              onRemoveSeniority={(sen) => onRemoveSeniority(sen.id)}
              onRemoveTechnology={(tech) => onRemoveTechnology(tech.id)}
              onToggleEmploymentType={onToggleEmploymentType}
              onToggleWorkMode={onToggleWorkMode}
              onRemoveSalary={() => onSalaryChange(salaryCurrency, undefined, undefined)}
              onRemoveExperience={() => onExperienceChange(undefined, undefined)}
              onClearAll={hasFilters ? onClearAll : undefined}
            />
          </div>
        ) : (
          <div className="space-y-3">
            <FilterPillsReadOnly
              filters={detail.filters}
              locations={resolvedLocations}
              occupations={resolvedOccupations}
              seniorities={resolvedSeniorities}
              technologies={resolvedTechnologies}
              workMode={
                (detail.filters.workMode ?? []).filter((m): m is WorkMode => WORK_MODE_VALUES.has(m as WorkMode))
              }
              employmentType={detail.filters.employmentType}
            />
          </div>
        )}
      </div>

      {/* Job results. WatchlistJobList owns the "Showing jobs ... ·
          N active · M in the last year" row internally so it stays
          inside the left flex column alongside the postings list,
          not stacked above the detail panel. */}
      <WatchlistJobList
        key={postingSnapshotKey}
        resultMode="broad"
        filters={postingFilters}
        initialPostings={initialPostings}
        initialTotal={initialTotal}
        initialTruncated={initialTruncated}
        yearTotal={yearTotal}
        initialSearchUnavailable={initialSearchUnavailable}
        jobLanguages={jobLanguages}
        locale={locale}
        onResultStateChange={handleAiResultStateChange}
        drawerControl={aiFilterControl}
        drawerOpen={aiDrawerOpen}
        sharedSnapshot={!isOwner}
      />
    </div>
  );
}
