import "server-only";
import { randomUUID } from "node:crypto";
import { sql } from "drizzle-orm";
import { db } from "@/db";
import { paidEntitlementCondition } from "@/lib/paid-entitlement";
import { aiFilterRepairDueCondition } from "./refresh-cadence";

export type WorkTarget = {
  ownerId: string;
  watchlistId: string;
  queryVersionId: string;
  kind: "historical" | "freshness";
  demandTargetOffset: number;
  acceptedTarget?: number;
  /** Canonical scope observed by this claim; invalidates within-slice verification. */
  scopeVersion?: string;
  generation: string;
  leaseOwner: string;
  query?: string;
  candidateLanguages?: string[];
};

/** Commit demand before returning to the caller. Duplicate tabs only extend it. */
export async function enqueueAiFilterWork(input: {
  ownerId: string;
  watchlistId: string;
  queryVersionId?: string;
  kind?: "historical" | "freshness";
  demandTargetOffset: number;
  /** Owner demand for a result page; background callers never acquire priority. */
  acceptedTarget?: number;
}): Promise<{ runId: string }> {
  if (
    !Number.isSafeInteger(input.demandTargetOffset) ||
    input.demandTargetOffset < 1 ||
    input.demandTargetOffset > 10_000
  ) {
    throw new TypeError("Invalid narrowed demand");
  }
  if (input.acceptedTarget !== undefined &&
    (!Number.isSafeInteger(input.acceptedTarget) || input.acceptedTarget < 1 || input.acceptedTarget > 10_000)) {
    throw new TypeError("Invalid narrowed result target");
  }
  const kind = input.kind ?? "historical";
  const interactive = kind === "historical" && input.acceptedTarget !== undefined;
  const rows = await db.execute(sql`
    WITH demand AS (INSERT INTO ai_filter_work (query_version_id,kind,watchlist_id,owner_id,target_offset,interactive_until,accepted_target)
    SELECT q.id,${kind},c.watchlist_id,c.owner_id,${input.demandTargetOffset},
      CASE WHEN ${interactive} THEN now()+interval '2 minutes' ELSE NULL END,${input.acceptedTarget ?? null}
    FROM ai_filter_configuration c JOIN ai_filter_query_version q ON q.configuration_id=c.id AND q.revision=c.current_revision
    WHERE c.watchlist_id=${input.watchlistId}::uuid AND c.owner_id=${input.ownerId} AND c.status='enabled'
      AND (${input.queryVersionId ?? null}::uuid IS NULL OR q.id=${input.queryVersionId ?? null}::uuid)
      AND ${paidEntitlementCondition(sql`c.owner_id`, new Date())}
    ON CONFLICT (query_version_id,kind) DO UPDATE SET
      target_offset=GREATEST(ai_filter_work.target_offset,excluded.target_offset),
      generation=ai_filter_work.generation+1,
      interactive_until=GREATEST(ai_filter_work.interactive_until,excluded.interactive_until),
      accepted_target=GREATEST(ai_filter_work.accepted_target,excluded.accepted_target),
      status=CASE WHEN ai_filter_work.status='processing' THEN 'processing' ELSE 'queued' END,
      available_at=CASE WHEN ai_filter_work.status='paused' THEN ai_filter_work.available_at ELSE LEAST(ai_filter_work.available_at,now()) END,
      updated_at=now()
    RETURNING query_version_id)
    SELECT query_version_id,pg_notify('jobseek_narrowed_work','') FROM demand
  `);
  if (!rows.length) throw new Error("Narrowed demand is unavailable");
  // Legacy response field kept compatible; this identifies durable demand, not a Workflow run.
  return { runId: `demand:${rows[0].query_version_id}:${kind}` };
}

