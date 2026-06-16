/**
 * Typesense connection-class retry helper.
 *
 * Background — issue #3008: cold-start `/en/explore` visits showed
 * `Fetch failed loading: POST "https://jseek.co/en/explore"` for some
 * server actions. A Playwright reproduction against production caught
 * 3/5 trials with `net::ERR_ABORTED` on `POST /en/explore` server-action
 * calls. The aborts correlate with cold serverless instances opening
 * the first connection to `typesense.colophon-group.org` over the
 * Cloudflare tunnel — a single TLS handshake + first query on a cold
 * Lambda + cold Typesense connection can exceed the 5s
 * `connectionTimeoutSeconds` and surface as a transient connection
 * error. Once the function instance is warm and the keep-alive socket
 * is open, the same call returns in 100-200ms.
 *
 * Without retries, the provider's outermost `try/catch` swallows the
 * error and returns `emptyResponse()` (`{ companies: [], degraded: true }`).
 * Combined with the page-level `'use cache'` (`cacheLife: 60s`), a
 * single cold-start blip would poison the prerender for the whole
 * region for 60 seconds.
 *
 * Mirror of `apps/web/src/lib/db-retry.ts` — same shape, scoped to the
 * Typesense client error vocabulary instead of postgres.js.
 *
 * Retry policy:
 *   - 3 attempts total (initial + 2 retries)
 *   - Exponential backoff: 200ms / 400ms baseline + 0..100ms jitter
 *   - Retry only on transient connection errors (see `isRetryableError`)
 *   - Non-retryable errors (4xx auth, 400 schema, syntax) propagate
 *     immediately so the upstream `try/catch` returns `emptyResponse()`
 *     without burning the budget
 *   - `console.warn` on every retry so observability picks it up
 */

const RETRYABLE_NODE_CODES = new Set([
  "ECONNRESET",
  "ETIMEDOUT",
  "ECONNREFUSED",
  "EPIPE",
  "ENOTFOUND",
  "ECONNABORTED",
  "EAI_AGAIN",
]);

/**
 * Substring matches against the Typesense client error message. The
 * Typesense node SDK wraps axios; transient connection-class events
 * surface as one of these strings — there is no dedicated error code.
 *
 *   - "request timed out"           — axios connect/read timeout
 *   - "timeout exceeded"            — Typesense client-internal timeout
 *   - "socket hang up"              — transient socket close mid-request
 *   - "connection reset"            — TCP RST during TLS handshake
 *   - "network error"               — generic axios network class
 *   - "service unavailable"         — Typesense returns 503 during boot
 *   - "request retry"               — Typesense SDK internal retry exhausted
 *
 * 4xx / `Bad Parameter` / auth errors are NOT in this list — they're
 * deterministic and shouldn't waste retry budget.
 */
const RETRYABLE_MESSAGE_FRAGMENTS = [
  "request timed out",
  "timeout exceeded",
  "socket hang up",
  "connection reset",
  "connection terminated",
  "network error",
  "service unavailable",
  "request retry",
  "econnreset",
  "etimedout",
  "econnrefused",
];

/**
 * HTTP status codes (when the Typesense client surfaces them on the
 * error) that are retry-worthy. The Typesense SDK wraps responses in
 * named error classes (`ServerError`, `ServiceUnavailable`, etc.) and
 * sets `httpStatus` on them.
 */
const RETRYABLE_HTTP_STATUSES = new Set([502, 503, 504]);

const CONFIG_UNAVAILABLE_MESSAGE_FRAGMENTS = [
  "typesense connection not configured",
  "typesense_search_key is not set",
];

export function isRetryableError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as {
    code?: unknown;
    message?: unknown;
    httpStatus?: unknown;
    cause?: unknown;
    name?: unknown;
  };
  if (typeof e.code === "string" && RETRYABLE_NODE_CODES.has(e.code)) {
    return true;
  }
  if (typeof e.httpStatus === "number" && RETRYABLE_HTTP_STATUSES.has(e.httpStatus)) {
    return true;
  }
  if (typeof e.message === "string") {
    const lower = e.message.toLowerCase();
    for (const frag of RETRYABLE_MESSAGE_FRAGMENTS) {
      if (lower.includes(frag)) return true;
    }
  }
  // The Typesense SDK / axios wraps the underlying network error in a
  // `cause` chain — recurse once to catch the inner ECONNRESET etc.
  if (e.cause !== undefined && e.cause !== err) {
    return isRetryableError(e.cause);
  }
  return false;
}

