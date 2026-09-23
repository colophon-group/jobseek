import type { WatchlistPostingEntry } from "@/lib/actions/watchlists";

export type AiFilterUiStatus =
  | "idle"
  | "processing"
  | "caught_up"
  | "paused_entitlement"
  | "paused_budget"
  | "provider_unavailable"
  | "waiting_for_jev"
  | "paused_kill"
  | "cancelled"
  | "failed"
  | "disabled";

/** Serializable owner-only state consumed by the watchlist client. */
export type AiFilterUiState = Readonly<{
  watchlistId: string;
  enabled: boolean;
  entitled: boolean;
  query: string;
  queryRevision: number;
  queryVersionId: string;
  status: AiFilterUiStatus;
  counts: Readonly<{ accepted: number; rejected: number; total: number }>;
  progress: Readonly<{
    selectionOffset: number;
    scannedCount: number;
    completedCount: number;
    stopReason: string | null;
  }>;
  lastCaughtUpAt: string | null;
  latestEventSequence: number;
}>;

export type AiFilterAcceptedPage = Readonly<{
  /** Query revision that owns the candidate-feed cursor below. */
  queryVersionId?: string;
  postings: WatchlistPostingEntry[];
  /** Current active postings in the accepted bucket (present on page zero). */
  total?: number;
  nextOffset: number;
  hasMore: boolean;
}>;