/** Admit only ready demand or due repair; keep idle ticks to one SQL statement. */
export async function claimAiFilterWorker(): Promise<string | null> {
  const leaseOwner = `shared-worker:${randomUUID()}`;
  const now = new Date();
  const rows = await db.execute(sql`
    UPDATE ai_filter_worker SET lease_owner=${leaseOwner},lease_expires_at=now()+interval '70 seconds',next_tick_at=now()+interval '2 seconds'
    WHERE id=1 AND (lease_expires_at IS NULL OR lease_expires_at<=now())
      AND (next_tick_at<=now() OR EXISTS (
        SELECT 1 FROM ai_filter_work w JOIN ai_filter_configuration c ON c.watchlist_id=w.watchlist_id
        JOIN ai_filter_query_version q ON q.configuration_id=c.id AND q.revision=c.current_revision AND q.id=w.query_version_id
        WHERE c.status='enabled' AND w.kind='historical' AND w.interactive_until>now()
          AND w.status='queued' AND w.available_at<=now()
          AND ${paidEntitlementCondition(sql`w.owner_id`, now)}
      )) AND (blocked_until IS NULL OR blocked_until<=now())
      AND EXISTS (
        SELECT 1 FROM ai_filter_configuration c
        JOIN ai_filter_query_version q ON q.configuration_id=c.id AND q.revision=c.current_revision
        WHERE c.status='enabled' AND ${paidEntitlementCondition(sql`c.owner_id`, now)}
          AND (
            ${aiFilterRepairDueCondition(sql`c.refresh_requested_at`, now)}
            OR EXISTS (
              SELECT 1 FROM ai_filter_work w
              WHERE w.watchlist_id=c.watchlist_id AND w.owner_id=c.owner_id AND w.query_version_id=q.id
                AND w.status<>'idle' AND w.available_at<=now()
                AND (w.lease_expires_at IS NULL OR w.lease_expires_at<=now())
            )
          )
      ) RETURNING id
  `);
  return rows.length ? leaseOwner : null;
}

export async function claimAiFilterWork(
  leaseOwner: string,
): Promise<WorkTarget | null> {
  return db.transaction(async (tx) => {
    const [worker] = await tx.execute(
      sql`SELECT next_lane,interactive_streak FROM ai_filter_worker WHERE id=1 AND lease_owner=${leaseOwner} AND lease_expires_at>now() FOR UPDATE`,
    );
    if (!worker) return null;
    const rows = await tx.execute(sql`
      SELECT w.*,q.query_text,q.candidate_languages,
        jsonb_build_array(wl.filters,(SELECT array_agg(wc.company_id ORDER BY wc.company_id)
          FROM watchlist_company wc WHERE wc.watchlist_id=w.watchlist_id))::text AS scope_version,
        (w.kind='historical' AND w.interactive_until>now()) AS interactive
      FROM ai_filter_work w JOIN ai_filter_configuration c ON c.watchlist_id=w.watchlist_id AND c.owner_id=w.owner_id
      JOIN watchlist wl ON wl.id=w.watchlist_id AND wl.user_id=w.owner_id
      JOIN ai_filter_query_version q ON q.id=w.query_version_id AND q.configuration_id=c.id AND q.revision=c.current_revision
      WHERE c.status='enabled' AND ${paidEntitlementCondition(sql`w.owner_id`, new Date())}
        AND w.status<>'idle' AND w.available_at<=now() AND (w.lease_expires_at IS NULL OR w.lease_expires_at<=now())
      ORDER BY
        CASE WHEN w.kind='historical' AND w.interactive_until>now() THEN
          CASE WHEN ${worker.interactive_streak}>=3 THEN 2 ELSE 0 END
        ELSE 1 END,
        CASE WHEN w.kind='historical' AND w.interactive_until>now() AND w.accepted_target<=20 THEN 0 ELSE 1 END,
        (w.kind=${worker.next_lane}) DESC,w.available_at,w.updated_at,w.query_version_id
      LIMIT 1 FOR UPDATE OF w SKIP LOCKED
    `);
    if (!rows.length) return null;
    const row = rows[0];
    await tx.execute(sql`UPDATE ai_filter_work SET status='processing',lease_owner=${leaseOwner},lease_expires_at=now()+interval '70 seconds',updated_at=now()
      WHERE query_version_id=${row.query_version_id}::uuid AND kind=${row.kind}`);
    await tx.execute(
      sql`UPDATE ai_filter_worker SET next_lane=${row.kind === "historical" ? "freshness" : "historical"},
        interactive_streak=CASE WHEN ${row.interactive === true}
          THEN LEAST(interactive_streak+1,3) ELSE 0 END WHERE id=1`,
    );
    return {
      ownerId: String(row.owner_id),
      watchlistId: String(row.watchlist_id),
      queryVersionId: String(row.query_version_id),
      kind: row.kind as WorkTarget["kind"],
      demandTargetOffset: Number(row.target_offset),
      acceptedTarget: row.accepted_target == null ? undefined : Number(row.accepted_target),
      scopeVersion: String(row.scope_version),
      generation: String(row.generation),
      leaseOwner,
      query: String(row.query_text),
      candidateLanguages: row.candidate_languages as string[],
    };
  });
}

