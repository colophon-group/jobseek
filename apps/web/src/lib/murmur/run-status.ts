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
  throw new Error("not implemented");
}

/**
 * Fetch the status of a Murmur run. Resolves with the small `{status,
 * webhook_status}` subset on success; throws {@link RunStatusError} on every
 * failure mode (no other error class escapes this function).
 *
 * @param runId    The opaque Murmur run id.
 * @param options  Test/override hooks (see {@link FetchRunStatusOptions}).
 * @returns        `{status, webhook_status}` on 2xx with the expected envelope.
 * @throws         {@link RunStatusError} (always; never any other error class).
 */
export async function fetchMurmurRunStatus(
  runId: string,
  options?: FetchRunStatusOptions,
): Promise<MurmurRunStatus> {
  throw new Error("not implemented");
}
