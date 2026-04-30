/**
 * POST /api/web/companies/request
 *
 * Public (signed-in) endpoint that lets a jobseek user request a company be
 * added to the catalog and, when the Murmur run-trigger feature flag is on,
 * kicks off a `jobseek-add-company` Murmur run on their behalf. The response
 * carries a `run_id` plus a copy-pasteable `agent_prompt` the UI can show.
 *
 * Behaviour:
 *   - Auth: signed-in better-auth session via `getSessionUserId()`. No session
 *     -> 401. Same gate the watchlist server actions use.
 *   - Body: `{ company_name: string, website: string }`. company_name must be
 *     a non-empty trimmed string. website must parse with `new URL()` and use
 *     the http/https protocol. Validation failures -> 400 with field-path
 *     error codes; the original input values are NEVER echoed back in errors.
 *   - Feature flag: `MURMUR_RUN_TRIGGER_ENABLED === "true"`. When the flag is
 *     unset / not "true", the route returns 503 and never calls Murmur. This
 *     is the same belt-and-suspenders pattern as the admin demo route — the
 *     existing GH-issue requestCompany path stays the default until ops flip
 *     the flag in Vercel.
 *   - Rate limit: per-user, 5 requests per 60 minutes. Implemented as a
 *     module-level `Map<userId, {count, resetAt}>` (in-process). This is
 *     deliberately scoped to the demo: we don't yet need cross-instance
 *     enforcement and the existing `companyRequestLimiter` in
 *     `@/lib/rate-limit` is keyed by IP, not user id. An upgrade to a
 *     Redis/KV-backed limiter keyed by user id is tracked in
 *     colophon-group/jobseek#2803.
 *   - StartRunError mapping (mirrors the admin route at
 *     `app/api/admin/murmur-demo/run`):
 *         config_missing -> 503
 *         http_4xx       -> 502
 *         http_5xx       -> 502
 *         timeout        -> 504
 *         network        -> 502
 *         bad_response   -> 502
 *
 * Response envelope:
 *   200: { ok: true,  data: { run_id, agent_prompt: { install_command, prompt_text } } }
 *   4xx/5xx: { ok: false, errors: string[] }   -- error codes only, never values
 *
 * The `agent_prompt` is structured rather than a single string so the UI can
 * render the MCP install command and the user-facing prompt as two separately
 * copyable blocks (jobseek#2809). The `install_command` carries the LITERAL
 * placeholder `<token-from-jobseek-team>` instead of a real bearer token —
 * production tokens are handed out at demo time, and per-user issuance lives
 * in murmur#77.
 *
 * NEVER LOGS THE TOKEN. The `MURMUR_TOKEN` only ever leaves `start-run.ts`
 * via the outgoing `Authorization` header. Error envelopes from this route
 * reference variable NAMES, never values — same convention as the admin
 * route and `apps/crawler/murmur/scripts/register.ts`.
 *
 * @see colophon-group/jobseek#2801
 * @see colophon-group/jobseek#2803  (KV-backed rate limit, captcha, post-demo)
 * @see colophon-group/jobseek#2809  (MCP install instructions in agent_prompt)
 * @see Murmur DESIGN.md §3.4 (Publisher API), §4.2 (Run trigger)
 */
import { NextResponse } from "next/server";
import { StartRunError, startRun } from "@/lib/murmur/start-run";
import { getSessionUserId } from "@/lib/sessionCache";

export const runtime = "nodejs";

/** Rate-limit window length in milliseconds (60 min, per the issue). */
const RATE_LIMIT_WINDOW_MS = 60 * 60 * 1000;
/** Max requests per user per window. */
const RATE_LIMIT_MAX = 5;

interface RateLimitEntry {
  count: number;
  resetAt: number;
}

/**
 * Per-user counter map. Module-scoped so it persists across requests on the
 * same serverless instance, but is reset on cold start / scale-out — that's
 * acceptable for the demo (see jobseek#2803 for the cross-instance upgrade).
 */
const rateLimitState: Map<string, RateLimitEntry> = new Map();

interface RequestBody {
  company_name?: unknown;
  website?: unknown;
}

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
export function parseBody(parsed: unknown): ParsedBody {
  if (parsed === null || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { ok: false, errors: ["validation:body:shape"] };
  }
  const errors: string[] = [];
  const { company_name, website } = parsed as RequestBody;

  let cleanName: string | null = null;
  if (typeof company_name !== "string") {
    errors.push("validation:company_name:type");
  } else {
    const trimmed = company_name.trim();
    if (trimmed.length === 0) {
      errors.push("validation:company_name:empty");
    } else {
      cleanName = trimmed;
    }
  }

  let cleanWebsite: string | null = null;
  if (typeof website !== "string") {
    errors.push("validation:website:type");
  } else {
    const trimmed = website.trim();
    if (trimmed.length === 0) {
      errors.push("validation:website:empty");
    } else {
      try {
        const url = new URL(trimmed);
        if (url.protocol !== "http:" && url.protocol !== "https:") {
          errors.push("validation:website:protocol");
        } else {
          cleanWebsite = trimmed;
        }
      } catch {
        errors.push("validation:website:url");
      }
    }
  }

  if (errors.length > 0 || cleanName === null || cleanWebsite === null) {
    return {
      ok: false,
      errors: errors.length > 0 ? errors : ["validation:body:shape"],
    };
  }
  return {
    ok: true,
    value: { company_name: cleanName, website: cleanWebsite },
  };
}

