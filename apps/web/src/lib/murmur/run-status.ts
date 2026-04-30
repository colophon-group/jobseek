/**
 * fetch-murmur-run-status
 *
 * Read-only companion to {@link ./start-run.ts}: forwards `GET {MURMUR_URL}/runs/{run_id}`
 * with the server's `MURMUR_TOKEN` and returns the small status subset the
 * web UI needs:
 *
 *   { status: string, webhook_status: string }
 *
 * Per the orchestrator's note, Murmur's response is
 *   `{ ok: true, data: { run_id, pipeline_id, status, webhook_status, agent_actions[] } }`.
 * We deliberately DROP `agent_actions` here — the audit log can contain
 * subagent inputs/outputs we have no business shipping back to the browser.
 *
 * Failure modes mirror `startRun` so the proxy route can map them to
 * status codes the same way.
 *
 * NEVER LOGS THE TOKEN. The token only ever leaves this module via the
 * outgoing `Authorization` header. Error messages reference variable NAMES,
 * never values — same convention as `startRun`.
 *
 * @module murmur/run-status
 * @see Murmur DESIGN.md §3.4 (Publisher API)
 * @see colophon-group/jobseek#2810
 */

/**
 * Minimal subset of Murmur's run-status response used by the proxy route.
 * Both fields are required; if either is missing we surface `bad_response`.
 */
export interface MurmurRunStatus {
  /** Run-level status, e.g. `running`, `completed`, `failed`. */
  readonly status: string;
  /** Webhook-delivery status, e.g. `pending`, `delivered`, `failed`. */
  readonly webhook_status: string;
}

/**
 * Typed-error codes raised by `fetchMurmurRunStatus`. Mirrors
 * {@link import("./start-run").StartRunErrorCode} 1:1 so the proxy route can
 * reuse the same status mapping.
 */
export type RunStatusErrorCode =
  | "config_missing"
  | "http_4xx"
  | "http_5xx"
  | "bad_response"
  | "timeout"
  | "network";

/**
 * Typed error thrown by `fetchMurmurRunStatus`. `status` is set when the
 * failure is HTTP. `cause` is preserved when an underlying `Error` is
 * available. Operator-facing message; never includes the token.
 */
export class RunStatusError extends Error {
  public readonly code: RunStatusErrorCode;
  public readonly status: number | undefined;

  constructor(
    code: RunStatusErrorCode,
    message: string,
    options?: { status?: number; cause?: unknown },
  ) {
    super(message, options);
    this.name = "RunStatusError";
    this.code = code;
    this.status = options?.status;
  }
}

/**
 * Injectable fetch implementation for tests.
 */
export type FetchImpl = (
  input: string | URL,
  init?: RequestInit,
) => Promise<Response>;

/**
 * Optional dependencies. Hidden from the public default call.
 */
export interface FetchRunStatusOptions {
  readonly fetchImpl?: FetchImpl;
  readonly env?: {
    MURMUR_URL?: string | undefined;
    MURMUR_TOKEN?: string | undefined;
  };
  /** Default 10000 ms — tighter than start-run because this is on a poll path. */
  readonly timeoutMs?: number;
}

/**
 * Build the absolute URL for `GET /runs/{run_id}` given a base Murmur URL.
 * Tolerates one or more trailing slashes on the base. The `runId` is
 * URL-path-encoded so unusual ids can't break the URL.
 */
export function buildRunStatusUrl(baseUrl: string, runId: string): string {
  const trimmed = baseUrl.replace(/\/+$/, "");
  return `${trimmed}/runs/${encodeURIComponent(runId)}`;
}

/** Default request timeout in milliseconds. Tighter than start-run because
 *  this is on a 5s-poll path; a hung publisher should surface fast.
 */
const DEFAULT_TIMEOUT_MS = 10_000;

/**
 * Fetch the status of a Murmur run. Resolves with the small `{status,
 * webhook_status}` subset on success; throws {@link RunStatusError} on every
 * failure mode (no other error class escapes this function).
 */