export function isTypesenseRateLimitError(err: unknown): boolean {
  if (!err || typeof err !== "object") return false;
  const e = err as {
    message?: unknown;
    httpStatus?: unknown;
    cause?: unknown;
  };
  if (e.httpStatus === 429) return true;
  if (typeof e.message === "string" && e.message.toLowerCase().includes("http code 429")) {
    return true;
  }
  if (e.cause !== undefined && e.cause !== err) {
    return isTypesenseRateLimitError(e.cause);
  }
  return false;
}

export function isTypesenseUnavailableError(err: unknown): boolean {
  if (isRetryableError(err)) return true;
  if (!err || typeof err !== "object") return false;
  const e = err as {
    message?: unknown;
    cause?: unknown;
  };
  if (typeof e.message === "string") {
    const lower = e.message.toLowerCase();
    if (CONFIG_UNAVAILABLE_MESSAGE_FRAGMENTS.some((frag) => lower.includes(frag))) {
      return true;
    }
  }
  if (e.cause !== undefined && e.cause !== err) {
    return isTypesenseUnavailableError(e.cause);
  }
  return false;
}

export interface TypesenseRetryOptions {
  /** Total attempts (initial + retries). Defaults to 3. */
  attempts?: number;
  /**
   * Base delays in ms before each retry attempt. The N-th retry waits
   * `baseDelaysMs[N-1] + jitter`. Defaults to [200, 400].
   */
  baseDelaysMs?: number[];
  /** Max additional jitter added to each delay. Defaults to 100ms. */
  maxJitterMs?: number;
  /** Sleep override for tests. */
  sleep?: (ms: number) => Promise<void>;
  /** Predicate override for tests / niche call-sites. */
  isRetryable?: (err: unknown) => boolean;
  /** Label used in retry log lines. */
  label?: string;
}

const defaultSleep = (ms: number): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, ms));

/**
 * Run `fn`, retrying on transient connection-class errors. Returns the
 * first successful result, or throws the last error after exhausting
 * the attempt budget. The final throw preserves the original exception
 * (no wrapping) so call-site error handling stays unchanged — the
 * `TypesenseSearchProvider` outer `try/catch` still observes the same
 * error vocabulary it always did.
 */
export async function withTypesenseRetry<T>(
  fn: () => Promise<T>,
  opts: TypesenseRetryOptions = {},
): Promise<T> {
  const attempts = opts.attempts ?? 3;
  const baseDelays = opts.baseDelaysMs ?? [200, 400];
  const maxJitter = opts.maxJitterMs ?? 100;
  const sleep = opts.sleep ?? defaultSleep;
  const retryable = opts.isRetryable ?? isRetryableError;
  const label = opts.label ?? "typesense";

  let lastErr: unknown;
  for (let attempt = 1; attempt <= attempts; attempt++) {
    try {
      return await fn();
    } catch (err) {
      lastErr = err;
      const isLast = attempt >= attempts;
      if (isLast || !retryable(err)) {
        throw err;
      }
      const baseDelay =
        baseDelays[attempt - 1] ?? baseDelays[baseDelays.length - 1] ?? 200;
      const jitter = Math.floor(Math.random() * (maxJitter + 1));
      const delay = baseDelay + jitter;
      const code = (err as { code?: unknown }).code;
      const httpStatus = (err as { httpStatus?: unknown }).httpStatus;
      const message = (err as { message?: unknown }).message;
      console.warn(
        `[${label}] transient error on attempt ${attempt}/${attempts}, ` +
          `retrying in ${delay}ms ` +
          `(code=${typeof code === "string" ? code : "n/a"}, ` +
          `httpStatus=${typeof httpStatus === "number" ? httpStatus : "n/a"}, ` +
          `message=${typeof message === "string" ? message.slice(0, 200) : "n/a"})`,
      );
      await sleep(delay);
    }
  }
  // Unreachable: the loop either returns or throws. Re-throw lastErr to
  // satisfy the type checker.
  throw lastErr;
}
