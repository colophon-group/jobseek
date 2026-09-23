"use client";

import {
  type ReactNode,
  useCallback,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
} from "react";
import { useRouter } from "next/navigation";
import { Crown, Funnel, LoaderCircle, Pencil, Trash2, X } from "lucide-react";
import { Plural, useLingui } from "@lingui/react/macro";

import { Button } from "@/components/ui/Button";
import { useSession } from "@/components/providers/SessionProvider";
import { useLocalePath } from "@/lib/useLocalePath";
import { useBrowserSearchParams } from "@/lib/use-browser-search-params";
import { withAuthReturnPath } from "@/lib/auth-return";
import {
  configureAiFilter,
  createAiFilteredWatchlist,
  disableAiFilter,
} from "@/lib/actions/ai-filter";
import type { AiFilterUiState } from "@/lib/ai-filter/ui-contract";
import type { SearchWatchlistDraft } from "@/lib/search/watchlist-draft";
import { stagePendingWatchlist } from "@/lib/pending-watchlist";
import {
  getAiSearchEligibility,
} from "@/lib/ai-filter/search-eligibility";

type Props = {
  isSubscribed: boolean;
  hasSearchFilters: boolean;
  candidateCount?: number;
  isSearchPending?: boolean;
  /** The prompt creates a new watchlist rather than refining an existing one. */
  createsWatchlist?: boolean;
  /** Saved-search scope used when this control creates a watchlist. */
  watchlistDraft?: SearchWatchlistDraft;
  /** Existing watchlist to configure when this control refines its feed. */
  watchlistId?: string;
  /** Persisted criteria for an already-configured watchlist. */
  initialQuery?: string | null;
  /** Live number of active postings accepted by the saved request. */
  narrowedResultCount?: number;
  /** Keeps the watchlist result surface in sync with configuration changes. */
  onStateChange?: (state: AiFilterUiState | null) => void;
  align?: "left" | "right";
  presentation?: "popover" | "drawer";
  /** Narrowed results rendered inside the drawer while the broad feed stays below. */
  drawerContent?: ReactNode | ((isOpen: boolean) => ReactNode);
  /** Lets the result stack remove the covered broad list from page layout. */
  onDrawerOpenChange?: (open: boolean) => void;
  /** Shared watchlists can expose saved results without owner mutation controls. */
  readOnly?: boolean;
};

const SCROLL_REMINDER_DISTANCE = 560;
const SCROLL_REMINDER_MIN_Y = 480;

