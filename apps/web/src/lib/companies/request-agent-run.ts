/**
 * Client helper for `POST /api/web/companies/request`.
 *
 * Returns a discriminated union so callers can route each response state to
 * the right UI branch without re-parsing the envelope shape:
 *
 *   - `ok`            -> 200 { ok: true,  data: { run_id, agent_prompt } }
 *   - `disabled`      -> 503 errors:["disabled"]   (feature flag off)
 *   - `rate_limited`  -> 429 errors:["rate_limited"]
 *   - `validation`    -> 400 errors:["validation:..."]
 *   - `unauthorized`  -> 401 errors:["unauthorized"]
 *   - `error`         -> any other failure (5xx, network, malformed JSON)
 *
 * NEVER throws. All failures are reflected in the return shape so calling
 * components can render UI deterministically.
 *
 * @see colophon-group/jobseek#2801 (endpoint contract)
 * @see colophon-group/jobseek#2802 (UI consumer)
 */
export type AgentRunRequestResult =
  | { kind: "ok"; runId: string; agentPrompt: string }
  | { kind: "disabled" }
  | { kind: "rate_limited" }
  | { kind: "validation"; codes: string[] }
  | { kind: "unauthorized" }
  | { kind: "error" };

export interface AgentRunRequestInput {
  /** Trimmed, non-empty company name. */
  companyName: string;
  /** Trimmed, http(s) URL string. */
  website: string;
}

/**
 * Fire the request. The endpoint is same-origin; no CSRF token is needed
 * because better-auth cookies are SameSite=Lax and the route also does its
 * own session check.
 */
export async function requestAgentRun(
  _input: AgentRunRequestInput,
): Promise<AgentRunRequestResult> {
  throw new Error("not implemented");
}
