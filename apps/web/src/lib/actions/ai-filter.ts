"use server";

import {
  AiFilterEntitlementError,
  disableAiFilterConfiguration,
  putAiFilterConfiguration,
} from "@/lib/ai-filter/configuration-service";
import type { AiFilterUiState } from "@/lib/ai-filter/ui-contract";
import { getSessionUserId } from "@/lib/sessionCache";
import { createWatchlist, deleteWatchlist } from "@/lib/services/watchlists";
import type { SearchWatchlistDraft } from "@/lib/search/watchlist-draft";
import { isWatchlistId } from "@/lib/watchlist-id";
import { logExternalError } from "@/lib/safe-external-error";
import { assertAiFilterCandidateScope } from "@/lib/ai-filter/candidate-loader";

type AiFilterMutationError =
  | "invalid_request"
  | "limit_reached"
  | "not_authenticated"
  | "not_found"
  | "subscription_required"
  | "temporarily_unavailable";

function mappedCreationError(error: string): AiFilterMutationError {
  return error === "limit_reached" ? error : "invalid_request";
}

export async function configureAiFilter(
  watchlistId: string,
  query: string,
): Promise<{ ok: true; state: AiFilterUiState } | { error: AiFilterMutationError }> {
  const ownerId = await getSessionUserId();
  if (!ownerId) return { error: "not_authenticated" };
  if (!isWatchlistId(watchlistId)) return { error: "not_found" };

  try {
    await assertAiFilterCandidateScope({ ownerId, watchlistId });
    const state = await putAiFilterConfiguration({ ownerId, watchlistId, query });
    return { ok: true, state };
  } catch (error) {
    if (error instanceof AiFilterEntitlementError) {
      return { error: "subscription_required" };
    }
    if (error instanceof TypeError) return { error: "invalid_request" };
    logExternalError(
      "error",
      { service: "database", operation: "configure_ai_filter_watchlist" },
      error,
    );
    return { error: "temporarily_unavailable" };
  }
}

export async function disableAiFilter(
  watchlistId: string,
): Promise<{ ok: true } | { error: AiFilterMutationError }> {
  const ownerId = await getSessionUserId();
  if (!ownerId) return { error: "not_authenticated" };
  if (!isWatchlistId(watchlistId)) return { error: "not_found" };

  try {
    await disableAiFilterConfiguration({ ownerId, watchlistId });
    return { ok: true };
  } catch (error) {
    logExternalError(
      "error",
      { service: "database", operation: "disable_ai_filter_watchlist" },
      error,
    );
    return { error: "temporarily_unavailable" };
  }
}

/** Create the saved search and configure it as one user-facing operation. */
export async function createAiFilteredWatchlist(input: {
  draft: SearchWatchlistDraft;
  query: string;
}): Promise<{ id: string; slug: string } | { error: AiFilterMutationError }> {
  const ownerId = await getSessionUserId();
  if (!ownerId) return { error: "not_authenticated" };

  const created = await createWatchlist(input.draft);
  if ("error" in created) return { error: mappedCreationError(created.error) };

  try {
    await assertAiFilterCandidateScope({
      ownerId,
      watchlistId: created.id,
    });
    await putAiFilterConfiguration({
      ownerId,
      watchlistId: created.id,
      query: input.query,
    });
    return created;
  } catch (error) {
    // Do not leave an ordinary watchlist behind when precise matching could
    // not be attached. The next click remains a clean retry.
    try {
      const rollback = await deleteWatchlist(created.id);
      if (!rollback.ok) throw new Error("Created watchlist rollback was rejected");
    } catch (rollbackError) {
      logExternalError(
        "error",
        { service: "database", operation: "rollback_ai_watchlist_create" },
        rollbackError,
      );
    }

    if (error instanceof AiFilterEntitlementError) {
      return { error: "subscription_required" };
    }
    if (error instanceof TypeError) return { error: "invalid_request" };
    logExternalError(
      "error",
      { service: "database", operation: "create_ai_filter_watchlist" },
      error,
    );
    return { error: "temporarily_unavailable" };
  }
}
