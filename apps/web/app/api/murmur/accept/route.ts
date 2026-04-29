/**
 * POST /api/murmur/accept — the Murmur webhook accept handler.
 *
 * Murmur POSTs the composed `final_output` for a completed run here,
 * with `Authorization: Bearer <MURMUR_TOKEN>` and
 * `Idempotency-Key: <run_id>`. Murmur retries non-2xx once after 30s.
 *
 * Request handling order — strict, no shortcuts:
 *
 *   1. Bearer auth (`requireBearer`). 401 on missing / wrong / disabled.
 *   2. Body cap (5 MB). 413 on overflow.
 *   3. `Idempotency-Key` header read; missing → 400.
 *   4. Body parse + canonical hash; invalid JSON → 400.
 *   5. Schema validation against `FINAL_OUTPUT_SCHEMA`.
 *      - failure → 400 with `errors: ["validation:<path>:<message>"]`.
 *   6. Idempotency lookup against the in-memory + DB ledger.
 *      - same run_id + same hash → 200 `{ applied: false, reason:
 *        "already_applied" }`.
 *      - same run_id + different hash → 200 `{ applied: false, reason:
 *        "body_mismatch" }` + warning log.
 *   7. Re-run probes via `rerunProbes`.
 *      - lib failure → 200 `{ ok: false, errors: [...] }` (NOT 5xx —
 *        the issue explicitly says "so Murmur doesn't retry forever").
 *      - timeout → 504 `{ ok: false, errors: ["probe_timeout"] }`.
 *   8. Apply catalog via `applyCatalog`. Wrapped with the ledger insert
 *      in one transaction.
 *      - I/O failure → 200 `{ ok: false, errors: ["catalog_write_failed"] }`.
 *   9. Return 200 `{ ok: true, data: { run_id, applied: true,
 *      company_id, board_count, warnings? } }`.
 *
 * Auth, idempotency, and the M0 envelope are NEVER skipped.
 *
 * @see colophon-group/jobseek#2763
 * @see Murmur DESIGN.md §4.1 (Webhook accept-handler contract)
 */

import { NextResponse } from "next/server";
import { requireBearer } from "../_lib/auth";
import { errJson, okJson } from "../_lib/envelope";
import {
  parseAndCapBody,
  rerunProbes,
} from "../_lib/accept-pipeline";
import { FINAL_OUTPUT_SCHEMA, type FinalOutput } from "../_lib/accept-schema";
import { validateAcceptBody } from "../_lib/accept-validate";
import {
  applyCatalog,
  resolveCatalogTarget,
  CatalogIdempotencyConflict,
} from "../_lib/catalog";
import { readLedger, sha256Canonical } from "../_lib/idempotency";

export const runtime = "nodejs";

/**
 * Header name Murmur uses for the run-id idempotency key.
 *
 * Lower-cased per the canonical-form convention from `_lib/headers.ts`.
 * Reading via `request.headers.get(...)` is case-insensitive at the
 * platform layer — the constant is for grep-auditability only.
 */
export const HEADER_IDEMPOTENCY_KEY = "idempotency-key" as const;

/**
 * In-process idempotency ledger.
 *
 * The Postgres ledger (`murmur_accept_log`) is the durable source of
 * truth, but unit tests don't carry a database. We keep a tiny
 * per-process Map so the unit-test "same run_id replay" case works
 * end-to-end without hitting the DB. Production usage is the same: the
 * map serves as a hot cache; the DB UNIQUE constraint is the
 * authoritative guard.
 *
 * The map only stores `{ runId → bodyHash }`. Catalog state lives in
 * the DB / CSV — never in this map.
 *
 * Eviction: there is no eviction. Demo-grade. The map grows by one
 * entry per accepted run; for the demo budget that's negligible.
 */
const inProcessLedger = new Map<string, string>();

/**
 * Test-only access to the in-process Map. Cold-start replay tests
 * clear it between calls to simulate a process restart while leaving
 * the durable ledger populated. Production code never reads this.
 */
export const __ledger = inProcessLedger;

interface AcceptResponseData {
  readonly run_id: string;
  readonly applied: boolean;
  readonly reason?: "already_applied" | "body_mismatch";
  readonly company_id?: string | null;
  readonly board_count?: number;
  readonly warnings?: readonly string[];
}

/**
 * Hand the request through the accept pipeline. Returns the
 * `NextResponse` the framework propagates verbatim.
 */