/**
 * Try to consume one rate-limit credit for `userId`. Returns `{ ok: true }`
 * when below the cap (and increments the counter), or `{ ok: false, resetAt }`
 * when the cap is hit. Window: 60 min, max 5 per window.
 *
 * Sweeps stale entries opportunistically so the map cannot grow unbounded
 * on a long-lived serverless instance — the sweep cost is O(active users)
 * which is fine: the map only ever holds users who hit this route in the
 * last hour, capped by the per-user budget.
 */
export function consumeRateLimit(
  userId: string,
  now: number = Date.now(),
): { ok: true } | { ok: false; resetAt: number } {
  for (const [k, v] of rateLimitState) {
    if (v.resetAt <= now) rateLimitState.delete(k);
  }

  const entry = rateLimitState.get(userId);
  if (!entry || entry.resetAt <= now) {
    rateLimitState.set(userId, {
      count: 1,
      resetAt: now + RATE_LIMIT_WINDOW_MS,
    });
    return { ok: true };
  }
  if (entry.count >= RATE_LIMIT_MAX) {
    return { ok: false, resetAt: entry.resetAt };
  }
  entry.count += 1;
  return { ok: true };
}

/**
 * Structured agent prompt the UI renders as two copyable blocks.
 *
 *   - `install_command` is the one-liner the user runs in their terminal to
 *     register the Murmur MCP server with their Claude Code. The bearer token
 *     in this string is the LITERAL placeholder `<token-from-jobseek-team>` —
 *     production tokens are handed out at demo time and per-user issuance is
 *     murmur#77 (post-demo).
 *   - `prompt_text` is the natural-language prompt the user pastes into Claude
 *     Code AFTER installing the MCP server. It mentions the company, website,
 *     and run id, and instructs the agent to drain `pull_task` until empty.
 *
 * Pre-formatted server-side so downstream consumers (the API client + the
 * `AgentPromptCard`) don't have to reconstruct the wording.
 *
 * @see colophon-group/jobseek#2809
 */
export interface AgentPrompt {
  /** `claude mcp add ...` one-liner with `<token-from-jobseek-team>` placeholder. */
  install_command: string;
  /** Natural-language prompt mentioning company, website, run id, and `pull_task`. */
  prompt_text: string;
}

/**
 * Build the structured agent prompt the UI shows next to `run_id`. See
 * {@link AgentPrompt} for the field contract. Mirrors the exact phrasing
 * called out in jobseek#2801 §Scope.5 + jobseek#2809 §Scope.1.
 */
export function buildAgentPrompt(_input: {
  company_name: string;
  website: string;
  run_id: string;
}): AgentPrompt {
  throw new Error("not implemented");
}

/**
 * Test-only hook so vitest cases can isolate per-user rate-limit state
 * without round-tripping through `vi.resetModules()`.
 */
export function __resetRateLimitForTests(): void {
  rateLimitState.clear();
}

function isFeatureEnabled(): boolean {
  return process.env.MURMUR_RUN_TRIGGER_ENABLED === "true";
}

function statusForStartRunCode(code: StartRunError["code"]): number {
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

export async function POST(request: Request): Promise<NextResponse> {
  // 1. Auth gate first. No session => 401, regardless of feature-flag state.
  let userId: string | null;
  try {
    userId = await getSessionUserId();
  } catch {
    // getSessionUserId() already swallows redis/db errors and returns null,
    // but be defensive: any unexpected throw here means we cannot identify
    // the user, so treat as unauthenticated rather than 500-ing.
    userId = null;
  }
  if (!userId) {
    return errorResponse(401, ["unauthorized"]);
  }

  // 2. Feature flag — fail closed when not explicitly enabled.
  if (!isFeatureEnabled()) {
    return errorResponse(503, ["disabled"]);
  }

  // 3. Parse + validate body (BEFORE consuming rate-limit credits — a
  //    malformed request shouldn't burn the user's budget).
  let raw: unknown;
  try {
    raw = await request.json();
  } catch {
    return errorResponse(400, ["validation:body:json"]);
  }
  const parsed = parseBody(raw);
  if (!parsed.ok) {
    return errorResponse(400, parsed.errors);
  }

  // 4. Per-user rate limit.
  const rl = consumeRateLimit(userId);
  if (!rl.ok) {
    return errorResponse(429, ["rate_limited"]);
  }

  // 5. Trigger the run. Every failure path -> typed StartRunError.
  try {
    const { run_id } = await startRun(parsed.value);
    const agent_prompt = buildAgentPrompt({
      company_name: parsed.value.company_name,
      website: parsed.value.website,
      run_id,
    });
    return NextResponse.json(
      { ok: true, data: { run_id, agent_prompt } },
      { status: 200 },
    );
  } catch (err) {
    if (err instanceof StartRunError) {
      // Log only the code; the message can include URL fragments and we
      // keep the audit trail terse. NEVER LOGS THE TOKEN.
      console.error("[web/companies/request] startRun failed", {
        code: err.code,
        status: err.status,
      });
      return errorResponse(statusForStartRunCode(err.code), [
        `upstream:${err.code}`,
      ]);
    }
    console.error("[web/companies/request] unexpected error", err);
    return errorResponse(500, ["internal"]);
  }
}
