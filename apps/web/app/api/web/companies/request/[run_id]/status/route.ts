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
 * On a fresh transition to `webhook_status === "delivered"` AND we have an
 * accept-log row (the row only exists once the webhook handler successfully
 * wrote the catalog), the route triggers `revalidatePath('/[lang]/(app)/explore')`
 * and `revalidateTag('companies')` so the user's browse views pick up the new
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
import { revalidatePath, revalidateTag } from "next/cache";
import {
  fetchMurmurRunStatus,
  RunStatusError,
  type RunStatusErrorCode,
} from "@/lib/murmur/run-status";
import { getSessionUserId } from "@/lib/sessionCache";
import { lookupAcceptedCompany } from "./helpers";

export const runtime = "nodejs";

/**
 * Successful proxy response payload (under `data`).
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

function isFeatureEnabled(): boolean {
  return process.env.MURMUR_RUN_TRIGGER_ENABLED === "true";
}

function statusForRunStatusCode(code: RunStatusErrorCode): number {
  switch (code) {
    case "config_missing":
      return 503;
    case "timeout":
      return 504;
    case "http_4xx":
    case "http_5xx":
    case "network":
    case "bad_response":
      return 502;
    default:
      return 502;
  }
}

function errorResponse(status: number, errors: string[]): NextResponse {
  return NextResponse.json({ ok: false, errors }, { status });
}

/**
 * Fire `revalidatePath` + `revalidateTag` so users on the explore view see
 * the new company without a hard reload. Failure is intentionally
 * swallowed — a revalidation hiccup must not 500 the proxy.
 */
export async function tryRevalidateCompanies(): Promise<void> {
  try {
    revalidatePath("/[lang]/(app)/explore");
  } catch (err) {
    console.warn("[web/companies/request/status] revalidatePath failed", err);
  }
  try {
    // Next 16 requires a second `profile` arg; "max" matches the framework
    // recommendation for "no expire" tag invalidations from route handlers.
    revalidateTag("companies", "max");
  } catch (err) {
    console.warn("[web/companies/request/status] revalidateTag failed", err);
  }
}

export async function GET(
  _request: NextRequest,
  context: { params: Promise<{ run_id: string }> },
): Promise<NextResponse> {
  // 1. Auth gate first.
  let userId: string | null;
  try {
    userId = await getSessionUserId();
  } catch {
    userId = null;
  }
  if (!userId) {
    return errorResponse(401, ["unauthorized"]);
  }

  // 2. Feature flag — fail closed when not explicitly enabled.
  if (!isFeatureEnabled()) {
    return errorResponse(503, ["disabled"]);
  }

  // 3. Resolve the dynamic param.
  const { run_id: runId } = await context.params;
  if (typeof runId !== "string" || runId.length === 0) {
    return errorResponse(400, ["validation:run_id:empty"]);
  }

  // 4. Forward to Murmur and look up the accept-log row in parallel.
  let upstream: Awaited<ReturnType<typeof fetchMurmurRunStatus>>;
  try {
    upstream = await fetchMurmurRunStatus(runId);
  } catch (err) {
    if (err instanceof RunStatusError) {
      console.error("[web/companies/request/status] upstream failed", {
        code: err.code,
        status: err.status,
      });
      return errorResponse(statusForRunStatusCode(err.code), [
        `upstream:${err.code}`,
      ]);
    }
    console.error("[web/companies/request/status] unexpected error", err);
    return errorResponse(500, ["internal"]);
  }

  let accept: { slug: string | null; companyId: string | null } | null = null;
  try {
    accept = await lookupAcceptedCompany(runId);
  } catch (err) {
    // DB hiccup shouldn't 500 the proxy — the user can still see the run
    // status; the success link will just be missing this tick.
    console.warn(
      "[web/companies/request/status] lookupAcceptedCompany failed",
      err,
    );
  }

  // 5. If the webhook delivered AND we have a catalog row, fire revalidation.
  //    The accept-log row gating is what makes this safe to call repeatedly:
  //    Next dedupes within a single request, and a row only exists post-
  //    webhook-write, so the first poll after webhook delivery is the
  //    earliest possible firing point.
  if (upstream.webhook_status === "delivered" && accept !== null) {
    await tryRevalidateCompanies();
  }

  const data: RunStatusProxyData = {
    status: upstream.status,
    webhook_status: upstream.webhook_status,
    ...(accept?.slug ? { slug: accept.slug } : {}),
    ...(accept?.companyId ? { company_id: accept.companyId } : {}),
  };

  return NextResponse.json({ ok: true, data }, { status: 200 });
}