export async function finishAiFilterWork(
  target: WorkTarget,
  outcome: string,
  reason?: string,
): Promise<void> {
  const complete = ["caught_up", "demand_satisfied", "disabled"].includes(
    outcome,
  );
  const paused = outcome.startsWith("paused_") || outcome === "failed";
  // Provider/quota failures cannot manufacture a minute retry loop. Failures
  // back off 5m..1h; entitlement is checked again before claiming work.
  await db.execute(sql`
    UPDATE ai_filter_work SET
      status=CASE WHEN generation<>${target.generation}::bigint THEN 'queued' WHEN ${complete} THEN 'idle' WHEN ${paused} THEN 'paused'
        WHEN kind='historical' AND accepted_target IS NOT NULL AND
          (SELECT count(*) FROM ai_filter_decision d WHERE d.query_version_id=ai_filter_work.query_version_id
            AND d.expires_at>now() AND COALESCE(d.user_override,d.model_decision)='accepted')>=accepted_target THEN 'idle'
        ELSE 'queued' END,
      available_at=CASE WHEN ${paused} THEN now()+LEAST(3600,300*power(2,LEAST(attempts,4))) * interval '1 second' WHEN ${outcome === "busy"} THEN now()+interval '70 seconds' ELSE now() END,
      attempts=CASE WHEN ${paused} THEN attempts+1 ELSE 0 END,
      reason=${paused ? (reason ?? outcome) : null},lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
    WHERE query_version_id=${target.queryVersionId}::uuid AND kind=${target.kind} AND lease_owner=${target.leaseOwner}
  `);
}

export async function releaseAiFilterWorker(
  leaseOwner: string,
  failed: boolean,
): Promise<void> {
  await db.execute(sql`
    UPDATE ai_filter_worker SET lease_owner=NULL,lease_expires_at=NULL,
      failures=CASE WHEN ${failed} THEN failures+1 ELSE 0 END,
      blocked_until=CASE WHEN ${failed} AND failures>=2 THEN now()+interval '15 minutes' ELSE NULL END,
      reason=CASE WHEN ${failed} AND failures>=2 THEN 'worker_unavailable' ELSE NULL END
    WHERE id=1 AND lease_owner=${leaseOwner}
  `);
}

/** Publish verified empty undecided coverage without creating an idle segment. */
export async function markAiFilterRepairCaughtUp(input: {
  watchlistId: string;
  queryVersionId: string;
}): Promise<void> {
  await db.transaction(async (tx) => {
    await tx.execute(sql`UPDATE ai_filter_configuration c SET last_sweep_at=now(),last_caught_up_at=GREATEST(c.last_caught_up_at,q.horizon_ends_at)
      FROM ai_filter_query_version q WHERE q.id=${input.queryVersionId}::uuid AND q.configuration_id=c.id AND q.revision=c.current_revision
      AND c.watchlist_id=${input.watchlistId}::uuid AND c.status='enabled'`);
    await tx.execute(sql`UPDATE ai_filter_work SET status='idle',reason=NULL,lease_owner=NULL,lease_expires_at=NULL,updated_at=now()
      WHERE query_version_id=${input.queryVersionId}::uuid AND kind='freshness' AND status<>'processing'`);
  });
}
