import "server-only";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { getAiFilterOwnerState } from "./configuration-service";
import { loadAiFilterCandidatePage } from "./candidate-loader";
import { aiFilterHistoricalHorizonStart, aiFilterHorizonEnd } from "./horizon";

/** Bearer-protected, read-only evidence. Never return query text or owner identity. */
export async function inspectAiFilterRecovery(watchlistId: string) {
  const [row] = await db.execute<{
    owner_id: string;
    filter_fingerprint: string;
    candidate_languages: string[];
  }>(sql`
    SELECT c.owner_id,q.filter_fingerprint,q.candidate_languages FROM ai_filter_configuration c
    JOIN ai_filter_query_version q ON q.configuration_id=c.id AND q.revision=c.current_revision
    WHERE c.watchlist_id=${watchlistId}::uuid AND c.status='enabled'
  `);
  if (!row) throw new Error("Unavailable recovery scope");
  const state = await getAiFilterOwnerState({
    ownerId: row.owner_id,
    watchlistId,
  });
  const page = await loadAiFilterCandidatePage({
    ownerId: row.owner_id,
    watchlistId,
    offset: 0,
    windowStart: aiFilterHistoricalHorizonStart(),
    windowEnd: aiFilterHorizonEnd(new Date()),
    onlyUndecidedForQueryVersionId: state.queryVersionId,
    candidateLimit: 1,
    signal: AbortSignal.timeout(15_000),
  });
  const [fence] = await db.execute<{ enabled: boolean }>(
    sql`SELECT EXISTS (SELECT 1 FROM pg_trigger WHERE tgname='ai_filter_shared_worker_fence' AND tgenabled='O') AS enabled`,
  );
  return {
    status: state.status,
    queryRevision: state.queryRevision,
    queryVersionId: state.queryVersionId,
    filterFingerprint: row.filter_fingerprint,
    candidateLanguages: row.candidate_languages,
    entitled: state.entitled,
    reviewed: state.counts.total,
    accepted: state.counts.accepted,
    remaining: page.total,
    coverage: state.coverage,
    legacyWorkersFenced: fence?.enabled === true,
  };
}
