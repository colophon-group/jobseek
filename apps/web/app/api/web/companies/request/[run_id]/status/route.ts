/**
 * GET /api/web/companies/request/{run_id}/status
 *
 * Same-origin proxy that lets the browser poll a Murmur run's progress
 * without ever holding `MURMUR_TOKEN`. Forwards to `GET {MURMUR_URL}/runs/{run_id}`
 * server-side, joins the `murmur_accept_log` ledger to resolve the
 * resulting company's slug + id once the webhook has landed, and returns
 * a tiny status subset:
 *
 *   200 { ok: true,  data: { status, webhook_status, slug?, company_id? } }
 *   401 { ok: false, errors: ["unauthorized"] }
 *   503 { ok: false, errors: ["disabled"] | ["upstream:config_missing"] }
 *   502 { ok: false, errors: ["upstream:http_4xx"|"upstream:http_5xx"|"upstream:network"|"upstream:bad_response"] }
 *   504 { ok: false, errors: ["upstream:timeout"] }
 *   500 { ok: false, errors: ["internal"] }
 *
 * On a fresh transition to `webhook_status === "delivered"` (which we
 * approximate via "delivered AND we have an accept-log row" — the row only
 * exists once the webhook handler successfully wrote the catalog), the
 * route triggers `revalidatePath('/[lang]/(app)/explore')` and
 * `revalidateTag('companies')` so the user's browse views pick up the new
 * company without a hard reload. Both calls are wrapped in try/catch — a
 * revalidation failure must not 500 the proxy (we still want to return the
 * status to the client).
 *
 * `agent_actions` from Murmur's response is NEVER returned. The audit log
 * can contain subagent inputs/outputs that we don't ship to browsers.
 *
 * Auth: signed-in better-auth session via `getSessionUserId()`. No session
 * -> 401. Same gate as `POST /api/web/companies/request`.
 *
 * Feature flag: `MURMUR_RUN_TRIGGER_ENABLED === "true"`. When the flag is
 * unset / not "true" we 503 — same belt-and-suspenders pattern as the
 * trigger route. The hook then stops polling.
 *
 * NEVER LOGS THE TOKEN. The token never leaves `fetchMurmurRunStatus`.
 *
 * @see colophon-group/jobseek#2810
 * @see Murmur DESIGN.md §3.4 (Publisher API)
 */
import { NextResponse } from "next/server";
import type { NextRequest } from "next/server";

export const runtime = "nodejs";

/**
 * Successful proxy response payload (under `data`).
 *
 *   - `status`         — Murmur run-level status.
 *   - `webhook_status` — webhook-delivery status; `"delivered"` is terminal.
 *   - `slug`           — present iff there's an accept-log row for this run
 *                        AND the joined `company.slug` is non-null.
 *   - `company_id`     — present iff there's an accept-log row for this run
 *                        AND the row's `company_id` is non-null.
 */
export interface RunStatusProxyData {
  readonly status: string;
  readonly webhook_status: string;
  readonly slug?: string;
  readonly company_id?: string;
}

/**
 * Wire format the route returns. Mirrors the existing envelope used by
 * `POST /api/web/companies/request`.
 */
export type RunStatusProxyResponse =
  | { ok: true; data: RunStatusProxyData }
  | { ok: false; errors: string[] };

/**
 * Look up the catalog `company.slug` + `company_id` for a given Murmur
 * run id, if the webhook handler has already landed. Returns `null` when
 * no accept-log row exists yet (i.e. webhook hasn't been processed).
 *
 * The accept-log row is the only piece of jobseek state that's
 * authoritatively keyed on `run_id`, so the proxy reads it directly
 * instead of trying to parse Murmur's `agent_actions`.
 */
export async function lookupAcceptedCompany(
  runId: string,
): Promise<{ slug: string | null; companyId: string | null } | null> {
  throw new Error("not implemented");
}

/**
 * Fire `revalidatePath` + `revalidateTag` so users on the explore view see
 * the new company without a hard reload. Failure is intentionally
 * swallowed — a revalidation hiccup must not 500 the proxy.
 */
export async function tryRevalidateCompanies(): Promise<void> {
  throw new Error("not implemented");
}

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ run_id: string }> },
): Promise<NextResponse> {
  throw new Error("not implemented");
}