function ScrollNarrowingReminder({
  enabled,
  storageKey,
  subscriptionRequired,
  signedOut,
  hasNarrowedResults,
  narrowedResultCount,
  onAction,
}: {
  enabled: boolean;
  storageKey: string;
  subscriptionRequired: boolean;
  signedOut: boolean;
  hasNarrowedResults: boolean;
  narrowedResultCount: number;
  onAction: () => void;
}) {
  const { t } = useLingui();
  const [dismissed, setDismissed] = useState<boolean | null>(null);
  const [visible, setVisible] = useState(false);
  const lastScrollYRef = useRef(0);
  const downwardDistanceRef = useRef(0);

  useEffect(() => {
    try {
      setDismissed(window.sessionStorage.getItem(storageKey) === "dismissed");
    } catch {
      setDismissed(false);
    }
  }, [storageKey]);

  useEffect(() => {
    if (!enabled || dismissed !== false) {
      setVisible(false);
      return;
    }

    lastScrollYRef.current = window.scrollY;
    downwardDistanceRef.current = 0;
    const handleScroll = () => {
      const nextScrollY = window.scrollY;
      const delta = nextScrollY - lastScrollYRef.current;
      lastScrollYRef.current = nextScrollY;

      if (delta > 0) downwardDistanceRef.current += delta;
      else if (delta < 0) downwardDistanceRef.current = 0;

      if (
        nextScrollY >= SCROLL_REMINDER_MIN_Y &&
        downwardDistanceRef.current >= SCROLL_REMINDER_DISTANCE
      ) {
        setVisible(true);
      }
    };

    window.addEventListener("scroll", handleScroll, { passive: true });
    return () => window.removeEventListener("scroll", handleScroll);
  }, [dismissed, enabled]);

  function dismiss() {
    setVisible(false);
    setDismissed(true);
    try {
      window.sessionStorage.setItem(storageKey, "dismissed");
    } catch {
      // The reminder can still be dismissed for this render when storage is unavailable.
    }
  }

  if (!visible || dismissed !== false) return null;

  const title = hasNarrowedResults
    ? t({
        id: "search.aiFilter.reminder.resultsTitle",
        comment: "State-neutral title of the scroll reminder for an already-narrowed watchlist",
        message: "Narrowed results",
      })
    : t({
        id: "search.aiFilter.reminder.title",
        comment: "Title of the scroll reminder advertising precise result matching",
        message: "Narrow these results",
      });

  return (
    <aside
      role="dialog"
      aria-label={t({
        id: "search.aiFilter.reminder.ariaLabel",
        comment: "Accessible label for the reminder shown after sustained result scrolling",
        message: "Narrow results reminder",
      })}
      className="fixed bottom-16 left-1/2 z-40 flex w-[min(30rem,calc(100vw-2rem))] -translate-x-1/2 items-center gap-2.5 rounded-xl border border-border-soft bg-surface-alpha p-2 shadow-xl shadow-black/15 backdrop-blur-md motion-safe:animate-in motion-safe:fade-in motion-safe:slide-in-from-bottom-2 md:bottom-4"
    >
      <span className="grid size-8 shrink-0 place-items-center rounded-lg bg-primary/10 text-primary">
        {subscriptionRequired ? (
          <Crown size={15} aria-hidden="true" />
        ) : (
          <Funnel size={15} aria-hidden="true" />
        )}
      </span>
      <div className="min-w-0 flex-1">
        <div className="flex items-center gap-1.5">
          <span className="truncate text-xs font-semibold text-foreground">{title}</span>
          {subscriptionRequired ? (
            <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-primary">
              {t({ id: "common.plan.pro", comment: "Short Pro subscription badge", message: "Pro" })}
            </span>
          ) : null}
        </div>
        <p className="truncate text-[11px] text-muted">
          {subscriptionRequired
            ? t({
                id: "search.aiFilter.reminder.proBody",
                comment: "Compact Pro teaser shown after sustained result scrolling",
                message: "Evaluate every posting against what matters to you.",
              })
            : hasNarrowedResults
              ? (
                  <Plural
                    id="search.aiFilter.reminder.matchCount"
                    comment="Match count in the scroll reminder for an already-narrowed watchlist"
                    value={narrowedResultCount}
                    one="# match in this feed"
                    other="# matches in this feed"
                  />
                )
              : t({
                  id: "search.aiFilter.reminder.body",
                  comment: "Compact reminder that a focused watchlist can be narrowed precisely",
                  message: "Describe what matters and focus this feed.",
                })}
        </p>
      </div>
      <button
        type="button"
        onClick={() => {
          dismiss();
          onAction();
        }}
        className="shrink-0 cursor-pointer whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-contrast transition-opacity hover:opacity-90"
      >
        {subscriptionRequired
          ? signedOut
            ? t({ id: "common.auth.login", comment: "Login button label", message: "Log in" })
            : t({
                id: "search.aiFilter.subscription.cta.short",
                comment: "Short link to the Pro subscription from the compact watchlist control",
                message: "Explore Pro",
              })
          : hasNarrowedResults
            ? t({
                id: "search.aiFilter.viewResults",
                comment: "Short button that opens narrowed watchlist results",
                message: "View",
              })
            : t({
                id: "search.aiFilter.reminder.action",
                comment: "Short action in the scroll reminder that opens precise matching setup",
                message: "Narrow results",
              })}
      </button>
      <button
        type="button"
        onClick={dismiss}
        className="shrink-0 cursor-pointer rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
        aria-label={t({
          id: "search.aiFilter.reminder.dismiss",
          comment: "Accessible label for dismissing the precise matching scroll reminder",
          message: "Dismiss narrow results reminder",
        })}
      >
        <X size={14} aria-hidden="true" />
      </button>
    </aside>
  );
}

