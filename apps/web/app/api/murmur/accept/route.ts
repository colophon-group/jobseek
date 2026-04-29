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
 *   6. Idempotency lookup against `murmur_accept_log`.
 *      - same run_id + same hash → 200 `{ applied: false, reason:
 *        "already_applied" }`.
 *      - same run_id + different hash → 200 `{ applied: false, reason:
 *        "body_mismatch" }` + warning log.
 *   7. Re-run probes via `rerunProbes`.
 *      - lib failure → 200 `{ ok: false, errors: [...] }` (NOT 5xx —
 *        the issue explicitly says "so Murmur doesn't retry forever").
 *      - timeout → 504 `{ ok: false, errors: ["probe_timeout"] }`.
 *   8. Apply catalog via `applyCatalog`. Wrapped with the ledger insert
 *      in one transaction — the catalog write and the idempotency row
 *      land or both roll back.
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

export const runtime = "nodejs";

/**
 * Hand the request through the accept pipeline. Returns the
 * `NextResponse` the framework propagates verbatim.
 */
export async function POST(_request: Request): Promise<NextResponse> {
  // Force the unused import to register so lint doesn't flag it during
  // the interfaces-first commit. Real implementation lives in the next
  // commit.
  void requireBearer;
  void errJson;
  void okJson;
  throw new Error("not implemented");
}

/**
 * Header name Murmur uses for the run-id idempotency key.
 *
 * Lower-cased per the canonical-form convention that already lives in
 * `_lib/headers.ts`. Reading the header from `request.headers.get(...)`
 * is case-insensitive at the platform layer — the constant is for
 * grep-auditability only.
 */
export const HEADER_IDEMPOTENCY_KEY = "idempotency-key" as const;