export async function POST(request: Request): Promise<NextResponse> {
  // 1. Bearer auth — first line of work, before any body read.
  const authFail = requireBearer(request);
  if (authFail) return authFail;

  // 2. Body cap + parse. Reads the buffer, refuses anything over
  //    5 MB regardless of what `Content-Length` claimed.
  const parsed = await parseAndCapBody(request);
  if (parsed.status === "too_large") {
    return errJson(["payload_too_large"], { status: 413 });
  }
  if (parsed.status === "invalid_json") {
    return errJson(["invalid_json"], { status: 400 });
  }

  // 3. Required header.
  const runId = (request.headers.get(HEADER_IDEMPOTENCY_KEY) ?? "").trim();
  if (!runId) {
    return errJson([`missing_header:${HEADER_IDEMPOTENCY_KEY}`], {
      status: 400,
    });
  }

  // 4. Schema validation.
  const schemaErrors = validateAcceptBody(parsed.body, FINAL_OUTPUT_SCHEMA);
  if (schemaErrors.length > 0) {
    return errJson(
      schemaErrors.map((e) => `validation:${e.path}:${e.message}`),
      { status: 400 },
    );
  }
  const body = parsed.body as FinalOutput;
  const bodyHash = sha256Canonical(body);

  // 5. Idempotency classification.
  //
  //    Two-tier lookup. The in-process Map is a hot cache; the durable
  //    ledger (`murmur_accept_log` for Postgres, `murmur_accept_log.csv`
  //    for the CSV target) is the source of truth. After a process
  //    restart the Map is empty, so we MUST consult the durable ledger
  //    on a Map miss — otherwise a Murmur retry replaying against a
  //    fresh process would either (Postgres) crash on the UNIQUE
  //    constraint and surface a 200/ok:false instead of the correct
  //    `already_applied`, or (CSV) blindly append a duplicate row.
  const seenHash = inProcessLedger.get(runId);
  const classify = await classifyIdempotency(runId, bodyHash, seenHash);
  if (classify.status === "already_applied") {
    return okJson<AcceptResponseData>({
      run_id: runId,
      applied: false,
      reason: "already_applied",
    });
  }
  if (classify.status === "body_mismatch") {
    console.warn(
      `[murmur accept] run_id=${runId} re-fired with a different body; discarding the new payload (hash mismatch)`,
    );
    return okJson<AcceptResponseData>({
      run_id: runId,
      applied: false,
      reason: "body_mismatch",
    });
  }

  // 6. Defense-in-depth probe re-run.
  const rerun = await rerunProbes(body);
  if (rerun.status === "timeout") {
    return errJson(["probe_timeout"], { status: 504 });
  }
  if (rerun.status === "failed") {
    return errJson(rerun.errors);
  }

  // 7. Catalog write.
  const target = resolveCatalogTarget();
  let result;
  try {
    result = await applyCatalog(target, body, { runId, bodyHash });
  } catch (err) {
    if (err instanceof CatalogIdempotencyConflict) {
      // Concurrent first-write race; the other writer landed first.
      // Cache the hash so subsequent re-fires hit the fast path.
      inProcessLedger.set(runId, bodyHash);
      return okJson<AcceptResponseData>({
        run_id: runId,
        applied: false,
        reason: "already_applied",
      });
    }
    console.error(
      `[murmur accept] catalog write failed for run_id=${runId}: ${(err as Error).message}`,
    );
    return errJson(["catalog_write_failed"]);
  }

  // 8. Record the hash for future re-fires within this process.
  inProcessLedger.set(runId, bodyHash);

  return okJson<AcceptResponseData>({
    run_id: runId,
    applied: true,
    company_id: result.companyId,
    board_count: result.boardCount,
    ...(result.warnings.length > 0 ? { warnings: result.warnings } : {}),
  });
}

/**
 * Resolve idempotency status for the inbound `runId + bodyHash`.
 *
 * Order:
 *   1. In-process Map — hot cache, avoids a DB round-trip on the happy
 *      path within a single process.
 *   2. Durable ledger (`readLedger`) — `murmur_accept_log` for the
 *      Postgres target, `murmur_accept_log.csv` for the CSV target.
 *      Source of truth across process restarts.
 *
 * On a durable hit we backfill the Map so subsequent requests stay on
 * the fast path.
 */
async function classifyIdempotency(
  runId: string,
  bodyHash: string,
  seenHash: string | undefined,
): Promise<
  | { readonly status: "fresh" }
  | { readonly status: "already_applied" }
  | { readonly status: "body_mismatch" }
> {
  if (seenHash !== undefined) {
    return seenHash === bodyHash
      ? { status: "already_applied" }
      : { status: "body_mismatch" };
  }
  const durable = await readLedger(runId, bodyHash);
  if (durable.status === "already_applied") {
    inProcessLedger.set(runId, bodyHash);
    return { status: "already_applied" };
  }
  if (durable.status === "body_mismatch") {
    // Map cache stores the hash of the FIRST body — but we only have
    // the inbound (mismatched) hash to hand. Skip the cache populate;
    // a subsequent re-fire of the FIRST body will repopulate it via
    // `already_applied`. The durable ledger remains authoritative.
    return { status: "body_mismatch" };
  }
  return { status: "fresh" };
}