export function AiSearchFilter({
  isSubscribed,
  hasSearchFilters,
  candidateCount,
  isSearchPending = false,
  createsWatchlist = false,
  watchlistDraft,
  watchlistId,
  initialQuery,
  narrowedResultCount,
  onStateChange,
  align = "left",
  presentation = "popover",
  drawerContent,
  onDrawerOpenChange,
  readOnly = false,
}: Props) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const browserSearchParams = useBrowserSearchParams();
  const { isLoggedIn, isPending: isSessionPending } = useSession();
  const rootRef = useRef<HTMLDivElement>(null);
  const panelId = useId();
  const queryId = useId();
  const resumeRequested = browserSearchParams.get("narrow") === "1";
  const persistedQuery = initialQuery?.trim() || null;
  const isDrawer = presentation === "drawer";
  const eligibility = getAiSearchEligibility({
    isSubscribed: readOnly || isSubscribed,
    hasSearchFilters,
    candidateCount: isSearchPending ? undefined : candidateCount,
  });
  const resumeCanOpen = resumeRequested && eligibility.status === "eligible";
  const [open, setOpen] = useState(resumeCanOpen);
  const [query, setQuery] = useState(persistedQuery ?? "");
  const [isApplying, setIsApplying] = useState(false);
  const [activeQuery, setActiveQuery] = useState<string | null>(persistedQuery);
  const [isEditing, setIsEditing] = useState(false);
  const appliedQueryRef = useRef<string | null>(persistedQuery);
  const [mutationError, setMutationError] = useState("");
  const drawerScrollYRef = useRef<number | null>(null);
  const [drawerLayoutRevision, setDrawerLayoutRevision] = useState(0);
  const resolvedDrawerContent = typeof drawerContent === "function"
    ? drawerContent(open)
    : drawerContent;
  const setPanelOpen = useCallback((nextOpen: boolean) => {
    const resolvedOpen = nextOpen && isDrawer &&
      eligibility.status === "subscription_required"
      ? false
      : nextOpen;
    if (isDrawer) {
      drawerScrollYRef.current = window.scrollY;
      setDrawerLayoutRevision((revision) => revision + 1);
    }
    setOpen(resolvedOpen);
    if (isDrawer) {
      onDrawerOpenChange?.(
        resolvedOpen && appliedQueryRef.current !== null,
      );
    }
  }, [eligibility.status, isDrawer, onDrawerOpenChange]);

  const clearResumeRequest = useCallback(() => {
    if (!resumeRequested) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("narrow");
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
  }, [resumeRequested]);

  useLayoutEffect(() => {
    if (!isDrawer || drawerScrollYRef.current == null) return;
    const scrollY = drawerScrollYRef.current;
    drawerScrollYRef.current = null;
    window.scrollTo({ top: scrollY, behavior: "instant" });
  }, [drawerLayoutRevision, isDrawer, open]);

  useEffect(() => {
    if (
      resumeRequested &&
      eligibility.status === "subscription_required" &&
      !isSessionPending
    ) {
      setPanelOpen(false);
      clearResumeRequest();
      return;
    }
    if (resumeCanOpen) setPanelOpen(true);
  }, [
    eligibility.status,
    isSessionPending,
    resumeCanOpen,
    resumeRequested,
    clearResumeRequest,
    setPanelOpen,
  ]);

  useEffect(() => {
    appliedQueryRef.current = persistedQuery;
    setQuery(persistedQuery ?? "");
    setActiveQuery(persistedQuery);
    setIsEditing(false);
  }, [persistedQuery]);

  const eligible = eligibility.status === "eligible";
  const canMountDrawerContent =
    isDrawer &&
    resolvedDrawerContent != null &&
    eligibility.status !== "subscription_required";
  const canApply = Boolean(
    (createsWatchlist && watchlistDraft) ||
    (!createsWatchlist && watchlistId),
  );
  const safeNarrowedResultCount = Number.isFinite(narrowedResultCount)
    ? Math.max(0, Math.trunc(narrowedResultCount!))
    : 0;

  function resumePath(): string {
    const url = new URL(window.location.href);
    url.searchParams.set("narrow", "1");
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function closePanel() {
    setPanelOpen(false);
    setIsEditing(false);
    if (!createsWatchlist) setActiveQuery(appliedQueryRef.current);
    clearResumeRequest();
  }

  function openPanel() {
    setPanelOpen(true);
  }

  function beginEditing(queryToEdit: string) {
    if (isDrawer) {
      drawerScrollYRef.current = window.scrollY;
      setDrawerLayoutRevision((revision) => revision + 1);
    }
    setQuery(queryToEdit);
    setIsEditing(true);
    setPanelOpen(true);
  }

  function cancelEditing() {
    if (!activeQuery) {
      closePanel();
      return;
    }
    if (isDrawer) {
      drawerScrollYRef.current = window.scrollY;
      setDrawerLayoutRevision((revision) => revision + 1);
    }
    setQuery(activeQuery);
    setIsEditing(false);
  }

  function goToSignIn() {
    const staged = createsWatchlist && watchlistDraft
      ? stagePendingWatchlist({ kind: "create", draft: watchlistDraft })
      : false;
    router.push(withAuthReturnPath(
      lp("/sign-in"),
      staged ? lp("/watchlists") : resumePath(),
    ));
  }

  function goToSubscription() {
    const billingPath = lp("/settings/billing");
    const params = new URLSearchParams({ next: resumePath() });
    router.push(`${billingPath}?${params.toString()}`);
  }

  function handleReminderAction() {
    if (eligibility.status === "subscription_required") {
      if (!isLoggedIn) goToSignIn();
      else goToSubscription();
      return;
    }
    openPanel();
    window.requestAnimationFrame(() => {
      rootRef.current?.scrollIntoView?.({ behavior: "smooth", block: "start" });
    });
  }

  // The control is progressive disclosure: ordinary filters must first
  // produce a non-empty, economically bounded candidate set.
  if (
    !activeQuery &&
    !persistedQuery &&
    (
      !hasSearchFilters ||
      isSearchPending ||
      candidateCount == null ||
      !Number.isSafeInteger(candidateCount) ||
      candidateCount < 1 ||
      candidateCount > eligibility.maxCandidates
    )
  ) {
    return null;
  }

  async function apply() {
    const normalized = query.trim().replace(/\s+/g, " ");
    if (!eligible || normalized.length === 0 || !canApply) return;
    setIsApplying(true);
    setMutationError("");
    try {
      if (createsWatchlist && watchlistDraft) {
        const result = await createAiFilteredWatchlist({
          draft: watchlistDraft,
          query: normalized,
        });
        if ("error" in result) {
          if (result.error === "not_authenticated") {
            goToSignIn();
            return;
          }
          if (result.error === "subscription_required") {
            goToSubscription();
            return;
          }
          setMutationError(result.error === "limit_reached"
            ? t({
                id: "watchlists.card.limitReached",
                comment: "Warning when precise matching would exceed the account watchlist limit",
                message: "Maximum of 10 watchlists reached",
              })
            : t({
                id: "search.aiFilter.createFailed",
                comment: "Error shown when a precisely matched watchlist cannot be created",
                message: "Could not create this watchlist. Try again.",
              }));
          return;
        }
        router.push(lp(`/watchlists/${result.id}`));
        return;
      } else if (watchlistId) {
        const result = await configureAiFilter(watchlistId, normalized);
        if ("error" in result) {
          if (result.error === "not_authenticated") {
            goToSignIn();
            return;
          }
          if (result.error === "subscription_required") {
            goToSubscription();
            return;
          }
          setMutationError(t({
            id: "search.aiFilter.applyFailed",
            comment: "Error shown when precise matching cannot be enabled on a watchlist",
            message: "Could not narrow this watchlist. Try again.",
          }));
          return;
        }
        onStateChange?.(result.state);
      }
      if (createsWatchlist) {
        setQuery("");
      } else {
        appliedQueryRef.current = normalized;
        setQuery(normalized);
        setActiveQuery(normalized);
        setIsEditing(false);
      }
      if (isDrawer && !createsWatchlist) {
        clearResumeRequest();
        setPanelOpen(true);
      } else {
        closePanel();
      }
    } finally {
      setIsApplying(false);
    }
  }

  async function removeActiveQuery() {
    if (!watchlistId || isApplying) return;
    setIsApplying(true);
    setMutationError("");
    try {
      const result = await disableAiFilter(watchlistId);
      if ("error" in result) {
        setActiveQuery(null);
        setPanelOpen(true);
        setMutationError(t({
          id: "search.aiFilter.removeFailed",
          comment: "Error shown when precise matching cannot be removed from a watchlist",
          message: "Could not remove these matching criteria. Try again.",
        }));
        return;
      }
      setActiveQuery(null);
      setQuery("");
      setIsEditing(false);
      appliedQueryRef.current = null;
      onStateChange?.(null);
      if (isDrawer) setPanelOpen(false);
    } finally {
      setIsApplying(false);
    }
  }

  if (activeQuery && !isDrawer && !isEditing) {
    return (
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={() => beginEditing(activeQuery)}
          className="inline-flex min-w-0 cursor-pointer items-center gap-1.5 rounded-full border border-primary/30 bg-primary/10 px-2.5 py-1 text-xs font-medium text-primary transition-colors hover:bg-primary/15"
          aria-label={t({
            id: "search.aiFilter.edit",
            comment: "Accessible label for editing an active AI search filter",
            message: "Edit matching criteria",
          })}
        >
          <Funnel size={13} aria-hidden="true" />
          <span className="max-w-48 truncate">{activeQuery}</span>
        </button>
        <button
          type="button"
          onClick={() => void removeActiveQuery()}
          disabled={isApplying}
          className="cursor-pointer rounded-full p-1 text-muted transition-colors hover:bg-border-soft hover:text-foreground"
          aria-label={t({
            id: "search.aiFilter.remove",
            comment: "Accessible label for removing the active AI search filter",
            message: "Remove matching criteria",
          })}
        >
          <X size={13} aria-hidden="true" />
        </button>
      </div>
    );
  }

  return (
    <div
      ref={rootRef}
      className={isDrawer
      ? "w-full min-w-0 rounded-lg bg-surface ring-1 ring-inset ring-border-soft"
      : "relative"}
    >
      {isDrawer ? (
        <aside
          aria-label={t({
            id: "search.aiFilter.controlPanel",
            comment: "Accessible label for the compact precise-matching control between watchlist stats and results",
            message: "Precise matching",
          })}
          className="flex min-h-11 w-full min-w-0 items-center gap-2.5 bg-background/40 px-3 py-2"
        >
          <span className="grid size-7 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
            {eligibility.status === "subscription_required" ? (
              <Crown size={14} aria-hidden="true" />
            ) : (
              <Funnel size={14} aria-hidden="true" />
            )}
          </span>
          <div className="flex min-w-0 flex-1 items-baseline gap-2">
            <span className="shrink-0 text-xs font-semibold text-foreground">
              {activeQuery || persistedQuery
                ? t({
                    id: "search.aiFilter.resultsToggle",
                    comment: "Label for the narrowed-results control on a watchlist",
                    message: "Narrowed results",
                  })
                : t({
                    id: "search.aiFilter.compactTitle",
                    comment: "Short title for the compact precise-matching watchlist control",
                    message: "Narrow results precisely",
                  })}
            </span>
            {eligibility.status === "subscription_required" ? (
              <>
                <span className="shrink-0 rounded-full bg-primary/10 px-1.5 py-0.5 text-[9px] font-semibold uppercase tracking-wide text-primary">
                  {t({ id: "common.plan.pro", comment: "Short Pro subscription badge", message: "Pro" })}
                </span>
                <span className="hidden min-w-0 truncate text-[11px] text-muted sm:inline">
                  {t({
                    id: "search.aiFilter.compactSubscriptionBody",
                    comment: "Compact Pro teaser explaining precise matching on a watchlist",
                    message: "Evaluate every posting against your request.",
                  })}
                </span>
              </>
            ) : (activeQuery || persistedQuery) && !open ? (
              <span className="min-w-0 truncate text-[11px] tabular-nums text-muted">
                <Plural
                  id="search.aiFilter.compactMatchCount"
                  comment="Number of active jobs in the compact narrowed-results control"
                  value={safeNarrowedResultCount}
                  one="# match"
                  other="# matches"
                />
              </span>
            ) : !activeQuery && !persistedQuery ? (
              <span className="hidden min-w-0 truncate text-[11px] text-muted sm:inline">
                {t({
                  id: "search.aiFilter.compactBody",
                  comment: "Compact explanation of precise matching on a watchlist",
                  message: "Evaluate every posting against your request.",
                })}
              </span>
            ) : null}
          </div>
          {eligibility.status === "subscription_required" ? (
            <button
              type="button"
              onClick={isSessionPending
                ? undefined
                : isLoggedIn
                  ? goToSubscription
                  : goToSignIn}
              disabled={isSessionPending}
              className="shrink-0 cursor-pointer whitespace-nowrap rounded-md border border-primary/25 px-2.5 py-1 text-[11px] font-medium text-primary transition-colors hover:bg-primary/10 disabled:cursor-wait disabled:opacity-60"
            >
              {isSessionPending
                ? t({
                    id: "search.aiFilter.accessChecking.short",
                    comment: "Short status while precise-matching account access is checked",
                    message: "Checking…",
                  })
                : !isLoggedIn
                  ? t({
                      id: "common.auth.login",
                      comment: "Login button label",
                      message: "Log in",
                    })
                  : t({
                      id: "search.aiFilter.subscription.cta.short",
                      comment: "Short link to the Pro subscription from the compact watchlist control",
                      message: "Explore Pro",
                    })}
            </button>
          ) : (
            <button
              type="button"
              onClick={() => {
                if (open && isEditing) cancelEditing();
                else if (open) closePanel();
                else openPanel();
              }}
              className="shrink-0 cursor-pointer whitespace-nowrap rounded-md bg-primary px-2.5 py-1 text-[11px] font-medium text-primary-contrast transition-opacity hover:opacity-90"
              aria-expanded={open}
              aria-controls={panelId}
            >
              {open
                ? isEditing || !activeQuery
                  ? t({
                      id: "common.actions.cancel",
                      comment: "Button that cancels precise-matching setup or editing",
                      message: "Cancel",
                    })
                  : t({
                      id: "search.aiFilter.showAllResults",
                      comment: "Button that closes narrowed results and returns to the broad watchlist feed",
                      message: "All results",
                    })
                : activeQuery || persistedQuery
                  ? t({
                      id: "search.aiFilter.viewResults",
                      comment: "Short button that opens narrowed watchlist results",
                      message: "View",
                    })
                  : t({
                      id: "search.aiFilter.setup",
                      comment: "Short button that opens precise matching setup",
                      message: "Set up",
                    })}
            </button>
          )}
        </aside>
      ) : !open ? (
        <Button
          type="button"
          onClick={() => {
            if (open) closePanel();
            else openPanel();
          }}
          size="sm"
          className="h-8 gap-1 px-3 text-xs"
          aria-expanded={open}
          aria-controls={panelId}
        >
          <Funnel size={14} aria-hidden="true" />
          {t({
            id: "search.aiFilter.toggle",
            comment: "Button that opens the natural-language AI filter on a job-results surface",
            message: "Narrow down search",
          })}
        </Button>
      ) : null}

      {(open || canMountDrawerContent) && (
        <>
          {!isDrawer ? (
            <div
              aria-hidden="true"
              className="fixed inset-0 z-20 bg-black/30 backdrop-blur-[1px] sm:hidden"
            />
          ) : null}
          <section
            id={panelId}
            aria-labelledby={isDrawer ? undefined : `${panelId}-title`}
            aria-label={isDrawer && activeQuery
              ? t({
                  id: "search.aiFilter.resultsTitle",
                  comment: "Title above the narrowed results inside a watchlist drawer",
                  message: "Narrowed results",
                })
              : isDrawer
                ? t({
                    id: "search.aiFilter.compactTitle",
                    comment: "Short title for the compact precise-matching watchlist control",
                    message: "Narrow results precisely",
                  })
                : undefined}
            data-state={open ? "open" : "closed"}
            hidden={!open}
            className={isDrawer
              ? "relative z-20 w-full min-w-0 max-w-full bg-surface data-[state=open]:animate-in data-[state=open]:fade-in data-[state=open]:duration-200 motion-reduce:animate-none"
              : `fixed inset-x-4 top-28 z-30 max-h-[calc(100vh-9rem)] w-auto overflow-y-auto rounded-lg border border-border-soft bg-surface p-4 shadow-xl shadow-black/10 sm:absolute sm:inset-x-auto sm:top-10 sm:max-h-[calc(100vh-6rem)] sm:w-[min(28rem,calc(100vw-2rem))] sm:overflow-y-auto ${
                  align === "right" ? "sm:right-0" : "sm:left-0"
                }`}
          >
          <div className={isDrawer ? "min-w-0 w-full" : undefined}>
          {isDrawer && activeQuery && !isEditing ? (
            <div className="border-b border-divider bg-background/40 p-3">
              <div className="flex min-w-0 items-center gap-3 rounded-md border border-border-soft bg-surface px-3 py-2">
                <div className="min-w-0 flex-1">
                  <span className="block text-[10px] font-medium uppercase tracking-wider text-muted">
                    {t({
                      id: "search.aiFilter.requestLabel",
                      comment: "Label above the natural-language request used to narrow watchlist results",
                      message: "Matching request",
                    })}
                  </span>
                  <p className="mt-0.5 line-clamp-2 text-xs leading-relaxed text-foreground/90">
                    {activeQuery}
                  </p>
                </div>
                {!readOnly ? (
                  <>
                    <button
                      type="button"
                      onClick={() => beginEditing(activeQuery)}
                      className="inline-flex shrink-0 cursor-pointer items-center justify-center rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary"
                      aria-label={t({
                        id: "search.aiFilter.edit",
                        comment: "Accessible label for editing an active AI search filter",
                        message: "Edit matching criteria",
                      })}
                      title={t({
                        id: "search.aiFilter.editRequest",
                        comment: "Tooltip for changing the matching request on a narrowed watchlist",
                        message: "Edit request",
                      })}
                    >
                      <Pencil size={16} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      onClick={() => void removeActiveQuery()}
                      disabled={isApplying}
                      className="inline-flex shrink-0 cursor-pointer items-center justify-center rounded-md p-1.5 text-muted transition-colors hover:bg-border-soft hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-primary disabled:cursor-wait disabled:opacity-50"
                      aria-label={t({
                        id: "search.aiFilter.remove",
                        comment: "Accessible label for removing the active AI search filter",
                        message: "Remove matching criteria",
                      })}
                      title={t({
                        id: "search.aiFilter.remove.short",
                        comment: "Tooltip for removing saved matching criteria from the drawer",
                        message: "Remove",
                      })}
                    >
                      <Trash2 size={16} aria-hidden="true" />
                    </button>
                  </>
                ) : null}
              </div>
            </div>
          ) : (
          <div className={isDrawer ? "px-4 pb-4 pt-2" : undefined}>
          {isDrawer ? (
            <p className="text-xs leading-relaxed text-muted">
              {createsWatchlist
                ? t({
                    id: "search.aiFilter.descriptionCreatesWatchlist",
                    comment: "Explains that entering AI criteria creates a watchlist from the focused search",
                    message: "Describe exactly what you are looking for. We’ll create a watchlist from this search and evaluate your request against every job posting in its feed to narrow the results precisely.",
                  })
                : t({
                    id: "search.aiFilter.description",
                    comment: "Explains that AI is a second-stage filter over the existing watchlist",
                    message: "Describe exactly what you are looking for. Your request will be evaluated against every job posting in this feed to narrow the results precisely.",
                  })}
            </p>
          ) : (
          <div className="flex items-start gap-3">
            <span className="mt-0.5 grid size-8 shrink-0 place-items-center rounded-md bg-primary/10 text-primary">
              <Funnel size={16} aria-hidden="true" />
            </span>
            <div className="min-w-0 flex-1">
              <div className="flex items-center gap-2">
                <h2 id={`${panelId}-title`} className="text-sm font-semibold">
                  {t({
                    id: "search.aiFilter.title",
                    comment: "Title of the AI filter editor on a job-results surface",
                    message: "Narrow down this search",
                  })}
                </h2>
                {!isSubscribed ? (
                  <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                    {t({ id: "common.plan.pro", comment: "Short Pro subscription badge", message: "Pro" })}
                  </span>
                ) : null}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {createsWatchlist
                  ? t({
                      id: "search.aiFilter.descriptionCreatesWatchlist",
                      comment: "Explains that entering AI criteria creates a watchlist from the focused search",
                      message: "Describe exactly what you are looking for. We’ll create a watchlist from this search and evaluate your request against every job posting in its feed to narrow the results precisely.",
                    })
                  : t({
                      id: "search.aiFilter.description",
                      comment: "Explains that AI is a second-stage filter over the existing watchlist",
                      message: "Describe exactly what you are looking for. Your request will be evaluated against every job posting in this feed to narrow the results precisely.",
                    })}
              </p>
            </div>
            {!isDrawer && persistedQuery && watchlistId ? (
              <button
                type="button"
                onClick={() => void removeActiveQuery()}
                disabled={isApplying}
                className="cursor-pointer whitespace-nowrap rounded px-2 py-1 text-xs text-muted transition-colors hover:bg-border-soft hover:text-foreground disabled:cursor-wait disabled:opacity-50"
                aria-label={t({
                  id: "search.aiFilter.remove",
                  comment: "Accessible label for removing the active AI search filter",
                  message: "Remove matching criteria",
                })}
              >
                {t({
                  id: "search.aiFilter.remove.short",
                  comment: "Short button label for removing saved matching criteria from the drawer",
                  message: "Remove",
                })}
              </button>
            ) : null}
            <button
              type="button"
              onClick={closePanel}
              className="cursor-pointer rounded p-1 text-muted transition-colors hover:bg-border-soft hover:text-foreground"
              aria-label={t({
                id: "search.aiFilter.close",
                comment: "Close button for the AI filter editor",
                message: "Close precise matching",
              })}
            >
              <X size={15} aria-hidden="true" />
            </button>
          </div>
          )}

          {eligibility.status === "subscription_required" && isSessionPending ? (
            <div className="mt-4 flex items-center gap-2 rounded-md border border-border-soft bg-background p-3 text-sm text-muted" role="status">
              <LoaderCircle size={15} className="animate-spin" aria-hidden="true" />
              {t({
                id: "search.aiFilter.accessChecking",
                comment: "Status while precise-matching account access is being checked",
                message: "Checking access…",
              })}
            </div>
          ) : eligibility.status === "subscription_required" && !isLoggedIn ? (
            <div className="mt-4 rounded-md border border-border-soft bg-background p-3">
              <p className="text-sm font-medium">
                {t({
                  id: "search.aiFilter.login.title",
                  comment: "Heading asking the viewer to sign in before creating a precisely matched watchlist",
                  message: "Narrowing is a Pro feature",
                })}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {t({
                  id: "search.aiFilter.login.body",
                  comment: "Explains that the current search will be restored after sign-in",
                  message: "Log in to check your plan or upgrade. Your current search will be restored when you return.",
                })}
              </p>
              <Button type="button" size="sm" className="mt-3" onClick={goToSignIn}>
                {t({
                  id: "common.auth.login",
                  comment: "Login button label",
                  message: "Log in",
                })}
              </Button>
            </div>
          ) : eligibility.status === "subscription_required" ? (
            <div className="mt-4 rounded-md border border-primary/20 bg-primary/5 p-3">
              <div className="flex items-center gap-2 text-sm font-medium">
                <Crown size={15} className="text-primary" aria-hidden="true" />
                {t({
                  id: "search.aiFilter.subscription.title",
                  comment: "Heading explaining AI filter subscription requirement",
                  message: "Precise matching is included with Pro",
                })}
              </div>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {t({
                  id: "search.aiFilter.subscription.body",
                  comment: "Copy explaining the Pro-only AI search filter",
                  message: "Upgrade to evaluate a focused job feed against the criteria that matter to you.",
                })}
              </p>
              <button
                type="button"
                onClick={goToSubscription}
                className="mt-3 inline-flex cursor-pointer text-xs font-medium text-primary hover:underline"
              >
                {t({
                  id: "search.aiFilter.subscription.cta",
                  comment: "Link to subscription settings from the AI filter",
                  message: "View Pro plan",
                })}
              </button>
            </div>
          ) : eligibility.status !== "eligible" ? (
            <div className="mt-4 rounded-md border border-border-soft bg-background p-3">
              <p className="text-sm font-medium">
                {eligibility.status === "too_broad"
                  ? t({
                      id: "search.aiFilter.narrow.title",
                      comment: "Heading shown when too many jobs match for AI filtering",
                      message: "Narrow the search first",
                    })
                  : eligibility.status === "no_matches"
                    ? t({
                        id: "search.aiFilter.empty.title",
                        comment: "Heading shown when no jobs are available to AI-filter",
                        message: "No jobs to review",
                      })
                    : eligibility.status === "count_unavailable"
                      ? t({
                          id: "search.aiFilter.unavailable.title",
                          comment: "Heading shown when exact AI candidate eligibility cannot be established",
                          message: "Precise matching is temporarily unavailable",
                        })
                      : t({
                          id: "search.aiFilter.addFilters.title",
                          comment: "Heading prompting the user to use normal filters before AI",
                          message: "Start with the job filters",
                        })}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {eligibility.status === "too_broad"
                  ? t({
                      id: "search.aiFilter.narrow.body",
                      comment: "Explains the maximum AI-filter candidate count; variables are current count and maximum",
                      message: `${eligibility.candidateCount} jobs match. Use location, role, level, or another filter to get to ${eligibility.maxCandidates} or fewer.`,
                    })
                  : eligibility.status === "no_matches"
                    ? t({
                        id: "search.aiFilter.empty.body",
                        comment: "Explains that normal filters need to find jobs before Jev can review them",
                        message: "Adjust the current filters until at least one job matches.",
                      })
                    : eligibility.status === "count_unavailable"
                      ? t({
                          id: "search.aiFilter.unavailable.body",
                          comment: "Explains fail-closed behavior when the exact candidate count is unavailable",
                          message: "We could not verify how many jobs are in this search. Try again in a moment.",
                        })
                      : t({
                          id: "search.aiFilter.addFilters.body",
                          comment: "Explains the normal-filter prerequisite for Jev",
                          message: `Choose a role, location, level, or another filter. This option appears when ${eligibility.maxCandidates} or fewer jobs remain.`,
                        })}
              </p>
            </div>
          ) : (
            <form
              className="mt-4"
              onSubmit={(event) => {
                event.preventDefault();
                void apply();
              }}
            >
              <label htmlFor={queryId} className="sr-only">
                {t({
                  id: "search.aiFilter.query.label",
                  comment: "Accessible label for the AI filter natural-language criteria",
                  message: "What should make a job a match?",
                })}
              </label>
              <textarea
                id={queryId}
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                maxLength={1_000}
                rows={4}
                autoFocus
                placeholder={t({
                  id: "search.aiFilter.query.placeholder",
                  comment: "Example natural-language criteria for Jev",
                  message: "e.g. Backend-heavy roles building developer tools. Exclude people management.",
                })}
                className="w-full resize-none rounded-md border border-border-soft bg-background px-3 py-2 text-sm leading-relaxed outline-none transition-colors placeholder:text-muted/70 focus:border-primary/50 focus:ring-2 focus:ring-primary/10"
              />
              {mutationError ? (
                <p className="mt-2 text-xs text-error" role="alert">
                  {mutationError}
                </p>
              ) : null}
              <div className="mt-3 flex justify-end">
                <button
                  type="submit"
                  disabled={query.trim().length === 0 || isApplying || !canApply}
                  className="inline-flex shrink-0 cursor-pointer items-center gap-1.5 whitespace-nowrap rounded-md bg-primary px-3 py-1.5 text-xs font-medium text-primary-contrast transition-opacity hover:opacity-90 disabled:cursor-not-allowed disabled:opacity-40"
                >
                  {isApplying ? (
                    <LoaderCircle size={13} className="animate-spin" aria-hidden="true" />
                  ) : createsWatchlist ? (
                    <Funnel size={13} aria-hidden="true" />
                  ) : null}
                  {isApplying
                    ? createsWatchlist
                      ? t({
                          id: "search.aiFilter.creatingWatchlist",
                          comment: "Pending label while an AI watchlist is being created",
                          message: "Creating…",
                        })
                      : t({
                          id: "search.aiFilter.applying",
                          comment: "Pending label while the AI filter is being applied",
                          message: "Applying…",
                        })
                    : createsWatchlist
                      ? t({
                          id: "search.aiFilter.createWatchlist",
                          comment: "Button to create a watchlist with natural-language AI criteria",
                          message: "Create watchlist",
                        })
                      : t({
                          id: "search.aiFilter.apply",
                          comment: "Button to apply the natural-language AI filter",
                          message: "Apply",
                        })}
                </button>
              </div>
            </form>
          )}
          </div>
          )}
          {isDrawer && activeQuery && canMountDrawerContent ? (
            <div className="min-w-0 max-w-full px-4 pb-2">
              {resolvedDrawerContent}
            </div>
          ) : null}
          </div>
          </section>
        </>
      )}
      {isDrawer ? (
        <ScrollNarrowingReminder
          enabled={
            !open &&
            !isApplying &&
            !activeQuery &&
            !persistedQuery &&
            !isSessionPending
          }
          storageKey={`jobseek:narrow-results-reminder:${watchlistId ?? "watchlist"}`}
          subscriptionRequired={eligibility.status === "subscription_required"}
          signedOut={!isLoggedIn}
          hasNarrowedResults={Boolean(activeQuery || persistedQuery)}
          narrowedResultCount={safeNarrowedResultCount}
          onAction={handleReminderAction}
        />
      ) : null}
    </div>
  );
}
