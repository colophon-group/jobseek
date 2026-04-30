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

const ENDPOINT = "/api/web/companies/request";

interface SuccessEnvelope {
  ok: true;
  data?: { run_id?: unknown; agent_prompt?: unknown };
}

interface ErrorEnvelope {
  ok: false;
  errors?: unknown;
}

type Envelope = SuccessEnvelope | ErrorEnvelope;

function isErrorEnvelope(body: unknown): body is ErrorEnvelope {
  return (
    typeof body === "object" &&
    body !== null &&
    "ok" in body &&
    (body as { ok: unknown }).ok === false
  );
}

function extractErrorCodes(body: unknown): string[] {
  if (!isErrorEnvelope(body)) return [];
  const errs = body.errors;
  if (!Array.isArray(errs)) return [];
  return errs.filter((e): e is string => typeof e === "string");
}

/**
 * Fire the request. The endpoint is same-origin; no CSRF token is needed
 * because better-auth cookies are SameSite=Lax and the route also does its
 * own session check.
 */
export async function requestAgentRun(
  input: AgentRunRequestInput,
): Promise<AgentRunRequestResult> {
  let response: Response;
  try {
    response = await fetch(ENDPOINT, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        company_name: input.companyName,
        website: input.website,
      }),
      // Important: same-origin so cookies are sent with the request. This is
      // the default for same-origin URLs but we make it explicit.
      credentials: "same-origin",
    });
  } catch {
    return { kind: "error" };
  }

  let body: Envelope | undefined;
  try {
    body = (await response.json()) as Envelope;
  } catch {
    return { kind: "error" };
  }

  if (response.status === 200 && body && body.ok === true) {
    const runId = body.data?.run_id;
    const agentPrompt = body.data?.agent_prompt;
    if (typeof runId === "string" && typeof agentPrompt === "string" && runId.length > 0) {
      return { kind: "ok", runId, agentPrompt };
    }
    return { kind: "error" };
  }

  if (response.status === 401) return { kind: "unauthorized" };
  if (response.status === 429) return { kind: "rate_limited" };
  if (response.status === 503) {
    // The endpoint also returns 503 for `upstream:config_missing`. Treat both
    // as "disabled" -> fall back to the GH-issue UI; this is what the demo
    // wants when Murmur is not reachable for any reason.
    return { kind: "disabled" };
  }
  if (response.status === 400) {
    return { kind: "validation", codes: extractErrorCodes(body) };
  }

  return { kind: "error" };
}