export async function fetchMurmurRunStatus(
  runId: string,
  options?: FetchRunStatusOptions,
): Promise<MurmurRunStatus> {
  // 1. Resolve env. Fail fast on missing config — error message references
  //    variable NAMES, never values.
  const env = options?.env ?? {
    MURMUR_URL: process.env.MURMUR_URL,
    MURMUR_TOKEN: process.env.MURMUR_TOKEN,
  };
  const missing: string[] = [];
  if (!env.MURMUR_URL || env.MURMUR_URL.length === 0) missing.push("MURMUR_URL");
  if (!env.MURMUR_TOKEN || env.MURMUR_TOKEN.length === 0)
    missing.push("MURMUR_TOKEN");
  if (missing.length > 0) {
    throw new RunStatusError(
      "config_missing",
      `fetchMurmurRunStatus: missing required env: ${missing.join(", ")}`,
    );
  }
  const baseUrl = env.MURMUR_URL as string;
  const token = env.MURMUR_TOKEN as string;

  const url = buildRunStatusUrl(baseUrl, runId);

  // 2. Set up the abort signal for the timeout budget.
  const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => controller.abort(), timeoutMs);

  const fetchImpl: FetchImpl =
    options?.fetchImpl ?? (globalThis.fetch.bind(globalThis) as FetchImpl);

  // 3. Issue the request.
  let res: Response;
  try {
    res = await fetchImpl(url, {
      method: "GET",
      headers: {
        Accept: "application/json",
        Authorization: `Bearer ${token}`,
      },
      signal: controller.signal,
    });
  } catch (err) {
    if (isAbortError(err) || controller.signal.aborted) {
      throw new RunStatusError(
        "timeout",
        `fetchMurmurRunStatus: request timed out after ${timeoutMs}ms`,
        { cause: err },
      );
    }
    throw new RunStatusError(
      "network",
      `fetchMurmurRunStatus: network error: ${(err as Error).message ?? String(err)}`,
      { cause: err },
    );
  } finally {
    clearTimeout(timeoutHandle);
  }

  // 4. Branch on status.
  if (res.status >= 400 && res.status < 500) {
    await safeDrain(res);
    throw new RunStatusError(
      "http_4xx",
      `fetchMurmurRunStatus: Murmur returned HTTP ${res.status}`,
      { status: res.status },
    );
  }
  if (res.status >= 500) {
    await safeDrain(res);
    throw new RunStatusError(
      "http_5xx",
      `fetchMurmurRunStatus: Murmur returned HTTP ${res.status}`,
      { status: res.status },
    );
  }

  // 2xx — parse and validate envelope.
  let text: string;
  try {
    text = await res.text();
  } catch (err) {
    throw new RunStatusError(
      "bad_response",
      `fetchMurmurRunStatus: failed to read response body`,
      { cause: err },
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new RunStatusError(
      "bad_response",
      `fetchMurmurRunStatus: response body was not JSON`,
      { cause: err },
    );
  }

  // The publisher returns `{ok:true, data:{...}}`. Some test fixtures and
  // future consumers may pass the bare data object; accept both shapes by
  // unwrapping `data` when present.
  const root =
    typeof parsed === "object" &&
    parsed !== null &&
    !Array.isArray(parsed) &&
    "data" in parsed
      ? (parsed as { data: unknown }).data
      : parsed;

  if (
    root === null ||
    typeof root !== "object" ||
    Array.isArray(root) ||
    typeof (root as { status?: unknown }).status !== "string" ||
    typeof (root as { webhook_status?: unknown }).webhook_status !== "string"
  ) {
    throw new RunStatusError(
      "bad_response",
      `fetchMurmurRunStatus: response missing required fields {status, webhook_status}`,
    );
  }

  const r = root as { status: string; webhook_status: string };
  return { status: r.status, webhook_status: r.webhook_status };
}

/**
 * Best-effort drain of a Response body. Never throws.
 */
async function safeDrain(res: Response): Promise<void> {
  try {
    await res.text();
  } catch {
    // Intentionally swallowed.
  }
}

/**
 * Detect an `AbortError`. Identifies by name to cover both WHATWG and
 * undici/DOMException shapes.
 */
function isAbortError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "name" in err &&
    (err as { name: unknown }).name === "AbortError"
  );
}
