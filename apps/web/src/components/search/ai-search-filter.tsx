"use client";

import { useEffect, useId, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { Crown, Funnel, LoaderCircle, X } from "lucide-react";
import { useLingui } from "@lingui/react/macro";

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
import {
  getAiSearchEligibility,
  type AiSearchEligibility,
} from "@/lib/ai-filter/search-eligibility";

export type AiSearchFilterDemoState =
  | "free"
  | "add-filters"
  | "too-broad"
  | "eligible";

export function parseAiSearchFilterDemoState(
  value: string | null,
): AiSearchFilterDemoState | undefined {
  return value === "free" ||
    value === "add-filters" ||
    value === "too-broad" ||
    value === "eligible"
    ? value
    : undefined;
}

type Props = {
  isSubscribed: boolean;
  hasSearchFilters: boolean;
  candidateCount?: number;
  isSearchPending?: boolean;
  demoState?: AiSearchFilterDemoState;
  onApply?: (query: string) => Promise<void>;
  /** The prompt creates a new watchlist rather than refining an existing one. */
  createsWatchlist?: boolean;
  /** Saved-search scope used when this control creates a watchlist. */
  watchlistDraft?: SearchWatchlistDraft;
  /** Existing watchlist to configure when this control refines its feed. */
  watchlistId?: string;
  /** Persisted criteria for an already-configured watchlist. */
  initialQuery?: string | null;
  /** Keeps the watchlist result surface in sync with configuration changes. */
  onStateChange?: (state: AiFilterUiState | null) => void;
  align?: "left" | "right";
};

function demoEligibility(
  state: AiSearchFilterDemoState | undefined,
  fallback: AiSearchEligibility,
): AiSearchEligibility {
  if (!state) return fallback;
  if (state === "free") {
    return getAiSearchEligibility({
      isSubscribed: false,
      hasSearchFilters: true,
      candidateCount: 24,
    });
  }
  if (state === "add-filters") {
    return getAiSearchEligibility({
      isSubscribed: true,
      hasSearchFilters: false,
      candidateCount: undefined,
    });
  }
  if (state === "too-broad") {
    return getAiSearchEligibility({
      isSubscribed: true,
      hasSearchFilters: true,
      candidateCount: 12_500,
    });
  }
  return getAiSearchEligibility({
    isSubscribed: true,
    hasSearchFilters: true,
    candidateCount: 24,
  });
}

export function AiSearchFilter({
  isSubscribed,
  hasSearchFilters,
  candidateCount,
  isSearchPending = false,
  demoState,
  onApply,
  createsWatchlist = false,
  watchlistDraft,
  watchlistId,
  initialQuery,
  onStateChange,
  align = "left",
}: Props) {
  const { t } = useLingui();
  const router = useRouter();
  const lp = useLocalePath();
  const browserSearchParams = useBrowserSearchParams();
  const { isLoggedIn, isPending: isSessionPending } = useSession();
  const panelId = useId();
  const queryId = useId();
  const resumeRequested = browserSearchParams.get("narrow") === "1";
  const [open, setOpen] = useState(demoState !== undefined || resumeRequested);
  const persistedQuery = initialQuery?.trim() || null;
  const [query, setQuery] = useState(persistedQuery ?? "");
  const [isApplying, setIsApplying] = useState(false);
  const [activeQuery, setActiveQuery] = useState<string | null>(persistedQuery);
  const appliedQueryRef = useRef<string | null>(persistedQuery);
  const [mutationError, setMutationError] = useState("");

  useEffect(() => {
    if (demoState !== undefined || resumeRequested) setOpen(true);
  }, [demoState, resumeRequested]);

  useEffect(() => {
    appliedQueryRef.current = persistedQuery;
    setQuery(persistedQuery ?? "");
    setActiveQuery(persistedQuery);
  }, [persistedQuery]);

  const eligibility = demoEligibility(
    demoState,
    getAiSearchEligibility({
      isSubscribed,
      hasSearchFilters,
      candidateCount: isSearchPending ? undefined : candidateCount,
    }),
  );
  const eligible = eligibility.status === "eligible";
  const canApply = Boolean(
    onApply ||
    (createsWatchlist && watchlistDraft) ||
    (!createsWatchlist && watchlistId),
  );

  function resumePath(): string {
    const url = new URL(window.location.href);
    url.searchParams.set("narrow", "1");
    return `${url.pathname}${url.search}${url.hash}`;
  }

  function clearResumeRequest() {
    if (!resumeRequested) return;
    const url = new URL(window.location.href);
    url.searchParams.delete("narrow");
    window.history.replaceState(
      window.history.state,
      "",
      `${url.pathname}${url.search}${url.hash}`,
    );
  }

  function closePanel() {
    setOpen(false);
    if (!createsWatchlist) setActiveQuery(appliedQueryRef.current);
    clearResumeRequest();
  }

  function goToSignIn() {
    router.push(withAuthReturnPath(lp("/sign-in"), resumePath()));
  }

  function goToSubscription() {
    const params = new URLSearchParams({ next: resumePath() });
    router.push(`${lp("/settings/billing")}?${params.toString()}`);
  }

  // The control is progressive disclosure: ordinary filters must first
  // produce a non-empty, economically bounded candidate set. Demo states
  // remain available locally so each gate can be reviewed deliberately.
  if (
    !activeQuery &&
    !persistedQuery &&
    demoState === undefined &&
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
      if (onApply) {
        await onApply(normalized);
      } else if (createsWatchlist && watchlistDraft) {
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
      }
      closePanel();
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
        setOpen(true);
        setMutationError(t({
          id: "search.aiFilter.removeFailed",
          comment: "Error shown when precise matching cannot be removed from a watchlist",
          message: "Could not remove these matching criteria. Try again.",
        }));
        return;
      }
      setActiveQuery(null);
      setQuery("");
      appliedQueryRef.current = null;
      onStateChange?.(null);
    } finally {
      setIsApplying(false);
    }
  }

  if (activeQuery) {
    return (
      <div className="flex min-w-0 items-center gap-2">
        <button
          type="button"
          onClick={() => {
            setQuery(activeQuery);
            setActiveQuery(null);
            setOpen(true);
          }}
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
    <div className="relative">
      <Button
        type="button"
        onClick={() => {
          if (open) closePanel();
          else setOpen(true);
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

      {open && (
        <>
          <div
            aria-hidden="true"
            className="fixed inset-0 z-20 bg-black/30 backdrop-blur-[1px] sm:hidden"
          />
          <section
            id={panelId}
            aria-labelledby={`${panelId}-title`}
            className={`fixed inset-x-4 top-28 z-30 max-h-[calc(100vh-9rem)] w-auto overflow-y-auto rounded-lg border border-border-soft bg-surface p-4 shadow-xl shadow-black/10 sm:absolute sm:inset-x-auto sm:top-10 sm:max-h-none sm:w-[min(28rem,calc(100vw-2rem))] sm:overflow-visible ${
              align === "right" ? "sm:right-0" : "sm:left-0"
            }`}
          >
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
                <span className="rounded-full bg-primary/10 px-1.5 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-primary">
                  {t({ id: "common.plan.pro", comment: "Short Pro subscription badge", message: "Pro" })}
                </span>
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

          {eligibility.status === "subscription_required" && isSessionPending && demoState === undefined ? (
            <div className="mt-4 flex items-center gap-2 rounded-md border border-border-soft bg-background p-3 text-sm text-muted" role="status">
              <LoaderCircle size={15} className="animate-spin" aria-hidden="true" />
              {t({
                id: "search.aiFilter.accessChecking",
                comment: "Status while precise-matching account access is being checked",
                message: "Checking access…",
              })}
            </div>
          ) : eligibility.status === "subscription_required" && !isLoggedIn && demoState === undefined ? (
            <div className="mt-4 rounded-md border border-border-soft bg-background p-3">
              <p className="text-sm font-medium">
                {t({
                  id: "search.aiFilter.login.title",
                  comment: "Heading asking the viewer to sign in before creating a precisely matched watchlist",
                  message: "Sign in to continue",
                })}
              </p>
              <p className="mt-1 text-xs leading-relaxed text-muted">
                {t({
                  id: "search.aiFilter.login.body",
                  comment: "Explains that the current search will be restored after sign-in",
                  message: "Your current search will be restored when you return.",
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
                  ) : (
                    <Funnel size={13} aria-hidden="true" />
                  )}
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
                          message: "Narrow down results",
                        })}
                </button>
              </div>
            </form>
          )}
          </section>
        </>
      )}
    </div>
  );
}
