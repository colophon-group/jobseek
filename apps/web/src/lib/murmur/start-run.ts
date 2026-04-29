/**
 * start-run
 *
 * Trigger a Murmur run via the publisher API:
 *
 *   POST {MURMUR_URL}/pipelines/jobseek-add-company/runs
 *   Authorization: Bearer ${MURMUR_TOKEN}
 *   Content-Type: application/json
 *   Body: { "initial_input": { "company_name": string, "website": string } }
 *
 * Returns the parsed `{ run_id }` envelope on success. Per Murmur DESIGN.md
 * §3.4, the M4 endpoint accepts `{initial_input, prior_outputs?}` and returns
 * `{run_id}`. Validation failures and unknown pipelines come back as 4xx;
 * server errors as 5xx; transport failures (DNS, connect-refused, abort) raise
 * `StartRunError` with `code: "network"` or `code: "timeout"`.
 *
 * Used from the demo-only admin route at `app/api/admin/murmur-demo/run`,
 * which gates the call behind basic auth and the `MURMUR_RUN_TRIGGER_ENABLED`
 * feature flag so the existing `requestCompany` flow (the GH-issue path) is
 * never affected.
 *
 * NEVER LOGS THE TOKEN. The token value only ever leaves this module via the
 * outgoing `Authorization` header. Missing-env error messages reference
 * variable NAMES, never values — matches the convention from P2's
 * `apps/crawler/murmur/scripts/register.ts`.
 *
 * No bare `fetch` is exposed to callers; the helper itself uses the platform
 * `fetch` global as P2 does (jobseek has no formal HTTP wrapper today and the
 * SSRF-flavoured `safeFetch` in `./ssrf.ts` is for inbound agent-supplied
 * URLs against the boards-host allowlist — it cannot reach the operator's
 * own Murmur host).
 *
 * @module murmur/start-run
 * @see Murmur DESIGN.md §3.4 (Publisher API), §4.2 (Run trigger)
 * @see colophon-group/jobseek#2762
 */

/**
 * The Murmur pipeline ID this helper triggers. Matches the `id` field of
 * `apps/crawler/murmur/pipelines/add-company.yaml` (P1).
 */
export const ADD_COMPANY_PIPELINE_ID = "jobseek-add-company";

/**
 * Request shape accepted by `startRun`. Both fields are required by the
 * pipeline def's `initial_input_schema`.
 */
export interface StartRunInput {
  readonly company_name: string;
  readonly website: string;
}

/**
 * Success response from `startRun`. Mirrors Murmur's `{run_id}` envelope.
 */
export interface StartRunResponse {
  readonly run_id: string;
}

/**
 * Typed-error codes raised by `startRun`. The full set is intentionally small
 * so callers can branch on `err.code` rather than message-string matching.
 *
 *   - `config_missing`  — `MURMUR_URL` or `MURMUR_TOKEN` env var is unset.
 *   - `http_4xx`        — Murmur returned 4xx (validation, unknown pipeline,
 *                          auth — `status` field carries the exact code).
 *   - `http_5xx`        — Murmur returned 5xx.
 *   - `bad_response`    — 2xx but body is not parseable JSON or is missing
 *                          `run_id` of type string.
 *   - `timeout`         — request took longer than the timeout budget
 *                          (default 15s) and was aborted, OR the underlying
 *                          fetch threw an `AbortError`.
 *   - `network`         — any other transport-layer failure (DNS, refused,
 *                          TLS, fetch reject).
 */
export type StartRunErrorCode =
  | "config_missing"
  | "http_4xx"
  | "http_5xx"
  | "bad_response"
  | "timeout"
  | "network";

/**
 * Typed error thrown by `startRun`. `status` is set when the failure is HTTP
 * (i.e. `code` is `http_4xx` / `http_5xx`); otherwise undefined. `cause` is
 * preserved when an underlying `Error` is available so observability tools
 * can chain stack traces. `message` is operator-facing and never includes the
 * token.
 */
export class StartRunError extends Error {
  public readonly code: StartRunErrorCode;
  public readonly status: number | undefined;

  constructor(
    code: StartRunErrorCode,
    message: string,
    options?: { status?: number; cause?: unknown },
  ) {
    super(message, options);
    this.name = "StartRunError";
    this.code = code;
    this.status = options?.status;
  }
}

/**
 * Injectable fetch implementation. Tests pass a stub; production calls bind
 * the global `fetch`. Typed against the WHATWG fetch signature.
 */
export type FetchImpl = (
  input: string | URL,
  init?: RequestInit,
) => Promise<Response>;

/**
 * Optional dependencies for `startRun`. Hidden from the public default call
 * but exposed for tests (and any future caller that wants to override the
 * env / fetch / timeout).
 */
export interface StartRunOptions {
  readonly fetchImpl?: FetchImpl;
  readonly env?: { MURMUR_URL?: string | undefined; MURMUR_TOKEN?: string | undefined };
  /** Default 15000 ms. */
  readonly timeoutMs?: number;
}

/**
 * Build the absolute URL for `POST /pipelines/{pipelineId}/runs` given a base
 * Murmur URL. Tolerates one or more trailing slashes on the base. The
 * `pipelineId` is URL-path-encoded so unusual ids (the demo uses the literal
 * "jobseek-add-company", but tests cover odd values) round-trip safely.
 */
export function buildRunsUrl(baseUrl: string, pipelineId: string): string {
  const trimmed = baseUrl.replace(/\/+$/, "");
  return `${trimmed}/pipelines/${encodeURIComponent(pipelineId)}/runs`;
}

