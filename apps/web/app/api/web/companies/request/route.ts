/**
 * POST /api/web/companies/request
 *
 * Public (signed-in) endpoint that lets a jobseek user request a company be
 * added to the catalog and, when the Murmur run-trigger feature flag is on,
 * kicks off a `jobseek-add-company` Murmur run on their behalf. The response
 * carries a `run_id` plus a copy-pasteable `agent_prompt` the UI can show.
 *
 * INTERFACES ONLY — implementation lives in the next commit.
 *
 * Behaviour:
 *   - Auth: signed-in better-auth session via `getSessionUserId()`. No session
 *     -> 401. Same gate the watchlist server actions use.
 *   - Body: `{ company_name: string, website: string }`. company_name must be
 *     a non-empty trimmed string. website must parse with `new URL()` and use
 *     the http/https protocol. Validation failures -> 400 with field-path
 *     error codes; the original input values are NEVER echoed back in errors.
 *   - Feature flag: `MURMUR_RUN_TRIGGER_ENABLED === "true"`. When the flag is
 *     unset / not "true", the route returns 503 and never calls Murmur.
 *   - Rate limit: per-user, 5 requests per 60 minutes. In-process map for the
 *     demo; cross-instance upgrade tracked in colophon-group/jobseek#2803.
 *
 * Response envelope:
 *   200: { ok: true,  data: { run_id, agent_prompt } }
 *   4xx/5xx: { ok: false, errors: string[] }   -- error codes only, never values
 *
 * NEVER LOGS THE TOKEN. The `MURMUR_TOKEN` only ever leaves `start-run.ts`
 * via the outgoing `Authorization` header. Error envelopes from this route
 * reference variable NAMES, never values.
 *
 * @see colophon-group/jobseek#2801
 * @see colophon-group/jobseek#2803  (KV-backed rate limit, captcha, post-demo)
 * @see Murmur DESIGN.md §3.4 (Publisher API), §4.2 (Run trigger)
 */
import type { NextResponse } from "next/server";

export const runtime = "nodejs";

/**
 * Validation result for the request body. On success carries the cleaned
 * `{company_name, website}`; on failure a list of field-path error codes
 * shaped `validation:<field>:<reason>` (e.g. `validation:website:url`).
 * Error codes never embed the original input values.
 */
export type ParsedBody =
  | { ok: true; value: { company_name: string; website: string } }
  | { ok: false; errors: string[] };

/**
 * Validate the parsed JSON body. See {@link ParsedBody} for the shape.
 *
 * @throws never — all errors expressed in the return shape.
 */
export function parseBody(_parsed: unknown): ParsedBody {
  throw new Error("not implemented");
}

/**
 * Try to consume one rate-limit credit for `userId`. Returns `{ ok: true }`
 * when below the cap (and increments the counter), or `{ ok: false, resetAt }`
 * when the cap is hit. Window: 60 min, max 5 per window.
 */
export function consumeRateLimit(
  _userId: string,
  _now?: number,
): { ok: true } | { ok: false; resetAt: number } {
  throw new Error("not implemented");
}

/**
 * Build the agent prompt the UI shows next to `run_id`. Pre-formatted so
 * downstream consumers don't have to reconstruct the wording.
 */
export function buildAgentPrompt(_input: {
  company_name: string;
  website: string;
  run_id: string;
}): string {
  throw new Error("not implemented");
}

/**
 * Test-only hook so vitest cases can isolate per-user rate-limit state
 * without round-tripping through `vi.resetModules()`.
 */
export function __resetRateLimitForTests(): void {
  throw new Error("not implemented");
}

/**
 * Next.js POST route handler. Returns one of:
 *   200 { ok: true, data: { run_id, agent_prompt } }
 *   400 { ok: false, errors: ["validation:..."] }
 *   401 { ok: false, errors: ["unauthorized"] }
 *   429 { ok: false, errors: ["rate_limited"] }
 *   502 { ok: false, errors: ["upstream:<code>"] }
 *   503 { ok: false, errors: ["disabled"] }
 *   504 { ok: false, errors: ["upstream:timeout"] }
 */
export async function POST(_request: Request): Promise<NextResponse> {
  throw new Error("not implemented");
}