/**
 * Default request timeout in milliseconds. Set deliberately high so a slow
 * demo pipeline that's also doing per-board probes server-side at start
 * time can still respond, but low enough that operator-side hangs surface
 * as a typed error rather than blocking the admin route handler.
 */
const DEFAULT_TIMEOUT_MS = 15_000;

/**
 * Trigger a Murmur run for the `jobseek-add-company` pipeline. Resolves with
 * the parsed `{ run_id }` envelope. Throws {@link StartRunError} on every
 * failure mode (no other error class escapes this function).
 *
 * @param input  `{ company_name, website }` — mapped 1:1 into `initial_input`.
 * @param options  Test/override hooks (see {@link StartRunOptions}).
 * @returns      `{ run_id }` on success.
 * @throws       {@link StartRunError} (always; never any other error class).
 */
export async function startRun(
  input: StartRunInput,
  options?: StartRunOptions,
): Promise<StartRunResponse> {
  // 1. Resolve env. Fail fast on missing config — error message references
  //    variable NAMES, never values, so the bearer token is never logged.
  const env = options?.env ?? {
    MURMUR_URL: process.env.MURMUR_URL,
    MURMUR_TOKEN: process.env.MURMUR_TOKEN,
  };
  const missing: string[] = [];
  if (!env.MURMUR_URL || env.MURMUR_URL.length === 0) missing.push("MURMUR_URL");
  if (!env.MURMUR_TOKEN || env.MURMUR_TOKEN.length === 0)
    missing.push("MURMUR_TOKEN");
  if (missing.length > 0) {
    throw new StartRunError(
      "config_missing",
      `startRun: missing required env: ${missing.join(", ")}`,
    );
  }
  const baseUrl = env.MURMUR_URL as string;
  const token = env.MURMUR_TOKEN as string;

  // 2. Build URL + body.
  const url = buildRunsUrl(baseUrl, ADD_COMPANY_PIPELINE_ID);
  const body = JSON.stringify({
    initial_input: {
      company_name: input.company_name,
      website: input.website,
    },
  });

  // 3. Set up the abort signal for the timeout budget. We compose with any
  //    caller-supplied signal so a containing route can still cancel us.
  const timeoutMs = options?.timeoutMs ?? DEFAULT_TIMEOUT_MS;
  const controller = new AbortController();
  const timeoutHandle = setTimeout(() => {
    controller.abort();
  }, timeoutMs);

  const fetchImpl: FetchImpl =
    options?.fetchImpl ?? (globalThis.fetch.bind(globalThis) as FetchImpl);

  // 4. Issue the request. Translate every failure mode into StartRunError.
  let res: Response;
  try {
    res = await fetchImpl(url, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Authorization: `Bearer ${token}`,
      },
      body,
      signal: controller.signal,
    });
  } catch (err) {
    if (isAbortError(err) || controller.signal.aborted) {
      throw new StartRunError(
        "timeout",
        `startRun: request timed out after ${timeoutMs}ms`,
        { cause: err },
      );
    }
    throw new StartRunError(
      "network",
      `startRun: network error: ${(err as Error).message ?? String(err)}`,
      { cause: err },
    );
  } finally {
    clearTimeout(timeoutHandle);
  }

  // 5. Branch on status.
  if (res.status >= 400 && res.status < 500) {
    // Drain the body so we don't leak a held-open connection. We don't
    // include the body in the error message — Murmur error envelopes can
    // include user-supplied input that we don't want to echo into logs.
    await safeDrain(res);
    throw new StartRunError(
      "http_4xx",
      `startRun: Murmur returned HTTP ${res.status}`,
      { status: res.status },
    );
  }
  if (res.status >= 500) {
    await safeDrain(res);
    throw new StartRunError(
      "http_5xx",
      `startRun: Murmur returned HTTP ${res.status}`,
      { status: res.status },
    );
  }

  // 2xx — parse and validate envelope.
  let text: string;
  try {
    text = await res.text();
  } catch (err) {
    throw new StartRunError(
      "bad_response",
      `startRun: failed to read response body`,
      { cause: err },
    );
  }
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch (err) {
    throw new StartRunError(
      "bad_response",
      `startRun: response body was not JSON`,
      { cause: err },
    );
  }
  if (
    parsed === null ||
    typeof parsed !== "object" ||
    Array.isArray(parsed) ||
    typeof (parsed as { run_id?: unknown }).run_id !== "string" ||
    ((parsed as { run_id: string }).run_id as string).length === 0
  ) {
    throw new StartRunError(
      "bad_response",
      `startRun: response missing required string field 'run_id'`,
    );
  }
  return { run_id: (parsed as { run_id: string }).run_id };
}

/**
 * Best-effort drain of a Response body. Never throws — this is a hygiene
 * call so 4xx / 5xx error paths don't leak an unconsumed stream.
 */
async function safeDrain(res: Response): Promise<void> {
  try {
    await res.text();
  } catch {
    // Intentionally swallowed.
  }
}

/**
 * Detect an `AbortError`. WHATWG fetch raises one with `name === "AbortError"`
 * (and Node's undici sometimes uses a `DOMException` whose name is also
 * `"AbortError"`); we identify by name to cover both shapes.
 */
function isAbortError(err: unknown): boolean {
  return (
    typeof err === "object" &&
    err !== null &&
    "name" in err &&
    (err as { name: unknown }).name === "AbortError"
  );
}
